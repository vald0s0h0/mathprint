"""Bac à sable de dépôt de scans (§5c) : upload EN VRAC de PDFs multi-pages,
images isolées, HEIC/JPEG/PNG, mélange de sujets, doublons.

Traité PAGE PAR PAGE et surtout GROUPÉ GLOBALEMENT par sujet sur l'ensemble du
dépôt : plusieurs fichiers d'un même sujet (typiquement des photos une par
copie) ne créent qu'UNE correction, pas une par fichier — toutes les pages
retenues s'accumulent dans l'unique ScanBatch du sujet (cf. services.scan_intake,
règle « un sujet = une correction = une ligne »).

Seuls les redépôts déjà enregistrés sont rejetés sur l'autorité du `page_id`.
Un doublon ou une page illisible présent dans la pile courante reste dans le PDF
afin de préserver l'alignement physique. On ne bloque JAMAIS sur le sha256 du
fichier : ce verrou survivait à la suppression d'une correction (SandboxUpload
n'est rattaché à aucun batch, donc data_admin ne peut pas le nettoyer), si bien
qu'un prof ayant supprimé une correction par erreur ne pouvait plus redéposer le
même fichier. Le page_id, lui, est remis à zéro par la suppression (ScannedPage
supprimée), donc il reflète fidèlement ce qui est réellement enregistré. Le
sha256 reste enregistré à titre d'historique (SandboxUpload)."""
import hashlib
import re
import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from ..config import settings
from ..models import SandboxUpload
from . import scan_intake, worker_cv


def scan_filename_key(filename: str) -> tuple:
    """Tri naturel et stable des fichiers émis par un ADF.

    Les scanners nomment généralement leurs sorties avec un compteur ou un
    horodatage. Un tri lexical placerait scan-10 avant scan-2 ; on compare donc
    chaque groupe de chiffres numériquement. L'ordre obtenu est l'ordre métier
    du dépôt, jamais l'ordre alphabétique des élèves.
    """
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", filename))


def ingest_files(db: Session, files: list[tuple[str, str, bytes]],
                 uploaded_by: str | None) -> dict:
    """Ingère une liste de fichiers reconnus [(filename, ext, content)] déposés
    en une fois. Retourne {"results": [...par fichier...], "batch_ids": [...]}.

    Le regroupement par sujet est GLOBAL au dépôt : deux photos du même sujet
    dans deux fichiers distincts finissent dans le même batch. Une même page
    présente deux fois n'est corrigée qu'une fois, mais ses deux emplacements
    restent dans le flux. L'appelant (routers.scans) planifie ensuite le pipeline
    une seule fois par batch touché."""
    kept_by_assessment: dict[str, list] = defaultdict(list)
    records: list[dict] = []

    # L'ordre du multipart dépend du navigateur et de la façon dont le dossier
    # a été sélectionné. On rétablit explicitement l'ordre compteur/horodatage
    # encodé par l'ADF, en conservant la position d'origine en cas d'égalité.
    ordered_files = [row for _i, row in sorted(
        enumerate(files), key=lambda pair: (scan_filename_key(pair[1][0]), pair[0]))]
    for filename, ext, content in ordered_files:
        file_kind = "pdf" if ext.lower() == ".pdf" else "image"
        sha = hashlib.sha256(content).hexdigest()
        upload = SandboxUpload(uploaded_by=uploaded_by, original_filename=filename, sha256=sha)
        db.add(upload)
        db.flush()

        tmp_dir = settings.data_dir / "scans" / "sandbox_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{uuid.uuid4().hex}{ext}"
        tmp.write_bytes(content)
        try:
            images = worker_cv.raster_any(str(tmp))
        except Exception as e:
            upload.status = "error"
            records.append({"filename": filename, "file_kind": file_kind,
                            "upload": upload, "error": str(e), "pages": []})
            continue
        finally:
            tmp.unlink(missing_ok=True)

        classified = []
        for img in images:
            page_id, aid, warped = scan_intake.classify_page(db, img)
            classified.append({
                "image": img, "page_id": page_id, "assessment_id": aid,
                "warped": warped,
                "already_registered": bool(
                    page_id and scan_intake.page_already_registered(db, page_id)),
            })
        records.append({"filename": filename, "file_kind": file_kind,
                        "upload": upload, "error": None, "pages": classified})

    # Une série de JPEGs ADF peut contenir une image sans QR entre deux images
    # reconnues. Si TOUT le dépôt ne désigne qu'un seul sujet, cette page est
    # rattachée au même lot sans lui attribuer d'élève : sa position devient un
    # placeholder. Avec plusieurs sujets mélangés, aucune attribution ambiguë.
    upload_assessment_ids = {
        page["assessment_id"] for record in records for page in record["pages"]
        if page["assessment_id"]
    }
    single_upload_assessment = (next(iter(upload_assessment_ids))
                                if len(upload_assessment_ids) == 1 else None)
    seen_page_ids: set[str] = set()
    results = []
    for record in records:
        filename, file_kind, upload = (record["filename"], record["file_kind"],
                                       record["upload"])
        if record["error"] is not None:
            results.append({"filename": filename, "file_kind": file_kind,
                            "status": "error",
                            "error": record["error"], "pages_added": 0,
                            "duplicates_rejected": 0, "blocked_pages": 0,
                            "batches_created": []})
            continue

        local_ids = {p["assessment_id"] for p in record["pages"]
                     if p["assessment_id"]}
        local_fallback = (next(iter(local_ids)) if len(local_ids) == 1 else None)
        fallback_aid = single_upload_assessment or local_fallback
        n_added = n_dup = n_blocked = 0
        for page in record["pages"]:
            page_id, aid = page["page_id"], page["assessment_id"]
            if page["already_registered"]:
                # Redépôt antérieur : pas dans la pile physique courante.
                n_dup += 1
                continue
            if page_id and aid:
                if page_id in seen_page_ids:
                    # Doublon au SEIN du lot courant : position conservée, mais
                    # le pipeline n'effectuera qu'une correction.
                    n_dup += 1
                else:
                    seen_page_ids.add(page_id)
                kept_by_assessment[aid].append(
                    page["warped"] if page["warped"] is not None else page["image"])
                n_added += 1
                continue
            n_blocked += 1
            if fallback_aid:
                kept_by_assessment[fallback_aid].append(page["image"])
                n_added += 1

        # Un fichier n'est un « doublon » que si toutes ses pages appartenaient
        # déjà à un dépôt antérieur. Les doublons présents dans le lot courant,
        # eux, restent comme emplacements de sécurité.
        if n_added == 0 and n_dup > 0 and n_blocked == 0:
            upload.status = "duplicate_rejected"
            results.append({"filename": filename, "file_kind": file_kind,
                            "status": "duplicate_file",
                            "pages_added": 0, "duplicates_rejected": n_dup,
                            "blocked_pages": 0, "batches_created": []})
            continue
        upload.status = "processed"
        results.append({"filename": filename, "file_kind": file_kind,
                        "status": "processed", "pages_added": n_added,
                        "duplicates_rejected": n_dup, "blocked_pages": n_blocked,
                        "batches_created": []})

    batch_ids: list[str] = []
    for assessment_id, imgs in kept_by_assessment.items():
        batch = scan_intake.get_or_create_batch(db, assessment_id, uploaded_by)
        scan_intake.append_pages(db, batch, assessment_id, imgs)
        if batch.id not in batch_ids:
            batch_ids.append(batch.id)
    db.commit()
    return {"results": results, "batch_ids": batch_ids}

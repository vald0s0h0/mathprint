"""Bac à sable de dépôt de scans (§5c) : upload EN VRAC de PDFs multi-pages,
images isolées, HEIC/JPEG/PNG, mélange de sujets, doublons.

Traité PAGE PAR PAGE et surtout GROUPÉ GLOBALEMENT par sujet sur l'ensemble du
dépôt : plusieurs fichiers d'un même sujet (typiquement des photos une par
copie) ne créent qu'UNE correction, pas une par fichier — toutes les pages
retenues s'accumulent dans l'unique ScanBatch du sujet (cf. services.scan_intake,
règle « un sujet = une correction = une ligne »).

Les doublons sont rejetés à deux niveaux, silencieusement (décision produit, pas
de file de validation) : fichier identique déjà déposé (sha256), et page déjà
enregistrée (page_id, dédup globale au sein du dépôt + inter-dépôts)."""
import hashlib
import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from ..config import settings
from ..models import SandboxUpload
from . import scan_intake, worker_cv


def ingest_files(db: Session, files: list[tuple[str, str, bytes]],
                 uploaded_by: str | None) -> dict:
    """Ingère une liste de fichiers reconnus [(filename, ext, content)] déposés
    en une fois. Retourne {"results": [...par fichier...], "batch_ids": [...]}.

    Le regroupement par sujet et la déduplication des pages sont GLOBAUX au
    dépôt : deux photos du même sujet dans deux fichiers distincts finissent
    dans le même batch, et une même page présente deux fois n'est comptée
    qu'une fois. L'appelant (routers.scans) planifie ensuite le pipeline une
    seule fois par batch touché."""
    seen_page_ids: set[str] = set()
    kept_by_assessment: dict[str, list] = defaultdict(list)
    results = []

    for filename, ext, content in files:
        sha = hashlib.sha256(content).hexdigest()
        dup_file = (db.query(SandboxUpload)
                    .filter(SandboxUpload.sha256 == sha, SandboxUpload.status != "error")
                    .first())
        upload = SandboxUpload(uploaded_by=uploaded_by, original_filename=filename, sha256=sha)
        db.add(upload)
        db.flush()

        if dup_file:
            upload.status = "duplicate_rejected"
            results.append({"filename": filename, "status": "duplicate_file", "pages_added": 0,
                            "duplicates_rejected": 0, "blocked_pages": 0, "batches_created": []})
            continue

        tmp_dir = settings.data_dir / "scans" / "sandbox_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{uuid.uuid4().hex}{ext}"
        tmp.write_bytes(content)
        try:
            images = worker_cv.raster_any(str(tmp))
        except Exception as e:
            upload.status = "error"
            results.append({"filename": filename, "status": "error", "error": str(e),
                            "pages_added": 0, "duplicates_rejected": 0, "blocked_pages": 0,
                            "batches_created": []})
            continue
        finally:
            tmp.unlink(missing_ok=True)

        n_added = n_dup = n_blocked = 0
        for img in images:
            page_id, aid = scan_intake.classify_page(db, img)
            if not page_id or not aid:
                n_blocked += 1  # non identifiée : jamais devinée (RM-001)
                continue
            if page_id in seen_page_ids or scan_intake.page_already_registered(db, page_id):
                n_dup += 1  # copie déjà scannée : rejet silencieux
                continue
            seen_page_ids.add(page_id)
            kept_by_assessment[aid].append(img)
            n_added += 1
        upload.status = "processed"
        results.append({"filename": filename, "status": "processed", "pages_added": n_added,
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

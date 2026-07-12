"""Bac à sable de dépôt de scans (§5c) : upload en vrac (PDFs multi-pages,
images isolées, HEIC/JPEG/PNG, mélange de sujets, doublons), traité PAGE PAR
PAGE — jamais par lot ou par sujet à l'avance, contrairement au dépôt normal
(POST /api/scans/batches) qui suppose un seul sujet par fichier.

Chaque page est identifiée individuellement (QR + fiduciels, comme le flux
normal, RM-001 : jamais devinée) ; les doublons (page déjà enregistrée par un
dépôt précédent, identifiée par son page_id — la clé la plus robuste puisque
chaque page imprimée porte un QR unique par copie) sont rejetés automatiquement
et silencieusement (décision produit — pas de file de validation). Les pages
restantes sont regroupées par sujet puis réencodées en PDF, réinjectées dans
le pipeline de correction existant SANS LE MODIFIER : un ScanBatch normal par
sujet, traité par process_batch comme n'importe quel dépôt."""
import hashlib
import io
import uuid
from collections import defaultdict

import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Copy, DocumentPage, FileObject, SandboxUpload, ScanBatch, ScannedPage
from . import worker_cv


def _encode_pages_to_pdf(images: list[np.ndarray]) -> bytes:
    """Réencode des pages déjà rastérisées en un nouveau PDF A4, une image par
    page — permet de réutiliser le pipeline de correction existant tel quel
    même si les pages provenaient d'un dépôt en vrac mélangé."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    for img in images:
        ok, png = cv2.imencode(".png", img)
        c.drawImage(ImageReader(io.BytesIO(png.tobytes())), 0, 0, width=pw, height=ph)
        c.showPage()
    c.save()
    return buf.getvalue()


def _page_already_registered(db: Session, page_id: str) -> bool:
    return db.query(ScannedPage).filter(
        ScannedPage.page_id == page_id,
        ScannedPage.status.in_(("registered", "graded", "finalized")),
    ).first() is not None


def ingest_file(db: Session, filename: str, ext: str, content: bytes,
                uploaded_by: str | None) -> dict:
    """Explose un fichier déposé en pages, identifie chacune, rejette les
    doublons, regroupe le reste par sujet et crée un ScanBatch par sujet.
    Retourne un résumé {filename, status, pages_added, duplicates_rejected,
    blocked_pages, batches_created}."""
    sha = hashlib.sha256(content).hexdigest()
    dup_file = (db.query(SandboxUpload)
                .filter(SandboxUpload.sha256 == sha, SandboxUpload.status != "error")
                .first())
    upload = SandboxUpload(uploaded_by=uploaded_by, original_filename=filename, sha256=sha)
    db.add(upload)
    db.flush()

    if dup_file:
        # même fichier déjà déposé (mot pour mot) : rejet silencieux (§5c)
        upload.status = "duplicate_rejected"
        db.commit()
        return {"filename": filename, "status": "duplicate_file", "pages_added": 0,
                "duplicates_rejected": 0, "blocked_pages": 0, "batches_created": []}

    tmp_dir = settings.data_dir / "scans" / "sandbox_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}{ext}"
    tmp.write_bytes(content)
    try:
        images = worker_cv.raster_any(str(tmp))
    except Exception as e:
        upload.status = "error"
        db.commit()
        return {"filename": filename, "status": "error", "error": str(e),
                "pages_added": 0, "duplicates_rejected": 0, "blocked_pages": 0,
                "batches_created": []}
    finally:
        tmp.unlink(missing_ok=True)

    by_assessment: dict[str, list[np.ndarray]] = defaultdict(list)
    n_dup, n_blocked = 0, 0
    for img in images:
        res = worker_cv.analyze_page(img)
        if res.status != "registered" or not res.page_id:
            n_blocked += 1  # non identifiée : jamais devinée (RM-001)
            continue
        if _page_already_registered(db, res.page_id):
            n_dup += 1  # copie déjà scannée et enregistrée : rejet silencieux
            continue
        page = db.get(DocumentPage, res.page_id)
        copy = db.get(Copy, page.copy_id) if page else None
        if not copy:
            n_blocked += 1
            continue
        by_assessment[copy.assessment_id].append(img)

    batches_created = []
    for assessment_id, imgs in by_assessment.items():
        pdf_bytes = _encode_pages_to_pdf(imgs)
        d = settings.data_dir / "assessments" / assessment_id / "scans" / "original"
        d.mkdir(parents=True, exist_ok=True)
        batch = ScanBatch(assessment_id=assessment_id, uploaded_by=uploaded_by)
        db.add(batch)
        db.flush()
        path = d / f"{batch.id}.pdf"
        path.write_bytes(pdf_bytes)
        fo = FileObject(owner_type="scan_batch", owner_id=batch.id, storage_path=str(path),
                        sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                        mime="application/pdf", size=len(pdf_bytes))
        db.add(fo)
        db.flush()
        batch.source_file_id = fo.id
        batches_created.append(batch.id)

    upload.status = "processed"
    db.commit()
    return {"filename": filename, "status": "processed",
            "pages_added": sum(len(v) for v in by_assessment.values()),
            "duplicates_rejected": n_dup, "blocked_pages": n_blocked,
            "batches_created": batches_created}

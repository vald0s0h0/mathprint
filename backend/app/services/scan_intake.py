"""Point d'entrée unique des scans dans la pipeline de correction.

RÈGLE CARDINALE : **un sujet = une correction = un seul ScanBatch**. Tout dépôt
(bac à sable OU dépôt ciblé) converge ici, et les pages scannées s'ACCUMULENT
dans l'unique batch du sujet, jamais un second. Sans ça, déposer plusieurs
images/fichiers d'un même sujet créait autant de « corrections » que de fichiers
(cf. incident bac à sable) — or une correction est toujours issue d'un sujet créé
par le professeur, en une seule ligne.

Chaque page est identifiée individuellement (QR + fiduciels, RM-001 : jamais
devinée) ; les doublons (page déjà scannée, identifiée par son `page_id` — chaque
page imprimée porte un QR unique par copie) sont rejetés silencieusement. Les
pages retenues sont fusionnées dans le PDF accumulé du batch (une image par page),
que le pipeline existant (`process_batch`) reprend sans modification — idempotent,
il ne re-corrige pas une copie déjà notée et note les nouvelles."""
import hashlib
import io

import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Copy, DocumentPage, FileObject, ScanBatch, ScannedPage
from . import worker_cv
from .security import verify_page_payload


def encode_pages_to_pdf(images: list[np.ndarray]) -> bytes:
    """Réencode des pages déjà rastérisées en un PDF A4, une image par page —
    format commun réinjecté dans le pipeline de correction quelle que soit la
    provenance (PDF, photo isolée, bac à sable mélangé)."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    for img in images:
        ok, png = cv2.imencode(".png", img)
        c.drawImage(ImageReader(io.BytesIO(png.tobytes())), 0, 0, width=pw, height=ph)
        c.showPage()
    c.save()
    return buf.getvalue()


def page_already_registered(db: Session, page_id: str) -> bool:
    """Page déjà enregistrée par un dépôt précédent (dédup inter-dépôts)."""
    return db.query(ScannedPage).filter(
        ScannedPage.page_id == page_id,
        ScannedPage.status.in_(("registered", "graded", "finalized")),
    ).first() is not None


def page_assessment(db: Session, page_id: str) -> str | None:
    page = db.get(DocumentPage, page_id)
    copy = db.get(Copy, page.copy_id) if page else None
    return copy.assessment_id if copy else None


def detect_assessment(db: Session, images: list[np.ndarray]) -> str | None:
    """Identifie le sujet depuis le QR signé de la première page reconnue : le
    QR suffit, aucun choix manuel d'évaluation n'est nécessaire au dépôt (§5.3)."""
    for img in images:
        for text, _quad in worker_cv._detect_qrcodes(img):
            page_id = verify_page_payload(text)
            if not page_id:
                continue
            aid = page_assessment(db, page_id)
            if aid:
                return aid
    return None


def classify_page(db: Session, img: np.ndarray
                  ) -> tuple[str | None, str | None, np.ndarray | None]:
    """(page_id, assessment_id, image RECALÉE) si la page est reconnue
    (registered), sinon (None, None, None). Recalage complet (QR + fiduciels +
    homographie) — une page non identifiée n'est jamais devinée (RM-001).

    On renvoie l'image DÉJÀ RECALÉE sur le gabarit A4 canonique (`res.warped`)
    pour qu'elle soit stockée telle quelle : le pipeline la reprend alors sans
    avoir à ré-identifier une photo brute (perspective, cadrage, résolution du
    téléphone) une deuxième fois. C'était la cause du « rien à corriger / overlays
    vides » : la page passait l'identification au dépôt, mais l'image brute
    ré-encodée en A4 (déformée, ré-échantillonnée) échouait au 2e passage du
    worker, et tout le sujet finissait vide en silence."""
    res = worker_cv.analyze_page(img)
    if res.status != "registered" or not res.page_id:
        return None, None, None
    return res.page_id, page_assessment(db, res.page_id), res.warped


def get_or_create_batch(db: Session, assessment_id: str,
                        uploaded_by: str | None) -> ScanBatch:
    """L'unique ScanBatch du sujet : le plus ancien s'il existe (données de dev
    ayant déjà plusieurs batches), sinon un nouveau. JAMAIS un second batch pour
    un sujet — une correction est issue d'un sujet, en une seule ligne."""
    batch = (db.query(ScanBatch).filter_by(assessment_id=assessment_id)
             .order_by(ScanBatch.created_at.asc()).first())
    if batch is None:
        batch = ScanBatch(assessment_id=assessment_id, uploaded_by=uploaded_by)
        db.add(batch)
        db.flush()
    return batch


def append_pages(db: Session, batch: ScanBatch, assessment_id: str,
                 images: list[np.ndarray]) -> None:
    """Fusionne de nouvelles pages dans le PDF accumulé du batch (append), met à
    jour le FileObject source. Les pages existantes ne sont pas rerastérisées :
    fusion PDF (pypdf) des pages déjà là + nouvelles."""
    if not images:
        return
    from pypdf import PdfReader, PdfWriter

    d = settings.data_dir / "assessments" / assessment_id / "scans" / "original"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{batch.id}.pdf"

    new_pdf = encode_pages_to_pdf(images)
    if batch.source_file_id and path.exists():
        writer = PdfWriter()
        for p in PdfReader(str(path)).pages:
            writer.add_page(p)
        for p in PdfReader(io.BytesIO(new_pdf)).pages:
            writer.add_page(p)
        out = io.BytesIO()
        writer.write(out)
        combined = out.getvalue()
    else:
        combined = new_pdf
    path.write_bytes(combined)

    fo = db.get(FileObject, batch.source_file_id) if batch.source_file_id else None
    if fo is None:
        fo = FileObject(owner_type="scan_batch", owner_id=batch.id, storage_path=str(path))
        db.add(fo)
        db.flush()
        batch.source_file_id = fo.id
    fo.storage_path = str(path)
    fo.sha256 = hashlib.sha256(combined).hexdigest()
    fo.mime = "application/pdf"
    fo.size = len(combined)


def attach_scan(db: Session, assessment_id: str, images: list[np.ndarray],
                uploaded_by: str | None) -> dict:
    """Attache des pages scannées (un seul fichier, un sujet connu) à l'unique
    batch du sujet, en dédupliquant. Retourne un résumé
    {batch_id, pages_added, duplicates_rejected, blocked_pages}."""
    batch = get_or_create_batch(db, assessment_id, uploaded_by)
    kept: list[np.ndarray] = []
    seen: set[str] = set()
    n_dup = n_blocked = 0
    for img in images:
        page_id, aid, warped = classify_page(db, img)
        if not page_id or aid != assessment_id:
            # non identifiée, ou page d'un autre sujet : jamais attribuée ici
            n_blocked += 1
            continue
        if page_id in seen or page_already_registered(db, page_id):
            n_dup += 1
            continue
        seen.add(page_id)
        # on stocke l'image RECALÉE (canonique), pas la photo brute : le pipeline
        # la reprend fidèlement, sans risque d'échec de ré-identification.
        kept.append(warped if warped is not None else img)
    append_pages(db, batch, assessment_id, kept)
    return {"batch_id": batch.id, "pages_added": len(kept),
            "duplicates_rejected": n_dup, "blocked_pages": n_blocked}

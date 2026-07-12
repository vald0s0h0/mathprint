"""Lots de scans, machine d'états, file de validation professeur (§6, §9.5)."""
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import current_user
from ..models import (
    Assessment, Copy, CopyItem, DocumentPage, FileObject, GradingDecision,
    ManualReview, OcrAttempt, ScanBatch, SchoolClass, Student, StudentResponse,
    User,
)
from ..services import sandbox as sandbox_service
from ..services.pipeline import PHASES, build_overlays, finalize_batch, process_batch

router = APIRouter(prefix="/api/scans", tags=["scans"], dependencies=[Depends(current_user)])


def _sniff_file(content: bytes) -> tuple[str, str] | None:
    """(extension, mime) reconnus par signature d'octets (magic bytes) — jamais
    le Content-Type client, purement déclaratif (§5b : PDF, JPEG, PNG, HEIC)."""
    if content.startswith(b"%PDF-"):
        return ".pdf", "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in (
            b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return ".heic", "image/heic"
    return None


def _run_pipeline(batch_id: str):
    db = SessionLocal()
    try:
        batch = db.get(ScanBatch, batch_id)
        process_batch(db, batch)
    except Exception as e:
        batch = db.get(ScanBatch, batch_id)
        batch.error = str(e)
        db.commit()
    finally:
        db.close()


def _detect_assessment(db: Session, content: bytes, ext: str) -> str | None:
    """Identifie le sujet depuis le QR signé d'une page : le QR suffit, aucun
    choix manuel d'évaluation n'est nécessaire au dépôt (§5.3)."""
    from ..services import worker_cv
    from ..services.security import verify_page_payload

    tmp_dir = settings.data_dir / "scans" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"detect-{uuid.uuid4().hex}{ext}"
    tmp.write_bytes(content)
    try:
        for img in worker_cv.raster_any(str(tmp)):
            for text, _quad in worker_cv._detect_qrcodes(img):
                page_id = verify_page_payload(text)
                if not page_id:
                    continue
                page = db.get(DocumentPage, page_id)
                copy = db.get(Copy, page.copy_id) if page else None
                if copy:
                    return copy.assessment_id
    finally:
        tmp.unlink(missing_ok=True)
    return None


@router.post("/batches")
async def create_batch(tasks: BackgroundTasks, assessment_id: str | None = None,
                       file: UploadFile | None = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    content = await file.read() if file is not None else None
    sniffed = _sniff_file(content) if content is not None else None
    if content is not None and sniffed is None:
        raise HTTPException(400, "Format non reconnu — PDF, JPEG, PNG ou HEIC uniquement")
    ext, mime = sniffed if sniffed else (".pdf", "application/pdf")
    if not assessment_id:
        if content is None:
            raise HTTPException(422, "Déposer un scan, ou préciser une évaluation "
                                     "pour un lot simulé")
        assessment_id = _detect_assessment(db, content, ext)
        if not assessment_id:
            raise HTTPException(422, "Aucun QR MathPrint reconnu dans ce scan — "
                                     "vérifier qu'il s'agit bien de copies générées ici")
    if not db.get(Assessment, assessment_id):
        raise HTTPException(404, "Évaluation inconnue")
    batch = ScanBatch(assessment_id=assessment_id, uploaded_by=user.id)
    db.add(batch)
    db.flush()
    if content is not None:
        d = settings.data_dir / "assessments" / assessment_id / "scans" / "original"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{batch.id}{ext}"
        path.write_bytes(content)  # scan original immuable (RM-002)
        fo = FileObject(owner_type="scan_batch", owner_id=batch.id, storage_path=str(path),
                        sha256=hashlib.sha256(content).hexdigest(), mime=mime, size=len(content))
        db.add(fo)
        db.flush()
        batch.source_file_id = fo.id
    db.commit()
    tasks.add_task(_run_pipeline, batch.id)
    return {"id": batch.id, "assessment_id": assessment_id}


@router.post("/sandbox")
async def sandbox_upload(tasks: BackgroundTasks, files: list[UploadFile],
                         db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    """Bac à sable (§5c) : dépôt en vrac de PDFs/images mélangés, traité page
    par page — chaque page est identifiée et associée à son sujet
    individuellement, les doublons (page déjà enregistrée) sont rejetés
    automatiquement et silencieusement. Un ScanBatch normal est créé par
    sujet identifié, puis traité par le pipeline existant sans modification."""
    results = []
    for f in files:
        content = await f.read()
        sniffed = _sniff_file(content)
        if sniffed is None:
            results.append({"filename": f.filename, "status": "unrecognized", "pages_added": 0,
                            "duplicates_rejected": 0, "blocked_pages": 0, "batches_created": []})
            continue
        ext, _mime = sniffed
        r = sandbox_service.ingest_file(db, f.filename or "scan", ext, content, user.id)
        results.append(r)
        for batch_id in r.get("batches_created", []):
            tasks.add_task(_run_pipeline, batch_id)
    return {"results": results}


@router.get("/batches")
def list_batches(assessment_id: str | None = None, db: Session = Depends(get_db)):
    q = (db.query(ScanBatch)
         .join(Assessment, ScanBatch.assessment_id == Assessment.id)
         .join(SchoolClass, Assessment.class_id == SchoolClass.id)
         .filter(SchoolClass.archived_at.is_(None))
         .order_by(ScanBatch.created_at.desc()))
    if assessment_id:
        q = q.filter(ScanBatch.assessment_id == assessment_id)
    rows = [_batch_view(db, b) for b in q.all()]
    if not assessment_id:
        rows += _awaiting_scan_rows(db)
    return rows


def _awaiting_scan_rows(db: Session) -> list[dict]:
    """Sujets générés/imprimés sans aucun lot de scan encore déposé (§5a) —
    invisibles jusqu'ici puisque Corrections ne listait que des ScanBatch."""
    scanned_ids = {a for (a,) in db.query(ScanBatch.assessment_id).distinct()}
    q = (db.query(Assessment)
         .join(SchoolClass, Assessment.class_id == SchoolClass.id)
         .filter(Assessment.status.in_(("ready", "printed")),
                 SchoolClass.archived_at.is_(None))
         .order_by(Assessment.created_at.desc()))
    out = []
    for a in q.all():
        if a.id in scanned_ids:
            continue
        cls = db.get(SchoolClass, a.class_id)
        out.append({
            "id": f"awaiting-{a.id}", "assessment_id": a.id, "status": "awaiting_scan",
            "assessment_title": a.title, "assessment_type": a.type,
            "class_name": cls.name if cls else "?", "class_id": cls.id if cls else None,
            "grade_level": cls.grade_level if cls else "", "page_count": 0,
            "error": None, "pending_reviews": 0, "segments": [],
            "overlay_printed": False, "overlay_distributed": False,
            "created_at": str(a.created_at),
        })
    return out


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    return _batch_view(db, b)


def _batch_view(db: Session, b: ScanBatch) -> dict:
    pending = _pending_reviews_query(db, b.assessment_id).count()
    # barre segmentée par palier : vert ok, orange si intervention requise
    segments = []
    reached = True
    for phase in PHASES:
        done = phase in (b.progress_json or {})
        color = "green" if done else ("orange" if reached and not done else "gray")
        if phase == "review_pending" and pending:
            color = "orange"
        segments.append({"phase": phase, "state": color})
        if not done:
            reached = False
    assessment = db.get(Assessment, b.assessment_id)
    cls = db.get(SchoolClass, assessment.class_id) if assessment else None
    return {"id": b.id, "assessment_id": b.assessment_id, "status": b.status,
            "assessment_title": assessment.title if assessment else "?",
            "assessment_type": assessment.type if assessment else "training",
            "class_name": cls.name if cls else "?",
            "class_id": cls.id if cls else None,
            "grade_level": cls.grade_level if cls else "",
            "page_count": b.page_count, "error": b.error,
            "pending_reviews": pending, "segments": segments,
            "overlay_printed": b.overlay_printed, "overlay_distributed": b.overlay_distributed,
            "created_at": str(b.created_at)}


def _pending_reviews_query(db: Session, assessment_id: str):
    return (db.query(ManualReview)
            .join(GradingDecision, ManualReview.decision_id == GradingDecision.id)
            .join(StudentResponse, GradingDecision.response_id == StudentResponse.id)
            .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
            .join(Copy, CopyItem.copy_id == Copy.id)
            .filter(Copy.assessment_id == assessment_id,
                    ManualReview.resolved_at.is_(None)))


@router.get("/batches/{batch_id}/reviews")
def list_reviews(batch_id: str, category: str | None = None, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    q = _pending_reviews_query(db, b.assessment_id)
    if category:
        q = q.filter(ManualReview.category == category)
    out = []
    for r in q.all():
        decision = db.get(GradingDecision, r.decision_id)
        resp = db.get(StudentResponse, decision.response_id)
        item = db.get(CopyItem, resp.copy_item_id)
        copy = db.get(Copy, item.copy_id)
        student = db.get(Student, copy.student_id)
        ocr = (db.query(OcrAttempt).filter_by(zone_id=resp.zone_id)
               .order_by(OcrAttempt.created_at.desc()).first())
        out.append({
            "review_id": r.id, "category": r.category,
            "student": f"{student.last_name} {student.first_name}",
            "statement": item.statement, "expected": item.expected_json,
            "correction": item.correction,
            "ocr_text": resp.final_text, "selected_choices": resp.selected_choices,
            "ocr_confidence": ocr.confidence if ocr else None,
            "reason_code": decision.reason_code,
            "proposed_score": decision.score, "max_score": decision.max_score,
        })
    return out


class ResolveIn(BaseModel):
    action: str          # accept | set_score | cancel_item | correct_ocr | rescan
    score: float | None = None
    corrected_text: str | None = None
    note: str = ""


@router.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: str, body: ResolveIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    r = db.get(ManualReview, review_id)
    if not r or r.resolved_at:
        raise HTTPException(404, "Revue introuvable ou déjà résolue")
    old = db.get(GradingDecision, r.decision_id)
    resp = db.get(StudentResponse, old.response_id)

    score = old.score
    if body.action == "set_score":
        if body.score is None:
            raise HTTPException(422, "score requis")
        score = min(max(0.0, body.score), old.max_score)
    elif body.action == "cancel_item":
        score = 0.0
    if body.corrected_text is not None:
        resp.final_text = body.corrected_text

    # décision professeur en append-only (RM-006)
    new = GradingDecision(response_id=old.response_id, source="teacher",
                          score=score if body.action != "cancel_item" else 0.0,
                          max_score=old.max_score if body.action != "cancel_item" else 0.0,
                          confidence=1.0, tier="D",
                          reason_code=f"teacher_{body.action}", status="validated",
                          evidence_json={"previous_decision": old.id, "note": body.note})
    old.status = "revised"
    db.add(new)
    r.resolution = body.action
    r.note = body.note
    r.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.post("/batches/{batch_id}/finalize")
def finalize(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    try:
        result = finalize_batch(db, b)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return result


@router.post("/batches/{batch_id}/overlays")
def overlays(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if b.status != "finalized" and "finalized" not in (b.progress_json or {}):
        raise HTTPException(409, "Finaliser le lot avant de générer les overlays")
    path = build_overlays(db, b)
    db.commit()
    return {"path": path, "download": f"/api/assessments/{b.assessment_id}/files/correction_overlay.pdf"}


class BatchFlagsIn(BaseModel):
    overlay_printed: bool | None = None
    overlay_distributed: bool | None = None


@router.patch("/batches/{batch_id}")
def update_batch_flags(batch_id: str, body: BatchFlagsIn, db: Session = Depends(get_db)):
    """Cases à cocher Imprimé / Distribué (§9.5) : suivi manuel post-overlay."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if body.overlay_printed is not None:
        b.overlay_printed = body.overlay_printed
    if body.overlay_distributed is not None:
        b.overlay_distributed = body.overlay_distributed
    db.commit()
    return _batch_view(db, b)

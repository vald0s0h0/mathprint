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
from ..services.pipeline import PHASES, build_overlays, finalize_batch, process_batch

router = APIRouter(prefix="/api/scans", tags=["scans"], dependencies=[Depends(current_user)])


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


def _detect_assessment(db: Session, content: bytes) -> str | None:
    """Identifie le sujet depuis le QR signé d'une page : le QR suffit, aucun
    choix manuel d'évaluation n'est nécessaire au dépôt (§5.3)."""
    from ..services import worker_cv
    from ..services.security import verify_page_payload

    tmp_dir = settings.data_dir / "scans" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"detect-{uuid.uuid4().hex}.pdf"
    tmp.write_bytes(content)
    try:
        for img in worker_cv.raster_pdf(str(tmp)):
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
    if not assessment_id:
        if content is None:
            raise HTTPException(422, "Déposer un PDF, ou préciser une évaluation "
                                     "pour un lot simulé")
        assessment_id = _detect_assessment(db, content)
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
        path = d / f"{batch.id}.pdf"
        path.write_bytes(content)  # scan original immuable (RM-002)
        fo = FileObject(owner_type="scan_batch", owner_id=batch.id, storage_path=str(path),
                        sha256=hashlib.sha256(content).hexdigest(), size=len(content))
        db.add(fo)
        db.flush()
        batch.source_file_id = fo.id
    db.commit()
    tasks.add_task(_run_pipeline, batch.id)
    return {"id": batch.id, "assessment_id": assessment_id}


@router.get("/batches")
def list_batches(assessment_id: str | None = None, db: Session = Depends(get_db)):
    q = (db.query(ScanBatch)
         .join(Assessment, ScanBatch.assessment_id == Assessment.id)
         .join(SchoolClass, Assessment.class_id == SchoolClass.id)
         .filter(SchoolClass.archived_at.is_(None))
         .order_by(ScanBatch.created_at.desc()))
    if assessment_id:
        q = q.filter(ScanBatch.assessment_id == assessment_id)
    return [_batch_view(db, b) for b in q.all()]


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

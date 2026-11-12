"""API du worker OCR (Mac) : file de tâches de la lecture multicouche.

Authentification propre au worker : jeton `Authorization: Bearer <jeton>`
(MATHPRINT_OCR_WORKER_TOKEN ou réglage `ocr_worker_token`), comparé en temps
constant, distinct des comptes utilisateurs. Sans jeton configuré, l'API est
fermée (503). Le jeton n'est jamais journalisé.
"""
import hmac
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import OcrCrop, OcrJob
from ..services import ocr_queue
from ..services import runtime_settings as rs

router = APIRouter(prefix="/api/ocr-worker", tags=["ocr-worker"])


def worker_auth(authorization: str = Header(default=""),
                x_ocr_worker: str = Header(default=""),
                db: Session = Depends(get_db)) -> str:
    expected = rs.ocr_worker_token(db)
    if not expected:
        raise HTTPException(503, "API du worker OCR fermée : aucun jeton configuré")
    scheme, _, given = authorization.partition(" ")
    ok = scheme.lower() == "bearer" and hmac.compare_digest(
        given.strip().encode(), expected.encode())
    if not ok:
        raise HTTPException(401, "Jeton du worker invalide")
    name = (x_ocr_worker or "worker").strip()[:80]
    ocr_queue.touch_worker(db, name)
    return name


@router.get("/pending")
def pending(engine: str | None = None, db: Session = Depends(get_db),
            _worker: str = Depends(worker_auth)):
    counts = ocr_queue.pending_counts(db, engine)
    if engine:
        return {"engine": engine, "pending": counts.get(engine, 0)}
    return {"pending": counts}


class ClaimIn(BaseModel):
    engine: str
    limit: int = Field(default=32, ge=1, le=ocr_queue.MAX_CLAIM)


@router.post("/claim")
def claim(body: ClaimIn, db: Session = Depends(get_db), worker: str = Depends(worker_auth)):
    jobs = ocr_queue.claim_jobs(db, engine=body.engine, limit=body.limit, worker=worker)
    out = []
    for job in jobs:
        crop = db.get(OcrCrop, job.crop_id)
        out.append({
            "job_id": job.id, "crop_id": job.crop_id, "engine": job.engine, "kind": job.kind,
            "candidates": job.candidates_json or [], "attempt": job.attempts,
            "zone_class": crop.zone_class, "charset": crop.charset,
            "width_px": crop.width_px, "height_px": crop.height_px, "ink_ratio": crop.ink_ratio,
            "image_sha256": crop.image_sha256,
            "image_url": f"/api/ocr-worker/crops/{crop.id}/image",
        })
    return {"jobs": out}


def crop_file(crop: OcrCrop) -> Path:
    """Chemin du PNG d'une découpe (relatif à data_dir) ; refuse toute sortie du dossier."""
    root = settings.data_dir.resolve()
    path = (root / crop.image_path).resolve()
    if not crop.image_path or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Image de découpe introuvable")
    return path


@router.get("/crops/{crop_id}/image")
def crop_image(crop_id: str, db: Session = Depends(get_db), _worker: str = Depends(worker_auth)):
    crop = db.get(OcrCrop, crop_id)
    if crop is None:
        raise HTTPException(404, "Découpe inconnue")
    return FileResponse(crop_file(crop), media_type="image/png",
                        headers={"X-Image-Sha256": crop.image_sha256, "Cache-Control": "no-store"})


class ResultIn(BaseModel):
    job_id: str
    engine: str | None = None
    raw_text: str = ""
    illegible: bool = False
    confidence_raw: float | None = None
    logprob_mean: float | None = None
    logprob_min: float | None = None
    alternatives: list = []
    scores: dict = {}
    engine_version: str = ""
    weights: str = ""
    backend: str = ""
    device: str = ""
    family: str = ""
    latency_ms: float | None = None
    error: str = ""


class ResultsIn(BaseModel):
    results: list[ResultIn] = Field(max_length=ocr_queue.MAX_CLAIM * 2)


@router.post("/results")
def results(body: ResultsIn, db: Session = Depends(get_db), worker: str = Depends(worker_auth)):
    return ocr_queue.post_results(db, [r.model_dump() for r in body.results], worker)

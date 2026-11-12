"""File de tâches de la lecture OCR multicouche.

Le serveur est la seule source de vérité : le worker (Mac) réserve des tâches
avec un bail, lit, puis poste ses résultats. Aucun modèle ne tourne ici.

- réservation sans double attribution : sur PostgreSQL `SELECT ... FOR UPDATE
  SKIP LOCKED`, et sur les deux bases une mise à jour conditionnelle
  (`WHERE status='pending'`, rowcount 1) qui fait foi ;
- bail expiré : la tâche repart en file, 3 essais au plus, puis `error` et la
  découpe passe en revue manuelle ;
- résultat idempotent par `job_id`.

La valeur attendue n'existe nulle part ici : ni dans une tâche, ni dans un
résultat.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import OcrCrop, OcrDecision, OcrEngineRead, OcrJob, ResponseZone, SystemSetting
from . import runtime_settings as rs
from .ocr_surface import to_surface

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_CLAIM = 64
MAX_RAW_CHARS = 4000
WORKER_OFFLINE_AFTER = timedelta(minutes=2)
_TOUCH_EVERY = timedelta(seconds=10)
_SEEN_KEY = "ocr_worker_last_seen"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime | None) -> datetime | None:
    """SQLite rend des dates sans fuseau ; elles sont toujours en UTC."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ------------------------------------------------------------- présence

def touch_worker(db: Session, worker: str = "") -> None:
    """Mémorise le dernier appel du worker (au plus une écriture toutes les 10 s)."""
    row = db.get(SystemSetting, _SEEN_KEY)
    now = _now()
    if row is None:
        db.add(SystemSetting(key=_SEEN_KEY, value_json={"at": now.isoformat(), "worker": worker}))
        db.commit()
        return
    last = _parse((row.value_json or {}).get("at"))
    if last is None or _naive(now) - last >= _TOUCH_EVERY:
        row.value_json = {"at": now.isoformat(), "worker": worker}
        row.version = (row.version or 0) + 1
        db.commit()


def _parse(value) -> datetime | None:
    try:
        return _naive(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def last_worker_seen_at(db: Session) -> datetime | None:
    row = db.get(SystemSetting, _SEEN_KEY)
    return _parse((row.value_json or {}).get("at")) if row else None


def worker_online(db: Session) -> bool:
    seen = last_worker_seen_at(db)
    return seen is not None and _naive(_now()) - seen <= WORKER_OFFLINE_AFTER


# ------------------------------------------------------------- tâches

def engine_role(plan: dict, zone_class: str, engine: str) -> str | None:
    """stage1 | stage2 | observe, ou None si le moteur n'est pas au plan."""
    roles = (plan.get("classes") or {}).get(zone_class) or {}
    for role in ("stage1", "stage2", "observe"):
        if engine in roles.get(role, []):
            return role
    return None


def enqueue_jobs(db: Session, crop: OcrCrop, engines: list[str], kind: str = "read",
                 candidates: list[str] | None = None) -> list[OcrJob]:
    """Crée une tâche par moteur (sans doublon pour un même moteur et un même type
    tant qu'une tâche `read` existe déjà)."""
    existing = {(j.engine, j.kind) for j in db.query(OcrJob).filter_by(crop_id=crop.id).all()}
    out = []
    for engine in engines:
        if kind == "read" and (engine, kind) in existing:
            continue
        job = OcrJob(crop_id=crop.id, engine=engine, kind=kind,
                     candidates_json=list(candidates or []))
        db.add(job)
        out.append(job)
    db.flush()
    return out


def pending_counts(db: Session, engine: str | None = None) -> dict[str, int]:
    q = db.query(OcrJob.engine, OcrJob.id).filter(OcrJob.status == "pending")
    if engine:
        q = q.filter(OcrJob.engine == engine)
    counts: dict[str, int] = {}
    for eng, _id in q.all():
        counts[eng] = counts.get(eng, 0) + 1
    return counts


def claim_jobs(db: Session, *, engine: str, limit: int, worker: str,
               lease_minutes: int | None = None) -> list[OcrJob]:
    limit = max(1, min(int(limit), MAX_CLAIM))
    lease = timedelta(minutes=lease_minutes or rs.ocr_setting(db, "ocr_lease_minutes"))
    q = (db.query(OcrJob).filter(OcrJob.engine == engine, OcrJob.status == "pending")
         .order_by(OcrJob.created_at, OcrJob.id).limit(limit))
    if db.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    won: list[str] = []
    until = _now() + lease
    for job in q.all():
        n = db.execute(
            update(OcrJob).where(OcrJob.id == job.id, OcrJob.status == "pending")
            .values(status="claimed", claimed_by=worker[:80], lease_until=until,
                    attempts=OcrJob.attempts + 1)).rowcount
        if n == 1:
            won.append(job.id)
    db.commit()
    if not won:
        return []
    db.expire_all()
    return db.query(OcrJob).filter(OcrJob.id.in_(won)).order_by(OcrJob.created_at, OcrJob.id).all()


def _force_manual(db: Session, crop: OcrCrop, reason: str) -> None:
    """Une découpe dont un moteur de vote échoue durablement part en manuel :
    jamais de note automatique sur une lecture incomplète."""
    if crop.status != "decided":
        db.add(OcrDecision(crop_id=crop.id, zone_id=crop.zone_id, batch_id=crop.batch_id,
                           stage_reached="manual", decision="manual", reason=reason,
                           mode=crop.mode or "shadow"))
        crop.status = "decided"


def _fail_job(db: Session, job: OcrJob, reason: str, plan: dict) -> str:
    """Remet en file ou, au 3e échec, passe en `error` (et en manuel si le moteur vote)."""
    if job.attempts < MAX_ATTEMPTS:
        job.status, job.claimed_by, job.lease_until = "pending", None, None
        return "requeued"
    job.status, job.lease_until = "error", None
    crop = db.get(OcrCrop, job.crop_id)
    if crop is not None and engine_role(plan, crop.zone_class, job.engine) in ("stage1", "stage2"):
        _force_manual(db, crop, f"engine_error:{job.engine}:{reason}")
    return "error"


def release_expired(db: Session) -> dict[str, int]:
    plan = rs.ocr_engine_plan(db)
    out = {"requeued": 0, "error": 0}
    expired = (db.query(OcrJob).filter(OcrJob.status == "claimed", OcrJob.lease_until < _now())
               .all())
    for job in expired:
        out[_fail_job(db, job, "lease_expired", plan)] += 1
    db.commit()
    return out


# ------------------------------------------------------------- résultats

def _f(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def post_results(db: Session, results: list[dict], worker: str = "") -> dict[str, int]:
    """Enregistre les résultats. Idempotent : un `job_id` déjà traité est compté
    comme doublon sans rien réécrire. Un résultat tardif (bail expiré, tâche
    remise en file) reste accepté tant que la tâche n'est pas terminée."""
    plan = rs.ocr_engine_plan(db)
    out = {"accepted": 0, "duplicates": 0, "ignored": 0, "requeued": 0}
    touched: set[str] = set()
    for res in results:
        job = db.get(OcrJob, str(res.get("job_id") or ""))
        if job is None or job.status == "cancelled" or (res.get("engine") and res["engine"] != job.engine):
            out["ignored"] += 1
            continue
        if job.status in ("done", "error") or db.query(OcrEngineRead.id).filter_by(job_id=job.id).first():
            out["duplicates"] += 1
            continue
        error = str(res.get("error") or "")[:1000]
        if error:
            db.add(OcrEngineRead(
                crop_id=job.crop_id, job_id=None, engine=job.engine, kind=job.kind, error=error,
                engine_version=str(res.get("engine_version") or "")[:80],
                backend=str(res.get("backend") or "")[:40], device=str(res.get("device") or "")[:40],
                family=str(res.get("family") or "")[:40], latency_ms=_f(res.get("latency_ms"))))
            if _fail_job(db, job, error[:120], plan) == "requeued":
                out["requeued"] += 1
            else:
                out["accepted"] += 1
            touched.add(job.crop_id)
            continue
        crop_row = db.get(OcrCrop, job.crop_id)
        raw_text = str(res.get("raw_text") or "")[:MAX_RAW_CHARS]
        scores = res.get("scores") if isinstance(res.get("scores"), dict) else {}
        alts = res.get("alternatives") if isinstance(res.get("alternatives"), list) else []
        db.add(OcrEngineRead(
            crop_id=job.crop_id, job_id=job.id, engine=job.engine, kind=job.kind,
            engine_version=str(res.get("engine_version") or "")[:80],
            weights=str(res.get("weights") or "")[:160],
            backend=str(res.get("backend") or "")[:40], device=str(res.get("device") or "")[:40],
            family=str(res.get("family") or "")[:40],
            raw_text=raw_text,
            surface=to_surface(raw_text, crop_row.zone_class) if crop_row is not None else "",
            illegible=bool(res.get("illegible")),
            confidence_raw=_f(res.get("confidence_raw")), logprob_mean=_f(res.get("logprob_mean")),
            logprob_min=_f(res.get("logprob_min")), alternatives_json=alts[:10],
            scores_json={str(k)[:MAX_RAW_CHARS]: _f(v) for k, v in scores.items()},
            latency_ms=_f(res.get("latency_ms"))))
        job.status, job.lease_until = "done", None
        out["accepted"] += 1
        touched.add(job.crop_id)
    db.flush()
    for crop_id in touched:
        crop = db.get(OcrCrop, crop_id)
        open_jobs = (db.query(OcrJob.id).filter(OcrJob.crop_id == crop_id,
                                                OcrJob.status.in_(("pending", "claimed"))).first())
        if crop is not None and crop.status == "pending" and open_jobs is None:
            crop.status = "local_done"
    db.commit()
    if touched:
        try:                                         # la cascade avance ; un échec ne perd jamais un résultat
            from . import ocr_cascade
            ocr_cascade.advance_after_results(db, sorted(touched))
        except Exception:
            logger.exception("ocr_cascade.advance_after_results")
            db.rollback()
    return out


# --------------------------------------------------- relâchement des baux

_started = False
_LEASE_TICK_S = 60


def _loop() -> None:
    import time
    while True:
        time.sleep(_LEASE_TICK_S)
        db = SessionLocal()
        try:
            release_expired(db)
        except Exception:                      # jamais fatal au thread
            logger.exception("ocr_queue: relâchement des baux")
            db.rollback()
        finally:
            db.close()


def start_lease_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="kironina-ocr-leases").start()

import traceback as _traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import Base, SessionLocal, engine, run_migrations
from .routers import (
    assessments, auth, connectors, content, data_admin, grades,
    indigo as indigo_router, misc, org, printing, scans, setup, students, system,
    users_admin, operations, ocr_worker,
)
from .seed import seed
from .services import errorlog, indigo, job_worker, mail_intake, scan_originals
from .services.bootstrap import ensure_strong_secrets

app = FastAPI(title="Kiro & Nina", version="0.9.0",
              description="Plateforme NAS de génération, correction automatisée "
                          "et suivi adaptatif en mathématiques")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

for r in (setup.router, auth.router, org.router, assessments.router, scans.router,
          students.router, misc.router, printing.router, system.router,
          connectors.router, content.router, data_admin.router, grades.router,
          indigo_router.router, users_admin.router, operations.router,
          ocr_worker.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def _capture_unhandled(request: Request, exc: Exception):
    """Toute exception NON gérée (500) est tracée dans le journal (Paramètres →
    Journaux) avec sa stack complète, et son message réel est renvoyé au client
    au lieu d'un « Internal Server Error » opaque. Les HTTPException (4xx/erreurs
    métier volontaires) gardent leur propre gestionnaire et ne passent pas ici."""
    detail = f"{type(exc).__name__}: {exc}"
    errorlog.record(method=request.method, path=request.url.path,
                    error=detail, traceback=_traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": detail})


def _warn_edition_mismatch(db):
    """Édition autonome sur une base qui contient des établissements ou des
    comptes Atelier : probablement une base de centre lancée par erreur. On ne
    bloque pas le démarrage (les profs autonomes doivent garder l'accès), on le
    signale dans le journal (Paramètres admin → Journaux)."""
    if settings.edition != "autonome":
        return
    from .models import School, User
    schools = db.query(School).filter_by(service_active=True).count()
    atelier = db.query(User).filter_by(role="corrector", active=True).count()
    if schools or atelier:
        errorlog.record(method="STARTUP", path="edition",
                        error=f"Édition autonome mais {schools} établissement(s) actif(s) et "
                              f"{atelier} compte(s) Atelier actif(s) : base de centre ?",
                        traceback="")


@app.on_event("startup")
def startup():
    if settings.edition not in ("centre", "autonome"):
        raise RuntimeError(f"MATHPRINT_EDITION invalide : {settings.edition!r} (centre | autonome)")
    Base.metadata.create_all(engine)
    run_migrations()
    ensure_strong_secrets()
    db = SessionLocal()
    try:
        seed(db)
        from .services.education import seed_structure
        seed_structure(db)
        indigo.seed_published(db)   # exercices Indigo publiés -> banque (tous déploiements)
        job_worker.resume_stuck_jobs(db)
        indigo.resume_stuck(db)
        scans.fail_interrupted_sandbox_jobs(db)
        _warn_edition_mismatch(db)
    finally:
        db.close()
    scan_originals.prune()      # scans déposés : conservés 30 jours, pas plus
    job_worker.start_worker()
    indigo.start_worker()
    mail_intake.start_worker()
    from .services import ocr_queue
    ocr_queue.start_lease_worker()   # baux OCR expirés : remise en file, 3 essais puis manuel
    from .services import ocr_cascade
    ocr_cascade.start_worker()       # fusion, Mathpix en dernier recours, reprise des lots
    from .services import card_warm
    card_warm.start_worker()   # aperçus de cartes pré-rendus, en pause pendant une correction


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.9.0", "edition": settings.edition}

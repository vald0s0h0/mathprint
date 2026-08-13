import traceback as _traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import Base, SessionLocal, engine, run_migrations
from .routers import (
    assessments, auth, connectors, content, data_admin, grades,
    indigo as indigo_router, misc, org, printing, scans, setup, students, system,
)
from .seed import seed
from .services import errorlog, indigo, job_worker
from .services.bootstrap import ensure_strong_secrets

app = FastAPI(title="MathPrint", version="0.9.0",
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
          indigo_router.router):
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


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    run_migrations()
    ensure_strong_secrets()
    db = SessionLocal()
    try:
        seed(db)
        indigo.seed_published(db)   # exercices Indigo publiés -> banque (tous déploiements)
        job_worker.resume_stuck_jobs(db)
        indigo.resume_stuck(db)
    finally:
        db.close()
    job_worker.start_worker()
    indigo.start_worker()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.9.0"}

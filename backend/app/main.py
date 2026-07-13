from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, SessionLocal, engine, run_migrations
from .routers import (
    assessments, auth, content, data_admin, misc, org, printing, scans, setup,
    students, system,
)
from .seed import seed
from .services import job_worker
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
          content.router, data_admin.router):
    app.include_router(r)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    run_migrations()
    ensure_strong_secrets()
    db = SessionLocal()
    try:
        seed(db)
        job_worker.resume_stuck_jobs(db)
    finally:
        db.close()
    job_worker.start_worker()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.9.0"}

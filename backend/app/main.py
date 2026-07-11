from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, SessionLocal, engine, run_migrations
from .routers import assessments, auth, misc, org, printing, scans, students, system
from .seed import seed

app = FastAPI(title="MathPrint", version="0.9.0",
              description="Plateforme NAS de génération, correction automatisée "
                          "et suivi adaptatif en mathématiques")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

for r in (auth.router, org.router, assessments.router, scans.router,
          students.router, misc.router, printing.router, system.router):
    app.include_router(r)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.9.0"}

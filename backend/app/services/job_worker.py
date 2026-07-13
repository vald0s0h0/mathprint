"""Worker de fond in-process pour la génération de sujets (assistant, étape
finale « Générer le sujet ») : la modale ferme immédiatement, la génération
(qui peut déclencher des appels DeepSeek/Claude via exercise_gen.ensure_bank
si la banque est insuffisante) tourne dans ce thread, hors requête HTTP.

Un seul job traité à la fois (déploiement mono-conteneur, cf. cahier des
charges) : la file est la table `jobs` existante, un simple thread daemon la
draine. `resume_stuck_jobs` remet en file, au redémarrage, tout job resté
`running` — signe d'un process tué en plein travail (BackgroundTasks ne
survit pas à un crash, contrairement à cette table)."""
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Assessment, Job

logger = logging.getLogger(__name__)

_TYPE = "assessment_generation"
_wake = threading.Event()
_started = False

_LOG_MAX_CHARS = 120_000  # ~1500 lignes ; on tronque par le début au-delà


class _JobLogHandler(logging.Handler):
    """Capture les logs `app.*` émis pendant un job et les persiste dans
    jobs.log_text via une session dédiée (commit immédiat, indépendant de la
    transaction du job) : le bouton « Voir log » de l'écran Sujets lit cette
    colonne en direct, même pendant un long appel LLM."""

    def __init__(self, job_id: str):
        super().__init__(level=logging.INFO)
        self.job_id = job_id
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(message)s",
                                            datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            return
        text = "\n".join(self.lines)
        if len(text) > _LOG_MAX_CHARS:
            text = text[-_LOG_MAX_CHARS:]
        # Échec d'écriture toléré en silence (ex. SQLite : la transaction du
        # job tient le verrou d'écriture) : les lignes restent en mémoire et
        # le texte complet est réécrit au prochain message — rien n'est perdu.
        # En Postgres (prod), chaque message est visible immédiatement.
        try:
            db = SessionLocal()
            try:
                db.execute(update(Job).where(Job.id == self.job_id)
                           .values(log_text=text))
                db.commit()
            finally:
                db.close()
        except Exception:
            pass


def enqueue_generation(db: Session, assessment: Assessment, font_size: int = 10) -> Job:
    job = Job(type=_TYPE, status="pending", assessment_id=assessment.id,
              payload_json={"font_size": font_size})
    db.add(job)
    assessment.status = "queued"
    db.commit()
    _wake.set()
    return job


def latest_job(db: Session, assessment_id: str) -> Job | None:
    return (db.query(Job).filter_by(type=_TYPE, assessment_id=assessment_id)
            .order_by(Job.created_at.desc()).first())


def active_jobs(db: Session) -> list[Job]:
    return (db.query(Job).filter(Job.type == _TYPE, Job.status.in_(("pending", "running")))
            .order_by(Job.created_at).all())


def _claim(db: Session, job: Job) -> bool:
    n = (db.query(Job).filter_by(id=job.id, status="pending")
         .update({"status": "running", "attempts": Job.attempts + 1,
                  "updated_at": datetime.now(timezone.utc)}))
    db.commit()
    return n == 1


def _run_job(db: Session, job: Job) -> None:
    from . import generation  # import tardif : évite un cycle au chargement du module

    assessment = db.get(Assessment, job.assessment_id)
    if assessment is None:
        job.status = "failed"
        job.error_code = "assessment_introuvable"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        return
    assessment.status = "generating"
    db.commit()
    # tout ce que les services app.* loguent (INFO+) pendant ce job est
    # recopié dans jobs.log_text, consultable depuis l'écran Sujets
    handler = _JobLogHandler(job.id)
    app_logger = logging.getLogger("app")
    app_logger.addHandler(handler)
    try:
        logger.info("Génération du sujet « %s » (tentative %s)",
                    assessment.title, job.attempts)
        font_size = (job.payload_json or {}).get("font_size", 10)
        generation.generate_assessment_job(db, assessment, job, font_size)
        job.status = "done"
        job.progress = 100
        assessment.status = "ready"
        logger.info("Sujet « %s » prêt", assessment.title)
    except Exception as e:
        logger.exception("Échec génération sujet %s", assessment.id)
        db.rollback()
        job = db.get(Job, job.id)
        assessment = db.get(Assessment, job.assessment_id)
        job.status = "failed"
        job.error_code = str(e)[:400]
        assessment.status = "error"
        assessment.error_message = str(e)[:400]
    finally:
        app_logger.removeHandler(handler)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def _drain() -> None:
    db = SessionLocal()
    try:
        while True:
            job = (db.query(Job).filter_by(type=_TYPE, status="pending")
                   .order_by(Job.created_at).first())
            if not job or not _claim(db, job):
                break
            try:
                _run_job(db, job)
            except Exception:
                logger.exception("Exception non capturée dans _run_job")
                db.rollback()
            db.expunge_all()
    finally:
        db.close()


def _loop() -> None:
    while True:
        _wake.wait(timeout=3)
        _wake.clear()
        try:
            _drain()
        except Exception:
            logger.exception("Boucle du worker de génération interrompue par une erreur")


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    # niveau INFO sur le package app : nécessaire pour que _JobLogHandler
    # reçoive les messages de progression (le root logger est à WARNING)
    logging.getLogger("app").setLevel(logging.INFO)
    threading.Thread(target=_loop, daemon=True, name="mathprint-job-worker").start()


def resume_stuck_jobs(db: Session, max_attempts: int = 3) -> int:
    """Au redémarrage : tout job resté `running` vient d'un process tué en
    plein travail. Remis en file, sauf s'il a déjà échoué trop de fois
    (évite une boucle de crash infinie)."""
    n = 0
    for job in db.query(Job).filter_by(type=_TYPE, status="running").all():
        assessment = db.get(Assessment, job.assessment_id) if job.assessment_id else None
        if job.attempts >= max_attempts:
            job.status = "failed"
            job.error_code = "crash_loop"
            if assessment:
                assessment.status = "error"
                assessment.error_message = "Échecs répétés après redémarrage"
        else:
            job.status = "pending"
            if assessment and assessment.status == "generating":
                assessment.status = "queued"
        job.updated_at = datetime.now(timezone.utc)
        n += 1
    db.commit()
    return n

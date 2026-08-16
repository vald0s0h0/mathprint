"""Présence et expiration des travaux des connecteurs d'impression."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import ConnectorPrintJob, PrintConnector

CONNECTOR_ONLINE_TIMEOUT = timedelta(seconds=90)
CONNECTOR_JOB_TIMEOUT = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def connector_online(connector: PrintConnector, *, at: datetime | None = None) -> bool:
    seen = as_utc(connector.last_seen_at)
    return bool(
        connector.active
        and seen
        and seen >= (at or utcnow()) - CONNECTOR_ONLINE_TIMEOUT
    )


def _job_document_path(job: ConnectorPrintJob) -> Path | None:
    root = settings.data_dir.resolve()
    path = (settings.data_dir / job.document_relpath).resolve()
    if path != root and root not in path.parents:
        return None
    return path


def expire_stale_queued_jobs(
    db: Session, *, connector_id: str | None = None, user_id: str | None = None,
) -> int:
    """Annule les travaux qu'aucun connecteur n'a réclamés à temps.

    Un travail déjà ``claimed`` n'est jamais touché : à partir de cet instant,
    le journal local et la file d'impression du système font autorité.
    """
    cutoff = utcnow() - CONNECTOR_JOB_TIMEOUT
    query = db.query(ConnectorPrintJob).filter(
        ConnectorPrintJob.status == "queued",
        ConnectorPrintJob.created_at < cutoff,
    )
    if connector_id:
        query = query.filter(ConnectorPrintJob.connector_id == connector_id)
    if user_id:
        query = query.filter(ConnectorPrintJob.user_id == user_id)
    jobs = query.all()
    if not jobs:
        return 0
    now = utcnow()
    for job in jobs:
        job.status = "cancelled"
        job.error_message = "Le poste n'a pas récupéré le travail dans les 5 minutes"
        job.updated_at = now
    db.commit()
    for job in jobs:
        path = _job_document_path(job)
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return len(jobs)

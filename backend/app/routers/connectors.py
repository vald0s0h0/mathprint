"""Connecteur d'impression local macOS/Windows.

Le connecteur ne publie aucun port : il s'authentifie puis réclame les travaux
par HTTPS sortant. Les PDF servis ici sont immuables et déjà préparés par le
serveur (ordre des pages et passe recto/verso compris).
"""
import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import (Assessment, AuditLog, ConnectorPrintJob, PrintConnector,
                      Printer, ScanBatch, User)
from ..services.security import verify_password

router = APIRouter(prefix="/api/connectors", tags=["print-connectors"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def current_connector(authorization: str = Header(default=""),
                      db: Session = Depends(get_db)) -> PrintConnector:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Connecteur non authentifié")
    digest = _token_hash(authorization.removeprefix("Bearer "))
    connector = db.query(PrintConnector).filter_by(token_hash=digest, active=True).first()
    if not connector:
        raise HTTPException(401, "Connecteur déconnecté")
    user = db.get(User, connector.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Compte inactif")
    return connector


class ConnectorLoginIn(BaseModel):
    email: str
    password: str
    installation_id: str = Field(min_length=16, max_length=128)
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default="", max_length=32)
    arch: str = Field(default="", max_length=32)
    app_version: str = Field(default="", max_length=32)


@router.post("/login")
def login_connector(body: ConnectorLoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email.lower().strip()).first()
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants invalides")

    connector = db.query(PrintConnector).filter_by(
        installation_id=body.installation_id).first()
    obsolete_jobs: list[ConnectorPrintJob] = []
    if connector is None:
        connector = PrintConnector(
            user_id=user.id, installation_id=body.installation_id,
            name=body.device_name.strip())
        db.add(connector)
    elif connector.user_id != user.id:
        # Un même poste peut changer de professeur. Les travaux de l'ancien
        # compte ne doivent jamais sortir après cette nouvelle association.
        obsolete_jobs = (db.query(ConnectorPrintJob)
                         .filter(ConnectorPrintJob.connector_id == connector.id,
                                 ConnectorPrintJob.status.in_(("queued", "claimed")))
                         .all())
        (db.query(ConnectorPrintJob)
         .filter(ConnectorPrintJob.connector_id == connector.id,
                 ConnectorPrintJob.status == "queued")
         .update({ConnectorPrintJob.status: "cancelled",
                  ConnectorPrintJob.error_message: "Connecteur associé à un autre compte",
                  ConnectorPrintJob.updated_at: _utcnow()}, synchronize_session=False))
        (db.query(ConnectorPrintJob)
         .filter(ConnectorPrintJob.connector_id == connector.id,
                 ConnectorPrintJob.status == "claimed")
         .update({ConnectorPrintJob.status: "uncertain",
                  ConnectorPrintJob.error_message:
                      "Compte changé pendant l'envoi ; vérifier la file système",
                  ConnectorPrintJob.updated_at: _utcnow()}, synchronize_session=False))
        connector.user_id = user.id
        connector.printers_json = []

    token = secrets.token_urlsafe(48)
    connector.name = body.device_name.strip()
    connector.platform = body.platform.strip().lower()
    connector.arch = body.arch.strip().lower()
    connector.app_version = body.app_version.strip()
    connector.token_hash = _token_hash(token)
    connector.active = True
    connector.last_seen_at = _utcnow()
    db.commit()
    for job in obsolete_jobs:
        _delete_job_document(job)
    return {
        "token": token,
        "connector": {
            "id": connector.id, "name": connector.name,
            "email": user.email, "display_name": user.display_name,
        },
    }


@router.post("/logout")
def logout_connector(connector: PrintConnector = Depends(current_connector),
                     db: Session = Depends(get_db)):
    now = _utcnow()
    obsolete_jobs = (db.query(ConnectorPrintJob)
                     .filter(ConnectorPrintJob.connector_id == connector.id,
                             ConnectorPrintJob.status.in_(("queued", "claimed")))
                     .all())
    connector.active = False
    connector.last_seen_at = now
    (db.query(ConnectorPrintJob)
     .filter(ConnectorPrintJob.connector_id == connector.id,
             ConnectorPrintJob.status == "queued")
     .update({ConnectorPrintJob.status: "cancelled",
              ConnectorPrintJob.error_message: "Connecteur déconnecté",
              ConnectorPrintJob.updated_at: now}, synchronize_session=False))
    (db.query(ConnectorPrintJob)
     .filter(ConnectorPrintJob.connector_id == connector.id,
             ConnectorPrintJob.status == "claimed")
     .update({ConnectorPrintJob.status: "uncertain",
              ConnectorPrintJob.error_message: "Déconnexion pendant l'envoi",
              ConnectorPrintJob.updated_at: now}, synchronize_session=False))
    for printer in db.query(Printer).filter_by(protocol="connector").all():
        if (printer.capabilities_json or {}).get("connector_id") == connector.id:
            printer.active = False
    db.commit()
    for job in obsolete_jobs:
        _delete_job_document(job)
    return {"ok": True}


class LocalPrinterIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    is_default: bool = False


class HeartbeatIn(BaseModel):
    app_version: str = Field(default="", max_length=32)
    printers: list[LocalPrinterIn] = Field(default_factory=list, max_length=100)


def _valid_native_printer_name(name: str) -> str:
    value = name.strip()
    if not value or any(ord(char) < 32 for char in value):
        raise HTTPException(422, "Nom d'imprimante local invalide")
    return value


def _profile_name(connector_id: str, native_name: str) -> str:
    suffix = hashlib.sha256(native_name.encode("utf-8")).hexdigest()[:16]
    return f"connector-{connector_id[:8]}-{suffix}"


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn,
              connector: PrintConnector = Depends(current_connector),
              db: Session = Depends(get_db)):
    now = _utcnow()
    connector.last_seen_at = now
    if body.app_version:
        connector.app_version = body.app_version

    reported: list[dict] = []
    active_profile_names: set[str] = set()
    seen_native: set[str] = set()
    for raw in body.printers:
        native_name = _valid_native_printer_name(raw.name)
        if native_name in seen_native:
            continue
        seen_native.add(native_name)
        internal_name = _profile_name(connector.id, native_name)
        active_profile_names.add(internal_name)
        profile = db.query(Printer).filter_by(name=internal_name).first()
        if profile is None:
            profile = Printer(name=internal_name, protocol="connector", active=True)
            db.add(profile)
        profile.protocol = "connector"
        profile.uri = f"connector://{connector.id}"
        profile.active = True
        profile.capabilities_json = {
            "connector_id": connector.id,
            "device_name": connector.name,
            "native_name": native_name,
            "platform": connector.platform,
            "is_default": raw.is_default,
        }
        reported.append({"name": native_name, "is_default": raw.is_default})

    for profile in db.query(Printer).filter_by(protocol="connector").all():
        caps = profile.capabilities_json or {}
        if caps.get("connector_id") == connector.id and profile.name not in active_profile_names:
            profile.active = False
    connector.printers_json = reported
    db.commit()
    return {"ok": True, "connector_id": connector.id,
            "pending": db.query(ConnectorPrintJob).filter_by(
                connector_id=connector.id, status="queued").count()}


def _job_view(job: ConnectorPrintJob, *, include_download: bool = False) -> dict:
    view = {
        "id": job.id, "title": job.title, "file_name": job.file_name,
        "pass_side": job.pass_side, "printer": job.native_printer_name,
        "status": job.status, "options": job.options_json or {},
        "sha256": job.document_sha256, "size": job.document_size,
        "spool_job_id": job.spool_job_id, "error": job.error_message,
        "created_at": str(job.created_at), "updated_at": str(job.updated_at),
    }
    if include_download:
        view["download_url"] = f"/api/connectors/jobs/{job.id}/file"
    return view


@router.post("/jobs/claim")
def claim_job(connector: PrintConnector = Depends(current_connector),
              db: Session = Depends(get_db)):
    connector.last_seen_at = _utcnow()
    # Après un redémarrage, rendre d'abord le job déjà réclamé. Le journal
    # local du connecteur décidera de le poursuivre ou de le déclarer incertain.
    job = (db.query(ConnectorPrintJob)
           .filter_by(connector_id=connector.id, status="claimed")
           .order_by(ConnectorPrintJob.created_at).first())
    if job is None:
        job = (db.query(ConnectorPrintJob)
               .filter_by(connector_id=connector.id, status="queued")
               .order_by(ConnectorPrintJob.created_at).first())
        if job:
            job.status = "claimed"
            job.claimed_at = _utcnow()
            job.updated_at = _utcnow()
    db.commit()
    return {"job": _job_view(job, include_download=True) if job else None}


def _job_document_path(job: ConnectorPrintJob) -> Path:
    root = settings.data_dir.resolve()
    path = (settings.data_dir / job.document_relpath).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(404, "Document invalide")
    return path


def _delete_job_document(job: ConnectorPrintJob):
    """Supprime seulement la copie de spool immuable, jamais le PDF source."""
    try:
        _job_document_path(job).unlink(missing_ok=True)
    except (HTTPException, OSError):
        # Le statut terminal reste l'autorité. Une panne de nettoyage ne doit
        # ni réactiver un job ni transformer un succès d'impression en échec.
        pass


@router.get("/jobs/{job_id}/file")
def download_job(job_id: str,
                 connector: PrintConnector = Depends(current_connector),
                 db: Session = Depends(get_db)):
    job = db.get(ConnectorPrintJob, job_id)
    if not job or job.connector_id != connector.id or job.status != "claimed":
        raise HTTPException(404, "Travail inconnu")
    path = _job_document_path(job)
    if not path.exists() or path.stat().st_size != job.document_size:
        raise HTTPException(404, "Document d'impression absent")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"mathprint-{job.id}.pdf",
                        headers={"X-Content-SHA256": job.document_sha256})


class JobResultIn(BaseModel):
    status: str
    spool_job_id: str = Field(default="", max_length=300)
    error: str = Field(default="", max_length=1000)


def _mark_business_success(db: Session, job: ConnectorPrintJob):
    assessment = db.get(Assessment, job.assessment_id) if job.assessment_id else None
    if job.file_name == "subject_batch.pdf" and job.pass_side in ("all", "verso"):
        if assessment and assessment.status == "ready":
            assessment.status = "printed"
    if job.file_name == "correction_overlay.pdf" and job.assessment_id:
        (db.query(ScanBatch).filter(ScanBatch.assessment_id == job.assessment_id)
         .update({ScanBatch.overlay_printed: True}, synchronize_session=False))


@router.post("/jobs/{job_id}/result")
def report_job(job_id: str, body: JobResultIn,
               connector: PrintConnector = Depends(current_connector),
               db: Session = Depends(get_db)):
    if body.status not in ("submitted", "failed", "uncertain"):
        raise HTTPException(422, "État de travail invalide")
    job = db.get(ConnectorPrintJob, job_id)
    if not job or job.connector_id != connector.id:
        raise HTTPException(404, "Travail inconnu")
    if job.status in ("submitted", "failed", "uncertain", "cancelled"):
        # Résultat idempotent : un retry réseau ne doit jamais modifier ni
        # relancer un travail déjà terminal.
        _delete_job_document(job)
        return {"ok": True, "status": job.status}
    if job.status != "claimed":
        raise HTTPException(409, "Travail non réclamé")

    now = _utcnow()
    job.status = body.status
    job.updated_at = now
    job.error_message = body.error.strip() or None
    job.spool_job_id = body.spool_job_id.strip()
    if body.status == "submitted":
        job.submitted_at = now
        _mark_business_success(db, job)
        db.add(AuditLog(
            actor_id=job.user_id, action="print", entity_type="assessment",
            entity_id=job.assessment_id or "test",
            after_json={
                "connector_id": connector.id,
                "device": connector.name,
                "printer": job.native_printer_name,
                "file": job.file_name,
                "pass_side": job.pass_side,
                "spool_job_id": job.spool_job_id,
                "options": job.options_json,
            }))
    db.commit()
    _delete_job_document(job)
    return {"ok": True, "status": job.status}


@router.get("/jobs")
def list_jobs(connector: PrintConnector = Depends(current_connector),
              db: Session = Depends(get_db)):
    rows = (db.query(ConnectorPrintJob).filter_by(connector_id=connector.id)
            .order_by(ConnectorPrintJob.created_at.desc()).limit(20).all())
    return [_job_view(row) for row in rows]

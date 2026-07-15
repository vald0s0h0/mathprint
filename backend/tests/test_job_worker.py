"""Tests du filet de sécurité de job_worker : délai global de génération et
suppression du sujet pendant que le worker travaille encore dessus (cf.
incident « Copie 1/5 » bloqué 20 min sans log, Sésamaths)."""
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import Assessment, Job, SchoolClass
from app.services import job_worker


@pytest.fixture
def db_session(monkeypatch):
    # StaticPool + check_same_thread=False : un seul :memory: partagé entre
    # le thread de test et le thread _GENERATION_POOL qui exécute le job.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(job_worker, "SessionLocal", Session)
    session = Session()
    yield session
    session.close()


def _seed_assessment(db) -> tuple[Assessment, Job]:
    cls = SchoolClass(name="Test", grade_level="5e")
    db.add(cls)
    db.flush()
    assessment = Assessment(class_id=cls.id, title="Sans titre", status="queued",
                            blueprint_json={"competency_ids": []})
    db.add(assessment)
    db.flush()
    job = Job(type="assessment_generation", status="running", assessment_id=assessment.id,
              attempts=1, payload_json={"font_size": 10})
    db.add(job)
    db.commit()
    return assessment, job


def test_job_timeout_marks_job_and_assessment_failed(db_session, monkeypatch):
    from app import config
    from app.services import generation

    monkeypatch.setattr(config.settings, "job_generation_timeout_s", 0.2)

    def _hang(db, assessment, job, font_size):
        time.sleep(2)  # largement au-delà du délai global de test

    monkeypatch.setattr(generation, "generate_assessment_job", _hang)

    assessment, job = _seed_assessment(db_session)
    job_worker._run_job(db_session, job)

    db_session.expire_all()
    refreshed_job = db_session.get(Job, job.id)
    refreshed_assessment = db_session.get(Assessment, assessment.id)
    assert refreshed_job.status == "failed"
    assert "délai global dépassé" in refreshed_job.error_code
    assert refreshed_assessment.status == "error"
    assert "délai global dépassé" in refreshed_assessment.error_message

    # le thread abandonné continue seul et ne doit rien casser en terminant
    time.sleep(2)


def test_job_deleted_mid_run_does_not_crash_worker(db_session, monkeypatch):
    from app.services import generation

    deleted_mid_flight = {}

    def _delete_then_run(db, assessment, job, font_size):
        # simule la suppression RGPD du sujet pendant que le worker le tient déjà
        deleted_mid_flight["ran"] = True
        db.query(Job).filter_by(id=job.id).delete()
        db.query(Assessment).filter_by(id=assessment.id).delete()
        db.commit()
        raise RuntimeError("échec après suppression concurrente")

    monkeypatch.setattr(generation, "generate_assessment_job", _delete_then_run)

    assessment, job = _seed_assessment(db_session)
    job_id, assessment_id = job.id, assessment.id
    job_worker._run_job(db_session, job)  # ne doit lever aucune exception

    assert deleted_mid_flight.get("ran") is True
    db_session.expunge_all()
    assert db_session.query(Job).filter_by(id=job_id).first() is None
    assert db_session.query(Assessment).filter_by(id=assessment_id).first() is None

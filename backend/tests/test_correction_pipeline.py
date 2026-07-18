"""Test d'intégration du chemin de correction manuelle bout en bout (chemin
mock, exercice `manual_drawing` toujours mis en revue) : process_batch ->
list_reviews (barème/groupe) -> resolve set_ratio -> finalize -> overlays.

N'appelle aucun réseau : manual_drawing ne déclenche pas d'OCR, et l'absence
de progrès/compétences dues évite l'appel d'appréciation."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (
    Assessment, Copy, CopyItem, CopyItemResult, DocumentPage, ResponseZone,
    ScanBatch, SchoolClass, Student, User,
)
from app.routers import scans as scans_router
from app.services import pipeline, scan_intake


@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_manual(db):
    cls = SchoolClass(name="5A", grade_level="5e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle", type="control", note_base=20)
    db.add(a)
    db.flush()
    for i in range(2):
        stu = Student(class_id=cls.id, first_name=f"E{i}", last_name="X", llm_pseudonym=f"p{i}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        item = CopyItem(
            copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
            response_type="manual_drawing", statement="Trace la figure.",
            correction="figure correcte",
            expected_json={"type": "manual"},
            grading_json={"comparator": "manual", "max_score": 1, "bareme_points": 1.5})
        db.add(item)
        db.flush()
        db.add(ResponseZone(page_id=page.id, item_id=item.id, type="drawing",
                            x_pt=50, y_pt=50, w_pt=100, h_pt=60, meta_json={}))
    db.commit()
    return a


def test_manual_correction_end_to_end(mock_db, tmp_path, monkeypatch):
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    user = User(email="prof@x.fr", password_hash="x", role="teacher")
    db.add(user)
    db.flush()

    # un seul batch pour le sujet, puis correction (chemin mock)
    batch = scan_intake.get_or_create_batch(db, a.id, user.id)
    db.commit()
    pipeline.process_batch(db, batch)

    # manual_drawing -> toujours une revue par copie
    reviews = scans_router.list_reviews(batch.id, None, db)
    assert len(reviews) == 2
    for r in reviews:
        assert r["bareme_points"] == 1.5
        assert r["group_key"].startswith("cat-1|")
        assert r["group_label"] == "Ex. 1"

    # 2/3 des points via le raccourci -> earned = 2/3 × 1,5 = 1,0
    body = scans_router.ResolveIn(action="set_ratio", ratio=2 / 3)
    for r in reviews:
        scans_router.resolve_review(r["review_id"], body, db, user)

    assert scans_router.list_reviews(batch.id, None, db) == []

    result = pipeline.finalize_batch(db, batch)
    db.commit()
    assert result["results_created"] == 2

    item_results = db.query(CopyItemResult).all()
    assert len(item_results) == 2
    for ir in item_results:
        assert abs(ir.points_earned - 1.0) < 1e-6

    # overlays générés dès la finalisation (aperçus disponibles)
    overlays = tmp_path / "assessments" / a.id / "overlays"
    assert (overlays / "correction_overlay.pdf").exists()
    assert (overlays / "correction_review.pdf").exists()
    assert db.get(ScanBatch, batch.id).status == "overlay_ready"


def test_review_all_and_response_resolve(mock_db, tmp_path, monkeypatch):
    """Voie « corriger manuellement » toujours accessible : scope=all expose
    TOUTES les réponses scannées (pas seulement les signalées) et la correction
    passe par response_id."""
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    user = User(email="p2@x.fr", password_hash="x", role="teacher")
    db.add(user)
    db.flush()
    batch = scan_intake.get_or_create_batch(db, a.id, user.id)
    db.commit()
    pipeline.process_batch(db, batch)

    all_items = scans_router.list_items(batch.id, "all", db)
    assert len(all_items) == 2
    assert all(it["response_id"] for it in all_items)
    assert all(it["flagged"] for it in all_items)  # manual_drawing → signalé
    assert len(scans_router.list_items(batch.id, "flagged", db)) == 2

    # correction d'une réponse par son id (voie « toutes les réponses »)
    body = scans_router.ResolveIn(action="set_ratio", ratio=1.0)
    scans_router.resolve_response(all_items[0]["response_id"], body, db, user)

    # la revue signalée correspondante est close ; scope=all la montre toujours,
    # désormais corrigée par le professeur à plein barème (1,5)
    assert len(scans_router.list_items(batch.id, "flagged", db)) == 1
    again = {it["response_id"]: it for it in scans_router.list_items(batch.id, "all", db)}
    fixed = again[all_items[0]["response_id"]]
    assert fixed["decision_source"] == "teacher"
    assert fixed["full_credit"] is True
    assert abs(fixed["current_points"] - 1.5) < 1e-6


def test_retry_clears_error_and_reschedules(mock_db, tmp_path, monkeypatch):
    """Bouton de déblocage : retry efface l'erreur et replanifie le pipeline."""
    from fastapi import BackgroundTasks

    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    batch.error = "Fichier scan introuvable"
    db.commit()

    tasks = BackgroundTasks()
    r = scans_router.retry_batch(batch.id, tasks, db)
    assert r["ok"] is True
    assert db.get(ScanBatch, batch.id).error is None
    assert len(tasks.tasks) == 1  # pipeline replanifié en tâche de fond

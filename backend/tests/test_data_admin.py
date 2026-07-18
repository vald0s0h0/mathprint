"""Paramètres → Données : suppression SANS orphelins + recensement/nettoyage.

La vraie exigence : supprimer un sujet (ou une correction) doit emporter TOUT
ce qui en dérive — copies, résultats consolidés, scans, overlays — sans laisser
une seule ligne pointant vers un parent disparu. On le prouve sur un sujet
réellement corrigé de bout en bout (le graphe le plus complet), d'où le besoin
du vrai manuel 5.pdf (création Gemini ancrée dans ses pages, cf. test_scoring).
Les tests de find_orphans/purge_orphans purs, eux, tournent sans manuel.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, CopyItemResult,
    CopyResult, GradingDecision, ManualReview, SchoolClass, ScanBatch, Student,
)
from app.services import data_admin, generation, pipeline

MANUAL_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "manuals" / "5.pdf"
needs_manual = pytest.mark.skipif(not MANUAL_PATH.exists(), reason="manuel 5.pdf absent")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sesamaths_manuals", {"5e": str(MANUAL_PATH)})
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_finalized(db) -> Assessment:
    """Un sujet 5e corrigé et finalisé (donc avec résultats consolidés)."""
    fw = CompetencyFramework(grade_level="5e", name="Test 5e")
    db.add(fw); db.flush()
    for short_id, label, order in [("A1.1", "Automatismes", 0),
                                   ("A1.2", "Divisions euclidiennes", 1)]:
        db.add(Competency(framework_id=fw.id, code=short_id, short_id=short_id,
                          label=label, domain_code="A", domain_name="Nombres et calculs",
                          chapter_code="A1", chapter_name="Opérations", order_index=order))
    comps = db.query(Competency).all()
    cls = SchoolClass(name="5eD", grade_level="5e"); db.add(cls); db.flush()
    for i in range(2):
        db.add(Student(class_id=cls.id, first_name=f"E{i}", last_name="T",
                       llm_pseudonym=f"E{i}", active=True))
    a = Assessment(class_id=cls.id, type="control", title="Sujet", pages_target=1,
                   personalization_mode="common", note_base=20)
    a.blueprint_json = {"competency_ids": [c.id for c in comps], "exercise_source": "gemini"}
    db.add(a); db.commit()

    generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()
    batch = ScanBatch(assessment_id=a.id); db.add(batch); db.commit()
    pipeline.process_batch(db, batch)
    for r in db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).all():
        old = db.get(GradingDecision, r.decision_id)
        db.add(GradingDecision(response_id=old.response_id, source="teacher", score=0.0,
                               max_score=old.max_score, confidence=1.0, tier="D",
                               reason_code="teacher_set_score", status="validated"))
        old.status = "revised"
        r.resolved_at = datetime.now(timezone.utc)
    db.commit()
    pipeline.finalize_batch(db, batch)
    db.commit()
    return a


@needs_manual
def test_delete_assessment_leaves_no_orphans(db):
    a = _seed_finalized(db)
    assert db.query(CopyResult).filter_by(assessment_id=a.id).count() == 2   # consolidé
    assert db.query(CopyItemResult).count() > 0

    data_admin.delete_assessment(db, a)
    db.commit()

    # le sujet et TOUT ce qui en dérive ont disparu…
    assert db.query(Assessment).count() == 0
    assert db.query(Copy).count() == 0
    assert db.query(CopyItem).count() == 0
    assert db.query(CopyResult).count() == 0            # <- la faille corrigée
    assert db.query(CopyItemResult).count() == 0
    assert db.query(ScanBatch).count() == 0
    # …sans laisser le moindre orphelin dans la base
    assert data_admin.find_orphans(db) == []


@needs_manual
def test_delete_correction_drops_consolidated_results_without_orphans(db):
    a = _seed_finalized(db)
    batch = db.query(ScanBatch).filter_by(assessment_id=a.id).first()
    assert db.query(CopyResult).count() == 2

    data_admin.delete_scan_batch(db, batch)
    db.commit()

    # la correction (résultats consolidés compris) est partie, les copies restent
    assert db.query(CopyResult).count() == 0
    assert db.query(CopyItemResult).count() == 0
    assert db.query(Copy).count() == 2
    assert all(c.status == "generated" for c in db.query(Copy).all())
    assert data_admin.find_orphans(db) == []


# --------------------------------------------- find/purge orphans (sans manuel)

def _plain_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_find_orphans_detects_dangling_rows():
    db = _plain_db()
    # une copie sans sujet ni élève, un résultat consolidé sans copie
    db.add(Copy(id="c1", assessment_id="ghost", student_id="ghost"))
    db.add(CopyResult(id="r1", copy_id="ghost", assessment_id="ghost", student_id="ghost"))
    db.flush()
    labels = {o["label"] for o in data_admin.find_orphans(db)}
    assert "Copies sans sujet" in labels
    assert "Résultats de copie sans copie" in labels


def test_purge_orphans_removes_them_and_reports_count():
    db = _plain_db()
    db.add(Copy(id="c1", assessment_id="ghost", student_id="ghost"))
    db.add(CopyResult(id="r1", copy_id="ghost", assessment_id="ghost", student_id="ghost"))
    db.flush()
    result = data_admin.purge_orphans(db)
    db.flush()
    assert result["deleted"] >= 2
    assert data_admin.find_orphans(db) == []
    assert db.query(Copy).count() == 0 and db.query(CopyResult).count() == 0


def test_purge_orphans_spares_valid_rows():
    db = _plain_db()
    fw = CompetencyFramework(grade_level="5e", name="T"); db.add(fw); db.flush()
    cls = SchoolClass(name="5eE", grade_level="5e"); db.add(cls); db.flush()
    stu = Student(class_id=cls.id, first_name="A", last_name="B", llm_pseudonym="p")
    db.add(stu); db.flush()
    db.add(Copy(id="orphan", assessment_id="ghost", student_id="ghost"))
    db.flush()

    data_admin.purge_orphans(db); db.flush()
    # l'élève, la classe et le référentiel bien rattachés survivent
    assert db.query(Student).count() == 1
    assert db.query(SchoolClass).count() == 1
    assert db.query(CompetencyFramework).count() == 1
    assert db.query(Copy).count() == 0            # seul l'orphelin est parti

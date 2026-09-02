"""L'endpoint qui rend le suivi VÉRIFIABLE : ce que le professeur lit dans
l'onglet Historique doit être exactement ce que lit le moteur de sujets.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, get_db
from app.deps import current_user
from app.main import app
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, CopyItemResult,
    CopyResult, SchoolClass, Student, User,
)

NOW = datetime.now(timezone.utc)


@pytest.fixture
def client():
    # StaticPool + check_same_thread : TestClient sert la requête dans un autre
    # thread que celui qui a semé la base (cf. tests/test_users_admin.py)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[current_user] = lambda: User(
        id="u1", email="prof@test.fr", role="teacher")
    yield TestClient(app), session
    app.dependency_overrides.clear()
    session.close()


def _seed(db, n=3):
    fw = CompetencyFramework(grade_level="3e", name="T")
    db.add(fw)
    db.flush()
    comp = Competency(framework_id=fw.id, code="A1.1", short_id="A1.1",
                      label="Fractions", domain_code="A", domain_name="Nombres",
                      chapter_code="A1", chapter_name="Opérations")
    db.add(comp)
    cls = SchoolClass(name="3eA", grade_level="3e")
    db.add(cls)
    db.flush()
    st = Student(class_id=cls.id, name="Alex", llm_pseudonym="E-1", active=True)
    db.add(st)
    a = Assessment(class_id=cls.id, type="control", title="Contrôle 1")
    db.add(a)
    db.flush()
    copy = Copy(assessment_id=a.id, student_id=st.id)
    db.add(copy)
    db.flush()
    res = CopyResult(copy_id=copy.id, assessment_id=a.id, student_id=st.id)
    db.add(res)
    db.flush()
    for i in range(n):
        item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=i,
                        generated_exercise_id=f"ex{i}", difficulty=(i % 3 + 1) * 3,
                        response_type="short_text", statement="x", correction="")
        db.add(item)
        db.flush()
        db.add(CopyItemResult(
            copy_result_id=res.id, copy_item_id=item.id, competency_id=comp.id,
            student_id=st.id, generated_exercise_id=f"ex{i}",
            sequence=i, response_type="short_text", difficulty=(i % 3 + 1) * 3,
            difficulty_level=i % 3 + 1, answer_text=f"réponse {i}",
            success_ratio=1.0 if i == 0 else 0.0,
            occurred_at=NOW - timedelta(days=i),
            score=1.0, max_score=1.0, bareme_points=1.0, points_earned=1.0))
    db.commit()
    return st


def test_history_returns_date_level_answer_and_success(client):
    api, db = client
    st = _seed(db)
    body = api.get(f"/api/students/{st.id}/history").json()
    assert body["total"] == 3
    first = body["items"][0]
    assert first["competency_label"] == "Fractions"
    assert first["assessment_title"] == "Contrôle 1"
    assert first["answer_text"] == "réponse 0"
    assert first["difficulty_level"] == 1
    assert first["success_ratio"] == 1.0
    assert first["occurred_at"]
    assert first["exercise_id"] == "ex0"


def test_history_is_newest_first(client):
    api, db = client
    st = _seed(db)
    items = api.get(f"/api/students/{st.id}/history").json()["items"]
    dates = [i["occurred_at"] for i in items]
    assert dates == sorted(dates, reverse=True)


def test_history_of_an_unknown_student_is_404(client):
    api, _db = client
    assert api.get("/api/students/nope/history").status_code == 404


def test_history_is_empty_not_broken_for_a_new_student(client):
    api, db = client
    cls = SchoolClass(name="3eB", grade_level="3e")
    db.add(cls)
    db.flush()
    st = Student(class_id=cls.id, name="Neuf", llm_pseudonym="E-2", active=True)
    db.add(st)
    db.commit()
    body = api.get(f"/api/students/{st.id}/history").json()
    assert body == {"items": [], "total": 0}


def test_student_detail_exposes_the_priority_that_drives_selection(client):
    api, db = client
    from app.models import StudentCompetencyState
    st = _seed(db)
    comp = db.query(Competency).first()
    db.add(StudentCompetencyState(
        student_id=st.id, competency_id=comp.id, mastery=0.9, confidence=0.9,
        stability=20.0, last_seen_at=NOW - timedelta(days=120)))
    db.commit()
    row = api.get(f"/api/students/{st.id}").json()["competencies"][0]
    # maîtrisée il y a 4 mois : la maîtrise brute reste haute, la priorité aussi
    assert row["mastery"] == 0.9
    assert row["strength"] < 0.1
    assert row["priority"] > 0.9

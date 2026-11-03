"""Filtres de la barre du haut : ce que l'utilisateur enseigne, et le catalogue Exercices."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.db import Base
from app.models import (
    ClassTeaching, Competency, CompetencyFramework, Program, SchoolClass, Subject, TeacherProfile, User,
)
from app.routers.auth import me
from app.routers.indigo import competencies, summary
from app.services.education import GRADES, teaching_scope


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add_all([Program(id="fr", name="Programme français", origin_country_id="FR"),
                     Program(id="ci", name="Programme ivoirien", origin_country_id="CI"),
                     Subject(id="math", name="Mathématiques"), Subject(id="pc", name="Physique")])
    session.flush()
    yield session
    session.close()


def _user(db, email="emile@x.fr", role="admin"):
    user = User(email=email, password_hash="x", display_name="Emile", role=role)
    db.add(user); db.flush()
    return user


def _profile(db, user):
    profile = TeacherProfile(name=user.display_name, service_mode="autonomous", user_id=user.id)
    db.add(profile); db.flush()
    return profile


def _class(db, name, grade, *, teacher=None, subject="math", program="fr", archived=False):
    cls = SchoolClass(name=name, grade_level=grade, program_id=program,
                      archived_at=_models.now() if archived else None)
    db.add(cls); db.flush()
    if teacher:
        db.add(ClassTeaching(class_id=cls.id, subject_id=subject, teacher_id=teacher.id))
        db.flush()
    return cls


def test_only_what_the_teacher_teaches_is_offered(db):
    user = _user(db)
    profile = _profile(db, user)
    colleague = _profile(db, _user(db, "autre@x.fr"))
    for name, grade in (("3e7", "3e"), ("6e3", "6e"), ("3e6", "3e"), ("6e4", "6e")):
        _class(db, name, grade, teacher=profile)
    _class(db, "5eA", "5e", teacher=colleague, subject="pc", program="ci")
    _class(db, "4eA", "4e", teacher=profile, archived=True)

    # Dans l'ordre scolaire, pas dans l'ordre de création.
    assert teaching_scope(db, user) == {
        "cycles": ["6e", "3e"], "subjects": [{"id": "math", "name": "Mathématiques"}], "programs": ["fr"]}
    assert me(user, db)["teaching"]["cycles"] == ["6e", "3e"]


def test_without_own_teaching_everything_visible_is_offered(db):
    teacher = _profile(db, _user(db))
    _class(db, "5eA", "5e", teacher=teacher, subject="pc", program="ci")
    _class(db, "6eA", "6e")  # classe sans enseignement affecté
    admin = _user(db, "admin@x.fr")
    assert teaching_scope(db, admin) == {
        "cycles": ["6e", "5e"], "subjects": [{"id": "pc", "name": "Physique"}], "programs": ["ci", "fr"]}


def test_a_blank_instance_offers_the_whole_catalog(db):
    assert teaching_scope(db, _user(db)) == {
        "cycles": list(GRADES),
        "subjects": [{"id": "math", "name": "Mathématiques"}, {"id": "pc", "name": "Physique"}],
        "programs": ["ci", "fr"]}


def _framework(db, program, subject, grade, code, status="published"):
    framework = CompetencyFramework(program_id=program, subject_id=subject, grade_level=grade,
                                    name=code, status=status)
    db.add(framework); db.flush()
    db.add(Competency(framework_id=framework.id, code=code, short_id=code, label=code,
                      domain_code="D", domain_name="D", chapter_code="C", chapter_name="C"))
    db.flush()


@pytest.mark.parametrize("program, subject, expected", [
    ("fr", "math", {"FR-M"}), ("ci", "math", {"CI-M"}), ("all", "math", {"FR-M", "CI-M"}),
    ("fr", "pc", {"FR-P"}), ("ci", "pc", set()),
])
def test_exercise_catalog_filters_program_and_subject(db, program, subject, expected):
    _framework(db, "fr", "math", "6e", "FR-M")
    _framework(db, "ci", "math", "6e", "CI-M")
    _framework(db, "fr", "pc", "6e", "FR-P")
    _framework(db, "fr", "math", "3e", "AUTRE-CYCLE")
    _framework(db, "fr", "math", "6e", "ARCHIVE", status="archived")
    listed = competencies("6e", db, program, subject)["competencies"]
    assert {row["code"] for row in listed} == expected
    assert {row["short_id"] for row in summary("6e", db, program, subject)} == expected


def test_exercise_catalog_defaults_to_the_french_math_manual(db):
    _framework(db, "fr", "math", "6e", "FR-M")
    _framework(db, "ci", "math", "6e", "CI-M")
    assert [row["code"] for row in competencies("6e", db)["competencies"]] == ["FR-M"]
    assert [row["short_id"] for row in summary("6e", db)] == ["FR-M"]


def test_first_account_is_an_autonomous_teacher(db):
    from app.routers.setup import SetupIn, create_first_admin
    create_first_admin(SetupIn(email="Emile@x.fr", display_name="Emile", password="motdepasse"), db)
    user = db.query(User).one()
    profile = db.query(TeacherProfile).filter_by(user_id=user.id).one()
    assert user.role == "admin" and profile.service_mode == "autonomous"
    assert profile.name == "Emile" and profile.email == "emile@x.fr" and profile.program_id is None
    # Aucun cycle tant qu'il n'a pas de classe, tout le catalogue à choisir.
    assert teaching_scope(db, user)["cycles"] == []

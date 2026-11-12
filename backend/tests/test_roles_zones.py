"""Trois rôles (admin, Atelier = corrector, prof), zones de l'interface et
édition de l'instance (centre | autonome), via les vrais droits HTTP."""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.db import Base, get_db
from app.models import (ClassTeaching, School, SchoolClass, SchoolProgram, SchoolYear,
                        SystemSetting, TeacherProfile, TeacherSchool, User)
from app.routers import (auth, misc, operations, org, students, system, users_admin)
from app.services.education import seed_structure
from app.services.security import make_token


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "edition", "centre")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as db:
        seed_structure(db)
        year = SchoolYear(label="2026-2027", active=True)
        admin = User(email="admin@test.fr", password_hash="x", role="admin")
        atelier = User(email="atelier@test.fr", password_hash="x", role="corrector")
        prof = User(email="prof@test.fr", password_hash="x", role="teacher")
        db.add_all([year, admin, atelier, prof]); db.flush()
        # l'admin est aussi prof autonome (cas réel), avec une classe à lui
        mine = TeacherProfile(name="Admin", service_mode="autonomous", user_id=admin.id,
                              program_id="fr", subject_id="math")
        # un établissement accompagné avec sa classe
        school = School(name="École A", country_id="CI")
        accompanied = TeacherProfile(name="Camille", service_mode="accompanied", user_id=prof.id)
        db.add_all([mine, school, accompanied]); db.flush()
        db.add_all([SchoolProgram(school_id=school.id, program_id="fr"),
                    TeacherSchool(teacher_id=accompanied.id, school_id=school.id)])
        own = SchoolClass(name="Ma 3e", grade_level="3e", school_year_id=year.id,
                          teacher_id=admin.id, program_id="fr")
        other = SchoolClass(name="4A", grade_level="4e", school_year_id=year.id,
                            teacher_id=prof.id, school_id=school.id, program_id="fr")
        db.add_all([own, other]); db.flush()
        db.add_all([ClassTeaching(class_id=own.id, subject_id="math", teacher_id=mine.id),
                    ClassTeaching(class_id=other.id, subject_id="math", teacher_id=accompanied.id)])
        db.commit()
        ids = {"own": own.id, "other": other.id, "school": school.id, "year": year.id}
        tokens = {u.role: {"Authorization": "Bearer " + make_token(u)} for u in (admin, atelier, prof)}
    app = FastAPI()
    for mod in (auth, misc, operations, org, students, system, users_admin):
        app.include_router(mod.router)

    def get_session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    return TestClient(app), factory, tokens, ids


def test_atelier_reads_management_but_only_admin_writes(env):
    client, _, t, ids = env
    a = t["corrector"]
    assert client.get("/api/operations/schools", headers=a).status_code == 200
    assert client.get("/api/operations/teachers", headers=a).status_code == 200
    assert client.get("/api/operations/dashboard", headers=a).status_code == 200
    assert client.get("/api/classes", headers=a).status_code == 200
    school = {"name": "École B", "country_id": "CI", "program_ids": ["fr"]}
    assert client.post("/api/operations/schools", headers=a, json=school).status_code == 403
    assert client.post("/api/operations/years", headers=a, json={"label": "2030-2031"}).status_code == 403
    assert client.post("/api/classes", headers=a, json={
        "name": "4B", "grade_level": "4e", "school_id": ids["school"], "program_id": "fr"}).status_code == 403
    assert client.patch(f"/api/classes/{ids['other']}", headers=a, json={"name": "x"}).status_code == 403
    assert client.post(f"/api/classes/{ids['other']}/students", headers=a,
                       json={"name": "Zoé"}).status_code == 403
    assert client.post("/api/admin/users/correctors", headers=a, json={
        "display_name": "X", "email": "x@test.fr", "password": "Long-password-1"}).status_code == 403
    assert client.post("/api/settings/system", headers=a,
                       json={"key": "dropout_color", "value": {"value": "#000"}}).status_code == 403
    assert client.post("/api/operations/schools", headers=t["admin"], json=school).status_code == 200


def test_calibration_belongs_to_atelier_in_centre_edition(env, monkeypatch):
    client, _, t, _ = env
    assert client.get("/api/system/calibration/profiles", headers=t["corrector"]).status_code == 200
    assert client.get("/api/system/calibration/profiles", headers=t["admin"]).status_code == 200
    assert client.get("/api/system/calibration/profiles", headers=t["teacher"]).status_code == 403
    # sans Atelier, le prof autonome calibre lui-même
    monkeypatch.setattr(settings, "edition", "autonome")
    assert client.get("/api/system/calibration/profiles", headers=t["teacher"]).status_code == 200


def test_teacher_cannot_touch_global_settings(env):
    client, _, t, _ = env
    for path, body in (("/api/settings/system", {"key": "dropout_color", "value": {"value": "#000"}}),
                       ("/api/settings/providers", {"provider": "mathpix", "secret": "x"})):
        assert client.post(path, headers=t["teacher"], json=body).status_code == 403


def test_admin_user_zone_shows_only_own_classes(env):
    client, _, t, ids = env
    everything = {c["id"] for c in client.get("/api/classes", headers=t["admin"]).json()}
    assert everything == {ids["own"], ids["other"]}
    for zone in ("atelier", "admin"):
        h = {**t["admin"], "X-KN-Zone": zone}
        assert {c["id"] for c in client.get("/api/classes", headers=h).json()} == everything
    user = {**t["admin"], "X-KN-Zone": "user"}
    assert [c["id"] for c in client.get("/api/classes", headers=user).json()] == [ids["own"]]
    assert client.get(f"/api/classes/{ids['other']}/students", headers=user).status_code == 404
    # filtres du haut : ses cycles en zone user, ceux de toute l'instance à l'Atelier
    with_school = client.get("/api/auth/me", headers={**t["admin"], "X-KN-Zone": "atelier"}).json()
    assert with_school["teaching"]["cycles"] == ["4e", "3e"]
    assert client.get("/api/auth/me", headers=user).json()["teaching"]["cycles"] == ["3e"]
    # zone inconnue : ignorée (pas de filtrage), les droits suivent le rôle
    odd = {**t["admin"], "X-KN-Zone": "n'importe"}
    assert {c["id"] for c in client.get("/api/classes", headers=odd).json()} == everything


def test_zone_header_never_widens_a_teacher(env):
    client, _, t, ids = env
    for zone in ("atelier", "admin"):
        h = {**t["teacher"], "X-KN-Zone": zone}
        assert [c["id"] for c in client.get("/api/classes", headers=h).json()] == [ids["other"]]
        assert client.get("/api/operations/dashboard", headers=h).status_code == 403


def test_autonomous_edition_has_no_centre_routes(env, monkeypatch):
    client, _, t, _ = env
    monkeypatch.setattr(settings, "edition", "autonome")
    h = t["admin"]
    assert client.get("/api/operations/catalog", headers=h).status_code == 200
    for path in ("/api/operations/dashboard", "/api/operations/schools", "/api/operations/teachers"):
        assert client.get(path, headers=h).status_code == 404
    assert client.post("/api/admin/users/correctors", headers=h, json={
        "display_name": "X", "email": "x@test.fr", "password": "Long-password-1"}).status_code == 404
    # l'administration des profs autonomes reste disponible
    assert client.get("/api/admin/users/autonomous-teachers", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).json()["edition"] == "autonome"


def test_correction_shortcuts_are_personal_with_global_default(env):
    client, factory, t, _ = env
    with factory() as db:
        db.add(SystemSetting(key="correction_shortcuts", value_json={"full": "a", "zero": "z"}))
        db.commit()
    assert client.get("/api/auth/me/preferences", headers=t["teacher"]).json()[
        "correction_shortcuts"] == {"full": "a", "zero": "z"}
    r = client.put("/api/auth/me/preferences/correction_shortcuts", headers=t["teacher"],
                   json={"value": {"full": "f"}})
    assert r.status_code == 200
    assert r.json()["correction_shortcuts"] == {"full": "f", "zero": "z"}
    # chacun les siens : l'Atelier garde le défaut global
    assert client.get("/api/auth/me/preferences", headers=t["corrector"]).json()[
        "correction_shortcuts"] == {"full": "a", "zero": "z"}
    assert client.put("/api/auth/me/preferences/autre", headers=t["teacher"],
                      json={"value": {}}).status_code == 404

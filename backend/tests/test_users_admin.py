"""Contrat de la liste des utilisateurs réservée aux admins."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, get_db
from app.deps import current_user
from app.models import User
from app.routers.users_admin import list_users, router


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_list_users_exposes_roles_and_only_classic_subscriptions():
    db = _database()
    db.add_all([
        User(email="user@test.fr", password_hash="secret", display_name="Zoé",
             role="teacher", subscription_plan="max"),
        User(email="admin@test.fr", password_hash="must-not-leak", display_name="Admin",
             role="admin", subscription_plan=None),
        User(email="corrector@test.fr", password_hash="secret", display_name="Correcteur",
             role="corrector", subscription_plan=None),
    ])
    db.commit()

    result = list_users(db)

    assert [row["display_name"] for row in result] == ["Admin", "Correcteur", "Zoé"]
    assert [row["subscription_plan"] for row in result] == [None, None, "max"]
    assert all("password_hash" not in row for row in result)
    db.close()


def test_users_route_is_admin_only():
    db = _database()
    admin = User(email="admin@test.fr", password_hash="x", role="admin")
    teacher = User(email="user@test.fr", password_hash="x", role="teacher")
    db.add_all([admin, teacher])
    db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: teacher
    client = TestClient(app)

    assert client.get("/api/admin/users").status_code == 403

    app.dependency_overrides[current_user] = lambda: admin
    response = client.get("/api/admin/users")
    assert response.status_code == 200
    assert len(response.json()) == 2
    db.close()

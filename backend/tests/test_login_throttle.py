"""Limitation des tentatives de connexion : par compte, par IP, et via HTTP."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models import User
from app.routers import auth, connectors
from app.services import login_throttle as lt
from app.services.security import hash_password


@pytest.fixture(autouse=True)
def _reset():
    lt.reset()
    yield
    lt.reset()


def _fail(n, email="prof@example.fr", ip="203.0.113.7", now=0.0):
    for i in range(n):
        lt.record_failure(email, ip, now=now + i)


def test_account_is_locked_after_too_many_failures_then_released():
    _fail(lt.MAX_FAILURES_PER_EMAIL - 1)
    lt.check_allowed("prof@example.fr", "198.51.100.1", now=10)
    _fail(1, now=10)
    with pytest.raises(HTTPException) as err:
        lt.check_allowed("prof@example.fr", "198.51.100.1", now=11)
    assert err.value.status_code == 429
    assert int(err.value.headers["Retry-After"]) > 0
    lt.check_allowed("autre@example.fr", "198.51.100.1", now=11)
    lt.check_allowed("prof@example.fr", "198.51.100.1", now=10 + lt.LOCK_S + 1)


def test_failures_outside_the_window_do_not_count():
    _fail(lt.MAX_FAILURES_PER_EMAIL - 1, now=0)
    _fail(1, now=lt.WINDOW_S + 100)
    lt.check_allowed("prof@example.fr", "", now=lt.WINDOW_S + 101)


def test_success_resets_the_account_counter():
    _fail(lt.MAX_FAILURES_PER_EMAIL - 1)
    lt.record_success("prof@example.fr")
    _fail(lt.MAX_FAILURES_PER_EMAIL - 1, now=20)
    lt.check_allowed("prof@example.fr", "203.0.113.7", now=40)


def test_ip_is_locked_after_many_failures_on_different_accounts():
    for i in range(lt.MAX_FAILURES_PER_IP):
        lt.record_failure(f"compte{i}@example.fr", "203.0.113.9", now=i)
    with pytest.raises(HTTPException):
        lt.check_allowed("nouveau@example.fr", "203.0.113.9", now=40)
    lt.check_allowed("nouveau@example.fr", "198.51.100.2", now=40)


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(User(email="prof@example.fr", password_hash=hash_password("bon-mot-de-passe")))
        db.commit()

    def db_override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(connectors.router)
    app.dependency_overrides[get_db] = db_override
    return TestClient(app)


def test_http_login_returns_429_even_with_the_right_password_once_locked():
    client = _client()
    headers = {"X-Forwarded-For": "203.0.113.50, 10.0.0.2"}
    bad = {"email": "Prof@Example.fr ", "password": "faux"}
    for _ in range(lt.MAX_FAILURES_PER_EMAIL):
        assert client.post("/api/auth/login", json=bad, headers=headers).status_code == 401
    r = client.post("/api/auth/login", headers=headers,
                    json={"email": "prof@example.fr", "password": "bon-mot-de-passe"})
    assert r.status_code == 429
    assert "Trop de tentatives" in r.json()["detail"]
    assert "Retry-After" in r.headers
    # l'IP vue est celle du visiteur (premier X-Forwarded-For), pas celle du proxy
    assert ("ip", "203.0.113.50") in lt._failures


def test_http_login_succeeds_and_resets_before_the_threshold():
    client = _client()
    for _ in range(lt.MAX_FAILURES_PER_EMAIL - 1):
        client.post("/api/auth/login", json={"email": "prof@example.fr", "password": "faux"})
    r = client.post("/api/auth/login", json={"email": "prof@example.fr", "password": "bon-mot-de-passe"})
    assert r.status_code == 200 and r.json()["token"]
    assert ("email", "prof@example.fr") not in lt._failures


def test_connector_login_is_throttled_too():
    client = _client()
    body = {"email": "prof@example.fr", "password": "faux",
            "installation_id": "11111111-2222-3333-4444-555555555555",
            "device_name": "Poste", "platform": "macos", "arch": "aarch64", "app_version": "1.1.0"}
    for _ in range(lt.MAX_FAILURES_PER_EMAIL):
        assert client.post("/api/connectors/login", json=body).status_code == 401
    assert client.post("/api/connectors/login", json=body).status_code == 429

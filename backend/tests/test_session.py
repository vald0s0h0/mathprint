"""Session glissante de 30 jours, coupée par un changement de mot de passe."""
import sys
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.db import Base, get_db
from app.models import User
from app.routers import auth
from app.services.security import SESSION_TOKEN_HEADER, hash_password, make_token

PASSWORD = "ancien-mot-de-passe"
ME = "/api/auth/me"
DAY = 24 * 3600


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as db:
        user = User(email="prof@test.fr", password_hash=hash_password(PASSWORD), role="admin")
        db.add(user)
        db.commit()
    app = FastAPI()
    app.include_router(auth.router)

    def get_session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    return TestClient(app), user


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def aged(token: str, seconds: int) -> str:
    """Le même jeton, émis `seconds` plus tôt."""
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    payload["iat"] -= seconds
    payload["exp"] -= seconds
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def test_session_dure_30_jours(env):
    _, user = env
    payload = jwt.decode(make_token(user), settings.secret_key, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == 30 * DAY


def test_pas_de_renouvellement_le_jour_meme(env):
    client, user = env
    res = client.get(ME, headers=bearer(make_token(user)))
    assert res.status_code == 200
    assert SESSION_TOKEN_HEADER not in res.headers


def test_utilisation_apres_un_jour_renouvelle_pour_30_jours(env):
    client, user = env
    res = client.get(ME, headers=bearer(aged(make_token(user), 2 * DAY)))
    assert res.status_code == 200
    renewed = jwt.decode(res.headers[SESSION_TOKEN_HEADER], settings.secret_key, algorithms=["HS256"])
    assert renewed["exp"] > time.time() + 29 * DAY
    assert client.get(ME, headers=bearer(res.headers[SESSION_TOKEN_HEADER])).status_code == 200


def test_trente_jours_sans_utilisation_expire(env):
    client, user = env
    assert client.get(ME, headers=bearer(aged(make_token(user), 31 * DAY))).status_code == 401


def test_mauvais_mot_de_passe_actuel_ne_deconnecte_pas(env):
    client, user = env
    token = make_token(user)
    res = client.post(f"{ME}/password", headers=bearer(token),
                      json={"current_password": "erreur", "new_password": "nouveau-mot-de-passe"})
    assert res.status_code == 400
    assert res.json()["detail"] == "Mot de passe actuel incorrect"
    assert client.get(ME, headers=bearer(token)).status_code == 200


def test_changer_le_mot_de_passe_deconnecte_les_autres_appareils(env):
    client, user = env
    other_device = make_token(user)
    # Jeton de plus d'un jour : le renouvellement ne doit pas écraser le nouveau jeton.
    this_device = aged(make_token(user), 2 * DAY)
    res = client.post(f"{ME}/password", headers=bearer(this_device),
                      json={"current_password": PASSWORD, "new_password": "nouveau-mot-de-passe"})
    assert res.status_code == 200
    assert client.get(ME, headers=bearer(other_device)).status_code == 401
    assert client.get(ME, headers=bearer(this_device)).status_code == 401
    assert client.get(ME, headers=bearer(res.headers[SESSION_TOKEN_HEADER])).status_code == 200

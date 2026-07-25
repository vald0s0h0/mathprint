"""Journal des erreurs serveur : une 500 non gérée est tracée (méthode, chemin,
message réel, stack) et renvoyée au client avec son vrai message, tandis que les
HTTPException (4xx métier) gardent leur comportement et ne polluent pas le journal.
"""
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings as cfg
from app.services import errorlog


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    errorlog.clear()
    from fastapi.testclient import TestClient
    from app.main import app

    r = APIRouter()

    @r.get("/api/_boom")
    def _boom():
        raise ValueError("explosion de test")

    @r.get("/api/_notfound")
    def _nf():
        raise HTTPException(404, "rien ici")

    app.include_router(r)
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_500_is_logged_with_real_message(app_client):
    resp = app_client.get("/api/_boom")
    assert resp.status_code == 500
    # le client reçoit le VRAI message (plus « Internal Server Error » opaque)
    assert resp.json()["detail"] == "ValueError: explosion de test"
    entries = errorlog.tail()
    assert len(entries) == 1
    e = entries[0]
    assert e["method"] == "GET" and e["path"] == "/api/_boom"
    assert e["error"] == "ValueError: explosion de test"
    assert "explosion de test" in e["traceback"]  # stack complète capturée


def test_http_exceptions_are_not_logged(app_client):
    resp = app_client.get("/api/_notfound")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "rien ici"
    assert errorlog.tail() == []  # 4xx métier : hors journal des 500


def test_tail_orders_recent_first_and_clear_empties(app_client):
    app_client.get("/api/_boom")
    app_client.get("/api/_boom")
    assert len(errorlog.tail()) == 2
    errorlog.clear()
    assert errorlog.tail() == []

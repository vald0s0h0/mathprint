"""Moteur de test des modules OCR : SQLite en mémoire par défaut ; PostgreSQL si
`MATHPRINT_TEST_PG_URL` est défini (base JETABLE, vidée à chaque test) :

    MATHPRINT_TEST_PG_URL=postgresql+psycopg2://mathprint@127.0.0.1:55432/mathprint_test \\
        backend/.venv/bin/python -m pytest backend/tests/test_ocr_*.py

Le nom de la base doit contenir « test » : on refuse de vider autre chose.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.db import Base


def pg_url() -> str | None:
    url = os.environ.get("MATHPRINT_TEST_PG_URL")
    if url and "test" not in url.rsplit("/", 1)[-1]:
        raise RuntimeError("MATHPRINT_TEST_PG_URL doit viser une base dont le nom contient « test »")
    return url


def make_engine():
    """Moteur neuf avec le schéma complet, vide."""
    url = pg_url()
    if url:
        engine = create_engine(url, poolclass=NullPool)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        return engine
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def make_file_engine(tmp_path):
    """Pour les tests de concurrence : une connexion par thread (fichier SQLite ou PostgreSQL)."""
    url = pg_url()
    if url:
        return make_engine()
    engine = create_engine(f"sqlite:///{tmp_path/'q.db'}", connect_args={"timeout": 60})
    Base.metadata.create_all(engine)
    return engine


def ensure_catalog(db, cid: str = "c") -> str:
    """PostgreSQL applique les clés étrangères (SQLite non) : un `CopyItem` de test a besoin de sa
    ligne `exercise_catalog`."""
    from app.models import ExerciseCatalog
    if db.get(ExerciseCatalog, cid) is None:
        db.add(ExerciseCatalog(id=cid, provider_ref=cid, title="t", grade_level="3e", response_type="short_text"))
        db.flush()
    return cid

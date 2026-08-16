"""Régressions des migrations appliquées aux bases déjà déployées."""
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module


def test_users_gain_subscription_and_legacy_roles_are_normalized(monkeypatch):
    legacy_engine = create_engine("sqlite:///:memory:")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE users ("
            "id TEXT PRIMARY KEY, email TEXT NOT NULL, password_hash TEXT NOT NULL, "
            "display_name TEXT NOT NULL, role TEXT NOT NULL, active BOOLEAN NOT NULL, "
            "last_login_at TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO users VALUES "
            "('admin', 'admin@test.fr', 'x', 'Admin', 'admin', 1, NULL), "
            "('teacher', 'prof@test.fr', 'x', 'Prof', 'teacher', 1, NULL), "
            "('viewer', 'correcteur@test.fr', 'x', 'Correcteur', 'viewer', 1, NULL)"
        ))

    monkeypatch.setattr(db_module, "engine", legacy_engine)
    db_module.run_migrations()
    db_module.run_migrations()

    assert "subscription_plan" in {
        column["name"] for column in inspect(legacy_engine).get_columns("users")
    }
    with legacy_engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, role, subscription_plan FROM users ORDER BY id"
        )).fetchall()
    assert rows == [
        ("admin", "admin", None),
        ("teacher", "teacher", "free"),
        ("viewer", "corrector", None),
    ]


def test_legacy_classes_is_mock_column_is_dropped(monkeypatch):
    legacy_engine = create_engine("sqlite:///:memory:")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE classes ("
            "id TEXT PRIMARY KEY, school_year_id TEXT, name TEXT NOT NULL, "
            "grade_level TEXT NOT NULL, teacher_id TEXT, "
            "is_mock BOOLEAN NOT NULL, archived_at TIMESTAMP)"
        ))

    monkeypatch.setattr(db_module, "engine", legacy_engine)
    db_module.run_migrations()
    db_module.run_migrations()  # les redémarrages NAS doivent rester sans effet

    columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("classes")
    }
    assert "is_mock" not in columns

    # Reproduit l'INSERT émis par le modèle courant lors de la création d'une
    # classe. Il échouait avant la migration à cause du NOT NULL hérité.
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO classes "
            "(id, school_year_id, name, grade_level, teacher_id, archived_at) "
            "VALUES ('class-id', NULL, '3e1', '3e', NULL, NULL)"
        ))


def test_lesson_reminder_storage_is_dropped(monkeypatch):
    legacy_engine = create_engine("sqlite:///:memory:")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE lesson_snippets ("
            "id TEXT PRIMARY KEY, competency_id TEXT, title TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE copy_items ("
            "id TEXT PRIMARY KEY, lesson_snippet_id TEXT, item_order INTEGER)"
        ))

    monkeypatch.setattr(db_module, "engine", legacy_engine)
    db_module.run_migrations()
    db_module.run_migrations()

    inspector = inspect(legacy_engine)
    assert "lesson_snippets" not in inspector.get_table_names()
    assert "lesson_snippet_id" not in {
        column["name"] for column in inspector.get_columns("copy_items")
    }

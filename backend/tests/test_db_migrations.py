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


def test_difficulty_levels_are_folded_from_five_to_three_exactly_once(monkeypatch):
    """Passage de 5 à 3 niveaux (§ exercise_gen.DIFFICULTY_LEVELS).

    La conversion 1,2->1 / 3->2 / 4,5->3 n'est PAS idempotente : rejouée, elle
    ferait redescendre à 2 les exercices déjà passés à 3. D'où le marqueur en
    base — et d'où ce test, qui lance la migration DEUX fois.
    """
    legacy_engine = create_engine("sqlite:///:memory:")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE system_settings ("
            "key TEXT PRIMARY KEY, value_json JSON, version INTEGER, updated_by TEXT)"))
        connection.execute(text(
            "CREATE TABLE generated_exercises ("
            "id TEXT PRIMARY KEY, difficulty_level INTEGER NOT NULL)"))
        connection.execute(text(
            "CREATE TABLE indigo_exercises ("
            "id TEXT PRIMARY KEY, difficulty INTEGER NOT NULL)"))
        connection.execute(text(
            "INSERT INTO generated_exercises VALUES "
            "('a', 1), ('b', 2), ('c', 3), ('d', 4), ('e', 5)"))
        connection.execute(text(
            "INSERT INTO indigo_exercises VALUES ('p', 2), ('q', 3), ('r', 4)"))

    monkeypatch.setattr(db_module, "engine", legacy_engine)
    db_module.run_migrations()
    db_module.run_migrations()          # rejouée : ne doit PLUS rien convertir

    with legacy_engine.begin() as connection:
        levels = dict(connection.execute(text(
            "SELECT id, difficulty_level FROM generated_exercises")).all())
        indigo = dict(connection.execute(text(
            "SELECT id, difficulty FROM indigo_exercises")).all())
    assert levels == {"a": 1, "b": 1, "c": 2, "d": 3, "e": 3}
    assert indigo == {"p": 1, "q": 2, "r": 3}


def test_item_history_is_backfilled_on_an_already_corrected_database(monkeypatch, tmp_path):
    """Une base en service, avec des corrections déjà finalisées, doit se
    retrouver avec un historique exploitable — sans quoi le moteur de sujets
    individuels démarre aveugle sur tout le travail déjà fait.

    On sème avec l'ORM (schéma courant), puis on RETIRE les colonnes récentes
    pour reconstituer l'état d'avant, et on migre."""
    from datetime import datetime, timezone

    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import (
        Assessment, Copy, CopyItem, CopyItemResult, CopyResult, ResponseZone,
        SchoolClass, Student, StudentCompetencyState, StudentResponse,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(SchoolClass(id="cl", name="3eA", grade_level="3e"))
    s.add(Student(id="st", class_id="cl", name="Alex", llm_pseudonym="E-1"))
    # devoir fait le 4 mai, corrigé seulement le 20 : l'écart est le piège
    s.add(Assessment(id="a", class_id="cl", type="control", title="C1",
                     status="finalized",
                     scheduled_at=datetime(2026, 5, 4, 8, tzinfo=timezone.utc)))
    s.add(Copy(id="cp", assessment_id="a", student_id="st", status="finalized",
               generated_at=datetime(2026, 5, 4, 8, tzinfo=timezone.utc)))
    s.add(CopyItem(id="it", copy_id="cp", catalog_id="cat", sequence=1, difficulty=9,
                   response_type="short_text", statement="Calcule.", correction=""))
    s.add(ResponseZone(id="z", page_id="pg", item_id="it", type="short_text",
                       x_pt=0, y_pt=0, w_pt=10, h_pt=10))
    s.add(StudentResponse(id="sr", copy_item_id="it", zone_id="z", final_text="42"))
    s.add(CopyResult(id="cr", copy_id="cp", assessment_id="a", student_id="st",
                     points_earned=1.5, points_total=2.0, note_base=20,
                     note_raw=15.0, note=15.0,
                     finalized_at=datetime(2026, 5, 20, 18, tzinfo=timezone.utc)))
    s.add(CopyItemResult(id="cir", copy_result_id="cr", copy_item_id="it",
                         competency_id="comp", sequence=1, response_type="short_text",
                         difficulty=9, score=3.0, max_score=4.0, bareme_points=2.0,
                         points_earned=1.5))
    # état de mémorisation hérité du plafond unique de 10 ans
    s.add(StudentCompetencyState(student_id="st", competency_id="comp", mastery=0.5,
                                 confidence=0.9, stability=3650.0, memory_difficulty=5.0,
                                 last_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    s.commit()
    s.close()

    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_copy_item_results_student_comp"))
        for table, column in (("copy_items", "generated_exercise_id"),
                              ("copy_item_results", "student_id"),
                              ("copy_item_results", "generated_exercise_id"),
                              ("copy_item_results", "difficulty_level"),
                              ("copy_item_results", "answer_text"),
                              ("copy_item_results", "success_ratio"),
                              ("copy_item_results", "occurred_at")):
            c.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        c.execute(text("ALTER TABLE students ADD COLUMN next_plan_json JSON"))
        c.execute(text("ALTER TABLE students ADD COLUMN next_plan_updated_at TIMESTAMP"))

    monkeypatch.setattr(db_module, "engine", engine)
    db_module.run_migrations()
    db_module.run_migrations()          # rejouée : idempotente

    assert "next_plan_json" not in {
        col["name"] for col in inspect(engine).get_columns("students")}
    with engine.begin() as c:
        row = c.execute(text(
            "SELECT student_id, difficulty_level, success_ratio, occurred_at, "
            "answer_text, generated_exercise_id FROM copy_item_results")).first()
        state = c.execute(text(
            "SELECT stability FROM student_competency_state")).scalar()
        indexes = {r[1] for r in c.execute(text("PRAGMA index_list(copy_item_results)"))}
        assert c.execute(text("SELECT COUNT(*) FROM copy_item_results")).scalar() == 1

    assert row[0] == "st"
    assert row[1] == 3                        # difficulty 9 -> dérivé « difficile »
    assert row[2] == 0.75                     # 3/4
    # la date du DEVOIR (4 mai), surtout pas celle de la correction (20 mai)
    assert str(row[3]).startswith("2026-05-04")
    assert row[4] == "42"
    # jamais écrit avant : assumé non reconstructible, et NULL et non ""
    assert row[5] is None
    # stabilité ramenée sous le plafond de la maîtrise 0,5 (~46 j), pas 10 ans
    assert state < 60
    assert "ix_copy_item_results_student_comp" in indexes

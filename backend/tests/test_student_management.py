"""Nom unique, ordre de classe et adaptation dyslexie des élèves."""
import io
import sys
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module
from app.db import Base
from app.models import SchoolClass, Student
from app.routers import org
from app.services import pdfgen


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_pasted_names_stay_indivisible_and_keep_their_order():
    assert org._parse_students("Camille\nCamille B.\nCamille Durand\nDurand\n") == [
        "Camille", "Camille B.", "Camille Durand", "Durand",
    ]


def test_legacy_names_are_migrated_without_data_loss(monkeypatch):
    legacy_engine = create_engine("sqlite:///:memory:")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE students (id TEXT PRIMARY KEY, class_id TEXT, "
            "first_name TEXT NOT NULL, last_name TEXT NOT NULL, "
            "llm_pseudonym TEXT NOT NULL, active BOOLEAN, level_locked BOOLEAN)"))
        connection.execute(text(
            "INSERT INTO students VALUES "
            "('2','c','Camille','Durand','p2',1,0),"
            "('1','c','Alice','Bernard','p1',1,0)"))
    monkeypatch.setattr(db_module, "engine", legacy_engine)

    db_module.run_migrations()

    columns = {column["name"] for column in inspect(legacy_engine).get_columns("students")}
    with legacy_engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT name, order_index, dyslexic FROM students ORDER BY order_index"
        )).fetchall()
    assert "first_name" not in columns and "last_name" not in columns
    assert {"name", "order_index", "dyslexic"}.issubset(columns)
    assert rows == [("Bernard Alice", 0, False), ("Durand Camille", 1, False)]


def test_reorder_students_persists_exact_class_order(db):
    school_class = SchoolClass(name="5e A", grade_level="5e")
    db.add(school_class)
    db.flush()
    students = [
        Student(class_id=school_class.id, name=name, order_index=index,
                llm_pseudonym=f"p{index}")
        for index, name in enumerate(("Camille", "Jules", "Durand"))
    ]
    db.add_all(students)
    db.commit()

    org.reorder_students(
        school_class.id,
        org.StudentOrderIn(student_ids=[students[2].id, students[0].id, students[1].id]),
        db,
    )

    ordered = (db.query(Student).order_by(Student.order_index).all())
    assert [student.name for student in ordered] == ["Durand", "Camille", "Jules"]
    assert [student.order_index for student in ordered] == [0, 1, 2]


def _render(dyslexic: bool) -> tuple[list[dict], bytes]:
    output = io.BytesIO()
    pdf_canvas = canvas.Canvas(output, pagesize=A4)
    items = [{
        "kind": "exercise", "item_id": f"e{i}",
        "statement": "Calcule le quotient puis justifie ton résultat avec une phrase complète.",
        "correction": "Il faut diviser puis vérifier le résultat.",
        "response_type": "short_text", "choices": [], "level5": 3,
        "figure": None, "grading": {"max_score": 1, "comparator": "numeric"},
        "inline": False,
    } for i in range(5)]
    zones = pdfgen.render_copy(
        pdf_canvas, student_name="Camille Durand", class_name="5e A", title="Test",
        assessment_type="training", items=items,
        pages_meta=[{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(8)],
        dyslexic=dyslexic,
    )
    pdf_canvas.save()
    return zones, output.getvalue()


def test_dyslexic_pdf_embeds_font_without_changing_card_geometry():
    regular_zones, regular_pdf = _render(False)
    dyslexic_zones, dyslexic_pdf = _render(True)

    geometry = lambda zones: [
        (zone["page_index"], zone["x_pt"], zone["y_pt"], zone["w_pt"], zone["h_pt"])
        for zone in zones
    ]
    assert geometry(dyslexic_zones) == pytest.approx(geometry(regular_zones))
    assert b"OpenDyslexic" not in regular_pdf
    assert b"OpenDyslexic" in dyslexic_pdf

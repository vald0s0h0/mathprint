"""Overlay de correction enrichi (§ améliorations overlay) :

- la bande corrigé sous une carte est dimensionnée sur le TEXTE du corrigé
  (anticipée), jamais coupée, et son corps est plus petit que l'énoncé ;
- les cases QCM du sujet sont décalées à droite pour réserver, à leur gauche,
  la case de correction que l'overlay imprime en cas d'erreur ;
- les marques par champ (coche/croix, cases correction, traits de liaison) se
  dessinent sans erreur pour chaque type de réponse.
"""
import sys
import tempfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import grading, pdfgen


# ------------------------- placement inchangé malgré la bande dimensionnée

def test_pages_needed_matches_render_copy_with_correction():
    """La bande corrigé plus haute change la hauteur d'une carte : la
    simulation (pages_needed) doit rester d'accord avec le dessin réel."""
    items = [{"kind": "exercise", "item_id": f"e{i}",
              "statement": f"Calcule $ {i} \\times {i + 2} $.",
              "correction": ("Piège : la retenue.\nRepose l'opération en "
                             "colonnes, aligne bien les chiffres avant "
                             "d'additionner, sinon le résultat glisse d'un rang."),
              "response_type": "short_text", "choices": [], "level5": 3,
              "figure": None, "grading": {"max_score": 1, "comparator": "numeric"},
              "inline": False} for i in range(9)]
    out = Path(tempfile.mkdtemp()) / "copy.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    pages_meta = [{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(12)]
    zones = pdfgen.render_copy(c, student_name="Test", class_name="5A", title="T",
                               assessment_type="training", items=items,
                               pages_meta=pages_meta, font_size=9)
    c.save()
    real = max((z["page_index"] for z in zones), default=0) + 1
    tpl = pdfgen.DEFAULT_TEMPLATES
    heights = [pdfgen.estimate_item_height(i, 9, 12, tpl["exercise"], tpl["lesson"])
               for i in items]
    assert pdfgen.pages_needed(heights) == real


# --------------------------------------------------------- bande corrigé

def test_strip_height_anticipates_correction_text():
    short = pdfgen._correction_strip_layout("Résultat court.", pdfgen.COL_W, 9)
    long = pdfgen._correction_strip_layout(
        "Attention à la retenue.\nAligne les virgules avant d'additionner "
        "$3{,}5 + 1{,}8$.\nUne seule règle à la fois, ne mélange pas les unités.",
        pdfgen.COL_W, 9)
    assert short["height"] >= pdfgen.STRIP_MIN_H          # plancher respecté
    assert long["height"] > short["height"]               # anticipe le texte long
    assert short["fs"] < 9                                 # corrigé plus petit que l'énoncé


def test_empty_correction_falls_back_to_min_strip():
    strip = pdfgen._correction_strip_layout("", pdfgen.COL_W, 9)
    assert strip["height"] == pytest.approx(pdfgen.STRIP_MIN_H)


# ------------------------------------------------------------ cell_marks

def test_cell_marks_skips_given_and_marks_all_fields():
    grading_json = {"comparator": "table_cells", "cells": [
        [{"type": "integer", "value": 56},
         {"type": "integer", "value": 72, "given": True}],
        [{"type": "integer", "value": 12}]]}
    # 2 cellules NON données : 56 juste, 12 attendu mais '13' lu -> faux
    marks = grading.cell_marks(grading_json, ["56", "13"])
    assert marks == [True, False]


def test_cell_marks_unreadable_and_missing_count_as_wrong():
    grading_json = {"comparator": "table_cells", "cells": [
        [{"type": "integer", "value": 5}, {"type": "integer", "value": 9}]]}
    # 1re illisible, 2de absente de l'OCR -> les deux marquées fausses
    assert grading.cell_marks(grading_json, ["oups"]) == [False, False]


# ------------------------------------------------- décalage des cases QCM

def _render_qcm_zone() -> dict:
    items = [{"kind": "exercise", "item_id": "q", "statement": "Coche.",
              "correction": "Piège : signe.", "response_type": "qcm_single",
              "choices": ["un", "deux", "trois"], "level5": 2, "figure": None,
              "grading": {"comparator": "qcm", "max_score": 1}, "inline": False}]
    out = Path(tempfile.mkdtemp()) / "c.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    zones = pdfgen.render_copy(c, student_name="A B", class_name="5A", title="T",
                               assessment_type="training", items=items,
                               pages_meta=[{"page_id": "p0", "payload": "MP1|p0|0"}],
                               font_size=9)
    c.save()
    return zones[0]


def test_qcm_reserves_correction_box_left_of_student_box():
    zone = _render_qcm_zone()
    boxes = zone["meta"]["boxes"]
    assert boxes and all("correction_box" in b for b in boxes)
    for b in boxes:
        cb = b["correction_box"]
        # la case correction est à gauche de la case élève, séparée d'une marge
        assert cb["x_pt"] + cb["w_pt"] <= b["x_pt"]
        assert b["x_pt"] - (cb["x_pt"] + cb["w_pt"]) >= pdfgen.QCM_CORR_GAP - 0.5


# ------------------------------------------------- dessin des marques (smoke)

def _page(zones: list[dict]) -> dict:
    return {"student": "A B", "assessment_type": "control", "note": "12/20",
            "page_zones": zones}


def test_render_overlay_draws_every_mark_kind_without_error():
    strip = {"x_pt": 40, "y_pt": 100, "w_pt": 250, "h_pt": 11, "fs": 8}
    zones = [
        {"x_pt": 40, "y_pt": 400, "w_pt": 120, "h_pt": 40, "score": 1, "max_score": 2,
         "full_credit": False, "strip": strip, "text": "Piège : la retenue.\n$3+4=7$",
         "marks": {"kind": "single_tr", "ok": False}},
        {"x_pt": 40, "y_pt": 300, "w_pt": 120, "h_pt": 40, "score": 2, "max_score": 2,
         "full_credit": True, "strip": {**strip, "y_pt": 260}, "text": "",
         "marks": {"kind": "single_br", "ok": True}},
        {"x_pt": 40, "y_pt": 200, "w_pt": 120, "h_pt": 40, "score": 1, "max_score": 2,
         "full_credit": False, "strip": {**strip, "y_pt": 160},
         "text": "Compare cellule par cellule.",
         "marks": {"kind": "cells", "cells": [
             {"x_pt": 50, "y_pt": 210, "w_pt": 20, "h_pt": 8, "ok": True},
             {"x_pt": 80, "y_pt": 210, "w_pt": 20, "h_pt": 8, "ok": False}]}},
        {"x_pt": 300, "y_pt": 400, "w_pt": 120, "h_pt": 40, "score": 0, "max_score": 1,
         "full_credit": False, "strip": {**strip, "x_pt": 300, "y_pt": 360},
         "text": "Relis la bonne réponse.",
         "marks": {"kind": "qcm", "any_error": True, "boxes": [
             {"x_pt": 300, "y_pt": 405, "w_pt": 5.7, "h_pt": 5.7, "should_check": True},
             {"x_pt": 300, "y_pt": 395, "w_pt": 5.7, "h_pt": 5.7, "should_check": False}]}},
        {"x_pt": 300, "y_pt": 200, "w_pt": 120, "h_pt": 60, "score": 1, "max_score": 3,
         "full_credit": False, "strip": {**strip, "x_pt": 300, "y_pt": 160},
         "text": "Refais les liaisons.",
         "marks": {"kind": "matching", "links": [
             {"x1": 305, "y1": 250, "x2": 405, "y2": 240},
             {"x1": 305, "y1": 230, "x2": 405, "y2": 210}]}},
    ]
    out = Path(tempfile.mkdtemp()) / "overlay.pdf"
    pdfgen.render_overlay(str(out), copies_annotations=[_page(zones)], color="#D32F2F")
    assert out.exists() and out.stat().st_size > 800


def test_zone_marks_qcm_shows_correct_boxes_only_on_error():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import (Assessment, Copy, CopyItem, DocumentPage,
                            GradingDecision, ResponseZone, SchoolClass, Student,
                            StudentResponse)
    from app.services import pipeline

    db = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(db.bind)
    cls = SchoolClass(name="5A", grade_level="5e"); db.add(cls); db.flush()
    stu = Student(class_id=cls.id, first_name="A", last_name="B", llm_pseudonym="p")
    db.add(stu); db.flush()
    a = Assessment(class_id=cls.id, title="C", type="control"); db.add(a); db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id); db.add(copy); db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1); db.add(page); db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=1, difficulty=4,
                    response_type="qcm_single", statement="Coche.",
                    correction="Piège.", expected_json={"type": "choice", "correct": [1]},
                    grading_json={"comparator": "qcm", "max_score": 1, "bareme_points": 1})
    db.add(item); db.flush()
    meta = {"boxes": [
        {"index": 0, "correction_box": {"x_pt": 40, "y_pt": 400, "w_pt": 5, "h_pt": 5}},
        {"index": 1, "correction_box": {"x_pt": 40, "y_pt": 390, "w_pt": 5, "h_pt": 5}}]}
    zone = ResponseZone(page_id=page.id, item_id=item.id, type="qcm_single",
                        x_pt=40, y_pt=380, w_pt=100, h_pt=30, meta_json=meta)
    db.add(zone); db.flush()
    resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id, selected_choices=[0])
    db.add(resp); db.flush()

    wrong = GradingDecision(response_id=resp.id, source="deterministic", score=0,
                            max_score=1, status="auto", tier="A")
    marks = pipeline._zone_marks(db, item, zone, wrong)
    assert marks["kind"] == "qcm" and marks["any_error"] is True
    checked = {b["should_check"] for b in marks["boxes"]}
    # seule la case du bon choix (index 1) est cochée
    assert [b["should_check"] for b in marks["boxes"]] == [False, True]

    right = GradingDecision(response_id=resp.id, source="deterministic", score=1,
                            max_score=1, status="auto", tier="A")
    assert pipeline._zone_marks(db, item, zone, right)["any_error"] is False


def test_render_copy_review_smoke_no_background():
    strip = {"x_pt": 40, "y_pt": 100, "w_pt": 250, "h_pt": 11, "fs": 8}
    zones = [{"x_pt": 40, "y_pt": 400, "w_pt": 120, "h_pt": 40, "score": 0,
              "max_score": 1, "full_credit": False, "strip": strip,
              "text": "Vérifie l'unité.", "marks": {"kind": "single_tr", "ok": False}}]
    out = Path(tempfile.mkdtemp()) / "review.pdf"
    pdfgen.render_copy_review(str(out), review_pages=[{
        "student": "A B", "assessment_type": "training", "page_zones": zones,
        "background": None}], color="#2E7D32")
    assert out.exists() and out.stat().st_size > 500

"""Overlay de correction enrichi (§ améliorations overlay) :

- la bande corrigé sous une carte est dimensionnée sur le TEXTE du corrigé
  (anticipée), jamais coupée, et son corps est plus petit que l'énoncé ;
- les cases QCM du sujet sont décalées à droite pour réserver, à leur gauche,
  la case de correction attendue que l'overlay imprime systématiquement ;
- les marques par champ (coche/croix, cases correction, traits de liaison) se
  dessinent sans erreur pour chaque type de réponse.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import grading, pdfgen, worker_cv


# ------------------------- placement inchangé malgré la bande dimensionnée

def test_pages_needed_matches_render_copy_with_correction():
    """La bande corrigé plus haute change la hauteur d'une carte : la
    simulation (pages_needed) doit rester d'accord avec le dessin réel."""
    items = [{"kind": "exercise", "item_id": f"e{i}",
              "statement": f"Calcule $ {i} \\times {i + 2} $.",
              "correction": ("Piège : la retenue.\nRepose l'opération en "
                             "colonnes, aligne bien les chiffres avant "
                             "d'additionner, sinon le résultat glisse d'un rang."),
              "response_type": "short_text", "choices": [], "level3": 3,
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
    heights = [pdfgen.estimate_item_height(i, 9, 12, tpl["exercise"])
               for i in items]
    assert pdfgen.pages_needed(heights) == real


# --------------------------------------------------------- bande corrigé

def test_strip_holds_only_the_score_whatever_the_old_guide_text():
    # les guides sont des encadrés « {{aide}} » de l'énoncé : la bande sous la
    # carte ne porte plus que la note, quelle que soit la longueur de l'ancien
    # guide (`correction`) — il n'est plus jamais composé ni imprimé
    short = pdfgen._correction_strip_layout("Résultat court.", pdfgen.COL_W, 9)
    long = pdfgen._correction_strip_layout(
        "Attention à la retenue.\nAligne les virgules avant d'additionner "
        "$3{,}5 + 1{,}8$.\nUne seule règle à la fois, ne mélange pas les unités.",
        pdfgen.COL_W, 9)
    assert short["height"] == long["height"] == pytest.approx(pdfgen.STRIP_MIN_H)
    assert short["fs"] < 9                                 # la note reste plus petite


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
              "choices": ["un", "deux", "trois"], "level3": 2, "figure": None,
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


def test_manual_review_crop_overlays_expected_qcm_without_mutating_scan():
    source = np.full((100, 200, 3), 255, dtype=np.uint8)
    meta = {"boxes": [
        {"index": 0, "correction_box": {"x_pt": 20, "y_pt": 30,
                                          "w_pt": 5, "h_pt": 5}},
        {"index": 1, "correction_box": {"x_pt": 20, "y_pt": 40,
                                          "w_pt": 5, "h_pt": 5}},
    ]}
    out = worker_cv.draw_expected_answer(
        source, zone_geo={"x_pt": 10, "y_pt": 20, "w_pt": 100,
                          "h_pt": 50, "padding_pt": 0},
        meta=meta, expected={"correct": [0]}, response_type="qcm_single")
    red = np.array([40, 40, 198], dtype=np.uint8)  # BGR de #C62828
    assert np.array_equal(out[75, 25], red)         # attendu : carré plein
    assert np.array_equal(out[55, 25], [255, 255, 255])  # non attendu : centre vide
    assert np.array_equal(out[50, 20], red)         # ...mais contour rouge
    assert np.all(source == 255)                    # scan/dérivé jamais modifié


def test_manual_review_crop_overlays_expected_matching_links():
    source = np.full((100, 200, 3), 255, dtype=np.uint8)
    point = lambda index, x: {"index": index, "x_pt": x, "y_pt": 32,
                              "w_pt": 4, "h_pt": 4}
    out = worker_cv.draw_expected_answer(
        source, zone_geo={"x_pt": 10, "y_pt": 20, "w_pt": 100,
                          "h_pt": 50, "padding_pt": 0},
        meta={"left_points": [point(0, 20)], "right_points": [point(0, 80)]},
        expected={"pairs": [[0, 0]]}, response_type="matching")
    # Le milieu de la liaison attendue est rouge sur le scan blanc.
    assert out[72, 84, 2] > 150 and out[72, 84, 0] < 100


# ------------------------------------------------- dessin des marques (smoke)

def _page(zones: list[dict]) -> dict:
    return {"student": "A B", "assessment_type": "control", "note": "12/20",
            "page_zones": zones}


def _marks(**kw):
    m = {"edge_x": 160, "edge": [], "circles": [], "strikes": [], "fills": [], "links": []}
    m.update(kw)
    return m


def test_render_overlay_draws_every_mark_kind_without_error():
    strip = {"x_pt": 40, "y_pt": 100, "w_pt": 250, "h_pt": 11, "fs": 8}
    zones = [
        {"x_pt": 40, "y_pt": 400, "w_pt": 120, "h_pt": 40, "score": 0, "max_score": 1,
         "full_credit": False, "strip": strip, "text": "",
         "marks": _marks(edge=[{"y": 430, "ok": False}, {"y": 415, "ok": True}],
                         circles=[{"x_pt": 60, "y_pt": 410, "w_pt": 40, "h_pt": 8}],
                         strikes=[{"x_pt": 60, "y_pt": 425, "w_pt": 40, "h_pt": 8}],
                         fills=[{"x_pt": 100, "y_pt": 405, "w_pt": 6, "h_pt": 6}],
                         links=[{"x1": 50, "y1": 420, "x2": 140, "y2": 410}])},
        {"x_pt": 40, "y_pt": 300, "w_pt": 120, "h_pt": 40, "score": 2, "max_score": 2,
         "full_credit": True, "strip": {**strip, "y_pt": 260}, "text": "", "marks": None},
    ]
    out = Path(tempfile.mkdtemp()) / "overlay.pdf"
    pdfgen.render_overlay(str(out), copies_annotations=[_page(zones)], color="#D32F2F")
    assert out.exists() and out.stat().st_size > 800


def _overlay_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import Assessment, Copy, DocumentPage, SchoolClass, Student

    db = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(db.bind)
    cls = SchoolClass(name="5A", grade_level="5e"); db.add(cls); db.flush()
    stu = Student(class_id=cls.id, name="A B", llm_pseudonym="p"); db.add(stu); db.flush()
    a = Assessment(class_id=cls.id, title="C", type="control"); db.add(a); db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id); db.add(copy); db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1); db.add(page); db.flush()
    return db, copy, page


def _answer(db, copy, page, rtype, expected, grading, meta, **resp_kw):
    from app.models import CopyItem, ResponseZone, StudentResponse

    item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=1, difficulty=3,
                    response_type=rtype, statement="Q.", correction="",
                    expected_json=expected, grading_json=grading)
    db.add(item); db.flush()
    strip = {"x_pt": 40 + 7.37, "y_pt": 360, "w_pt": 200 - 2 * 7.37, "h_pt": 10}
    zone = ResponseZone(page_id=page.id, item_id=item.id, type=rtype, x_pt=40, y_pt=380,
                        w_pt=200, h_pt=60, meta_json={**meta, "correction_strip": strip})
    db.add(zone); db.flush()
    resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id, **resp_kw)
    db.add(resp); db.flush()
    return item, zone, resp


def _decision(resp, score, max_score):
    from app.models import GradingDecision
    return GradingDecision(response_id=resp.id, source="deterministic", score=score,
                           max_score=max_score, status="auto", tier="A")


def _qcm_boxes(n, y0=420):
    return [{"index": i, "x_pt": 60 + 50 * i, "y_pt": y0, "w_pt": 5.7, "h_pt": 5.7,
             "label": {"x_pt": 70 + 50 * i, "y_pt": y0 - 1, "w_pt": 30, "h_pt": 8}}
            for i in range(n)]


def test_marks_sit_on_the_card_right_border_aligned_with_the_answer_line():
    from app.services import pipeline

    db, copy, page = _overlay_db()
    item, zone, resp = _answer(db, copy, page, "qcm_single",
                               {"type": "choice", "correct": [1]},
                               {"comparator": "qcm", "max_score": 1}, {"boxes": _qcm_boxes(3)},
                               selected_choices=[0])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 1, 1))
    assert abs(m["edge_x"] - 240) < 0.5                 # bord droit du cadre (x + w)
    assert m["edge"] == [{"y": 420 + 5.7 / 2, "ok": True}]
    assert not (m["circles"] or m["strikes"] or m["fills"] or m["links"])  # tout juste : rien d'autre


def test_qcm_single_wrong_circles_only_the_right_answer():
    from app.services import pipeline

    db, copy, page = _overlay_db()
    item, zone, resp = _answer(db, copy, page, "qcm_single",
                               {"type": "choice", "correct": [1]},
                               {"comparator": "qcm", "max_score": 1}, {"boxes": _qcm_boxes(3)},
                               selected_choices=[0])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 0, 1))
    assert [e["ok"] for e in m["edge"]] == [False]
    # case + libellé de la bonne réponse (index 1) entourés, rien de barré
    assert m["circles"] == [{"x_pt": 110, "y_pt": 419, "w_pt": 30 + 10, "h_pt": 8}]
    assert [b["index"] for b in m["fills"]] == [1]       # et sa case remplie
    assert m["strikes"] == []


def test_qcm_multiple_wrong_strikes_wrong_choices_and_circles_all_right_ones():
    from app.services import pipeline

    db, copy, page = _overlay_db()
    item, zone, resp = _answer(db, copy, page, "qcm_multiple",
                               {"type": "choice", "correct": [0, 2]},
                               {"comparator": "qcm", "max_score": 1, "exclusive": False},
                               {"boxes": _qcm_boxes(4)}, selected_choices=[0, 1])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 0.5, 1))
    assert [e["ok"] for e in m["edge"]] == [False]       # partiel = pas juste
    assert [round(r["x_pt"]) for r in m["circles"]] == [60, 160]
    assert [b["index"] for b in m["fills"]] == [0, 2]
    assert [round(r["x_pt"]) for r in m["strikes"]] == [110]


def test_grid_marks_each_row_and_fills_the_right_box_on_wrong_rows():
    from app.services import pipeline

    db, copy, page = _overlay_db()
    rows = [{"label": "a", "correct": 1}, {"label": "b", "correct": 0}]
    boxes = [{"row": r, "col": c, "index": 2 * r + c, "x_pt": 150 + 20 * c,
              "y_pt": 420 - 15 * r, "w_pt": 9, "h_pt": 9} for r in range(2) for c in range(2)]
    item, zone, resp = _answer(db, copy, page, "checkbox_grid",
                               {"type": "grid", "rows": rows},
                               {"comparator": "grid", "max_score": 2}, {"boxes": boxes},
                               selected_choices=[1, 1])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 1, 2))
    assert [(round(e["y"], 1), e["ok"]) for e in m["edge"]] == [(424.5, True), (409.5, False)]
    assert [(b["row"], b["col"]) for b in m["fills"]] == [(1, 0)]


def test_matching_marks_follow_the_right_column_and_dash_only_wrong_links():
    from app.services import pipeline

    db, copy, page = _overlay_db()
    pt = lambda i, x: {"index": i, "x_pt": x, "y_pt": 430 - 15 * i, "w_pt": 6, "h_pt": 6}
    meta = {"left_points": [pt(i, 60) for i in range(3)],
            "right_points": [pt(i, 180) for i in range(3)]}
    item, zone, resp = _answer(db, copy, page, "matching",
                               {"type": "matching", "pairs": [[0, 1], [1, 0], [2, 2]]},
                               {"comparator": "matching", "max_score": 3}, meta,
                               selected_pairs=[[0, 1], [1, 2], [2, 0]])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 1, 3))
    # de haut en bas sur la colonne de droite : R0 (attendu L1) faux, R1 (L0) juste, R2 (L2) faux
    assert [(round(e["y"]), e["ok"]) for e in m["edge"]] == [(433, False), (418, True), (403, False)]
    assert len(m["links"]) == 2                           # seulement les liaisons fausses


def test_horizontal_matching_marks_sit_beside_each_bottom_point():
    """Disposition horizontale : toutes les réponses sont sur une même
    rangée, une marque au bord de carte les empilerait — chacune se pose à
    droite de son point."""
    from app.services import pipeline

    db, copy, page = _overlay_db()
    pt = lambda i, y: {"index": i, "x_pt": 60 + 40 * i, "y_pt": y, "w_pt": 6, "h_pt": 6}
    meta = {"orientation": "horizontal", "left_points": [pt(i, 440) for i in range(3)],
            "right_points": [pt(i, 395) for i in range(3)]}
    item, zone, resp = _answer(db, copy, page, "matching",
                               {"type": "matching", "pairs": [[0, 1], [1, 0], [2, 2]]},
                               {"comparator": "matching", "max_score": 3}, meta,
                               selected_pairs=[[0, 1], [1, 2], [2, 0]])
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 1, 3))
    marks = sorted((round(e["x"]), round(e["y"]), e["ok"]) for e in m["edge"])
    assert [(ok, y) for _x, y, ok in marks] == [(False, 398), (True, 398), (False, 398)]
    assert all(x > 60 + 40 * i + 6 for i, (x, _y, _ok) in enumerate(marks))
    assert len(m["links"]) == 2


def _printed_matching(render):
    """Dessine la carte comme à l'impression (depuis l'instantané du barème)
    et relit dans le PDF le TEXTE imprimé à côté de chaque point : la
    vérification ne fait confiance à aucun indice."""
    import io

    import fitz
    from reportlab.pdfgen import canvas

    from app.services import pdfgen

    g = render["grading"]
    geo = pdfgen._matching_layout(g["left"], g["right"], 9, pdfgen.COL_W)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=pdfgen.A4)
    meta = pdfgen._draw_matching_zone(c, 40, 380, pdfgen.COL_W, geo["height"],
                                      g["left"], g["right"], 9)
    c.save()
    with fitz.open(stream=buf.getvalue(), filetype="pdf") as doc:
        words = doc[0].get_text("words")          # repère haut-gauche, y vers le bas
    horizontal = meta.get("orientation") == "horizontal"

    def label(p, first_row):
        cx, cy = p["x_pt"] + p["w_pt"] / 2, pdfgen.PAGE_H - (p["y_pt"] + p["h_pt"] / 2)
        if horizontal:      # étiquette au-dessus (rangée du haut) ou en dessous
            near = [w for w in words if abs((w[0] + w[2]) / 2 - cx) < 4
                    and (w[3] < cy if first_row else w[1] > cy)]
            return min(near, key=lambda w: abs((w[1] + w[3]) / 2 - cy))[4]
        near = [w for w in words if abs((w[1] + w[3]) / 2 - cy) < 4
                and (w[2] < cx if first_row else w[0] > cx)]
        return min(near, key=lambda w: abs((w[0] + w[2]) / 2 - cx))[4]
    return (meta, [label(p, True) for p in meta["left_points"]],
            [label(p, False) for p in meta["right_points"]])


def _check_matching_wiring(left, right, bank_pairs, orientation):
    """Ordre de la colonne de droite changé à la génération : ce qui est
    imprimé, la note et les marques de l'overlay restent câblés sur le CONTENU
    (l'élément « alpha » va avec la réponse « trois », où qu'elle soit imprimée)."""
    from types import SimpleNamespace

    from app.services import generation, pipeline
    from app.services import grading as grader

    row = SimpleNamespace(
        id="bank-1", response_type="matching", statement="Relier.", correction="",
        difficulty_level=1,
        figure_json=None, source="generated", raw_extract_json={},
        grading_json={"comparator": "matching", "max_score": len(bank_pairs),
                      "left": left, "right": right, "pairs": bank_pairs},
        expected_json={"type": "matching", "left": left, "right": right, "pairs": bank_pairs})
    from app.models import CopyItem

    db, copy, page = _overlay_db()
    # création réelle de la copie : la carte à imprimer ET l'instantané en base
    render = generation.build_render_item(db, row=row, copy_id=copy.id, catalog_id="cat", seq=1)
    stored = db.get(CopyItem, render["item_id"])
    assert stored.grading_json == render["grading"] and stored.expected_json == render["expected"]
    assert render["grading"]["right"] != right            # l'ordre a bien changé
    meta, printed_left, printed_right = _printed_matching(render)
    assert meta.get("orientation", "vertical") == orientation
    # 1. la carte imprimée EST l'instantané stocké (barème et réponse attendue)
    assert printed_left == left
    assert printed_right == render["grading"]["right"] == render["expected"]["right"]
    key = {left[i]: right[j] for i, j in bank_pairs}     # clé de la banque, par contenu
    # 2. l'élève qui relie juste, sur le papier, a tout juste
    good = [[i, printed_right.index(key[printed_left[i]])] for i in range(len(left))]
    verdict = grader.grade(stored.expected_json, stored.grading_json, "", 0.99,
                           selected_pairs=good)
    assert verdict["score"] == verdict["max_score"] == len(bank_pairs)
    # 3. deux réponses échangées : chaque ✓/✗ face au point de SA réponse
    wrong = [list(p) for p in good]
    wrong[0][1], wrong[1][1] = good[1][1], good[0][1]
    item, zone, resp = _answer(db, copy, page, "matching", stored.expected_json,
                               stored.grading_json, meta, selected_pairs=wrong)
    m = pipeline._zone_marks(db, item, zone, _decision(resp, len(left) - 2, len(left)))
    points = meta["right_points"]
    chosen = {printed_left[i]: printed_right[k] for i, k in wrong}
    assert len(m["edge"]) == len(bank_pairs)
    for e in m["edge"]:
        if orientation == "horizontal":   # à droite de son point, même rangée
            k = min(range(len(points)), key=lambda q: abs(e["x"] - (points[q]["x_pt"] + 6)))
            assert e["x"] > points[k]["x_pt"] + points[k]["w_pt"]
        else:                              # bord de carte, à la hauteur de son point
            k = min(range(len(points)), key=lambda q: abs(e["y"] - pipeline._center_y(points[q])))
        assert abs(e["y"] - pipeline._center_y(points[k])) < 0.01
        answer = printed_right[k]
        item_label = next(lab for lab, ans in key.items() if ans == answer)
        assert e["ok"] == (chosen[item_label] == answer)
    assert sorted(e["ok"] for e in m["edge"]).count(False) == 2
    # pointillés : de l'élément faux vers le point où est imprimée SA réponse
    for ln in m["links"]:
        i = min(range(len(left)), key=lambda q: abs(
            ln["x1"] - meta["left_points"][q]["x_pt"] - 3.1) + abs(
            ln["y1"] - pipeline._center_y(meta["left_points"][q])))
        k = min(range(len(points)), key=lambda q: abs(
            ln["x2"] - points[q]["x_pt"] - 3.1) + abs(ln["y2"] - pipeline._center_y(points[q])))
        assert printed_right[k] == key[printed_left[i]]
    assert len(m["links"]) == 2


def test_reordered_matching_keeps_print_grade_and_overlay_wired_by_content():
    # 3 droites concourantes dans la banque -> réordonné à la génération
    _check_matching_wiring(["alpha", "beta", "gamma"], ["un", "deux", "trois"],
                           [[0, 2], [1, 1], [2, 0]], "vertical")


def test_reordered_horizontal_matching_keeps_overlay_wired_by_content():
    # chaque élément en face de sa réponse (réponse donnée) -> réordonné, horizontal
    left = ["aa", "bb", "cc", "dd", "ee", "ff"]
    right = ["u1", "u2", "u3", "u4", "u5", "u6"]
    _check_matching_wiring(left, right, [[i, i] for i in range(6)], "horizontal")


def test_table_fill_one_mark_per_row_without_per_cell_marks():
    from app.services import pipeline
    from app.models import OcrAttempt

    db, copy, page = _overlay_db()
    cell = lambda r, c: {"x_pt": 60 + 40 * c, "y_pt": 420 - 14 * r, "w_pt": 30, "h_pt": 10}
    meta = {"cells": [[cell(0, 0), cell(0, 1)], [cell(1, 0), cell(1, 1)]]}
    grading = {"comparator": "table", "max_score": 4,
               "cells": [[{"value": "1"}, {"value": "2"}], [{"value": "3"}, {"value": "4"}]]}
    item, zone, resp = _answer(db, copy, page, "table_fill",
                               {"type": "table", "cells": [[{}, {}], [{}, {}]]}, grading, meta)
    db.add(OcrAttempt(zone_id=zone.id, provider="teacher_ocr", confidence=1.0,
                      raw_json={"cells": ["1", "2", "3", "9"],
                                "cell_credits": [1.0, 1.0, 1.0, 0.0]}))
    db.flush()
    m = pipeline._zone_marks(db, item, zone, _decision(resp, 3, 4))
    assert [e["ok"] for e in m["edge"]] == [True, False]
    assert not (m["circles"] or m["fills"])


class _FakeCanvas:
    """Canvas espion : enregistre rect / roundRect / line avec l'épaisseur de
    trait courante — assez pour vérifier CE QUI est dessiné, sans rendre de PDF."""
    def __init__(self):
        self.rects, self.round_rects, self.lines = [], [], []
        self.lw = 1.0
    def saveState(self): pass
    def restoreState(self): pass
    def setStrokeColor(self, *a, **k): pass
    def setFillColor(self, *a, **k): pass
    def setLineCap(self, *a, **k): pass
    def setDash(self, *a, **k): pass
    def setLineWidth(self, w): self.lw = w
    def rect(self, x, y, w, h, stroke=1, fill=0):
        self.rects.append((x, y, w, h, fill))
    def roundRect(self, x, y, w, h, r, stroke=1, fill=0):
        self.round_rects.append((x, y, w, h))
    def line(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2, self.lw))


def test_check_is_thin_and_cross_is_thick_centered_on_the_border():
    fc = _FakeCanvas()
    pdfgen._draw_zone_marks(fc, {"marks": _marks(edge_x=200, edge=[
        {"y": 400, "ok": True}, {"y": 380, "ok": False}])}, pdfgen.black)
    check = [ln for ln in fc.lines if ln[1] > 395]
    cross = [ln for ln in fc.lines if ln[1] < 390]
    assert check and all(ln[4] == pdfgen.CHECK_LW for ln in check)
    assert cross and all(ln[4] == pdfgen.CROSS_LW for ln in cross)
    assert pdfgen.CROSS_LW > 2 * pdfgen.CHECK_LW
    xs = [x for ln in fc.lines for x in (ln[0], ln[2])]
    assert min(xs) < 200 < max(xs)                    # à cheval sur le cadre


def test_circle_strike_fill_and_dashed_link_are_drawn():
    fc = _FakeCanvas()
    pdfgen._draw_zone_marks(fc, {"marks": _marks(
        circles=[{"x_pt": 60, "y_pt": 410, "w_pt": 40, "h_pt": 8}],
        strikes=[{"x_pt": 60, "y_pt": 425, "w_pt": 40, "h_pt": 8}],
        fills=[{"x_pt": 100, "y_pt": 405, "w_pt": 6, "h_pt": 6}],
        links=[{"x1": 50, "y1": 420, "x2": 140, "y2": 410}])}, pdfgen.black)
    assert len(fc.round_rects) == 1                    # bonne réponse entourée
    assert fc.rects == [(100, 405, 6, 6, 1)]           # bonne case remplie
    assert len(fc.lines) == 2                          # rature + liaison pointillée


def test_render_copy_review_smoke_no_background():
    strip = {"x_pt": 40, "y_pt": 100, "w_pt": 250, "h_pt": 11, "fs": 8}
    zones = [{"x_pt": 40, "y_pt": 400, "w_pt": 120, "h_pt": 40, "score": 0,
              "max_score": 1, "full_credit": False, "strip": strip, "text": "",
              "marks": _marks(edge=[{"y": 420, "ok": False}])}]
    out = Path(tempfile.mkdtemp()) / "review.pdf"
    pdfgen.render_copy_review(str(out), review_pages=[{
        "student": "A B", "assessment_type": "training", "page_zones": zones,
        "background": None}], color="#2E7D32")
    assert out.exists() and out.stat().st_size > 500


def test_qcm_labels_of_old_copies_are_recovered_from_the_layout():
    """Copies imprimées avant l'enregistrement des libellés : la géométrie est
    retrouvée en rejouant la mise en page (case + libellé entourés)."""
    from reportlab.pdfgen import canvas as rl_canvas

    choices = ["2, 4, 3, 1", "4, 2, 3, 1", "1, 3, 4, 2"]
    c = rl_canvas.Canvas(str(Path(tempfile.mkdtemp()) / "q.pdf"))
    meta = pdfgen._draw_answer_zone(c, 40, 300, 250, 20, "qcm_single", choices, 10)
    stored = [b["label"] for b in meta["boxes"]]
    old = [{k: v for k, v in b.items() if k != "label"} for b in meta["boxes"]]
    rects = pdfgen.qcm_answer_rects(choices, {"x_pt": 40, "y_pt": 300, "w_pt": 250,
                                              "h_pt": 20}, old)
    for b, lab in zip(old, stored):
        r = rects[b["index"]]
        assert abs((r["x_pt"] + r["w_pt"]) - (lab["x_pt"] + lab["w_pt"])) < 0.5

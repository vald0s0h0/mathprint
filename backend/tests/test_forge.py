"""Pipeline forge (agents/forge) : contrat, grilles, barème, contrôles, rendu.

Les cartes étalons servent de modèles au rédacteur : si l'une d'elles ne passe pas
les contrôles, chaque run enseignerait un format refusé. Aucun appel à un modèle ici.
"""
import copy
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import forge  # noqa: E402
import goldfish  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Competency, CompetencyFramework, IndigoExercise  # noqa: E402
from app.services import card_preview, figures, forge_contract as fc, generation, pdfgen, scoring, typography  # noqa: E402

ETALONS = json.loads((REPO / "agents" / "forge" / "matieres" / "math" / "etalons.json")
                     .read_text(encoding="utf-8"))["cartes"]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for program, label in (("fr", "Fr"), ("ci", "Ci")):
        framework = CompetencyFramework(grade_level="3e", name=label, program_id=program,
                                        subject_id="math", status="published")
        session.add(framework)
        session.flush()
        for i, code in enumerate(("Z1.1", "Z1.2")):
            session.add(Competency(framework_id=framework.id, code=code, short_id=code,
                                   label=f"{label} {code}", domain_code="Z", domain_name="Test",
                                   chapter_code="Z1", chapter_name="Test", order_index=i))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _slot(card: dict, key: str = "s01") -> dict:
    exercise = card["exercice"]
    return {"slot": key, "kind": "exercise", "competency": "Z1.1", "level": card["niveau"],
            "difficulty": forge.LEVEL_NAMES[card["niveau"]],
            "response_type": exercise["response_type"], "size": "S",
            "figure": bool(exercise.get("figure"))}


def _comp(db):
    framework = forge.framework_for(db, "fr", "3e")
    return db.query(Competency).filter_by(framework_id=framework.id, short_id="Z1.1").one()


def _run(db, cards, *, grid=None) -> Path:
    runs = forge.prepare(db, "fr", "3e", "Z1.1", model="test-model")
    run = runs[0]
    slots = []
    for i, card in enumerate(cards):
        key = f"s{i + 1:02d}"
        slot = _slot(card, key)
        slots.append(slot)
        forge.dump_json(run / "slots" / f"{key}.json", {**card["exercice"], "slot": key})
    forge.dump_json(run / "grid.json", grid or slots)
    return run


# ------------------------------------------------------------------ étalons
@pytest.mark.parametrize("card", ETALONS, ids=[c["exercice"]["family_id"] for c in ETALONS])
def test_each_standard_card_passes_every_check(db, card):
    exercise = {**card["exercice"], "slot": "s01"}
    slot = _slot(card)
    assert forge._slot_problems(exercise, slot, {}) == []
    contract, problems = forge.build_contract(db, exercise, slot, _comp(db), "math", set())
    assert problems == []
    assert contract["grading"]["route"] == fc.default_route(exercise["response_type"])
    assert contract["grading"]["bareme_points"] > 0


def test_standard_cards_cover_every_format():
    formats = {c["exercice"]["response_type"] for c in ETALONS}
    assert {"qcm_single", "qcm_multiple", "checkbox_grid", "matching", "short_text",
            "table_fill", "multiline_text"} <= formats


def test_standard_cards_render_as_real_cards(db):
    run = _run(db, ETALONS)
    summary = forge.render(db, run)
    assert set(summary["cards"]) == {f"s{i + 1:02d}" for i in range(len(ETALONS))}
    assert all((run / "cards" / f"{k}.png").stat().st_size > 1000 for k in summary["cards"])
    assert not summary["too_tall"]


# ------------------------------------------------------------------- grilles
def test_exercise_grid_applies_the_quotas():
    grid = forge.exercise_grid("A1.2", 4)
    assert len(grid) == 20
    assert [sum(s["level"] == n for s in grid) for n in (1, 2, 3)] == [8, 8, 4]
    written = [s for s in grid if s["response_type"] in forge.WRITTEN_TYPES]
    assert len(written) == 6
    assert not [s for s in written if s["level"] == 1]
    assert sum(s["figure"] for s in grid) == 4
    assert forge.size_targets(grid) == {"XS": 5, "S": 6, "M": 5, "L": 3, "XL": 1}
    assert forge.exercise_grid("A1.2", 4) == grid


def test_figure_count_is_bounded():
    assert sum(s["figure"] for s in forge.exercise_grid("A", 0)) == forge.FIGURE_MIN
    assert sum(s["figure"] for s in forge.exercise_grid("A", 20)) == forge.FIGURE_MAX


def test_problem_grid_covers_the_chapter():
    grid = forge.problem_grid(["A1.1", "A1.2", "A1.3"])
    assert len(grid) == 20
    assert [sum(s["level"] == n for s in grid) for n in (1, 2, 3)] == [8, 8, 4]
    assert all(len(s["competencies"]) >= 2 for s in grid)
    assert {c for s in grid for c in s["competencies"]} == {"A1.1", "A1.2", "A1.3"}


# -------------------------------------------------------------------- barème
def test_bareme_follows_difficulty_not_structure():
    assert scoring.forge_bareme("qcm_multiple", 1) == 1.0
    assert scoring.forge_bareme("qcm_multiple", 3) == 3.0
    assert scoring.forge_bareme("qcm_single", 3) == 2.0
    assert scoring.forge_bareme("multiline_text", 3) == 4.0
    assert scoring.forge_bareme("", 3, problem=True) == 5.0


def test_bareme_adjusts_with_time_within_limits():
    assert scoring.forge_bareme("short_text", 2, 200) == 2.5
    assert scoring.forge_bareme("short_text", 2, 30) == 1.5
    assert scoring.forge_bareme("multiline_text", 3, 900) <= scoring.BAREME_MAX


def test_split_bareme_loses_nothing():
    shares = scoring.split_bareme(3.5, [1.5, 2.0, 2.0])
    assert sum(shares) == 3.5
    assert all(s / scoring.BAREME_STEP == int(s / scoring.BAREME_STEP) for s in shares)


def test_model_cannot_choose_the_bareme(db):
    card = copy.deepcopy(ETALONS[0])
    card["exercice"]["bareme_points"] = 5
    contract, problems = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                              _comp(db), "math", set())
    assert problems == []
    assert contract["grading"]["bareme_points"] == 1.0


# ------------------------------------------------------------------ justesse
def _mutated(mutate, index=0):
    card = copy.deepcopy(ETALONS[index])
    mutate(card["exercice"])
    return card


@pytest.mark.parametrize("mutate, needle", [
    (lambda e: e["choices"][0].update(text="$18$"), "ne vaut pas"),
    (lambda e: e["choices"][1].update(calc="5+2*6"), "son calcul déclaré"),
    (lambda e: e["choices"][1].update(text="$17$"), "vaut aussi la bonne réponse"),
    (lambda e: e["choices"][1].pop("error"), "code d'erreur"),
    (lambda e: e["choices"][1].update(error="math.invente"), "code d'erreur inconnu"),
    (lambda e: e.pop("blank_feedback"), "feedback.blank"),
    (lambda e: e.pop("answer_expr"), "answer_expr obligatoire"),
    (lambda e: e["choices"][1].update(feedback="La case 2 est fausse."), "position"),
    (lambda e: e["choices"][1].update(feedback="Tu t'es trompé. " * 20), "trop long"),
])
def test_defects_are_named(db, mutate, needle):
    card = _mutated(mutate)
    _contract, problems = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                               _comp(db), "math", set())
    assert any(needle in p for p in problems), problems


def test_form_must_match_the_statement():
    assert fc.form_problem("Donne la fraction irréductible.", "any")
    assert fc.form_problem("Calcule.", "irreducible")
    assert fc.form_problem("Donne la fraction irréductible.", "irreducible") is None
    assert fc.form_problem("Factorise $2x+6$.", "factored") is None
    assert fc.form_problem("Calcule.", None) is None
    # arrondi ou encadrement : sans la forme exigée, un arrondi faux aurait des points
    for asked in ("Arrondis $1{,}786$ au centième.", "Encadre $3{,}4$ à l'unité.",
                  "Complète l'encadrement de $m$.", "Donne la valeur décimale arrondie au dixième."):
        assert fc.form_problem(asked, "any")
        assert fc.form_problem(asked, "decimal")
        assert fc.form_problem(asked, "exact_decimal") is None
    assert fc.form_problem("Calcule.", "exact_decimal")
    assert fc.form_problem("Relis l'encadré, puis calcule.", "any") is None
    assert fc.form_problem("Elle habite le 12e arrondissement. Calcule.", "any") is None


def test_routes_can_only_rise_to_the_llm():
    assert fc.resolve_route("short_text", "llm") == ("llm", None)
    assert fc.resolve_route("qcm_single", "llm")[1]
    assert fc.resolve_route("multiline_text", "cv")[1]
    assert fc.default_route("matching") == "cv"
    assert fc.default_route("table_fill") == "ocr_llm"
    assert fc.default_route("multiline_text") == "llm"


def test_rewritten_answer_needs_a_brief(db):
    index = next(i for i, c in enumerate(ETALONS) if c["exercice"]["response_type"] == "multiline_text")
    card = _mutated(lambda e: e.pop("llm_brief"), index)
    _contract, problems = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                               _comp(db), "math", set())
    assert any("llm_brief" in p for p in problems)


def test_matching_traps_get_a_uniform_instruction(db):
    index = next(i for i, c in enumerate(ETALONS) if c["exercice"]["response_type"] == "matching")
    card = ETALONS[index]
    contract, problems = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                              _comp(db), "math", set())
    assert problems == []
    assert "Attention : 2 propositions ne servent pas." in contract["statement"]
    assert contract["grading"]["right_ids"] == ["d1", "d2", "d3", "p1", "p2"]


def test_stable_right_ids_follow_the_printed_order():
    grading = {"left": ["a", "b", "c"], "right": ["1", "2", "3", "4"], "right_ids": ["i1", "i2", "i3", "i4"],
               "pairs": [[0, 2], [1, 0], [2, 3]]}
    expected = {"right": grading["right"], "pairs": grading["pairs"]}
    permuted, _expected = generation.legible_matching(grading, expected)
    by_text = dict(zip(grading["right"], grading["right_ids"]))
    assert [by_text[r] for r in permuted["right"]] == permuted["right_ids"]


def test_typography_covers_micro_corrections_only_on_text():
    record = typography.apply_to_record({"grading": {"feedback": {
        "lines": 2, "blank": {"text": "As-tu fini ?"},
        "options": {"opt_a": {"text": "Combien de cm : 5 cm ?", "error": "math.x : y"}}}}})
    feedback = record["grading"]["feedback"]
    assert feedback["blank"]["text"].endswith(" ?")
    assert "5 cm" in feedback["options"]["opt_a"]["text"]
    assert feedback["options"]["opt_a"]["error"] == "math.x : y"
    assert typography.apply_to_record(record) == record


def test_measure_sees_the_final_typography(db):
    card = ETALONS[0]
    contract, _ = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                       _comp(db), "math", set())
    ex = forge.preview_exercise(contract, {**card["exercice"], "slot": "s01"}, _slot(card))
    assert typography.apply_to_record({"statement": ex["statement"]})["statement"] == ex["statement"]


# --------------------------------------------------------------------- bande
def test_old_cards_keep_their_geometry():
    item = {"statement": "Calcule $2+2$.", "response_type": "qcm_single",
            "choices": ["1", "4"], "grading": {"choices": ["1", "4"]}, "level3": 1}
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    before = pdfgen.estimate_item_height(item, 9, 9, tpl)
    with_feedback = {**item, "grading": {**item["grading"], "feedback": {"lines": 2}}}
    longer = {**item, "grading": {**item["grading"], "feedback": {"lines": 4}}}
    two = pdfgen.estimate_item_height(with_feedback, 9, 9, tpl)
    four = pdfgen.estimate_item_height(longer, 9, 9, tpl)
    assert pdfgen.reserved_feedback_lines(item) == 0
    assert before < two < four
    # Mesures du plan : environ 10,3 mm et 17,6 mm de bande.
    assert round((two - before) / pdfgen.mm, 1) == pytest.approx(3.8, abs=0.3)
    assert round((four - before) / pdfgen.mm, 1) == pytest.approx(11.1, abs=0.3)


def test_composite_reservation_is_capped():
    item = {"statement": "Contexte", "response_type": "composite", "grading": {"parts": [
        {"statement": "a", "response_type": "short_text", "grading": {"feedback": {"lines": 2}}},
        {"statement": "b", "response_type": "short_text", "grading": {"feedback": {"lines": 2}}},
        {"statement": "c", "response_type": "short_text", "grading": {"feedback": {"lines": 2}}}]}}
    assert pdfgen.reserved_feedback_lines(item) == pdfgen.FEEDBACK_MAX_LINES


def test_message_budget_is_measured_in_both_fonts():
    assert pdfgen.feedback_fits("Tu as additionné avant de multiplier.", 2)
    assert pdfgen.feedback_fits("Tu as additionné avant de multiplier.", 2, dyslexic=True)
    assert not pdfgen.feedback_fits("Tu as additionné avant de multiplier. " * 8, 2)


# ------------------------------------------------------------- figure à côté
@pytest.mark.parametrize("layout", ["above", "below", "left", "right", "grid_left", "grid_right"])
def test_side_figure_measure_equals_drawing(layout):
    import io

    from reportlab.pdfgen import canvas
    figure = {"type": "template", "params": {"name": "cube", "width_mm": 28}}
    item = {"statement": "Quel est le volume de ce cube d'arête $3$ cm ?", "response_type": "qcm_single",
            "choices": ["$9$", "$27$"], "grading": {"choices": ["$9$", "$27$"]}, "level3": 1,
            "figure": figure, "figure_layout": layout, "is_probleme": False}
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    layout_data, zone_fs, zone_h, strip = pdfgen._exercise_layout(item, 9, 9)
    c = canvas.Canvas(io.BytesIO())
    height, _geo, _meta = pdfgen._draw_exercise_card(
        c, 30, 700, pdfgen.COL_W, 1, layout_data, zone_h, strip, 1, "qcm_single", item["choices"],
        tpl, 9, zone_fs, item["grading"], False)
    assert height + pdfgen.GAP == pytest.approx(pdfgen.estimate_item_height(item, 9, 9, tpl))


def test_normal_line_spacing_is_not_wasted_space():
    import io

    import numpy as np
    from PIL import Image
    lines = np.full((400, 600), 255, dtype=np.uint8)
    lines[::20, :] = 0
    out = io.BytesIO()
    Image.fromarray(lines).save(out, format="PNG", dpi=(180, 180))
    assert forge.whitespace(out.getvalue())["empty_ratio"] == 0


def test_smallest_card_has_a_reachable_size_class(db):
    card = ETALONS[0]
    contract, _ = forge.build_contract(db, {**card["exercice"], "slot": "s01"}, _slot(card),
                                       _comp(db), "math", set())
    ex = forge.preview_exercise(contract, {**card["exercice"], "slot": "s01"}, _slot(card))
    assert forge.size_class(forge.card_height_mm(ex)) == "XS"


def test_compaction_shrinks_a_figure_only_if_it_stays_readable():
    item = {"statement": "Le triangle $ABC$ est rectangle en $B$. Quelle égalité est vraie ?",
            "response_type": "qcm_single", "expected": {}, "difficulty": 2,
            "grading": {"choices": ["$1$", "$2$"]}, "choices": ["$1$", "$2$"],
            "figure": {"type": "geo", "params": {"points": {"A": [0, 3], "B": [0, 0], "C": [4, 0]},
                                                 "polygons": [["A", "B", "C"]], "width_mm": 40}}}
    layout, scale, height = forge.best_layout(item)
    assert height < forge.card_height_mm(item)
    assert layout in ("right", "left", "grid_right", "grid_left") and scale <= 1.0
    assert forge._figure_stays_readable(item, layout, scale)


def test_wide_figure_is_not_shrunk_beside_the_text():
    item = {"figure": {"type": "template", "params": {"name": "droite_graduee", "min": 0, "max": 2, "step": 0.1,
                                                      "label_every": 10, "width_mm": 75}}}
    assert not forge._figure_stays_readable(item, "right", 1.0)
    assert forge._figure_stays_readable(item, None, 1.0)


def _grid_item(layout, **extra):
    figure = {"type": "geo", "params": {"points": {"A": [0, 3], "B": [0, 0], "C": [4, 0]},
                                        "polygons": [["A", "B", "C"]], "width_mm": 32}}
    choices = ["$AC^2 = AB^2 + BC^2$", "$AB^2 = AC^2 + BC^2$", "$AC = AB + BC$"]
    return {"statement": "Le triangle $ABC$ est rectangle en $B$. Quelle égalité est vraie ?",
            "response_type": "qcm_single", "choices": choices, "grading": {"choices": choices},
            "level3": 2, "figure": figure, "figure_layout": layout, "figure_scale": 0.85,
            "calc": "autorisee", "is_probleme": False, **extra}


def test_grid_places_answers_beside_the_figure_and_saves_height():
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    grid = pdfgen.estimate_item_height(_grid_item("grid_right"), 9, 9, tpl)
    side = pdfgen.estimate_item_height(_grid_item("right"), 9, 9, tpl)
    assert grid < side


@pytest.mark.parametrize("layout", ["grid_left", "grid_right"])
def test_grid_answer_boxes_never_touch_the_figure(layout):
    import io

    from reportlab.pdfgen import canvas
    item = _grid_item(layout)
    data, zone_fs, zone_h, strip = pdfgen._exercise_layout(item, 9, 9)
    assert data["figure_grid"]
    c = canvas.Canvas(io.BytesIO())
    x = 30
    _h, geo, meta = pdfgen._draw_exercise_card(
        c, x, 700, pdfgen.COL_W, 1, data, zone_h, strip, 2, "qcm_single", item["choices"],
        pdfgen.DEFAULT_TEMPLATES["exercise"], 9, zone_fs, item["grading"], False)
    figure_w = data["figure"][1]
    fig_x0 = x + pdfgen.CARD_PAD if layout == "grid_left" else x + pdfgen.COL_W - pdfgen.CARD_PAD - figure_w
    fig_x1 = fig_x0 + figure_w
    for box in meta["boxes"]:
        assert box["x_pt"] + box["w_pt"] <= fig_x0 or box["x_pt"] >= fig_x1
    assert geo["x_pt"] >= x and geo["x_pt"] + geo["w_pt"] <= x + pdfgen.COL_W + 0.1


def test_grid_falls_back_when_the_column_would_be_too_narrow():
    item = {**_grid_item("grid_right"), "response_type": "matching", "figure_scale": 1.0,
            "grading": {"left": ["a", "b"], "right": ["c", "d"]}, "choices": []}
    item["figure"]["params"]["width_mm"] = 60
    data, *_ = pdfgen._exercise_layout(item, 9, 9)
    assert not data.get("figure_grid")


def test_composite_grid_keeps_every_part_in_the_content_column():
    import io

    from reportlab.pdfgen import canvas
    part = {"statement": "Choisis.", "response_type": "qcm_single",
            "grading": {"choices": ["$1$", "$2$"]}}
    item = {**_grid_item("grid_right"), "response_type": "composite", "statement": "Contexte du problème.",
            "figure_scale": 0.7,
            "grading": {"parts": [part, dict(part)]}, "part_item_ids": ["a", "b"]}
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    layout = pdfgen._composite_layout(item, 9, 9)
    assert layout["stmt"]["figure_grid"]
    c = canvas.Canvas(io.BytesIO())
    height, zones = pdfgen._draw_composite_card(c, 30, 750, pdfgen.COL_W, 1, layout, item, tpl, 9)
    assert height + pdfgen.GAP == pytest.approx(pdfgen.estimate_item_height(item, 9, 9, tpl))
    assert all(z["zone_geo"]["w_pt"] == layout["stmt"]["column_w"] for z in zones)


def test_table_columns_are_equal_when_content_allows():
    cells = [[{"type": "integer", "value": 2, "given": True}, {"type": "integer", "value": 5, "given": True},
              {"type": "integer", "value": 8}],
             [{"type": "integer", "value": 6, "given": True}, {"type": "integer", "value": 15},
              {"type": "integer", "value": 24, "given": True}]]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, None, ["Masse (kg)", "Prix (€)"], cells, 9)
    widths = geometry["col_ws"]
    assert len(set(round(w, 3) for w in widths)) == 1


def test_table_columns_differ_when_a_column_needs_room():
    long_answer = {"type": "expression", "value": "3x^2 + 12x - 45 + 7y"}
    cells = [[{"type": "integer", "value": 1}, {"type": "integer", "value": 2}, {"type": "integer", "value": 3},
              {"type": "integer", "value": 4}, dict(long_answer)]]
    labels = ["A", "B", "C", "D", "Expression développée réduite"]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, labels, None, cells, 9)
    assert len(set(round(w, 3) for w in geometry["col_ws"])) > 1


def test_side_layout_saves_height_compared_to_below():
    figure = {"type": "template", "params": {"name": "cube", "width_mm": 28}}
    item = {"statement": "Quel est le volume de ce cube d'arête $3$ cm ?", "response_type": "qcm_single",
            "choices": ["$9$", "$27$"], "grading": {"choices": ["$9$", "$27$"]}, "level3": 1, "figure": figure}
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    below = pdfgen.estimate_item_height({**item, "figure_layout": None}, 9, 9, tpl)
    right = pdfgen.estimate_item_height({**item, "figure_layout": "right"}, 9, 9, tpl)
    assert right < below


# -------------------------------------------------------------------- figures
@pytest.mark.parametrize("spec", [
    {"name": "cube"}, {"name": "pave", "width": 3, "height": 2, "depth": 1},
    {"name": "cylindre"}, {"name": "cone"}, {"name": "pyramide"}, {"name": "boule"},
    {"name": "patron_cube"}, {"name": "fraction", "total": 8, "filled": 3},
    {"name": "fraction", "total": 6, "filled": 2, "shape": "disc"},
    {"name": "droite_graduee", "min": 0, "max": 5, "step": 1, "points": [{"x": 2, "label": "A"}]},
    {"name": "balance", "left": "$2x$", "right": "$6$"},
])
def test_templates_render(spec):
    figure = {"type": "template", "params": spec}
    assert figures.figure_error(figure) is None
    assert figures.render_figure(figure)[:4] == b"\x89PNG"


@pytest.mark.parametrize("svg, needle", [
    ('<svg viewBox="0 0 1 1"><script>x</script></svg>', "balise interdite"),
    ('<!DOCTYPE x><svg viewBox="0 0 1 1"/>', "DOCTYPE"),
    ('<svg viewBox="0 0 1 1"><rect onclick="x"/></svg>', "attribut interdit"),
    ('<svg viewBox="0 0 1 1"><rect fill="url(#a)"/></svg>', "url("),
    ('<svg><rect/></svg>', "viewBox"),
])
def test_restricted_svg_rejects_dangerous_content(svg, needle):
    assert needle in figures.figure_error({"type": "svg", "params": {"svg": svg}})


def test_restricted_svg_renders():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60"><rect x="5" y="5" width="50" height="30" fill="none" stroke="black"/></svg>'
    assert figures.render_figure({"type": "svg", "params": {"svg": svg, "width_mm": 40}})[:4] == b"\x89PNG"


def test_manual_crop_is_forbidden():
    assert "découpe" in forge._figure_problems({"kind": "crop", "page": "p1"})[0]


# ------------------------------------------------------------------ anti-copie
def test_copy_detection():
    source = "Un fermier possède un grand champ rectangulaire dont la longueur vaut 125 mètres et la largeur 48 mètres."
    corpus = (set(forge._copy_shingles(source)), [{"125", "48", "12"}])
    assert "trop proche" in forge.copy_problem(source, corpus)
    assert forge.copy_problem("Calcule l'aire d'un petit jardin carré de côté égal à sept mètres environ.", corpus) is None
    assert "mêmes valeurs" in forge.copy_problem("Mesure $125$ puis $48$ puis $12$.", corpus)


# ------------------------------------------------------------------- programme
def test_target_never_crosses_programmes(db):
    target = forge.resolve_target(db, "ci", "3e", "Z1.1")
    assert target["framework"].program_id == "ci"
    assert target["competency"].framework_id == target["framework"].id
    with pytest.raises(SystemExit):
        forge.resolve_target(db, "de", "3e", "Z1.1")
    with pytest.raises(SystemExit):
        forge.resolve_target(db, "fr", "3e", "INCONNU")


def test_job_refuses_competencies_of_another_framework(db):
    from types import SimpleNamespace
    fr = forge.resolve_target(db, "fr", "3e", "Z1.1")["competency"]
    ci = forge.resolve_target(db, "ci", "3e", "Z1.1")["competency"]
    assessment = SimpleNamespace(framework_id=fr.framework_id)
    generation.assert_competencies_in_framework(assessment, [fr])
    with pytest.raises(ValueError, match="hors du programme"):
        generation.assert_competencies_in_framework(assessment, [ci])
    generation.assert_competencies_in_framework(SimpleNamespace(framework_id=None), [ci])


# ------------------------------------------------------------------- simulation
def test_simulation_measures_fill_without_any_reference_bank():
    varied = [12, 18, 22, 25, 28, 30, 35, 40, 48, 55, 60, 70, 80, 90, 25, 30, 18, 22, 35, 45]
    tiers = [1] * len(varied)
    result = forge.simulate(varied, tiers, n=40)
    assert 0.7 < result["median_fill"] <= 1.0
    assert forge.simulate([], [], n=5)["runs"] == 0
    assert forge.simulate(varied, tiers, n=40) == result


def test_whitespace_is_read_from_pixels():
    import io

    import numpy as np
    from PIL import Image

    def png(array):
        out = io.BytesIO()
        Image.fromarray(array).save(out, format="PNG", dpi=(180, 180))
        return out.getvalue()
    full = np.zeros((400, 600), dtype=np.uint8)
    assert forge.whitespace(png(full))["empty_ratio"] == 0
    gap = np.zeros((400, 600), dtype=np.uint8)
    gap[100:300, :] = 255
    metrics = forge.whitespace(png(gap))
    assert metrics["empty_run_mm"] > 25 and metrics["empty_ratio"] > 0.2
    assert forge.whitespace_excess(metrics)
    narrow = np.full((400, 600), 255, dtype=np.uint8)
    narrow[:, 40:140] = 0
    assert forge.whitespace(png(narrow))["side_void"] > 0.5
    centered = np.full((400, 600), 255, dtype=np.uint8)
    centered[:, 250:350] = 0
    assert forge.whitespace(png(centered))["side_void"] == 0        # marges égales : voulu


# ---------------------------------------------------------------- persistance
def test_persist_writes_drafts_with_provenance(db):
    grid_cards = [ETALONS[0]]
    run = _run(db, grid_cards)
    forge.render(db, run)
    result = forge.persist(db, run)
    rows = db.query(IndigoExercise).all()
    assert len(rows) == 1 and rows[0].status == "draft"
    raw = rows[0].raw_ocr_json
    assert raw["pipeline"] == "forge" and raw["source_version"] == forge.VERSION
    assert raw["estimated_seconds"] == 40 and raw["model"] == "test-model"
    assert rows[0].grading_json["feedback"]["options"]["b"]["error"]
    assert result["review"] == []  or isinstance(result["review"], list)
    with pytest.raises(SystemExit, match="déjà en brouillon"):
        forge.persist(db, run)
    forge.persist(db, run, replace=True)
    assert db.query(IndigoExercise).count() == 1


def test_persist_refuses_an_incomplete_run(db):
    run = _run(db, [ETALONS[0]])
    grid = forge.load_json(run / "grid.json")
    grid.append({**grid[0], "slot": "s02"})
    forge.dump_json(run / "grid.json", grid)
    with pytest.raises(SystemExit, match="incomplet"):
        forge.persist(db, run)


def test_resume_finds_the_first_empty_slot(db):
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="m")[0]
    assert forge.remaining_slots(run)[0]["slot"] == "s01"
    forge.dump_json(run / "slots" / "s01.json", {})
    assert forge.remaining_slots(run)[0]["slot"] == "s02"
    assert run in forge.unfinished_runs()


def test_chapter_prepares_one_run_per_competency(db):
    runs = forge.prepare(db, "fr", "3e", "Z1", model="m")
    assert len(runs) == 2
    assert {forge.load_json(r / "payload.json")["competency"] for r in runs} == {"Z1.1", "Z1.2"}
    problems = forge.prepare(db, "fr", "3e", "Z1", model="m", problems=True)
    assert len(problems) == 1
    assert forge.load_json(problems[0] / "grid.json")[0]["kind"] == "problem"


def test_context_order_is_stable_and_free_of_dates(db):
    first = forge.prepare(db, "fr", "3e", "Z1.1", model="m")[0]
    second = forge.prepare(db, "fr", "3e", "Z1.1", model="m")[0]
    first_order = forge.load_json(first / "payload.json")["context"]["read_order"]
    assert first_order == forge.load_json(second / "payload.json")["context"]["read_order"]
    assert first.name not in " ".join(first_order)


# ------------------------------------------------------------------- goldfish
def test_goldfish_compares_answers_by_value():
    assert goldfish.answer_matches("17", ["$17$"]) is True
    assert goldfish.answer_matches("$\\dfrac{1}{2}$", ["1/2"]) is True
    assert goldfish.answer_matches("18", ["$17$"]) is False
    assert goldfish.answer_matches("Peu importe", []) is None
    assert goldfish.answer_matches("$27$ ; $102$", ["$27$", "$102$"]) is True
    assert goldfish.answer_matches("$27$", ["$27$", "$102$"]) is False


def test_goldfish_verdict_severity():
    leaf = ETALONS[0]["exercice"]
    good = {"comprehensible": True, "reponse": "17", "difficulte": "facile"}
    assert goldfish.judge_student(good, leaf, 1) == (None, [])
    assert goldfish.judge_student({**good, "reponse": "42"}, leaf, 1)[0] == "blocker"
    assert goldfish.judge_student({**good, "seconde_lecture": "x"}, leaf, 1)[0] == "blocker"
    assert goldfish.judge_student({**good, "ambiguites": "x"}, leaf, 1)[0] == "major"
    assert goldfish.judge_student({**good, "difficulte": "difficile"}, leaf, 1)[0] == "major"
    assert goldfish.judge_student({**good, "comprehensible": False}, leaf, 1)[0] == "blocker"


def test_goldfish_second_failure_means_rewrite(db, monkeypatch):
    run = _run(db, [ETALONS[0]])
    forge.render(db, run)
    answers = {"cartes": [{"comprehensible": False, "reponse": "42"}]}
    seen_images = []

    def fake(_cli, _system, _text, images):
        seen_images.extend(images)
        return answers if "cartes" in _text or "carte" in _text else {"messages": []}
    monkeypatch.setattr(goldfish, "ask", fake)
    state = goldfish.run(run, cli="claude")
    assert state["verdicts"]["s01"]["flag"] and not state["verdicts"]["s01"]["rewrite"]
    state = goldfish.run(run, slots=["s01"], cli="claude")
    assert state["verdicts"]["s01"]["rewrite"] is True
    assert all(p.suffix == ".png" for p in seen_images)


# ----------------------------------------------------------------- instructions
def test_agents_md_is_the_only_instruction_source():
    assert not (REPO / "CLAUDE.md").exists()
    assert not (REPO / ".agents").exists()
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "agents/forge/FORGE.md" in text


def test_taxonomy_has_unique_codes_under_the_subject():
    data = json.loads((REPO / "backend/app/data/misconceptions/math.json").read_text(encoding="utf-8"))
    codes = [e["code"] for e in data["errors"]]
    assert len(codes) == len(set(codes))
    assert all(c.startswith("math.") for c in codes)
    assert "autre" in fc.error_codes("math")


# ------------------------------------------------------------------- second run
def test_complement_only_fills_what_is_missing(db):
    runs = forge.prepare(db, "fr", "3e", "Z1.1", model="m", complement={3: 6})
    grid = forge.load_json(runs[0] / "grid.json")
    assert len(grid) == 6 and {s["level"] for s in grid} == {3}
    assert len({s["slot"] for s in grid}) == 6
    payload = forge.load_json(runs[0] / "payload.json")
    assert payload["complement"] is True
    assert payload["context"]["read_order"][-1].endswith("existants.md")


def test_complement_keeps_the_usual_proportions_when_not_told_otherwise():
    assert forge.split_complement(10) == {1: 4, 2: 4, 3: 2}


def test_complement_remembers_existing_exercises_without_their_full_text(db):
    from app.models import GeneratedExercise
    comp = _comp(db)
    long_statement = "Calcule le périmètre de ce grand rectangle de longueur 125 et de largeur 48 maintenant."
    db.add(GeneratedExercise(competency_id=comp.id, difficulty_level=1, statement=long_statement, status="active",
                             response_type="short_text", grading_json={"family_id": "perimetre"}, source="forge"))
    db.commit()
    lines = forge.existing_signatures(db, [comp.id], False)
    assert "perimetre" in lines[0] and "125,48" in lines[0]
    assert long_statement not in lines[0]


# ------------------------------------------------------- remarques de relecture du pilote
def _cell(value, **extra):
    return {"type": "integer", "value": value, **extra}


def _rational(num, den, **extra):
    return {"type": "rational", "value": [num, den], **extra}


def test_table_columns_stay_equal_when_answers_differ_in_kind():
    """Pilote A2.1 s16 : une colonne de fractions gonflait et écrasait les autres."""
    cells = [[_rational(5, 8), _cell(8), _cell(5)], [_rational(13, 6, given=True), _cell(6), _cell(13)],
             [_rational(9, 9), _cell(9, given=True), _cell(9)], [_rational(3, 2), _cell(2), _cell(3, given=True)]]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ["Écriture fractionnaire", "Parts égales de l'unité", "Parts prises"],
                                      ["cinq huitièmes", "treize sixièmes", "neuf neuvièmes", "trois demis"], cells, 9)
    assert len({round(w, 2) for w in geometry["col_ws"]}) == 1


def test_table_columns_stay_equal_with_many_given_values():
    """Pilote A2.2 s20 : colonnes A/B/C/D de largeurs différentes pour rien."""
    cells = [[_cell(2437, given=True), _cell(5006, given=True), _cell(4092), _cell(306, given=True)],
             [_cell(2, given=True), _cell(5), _cell(4, given=True), _cell(0)],
             [_cell(4, given=True), _cell(0), _cell(0, given=True), _cell(3)]]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ["$A$", "$B$", "$C$", "$D$"],
                                      ["Numérateur", "Unités", "Dixièmes"], cells, 9)
    assert len({round(w, 2) for w in geometry["col_ws"]}) == 1


def test_only_the_column_that_needs_room_is_wider_and_the_others_stay_equal():
    widths = pdfgen._fair_widths([10.0, 10.0, 60.0, 10.0], 120.0)
    assert widths[2] == 60.0 and len({round(w, 3) for i, w in enumerate(widths) if i != 2}) == 1
    assert sum(widths) == pytest.approx(120.0)
    assert sum(pdfgen._fair_widths([80.0, 80.0], 100.0)) == pytest.approx(100.0)


def test_handwriting_measures_what_a_student_writes():
    from app.services import handwriting as hw
    assert hw.forms({"type": "rational", "value": [5, 6]}) == ("5", "6")
    assert hw.forms("8/9") == ("8", "9")
    assert hw.forms({"type": "decimal", "value": 5.03}) == ("5,03",)
    assert hw.forms({"type": "integer", "value": 4092}) == ("4\u202f092",)
    assert hw.plain("$\\dfrac{25}{100} + 3 \\times 2$") == "25/100 + 3 × 2"
    four_digits = hw.need({"type": "integer", "value": 4092})
    assert four_digits[0] > hw.need({"type": "integer", "value": 40})[0]
    fraction = hw.need({"type": "rational", "value": [5, 6]})
    assert fraction[1] > four_digits[1]
    assert not hw.fits({"type": "integer", "value": 123456789}, 20 * hw.MM, 8 * hw.MM)


def test_a_table_gives_each_answer_the_room_to_be_handwritten():
    from app.services import handwriting as hw
    long_answer = _cell(123456)
    cells = [[_cell(1), long_answer], [_cell(2), _cell(3)]]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ["A", "B"], ["x", "y"], cells, 9)
    box = geometry["col_ws"][1] - 2 * pdfgen._TABLE_CELL_PAD
    assert hw.fits(long_answer, box, 8 * hw.MM)


def _written(statement, rtype, expected, grading=None):
    return card_preview.written_preview({"statement": statement, "response_type": rtype,
                                         "expected": expected, "grading": grading or {},
                                         "difficulty": 2})


def test_written_preview_accepts_an_answer_that_fits_and_refuses_one_that_does_not():
    table = {"cells": [[_rational(5, 6)], [_rational(7, 8)]]}
    _png, ok = _written("Complète.", "table_fill", table,
                        {**table, "row_labels": ["a", "b"]})
    assert ok and not any(f["problem"] for f in ok)
    grading = {**table, "row_labels": ["a", "b"],
               "feedback": {"wrong_answers": [{"cell": [1, 0], "value": "1234567890123456789012345678901234567890"}]}}
    _png, tight = _written("Complète.", "table_fill", table, grading)
    assert [f["name"] for f in tight if f["problem"]] == ["case 2,1"]


def test_written_preview_counts_the_lines_of_a_written_answer():
    steps = [{"expected_text": "Une très longue phrase de démonstration " * 8}]
    _png, fields = _written("Explique.", "multiline_text", {"steps": steps}, {"lines": 3})
    assert fields[0]["problem"] and "ligne" in fields[0]["problem"]
    _png, fields = _written("Explique.", "multiline_text", {"steps": [{"expected_text": "$2 + 3 = 5$"}]}, {"lines": 3})
    assert not fields[0]["problem"]


def test_blank_after_a_question_goes_to_its_own_line():
    """Pilote A2.1 s10 : la case écartait l'interligne de la question."""
    lay = pdfgen._rich_layout("Quelle fraction d'une tablette a-t-elle mangée ? {{blank_tall}}", 200, 9, blank_fs=10)
    assert not any(s[0] == "blank" for s in lay["lines"][0]["segs"])
    assert [s[0] for s in lay["lines"][-1]["segs"]] == ["blank"]
    assert pdfgen.broken_leading({"intro": lay}) == 0
    short = pdfgen._rich_layout("$x =$ {{blank}}", 200, 9)
    assert len(short["lines"]) == 1


def test_a_blank_in_the_middle_of_a_wrapped_sentence_is_reported():
    text = "La partie grise vaut {{blank}} centièmes, soit {{blank}} dixièmes et {{blank}} centièmes en tout."
    assert pdfgen.broken_leading({"intro": pdfgen._rich_layout(text, 150, 9)}) > 0


def test_written_zone_keeps_the_card_width_beside_a_figure():
    """Pilote A2.2 s18 : en haut énoncé et figure, en bas la question et la rédaction."""
    figure = {"type": "template", "params": {"name": "cube", "width_mm": 28}}
    item = {"statement": "Voici un cube dont chaque arête mesure trois centimètres.\nQuel est son volume ? Rédige.", "response_type": "multiline_text",
            "grading": {"lines": 3}, "expected": {}, "level3": 2, "figure": figure, "figure_layout": "grid_right"}
    layout = pdfgen._exercise_layout(item, 9, 9)[0]
    assert not layout["figure_grid"] and layout["intro_after"]["height"] > 0
    assert layout["height"] == pytest.approx(layout["side_top_h"] + layout["intro_after"]["height"])
    card = {"statement": item["statement"], "response_type": "multiline_text", "grading": {"lines": 3},
            "expected": {}, "difficulty": 2, "figure": figure}
    assert forge.best_layout(card)[0] not in pdfgen.FIGURE_GRID_LAYOUTS


def test_shorter_matching_column_is_centered_on_the_longer_one():
    """Pilote A2.2 s17 : trois points à gauche collés en haut, cinq à droite."""
    geo = pdfgen._matching_geometry(["a", "b", "c"], ["1", "2", "3", "4", "5"], 9, pdfgen.COL_W)
    left, right = geo["ys"]
    assert sum(left) / 3 == pytest.approx(sum(right) / 5)
    same = pdfgen._matching_geometry(["a", "b"], ["1", "2"], 9, pdfgen.COL_W)
    assert same["ys"][0] == same["ys"][1]


def test_checkboxes_of_a_qcm_row_share_the_same_height():
    """Pilote A2.3 s05 : une fraction descendait sa case sous celle du nombre voisin."""
    choices = ["$5{,}30$", "$\\dfrac{53}{10}$", "$5 + \\dfrac{3}{10}$", "$5{,}03$"]
    c = canvas.Canvas(io.BytesIO())
    zone = pdfgen._zone_height("qcm_multiple", choices, pdfgen.COL_W, 9)
    meta = pdfgen._draw_answer_zone(c, 20, 100, pdfgen.COL_W, zone, "qcm_multiple", choices, 9)
    assert len({round(box["y_pt"], 1) for box in meta["boxes"]}) == 1


def test_grid_row_label_is_centered_in_its_cell():
    """Pilote A2.1 s08 : les fractions de la colonne de gauche collaient à gauche."""
    drawn = []
    original = pdfgen._draw_rich

    def spy(c, x, y, lay, **kwargs):
        drawn.append(kwargs)
        return original(c, x, y, lay, **kwargs)

    rows = [{"label": "$\\dfrac{7}{9}$"}, {"label": "$\\dfrac{3}{2}$"}]
    c = canvas.Canvas(io.BytesIO())
    with patch.object(pdfgen, "_draw_rich", spy):
        pdfgen._draw_grid_zone(c, 20, 100, pdfgen.COL_W, 60, ["Vrai", "Faux"], rows, 9)
    assert all(kwargs.get("centered") for kwargs in drawn)


def test_labelled_blanks_are_refused_in_favor_of_a_table():
    leaf = {"response_type": "multi_blank", "statement": "Abscisse de $A$ : {{blank}}    Abscisse de $B$ : {{blank}}",
            "blanks": [_cell(1), _cell(2)], "answer_form": "any", "blank_feedback": "x", "fallback_feedback": "y",
            "correct_feedback": "z"}
    problems, _raw, _extra = forge._leaf_problems(leaf, 2, "math")
    assert any("table_fill" in p for p in problems)


def test_written_previews_stay_out_of_what_goldfish_reads():
    source = (Path(forge.__file__).parent / "goldfish.py").read_text()
    assert '"ecrit"' not in source


def test_single_line_statement_stays_above_its_figure():
    placed = forge._place_figure("Quelle est cette figure ?", {"kind": "svg"}, "qcm_single")
    assert placed.index("Quelle") < placed.index("{{figure}}")


def test_layout_chosen_at_render_replaces_the_one_of_the_author():
    grading = {"figure_layout": "above", "figure_scale": 0.85, "lines": 3}
    assert forge._apply_layout(grading, {"figure_layout": None, "figure_scale": 0.7}) == {"figure_scale": 0.7, "lines": 3}
    assert forge._apply_layout(grading, {"figure_layout": "right", "figure_scale": 1.0}) == {"figure_layout": "right", "lines": 3}
    assert forge._apply_layout(grading, None) == grading


def test_row_label_wraps_to_leave_air_around_the_written_answers():
    """Pilote A2.3 s20 : « 0,04 » tenait au millimètre, à côté d'un libellé sur une ligne."""
    from app.services import handwriting as hw
    answers = [{"type": "decimal", "value": 5.34}, {"type": "decimal", "value": 5.38}, {"type": "decimal", "value": 5.08}]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ["$A$", "$B$", "$C$"],
                                      ["Abscisse", "Nombre total de centièmes"], [answers, answers], 9)
    box = geometry["col_ws"][0] - 2 * pdfgen._TABLE_CELL_PAD
    assert box >= hw.need_w(answers[0]) + 3 * hw.MM
    assert len(geometry["row_lays"][1]["lines"]) <= 3


# ------------------------------------------------------------- seconde relecture du pilote
def test_french_punctuation_never_starts_a_line_even_without_typography():
    """Pilote A2.1 s01 : le brouillon gardait une espace ordinaire avant le « ? »."""
    width = pdfgen._natural_w("Quel est ce nombre", 9) + 1
    lay = pdfgen._rich_layout("Quel est ce nombre ?", width, 9)
    assert all(ln["segs"][0][1].strip("\u00a0") != "?" for ln in lay["lines"])


def test_blank_alone_on_its_line_is_centered():
    """Pilote A2.2 s10 : marges égales autour de la case et de son unité."""
    lay = pdfgen._rich_layout("Combien de centièmes en tout ? {{blank}} centièmes", 200, 9, blank_fs=10)
    tail = lay["lines"][-1]
    assert tail["segs"][0][0] == "blank"
    assert tail["indent"] == pytest.approx((200 - tail["w"]) / 2)


def test_short_text_beside_a_tall_figure_gives_way_to_a_centered_figure():
    """Pilote A2.2 s12 : une ligne de texte à côté d'une grande figure laissait un trou."""
    figure = {"type": "template", "params": {"name": "cube", "width_mm": 40}}
    item = {"statement": "Voici.\nQuel est son volume ?", "response_type": "qcm_single", "choices": ["$1$", "$2$"],
            "grading": {"choices": ["$1$", "$2$"]}, "level3": 1, "figure": figure, "figure_layout": "right"}
    assert not pdfgen._exercise_layout(item, 9, 9)[0].get("figure_side")


def test_side_figure_shares_the_free_width_on_both_sides():
    figure = {"type": "template", "params": {"name": "cube", "width_mm": 25}}
    item = {"statement": "Regarde ce petit cube posé sur la table de la classe. Que vois-tu ?", "response_type": "qcm_single", "choices": ["$1$", "$2$"],
            "grading": {"choices": ["$1$", "$2$"]}, "level3": 1, "figure": figure, "figure_layout": "right"}
    layout = pdfgen._exercise_layout(item, 9, 9)[0]
    column = pdfgen.COL_W - 2 * pdfgen.CARD_PAD - layout["figure"][1] - pdfgen._FIG_SIDE_GAP
    used = max(ln.get("indent", 0.0) + ln["w"] for ln in layout["intro"]["lines"])
    assert layout["figure_shift"] == pytest.approx((column - used) / 2)


def test_label_column_shrinks_while_the_table_height_holds():
    """Pilote A2.3 s20 : les libellés se replient tant que les lignes ne montent pas."""
    cells = [[_cell(534), _cell(538), _cell(580)] for _ in range(2)]
    labels = ["Chiffre des dixièmes", "Nombre total de centièmes"]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ["$A$", "$B$", "$C$"], labels, cells, 9)
    fs = geometry["lab_fs"]
    assert geometry["rowlab_w"] < pdfgen._natural_w(labels[1], fs)
    wide_h = 2 * pdfgen._TABLE_ROW_MIN_H
    assert sum(geometry["row_hs"]) <= wide_h + pdfgen._TABLE_LABEL_SLACK_H + 0.01


def test_checkbox_grid_shrinks_to_its_content():
    """Pilote A2.3 s03 : du blanc dans la colonne des libellés, tableau à centrer."""
    short = pdfgen._grid_geometry(pdfgen.COL_W, ["Vrai", "Faux"], [{"label": "$\\dfrac{1}{2} = 0{,}5$"}], 9)
    inner = pdfgen.COL_W - 2 * pdfgen.CARD_PAD
    assert short["rowlab_w"] + short["ncols"] * short["opt_w"] < inner * 0.8


def test_empty_corner_cell_has_no_outer_lines():
    """Pilote A2.2 s08 : l'élève pouvait prendre la cellule d'angle pour une case."""
    calls = []

    class Spy:
        def rect(self, *a, **k):
            calls.append(("rect", a))

        def line(self, *a):
            calls.append(("line", a))

    pdfgen._table_frame(Spy(), 0, 0, 100, 50, 30, 10)
    assert all(name == "line" for name, _ in calls)
    assert (30, 50, 100, 50) in [a for _, a in calls]        # le haut part après l'angle
    assert (0, 0, 0, 40) in [a for _, a in calls]            # la gauche s'arrête sous l'angle
    calls.clear()
    pdfgen._table_frame(Spy(), 0, 0, 100, 50, 0.0, 10)
    assert calls[0][0] == "rect"


def test_guides_go_where_the_author_put_them():
    before = forge.place_guides("Contexte.\nQuestion ?", [{"text": "Aide.", "position": "avant_question"}])
    assert before == "Contexte.\n{{aide}} Aide.\nQuestion ?"
    after = forge.place_guides("Question ?", [{"text": "Aide.", "position": "apres_question"}])
    assert after == "Question ?\n{{aide}} Aide."
    blank = forge.place_guides("Question ? {{blank}} cm", [{"text": "Aide.", "position": "apres_question"}])
    assert blank == "Question ?\n{{aide}} Aide.\n{{blank}} cm"


def test_guide_is_refused_when_badly_placed_or_giving_the_answer():
    base = {"statement": "Question ?", "guides": [{"text": "Une aide de six mots utiles.", "position": "avant_question"}]}
    assert any("saut de ligne" in p for p in forge._guide_problems(base))
    assert forge._guide_problems({**base, "guides": [{"text": "Trop court", "position": "apres_question"}]})
    leaf = {"response_type": "short_text", "statement": "Combien ?", "answer": {"type": "integer", "value": 463}}
    assert forge._guide_leaks(leaf, [{"text": "La réponse est $463$."}])
    assert not forge._guide_leaks(leaf, [{"text": "Une unité vaut $100$ centièmes."}])


def test_placed_guide_stays_before_the_answer_zone():
    item = {"statement": "Contexte.\n{{aide}} Une aide utile ici.\nQuestion ?", "response_type": "qcm_single",
            "choices": ["$1$", "$2$"], "grading": {"choices": ["$1$", "$2$"], "guides_placed": True}, "level3": 1}
    layout = pdfgen._exercise_layout(item, 9, 9)[0]
    assert [bool(ln.get("guide")) for ln in layout["intro"]["lines"]] == [False, True, False]
    assert not layout["guides"]["lines"]
    legacy = {**item, "grading": {"choices": ["$1$", "$2$"]}}
    assert pdfgen._exercise_layout(legacy, 9, 9)[0]["guides"]["lines"]


def test_placed_guides_of_a_composite_keep_their_place():
    parts = [{"response_type": "short_text", "statement": "{{aide}} Avant la question a.\nPremière ?",
              "grading": {}, "expected": {}},
             {"response_type": "short_text", "statement": "Seconde ?\n{{aide}} Après la question b.",
              "grading": {}, "expected": {}}]
    item = {"statement": "Contexte.\n{{aide}} Pour tout l'exercice.", "response_type": "composite", "level3": 1,
            "grading": {"guides_placed": True, "parts": parts}, "part_item_ids": ["a", "b"]}
    layout = pdfgen._composite_layout(item, 9, 9)
    assert any(ln.get("guide") for ln in layout["stmt"]["intro"]["lines"])
    assert any(ln.get("guide") for ln in layout["parts"][0]["before"]["lines"])
    assert any(ln.get("guide") for ln in layout["parts"][1]["frag"]["lines"])
    assert not layout["parts"][1]["guides"]["lines"]


def test_calculator_is_purged_from_stored_json():
    from app import db as db_module
    data = {"indigo": {"calculator": "interdite", "title": "x"}, "liste": [{"calculator": "x", "a": 1}]}
    assert db_module._without_calculator(data) == {"indigo": {"title": "x"}, "liste": [{"a": 1}]}


def test_persisted_draft_is_typeset_like_the_reviewed_card():
    """Pilote A2.1 s01 : l'aperçu était typographié, pas le brouillon enregistré."""
    source = (Path(forge.__file__)).read_text()
    assert 'typography.apply_to_record({"statement": statement' in source


def test_goldfish_reader_may_comment_after_the_value():
    assert goldfish.answer_matches("8/3 d'une tablette (soit 2 tablettes et 2/3)", ["8/3"]) is True
    assert goldfish.answer_matches("8/9 d'une tablette", ["8/3"]) is False


def test_goldfish_keeps_the_first_json_object():
    assert goldfish._parse('{"messages": []}\n\n{"autre": 1}') == {"messages": []}
    assert goldfish._parse('Voici : {"cartes": [1]} merci') == {"cartes": [1]}

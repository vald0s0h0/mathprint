"""Géométrie à tracer (services/construction) : contrat, impression, lecture CV en
mode ombre. Le banc complet est scripts/construction_bench.py ; ici, un cas par règle."""
import copy
import io

import numpy as np
import pytest
from reportlab.pdfgen import canvas

from app.services import construction as C
from app.services import pdfgen, worker_cv

SYMETRIE = {
    "cols": 16, "rows": 9,
    "points": {"A": [1, 1], "B": [6, 2], "C": [2, 7], "P": [8, 0], "Q": [8, 9],
               "A'": [15, 1], "B'": [10, 2], "C'": [14, 7],
               "A1": [9, 1], "B1": [14, 2], "C1": [10, 7]},
    "given": {"points": ["A", "B", "C"], "polygons": [["A", "B", "C"]],
              "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
    "expected": {"polygons": [["A'", "B'", "C'"]]},
    "errors": [{"id": "glisse", "trace": {"polygons": [["A1", "B1", "C1"]]}}],
}
CERCLE = {"grid": False, "cols": 14, "rows": 10, "points": {"O": [7, 5], "A": [10, 7]},
          "given": {"points": ["O", "A"]}, "expected": {"circles": [{"center": "O", "through": "A"}]}}


def _spec(**changes):
    spec = copy.deepcopy(SYMETRIE)
    spec.update(changes)
    return spec


# ------------------------------------------------------------------ contrat
def test_valid_specs_have_no_problem():
    assert C.problems(SYMETRIE) == []
    assert C.problems(CERCLE) == []


@pytest.mark.parametrize("spec, needle", [
    (_spec(cols=20), "cols"),
    (_spec(expected={"points": ["A"]}), "est imprimé"),
    (_spec(expected={"segments": [["A", "B"]]}), "caché sous la figure"),
    (_spec(expected={"segments": [["A'", "Z"]]}), "absent de points"),
    (_spec(points={**SYMETRIE["points"], "Z": [15.5, 1]}, expected={"segments": [["A'", "Z"]]}), "trop court"),
    (_spec(errors=[{"id": "x", "trace": {"polygons": [["A'", "B'", "C'"]]}}]), "se confond"),
    (_spec(given={}, grid=True), "sans quadrillage"),
    (_spec(given={}, grid=False, points={**SYMETRIE["points"], "C'": [17, 7]}), "déborde"),
    (_spec(tolerance_mm=5), "tolerance_mm"),
])
def test_defects_are_named(spec, needle):
    assert any(needle in p for p in C.problems(spec)), C.problems(spec)


# ------------------------------------------------------------------ impression
def test_zone_height_holds_the_grid_and_meta_locates_it():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    grading = {"construction": SYMETRIE}
    h = pdfgen._zone_height("manual_drawing", [], pdfgen.COL_W, 9, grading)
    assert h == pytest.approx(C.zone_height(C.normalize(SYMETRIE)))
    meta = pdfgen._draw_answer_zone(c, 30, 300, pdfgen.COL_W, h, "manual_drawing", [], 9, grading)
    info = meta["construction"]
    ox, oy = info["origin_pt"]
    assert 30 + pdfgen.CARD_PAD <= ox and ox + 16 * info["unit_pt"] <= 30 + pdfgen.COL_W - pdfgen.CARD_PAD
    assert 300 < oy and oy + 9 * info["unit_pt"] < 300 + h
    assert len(info["label_boxes"]) == 4            # A, B, C et (d)


def test_plain_manual_drawing_is_unchanged():
    assert pdfgen._zone_height("manual_drawing", [], pdfgen.COL_W, 9, {}) == pdfgen._MANUAL_DRAWING_H


# ------------------------------------------------------------------ lecture
def test_right_drawing_is_read_right_and_sure():
    for style in ("pen", "pencil"):
        img, meta = C.synthetic_page(SYMETRIE, "expected", seed=4, style=style)
        got = C.read(img, meta)
        assert got["status"] == "read" and got["fraction"] == 1.0
        assert got["confidence"] >= C.SELFTEST_CONFIDENCE, got["reasons"]


def test_blank_copy_is_blank():
    img, meta = C.synthetic_page(SYMETRIE, "blank")
    assert C.read(img, meta)["status"] == "blank"


def test_typical_error_is_recognised_and_never_full_credit():
    img, meta = C.synthetic_page(SYMETRIE, "error:glisse", seed=2)
    got = C.read(img, meta)
    assert got["fraction"] < 1 and got["error_id"] == "glisse"


def test_page_misregistration_is_corrected_by_the_printed_figure():
    img, meta = C.synthetic_page(SYMETRIE, "expected", seed=5, shift_mm=(1.0, -0.8))
    got = C.read(img, meta)
    assert got["fraction"] == 1.0 and got["confidence"] >= C.SELFTEST_CONFIDENCE
    assert got["shift_px"] != [0, 0]


def test_wrong_place_is_never_full_credit():
    img, meta = C.synthetic_page(CERCLE, "expected", seed=1, offset_units=(1, 0))
    assert C.read(img, meta)["fraction"] < 1


def test_grey_copier_sends_to_the_teacher():
    img, meta = C.synthetic_page(SYMETRIE, "expected", copier_gray=True)
    got = C.read(img, meta)
    assert got["confidence"] <= C.CAP_DOUBT and "quadrillage resté visible" in got["reasons"]


def test_selftest_passes_on_a_readable_card():
    assert C.selftest(SYMETRIE) == []


# ------------------------------------------------------------------ correction
def test_teacher_overlay_draws_the_expected_trace_in_red():
    img, meta = C.synthetic_page(SYMETRIE, "blank")
    info = meta["construction"]
    ox, oy, u = *info["origin_pt"], info["unit_pt"]
    zone = {"x_pt": ox - 5, "y_pt": oy - 5, "w_pt": 16 * u + 10, "h_pt": 9 * u + 10, "padding_pt": 3}
    crop = worker_cv.crop_zone(img, zone["x_pt"], zone["y_pt"], zone["w_pt"], zone["h_pt"], padding_pt=3)
    out = worker_cv.draw_expected_answer(crop, zone_geo=zone, meta=meta, expected={},
                                         response_type="manual_drawing")
    red = (out[..., 2] > 150) & (out[..., 1] < 80) & (out[..., 0] < 80)
    assert red.sum() > 200
    # le tracé rouge est du côté de l'image, à droite de l'axe
    xs = np.nonzero(red)[1]
    assert xs.mean() > crop.shape[1] / 2


# ------------------------------------------------------------------ tracé libre
TRIANGLE_LIBRE = {"cols": 14, "rows": 10, "given": {},
                  "points": {"A": [2, 2], "B": [12, 2], "C": [8.4, 6.8]},
                  "expected": {"polygons": [["A", "B", "C"]]}}
ANGLE_LIBRE = {"cols": 16, "rows": 9, "given": {},
               "points": {"O": [2, 1.5], "X": [12, 1.5], "Y": [7.785, 8.394], "Z": [-2.5, 6.862]},
               "expected": {"rays": [["O", "X"], ["O", "Y"]]},
               "errors": [{"id": "envers", "trace": {"rays": [["O", "X"], ["O", "Z"]]}}]}


def test_free_construction_has_no_grid_and_a_rounded_box():
    spec = C.normalize(TRIANGLE_LIBRE)
    assert C.is_free(spec) and spec["grid"] is False
    assert C.problems(TRIANGLE_LIBRE) == []
    assert any("sans quadrillage" in p for p in C.problems({**TRIANGLE_LIBRE, "grid": True}))


def test_free_triangle_is_found_wherever_the_student_draws_it():
    for seed in range(3):
        img, meta = C.synthetic_page(TRIANGLE_LIBRE, "expected", seed=seed, arcs=True)
        got = C.read(img, meta)
        assert got["fraction"] == 1.0 and got["confidence"] >= C.SELFTEST_CONFIDENCE, got
        assert all(abs(e["length_mm"] - e["expected_mm"]) <= C.LENGTH_OK_MM for e in got["elements"])
        assert "placement" in got


def test_free_wrong_lengths_are_never_full_credit_and_say_why():
    img, meta = C.synthetic_page(TRIANGLE_LIBRE, "expected", seed=1, scale=0.8)
    got = C.read(img, meta)
    assert got["fraction"] < 1
    if any(e["state"] == "wrong_length" for e in got["elements"]):
        assert "au lieu de" in C.feedback_text(got)


def test_free_angle_error_is_recognised():
    img, meta = C.synthetic_page(ANGLE_LIBRE, "error:envers", seed=2)
    got = C.read(img, meta)
    assert got["fraction"] < 1 and got["error_id"] == "envers"


def test_measured_feedback_names_the_missing_element():
    reading = {"status": "read", "elements": [{"name": "[AB]", "state": "present"},
                                              {"name": "[BC]", "state": "absent"}]}
    assert C.feedback_text(reading).startswith("Il manque [BC]")
    reading = {"status": "read", "elements": [{"name": "[AB]", "state": "wrong_length",
                                               "length_mm": 56.0, "expected_mm": 60.0}]}
    assert C.feedback_text(reading) == "Ton côté [AB] mesure 5,6 cm au lieu de 6 cm : pars du 0 de la règle."


# ------------------------------------------------------------------ notation au seuil
def test_verdict_applies_the_note_only_above_the_threshold():
    sure = {"status": "read", "fraction": 2 / 3, "confidence": 0.95}
    assert C.verdict(sure, 1.0, 0.9)["tier"] == "B"
    assert C.verdict(sure, 1.0, 0.9)["score"] == pytest.approx(2 / 3)
    assert C.verdict({**sure, "confidence": 0.7}, 1.0, 0.9)["reason_code"] == "construction_low_confidence"
    assert C.verdict({"status": "blank", "confidence": 0.99}, 1.0, 0.9)["reason_code"] == "construction_blank"
    assert C.verdict({"status": "error"}, 1.0, 0.9)["tier"] == "D"
    assert C.verdict(sure, 1.0, 1.01)["tier"] == "D"          # 101 % : jamais automatique


def test_audit_sample_is_stable_and_proportional():
    from app.services import pipeline
    picks = [pipeline._audit_pick(f"r{i}", 0.2) for i in range(2000)]
    assert 0.16 < sum(picks) / len(picks) < 0.24
    assert picks == [pipeline._audit_pick(f"r{i}", 0.2) for i in range(2000)]
    assert not any(pipeline._audit_pick(f"r{i}", 0.0) for i in range(100))


def test_calibration_recommends_the_lowest_threshold_without_error():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_construction.py"
    spec = importlib.util.spec_from_file_location("calibrate_construction", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = ([{"confidence": 0.95, "fraction": 1.0, "teacher": 1.0}] * 40
            + [{"confidence": 0.72, "fraction": 1.0, "teacher": 0.5}]
            + [{"confidence": 0.8, "fraction": 0.0, "teacher": 0.0}] * 5)
    _table, best = mod.sweep(rows, 0.01, 30)
    assert 0.72 < best <= 0.8
    _table, best = mod.sweep(rows, 0.01, 100)
    assert best is None


def test_micro_correction_of_a_trace_prefers_the_typical_error_then_the_measure():
    from types import SimpleNamespace
    from app.services import microcorrection
    feedback = {"lines": 2, "options": {"envers": {"text": "Lis le rapporteur depuis le 0 de ton côté.",
                                                    "error": "math.geo.angle_mesure"}},
                "fallback": {"text": "Reprends la construction pas à pas."},
                "blank": {"text": "Commence par tracer [AB]."}}
    item = SimpleNamespace(grading_json={"feedback": feedback, "construction": ANGLE_LIBRE},
                           response_type="manual_drawing", difficulty=5)
    decision = SimpleNamespace(reason_code="construction_cv", score=0.0, max_score=1.0, evidence_json={})

    def pick(reading):
        return microcorrection.select(item, decision, None,
                                      SimpleNamespace(raw_json={"construction": reading}))

    chosen = pick({"status": "read", "error_id": "envers", "elements": []})
    assert chosen.kind == "option" and chosen.errors == [("math.geo.angle_mesure", "envers")]
    chosen = pick({"status": "read", "error_id": None, "elements": [
        {"name": "[AB]", "state": "present"}, {"name": "[BC]", "state": "absent"}]})
    assert chosen.kind == "measure" and "[BC]" in chosen.text
    assert pick({"status": "read", "elements": []}).kind == "generic"
    blank = SimpleNamespace(reason_code="construction_blank", score=0.0, max_score=1.0, evidence_json={})
    assert microcorrection.select(item, blank, None, None).kind == "blank"

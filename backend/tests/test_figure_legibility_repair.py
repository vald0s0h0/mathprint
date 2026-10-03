"""La reprise de B2 conserve les données et la numérotation des candidats."""
import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.repair_figure_legibility import reflow_candidates, repair_nested


def candidates():
    return {"points": {f"{i}_{n}": {"xy": [i*5+x, y], "label": n}
                       for i in range(3) for n, x, y in (("A", 0, 0), ("B", 3, 0), ("C", 1, 2))},
            "polygons": [[f"{i}_{n}" for n in ("A", "B", "C")] for i in range(3)],
            "circles": [{"center": "0_A", "radius": 2}],
            "texts": [{"at": [i*5+1.5, -0.9], "text": str(i+1)} for i in range(3)],
            "width_mm": 93}


def test_candidate_reflow_preserves_geometry_and_is_idempotent():
    before = candidates()
    saved = copy.deepcopy(before)
    after = reflow_candidates(before)
    assert before == saved
    for names in before["polygons"]:
        for a, b in zip(names, names[1:]+names[:1]):
            assert math.dist(before["points"][a]["xy"], before["points"][b]["xy"]) == math.dist(
                after["points"][a]["xy"], after["points"][b]["xy"])
    assert before["circles"] == after["circles"]
    assert after["points"]["2_A"]["xy"][1] < after["points"]["0_A"]["xy"][1]
    assert [t["text"] for t in after["texts"]] == ["1", "2", "3"]
    assert reflow_candidates(after) == after


def test_figures_linking_candidates_are_not_reflowed():
    spec = candidates()
    spec["segments"] = [["0_A", "1_B"]]
    assert reflow_candidates(spec) == spec


def test_nested_repair_keeps_all_answers_and_grading():
    question = {"figure": {"type": "geo", "params": candidates()},
                "expected": {"correct": [2]}, "grading": {"max_score": 2},
                "statement": "Choisis la figure 3."}
    before = copy.deepcopy(question)
    assert repair_nested({"parts": [question]}) == 1
    assert {k: v for k, v in question.items() if k != "figure"} == {
        k: v for k, v in before.items() if k != "figure"}

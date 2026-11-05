"""Composition automatique d'un sujet : le professeur choisit ses cartes, le moteur
les place, et chaque élève reçoit une copie mélangée.

Invariants surveillés ici :
  1. la place — une feuille acceptée tient toujours, recto-verso compris, et le
     navigateur (packEngine.ts) rend le même verdict que le serveur ;
  2. l'ordre — facile, moyen, difficile à une carte près, problèmes en dernier ;
  3. les champs — mélangés par copie sans jamais changer la bonne réponse, ni
     agrandir la carte, ni rendre illisibles les points à relier.
"""
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, GeneratedExercise,
    SchoolClass, Student, StudentLevel,
)
from app.services import answer_mix, grading as grader, manual_subject, pdfgen, sheet_engine
from app.services.pdfgen import COL_W

PACK_ENGINE = Path(__file__).resolve().parents[2] / "frontend/src/pages/subjects/packEngine.ts"
GUIDE_CURVE = Path(__file__).resolve().parents[2] / "frontend/src/pages/subjects/guideCurve.ts"
TIERS = [1, 2, 3, 11, 12, 13]


def _cap(b: int) -> float:
    return pdfgen.column_capacity(b)


def _lower_read_after(sequence: list[int], tiers: list[int]) -> list[int]:
    """Pour chaque carte lue : nombre de cartes de palier inférieur lues APRÈS elle."""
    return [sum(tiers[later] < tiers[card] for later in sequence[k + 1:])
            for k, card in enumerate(sequence)]


def _random_cards(rnd: random.Random, n: int, top: float = 330.0) -> tuple[list[float], list[int]]:
    return ([round(rnd.uniform(40, top), 1) for _ in range(n)],
            [rnd.choice(TIERS) for _ in range(n)])


# ------------------------------------------------------------------ la place

def test_pack_best_never_overfills_a_column_and_respects_the_pages():
    rnd = random.Random(11)
    for _ in range(150):
        pages = rnd.randint(1, 4)
        heights, tiers = _random_cards(rnd, rnd.randint(1, 26))
        columns = sheet_engine.pack_best(heights, tiers, pages)
        if columns is None:
            continue
        assert len(columns) <= 2 * pages
        assert sorted(i for col in columns for i in col) == list(range(len(heights)))
        for b, col in enumerate(columns):
            assert sum(heights[i] for i in col) <= _cap(b) + 1e-6


def test_recto_and_verso_have_their_own_column_height():
    # deux de ces cartes tiennent dans une colonne du recto, pas dans celle du verso
    assert _cap(0) > _cap(2)
    half = _cap(0) / 2 - 0.1
    assert 2 * half > _cap(2)
    assert sheet_engine.pack_best([half] * 4, [1] * 4, 1) is not None
    assert sheet_engine.pack_best([half] * 5, [1] * 5, 1) is None
    # 2 + 2 au recto, puis 1 + 1 au verso
    assert sheet_engine.pack_best([half] * 6, [1] * 6, 2) is not None
    assert sheet_engine.pack_best([half] * 7, [1] * 7, 2) is None


def test_a_card_taller_than_a_full_column_never_fits():
    assert sheet_engine.pack_best([_cap(2) + 1], [1], 6) is None


def test_slack_packing_rescues_a_selection_that_first_fit_decreasing_loses():
    # FFD pose 300 puis 250 ensemble, et laisse perdre la place qu'exigent les autres
    first, second = _cap(0), _cap(1)
    heights = [first * 0.5, first * 0.3, first * 0.3, first * 0.4, second * 0.5]
    tiers = [1] * 5
    columns = sheet_engine.pack_best(heights, tiers, 1)
    assert columns is not None and len(columns) == 2


def test_tolerance_lets_one_harder_card_fill_a_hole():
    # le dernier « facile » est trop haut pour le reste de la colonne : une carte
    # moyenne comble le trou, et se lit donc une carte plus tôt que son niveau
    cap = _cap(0)
    heights = [cap * 0.8, cap * 0.8, cap * 0.2, _cap(2) * 0.9, _cap(2) * 0.9]
    tiers = [1, 1, 2, 2, 2]
    columns = sheet_engine._pack_by_slack(heights, tiers, 4)
    assert columns is not None
    assert 2 in columns[0]
    sequence = [i for col in columns for i in sorted(col, key=lambda i: tiers[i])]
    assert max(_lower_read_after(sequence, tiers)) <= 1


def test_tolerance_never_brings_a_problem_before_an_exercise():
    cap = _cap(0)
    heights = [cap * 0.8, cap * 0.8, cap * 0.2]
    tiers = [3, 3, 11]
    columns = sheet_engine._pack_by_slack(heights, tiers, 4)
    assert 2 not in columns[0]


def _run_in_node(script: str, payload) -> list:
    """Exécute `script` (qui lit le JSON de process.argv[2]) et renvoie le JSON affiché."""
    if shutil.which("node") is None:
        pytest.skip("node absent")
    version = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout
    major, minor = (int(x) for x in re.match(r"v(\d+)\.(\d+)", version).groups())
    if (major, minor) < (22, 6):
        pytest.skip("node trop ancien pour exécuter du TypeScript")
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "payload.json"
        data.write_text(json.dumps(payload))
        module = Path(tmp) / "run.mts"
        module.write_text(script)
        done = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(module), str(data)],
            capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_browser_guide_curve_is_the_servers():
    from app.services import student_history
    script = f"""
      import {{ DEFAULT_GUIDE_REACH, guideRatio }} from {json.dumps(str(GUIDE_CURVE))};
      import {{ readFileSync }} from 'node:fs';
      const reaches = JSON.parse(readFileSync(process.argv[2], 'utf8'));
      console.log(JSON.stringify({{ fallback: DEFAULT_GUIDE_REACH, curves: reaches.map((r) =>
        Array.from({{ length: 10 }}, (_, i) => guideRatio(i + 1, r))) }}));
    """
    reaches = list(range(0, 101))
    browser = _run_in_node(script, reaches)
    assert browser["fallback"] == student_history.DEFAULT_GUIDE_REACH
    for reach, curve in zip(reaches, browser["curves"]):
        assert curve == pytest.approx(
            [student_history.guide_ratio(lvl, reach) for lvl in range(1, 11)])


def test_the_browser_engine_gives_the_same_verdict_as_the_server():
    rnd = random.Random(5)
    vectors = []
    for _ in range(400):
        pages = rnd.randint(1, 4)
        # de nombreuses cartes : c'est quand la feuille est pleine que le moteur travaille
        heights, tiers = _random_cards(rnd, rnd.randint(1, 8 * pages + 6))
        vectors.append({"heights": heights, "tiers": tiers, "pages": pages})
    caps = {"first": _cap(0), "rest": _cap(2)}
    script = f"""
      import {{ packBest }} from {json.dumps(str(PACK_ENGINE))};
      import {{ readFileSync }} from 'node:fs';
      const vectors = JSON.parse(readFileSync(process.argv[2], 'utf8'));
      const caps = {json.dumps(caps)};
      console.log(JSON.stringify(vectors.map((v) => packBest(
        v.heights.map((height, i) => ({{ height, tier: v.tiers[i] }})), caps, v.pages))));
    """
    browser = _run_in_node(script, vectors)
    fits = 0
    for vector, result in zip(vectors, browser):
        expected = sheet_engine.pack_best(vector["heights"], vector["tiers"], vector["pages"])
        assert result == expected, vector
        fits += expected is not None
    assert 0 < fits < len(vectors)


# ------------------------------------------------------------------ l'ordre

def test_each_student_reads_easy_to_hard_within_one_card_and_problems_last():
    rnd = random.Random(23)
    seen_inversion = False
    for _ in range(120):
        pages = rnd.randint(1, 3)
        heights, tiers = _random_cards(rnd, rnd.randint(2, 7 * pages), top=250)
        if sheet_engine.pack_best(heights, tiers, pages) is None:
            continue
        columns = sheet_engine.mix_columns(heights, tiers, pages, random.Random(rnd.random()))
        assert columns is not None and len(columns) <= 2 * pages
        for b, col in enumerate(columns):
            assert sum(heights[i] for i in col) <= _cap(b) + 1e-6
        sequence = [i for col in columns for i in col]
        assert sorted(sequence) == list(range(len(heights)))
        behind = _lower_read_after(sequence, tiers)
        assert max(behind) <= 1, (sequence, [tiers[i] for i in sequence])
        seen_inversion = seen_inversion or max(behind) == 1
        read = [tiers[i] for i in sequence]
        problems = [k for k, t in enumerate(read) if t >= sheet_engine.PROBLEM_TIER]
        if problems:
            assert problems == list(range(problems[0], len(read)))
    assert seen_inversion


def test_a_nearly_full_sheet_still_gives_at_least_two_layouts():
    # recto rempli à ras bord : tout tirage libre échoue, mais l'ordre change encore
    cap = _cap(0)
    heights = [cap / 3 - 0.1] * 6
    tiers = [1, 1, 1, 2, 2, 2]
    layouts = {json.dumps(sheet_engine.mix_columns(heights, tiers, 1, random.Random(seed)))
               for seed in range(30)}
    assert len(layouts) >= 2


def test_real_sheet_filled_to_the_limit_varies_between_students():
    rnd = random.Random(2)
    heights, tiers = [], []
    while True:
        h, t = round(rnd.uniform(70, 170), 1), rnd.choice([1, 2, 3])
        if sheet_engine.pack_best(heights + [h], tiers + [t], 2) is None:
            break
        heights.append(h)
        tiers.append(t)
    fill = sum(heights) / sum(_cap(b) for b in range(4))
    assert fill > 0.9
    layouts = {json.dumps(sheet_engine.mix_columns(heights, tiers, 2, random.Random(s)))
               for s in range(30)}
    assert len(layouts) >= 2


# ------------------------------------------------------------------ les champs

def _qcm_shape(choices, correct, statement="Choisis.", **grading):
    return {"response_type": "qcm_single", "statement": statement, "choices": choices,
            "grading": {"comparator": "qcm", "choices": choices, **grading},
            "expected": {"type": "choice", "correct": correct}}


def test_qcm_order_changes_but_the_right_answer_stays_the_same():
    choices = ["$12$", "$15$", "$18$", "$21$"]
    shape = _qcm_shape(choices, [2], option_ids=["a", "b", "c", "d"])
    orders = set()
    for seed in range(40):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        got = mixed["grading"]["choices"]
        assert mixed["choices"] == got and sorted(got) == sorted(choices)
        assert [got[i] for i in mixed["expected"]["correct"]] == ["$18$"]
        assert got[mixed["grading"]["option_ids"].index("c")] == "$18$"
        orders.add(tuple(got))
    assert len(orders) > 5
    # la carte de la banque n'est jamais modifiée
    assert shape["grading"]["choices"] == choices and shape["expected"]["correct"] == [2]


@pytest.mark.parametrize("choices", [
    ["a", "b", "b et c"], ["a. 4", "b. 5", "c. 6"],
    ["3", "4", "Aucune de ces réponses"], ["3", "4", "Toutes les réponses"],
])
def test_a_choice_that_points_at_another_keeps_the_order(choices):
    shape = _qcm_shape(choices, [0])
    for seed in range(10):
        assert answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)["grading"]["choices"] == choices


def test_a_statement_naming_a_choice_keeps_the_order():
    choices = ["3", "4", "5"]
    shape = _qcm_shape(choices, [0], statement="La réponse B est-elle juste ?")
    assert answer_mix.mix_shape(shape, random.Random(1), COL_W, 9)["grading"]["choices"] == choices


def test_grid_rows_move_with_their_answer_and_message():
    rows = [{"label": f"Affirmation {k}", "correct": k % 2} for k in range(5)]
    shape = {"response_type": "checkbox_grid", "statement": "Vrai ou faux ?",
             "grading": {"comparator": "grid", "cols": ["Vrai", "Faux"], "rows": rows,
                         "option_ids": [f"o{k}" for k in range(5)]},
             "expected": {"type": "grid", "cols": ["Vrai", "Faux"], "rows": rows}}
    moved = False
    for seed in range(10):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        got = mixed["grading"]["rows"]
        assert mixed["expected"]["rows"] == got
        for row, oid in zip(got, mixed["grading"]["option_ids"]):
            k = int(row["label"].split()[-1])
            assert row["correct"] == k % 2 and oid == f"o{k}"
        moved = moved or got != rows
    assert moved


def _table_shape(labels=None, feedback=None):
    cells = [[{"type": "integer", "value": k * 10}] for k in range(4)]
    grading = {"comparator": "table_cells", "cells": cells, "col_labels": ["Résultat"],
               "row_labels": labels}
    if feedback:
        grading["feedback"] = feedback
    return {"response_type": "table_fill", "statement": "Complète.", "grading": grading,
            "expected": {"type": "table", "rows": 4, "cols": 1, "cells": cells}}


def test_table_rows_move_with_their_answer_and_their_error_message():
    labels = [f"Calcul {name}" for name in "wxyz"]
    feedback = {"wrong_answers": [{"value": 99, "text": "Erreur", "cell": [2, 0]}]}
    shape = _table_shape(labels, feedback)
    moved = False
    for seed in range(12):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        g = mixed["grading"]
        assert mixed["expected"]["cells"] == g["cells"]
        for label, row in zip(g["row_labels"], g["cells"]):
            assert row[0]["value"] == labels.index(label) * 10
        target = g["feedback"]["wrong_answers"][0]["cell"]
        assert g["row_labels"][target[0]] == "Calcul y" and target[1] == 0
        moved = moved or g["row_labels"] != labels
    assert moved


@pytest.mark.parametrize("labels", [
    ["a. Premier", "b. Deuxième", "c. Troisième", "d. Quatrième"],
    ["Étape 1", "Étape 2", "Étape 3", "Étape 4"],
    ["Ana", "Bo", "Cid", "Total"],
    ["1", "2", "3", "4"],
    None,
])
def test_table_rows_that_follow_a_sequence_keep_their_order(labels):
    shape = _table_shape(labels)
    for seed in range(8):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        assert mixed["grading"]["cells"] == shape["grading"]["cells"]


@pytest.mark.parametrize("slot,columns,labels,values", [
    ("s16", ["Tomates", "Pommes", "Carottes"], ["Masse (kg)", "Prix (€)"],
     [[(2.5, True), (1.5, True), (3, True)], [(6, False), (4.8, False), (3.75, False)]]),
    ("s20", ["Cour", "Potager", "Pelouse", "Jardin entier"],
     ["Grand côté (m)", "Petit côté (m)", "Aire ($\\text{m}^2$)"],
     [[(7.5, True), (8.5, False), (8.5, False), (12, True)],
      [(3.5, True), (2.5, True), (5, False), (7.5, True)],
      [(26.25, False), (21.25, False), (42.5, False), (90, False)]]),
])
def test_a3_3_tables_mix_columns_without_moving_measurement_rows(slot, columns, labels, values):
    # Structure des exercices fr-6e-A3.3-20261104-220849/s16 et /s20.
    cells = [[{"type": "decimal", "value": value, **({"given": True} if given else {})}
              for value, given in row] for row in values]
    wrong = [{"cell": [r, c], "value": "99", "text": f"Erreur {slot} {r},{c}"}
             for r, row in enumerate(cells) for c, cell in enumerate(row) if not cell.get("given")]
    shape = {"response_type": "table_fill", "statement": "Complète le tableau.",
             "grading": {"comparator": "table_cells", "max_score": len(wrong), "cells": cells,
                         "row_labels": labels, "col_labels": columns, "mix": "columns",
                         "feedback": {"wrong_answers": wrong}},
             "expected": {"type": "table", "rows": len(cells), "cols": len(columns), "cells": cells}}
    original = deepcopy(shape)
    orders = set()
    base_height = pdfgen.answer_zone_height("table_fill", shape["grading"], shape["expected"], COL_W, 9)
    for seed in range(40):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        g, e = mixed["grading"], mixed["expected"]
        assert g["row_labels"] == labels
        assert sorted(g["col_labels"]) == sorted(columns)
        assert g["cells"] == e["cells"]
        assert (e["rows"], e["cols"]) == (len(cells), len(columns))
        for col, label in enumerate(g["col_labels"]):
            old_col = columns.index(label)
            assert [row[col] for row in g["cells"]] == [row[old_col] for row in cells]
        for before, after in zip(wrong, g["feedback"]["wrong_answers"]):
            old_r, old_c = before["cell"]
            new_r, new_c = after["cell"]
            assert new_r == old_r and g["col_labels"][new_c] == columns[old_c]
            assert after["text"] == before["text"] and after["value"] == before["value"]
        answers = [str(cell["value"]) for row in g["cells"] for cell in row if not cell.get("given")]
        verdict = grader.grade(e, g, "", 1.0, cell_texts=answers)
        assert verdict["score"] == g["max_score"]
        assert pdfgen.answer_zone_height("table_fill", g, e, COL_W, 9) <= base_height
        orders.add(tuple(g["col_labels"]))
    assert len(orders) > 1
    assert shape == original


def test_single_row_table_can_mix_named_columns():
    cells = [[{"type": "integer", "value": n} for n in range(3)]]
    shape = {"response_type": "table_fill", "statement": "Complète.",
             "grading": {"cells": cells, "col_labels": ["Cour", "Potager", "Pelouse"], "row_labels": ["Aire"]},
             "expected": {"cells": cells}}
    mixed = answer_mix.mix_shape(shape, random.Random(1), COL_W, 9)
    assert mixed["grading"]["col_labels"] != shape["grading"]["col_labels"]
    assert mixed["grading"]["row_labels"] == ["Aire"]


def test_ordered_table_columns_never_fall_back_to_moving_measurement_rows():
    cells = [[{"type": "integer", "value": r + c} for c in range(3)] for r in range(2)]
    for columns in (["1", "2", "3"], ["Étape 1", "Étape 2", "Étape 3"], ["Cour", "Potager", "Total"]):
        shape = {"response_type": "table_fill", "statement": "Complète.",
                 "grading": {"cells": cells, "col_labels": columns, "row_labels": ["Longueur", "Aire"],
                             "mix": "columns"},
                 "expected": {"cells": cells}}
        for seed in range(8):
            mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
            assert mixed["grading"] == shape["grading"]
            assert mixed["expected"] == shape["expected"]


def _two_axes_table(**declared):
    cells = [[{"type": "integer", "value": 10 * r + c} for c in range(3)] for r in range(4)]
    grading = {"cells": cells, "col_labels": ["Valeur", "Plus petite", "Plus grande"],
               "row_labels": ["E1", "E2", "E3", "E4"], **declared}
    return {"response_type": "table_fill", "statement": "Complète.", "grading": grading,
            "expected": {"cells": cells}}


def test_two_axes_table_never_mixes_without_a_declaration():
    for declared in ({}, {"mix": "none"}):
        shape = _two_axes_table(**declared)
        for seed in range(8):
            assert answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)["grading"] == shape["grading"]


def test_two_axes_table_mixes_only_the_declared_axis():
    rows = _two_axes_table(mix="rows")
    orders = set()
    for seed in range(8):
        mixed = answer_mix.mix_shape(rows, random.Random(seed), COL_W, 9)
        assert mixed["grading"]["col_labels"] == rows["grading"]["col_labels"]
        orders.add(tuple(mixed["grading"]["row_labels"]))
        # chaque équation garde ses réponses, dans le même ordre de colonnes
        for label, line in zip(mixed["grading"]["row_labels"], mixed["expected"]["cells"]):
            r = int(label[1]) - 1
            assert [c["value"] for c in line] == [10 * r, 10 * r + 1, 10 * r + 2]
    assert len(orders) > 1

    columns = _two_axes_table(mix="columns")
    orders = set()
    for seed in range(8):
        mixed = answer_mix.mix_shape(columns, random.Random(seed), COL_W, 9)
        assert mixed["grading"]["row_labels"] == columns["grading"]["row_labels"]
        orders.add(tuple(mixed["grading"]["col_labels"]))
    assert len(orders) > 1


def _matching_shape():
    left = ["$2+2$", "$3+3$", "$4+4$", "$5+5$", "$6+6$"]
    right = ["$4$", "$6$", "$8$", "$10$", "$12$", "$14$"]
    pairs = [[k, k] for k in range(5)]
    grading = {"comparator": "matching", "left": left, "right": right, "pairs": pairs,
               "right_ids": [f"r{k}" for k in range(6)], "scoring": "per_point"}
    expected = {"type": "matching", "left": left, "right": right, "pairs": pairs}
    return {"response_type": "matching", "statement": "Relie.", "grading": grading,
            "expected": expected}


def test_matching_keeps_every_link_and_never_worsens_legibility():
    shape = _matching_shape()
    grading, expected = shape["grading"], shape["expected"]
    left, right = grading["left"], grading["right"]
    geo = pdfgen._matching_layout(left, right, 9, COL_W)
    links = [(a, b) for a, b in expected["pairs"]]
    base = pdfgen._matching_order_defects(geo, 5, 6, links, tuple(range(6)))
    base_height = pdfgen.answer_zone_height("matching", grading, expected, COL_W, 9)
    orders = set()
    for seed in range(25):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)
        g, e = mixed["grading"], mixed["expected"]
        assert g["pairs"] == e["pairs"] and g["right"] == e["right"]
        assert {(left[a], g["right"][b]) for a, b in e["pairs"]} == {
            (left[k], right[k]) for k in range(5)}
        for k, oid in enumerate(g["right_ids"]):
            assert g["right"][k] == right[int(oid[1:])]
        order = tuple(right.index(r) for r in g["right"])
        flaws, straight = pdfgen._matching_order_defects(geo, 5, 6, links, order)
        assert flaws <= base[0] and straight <= max(1, base[1])
        assert pdfgen.answer_zone_height("matching", g, e, COL_W, 9) <= base_height
        orders.add(order)
    assert len(orders) > 3


def test_matching_avoids_three_lines_through_one_point():
    # 3 traits (0→2, 1→1, 2→0) concourants : tous les tirages acceptés l'évitent
    left, right, pairs = ["A", "B", "C"], ["1", "2", "3"], [[0, 0], [1, 1], [2, 2]]
    geo = pdfgen._matching_layout(left, right, 9, COL_W)
    links = [(0, 0), (1, 1), (2, 2)]
    assert pdfgen._matching_order_defects(geo, 3, 3, links, (2, 1, 0))[0] > 0
    for seed in range(30):
        order = pdfgen.shuffled_legible_order(left, right, pairs, random.Random(seed), 9, COL_W)
        assert order != (2, 1, 0)


def test_composite_parts_keep_their_order_but_each_is_mixed():
    part = lambda k: {"response_type": "qcm_single", "statement": f"Question {k}",
                      "grading": {"comparator": "qcm", "choices": ["10", "20", "30", "40"]},
                      "expected": {"type": "choice", "correct": [k]}}
    shape = {"response_type": "composite", "statement": "Contexte",
             "grading": {"parts": [part(0), part(1), part(2)]}}
    seen = set()
    for seed in range(20):
        parts = answer_mix.mix_shape(shape, random.Random(seed), COL_W, 9)["grading"]["parts"]
        assert [p["statement"] for p in parts] == ["Question 0", "Question 1", "Question 2"]
        for k, p in enumerate(parts):
            assert [p["grading"]["choices"][i] for i in p["expected"]["correct"]] == [["10", "20", "30"][k]]
        seen.add(tuple(parts[0]["grading"]["choices"]))
    assert len(seen) > 3


# ------------------------------------------------------------------ de bout en bout

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: User(
        email="prof@test", password_hash="x", role="admin")
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


GUIDED = "Calcule.\n{{aide}} Pense à la priorité des opérations."


def _seed(db, levels=(2, 5, 9, 9), pages=2, guides=manual_subject.GUIDES_BY_LEVEL, n_qcm=6):
    cls = SchoolClass(name="3eB", grade_level="3e")
    db.add(cls)
    db.flush()
    fw = CompetencyFramework(grade_level="3e", name="Test")
    db.add(fw)
    db.flush()
    comp = Competency(framework_id=fw.id, code="C0", short_id="A1.0", label="Fractions",
                      order_index=0, chapter_code="A1", chapter_name="Chapitre A1",
                      domain_code="A", domain_name="Nombres")
    db.add(comp)
    db.flush()
    rows = []
    for k in range(n_qcm):
        choices = [f"${k}{d}$" for d in range(4)]
        rows.append(GeneratedExercise(
            competency_id=comp.id, difficulty_level=k % 3 + 1, statement=GUIDED,
            response_type="qcm_single", source="indigo", kind="application", status="active",
            expected_json={"type": "choice", "correct": [1]},
            grading_json={"comparator": "qcm", "choices": choices, "exclusive": True,
                          "max_score": 1, "bareme_points": 1}))
    rows.append(GeneratedExercise(
        competency_id=comp.id, difficulty_level=1, statement="Problème final.",
        response_type="short_text", source="indigo", kind="probleme", status="active",
        expected_json={"value": 1},
        grading_json={"comparator": "numeric", "max_score": 1, "bareme_points": 2}))
    db.add_all(rows)
    students = [Student(class_id=cls.id, name=f"Élève {k}", llm_pseudonym=f"E{k}")
                for k in range(len(levels))]
    db.add_all(students)
    db.flush()
    db.add_all(StudentLevel(student_id=st.id, level=lv) for st, lv in zip(students, levels))
    a = Assessment(class_id=cls.id, type="training", title="Sujet", pages_target=pages,
                   framework_id=fw.id,
                   blueprint_json={
                       "mode": "manual", "composition": manual_subject.COMPOSITION_ENGINE,
                       "guides": guides, "competency_ids": [comp.id],
                       "variant_kind": "none",
                       "variants": [{"key": "A", "label": "Sujet unique", "items": [
                           {"exercise_id": r.id, "page": 0, "col": 0, "rank": i}
                           for i, r in enumerate(rows)]}]})
    db.add(a)
    db.commit()
    return a, rows, students


def _generate(db, a, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return manual_subject.generate_manual_job(db, a, None, 9)


def test_every_copy_has_the_same_cards_in_its_own_order(db, tmp_path, monkeypatch):
    a, rows, students = _seed(db, levels=(5,) * 12)
    report = _generate(db, a, tmp_path, monkeypatch)
    assert report["copies"] == 12 and not report["warnings"]
    orders, answer_orders = set(), set()
    tier_of = {r.id: pdfgen.reading_tier(r.difficulty_level, r.kind == "probleme") for r in rows}
    for copy in db.query(Copy).filter_by(assessment_id=a.id).all():
        assert copy.total_pages == 2
        items = (db.query(CopyItem).filter_by(copy_id=copy.id)
                 .order_by(CopyItem.sequence).all())
        ids = [it.generated_exercise_id for it in items]
        assert sorted(ids) == sorted(r.id for r in rows)
        assert [it.sequence for it in items] == list(range(1, len(rows) + 1))
        tiers = [tier_of[i] for i in ids]
        assert tiers[-1] >= sheet_engine.PROBLEM_TIER
        assert max(_lower_read_after(list(range(len(tiers))), tiers)) <= 1
        orders.add(tuple(ids))
        for item in items:
            if item.response_type.startswith("qcm"):
                bank = next(r for r in rows if r.id == item.generated_exercise_id)
                choices = item.grading_json["choices"]
                assert sorted(choices) == sorted(bank.grading_json["choices"])
                # la réponse attendue désigne le même texte que dans la banque
                assert [choices[i] for i in item.expected_json["correct"]] == [
                    bank.grading_json["choices"][1]]
                answer_orders.add(tuple(choices))
    assert len(orders) >= 2
    assert len(answer_orders) > 6


def test_a_student_keeps_the_same_copy_when_the_subject_is_generated_again(db, tmp_path, monkeypatch):
    a, _rows, students = _seed(db, levels=(5, 5))

    def copies():
        out = {}
        for copy in db.query(Copy).filter_by(assessment_id=a.id).all():
            items = db.query(CopyItem).filter_by(copy_id=copy.id).order_by(CopyItem.sequence).all()
            out[copy.student_id] = [(it.generated_exercise_id, it.grading_json.get("choices"))
                                    for it in items]
        return out

    _generate(db, a, tmp_path, monkeypatch)
    first = copies()
    for copy in db.query(Copy).filter_by(assessment_id=a.id).all():
        db.query(CopyItem).filter_by(copy_id=copy.id).delete()
        db.delete(copy)
    db.commit()
    _generate(db, a, tmp_path, monkeypatch)
    assert copies() == first


def test_guides_follow_each_students_level_with_the_same_sheet(db, tmp_path, monkeypatch):
    a, rows, students = _seed(db, levels=(2, 10))
    captured = []
    render_copy = pdfgen.render_copy

    def spy(*args, **kwargs):
        captured.append((kwargs["student_name"], [it.get("guides") for it in kwargs["items"]],
                         kwargs["placement"]))
        return render_copy(*args, **kwargs)

    monkeypatch.setattr(pdfgen, "render_copy", spy)
    report = _generate(db, a, tmp_path, monkeypatch)
    assert not report["warnings"]
    guides = dict((name, modes) for name, modes, _ in captured)
    # le problème final n'a pas de guide : seul le statut des QCM compte
    assert guides["Élève 0"].count(pdfgen.GUIDES_INCLUDE) == 6
    assert guides["Élève 1"].count(pdfgen.GUIDES_INCLUDE) == 0


def test_guides_can_go_to_every_student_of_a_composed_subject(db, tmp_path, monkeypatch):
    a, _rows, _students = _seed(db, levels=(2, 10), guides=pdfgen.GUIDES_INCLUDE)
    captured = {}
    render_copy = pdfgen.render_copy

    def spy(*args, **kwargs):
        captured[kwargs["student_name"]] = [it["guides"] for it in kwargs["items"]]
        return render_copy(*args, **kwargs)

    monkeypatch.setattr(pdfgen, "render_copy", spy)
    _generate(db, a, tmp_path, monkeypatch)
    for modes in captured.values():
        assert set(modes) == {pdfgen.GUIDES_INCLUDE}


def test_a_dyslexic_copy_is_packed_with_its_own_taller_cards(db, tmp_path, monkeypatch):
    # sur la vraie banque, la police adaptée agrandit certaines cartes : on force l'écart
    # pour qu'une feuille bien remplie en police standard ne tienne plus telle quelle
    real_height = pdfgen.estimate_item_height
    monkeypatch.setattr(pdfgen, "estimate_item_height", lambda *args: real_height(*args) * (
        1.25 if pdfgen._USE_DYSLEXIC.get() else 1.0))
    a, rows, students = _seed(db, levels=(5, 5), pages=2, guides=pdfgen.GUIDES_NONE, n_qcm=60)
    students[1].dyslexic = True
    db.commit()
    measures = manual_subject._CardMeasures(manual_subject.doc_templates(db), pdfgen.GUIDES_NONE)
    capacity = sum(_cap(b) for b in range(4))
    chosen, used = [], 0.0
    for row in rows:
        if row.kind != "probleme" and used + measures.height(row) <= 0.78 * capacity:
            chosen.append(row)
            used += measures.height(row)
    adapted = {r.id: measures.height(r, True) for r in chosen}
    assert sum(adapted.values()) > 0.9 * capacity
    variant = a.blueprint_json["variants"][0]
    a.blueprint_json = {**a.blueprint_json, "variants": [{**variant, "items": [
        {"exercise_id": r.id, "page": 0, "col": 0, "rank": i} for i, r in enumerate(chosen)]}]}
    db.commit()

    captured = {}
    render_copy = pdfgen.render_copy

    def spy(*args, **kwargs):
        captured[kwargs["student_name"]] = (kwargs["placement"], [it["item_id"] for it in kwargs["items"]])
        return render_copy(*args, **kwargs)

    monkeypatch.setattr(pdfgen, "render_copy", spy)
    _generate(db, a, tmp_path, monkeypatch)
    placement, item_ids = captured[students[1].name]
    per_column = {}
    for slot, item_id in zip(placement, item_ids):
        exercise_id = db.get(CopyItem, item_id).generated_exercise_id
        per_column[slot] = per_column.get(slot, 0.0) + adapted[exercise_id]
    for (page, col), height in per_column.items():
        assert height <= _cap(2 * page + col) + 1e-6


def test_a_composite_guided_only_in_its_parts_counts_as_guided(db):
    from app.services import generation
    row = GeneratedExercise(statement="Contexte", response_type="composite", expected_json={
        "parts": [{"response_type": "short_text", "statement": "{{aide}} Aide.\nQuestion"}]},
        grading_json={})
    assert generation.card_has_guides(row)


def _draft(db, a):
    draft = Assessment(class_id=a.class_id, type="training", title="Brouillon",
                       pages_target=a.pages_target, framework_id=a.framework_id)
    db.add(draft)
    db.commit()
    return draft


def test_plan_endpoint_saves_a_composed_subject(client, db):
    a, rows, _students = _seed(db)
    draft = _draft(db, a)
    r = client.post(f"/api/assessments/{draft.id}/manual-plan", json={
        "competency_ids": a.blueprint_json["competency_ids"], "composition": "engine",
        "guides": "auto", "guides_reach_pct": 30, "variant_kind": "none",
        "variants": [{"key": "A", "items": [{"exercise_id": r.id} for r in rows]}]})
    assert r.status_code == 200, r.text
    db.refresh(draft)
    bp = draft.blueprint_json
    assert (bp["composition"], bp["guides"], bp["guides_reach_pct"]) == ("engine", "auto", 30)


def test_plan_endpoint_refuses_a_selection_that_does_not_fit(client, db):
    a, rows, _students = _seed(db, pages=1, n_qcm=90)
    draft = _draft(db, a)
    r = client.post(f"/api/assessments/{draft.id}/manual-plan", json={
        "competency_ids": a.blueprint_json["competency_ids"], "composition": "engine",
        "guides": "auto", "variant_kind": "none",
        "variants": [{"key": "A", "items": [{"exercise_id": r.id} for r in rows]}]})
    assert r.status_code == 422
    assert "ne tiennent pas" in r.json()["detail"]


def test_pool_gives_what_the_picker_needs(client, db):
    a, rows, _students = _seed(db)
    comp_id = a.blueprint_json["competency_ids"][0]
    body = client.get(f"/api/assessments/manual/pool?competency_ids={comp_id}&pages=2").json()
    assert body["metrics"]["column_h_rest"] == pytest.approx(_cap(2))
    card = body["exercises"][0]
    assert card["estimated_seconds"] > 0
    assert card["height_pt"] >= card["height_pt_no_guide"]
    assert body["problems"][0]["tier"] >= sheet_engine.PROBLEM_TIER


def test_time_estimate_prefers_the_authors_value_and_grows_with_the_work():
    author = GeneratedExercise(statement="x", response_type="qcm_single", estimated_seconds=70)
    assert manual_subject.estimated_seconds(author) == 70
    qcm = GeneratedExercise(statement="x", response_type="qcm_single", difficulty_level=1,
                            grading_json={"choices": ["1", "2"]}, expected_json={})
    harder = GeneratedExercise(statement="x", response_type="qcm_single", difficulty_level=3,
                               grading_json={"choices": ["1", "2"]}, expected_json={})
    composite = GeneratedExercise(statement="x", response_type="composite", difficulty_level=1,
                                  grading_json={}, expected_json={"parts": [
                                      {"response_type": "qcm_single", "statement": "a", "grading": {}},
                                      {"response_type": "qcm_single", "statement": "b", "grading": {}}]})
    assert 30 <= manual_subject.estimated_seconds(qcm) < manual_subject.estimated_seconds(harder)
    assert manual_subject.estimated_seconds(composite) > manual_subject.estimated_seconds(qcm)

"""Familles de la forge : une carte écrite une fois, des jumelles justes par
construction. Les dispositions sont celles de l'auteur ; aucun script ne mélange."""
import json
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import familles  # noqa: E402
import forge  # noqa: E402

from app.services import answer_mix  # noqa: E402
from test_forge import db  # noqa: E402,F401  (base de test partagée)

QCM = {
    "response_type": "qcm_single",
    "statement": "Calcule $<<a>> + <<b>> \\times <<c>>$.",
    "estimated_seconds": 40, "micro_skill": "Priorité de la multiplication sur l'addition",
    "family_id": "calcul-priorite", "figure": None,
    "leak_keys": ["valeur:<<a+b*c|calc>>"], "gives": [],
    "choices": [
        {"id": "a", "text": "$<<a+b*c>>$", "correct": True},
        {"id": "b", "text": "$<<(a+b)*c>>$", "correct": False, "calc": "(<<a>>+<<b>>)*<<c>>",
         "error": "math.calc.priorite_operations",
         "feedback": "Tu as additionné d'abord : la multiplication passe avant l'addition."},
        {"id": "c", "text": "$<<a+b+c>>$", "correct": False, "calc": "<<a>>+<<b>>+<<c>>",
         "error": "math.calc.operation_inversee",
         "feedback": "Tu as additionné $<<b>>$ et $<<c>>$ : le signe $\\times$ demande de les multiplier."}],
    "answer_expr": "<<a>>+<<b>>*<<c>>",
    "dispositions": [["a", "b", "c"], ["c", "a", "b"]],
    "blank_feedback": "Commence par la multiplication, puis ajoute $<<a>>$.",
    "fallback_feedback": "Calcule d'abord $<<b>> \\times <<c>>$, puis ajoute $<<a>>$.",
    "guides": [{"text": "Repère l'opération à effectuer en premier, avant de regarder les propositions.",
                "position": "apres_question"}]}
JEUX = [{"a": 5, "b": 2, "c": 6}, {"a": 4, "b": 3, "c": 7}, {"a": 8, "b": 2, "c": 9}]


def _family(**changes) -> dict:
    card = json.loads(json.dumps(QCM))
    card.update(changes.pop("carte", {}))
    return {"slot": "s01", "jeux": changes.pop("jeux", JEUX), "carte": card, **changes}


def test_a_family_gives_every_set_of_values_in_both_orders():
    cards, problems = familles.twins(_family())
    assert problems == []
    assert sorted(cards) == ["s01", "s01.j1d2", "s01.j2d1", "s01.j2d2", "s01.j3d1", "s01.j3d2"]
    first, second = cards["s01"], cards["s01.j1d2"]
    assert first["statement"] == "Calcule $5 + 2 \\times 6$."
    assert [c["text"] for c in first["choices"]] == ["$17$", "$42$", "$13$"]
    assert first["choices"][1]["calc"] == "(5+2)*6" and first["answer_expr"] == "5+2*6"
    # même carte, autre ordre écrit par l'auteur : chaque réponse garde son message
    assert [c["id"] for c in second["choices"]] == ["c", "a", "b"]
    assert second["choices"][0]["feedback"].startswith("Tu as additionné $2$ et $6$")
    assert cards["s01.j2d1"]["statement"] == "Calcule $4 + 3 \\times 7$."
    assert "dispositions" not in first and first["twin"]["jeux"] == 3


@pytest.mark.parametrize("text,jeu,expected", [
    ("$<<a/b>>$", {"a": 5, "b": 2}, "$2{,}5$"),
    ("Il paie <<a/b>> €.", {"a": 5, "b": 2}, "Il paie 2,5 €."),
    ("$<<a/b>>$", {"a": 1, "b": 3}, "$\\dfrac{1}{3}$"),
    ("$<<a/b|frac>>$", {"a": 5, "b": 2}, "$\\dfrac{5}{2}$"),
    ("<<a/b|dec2>>", {"a": 2, "b": 3}, "0,67"),
    ("Le point $<<P>>$", {"P": "M"}, "Le point $M$"),
    ("$<<a*k>>$ cm", {"a": 4, "k": "1,5"}, "$6$ cm"),
])
def test_values_are_written_the_french_way(text, jeu, expected):
    assert familles.fill(text, jeu) == expected


def test_a_whole_field_becomes_a_typed_value_and_calculations_stay_ascii():
    card = {"answer": {"type": "integer", "value": "<<a*b>>"},
            "frac": {"value": "<<a/3|rat>>"}, "calc": "<<a>>/<<b>>",
            "figure": {"kind": "tikz", "spec": {"tikz": "\\draw (0,0)--(<<a/b>>,0);"}}}
    out = familles.fill(card, {"a": 5, "b": 2})
    assert out["answer"]["value"] == 10 and out["frac"]["value"] == [5, 3]
    assert out["calc"] == "5/2" and out["figure"]["spec"]["tikz"] == "\\draw (0,0)--(2.5,0);"


@pytest.mark.parametrize("change,fragment", [
    ({"jeux": JEUX[:1]}, "2 ou 3 jeux"),
    ({"jeux": [JEUX[0], {"a": 4, "b": 3}]}, "mêmes noms"),
    ({"jeux": [JEUX[0], {"a": 4, "b": 3, "c": "2,5"}]}, "difficulté inégale"),
    ({"jeux": [JEUX[0], {"a": 4, "b": 3, "c": 700}]}, "change trop de taille"),
    ({"jeux": [JEUX[0], {"a": 5, "b": 6, "c": 2}]}, "même réponse"),
    ({"carte": {"dispositions": [["a", "b", "c"], ["a", "c", "b"]]}}, "même place"),
    ({"carte": {"dispositions": [["a", "b", "c"], ["a", "b"]]}}, "exactement"),
    ({"carte": {"dispositions": None}}, "ordre_impose"),
    ({"carte": {"statement": "Calcule $<<a>> + <<z>>$."}}, "n'est pas une valeur du jeu"),
])
def test_a_faulty_family_gives_no_twin(change, fragment):
    cards, problems = familles.twins(_family(**change))
    assert cards == {} and any(fragment in p for p in problems), problems


def test_an_imposed_order_keeps_one_disposition():
    cards, problems = familles.twins(_family(carte={"dispositions": None,
                                                    "ordre_impose": "valeurs croissantes"}))
    assert problems == [] and sorted(cards) == ["s01", "s01.j2d1", "s01.j3d1"]


def test_table_columns_move_and_rows_never_do():
    leaf = {"response_type": "table_fill", "col_labels": ["A", "B", "C"], "row_labels": ["x", "y"],
            "cells": [[{"value": 1}, {"value": 2}, {"value": 3}], [{"value": 4}, {"value": 5}, {"value": 6}]],
            "wrong_answers": [{"cell": [1, 2], "value": "9", "feedback": "…"}],
            "dispositions": [[0, 1, 2], [2, 0, 1]]}
    assert familles.disposition_problems(leaf) == []
    moved = familles.arrange(leaf, 2)
    assert moved["col_labels"] == ["C", "A", "B"] and moved["row_labels"] == ["x", "y"]
    assert [c["value"] for c in moved["cells"][1]] == [6, 4, 5]
    assert moved["wrong_answers"][0]["cell"] == [1, 0] and moved["mix"] == "none"


def test_the_author_order_is_never_mixed_at_print_time():
    choices = ["$17$", "$42$", "$13$"]
    shape = {"response_type": "qcm_single", "statement": "Calcule.", "choices": choices,
             "grading": {"choices": choices, "option_ids": ["a", "b", "c"], "author_order": True},
             "expected": {"correct": [0]}}
    for seed in range(10):
        assert answer_mix.mix_shape(shape, random.Random(seed), 250, 9) == shape


def test_check_validates_every_twin_and_a_faulty_family_blocks_its_slot(db):  # noqa: F811
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="test-model")[0]
    (run / "familles").mkdir()
    (run / "familles" / "s01.json").write_text(json.dumps(_family()), encoding="utf-8")
    report, contracts = forge.check(db, run)
    assert not [e for e in report.errors if e.startswith("s01")], report.errors
    assert {k for k in contracts if k.startswith("s01")} == {
        "s01", "s01.j1d2", "s01.j2d1", "s01.j2d2", "s01.j3d1", "s01.j3d2"}
    assert "s01" not in [s["slot"] for s in forge.remaining_slots(run)]
    # un distracteur faux dans la famille : toutes ses jumelles tombent
    bad = _family()
    bad["carte"]["choices"][1]["text"] = "$<<(a+b)*c+1>>$"
    (run / "familles" / "s01.json").write_text(json.dumps(bad), encoding="utf-8")
    report, contracts = forge.check(db, run)
    assert any(e.startswith("s01") for e in report.errors)
    assert not [k for k in contracts if k.startswith("s01")]
    # une famille mal formée : aucune carte écrite, l'erreur nomme l'emplacement
    (run / "familles" / "s01.json").write_text(json.dumps(_family(jeux=JEUX[:1])), encoding="utf-8")
    report, _ = forge.check(db, run)
    assert any(e.startswith("s01 : famille") for e in report.errors)
    assert not (run / "slots" / "s01.json").exists() and not list((run / "jumelles").glob("s01*"))


def test_twins_are_rendered_and_saved_as_one_group(db):  # noqa: F811
    from app.models import IndigoExercise
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="test-model")[0]
    grid = [s for s in forge.load_json(run / "grid.json") if s["slot"] == "s01"]
    forge.dump_json(run / "grid.json", grid)
    (run / "familles").mkdir()
    (run / "familles" / "s01.json").write_text(json.dumps(_family()), encoding="utf-8")
    summary = forge.render(db, run)
    assert summary["complete"] and not summary["geometry"]
    assert len(list((run / "cards").glob("s01*.png"))) == 6
    forge.persist(db, run)
    rows = db.query(IndigoExercise).all()
    assert len(rows) == 6
    twins = [r.grading_json["twin"] for r in rows]
    assert {t["group"] for t in twins} == {f"{run.name}/s01"}
    assert sorted((t["jeu"], t["disposition"]) for t in twins) == [(j, d) for j in (1, 2, 3) for d in (1, 2)]
    assert sum(t["principal"] for t in twins) == 1
    assert all(r.grading_json["author_order"] for r in rows)


def test_a_verdict_and_the_bank_treat_the_family_as_one_card(db):  # noqa: F811
    from app.models import GeneratedExercise, IndigoExercise
    from app.services import indigo
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="test-model")[0]
    forge.dump_json(run / "grid.json", [s for s in forge.load_json(run / "grid.json") if s["slot"] == "s01"])
    (run / "familles").mkdir()
    (run / "familles" / "s01.json").write_text(json.dumps(_family()), encoding="utf-8")
    forge.render(db, run)
    forge.persist(db, run)
    rows = db.query(IndigoExercise).all()
    head = next(r for r in rows if r.grading_json["twin"]["principal"])
    indigo.set_review_verdict(db, head, "dislike", "valeurs trop grandes")
    assert all((r.raw_ocr_json or {}).get("review_verdict") == "dislike" for r in db.query(IndigoExercise).all())
    indigo.set_review_verdict(db, head, None)
    # en banque : une seule carte active, ses jumelles en réserve pour l'impression
    data = {"exercises": []}
    for r in db.query(IndigoExercise).all():
        rec = {"id": r.id, "competency_code": "Z1.1", "grade_level": "3e", "statement": r.statement,
               "response_type": r.response_type, "expected": r.expected_json, "grading": r.grading_json,
               "source": "forge", "difficulty": 1}
        assert indigo._seed_record(db, data, rec, Path("."))
    db.flush()
    bank = db.query(GeneratedExercise).all()
    assert sorted(b.status for b in bank) == ["active"] + ["twin"] * 5
    assert {b.twin_group for b in bank} == {f"{run.name}/s01"}

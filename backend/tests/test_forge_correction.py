"""Forge côté correction : barème des pièges, formes de réponse, micro-corrections,
journal d'erreurs, tirage, préférences, alertes de banque."""
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.db import Base  # noqa: E402
from app.models import (  # noqa: E402
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, ExercisePreference, GeneratedExercise,
    GradingDecision, SchoolClass, SchoolYear, Student, StudentLearningEvent, StudentResponse, TeacherProfile,
)
from app.services import (  # noqa: E402
    bank_health, draw_rules, error_journal, grading, microcorrection, pdfgen, student_history,
)
from app.services import scoring  # noqa: E402

FEEDBACK = {
    "lines": 2,
    "correct": {"text": "Oui, c'est juste."},
    "blank": {"text": "Commence par la multiplication."},
    "fallback": {"text": "Reprends le calcul étape par étape."},
    "options": {"b": {"text": "Tu as additionné d'abord.", "error": "math.calc.priorite_operations"},
                "c": {"text": "Tu as tout additionné.", "error": "math.calc.operation_inversee"},
                "d": {"text": "Regarde bien le signe.", "error": "math.calc.signe"}},
}


def _item(rtype="qcm_multiple", *, correct=(0,), choices=4, level=2, **extra):
    grading_json = {"feedback": FEEDBACK, "choices": [str(i) for i in range(choices)],
                    "option_ids": ["a", "b", "c", "d"][:choices],
                    "exclusive": rtype == "qcm_single", **extra}
    return SimpleNamespace(response_type=rtype, grading_json=grading_json,
                           expected_json={"correct": list(correct)}, difficulty=level * 3)


def _decision(score=0.0, max_score=4.0, reason="qcm_partial", evidence=None):
    return SimpleNamespace(score=score, max_score=max_score, reason_code=reason, evidence_json=evidence or {})


def _response(choices=None, pairs=None, text=""):
    return SimpleNamespace(selected_choices=choices, selected_pairs=pairs, final_text=text)


# ------------------------------------------------------------- relier avec pièges
MATCHING = {"comparator": "matching", "scoring": "per_point", "right": list("abcdef"), "max_score": 3}
PAIRS = {"type": "matching", "pairs": [[0, 0], [1, 1], [2, 2]]}


@pytest.mark.parametrize("links, score", [
    ([[0, 0], [1, 1], [2, 2]], 6), ([[0, 0], [1, 1], [2, 3]], 4), ([[0, 0]], 4),
    ([[0, 3], [1, 4], [2, 5]], 0), ([], 0)])
def test_each_right_point_is_one_decision(links, score):
    verdict = grading.grade(PAIRS, MATCHING, "", 0.99, selected_pairs=links)
    assert (verdict["score"], verdict["max_score"]) == (score, 6.0)


def test_no_link_is_blank_not_half_the_points():
    assert grading.grade(PAIRS, MATCHING, "", 0.99, selected_pairs=[])["reason_code"] == "matching_blank"


def test_old_matching_keeps_its_scoring():
    old = {"comparator": "matching", "right": ["a", "b"], "max_score": 2}
    verdict = grading.grade({"type": "matching", "pairs": [[0, 0], [1, 1]]}, old, "", 0.99,
                            selected_pairs=[[0, 0]])
    assert (verdict["score"], verdict["max_score"]) == (1.0, 2.0)


def test_link_to_a_trap_is_a_wrong_answer_not_a_bad_reading():
    from app.services.pipeline import _recognized_answer_issue
    item = SimpleNamespace(grading_json={"comparator": "matching", "left": ["x", "y", "z"], "right": list("abcdef")},
                           expected_json={"pairs": [[0, 0], [1, 1], [2, 2]]})
    def issue(pairs):
        return _recognized_answer_issue(item, selected=None, cell_texts=None, selected_pairs=pairs)
    assert issue([[0, 5]]) is None
    assert issue([[0, 6]]) == "matching_reading_mismatch"
    assert issue([[0, 1], [1, 1]]) == "matching_reading_mismatch"


def test_points_follow_the_shared_ratio():
    assert scoring.earned_points(4, 6, 3.0) == pytest.approx(2.0)


# ------------------------------------------------------------- formes de réponse
@pytest.mark.parametrize("form, text, expected", [
    ("irreducible", "3/4", True), ("irreducible", "6/8", False), ("irreducible", "\\dfrac{3}{4}", True),
    ("irreducible", "\\dfrac{6}{8}", False), ("irreducible", "0,75", False), ("irreducible", "2", True),
    ("decimal", "0,75", True), ("decimal", "3/4", False),
    ("factored", "2(x+3)", True), ("factored", "(x+1)(x-1)", True), ("factored", "(x+3)^2", True),
    ("factored", "2x+6", False),
    ("expanded", "2x+6", True), ("expanded", "6+2x", True), ("expanded", "2(x+3)", False),
    ("expanded", "(x+3)^2", False),
    ("scientific", "3,2\\times 10^{4}", True), ("scientific", "32000", False),
    ("scientific", "32\\times10^3", False),
    ("any", "n'importe quoi", True),
])
def test_form_is_read_as_written(form, text, expected):
    assert grading.form_matches(form, text) is expected


def test_wrong_form_counts_as_wrong_only_when_demanded():
    expected = {"type": "rational", "value": [3, 4]}
    asked = {"max_score": 2, "comparator": "rational_equiv", "answer_form": "irreducible"}
    free = {"max_score": 2, "comparator": "rational_equiv"}
    assert grading.grade(expected, asked, "3/4", 0.99)["score"] == 2
    verdict = grading.grade(expected, asked, "6/8", 0.99)
    assert (verdict["score"], verdict["reason_code"]) == (0.0, "form_mismatch")
    assert grading.grade(expected, free, "6/8", 0.99)["score"] == 2


def test_expression_form_counts_too():
    expected = {"type": "expression", "value": "2*(x+3)", "variable": "x"}
    policy = {"max_score": 2, "comparator": "symbolic_equiv", "answer_form": "factored"}
    assert grading.grade(expected, policy, "2(x+3)", 0.99)["score"] == 2
    assert grading.grade(expected, policy, "2x+6", 0.99)["reason_code"] == "form_mismatch"


def test_form_applies_cell_by_cell():
    cells = [[{"type": "rational", "value": [1, 2]}, {"type": "rational", "value": [3, 4]}]]
    policy = {"comparator": "table_cells", "cells": cells, "answer_form": "irreducible", "max_score": 2}
    assert grading.table_credits(policy, ["1/2", "6/8"]) == [1.0, 0.0]


# ------------------------------------------------------------- arrondi exigé
ROUNDED = {"type": "decimal", "value": 1.79}
ROUNDED_POLICY = {"max_score": 2, "comparator": "numeric", "answer_form": "exact_decimal"}


@pytest.mark.parametrize("written, score, reason", [
    ("1,79", 2, "numeric_match"),
    ("1,79 €", 2, "numeric_match"),
    ("1,790", 2, "numeric_match"),
    ("1,786 ≈ 1,79", 2, "numeric_match"),
    ("1,786 \\approx 1,79", 2, "numeric_match"),
    ("1,786", 0, "numeric_mismatch"),   # recopie : plus précise que l'attendu
    ("1,8", 0, "numeric_mismatch"),     # arrondi à un autre rang
    ("1,78", 0, "numeric_mismatch"),    # troncature
    ("2", 0, "numeric_mismatch"),
    ("179/100", 0, "form_mismatch"),    # une fraction n'est pas un arrondi écrit
])
def test_rounding_demanded_only_the_exact_value_scores(written, score, reason):
    verdict = grading.grade(ROUNDED, ROUNDED_POLICY, written, 0.99)
    assert (verdict["score"], verdict["reason_code"]) == (score, reason)


def test_rounding_tolerance_stays_when_not_demanded():
    """Hors arrondi exigé, la règle du 02/08 reste : demi-point, copie plus précise juste."""
    free = {"max_score": 2, "comparator": "numeric"}
    assert grading.grade(ROUNDED, free, "1,8", 0.99)["score"] == 1
    assert grading.grade(ROUNDED, free, "1,786", 0.99)["score"] == 2


def test_rounding_demanded_cell_by_cell():
    cells = [[{"type": "decimal", "value": 14.37}, {"type": "decimal", "value": 14.4}],
             [{"type": "decimal", "value": 6.0}, {"type": "integer", "value": 15}]]
    policy = {"comparator": "table_cells", "cells": cells, "answer_form": "exact_decimal", "max_score": 4}
    # 14,3 tronque 14,37 et 14,37 s'arrondit à 14,4 : sans l'exigence, ces cases auraient des points.
    assert grading.table_credits(policy, ["14,3", "14,37", "6", "15"]) == [0.0, 0.0, 1.0, 1.0]
    free = {**policy, "answer_form": "any"}
    assert grading.table_credits(free, ["14,3", "14,37", "6", "15"]) == [0.5, 1.0, 1.0, 1.0]


def test_rounding_demanded_message_is_the_planned_one():
    feedback = {"wrong_answers": [{"value": "1,786", "text": "Tu as recopié.", "error": "autre"}]}
    assert microcorrection._wrong_answer(feedback, "1,786 €")["text"] == "Tu as recopié."


# -------------------------------------------------------------- micro-corrections
def test_one_extra_check_is_named():
    item = _item(correct=(0,))
    selection = microcorrection.select(item, _decision(), _response([0, 1]))
    assert selection.kind == "count"
    assert selection.text.startswith("Tu as coché une case de trop.")
    assert ("comptage.trop_de_cases", None) in selection.errors
    assert ("math.calc.priorite_operations", "b") in selection.errors


def test_many_extra_checks_are_counted():
    item = _item(correct=(0,))
    text = microcorrection.select(item, _decision(), _response([0, 1, 2])).text
    assert text.startswith("Tu as coché 2 cases de trop.")


def test_missing_checks_are_counted():
    assert microcorrection.select(_item(correct=(0, 1, 2)), _decision(), _response([0])).text \
        .startswith("Il manque 2 cases.")


def test_wrong_option_gives_its_own_message():
    selection = microcorrection.select(_item("qcm_single", correct=(0,)), _decision(max_score=1, reason="qcm_mismatch"),
                                       _response([1]))
    assert (selection.text, selection.kind, selection.option_id) == ("Tu as additionné d'abord.", "option", "b")


def test_blank_has_priority_and_is_journaled():
    selection = microcorrection.select(_item(), _decision(reason="qcm_blank"), _response([]))
    assert selection.text == FEEDBACK["blank"]["text"]
    assert (microcorrection.NO_ANSWER, None) in selection.errors


@pytest.mark.parametrize("level, printed", [(1, ""), (2, "Oui, c'est juste."), (3, "Oui, c'est juste.")])
def test_right_answer_is_explained_from_medium_up(level, printed):
    selection = microcorrection.select(_item(level=level), _decision(score=4), _response([0]))
    assert selection.text == printed


def test_teacher_verdict_never_invents_an_error():
    selection = microcorrection.select(_item(), _decision(reason="teacher_wrong"), _response([1]))
    assert selection.text == FEEDBACK["fallback"]["text"]
    assert not selection.comparable and selection.errors == []


def test_cards_without_contract_print_nothing():
    item = SimpleNamespace(response_type="qcm_single", grading_json={}, expected_json={}, difficulty=3)
    assert microcorrection.select(item, _decision(), _response([1])) is None


def _matching_item():
    return SimpleNamespace(
        response_type="matching", difficulty=6,
        grading_json={"feedback": {**FEEDBACK, "options": {
            "d1": {"text": "ok", "error": "math.geo.vocabulaire"},
            "p1": {"text": "Ce mot ne convient pas.", "error": "math.geo.notation"}}},
            "right_ids": ["d1", "d2", "p1"]},
        expected_json={"pairs": [[0, 0], [1, 1]]})


@pytest.mark.parametrize("pairs, start", [
    ([[0, 0], [1, 1], [2, 2]], "Tu as tracé un lien de trop."),
    ([[0, 0]], "Il manque un lien."),
])
def test_links_are_counted(pairs, start):
    selection = microcorrection.select(_matching_item(), _decision(reason="matching_partial"),
                                       _response(pairs=pairs))
    assert selection.text.startswith(start)


def test_many_links_are_counted():
    assert microcorrection.extra_links_text(3) == "Tu as tracé 3 liens de trop."
    assert microcorrection.missing_links_text(2) == "Il manque 2 liens."


def test_trap_message_is_found_by_stable_id():
    selection = microcorrection.select(_matching_item(), _decision(reason="matching_partial"),
                                       _response(pairs=[[0, 0], [1, 2]]))
    assert selection.text == "Ce mot ne convient pas."
    assert ("math.geo.notation", "p1") in selection.errors


def _written_item():
    return SimpleNamespace(
        response_type="short_text", difficulty=6,
        grading_json={"comparator": "rational_equiv", "feedback": {
            **FEEDBACK, "options": {}, "wrong_answers": [
                {"value": "6/8", "text": "Pas irréductible.", "error": "math.frac.non_simplifiee"},
                {"value": "2/6", "text": "Mauvais dénominateur.", "error": "math.frac.addition_num_den"}]}},
        expected_json={"type": "rational", "value": [3, 4]})


def test_expected_wrong_answer_gets_its_message():
    selection = microcorrection.select(_written_item(), _decision(reason="form_mismatch"), _response(text="6/8"))
    assert selection.text == "Pas irréductible."


def test_unexpected_wrong_form_gets_the_form_message():
    item = _written_item()
    item.grading_json["feedback"]["wrong_answers"] = []
    selection = microcorrection.select(item, _decision(reason="form_mismatch"), _response(text="9/12"))
    assert selection.text == microcorrection.FORM_MISMATCH_TEXT


def _llm_item():
    feedback = {**FEEDBACK, "lines": 4, "options": {"e1": {"text": "Message prévu.", "error": "math.eq.transposition"}}}
    return SimpleNamespace(response_type="multiline_text", difficulty=9, expected_json={},
                           grading_json={"feedback": feedback})


def test_llm_choice_prints_the_planned_text_exactly():
    decision = _decision(reason="llm_partial", evidence={"llm": [{"message_id": "e1", "message_libre": "autre"}]})
    selection = microcorrection.select(_llm_item(), decision, _response(text="x"))
    assert selection.text == "Message prévu."
    assert ("math.eq.transposition", "e1") in selection.errors


def test_llm_free_text_is_used_only_if_it_fits():
    short = _decision(reason="llm_partial", evidence={"llm": [{"message_libre": "Tu as oublié la conclusion."}]})
    selection = microcorrection.select(_llm_item(), short, _response(text="x"))
    assert (selection.text, selection.kind) == ("Tu as oublié la conclusion.", "llm")
    assert ("autre", None) in selection.errors
    long = _decision(reason="llm_partial", evidence={"llm": [{"message_libre": "Très long. " * 80}]})
    assert microcorrection.select(_llm_item(), long, _response(text="x")).text == FEEDBACK["fallback"]["text"]


def test_group_text_keeps_order_and_stops_when_full():
    long_text = "Une explication assez longue pour remplir toute la largeur de la bande. " * 2
    joined = microcorrection.group_text(["Premier.", long_text, long_text, long_text, "Dernier."])
    assert joined.startswith("Premier.")
    assert "Dernier." not in joined


# ------------------------------------------------------------------------ overlay
def test_message_is_drawn_in_the_reserved_strip():
    item = {"statement": "Calcule.", "response_type": "qcm_single", "choices": ["1", "2"],
            "grading": {"choices": ["1", "2"], "feedback": {"lines": 2}}, "level3": 1}
    _layout, _fs, _h, strip = pdfgen._exercise_layout(item, 9, 9)
    c = canvas.Canvas(io.BytesIO())
    meta = pdfgen._strip_meta(c, 30, 100, pdfgen.COL_W, strip)
    zone = {"strip": meta, "feedback": "Tu as additionné avant de multiplier : la multiplication passe d'abord.",
            "score": 0, "max_score": 1}
    pdfgen._draw_correction_strip(c, zone, pdfgen.black)
    pdfgen._draw_correction_strip(c, {**zone, "dyslexic": True}, pdfgen.black)


def test_group_message_is_drawn_without_a_score():
    c = canvas.Canvas(io.BytesIO())
    strip = {"x_pt": 30, "y_pt": 100, "w_pt": 250, "h_pt": 30, "fs": 8}
    pdfgen._draw_correction_strip(c, {"group_strip": strip, "group_feedback": "a. Revois.", "x_pt": 30, "y_pt": 100,
                                      "w_pt": 250, "h_pt": 20, "score": 1, "max_score": 2}, pdfgen.black)


# ------------------------------------------------------------------------ journal
@pytest.fixture()
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _world(db):
    framework = CompetencyFramework(grade_level="3e", name="F", program_id="fr", subject_id="math", status="published")
    db.add(framework)
    db.flush()
    competency = Competency(framework_id=framework.id, code="A1.1", short_id="A1.1", label="Calcul",
                            chapter_code="A1", chapter_name="Ch", domain_code="A", domain_name="D")
    db.add(competency)
    year = SchoolYear(label="2026-2027", active=True)
    db.add(year)
    db.flush()
    klass = SchoolClass(name="3A", grade_level="3e", school_year_id=year.id, program_id="fr")
    db.add(klass)
    db.flush()
    student = Student(name="Élève", class_id=klass.id, llm_pseudonym="p")
    db.add(student)
    db.flush()
    assessment = Assessment(class_id=klass.id, subject_id="math", framework_id=framework.id,
                            title="Sujet", type="control", created_by="prof")
    db.add(assessment)
    db.flush()
    return SimpleNamespace(framework=framework, competency=competency, klass=klass, student=student,
                           assessment=assessment)


def _copy(db, world, exercise_id="ex1", chosen=(1,), reason="qcm_mismatch", score=0.0, rtype="qcm_single",
          status="auto", when=None):
    copy = Copy(assessment_id=world.assessment.id, student_id=world.student.id, seed=1)
    db.add(copy)
    db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=1, generated_exercise_id=exercise_id,
                    difficulty=6, response_type=rtype, statement="s", correction="",
                    expected_json={"correct": [0]},
                    grading_json={"feedback": FEEDBACK, "option_ids": ["a", "b", "c", "d"], "exclusive": True,
                                  "choices": ["1", "2", "3", "4"]})
    db.add(item)
    db.flush()
    response = StudentResponse(copy_item_id=item.id, selected_choices=list(chosen))
    db.add(response)
    db.flush()
    decision = GradingDecision(response_id=response.id, score=score, max_score=1.0, reason_code=reason,
                               tier="A", source="deterministic", status=status, confidence=0.99)
    db.add(decision)
    db.flush()
    return SimpleNamespace(copy=copy, item=item, response=response, decision=decision)


def test_journal_records_errors_and_occasions(db):
    world = _world(db)
    entry = _copy(db, world)
    assert error_journal.sync_copy(db, entry.copy, world.assessment) == 3
    db.flush()
    rows = db.query(StudentLearningEvent).all()
    by_code = {r.error_code: r for r in rows}
    assert by_code["math.calc.priorite_operations"].observed is True
    assert by_code["math.calc.priorite_operations"].option_id == "b"
    assert by_code["math.calc.priorite_operations"].message == "Tu as additionné d'abord."
    # les pièges évités comptent aussi : c'est le dénominateur
    assert by_code["math.calc.signe"].observed is False
    assert {r.subject_id for r in rows} == {"math"}


def test_redoing_a_correction_creates_no_duplicate(db):
    world = _world(db)
    entry = _copy(db, world)
    error_journal.sync_copy(db, entry.copy, world.assessment)
    db.flush()
    assert error_journal.sync_copy(db, entry.copy, world.assessment) == 0


def test_changed_answer_adds_a_revision_and_keeps_history(db):
    world = _world(db)
    entry = _copy(db, world)
    error_journal.sync_copy(db, entry.copy, world.assessment)
    db.flush()
    entry.response.selected_choices = [2]
    assert error_journal.sync_copy(db, entry.copy, world.assessment) == 2
    db.flush()
    rows = db.query(StudentLearningEvent).filter_by(error_code="math.calc.priorite_operations").order_by(
        StudentLearningEvent.revision).all()
    assert [r.observed for r in rows] == [True, False]
    stats = {s.code: s for s in error_journal.error_stats(db, world.student.id)}
    assert stats["math.calc.priorite_operations"].occurrences == 0
    assert stats["math.calc.operation_inversee"].occurrences == 1


def test_pending_review_writes_nothing(db):
    world = _world(db)
    entry = _copy(db, world, status="review_pending")
    assert error_journal.sync_copy(db, entry.copy, world.assessment) == 0


def test_cancelled_question_and_withdrawal_leave_history_but_stop_counting(db):
    world = _world(db)
    entry = _copy(db, world)
    error_journal.sync_copy(db, entry.copy, world.assessment)
    db.flush()
    error_journal.withdraw_copy(db, entry.copy.id)
    db.flush()
    assert db.query(StudentLearningEvent).count() == 6
    assert error_journal.error_stats(db, world.student.id) == []


def test_one_error_alone_is_not_a_misconception(db):
    world = _world(db)
    error_journal.sync_copy(db, _copy(db, world, "ex1").copy, world.assessment)
    db.flush()
    assert error_journal.active_errors(db, world.student.id) == set()


def test_error_becomes_active_then_closes_after_two_avoided(db):
    world = _world(db)
    code = "math.calc.priorite_operations"
    error_journal.sync_copy(db, _copy(db, world, "ex1").copy, world.assessment)
    db.flush()
    error_journal.sync_copy(db, _copy(db, world, "ex2").copy, world.assessment)
    db.flush()
    assert code in error_journal.active_errors(db, world.student.id)
    assert not any(c.startswith("comptage.") for c in error_journal.active_errors(db, world.student.id))
    error_journal.sync_copy(db, _copy(db, world, "ex3", chosen=(2,)).copy, world.assessment)
    db.flush()
    assert code in error_journal.active_errors(db, world.student.id)
    error_journal.sync_copy(db, _copy(db, world, "ex4", chosen=(2,)).copy, world.assessment)
    db.flush()
    assert code not in error_journal.active_errors(db, world.student.id)


def test_subjects_are_kept_apart(db):
    world = _world(db)
    error_journal.sync_copy(db, _copy(db, world).copy, world.assessment)
    db.flush()
    assert error_journal.error_stats(db, world.student.id, "francais") == []
    assert error_journal.error_stats(db, world.student.id, "math")


# ------------------------------------------------------------------------- tirage
def _row(identifier, source="indigo", grading_json=None, level=1):
    return SimpleNamespace(id=identifier, source=source, grading_json=grading_json or {},
                           response_type="qcm_single", difficulty_level=level)


TARGETED = {"feedback": {"options": {"b": {"text": "x", "error": "math.calc.priorite_operations"}}}}


def test_order_is_remediation_then_like_then_forge():
    context = draw_rules.DrawContext(likes={"liked"}, active_errors={"math.calc.priorite_operations"},
                                     remediation_left=2)
    rows = [_row("plain"), _row("new", "forge"), _row("liked"), _row("fix", grading_json=TARGETED)]
    ordered = sorted(rows, key=lambda r: draw_rules.preference_key(r, context))
    assert [r.id for r in ordered] == ["fix", "liked", "new", "plain"]
    assert [r.id for r in draw_rules.preferred(rows, context)] == ["fix"]


def test_remediation_is_limited():
    context = draw_rules.DrawContext(active_errors={"math.calc.priorite_operations"}, remediation_left=0)
    assert not draw_rules.is_remediation(_row("fix", grading_json=TARGETED), context)
    assert draw_rules.DrawContext().for_student({"x"}).remediation_left == draw_rules.MAX_REMEDIATION_CARDS


def test_disliked_rows_are_never_allowed():
    context = draw_rules.DrawContext(dislikes={"no"})
    assert [r.id for r in draw_rules.allowed([_row("no"), _row("yes")], context)] == ["yes"]


def test_leak_between_cards_is_blocked():
    state = draw_rules.SheetState()
    state.add(_row("first", grading_json={"gives": ["formule:aire"], "leak_keys": ["valeur:12"], "family_id": "f"}))
    assert draw_rules.conflicts(_row("a", grading_json={"leak_keys": ["formule:aire"]}), state)
    assert draw_rules.conflicts(_row("b", grading_json={"gives": ["valeur:12"]}), state)
    assert draw_rules.conflicts(_row("c", grading_json={"family_id": "f"}), state)
    clean = _row("d", grading_json={"leak_keys": ["valeur:7"], "family_id": "g"})
    assert not draw_rules.conflicts(clean, state)
    assert draw_rules.without_conflicts([clean], state) == [clean]


def test_error_raises_competency_priority_without_changing_level_guards():
    stats = student_history.Stats(competency_id="c1", priority=0.3)
    assert student_history._level_for(student_history.Stats(competency_id="c", strength=0.1), 3) <= 2
    assert stats.priority + student_history.ERROR_PRIORITY_BOOST == pytest.approx(0.5)


# ------------------------------------------------------------------- préférences
def test_preferences_belong_to_the_teacher_of_the_subject(db):
    profile = TeacherProfile(name="Prof", user_id="teacher")
    db.add(profile)
    db.flush()
    db.add_all([ExercisePreference(user_id="teacher", exercise_id="a", value="like"),
                ExercisePreference(user_id="teacher", exercise_id="b", value="dislike"),
                ExercisePreference(user_id="other", exercise_id="c", value="dislike")])
    db.flush()
    context = draw_rules.load_context(db, SimpleNamespace(teacher_profile_id=profile.id, created_by="corrector"))
    assert (context.likes, context.dislikes) == ({"a"}, {"b"})
    assert draw_rules.preference_owner(db, SimpleNamespace(teacher_profile_id=None, created_by="author")) == "author"
    no_account = TeacherProfile(name="Sans compte")
    db.add(no_account)
    db.flush()
    assert draw_rules.preference_owner(
        db, SimpleNamespace(teacher_profile_id=no_account.id, created_by="corrector")) == "corrector"


# ------------------------------------------------------------------ alertes banque
def _bank_row(db, competency, identifier, level=1):
    db.add(GeneratedExercise(id=identifier, competency_id=competency.id, difficulty_level=level, statement="s",
                             response_type="qcm_single", source="forge", status="active"))


def test_exhausted_competency_is_red_with_a_command(db):
    world = _world(db)
    _bank_row(db, world.competency, "only")
    db.flush()
    _copy(db, world, "only")
    db.flush()
    alerts = bank_health.exhaustion(db)
    assert alerts[0]["severity"] == "rouge"
    assert alerts[0]["students_out"] == 1
    assert alerts[0]["command"].startswith("forge fr 3e A1.1 --complement ")
    assert "--facile" in alerts[0]["command"]


def test_healthy_stock_raises_no_alert(db):
    world = _world(db)
    for index in range(6):
        _bank_row(db, world.competency, f"e{index}")
    db.flush()
    assert bank_health.exhaustion(db) == []


def test_field_report_needs_enough_responses(db):
    world = _world(db)
    _bank_row(db, world.competency, "ex")
    db.flush()
    assert bank_health.field_report(db) == []


@pytest.mark.parametrize("level, stats, signal", [
    (1, {"success": 0.35}, "difficulte"), (3, {"success": 0.92}, "difficulte"),
    (2, {"blank": 0.4}, "non_reponse"), (2, {"teacher": 0.4}, "correction"),
    (2, {"discrimination": -0.2}, "discrimination"),
    (2, {"options": [{"text": "x", "correct": False, "share": 0}]}, "distracteur"),
    (2, {"success": 0.25, "options": [{"text": "x", "correct": False, "share": 0.6}]}, "distracteur"),
])
def test_field_signals(level, stats, signal):
    base = {"success": 0.6, "blank": 0.0, "teacher": 0.0, "discrimination": 0.3, "options": []}
    notes = bank_health._quality_notes(None, level, {**base, **stats})
    assert signal in {n["signal"] for n in notes}


# ------------------------------------------------------------------- barème Indigo
def test_rebareme_changes_only_future_points():
    sys.path.insert(0, str(REPO / "backend" / "scripts"))
    import apply_bareme
    single = apply_bareme.rebareme({"bareme_points": 1.0}, "qcm_multiple", 3, False)
    assert single["bareme_points"] == 3.0
    parts = [{"response_type": "qcm_single", "grading": {"bareme_points": 1}},
             {"response_type": "short_text", "grading": {"bareme_points": 9}}]
    composite = apply_bareme.rebareme({"parts": parts}, "composite", 2, True)
    assert composite["bareme_points"] == 3.5
    assert sum(p["grading"]["bareme_points"] for p in composite["parts"]) == 3.5




# ------------------------------------------------------------------------ ombre OCR
def test_shadow_is_off_by_default_and_changes_nothing(db, monkeypatch):
    from app.config import settings
    from app.models import OcrShadowReading
    from app.services import ocr_shadow, providers
    called = []
    monkeypatch.setattr(providers, "deepseek_json", lambda *a, **k: called.append(1) or {})
    item = SimpleNamespace(id="i", statement="s", grading_json={"route": "ocr_llm"})
    zone = SimpleNamespace(id="z")
    assert settings.ocr_llm_shadow is False
    assert ocr_shadow.record(db, item=item, zone=zone, image=b"x", ocr_text="t", ocr_confidence=0.2, threshold=0.9) is None
    assert called == []
    assert db.query(OcrShadowReading).count() == 0


def test_shadow_compares_to_the_teacher_and_requires_zero_wrong(db, monkeypatch):
    from app.config import settings
    from app.models import OcrAttempt, OcrShadowReading
    from app.services import ocr_shadow, providers
    monkeypatch.setattr(settings, "ocr_llm_shadow", True)
    monkeypatch.setattr(providers, "deepseek_json",
                        lambda *a, **k: {"transcription": "3/4", "confiance": 0.95, "illisible": False})
    item = SimpleNamespace(id="i", statement="s", grading_json={"route": "ocr_llm"})
    cv_item = SimpleNamespace(id="j", statement="s", grading_json={"route": "cv"})
    record = lambda it, conf: ocr_shadow.record(db, item=it, zone=SimpleNamespace(id="z"), image=b"x",
                                                ocr_text="t", ocr_confidence=conf, threshold=0.9)
    assert record(cv_item, 0.2) is None       # pas de texte à soumettre
    assert record(item, 0.95) is None         # lecture sûre : rien à faire
    assert record(item, 0.2) is not None
    db.flush()
    assert db.query(OcrShadowReading).count() == 1
    db.add(OcrAttempt(zone_id="z", provider="teacher_ocr", raw_json={}, text="3/4", latex="", confidence=1.0))
    db.flush()
    summary = ocr_shadow.report(db)
    assert (summary["compared"], summary["agree"], summary["wrong"]) == (1, 1, 0)
    assert summary["ready"] is False          # trop peu de copies pour décider
    db.query(OcrAttempt).filter_by(provider="teacher_ocr").first().text = "5/4"
    db.flush()
    assert ocr_shadow.report(db)["wrong"] == 1


# ---------------------------------------------------------------- feuille quadrillée
def test_grid_sheet_verdicts(tmp_path):
    sys.path.insert(0, str(REPO / "backend" / "scripts"))
    import geo_grid_test as grid
    path = tmp_path / "feuille.pdf"
    grid.make_sheet(str(path))
    frames = grid.measure_frames(grid._load(str(path)))
    # Le PDF lui-même est le cas idéal : tout le saumon disparaît, rien n'a été tracé.
    assert all(f["ink"] < grid.RESIDUE_OK for f in frames)
    good = [{"frame": 1, "empty": True, "ink": 0.0005}, {"frame": 2, "empty": False, "ink": 0.03}]
    grey = [{"frame": 1, "empty": True, "ink": 0.08}, {"frame": 2, "empty": False, "ink": 0.09}]
    assert grid.verdict(good)[0] == "OK"
    assert grid.verdict(grey)[0] == "ÉCHEC"


# -------------------------------------------------------------- préférences par l'API
@pytest.fixture()
def bank_api(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_db
    from app.deps import current_user
    from app.routers import content
    world = _world(db)
    _bank_row(db, world.competency, "ex1")
    db.commit()
    app = FastAPI()
    app.include_router(content.router)
    state = SimpleNamespace(user=SimpleNamespace(id="anne", role="teacher"))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: state.user
    return SimpleNamespace(client=TestClient(app), world=world, state=state)


def test_preference_stays_private_to_its_author(bank_api):
    client, competency = bank_api.client, bank_api.world.competency.id
    assert client.put("/api/content/exercises/ex1/preference", json={"value": "dislike"}).status_code == 200
    mine = client.get(f"/api/content/exercises?competency_id={competency}").json()
    assert mine[0]["preference"] == "dislike"
    bank_api.state.user = SimpleNamespace(id="bruno", role="teacher")
    theirs = client.get(f"/api/content/exercises?competency_id={competency}").json()
    assert theirs[0]["preference"] is None
    assert "dislike" not in str({k: v for k, v in theirs[0].items() if k != "preference"})
    assert client.get(f"/api/content/exercises?competency_id={competency}&preference=dislike").json() == []
    bank_api.state.user = SimpleNamespace(id="anne", role="teacher")
    assert client.delete("/api/content/exercises/ex1/preference").status_code == 200
    assert client.get(f"/api/content/exercises?competency_id={competency}").json()[0]["preference"] is None


def test_preference_on_unknown_exercise_is_refused(bank_api):
    assert bank_api.client.put("/api/content/exercises/nope/preference", json={"value": "like"}).status_code == 404
    assert bank_api.client.put("/api/content/exercises/ex1/preference", json={"value": "bof"}).status_code == 422


def test_bank_health_is_admin_only(bank_api):
    assert bank_api.client.get("/api/content/bank-health").status_code == 403


# ------------------------------------------------------- publication d'un exercice forge
def test_forge_exercise_survives_publication_with_its_provenance(db, tmp_path):
    from app.services import generation, indigo
    world = _world(db)
    record = {"id": "forge-1", "competency_code": "A1.1", "grade_level": "3e", "badge_type": "exercice",
              "difficulty": 2, "statement": "Calcule.", "response_type": "qcm_single",
              "expected": {"correct": [0]}, "grading": {"choices": ["1", "2"], "feedback": FEEDBACK,
                                                       "figure_layout": "grid_right", "route": "cv"},
              "source": "forge", "source_version": "forge-1.0", "estimated_seconds": 90,
              "quality": {"goldfish": {"flag": False}}, "title": ""}
    assert indigo._seed_record(db, {"version": 99}, record, tmp_path)
    db.flush()
    row = db.get(GeneratedExercise, "forge-1")
    assert (row.source, row.source_version, row.estimated_seconds) == ("forge", "forge-1.0", 90)
    assert row.competency_id == world.competency.id
    assert indigo.published_rows(db, world.competency, 2) == [row]
    shape = generation.render_shape(row)
    assert shape["figure_layout"] == "grid_right" and "calc" not in shape
    assert pdfgen.reserved_feedback_lines(shape) == 2


def test_problem_part_credits_its_own_competency(db):
    from app.services import generation
    world = _world(db)
    other = Competency(framework_id=world.framework.id, code="A1.2", short_id="A1.2", label="Autre",
                       chapter_code="A1", chapter_name="Ch", domain_code="A", domain_name="D")
    db.add(other)
    db.flush()
    row = SimpleNamespace(competency_id=world.competency.id)
    part = {"grading": {"competency_code": "A1.2"}}
    catalog = generation._part_catalog(db, row, part, "defaut")
    assert catalog != "defaut"
    assert generation._part_catalog(db, row, {"grading": {}}, "defaut") == "defaut"
    assert generation._part_catalog(db, row, {"grading": {"competency_code": "ZZ"}}, "defaut") == "defaut"


# ------------------------------------------------------------- overlay de bout en bout
def _overlay_entry(db, world, chosen=(1,), **kw):
    from app.models import DocumentPage, ResponseZone
    entry = _copy(db, world, chosen=chosen, **kw)
    page = DocumentPage(copy_id=entry.copy.id, page_no=1)
    db.add(page)
    db.flush()
    strip = {"x_pt": 30, "y_pt": 100, "w_pt": 250, "h_pt": 21, "fs": 8}
    zone = ResponseZone(item_id=entry.item.id, page_id=page.id, type="qcm_single", x_pt=30, y_pt=130, w_pt=260, h_pt=30,
                        meta_json={"correction_strip": strip,
                                   "boxes": [{"index": i, "x_pt": 40 + 20 * i, "y_pt": 140, "w_pt": 6, "h_pt": 6}
                                             for i in range(4)]})
    db.add(zone)
    db.flush()
    entry.response.zone_id = zone.id
    db.flush()
    return entry


def test_overlay_prints_the_message_of_the_chosen_answer(db):
    from app.services import pipeline
    world = _world(db)
    entry = _overlay_entry(db, world)
    pages = pipeline.copy_overlay_pages(db, world.assessment, entry.copy, {}, persist=False)
    zone = pages[0]["page_zones"][0]
    assert zone["feedback"].startswith("Tu as additionné d")
    assert zone["dyslexic"] is False
    assert zone["full_credit"] is False


def test_overlay_of_an_old_card_has_no_feedback_key(db):
    from app.services import pipeline
    world = _world(db)
    entry = _overlay_entry(db, world)
    entry.item.grading_json = {"choices": ["1", "2", "3", "4"], "exclusive": True}
    db.flush()
    pages = pipeline.copy_overlay_pages(db, world.assessment, entry.copy, {}, persist=False)
    assert "feedback" not in pages[0]["page_zones"][0]


def test_dyslexic_student_gets_the_flag_with_the_message(db):
    from app.services import pipeline
    world = _world(db)
    world.student.dyslexic = True
    entry = _overlay_entry(db, world)
    pages = pipeline.copy_overlay_pages(db, world.assessment, entry.copy, {}, persist=False)
    assert pages[0]["page_zones"][0]["dyslexic"] is True

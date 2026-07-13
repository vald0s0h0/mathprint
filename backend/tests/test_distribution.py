"""Tests unitaires de services.distribution (répartition automatique des
exercices : difficulté, mix homogène des types, plan post-correction)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import GeneratedExercise, Student
from app.services import distribution


def _row(kind: str, response_type: str = "short_text") -> GeneratedExercise:
    return GeneratedExercise(kind=kind, response_type=response_type, difficulty_level=3)


def test_difficulty_level5_neutral_for_common_modes():
    assert distribution.difficulty_level5("common", student_level_1_10=10) == \
        distribution.difficulty_level5("common", student_level_1_10=1)
    assert distribution.difficulty_level5("common_variants", student_level_1_10=10) == \
        distribution.difficulty_level5("common", student_level_1_10=1)


def test_difficulty_level5_individual_adapts_to_student():
    weak = distribution.difficulty_level5("individual", student_level_1_10=1)
    strong = distribution.difficulty_level5("individual", student_level_1_10=10)
    assert weak < strong


def test_variant_seed_common_is_identical_for_all_students():
    seeds = {distribution.variant_seed(1000, "common", i) for i in range(20)}
    assert seeds == {1000}


def test_variant_seed_common_variants_caps_at_three():
    seeds = {distribution.variant_seed(1000, "common_variants", i) for i in range(20)}
    assert len(seeds) == 3


def test_variant_seed_individual_is_unique_per_student():
    seeds = [distribution.variant_seed(1000, "individual", i) for i in range(20)]
    assert len(set(seeds)) == 20


def test_pick_balanced_exercise_favors_underrepresented_kind():
    rows = [_row("application"), _row("application"), _row("probleme")]
    target_mix = {"application": 0.55, "probleme": 0.35, "qcm": 0.10}
    counts = {"application": 10, "probleme": 0, "qcm": 0}
    picked = distribution.pick_balanced_exercise(rows, counts, target_mix, seed=0)
    assert distribution.exercise_bucket(picked) == "probleme"


def test_pick_balanced_exercise_qcm_bucket_from_response_type():
    row = _row("application", response_type="qcm_single")
    assert distribution.exercise_bucket(row) == "qcm"


def test_pick_balanced_exercise_empty_bank_raises():
    try:
        distribution.pick_balanced_exercise([], {}, {}, 0)
        assert False, "devait lever ValueError"
    except ValueError:
        pass


def test_apply_next_plan_ignored_when_absent():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    mix, level = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert mix == {"application": 1.0} and level == 3


def test_apply_next_plan_used_when_recent():
    student = Student(
        next_plan_json={"kind_mix": {"qcm": 1.0}, "difficulty_level": 5},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=1))
    mix, level = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert mix == {"qcm": 1.0} and level == 5


def test_apply_next_plan_ignored_when_stale():
    student = Student(
        next_plan_json={"kind_mix": {"qcm": 1.0}, "difficulty_level": 5},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=200))
    mix, level = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert mix == {"application": 1.0} and level == 3


def test_lesson_review_targets_never_in_control():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    targets = distribution.lesson_review_targets(
        ["a", "b"], student, [], level=2, assessment_type="control")
    assert targets == []


def test_lesson_review_targets_uses_fresh_plan_first():
    student = Student(
        next_plan_json={"lesson_competency_ids": ["b", "z"]},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=1))
    due = [{"competency_id": "a", "mastery": 0.1}]  # aurait matché le repli
    targets = distribution.lesson_review_targets(
        ["a", "b", "c"], student, due, level=8, assessment_type="training")
    # "z" n'est pas parmi les compétences du sujet -> filtré ; le repli sur
    # `due` n'est pas utilisé puisque le plan a fourni au moins une cible valide
    assert targets == ["b"]


def test_lesson_review_targets_ignored_when_plan_stale():
    student = Student(
        next_plan_json={"lesson_competency_ids": ["a"]},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=200))
    due = [{"competency_id": "b", "mastery": 0.1}]
    targets = distribution.lesson_review_targets(
        ["a", "b"], student, due, level=8, assessment_type="training")
    assert targets == ["b"]


def test_lesson_review_targets_falls_back_to_weak_mastery():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    due = [{"competency_id": "a", "mastery": 0.9},   # solide : simplement due, pas une lacune
           {"competency_id": "b", "mastery": 0.2}]   # lacune réelle
    targets = distribution.lesson_review_targets(
        ["a", "b"], student, due, level=8, assessment_type="training")
    assert targets == ["b"]


def test_lesson_review_targets_caps_at_max_lessons_per_copy():
    student = Student(
        next_plan_json={"lesson_competency_ids": ["a", "b", "c"]},
        next_plan_updated_at=datetime.now(timezone.utc))
    targets = distribution.lesson_review_targets(
        ["a", "b", "c"], student, [], level=8, assessment_type="training")
    assert len(targets) <= 2


def test_lesson_review_targets_fragile_student_safety_net():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    targets = distribution.lesson_review_targets(
        ["a", "b"], student, [], level=3, assessment_type="training")
    assert targets == ["a"]


def test_lesson_review_targets_no_plan_no_due_strong_student():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    targets = distribution.lesson_review_targets(
        ["a", "b"], student, [], level=8, assessment_type="training")
    assert targets == []

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


def _statement_row(statement: str, value: int, row_id: str = "") -> GeneratedExercise:
    # id explicite : le défaut SQLAlchemy n'est appliqué qu'à l'insertion
    return GeneratedExercise(id=row_id or f"row-{id(statement)}-{value}",
                             kind="application", response_type="short_text",
                             difficulty_level=3, statement=statement,
                             expected_json={"type": "integer", "value": value},
                             grading_json={"max_score": 1, "comparator": "numeric"})


def test_exercise_identity_is_content_based_not_row_id():
    # Deux LIGNES distinctes (banques de deux compétences voisines) portant le
    # MÊME exercice : pour l'élève c'est un doublon, même si le dédoublonnage
    # de la banque — par compétence — les a légitimement laissées passer.
    a = _statement_row("Calcule $7 \\times 8$.", 56, row_id="ligne-competence-A1.1")
    b = _statement_row("Calcule $7 \\times 8$.", 56, row_id="ligne-competence-A1.2")
    assert a.id != b.id
    assert distribution.exercise_identity(a) == distribution.exercise_identity(b)
    other = _statement_row("Calcule $9 \\times 8$.", 72)
    assert distribution.exercise_identity(other) != distribution.exercise_identity(a)


def test_pick_balanced_exercise_excludes_same_exercise_from_another_competency():
    served = _statement_row("Calcule $7 \\times 8$.", 56)
    twin = _statement_row("Calcule $7 \\times 8$.", 56)      # autre ligne, même contenu
    fresh = _statement_row("Calcule $9 \\times 8$.", 72)
    picked = distribution.pick_balanced_exercise(
        [twin, fresh], {}, {"application": 1.0}, seed=0,
        exclude_keys={distribution.exercise_identity(served)})
    assert picked is fresh


def test_pick_balanced_exercise_falls_back_when_everything_excluded():
    # Filet de sécurité : mieux vaut répéter un exercice que ne rien imprimer.
    row = _statement_row("Calcule $7 \\times 8$.", 56)
    picked = distribution.pick_balanced_exercise(
        [row], {}, {"application": 1.0}, seed=0,
        exclude_keys={distribution.exercise_identity(row)})
    assert picked is row


def test_apply_next_plan_ignored_when_absent():
    student = Student(next_plan_json=None, next_plan_updated_at=None)
    mix, level = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert mix == {"application": 1.0} and level == 3


def test_apply_next_plan_used_when_recent():
    student = Student(
        next_plan_json={"difficulty_level": 5},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=1))
    mix, level = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert level == 5                      # la difficulté, elle, reste personnalisée
    assert mix == {"application": 1.0}


def test_apply_next_plan_never_overrides_the_correction_load_mix():
    # Le mix qcm/manuscrit répartit la charge de correction entre CV (gratuit)
    # et OCR Mathpix (payant, sous quota) : c'est une contrainte globale, pas
    # une préférence à personnaliser. Un vieux plan qui en porte encore un
    # (champ retiré du prompt, mais des plans stockés en contiennent) ne doit
    # PLUS l'imposer.
    student = Student(
        next_plan_json={"kind_mix": {"qcm": 1.0}, "difficulty_level": 5},
        next_plan_updated_at=datetime.now(timezone.utc) - timedelta(days=1))
    mix, _ = distribution.apply_next_plan(student, {"application": 1.0}, 3)
    assert mix == {"application": 1.0}


def test_apply_next_plan_ignored_when_stale():
    student = Student(
        next_plan_json={"difficulty_level": 5},
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

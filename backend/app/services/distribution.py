"""Répartition automatique des exercices par élève (étape Exercices de
l'assistant sujet) : priorité selon la courbe de l'oubli, difficulté selon
le mode d'adaptation, mix homogène des types de réponses au sein d'une
copie. Remplace les anciennes heuristiques fixes (suggestion 60/30/10,
remplissage par répétition sans diversité) — la sélection concrète des
exercices en banque reste celle de exercise_gen (ensure_bank/bank_rows_near_level),
jamais réinventée ici.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import GeneratedExercise, Student, StudentCompetencyState
from . import exercise_gen, forgetting


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def priority_competencies(db: Session, student_id: str, competency_ids: list[str]) -> list[str]:
    """Trie les compétences cochées par urgence décroissante (probabilité de
    rappel croissante) ; une compétence jamais évaluée revient en tête
    (recall_probability = 0, cf. forgetting.recall_probability)."""
    if not competency_ids:
        return []
    states = {
        s.competency_id: s
        for s in db.query(StudentCompetencyState).filter(
            StudentCompetencyState.student_id == student_id,
            StudentCompetencyState.competency_id.in_(competency_ids)).all()
    }

    def urgency(cid: str) -> float:
        state = states.get(cid)
        return forgetting.recall_probability(state) if state else 0.0

    return sorted(competency_ids, key=urgency)


def difficulty_level5(personalization_mode: str, student_level_1_10: int) -> int:
    """Niveau de banque (1-5) : neutre pour commun/variantes communes,
    adapté au niveau élève (±2 autour d'une base neutre) en individuel."""
    base = 5
    if personalization_mode == "individual":
        delta = max(-2, min(2, student_level_1_10 - 5))
        base = max(1, min(10, base + delta))
    return exercise_gen.student_level_to_difficulty(base)


def variant_seed(base_seed: int, personalization_mode: str, student_index: int) -> int:
    """Seed de copie : commune à toute la classe, plafonnée à 3 variantes
    (anti-copie) pour "commun avec variantes", unique par élève sinon."""
    if personalization_mode == "common":
        return base_seed
    if personalization_mode == "common_variants":
        return base_seed + (student_index % 3) + 1
    return base_seed + student_index + 1


def exercise_bucket(row: GeneratedExercise) -> str:
    if row.response_type.startswith("qcm"):
        return "qcm"
    return row.kind or "application"


def pick_balanced_exercise(rows: list[GeneratedExercise], counts: dict[str, int],
                           target_mix: dict[str, float], seed: int) -> GeneratedExercise:
    """Choisit, dans une banque déjà chargée pour une compétence × niveau,
    l'exercice du type le moins représenté par rapport au mix cible de la
    copie en cours ; sélection déterministe (seed) à l'intérieur du type
    retenu. Le compteur `counts` est mis à jour — à décrémenter par
    l'appelant (via exercise_bucket) si l'item est finalement retiré (ex.
    dépassement de la capacité de page)."""
    if not rows:
        raise ValueError("banque vide")
    by_bucket: dict[str, list[GeneratedExercise]] = {}
    for r in rows:
        by_bucket.setdefault(exercise_bucket(r), []).append(r)
    total = sum(counts.values()) + 1

    def deficit(bucket: str) -> float:
        return target_mix.get(bucket, 0.0) - counts.get(bucket, 0) / total

    bucket = max(by_bucket, key=deficit)
    candidates = by_bucket[bucket]
    row = candidates[seed % len(candidates)]
    counts[bucket] = counts.get(bucket, 0) + 1
    return row


def apply_next_plan(student: Student, target_mix: dict[str, float],
                    level5: int) -> tuple[dict[str, float], int]:
    """En mode "individuel", affine (sans jamais remplacer) le mix de types
    et la difficulté à partir du plan post-correction stocké pour l'élève
    (cf. services.appreciation) — évite un second appel LLM à la création du
    sujet. Ignoré si absent ou plus vieux que settings.next_plan_max_age_days ;
    le périmètre de compétences coché par le professeur n'est jamais modifié
    par cette fonction."""
    plan = student.next_plan_json
    if not plan or not student.next_plan_updated_at:
        return target_mix, level5
    age_days = (datetime.now(timezone.utc) - _utc(student.next_plan_updated_at)).days
    if age_days > settings.next_plan_max_age_days:
        return target_mix, level5
    mix = plan.get("kind_mix") or target_mix
    plan_level = plan.get("difficulty_level")
    level = plan_level if isinstance(plan_level, int) and 1 <= plan_level <= 5 else level5
    return mix, level

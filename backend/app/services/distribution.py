"""Répartition automatique des exercices par élève (étape Exercices de
l'assistant sujet) : priorité selon la courbe de l'oubli, difficulté selon
le mode d'adaptation, mix homogène des types de réponses au sein d'une
copie. Remplace les anciennes heuristiques fixes (suggestion 60/30/10,
remplissage par répétition sans diversité) — la sélection concrète des
exercices en banque reste celle de exercise_gen (ensure_bank/bank_rows_near_level),
jamais réinventée ici.

Inclut aussi `lesson_review_targets` : quelles compétences doivent recevoir
un rappel de leçon dans la copie d'un élève, pilotée par le même plan
post-correction (lacunes/courbe d'oubli) que apply_next_plan — cf.
services.generation pour l'insertion effective des rappels.
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


def exercise_identity(row: GeneratedExercise) -> str:
    """Identité d'un exercice pour l'anti-répétition DANS une copie.

    L'id de ligne ne suffit pas : la banque est constituée par compétence, et
    deux compétences voisines peuvent parfaitement avoir produit le MÊME
    exercice chacune de son côté (le dédoublonnage d'exercise_gen est, lui,
    par compétence — deux lignes distinctes, légitimes). Pour l'élève, c'est
    pourtant deux fois le même exercice sur sa copie. On compare donc le
    contenu, avec la même clé que la banque (_dedup_key)."""
    return exercise_gen._dedup_key(row.statement, row.expected_json,
                                   (row.grading_json or {}).get("choices"))


def pick_balanced_exercise(rows: list[GeneratedExercise], counts: dict[str, int],
                           target_mix: dict[str, float], seed: int,
                           exclude_keys: set[str] | None = None) -> GeneratedExercise:
    """Choisit, dans une banque déjà chargée pour une compétence × niveau,
    l'exercice du type le moins représenté par rapport au mix cible de la
    copie en cours ; sélection déterministe (seed) à l'intérieur du type
    retenu. Le compteur `counts` est mis à jour — à décrémenter par
    l'appelant (via exercise_bucket) si l'item est finalement retiré (ex.
    dépassement de la capacité de page).

    `exclude_keys` : identités (cf. exercise_identity) déjà servies dans la
    copie en cours — jamais re-piochées tant qu'il reste des exercices non
    utilisés (pas deux fois le même exercice dans un même sujet d'un élève).
    Repli sur l'ensemble complet si tous les exercices disponibles sont déjà
    exclus."""
    if not rows:
        raise ValueError("banque vide")
    if exclude_keys:
        available = [r for r in rows if exercise_identity(r) not in exclude_keys]
        rows = available or rows
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


def _fresh_plan(student: Student) -> dict | None:
    """Plan post-correction (services.appreciation) si présent et pas plus
    vieux que next_plan_max_age_days, sinon None."""
    plan = student.next_plan_json
    if not plan or not student.next_plan_updated_at:
        return None
    age_days = (datetime.now(timezone.utc) - _utc(student.next_plan_updated_at)).days
    if age_days > settings.next_plan_max_age_days:
        return None
    return plan


def apply_next_plan(student: Student, target_mix: dict[str, float],
                    level5: int) -> tuple[dict[str, float], int]:
    """En mode "individuel", affine la difficulté à partir du plan
    post-correction stocké pour l'élève (cf. services.appreciation) — évite un
    second appel LLM à la création du sujet. Ignoré si absent ou plus vieux que
    settings.next_plan_max_age_days ; le périmètre de compétences coché par le
    professeur n'est jamais modifié par cette fonction.

    Le mix de types, lui, n'est PLUS pris dans le plan (le LLM en proposait un
    par élève, qui écrasait silencieusement settings.exercise_kind_mix) : ce
    réglage fixe la répartition de la charge de correction entre CV (gratuit)
    et OCR Mathpix (payant, sous quota), une contrainte d'infrastructure globale
    — pas une préférence pédagogique à personnaliser élève par élève. Le retour
    reste un couple (mix, niveau) : le mix passé par l'appelant est renvoyé
    inchangé, la signature ne bouge pas."""
    plan = _fresh_plan(student)
    if not plan:
        return target_mix, level5
    plan_level = plan.get("difficulty_level")
    level = plan_level if isinstance(plan_level, int) and 1 <= plan_level <= 5 else level5
    return target_mix, level


def lesson_review_targets(candidate_ids: list[str], student: Student, due: list[dict],
                          level: int, assessment_type: str) -> list[str]:
    """Compétences (parmi `candidate_ids`, cochées pour ce sujet) devant
    recevoir un rappel de leçon dans cette copie — jamais en contrôle, jamais
    plus de `settings.max_lessons_per_copy`. Un rappel de leçon peut se
    répéter d'un sujet à l'autre pour le même élève (voulu, cf. accompagnement
    personnalisé) ; ne jamais l'inclure deux fois DANS la même copie reste à
    la charge de l'appelant (dédoublonnage sur competency_id).

    Priorité, jamais cumulée :
      1. le plan post-correction personnalisé (`Student.next_plan_json
         ["lesson_competency_ids"]`, cf. services.appreciation) — lacunes
         identifiées par le LLM à partir de la courbe d'oubli lors de la
         dernière correction ;
      2. à défaut (plan absent/périmé/vide), repli déterministe : compétences
         de `due` (services.forgetting.due_competencies) dont la maîtrise
         est sous `lesson_review_mastery_threshold` — une vraie lacune, pas
         simplement "due" par le temps ;
      3. filet de sécurité historique pour un élève sans aucune preuve
         encore : niveau global <= 4 -> 1re compétence du sujet.
    """
    if assessment_type != "training" or not candidate_ids:
        return []
    candidates = set(candidate_ids)
    cap = settings.max_lessons_per_copy

    plan = _fresh_plan(student)
    if plan:
        planned = [cid for cid in (plan.get("lesson_competency_ids") or [])
                  if cid in candidates]
        if planned:
            return planned[:cap]

    weak = [d["competency_id"] for d in due
            if d["competency_id"] in candidates
            and d.get("mastery", 1.0) < settings.lesson_review_mastery_threshold]
    if weak:
        return weak[:cap]

    if level <= 4:
        return candidate_ids[:1]
    return []

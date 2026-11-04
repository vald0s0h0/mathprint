"""Règles de tirage ajoutées à l'ordre existant, sans le remplacer.

L'ordre de l'élève passe toujours d'abord (inédit, puis raté ancien, puis le reste,
cf. student_history.candidate_rank). À rang égal, on préfère : une carte qui teste
une erreur active de cet élève, puis une carte aimée par le professeur, puis une
carte de la forge. Une carte écartée n'est jamais tirée pour ce professeur.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import ExercisePreference, TeacherProfile
from . import microcorrection

LIKE, DISLIKE = "like", "dislike"
FORGE_SOURCE = "forge"
MAX_REMEDIATION_CARDS = 2   # au-delà, on enfermerait l'élève dans ses erreurs


@dataclass
class DrawContext:
    likes: set[str] = field(default_factory=set)
    dislikes: set[str] = field(default_factory=set)
    active_errors: set[str] = field(default_factory=set)
    remediation_left: int = 0

    def for_student(self, active_errors: set[str]) -> "DrawContext":
        return DrawContext(self.likes, self.dislikes, set(active_errors),
                           MAX_REMEDIATION_CARDS if active_errors else 0)


def preference_owner(db: Session, assessment) -> str | None:
    """Le professeur du sujet, même si un correcteur le compose. Une fiche sans
    compte de connexion n'a pas de préférences : on retombe sur l'auteur."""
    profile_id = getattr(assessment, "teacher_profile_id", None)
    profile = db.get(TeacherProfile, profile_id) if profile_id else None
    return (profile.user_id if profile else None) or getattr(assessment, "created_by", None)


def load_context(db: Session, assessment) -> DrawContext:
    owner = preference_owner(db, assessment)
    if not owner:
        return DrawContext()
    rows = db.query(ExercisePreference).filter_by(user_id=owner).all()
    return DrawContext(likes={r.exercise_id for r in rows if r.value == LIKE},
                       dislikes={r.exercise_id for r in rows if r.value == DISLIKE})


def row_error_targets(row) -> set[str]:
    """Erreurs types qu'un exercice permet d'observer, parties d'un composite comprises."""
    grading = row.grading_json or {}
    parts = grading.get("parts") or []
    if not parts:
        return microcorrection.error_targets(row)
    out: set[str] = set()
    for part in parts:
        out |= microcorrection.error_targets(_Part(part.get("grading") or {}))
    return out


@dataclass
class _Part:
    grading_json: dict


def is_remediation(row, context: DrawContext) -> bool:
    return bool(context.remediation_left > 0 and row_error_targets(row) & context.active_errors)


def preference_key(row, context: DrawContext) -> tuple[bool, bool, bool]:
    """À rang égal, le plus petit passe devant."""
    return (not is_remediation(row, context), row.id not in context.likes,
            row.source != FORGE_SOURCE)


def allowed(rows: list, context: DrawContext) -> list:
    return [r for r in rows if r.id not in context.dislikes]


def preferred(rows: list, context: DrawContext) -> list:
    """Les meilleures cartes selon ces règles. Le tirage équilibré des types de
    réponse se fait ensuite parmi elles."""
    if not rows:
        return rows
    best = min(preference_key(r, context) for r in rows)
    return [r for r in rows if preference_key(r, context) == best]


@dataclass
class SheetState:
    """Ce qu'un sujet affiche et révèle déjà, pour éviter qu'une carte en trahisse une autre."""
    gives: set[str] = field(default_factory=set)
    reveals: set[str] = field(default_factory=set)
    families: set[str] = field(default_factory=set)

    def add(self, row) -> None:
        grading = row.grading_json or {}
        self.gives |= set(grading.get("gives") or [])
        self.reveals |= set(grading.get("leak_keys") or [])
        if grading.get("family_id"):
            self.families.add(grading["family_id"])


def conflicts(row, state: SheetState) -> bool:
    """Déterministe, sans LLM : les clés ont été vérifiées à la génération."""
    grading = row.grading_json or {}
    return bool(set(grading.get("leak_keys") or []) & state.gives
                or set(grading.get("gives") or []) & state.reveals
                or (grading.get("family_id") and grading["family_id"] in state.families))


def without_conflicts(rows: list, state: SheetState) -> list:
    """Cartes compatibles avec le sujet. S'il n'en reste aucune, l'appelant garde
    les cartes d'origine et prévient le professeur : mieux vaut une carte à vérifier
    qu'une compétence absente."""
    return [r for r in rows if not conflicts(r, state)]

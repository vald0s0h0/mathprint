"""La revanche et l'objectif par devoir (sujets individuels), sans LLM.

Revanche : une erreur type encore active, observée sur une carte qui a des
jumelles, fait revenir cette famille au sujet suivant, avec un AUTRE jeu de
valeurs que ceux déjà servis à l'élève. Elle compte comme une carte de
remédiation (draw_rules.MAX_REMEDIATION_CARDS). Quand le journal referme
l'erreur (error_journal.closures), la copie corrigée l'écrit : « Erreur vaincue ».

Objectif : une ligne en tête de copie, tirée de la première erreur active de
l'élève qui porte sur une compétence du devoir, à défaut de la compétence la
plus prioritaire du devoir. Figé sur la copie (Copy.goal_json) ; la copie
corrigée dit « Objectif atteint » ou « Objectif : pas encore ».
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    Competency, Copy, CopyItem, CopyItemResult, CopyResult, GeneratedExercise,
)
from . import error_journal
from .mathrender import strip_math

REVANCHE_LABEL = "Revanche"
VANQUISHED_LABEL = "Erreur vaincue"
GOAL_REACHED = 0.6          # réussite moyenne des cartes de la compétence (forgetting.SUCCESS_Q)


def _utc(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ------------------------------------------------------------------ revanche

def revenge_groups(db: Session, student_id: str, subject_id: str = "math") -> dict[str, str]:
    """{famille de jumelles → erreur active observée sur une de ses cartes}."""
    active = error_journal.active_errors(db, student_id, subject_id)
    if not active:
        return {}
    by_exercise: dict[str, str] = {}
    for row in error_journal._current_rows(db, student_id, subject_id):
        if row.observed and row.error_code in active and row.exercise_id:
            by_exercise.setdefault(row.exercise_id, row.error_code)
    if not by_exercise:
        return {}
    out: dict[str, str] = {}
    for ex_id, group in (db.query(GeneratedExercise.id, GeneratedExercise.twin_group)
                         .filter(GeneratedExercise.id.in_(list(by_exercise)),
                                 GeneratedExercise.twin_group.isnot(None)).all()):
        out.setdefault(group, by_exercise[ex_id])
    return out


def served_twins(db: Session, student_id: str, groups) -> dict[str, dict[tuple[int, int], datetime]]:
    """{famille → {(jeu, disposition) → dernier jour servi}} pour cet élève."""
    groups = [g for g in groups if g]
    out: dict[str, dict[tuple[int, int], datetime]] = {}
    if not groups:
        return out
    rows = (db.query(GeneratedExercise.twin_group, GeneratedExercise.twin_jeu,
                     GeneratedExercise.twin_disposition, Copy.generated_at)
            .join(CopyItem, CopyItem.generated_exercise_id == GeneratedExercise.id)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .filter(Copy.student_id == student_id, GeneratedExercise.twin_group.in_(groups)).all())
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    for group, jeu, disp, at in rows:
        seen = out.setdefault(group, {})
        at = _utc(at) or epoch
        seen[(jeu, disp)] = max(at, seen.get((jeu, disp), epoch))
    return out


def revenge_choice(siblings: dict[tuple[int, int], object], served: dict[tuple[int, int], datetime],
                   default_disposition: int = 1) -> tuple[int, int] | None:
    """(jeu, disposition) de la revanche : un jeu jamais servi (le plus petit), dans
    la disposition des voisins si elle existe ; sinon le jeu servi le plus
    anciennement, dans l'autre disposition. Jamais une jumelle déjà servie quand
    une autre existe."""
    if not siblings:
        return None
    jeux = sorted({j for j, _d in siblings})
    seen_jeux = {j for j, _d in served}
    fresh = [j for j in jeux if j not in seen_jeux]
    if fresh:
        jeu = fresh[0]
        options = sorted(d for j, d in siblings if j == jeu)
        return jeu, default_disposition if default_disposition in options else options[0]
    last = {}
    for (j, d), at in served.items():
        if j not in last or at > last[j][0]:
            last[j] = (at, d)
    jeu = min(jeux, key=lambda j: (last.get(j, (datetime.min.replace(tzinfo=timezone.utc), 0))[0], j))
    unseen = sorted(d for j, d in siblings if j == jeu and (j, d) not in served)
    if unseen:
        return jeu, unseen[0]
    others = sorted(d for j, d in siblings if j == jeu and d != last[jeu][1])
    if others:
        return jeu, others[0]
    # toutes les jumelles ont été servies et il n'y a qu'une disposition : la plus ancienne
    return min(served, key=lambda k: (served[k], k)) if served else (jeu, last[jeu][1])


# ------------------------------------------------------------------ objectif

def goal_text(label: str) -> str:
    return f"Ton objectif : réussir « {strip_math(label).strip().rstrip('.')} »"


def choose_goal(db: Session, student_id: str, subject_id: str, competency_ids: list[str],
                by_priority: list[str]) -> dict | None:
    """Objectif du devoir. `by_priority` : compétences du devoir, la plus
    prioritaire d'abord (student_history.student_plan)."""
    wanted = set(competency_ids)
    target, source, code = None, "priority", None
    rows = error_journal._current_rows(db, student_id, subject_id)
    for stat in error_journal.error_stats(db, student_id, subject_id):
        if not stat.active or stat.code.startswith("comptage."):
            continue
        comps = [r.competency_id for r in rows
                 if r.error_code == stat.code and r.observed and r.competency_id in wanted]
        if comps:
            target, source, code = comps[-1], "error", stat.code
            break
    if target is None:
        target = next((c for c in by_priority if c in wanted), None)
    comp = db.get(Competency, target) if target else None
    if comp is None:
        return None
    return {"competency_id": comp.id, "label": comp.label, "text": goal_text(comp.label),
            "source": source, "error_code": code}


def goal_verdict(db: Session, copy: Copy) -> bool | None:
    """True « Objectif atteint », False « pas encore », None sans carte corrigée
    de la compétence visée dans ce devoir."""
    goal = copy.goal_json or {}
    if not goal.get("competency_id"):
        return None
    ratios = [r.success_ratio or 0.0 for r in (
        db.query(CopyItemResult).join(CopyResult, CopyResult.id == CopyItemResult.copy_result_id)
        .filter(CopyResult.copy_id == copy.id,
                CopyItemResult.competency_id == goal["competency_id"]).all())]
    if not ratios:
        return None
    return sum(ratios) / len(ratios) >= GOAL_REACHED


def vanquished_items(db: Session, copy: Copy, subject_id: str = "math") -> dict[str, str]:
    """{carte de cette copie → erreur vaincue sur elle}."""
    return {row.copy_item_id: code
            for code, row, _still in error_journal.closures(db, copy.student_id, subject_id)
            if row.copy_id == copy.id}

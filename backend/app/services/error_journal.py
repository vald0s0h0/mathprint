"""Journal d'erreurs par élève, en ajout seul.

Une ligne dit : à cette date, sur cette carte, cette erreur type a été observée ou
évitée. Le dénominateur compte autant que le numérateur : « 3 fois » ne veut rien
dire sans le nombre d'occasions de se tromper.

Refaire une correction ne crée aucun doublon : `sync_copy` compare au dernier état
enregistré et n'ajoute une révision que s'il a changé. On ne réécrit jamais une
ligne ; la plus récente de chaque couple (carte, erreur) fait foi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    Competency, Copy, CopyItem, ExerciseCompetency, GradingDecision, OcrAttempt,
    StudentLearningEvent, StudentResponse,
)
from . import microcorrection, scoring

MIN_OCCURRENCES = 2         # une erreur isolée n'est pas une conception erronée
MIN_EXERCISES = 2
RESOLVED_AFTER = 2          # occasions évitées d'affilée, sur des exercices différents
KIND_ERROR = "erreur"
KIND_WITHDRAWN = "retrait"


def _reading(db: Session, zone_id: str | None):
    if not zone_id:
        return None
    return (db.query(OcrAttempt).filter_by(zone_id=zone_id)
            .order_by(OcrAttempt.created_at.desc()).first())


def _competency_id(db: Session, item: CopyItem, framework_id: str | None) -> str | None:
    """La compétence de la partie, si elle la déclare (problème), sinon celle de
    l'entrée catalogue."""
    code = (item.grading_json or {}).get("competency_code")
    if code and framework_id:
        row = (db.query(Competency).filter_by(framework_id=framework_id)
               .filter((Competency.short_id == code) | (Competency.code == code)).first())
        if row:
            return row.id
    link = db.query(ExerciseCompetency).filter_by(exercise_id=item.catalog_id).first()
    return link.competency_id if link else None


def _wanted_state(db: Session, item: CopyItem) -> dict[str, tuple[bool, str | None, object]] | None:
    """{code: (observée, option, décision)} de cette carte, ou None si elle n'a
    pas encore de décision définitive."""
    response = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
    if response is None:
        return None
    decision = (db.query(GradingDecision).filter_by(response_id=response.id)
                .order_by(GradingDecision.created_at.desc()).first())
    if decision is None or decision.status == "review_pending":
        return None
    if not decision.max_score:
        return {}       # question annulée par le professeur : elle ne compte plus
    selection = microcorrection.select(item, decision, response, _reading(db, response.zone_id))
    if selection is None or not selection.comparable:
        return {}
    observed = dict(selection.errors)
    state: dict[str, tuple[bool, str | None, object]] = {
        code: (code in observed, observed.get(code), decision)
        for code in microcorrection.error_targets(item)}
    for code, oid in observed.items():
        state[code] = (True, oid, decision)
    return state


def _latest(db: Session, item_id: str) -> dict[str, StudentLearningEvent]:
    rows = (db.query(StudentLearningEvent).filter_by(copy_item_id=item_id)
            .order_by(StudentLearningEvent.revision).all())
    return {row.error_code: row for row in rows}


def sync_copy(db: Session, copy: Copy, assessment) -> int:
    """Met le journal d'une copie en accord avec ses décisions. Retourne le nombre
    de lignes ajoutées."""
    items = db.query(CopyItem).filter_by(copy_id=copy.id).all()
    framework_id = getattr(assessment, "framework_id", None)
    occurred_at = scoring.assessment_date(assessment, copy)
    added = 0
    for item in items:
        if not isinstance((item.grading_json or {}).get("feedback"), dict):
            continue
        wanted = _wanted_state(db, item)
        if wanted is None:
            continue
        latest = _latest(db, item.id)
        now = datetime.now(timezone.utc)
        competency_id = None
        for code, (observed, option_id, decision) in wanted.items():
            previous = latest.get(code)
            if (previous is not None and previous.kind == KIND_ERROR
                    and previous.observed == observed and previous.option_id == option_id):
                continue
            competency_id = competency_id or _competency_id(db, item, framework_id)
            db.add(StudentLearningEvent(
                student_id=copy.student_id, subject_id=assessment.subject_id or "math",
                competency_id=competency_id, copy_id=copy.id, copy_item_id=item.id,
                exercise_id=item.generated_exercise_id, error_code=code, observed=observed,
                kind=KIND_ERROR, option_id=option_id,
                message=_message(item, code, option_id),
                source=getattr(decision, "source", "") or "", confidence=getattr(decision, "confidence", None),
                occurred_at=occurred_at, recorded_at=now,
                revision=previous.revision + 1 if previous else 1))
            added += 1
        for code, previous in latest.items():
            if code not in wanted and previous.kind != KIND_WITHDRAWN:
                added += _withdraw_row(db, previous, now)
    return added


def _message(item: CopyItem, code: str, option_id: str | None) -> str:
    """Texte figé : la banque peut changer, le journal garde ce qui valait alors."""
    feedback = (item.grading_json or {}).get("feedback") or {}
    node = (feedback.get("options") or {}).get(option_id or "")
    if node:
        return node.get("text", "")
    for wrong in feedback.get("wrong_answers") or []:
        if wrong.get("error") == code:
            return wrong.get("text", "")
    return ""


def _withdraw_row(db: Session, previous: StudentLearningEvent, now: datetime) -> int:
    db.add(StudentLearningEvent(
        student_id=previous.student_id, subject_id=previous.subject_id,
        competency_id=previous.competency_id, copy_id=previous.copy_id,
        copy_item_id=previous.copy_item_id, exercise_id=previous.exercise_id,
        error_code=previous.error_code, observed=False, kind=KIND_WITHDRAWN,
        option_id=None, message="", source=previous.source, confidence=None,
        occurred_at=previous.occurred_at, recorded_at=now, withdrawn_at=now,
        revision=previous.revision + 1))
    return 1


def withdraw_items(db: Session, item_ids: list[str]) -> int:
    """La copie ou la question sort de la correction : ses lignes ne comptent plus,
    mais restent dans l'historique."""
    now = datetime.now(timezone.utc)
    added = 0
    for item_id in item_ids:
        for previous in _latest(db, item_id).values():
            if previous.kind != KIND_WITHDRAWN:
                added += _withdraw_row(db, previous, now)
    return added


def withdraw_copy(db: Session, copy_id: str) -> int:
    item_ids = [row.id for row in db.query(CopyItem.id).filter_by(copy_id=copy_id).all()]
    return withdraw_items(db, item_ids)


@dataclass
class ErrorStat:
    code: str
    occurrences: int
    occasions: int
    exercises: int
    recent_occurrences: int
    recent_occasions: int
    last_at: datetime | None
    active: bool


def _current_rows(db: Session, student_id: str, subject_id: str | None) -> list[StudentLearningEvent]:
    """Dernière ligne de chaque couple (carte, erreur), retraits exclus."""
    query = db.query(StudentLearningEvent).filter_by(student_id=student_id)
    if subject_id:
        query = query.filter_by(subject_id=subject_id)
    latest: dict[tuple[str, str], StudentLearningEvent] = {}
    for row in query.order_by(StudentLearningEvent.revision).all():
        latest[(row.copy_item_id, row.error_code)] = row
    return [row for row in latest.values() if row.kind == KIND_ERROR]


def error_stats(db: Session, student_id: str, subject_id: str | None = "math",
                recent: int = 4) -> list[ErrorStat]:
    """Fréquence de chaque erreur : « 3 fois sur 11 occasions, dont 1 sur les 4
    dernières ». Une erreur devient active à partir de 2 occurrences sur 2
    exercices différents, et se referme après 2 occasions évitées."""
    by_code: dict[str, list[StudentLearningEvent]] = {}
    for row in _current_rows(db, student_id, subject_id):
        by_code.setdefault(row.error_code, []).append(row)
    out = []
    for code, rows in by_code.items():
        rows.sort(key=lambda r: (r.occurred_at or datetime.min.replace(tzinfo=None), r.recorded_at))
        seen = [r for r in rows if r.observed]
        exercises = {r.exercise_id or r.copy_item_id for r in seen}
        last = rows[-recent:]
        out.append(ErrorStat(
            code=code, occurrences=len(seen), occasions=len(rows), exercises=len(exercises),
            recent_occurrences=sum(r.observed for r in last), recent_occasions=len(last),
            last_at=seen[-1].occurred_at if seen else None,
            active=_is_active(rows, seen, exercises)))
    out.sort(key=lambda s: (-s.active, -s.occurrences, s.code))
    return out


def _is_active(rows, seen, exercises) -> bool:
    if len(seen) < MIN_OCCURRENCES or len(exercises) < MIN_EXERCISES:
        return False
    index = rows.index(seen[-1])
    avoided = {r.exercise_id or r.copy_item_id for r in rows[index + 1:] if not r.observed}
    return len(avoided) < RESOLVED_AFTER


def active_errors(db: Session, student_id: str, subject_id: str | None = "math") -> set[str]:
    """Erreurs à retravailler. Les codes de comptage décrivent une attitude, pas
    une notion : ils restent au journal, mais ne déclenchent aucune remédiation."""
    return {s.code for s in error_stats(db, student_id, subject_id)
            if s.active and not s.code.startswith("comptage.")}


def active_error_competencies(db: Session, student_id: str, subject_id: str | None = "math") -> set[str]:
    """Compétences où l'élève a commis une erreur encore active."""
    active = active_errors(db, student_id, subject_id)
    return {row.competency_id for row in _current_rows(db, student_id, subject_id)
            if row.observed and row.error_code in active and row.competency_id}


__all__ = ["sync_copy", "withdraw_items", "withdraw_copy", "error_stats", "active_errors",
           "ErrorStat"]

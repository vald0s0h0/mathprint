"""Carnet de notes : une matrice élèves × sujets corrigés."""
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import (
    Assessment, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItemResult,
    CopyResult, SchoolClass, Student, StudentLevel,
)
from ..services import scoring

router = APIRouter(prefix="/api/grades", tags=["grades"],
                   dependencies=[Depends(current_user)])


class PronoteStatusIn(BaseModel):
    entered: bool


@router.patch("/assessments/{assessment_id}/pronote")
def set_pronote_status(
    assessment_id: str,
    body: PronoteStatusIn,
    db: Session = Depends(get_db),
):
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        raise HTTPException(404, "Évaluation inconnue")
    assessment.pronote_entered = body.entered
    db.commit()
    return {"pronote_entered": assessment.pronote_entered}


@router.get("/classes/{class_id}")
def class_gradebook(
    class_id: str,
    kind: Literal["all", "control", "training"] = "all",
    db: Session = Depends(get_db),
):
    school_class = db.get(SchoolClass, class_id)
    if not school_class or school_class.archived_at is not None:
        raise HTTPException(404, "Classe inconnue")

    students = (db.query(Student).filter_by(class_id=class_id, active=True)
                .order_by(Student.order_index, Student.id).all())
    student_ids = [student.id for student in students]

    # Un sujet devient une colonne lorsqu'au moins une copie a été consolidée.
    # Cela évite d'encombrer le carnet avec les brouillons et sujets à venir.
    result_query = (db.query(CopyResult)
                    .filter(CopyResult.student_id.in_(student_ids))
                    .order_by(CopyResult.finalized_at.asc())) if student_ids else None
    results = result_query.all() if result_query is not None else []
    # `generated`/`printed` sur un sujet finalisé désigne une copie qui n'est
    # jamais revenue au scanner. On les accepte aussi pour les corrections
    # historiques, avant que la finalisation ne les convertisse en `absent`.
    absent_copies = (db.query(Copy).join(Assessment, Copy.assessment_id == Assessment.id)
                     .filter(Copy.student_id.in_(student_ids),
                             Copy.status.in_(("absent", "generated", "printed")),
                             Assessment.status == "finalized").all()
                     if student_ids else [])
    assessment_ids = list(dict.fromkeys(
        [result.assessment_id for result in results]
        + [copy.assessment_id for copy in absent_copies]
    ))

    assessment_query = db.query(Assessment).filter(
        Assessment.class_id == class_id, Assessment.id.in_(assessment_ids))
    if kind != "all":
        assessment_query = assessment_query.filter(Assessment.type == kind)
    assessments = assessment_query.order_by(Assessment.created_at.asc(), Assessment.id).all()
    allowed_ids = {assessment.id for assessment in assessments}

    values: dict[str, dict[str, dict]] = {student.id: {} for student in students}
    assessments_by_id = {assessment.id: assessment for assessment in assessments}
    for result in results:
        if result.assessment_id not in allowed_ids or result.student_id not in values:
            continue
        assessment = assessments_by_id[result.assessment_id]
        base = scoring.assessment_note_base(assessment)
        # Repli pour les entraînements déjà finalisés avant leur scoring : les
        # points consolidés suffisent à reconstruire la même règle de trois.
        raw, rounded = scoring.note_from_points(
            result.points_earned, result.points_total, base)
        values[result.student_id][result.assessment_id] = {
            "note": result.note if result.note is not None and result.note_base == base else rounded,
            "note_raw": (result.note_raw
                         if result.note_raw is not None and result.note_base == base else raw),
            "note_base": base,
            "points_earned": result.points_earned,
            "points_total": result.points_total,
            "absent": False,
            "mastery_delta": None,
            "level_delta": None,
        }

    # Une absence est une donnée, pas une case sans résultat. Elle reste donc
    # explicite même si aucune copie de l'évaluation n'a produit de note.
    for copy in absent_copies:
        if copy.assessment_id not in allowed_ids or copy.student_id not in values:
            continue
        values[copy.student_id].setdefault(copy.assessment_id, {
            "note": None,
            "note_raw": None,
            "note_base": scoring.assessment_note_base(assessments_by_id[copy.assessment_id]),
            "points_earned": None,
            "points_total": None,
            "absent": True,
            "mastery_delta": None,
            "level_delta": None,
        })

    # Une correction peut comporter plusieurs exercices sur une même
    # compétence. On additionne leurs variations successives, puis on fait la
    # moyenne des compétences mobilisées : le résultat est un gain/perte moyen
    # en points de pourcentage pour cette évaluation.
    visible_results = [result for result in results
                       if result.assessment_id in allowed_ids and result.student_id in values]
    result_context = {
        result.id: (result.student_id, result.assessment_id) for result in visible_results
    }
    item_results = (db.query(CopyItemResult)
                    .filter(CopyItemResult.copy_result_id.in_(result_context)).all()
                    if result_context else [])
    item_context = {
        row.copy_item_id: result_context[row.copy_result_id] for row in item_results
    }
    evidences = (db.query(CompetencyEvidence)
                 .filter(CompetencyEvidence.item_id.in_(item_context)).all()
                 if item_context else [])
    evidence_context = {
        evidence.id: (*item_context[evidence.item_id], evidence.competency_id)
        for evidence in evidences if evidence.item_id in item_context
    }
    histories = (db.query(CompetencyStateHistory)
                 .filter(CompetencyStateHistory.evidence_id.in_(evidence_context)).all()
                 if evidence_context else [])
    competency_deltas: dict[tuple[str, str, str], float] = defaultdict(float)
    for history in histories:
        context = evidence_context.get(history.evidence_id)
        if context is None:
            continue
        before = float((history.before_json or {}).get("mastery", 0))
        after = float((history.after_json or {}).get("mastery", before))
        competency_deltas[context] += after - before
    assessment_deltas: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (student_id, assessment_id, _competency_id), delta in competency_deltas.items():
        assessment_deltas[(student_id, assessment_id)].append(delta)
    for (student_id, assessment_id), deltas in assessment_deltas.items():
        value = values.get(student_id, {}).get(assessment_id)
        if value is not None and deltas:
            value["mastery_delta"] = round(sum(deltas) / len(deltas) * 100, 1)

    # Les paliers automatiques portent l'id exact de la correction. Les lignes
    # manuelles/historiques restent dans la chronologie afin de fournir le
    # niveau précédent, mais ne sont attribuées à aucune colonne.
    level_rows = (db.query(StudentLevel)
                  .filter(StudentLevel.student_id.in_(student_ids))
                  .order_by(StudentLevel.valid_from.asc(), StudentLevel.id.asc()).all()
                  if student_ids else [])
    previous_levels: dict[str, int] = {}
    for row in level_rows:
        previous = previous_levels.get(row.student_id)
        if row.assessment_id in allowed_ids and previous is not None:
            value = values.get(row.student_id, {}).get(row.assessment_id)
            if value is not None:
                difference = row.level - previous
                value["level_delta"] = 1 if difference > 0 else -1 if difference < 0 else 0
        previous_levels[row.student_id] = row.level

    return {
        "class": {"id": school_class.id, "name": school_class.name,
                  "grade_level": school_class.grade_level},
        "students": [{"id": student.id, "name": student.name,
                      "order_index": student.order_index} for student in students],
        "assessments": [{
            "id": assessment.id, "title": assessment.title, "type": assessment.type,
            "note_base": scoring.assessment_note_base(assessment),
            "pronote_entered": bool(assessment.pronote_entered),
            "created_at": str(assessment.created_at),
        } for assessment in assessments],
        "values": values,
    }

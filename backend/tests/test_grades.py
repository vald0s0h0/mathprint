"""Carnet de notes : ordre de classe, filtres et scoring des entraînements."""
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import (
    Assessment, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItemResult,
    CopyResult, SchoolClass, Student, StudentLevel,
)
from app.routers.grades import PronoteStatusIn, class_gradebook, set_pronote_status


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_gradebook_keeps_class_order_and_scores_legacy_training():
    db = _db()
    school_class = SchoolClass(name="5e A", grade_level="5e")
    db.add(school_class)
    db.flush()
    second = Student(class_id=school_class.id, name="Deuxième", order_index=1,
                     llm_pseudonym="p2")
    first = Student(class_id=school_class.id, name="Premier", order_index=0,
                    llm_pseudonym="p1")
    db.add_all([second, first])
    db.flush()
    control = Assessment(class_id=school_class.id, type="control", title="Fractions",
                         status="finalized", note_base=10)
    training = Assessment(class_id=school_class.id, type="training", title="Calcul mental",
                          status="finalized", note_base=5)
    db.add_all([control, training])
    db.flush()
    db.add_all([
        CopyResult(copy_id="c1", assessment_id=control.id, student_id=first.id,
                   points_earned=3, points_total=4, note_base=10,
                   note_raw=7.5, note=7.5),
        # Ancien entraînement : points présents, mais note_base=0 et notes
        # nulles. Le carnet doit reconstruire 4/5 par règle de trois.
        CopyResult(copy_id="c2", assessment_id=training.id, student_id=first.id,
                   points_earned=8, points_total=10, note_base=0),
    ])
    db.commit()

    book = class_gradebook(school_class.id, "all", db)

    assert [student["name"] for student in book["students"]] == ["Premier", "Deuxième"]
    assert [assessment["title"] for assessment in book["assessments"]] == [
        "Fractions", "Calcul mental",
    ]
    training_value = book["values"][first.id][training.id]
    assert training_value["note"] == 4
    assert training_value["note_base"] == 5

    trainings = class_gradebook(school_class.id, "training", db)
    assert [assessment["id"] for assessment in trainings["assessments"]] == [training.id]


def test_gradebook_reports_mastery_and_level_changes_for_the_exact_assessment():
    db = _db()
    school_class = SchoolClass(name="4e A", grade_level="4e")
    db.add(school_class)
    db.flush()
    student = Student(class_id=school_class.id, name="Camille", order_index=0,
                      llm_pseudonym="p1")
    db.add(student)
    db.flush()
    assessment = Assessment(class_id=school_class.id, type="control", title="Calcul",
                            status="finalized", note_base=20)
    db.add(assessment)
    db.flush()
    result = CopyResult(copy_id="copy", assessment_id=assessment.id,
                        student_id=student.id, points_earned=8,
                        points_total=10, note_base=20, note_raw=16, note=16)
    db.add(result)
    db.flush()
    db.add_all([
        CopyItemResult(copy_result_id=result.id, copy_item_id="item-1",
                       competency_id="comp-1"),
        CopyItemResult(copy_result_id=result.id, copy_item_id="item-2",
                       competency_id="comp-1"),
        CopyItemResult(copy_result_id=result.id, copy_item_id="item-3",
                       competency_id="comp-2"),
    ])
    evidences = [
        CompetencyEvidence(student_id=student.id, competency_id="comp-1",
                           item_id="item-1", score_ratio=1),
        CompetencyEvidence(student_id=student.id, competency_id="comp-1",
                           item_id="item-2", score_ratio=0),
        CompetencyEvidence(student_id=student.id, competency_id="comp-2",
                           item_id="item-3", score_ratio=1),
    ]
    db.add_all(evidences)
    db.flush()
    db.add_all([
        CompetencyStateHistory(student_id=student.id, competency_id="comp-1",
                               evidence_id=evidences[0].id,
                               before_json={"mastery": .2}, after_json={"mastery": .4}),
        CompetencyStateHistory(student_id=student.id, competency_id="comp-1",
                               evidence_id=evidences[1].id,
                               before_json={"mastery": .4}, after_json={"mastery": .35}),
        CompetencyStateHistory(student_id=student.id, competency_id="comp-2",
                               evidence_id=evidences[2].id,
                               before_json={"mastery": .1}, after_json={"mastery": .4}),
        StudentLevel(student_id=student.id, level=5,
                     valid_from=datetime(2026, 1, 1)),
        StudentLevel(student_id=student.id, assessment_id=assessment.id, level=6,
                     valid_from=datetime(2026, 2, 1)),
    ])
    db.commit()

    value = class_gradebook(school_class.id, "all", db)["values"][student.id][assessment.id]

    assert value["mastery_delta"] == 22.5
    assert value["level_delta"] == 1


def test_gradebook_marks_an_absent_copy_even_when_nobody_has_a_score():
    db = _db()
    school_class = SchoolClass(name="3e B", grade_level="3e")
    db.add(school_class)
    db.flush()
    student = Student(class_id=school_class.id, name="Camille B.", order_index=0,
                      llm_pseudonym="p1")
    db.add(student)
    db.flush()
    assessment = Assessment(class_id=school_class.id, type="control", title="Géométrie",
                            status="finalized", note_base=10)
    db.add(assessment)
    db.flush()
    # Une copie restée `generated` n'a jamais été scannée. Sur une évaluation
    # finalisée, le carnet doit la considérer absente même pour l'historique.
    db.add(Copy(assessment_id=assessment.id, student_id=student.id,
                status="generated"))
    db.commit()

    book = class_gradebook(school_class.id, "all", db)

    assert [row["id"] for row in book["assessments"]] == [assessment.id]
    value = book["values"][student.id][assessment.id]
    assert value["absent"] is True
    assert value["note"] is None


def test_pronote_status_is_persisted_in_gradebook():
    db = _db()
    school_class = SchoolClass(name="6e C", grade_level="6e")
    db.add(school_class)
    db.flush()
    student = Student(class_id=school_class.id, name="Lou", order_index=0,
                      llm_pseudonym="p1")
    db.add(student)
    db.flush()
    assessment = Assessment(class_id=school_class.id, type="control", title="Nombres",
                            status="finalized", note_base=10)
    db.add(assessment)
    db.flush()
    db.add(CopyResult(copy_id="copy-pronote", assessment_id=assessment.id,
                      student_id=student.id, points_earned=7, points_total=10,
                      note_base=10, note_raw=7, note=7))
    db.commit()

    set_pronote_status(assessment.id, PronoteStatusIn(entered=True), db)
    book = class_gradebook(school_class.id, "all", db)

    assert book["assessments"][0]["pronote_entered"] is True

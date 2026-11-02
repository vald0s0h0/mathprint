"""Classes, années scolaires et élèves (avec collage en lot, §9.4).

Vocabulaire (à respecter partout, UI comprise) :
- CYCLE : le niveau scolaire 6e/5e/4e/3e (grade_level) ;
- CLASSE : un groupe d'élèves d'un même cycle (ex. « 5eA ») ;
- NIVEAU : le niveau pédagogique 1-10 d'un élève, privé professeur (RM-007).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import (
    CompetencyEvidence, SchoolClass, SchoolYear, Student,
    StudentCompetencyState, StudentLevel, User, now, School, SchoolProgram,
    TeacherProfile, TeacherSchool, ClassTeaching, Subject, CurriculumPath, Assessment,
    Competency, CompetencyFramework,
)
from ..services.forgetting import recall_probability
from ..services.security import new_pseudonym

router = APIRouter(prefix="/api", tags=["org"], dependencies=[Depends(current_user)])

from ..services.education import GRADES as GRADE_LEVELS


class ClassIn(BaseModel):
    school_id: str
    program_id: str = "fr"
    series_id: str | None = None
    teacher_profile_id: str | None = None
    subject_id: str = "math"
    name: str
    grade_level: str = "5e"          # cycle : 6e | 5e | 4e | 3e
    school_year_id: str | None = None
    students_text: str = ""          # optionnel : liste collée, un élève par ligne


class ClassPatch(BaseModel):
    name: str | None = None
    grade_level: str | None = None
    archived: bool | None = None
    program_id: str | None = None
    series_id: str | None = None
    school_year_id: str | None = None


class StudentIn(BaseModel):
    name: str
    dyslexic: bool = False


class StudentPatch(BaseModel):
    name: str | None = None
    dyslexic: bool | None = None


class StudentOrderIn(BaseModel):
    student_ids: list[str]


class BatchStudentsIn(BaseModel):
    # texte collé : une ligne = le nom complet, sans tentative de découpage
    text: str


def _parse_students(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


@router.get("/years")
def list_years(db: Session = Depends(get_db)):
    return [{"id": y.id, "label": y.label, "active": y.active}
            for y in db.query(SchoolYear).order_by(SchoolYear.label.desc()).all()]


def _class_context(db, grade, program, series):
    if grade not in GRADE_LEVELS:
        raise HTTPException(422, "Cycle invalide")
    if series:
        path = db.get(CurriculumPath, series)
        if not path or path.kind != "series" or path.program_id != program or grade not in path.grades:
            raise HTTPException(422, "Série incompatible avec le programme et le cycle")
    elif program == "ci" and grade not in GRADE_LEVELS[:4]:
        raise HTTPException(422, "Choisissez une série pour le lycée ivoirien")


@router.get("/classes")
def list_classes(db: Session = Depends(get_db), include_archived: bool = False):
    years = {y.id: y.label for y in db.query(SchoolYear).all()}
    schools = {x.id: x.name for x in db.query(School).all()}
    query = db.query(SchoolClass)
    if not include_archived: query = query.filter(SchoolClass.archived_at.is_(None))
    return [{"id": c.id, "name": c.name, "grade_level": c.grade_level,
             "school_year": years.get(c.school_year_id), "school_year_id": c.school_year_id,
             "school_id": c.school_id, "school_name": schools.get(c.school_id, ""),
             "program_id": c.program_id, "series_id": c.series_id,
             "archived": c.archived_at is not None,
             "student_count": len([s for s in c.students if s.active])}
            for c in query.order_by(SchoolClass.name).all()]


@router.post("/classes")
def create_class(body: ClassIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    name = body.name.strip()
    if not name: raise HTTPException(422, "Nom de classe requis")
    school = db.get(School, body.school_id)
    if not school: raise HTTPException(404, "Établissement inaccessible")
    if not db.query(SchoolProgram).filter_by(school_id=school.id, program_id=body.program_id).first():
        raise HTTPException(422, "Programme non proposé par cet établissement")
    _class_context(db, body.grade_level, body.program_id, body.series_id)
    year = db.get(SchoolYear, body.school_year_id) if body.school_year_id else db.query(SchoolYear).filter_by(active=True).first()
    if not year or not year.active: raise HTTPException(422, "Année scolaire active requise")
    teacher = (db.query(TeacherProfile).filter_by(user_id=user.id).first() if user.role == "teacher"
               else db.get(TeacherProfile, body.teacher_profile_id) if body.teacher_profile_id else None)
    if not teacher or not teacher.active or not db.query(TeacherSchool).filter_by(teacher_id=teacher.id, school_id=school.id).first():
        raise HTTPException(422, "Choisissez un professeur de cet établissement")
    if not db.get(Subject, body.subject_id): raise HTTPException(422, "Matière inconnue")
    # La contrainte est vérifiée sur toutes les classes de l'établissement,
    # même celles qu'un autre professeur ne peut pas consulter.
    from sqlalchemy import select
    table = SchoolClass.__table__
    exists = db.execute(select(table.c.id).where(table.c.school_id == school.id,
        table.c.school_year_id == year.id, table.c.name == name)).first()
    if exists: raise HTTPException(409, "Cette classe existe déjà pour cet établissement et cette année")
    c = SchoolClass(name=name, grade_level=body.grade_level, school_year_id=year.id,
        teacher_id=teacher.user_id, school_id=school.id, program_id=body.program_id, series_id=body.series_id)
    db.add(c); db.flush()
    db.add(ClassTeaching(class_id=c.id, subject_id=body.subject_id, teacher_id=teacher.id))
    created = 0
    for order_index, student_name in enumerate(_parse_students(body.students_text)):
        db.add(Student(class_id=c.id, name=student_name, order_index=order_index,
                       llm_pseudonym=new_pseudonym()))
        created += 1
    db.commit()
    return {"id": c.id, "students_created": created}


@router.patch("/classes/{class_id}")
def update_class(class_id: str, body: ClassPatch, db: Session = Depends(get_db)):
    c = db.get(SchoolClass, class_id)
    if not c:
        raise HTTPException(404, "Classe inconnue")
    grade = body.grade_level or c.grade_level
    program = body.program_id or c.program_id
    series = body.series_id if "series_id" in body.model_fields_set else c.series_id
    _class_context(db, grade, program, series)
    if (grade, program, series) != (c.grade_level, c.program_id, c.series_id) and db.query(Assessment).filter_by(class_id=c.id).first():
        raise HTTPException(409, "Cette classe a des sujets : créez une nouvelle classe pour changer son parcours")
    if c.school_id and not db.query(SchoolProgram).filter_by(school_id=c.school_id, program_id=program).first():
        raise HTTPException(422, "Programme non proposé par l’établissement")
    if body.name is not None:
        if not body.name.strip(): raise HTTPException(422, "Nom requis")
        table = SchoolClass.__table__
        if db.execute(table.select().where(table.c.school_id == c.school_id,
                table.c.school_year_id == c.school_year_id, table.c.name == body.name.strip(),
                table.c.id != c.id)).first():
            raise HTTPException(409, "Une classe porte déjà ce nom dans cet établissement et cette année")
        c.name = body.name.strip()
    if body.school_year_id and body.school_year_id != c.school_year_id:
        raise HTTPException(409, "Utilisez la reprise de classe pour changer d’année scolaire")
    c.grade_level, c.program_id, c.series_id = grade, program, series
    if body.archived is not None:
        year = db.get(SchoolYear, c.school_year_id) if c.school_year_id else None
        if not body.archived and year and not year.active:
            raise HTTPException(409, "L’année scolaire est archivée")
        c.archived_at = now() if body.archived else None
    db.commit()
    return {"ok": True}


@router.get("/classes/{class_id}/students")
def list_students(class_id: str, db: Session = Depends(get_db), subject_id: str = "math"):
    """Liste enrichie : niveau 1-10, maîtrise moyenne, compétences dues,
    nombre de preuves — plutôt que des identifiants techniques."""
    if not db.get(SchoolClass, class_id):
        raise HTTPException(404, "Classe inconnue")
    rows = (db.query(Student).filter_by(class_id=class_id, active=True)
            .order_by(Student.order_index, Student.id).all())
    ids = [s.id for s in rows]
    comp_ids = db.query(Competency.id).join(CompetencyFramework).filter(CompetencyFramework.subject_id == subject_id)
    states: dict[str, list[StudentCompetencyState]] = {i: [] for i in ids}
    if ids:
        for st in (db.query(StudentCompetencyState)
                   .filter(StudentCompetencyState.student_id.in_(ids), StudentCompetencyState.competency_id.in_(comp_ids)).all()):
            states[st.student_id].append(st)
    levels: dict[str, int] = {}
    locks: dict[str, bool] = {}
    if ids:
        for lv in (db.query(StudentLevel)
                   .filter(StudentLevel.student_id.in_(ids), StudentLevel.subject_id == subject_id)
                   .order_by(StudentLevel.valid_from.asc()).all()):
            locks[lv.student_id] = lv.locked
            levels[lv.student_id] = lv.level  # le plus récent gagne
    evidence: dict[str, int] = {i: 0 for i in ids}
    if ids:
        for ev in (db.query(CompetencyEvidence)
                   .filter(CompetencyEvidence.student_id.in_(ids), CompetencyEvidence.competency_id.in_(comp_ids)).all()):
            evidence[ev.student_id] = evidence.get(ev.student_id, 0) + 1

    out = []
    for s in rows:
        sts = states.get(s.id, [])
        avg = sum(st.mastery for st in sts) / len(sts) if sts else None
        due = sum(1 for st in sts
                  if recall_probability(st) < settings.forgetting_threshold)
        out.append({"id": s.id, "name": s.name, "order_index": s.order_index,
                    "dyslexic": s.dyslexic, "pseudonym": s.llm_pseudonym,
                    "level": levels.get(s.id), "level_locked": locks.get(s.id, False),
                    "avg_mastery": round(avg, 2) if avg is not None else None,
                    "due_count": due, "evidence_count": evidence.get(s.id, 0)})
    return out


@router.post("/classes/{class_id}/students")
def add_student(class_id: str, body: StudentIn, db: Session = Depends(get_db)):
    if not db.get(SchoolClass, class_id):
        raise HTTPException(404, "Classe inconnue")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Nom de l'élève requis")
    last_position = (db.query(Student.order_index).filter_by(class_id=class_id)
                     .order_by(Student.order_index.desc()).first())
    s = Student(class_id=class_id, name=name, dyslexic=body.dyslexic,
                order_index=(last_position[0] + 1 if last_position else 0),
                llm_pseudonym=new_pseudonym())
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.post("/classes/{class_id}/students/batch")
def add_students_batch(class_id: str, body: BatchStudentsIn, db: Session = Depends(get_db)):
    if not db.get(SchoolClass, class_id):
        raise HTTPException(404, "Classe inconnue")
    last_position = (db.query(Student.order_index).filter_by(class_id=class_id)
                     .order_by(Student.order_index.desc()).first())
    start = last_position[0] + 1 if last_position else 0
    created = 0
    for offset, student_name in enumerate(_parse_students(body.text)):
        db.add(Student(class_id=class_id, name=student_name,
                       order_index=start + offset, llm_pseudonym=new_pseudonym()))
        created += 1
    db.commit()
    return {"created": created}


@router.patch("/students/{student_id}")
def update_student(student_id: str, body: StudentPatch, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404, "Élève inconnu")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Nom de l'élève requis")
        s.name = name
    if body.dyslexic is not None:
        s.dyslexic = body.dyslexic
    db.commit()
    return {"ok": True}


@router.put("/classes/{class_id}/students/order")
def reorder_students(class_id: str, body: StudentOrderIn,
                     db: Session = Depends(get_db)):
    rows = db.query(Student).filter_by(class_id=class_id, active=True).all()
    by_id = {s.id: s for s in rows}
    if len(body.student_ids) != len(set(body.student_ids)) or set(body.student_ids) != set(by_id):
        raise HTTPException(422, "La liste ordonnée doit contenir tous les élèves actifs")
    for order_index, student_id in enumerate(body.student_ids):
        by_id[student_id].order_index = order_index
    db.commit()
    return {"ok": True}


@router.delete("/students/{student_id}")
def deactivate_student(student_id: str, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    s.active = False
    db.commit()
    return {"ok": True}

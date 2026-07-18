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
    StudentCompetencyState, StudentLevel, User, now,
)
from ..services.forgetting import recall_probability
from ..services.security import new_pseudonym

router = APIRouter(prefix="/api", tags=["org"], dependencies=[Depends(current_user)])

GRADE_LEVELS = ("6e", "5e", "4e", "3e")


class ClassIn(BaseModel):
    name: str
    grade_level: str = "5e"          # cycle : 6e | 5e | 4e | 3e
    school_year_id: str | None = None
    students_text: str = ""          # optionnel : liste collée, un élève par ligne


class ClassPatch(BaseModel):
    name: str | None = None
    grade_level: str | None = None
    archived: bool | None = None


class StudentIn(BaseModel):
    first_name: str
    last_name: str


class BatchStudentsIn(BaseModel):
    # texte collé : une ligne par élève, "Nom Prénom" ou "Nom;Prénom"
    text: str


def _parse_students(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in (";", "\t", ","):
            if sep in line:
                last, first = [p.strip() for p in line.split(sep, 1)]
                break
        else:
            parts = line.split()
            last, first = parts[0], " ".join(parts[1:]) or "?"
        out.append((last, first))
    return out


@router.get("/years")
def list_years(db: Session = Depends(get_db)):
    return [{"id": y.id, "label": y.label, "active": y.active}
            for y in db.query(SchoolYear).order_by(SchoolYear.label.desc()).all()]


@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    years = {y.id: y.label for y in db.query(SchoolYear).all()}
    return [{"id": c.id, "name": c.name, "grade_level": c.grade_level,
             "school_year": years.get(c.school_year_id),
             "student_count": len([s for s in c.students if s.active])}
            for c in db.query(SchoolClass).filter(SchoolClass.archived_at.is_(None)).all()]


@router.post("/classes")
def create_class(body: ClassIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Nom de classe requis")
    if body.grade_level not in GRADE_LEVELS:
        raise HTTPException(422, "Cycle invalide (6e, 5e, 4e ou 3e)")
    exists = (db.query(SchoolClass)
              .filter(SchoolClass.archived_at.is_(None), SchoolClass.name == name)
              .first())
    if exists:
        raise HTTPException(409, f"La classe « {name} » existe déjà")
    year = db.query(SchoolYear).filter_by(active=True).first()
    c = SchoolClass(name=name, grade_level=body.grade_level,
                    school_year_id=body.school_year_id or (year.id if year else None),
                    teacher_id=user.id)
    db.add(c)
    db.flush()
    created = 0
    for last, first in _parse_students(body.students_text):
        db.add(Student(class_id=c.id, first_name=first, last_name=last,
                       llm_pseudonym=new_pseudonym()))
        created += 1
    db.commit()
    return {"id": c.id, "students_created": created}


@router.patch("/classes/{class_id}")
def update_class(class_id: str, body: ClassPatch, db: Session = Depends(get_db)):
    c = db.get(SchoolClass, class_id)
    if not c:
        raise HTTPException(404, "Classe inconnue")
    if body.name is not None and body.name.strip():
        c.name = body.name.strip()
    if body.grade_level is not None:
        if body.grade_level not in GRADE_LEVELS:
            raise HTTPException(422, "Cycle invalide (6e, 5e, 4e ou 3e)")
        c.grade_level = body.grade_level
    if body.archived is not None:
        c.archived_at = now() if body.archived else None
    db.commit()
    return {"ok": True}


@router.get("/classes/{class_id}/students")
def list_students(class_id: str, db: Session = Depends(get_db)):
    """Liste enrichie : niveau 1-10, maîtrise moyenne, compétences dues,
    nombre de preuves — plutôt que des identifiants techniques."""
    rows = db.query(Student).filter_by(class_id=class_id, active=True).all()
    ids = [s.id for s in rows]
    states: dict[str, list[StudentCompetencyState]] = {i: [] for i in ids}
    if ids:
        for st in (db.query(StudentCompetencyState)
                   .filter(StudentCompetencyState.student_id.in_(ids)).all()):
            states[st.student_id].append(st)
    levels: dict[str, int] = {}
    if ids:
        for lv in (db.query(StudentLevel)
                   .filter(StudentLevel.student_id.in_(ids))
                   .order_by(StudentLevel.valid_from.asc()).all()):
            levels[lv.student_id] = lv.level  # le plus récent gagne
    evidence: dict[str, int] = {i: 0 for i in ids}
    if ids:
        for ev in (db.query(CompetencyEvidence)
                   .filter(CompetencyEvidence.student_id.in_(ids)).all()):
            evidence[ev.student_id] = evidence.get(ev.student_id, 0) + 1

    out = []
    for s in sorted(rows, key=lambda r: (r.last_name.lower(), r.first_name.lower())):
        sts = states.get(s.id, [])
        avg = sum(st.mastery for st in sts) / len(sts) if sts else None
        due = sum(1 for st in sts
                  if recall_probability(st) < settings.forgetting_threshold)
        out.append({"id": s.id, "first_name": s.first_name, "last_name": s.last_name,
                    "pseudonym": s.llm_pseudonym,
                    "level": levels.get(s.id), "level_locked": s.level_locked,
                    "avg_mastery": round(avg, 2) if avg is not None else None,
                    "due_count": due, "evidence_count": evidence.get(s.id, 0)})
    return out


@router.post("/classes/{class_id}/students")
def add_student(class_id: str, body: StudentIn, db: Session = Depends(get_db)):
    s = Student(class_id=class_id, first_name=body.first_name.strip(),
                last_name=body.last_name.strip(), llm_pseudonym=new_pseudonym())
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.post("/classes/{class_id}/students/batch")
def add_students_batch(class_id: str, body: BatchStudentsIn, db: Session = Depends(get_db)):
    if not db.get(SchoolClass, class_id):
        raise HTTPException(404, "Classe inconnue")
    created = 0
    for last, first in _parse_students(body.text):
        db.add(Student(class_id=class_id, first_name=first, last_name=last,
                       llm_pseudonym=new_pseudonym()))
        created += 1
    db.commit()
    return {"created": created}


@router.delete("/students/{student_id}")
def deactivate_student(student_id: str, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    s.active = False
    db.commit()
    return {"ok": True}

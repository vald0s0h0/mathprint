"""Classes, années scolaires et élèves (avec collage en lot, §9.4)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import SchoolClass, SchoolYear, Student, User
from ..services.security import new_pseudonym

router = APIRouter(prefix="/api", tags=["org"], dependencies=[Depends(current_user)])


class ClassIn(BaseModel):
    name: str
    grade_level: str = "5e"
    school_year_id: str | None = None


class StudentIn(BaseModel):
    first_name: str
    last_name: str


class BatchStudentsIn(BaseModel):
    # texte collé : une ligne par élève, "Nom Prénom" ou "Nom;Prénom" ou "Nom\tPrénom"
    text: str


@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "grade_level": c.grade_level,
             "is_mock": c.is_mock, "student_count": len([s for s in c.students if s.active])}
            for c in db.query(SchoolClass).filter(SchoolClass.archived_at.is_(None)).all()]


@router.post("/classes")
def create_class(body: ClassIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    year = db.query(SchoolYear).filter_by(active=True).first()
    c = SchoolClass(name=body.name, grade_level=body.grade_level,
                    school_year_id=body.school_year_id or (year.id if year else None),
                    teacher_id=user.id)
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.get("/classes/{class_id}/students")
def list_students(class_id: str, db: Session = Depends(get_db)):
    rows = db.query(Student).filter_by(class_id=class_id, active=True).all()
    return [{"id": s.id, "first_name": s.first_name, "last_name": s.last_name,
             "pseudonym": s.llm_pseudonym} for s in rows]


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
    for line in body.text.splitlines():
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

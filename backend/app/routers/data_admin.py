"""Paramètres → Données (§9.6) : vue d'ensemble et suppression définitive
(RGPD, ménage) des classes, élèves, sujets et corrections. Distinct des
actions "douces" existantes (archiver une classe, désactiver un élève) :
ici tout disparaît, y compris les PDF/images stockés sur le volume.

La vue est COMPACTÉE par classe (des centaines d'élèves/sujets attendus) :
`/overview` donne les totaux et un agrégat par classe ; le détail d'une classe
(élèves, sujets, corrections) se charge à la demande via les filtres `class_id`.
`/orphans` recense les données pointant vers un parent disparu, pour un ménage
propre de la base."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import (Assessment, Copy, GeneratedExercise, ScanBatch,
                      SchoolClass, SchoolYear, Student)
from ..services import data_admin

router = APIRouter(prefix="/api/data", tags=["data-admin"],
                   dependencies=[Depends(require_role("admin"))])


# ----------------------------------------------------------------- vue d'ensemble

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """Totaux + un agrégat par classe (élèves, sujets, corrections) — de quoi
    afficher une vue repliée par classe sans tirer des centaines de lignes."""
    classes = db.query(SchoolClass).order_by(
        SchoolClass.archived_at.is_(None).desc(), SchoolClass.name).all()
    students = db.query(Student).all()
    assessments = db.query(Assessment).all()
    batches = db.query(ScanBatch).all()

    by_class_students: dict[str, int] = {}
    for s in students:
        by_class_students[s.class_id] = by_class_students.get(s.class_id, 0) + 1
    by_class_assess: dict[str, int] = {}
    assess_class = {}
    for a in assessments:
        by_class_assess[a.class_id] = by_class_assess.get(a.class_id, 0) + 1
        assess_class[a.id] = a.class_id
    by_class_corr: dict[str, int] = {}
    for b in batches:
        cid = assess_class.get(b.assessment_id)
        if cid:
            by_class_corr[cid] = by_class_corr.get(cid, 0) + 1

    orphans = data_admin.find_orphans(db)
    return {
        "totals": {
            "classes": len(classes), "students": len(students),
            "assessments": len(assessments), "corrections": len(batches),
            "bank_exercises": db.query(GeneratedExercise).count(),
            "orphans": sum(o["count"] for o in orphans),
        },
        "classes": [{
            "id": c.id, "name": c.name, "grade_level": c.grade_level,
            "archived": c.archived_at is not None,
            "students": by_class_students.get(c.id, 0),
            "assessments": by_class_assess.get(c.id, 0),
            "corrections": by_class_corr.get(c.id, 0),
        } for c in classes],
        "orphans": orphans,
    }


# --------------------------------------------------------------------- orphelins

@router.get("/orphans")
def list_orphans(db: Session = Depends(get_db)):
    return {"orphans": data_admin.find_orphans(db)}


@router.post("/orphans/purge")
def purge_orphans(db: Session = Depends(get_db)):
    result = data_admin.purge_orphans(db)
    db.commit()
    return {"ok": True, **result}


# ----------------------------------------------------------------------- classes

@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    years = {y.id: y.label for y in db.query(SchoolYear).all()}
    out = []
    for c in db.query(SchoolClass).order_by(SchoolClass.archived_at.is_(None).desc(),
                                            SchoolClass.name).all():
        out.append({
            "id": c.id, "name": c.name, "grade_level": c.grade_level,
            "school_year": years.get(c.school_year_id),
            "archived": c.archived_at is not None,
            "student_count": db.query(Student).filter_by(class_id=c.id).count(),
            "assessment_count": db.query(Assessment).filter_by(class_id=c.id).count(),
        })
    return out


@router.delete("/classes/{class_id}")
def delete_class(class_id: str, db: Session = Depends(get_db)):
    c = db.get(SchoolClass, class_id)
    if not c:
        raise HTTPException(404, "Classe inconnue")
    result = data_admin.delete_class(db, c)
    db.commit()
    return {"ok": True, **result}


# ------------------------------------------------------------------------ élèves

@router.get("/students")
def list_students(class_id: str | None = None, db: Session = Depends(get_db)):
    classes = {c.id: c.name for c in db.query(SchoolClass).all()}
    q = db.query(Student)
    if class_id:
        q = q.filter(Student.class_id == class_id)
    out = []
    for s in q.order_by(Student.last_name, Student.first_name).all():
        out.append({
            "id": s.id, "first_name": s.first_name, "last_name": s.last_name,
            "class_id": s.class_id, "class_name": classes.get(s.class_id, "—"),
            "active": s.active,
            "copy_count": db.query(Copy).filter_by(student_id=s.id).count(),
        })
    return out


@router.delete("/students/{student_id}")
def delete_student(student_id: str, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404, "Élève inconnu")
    result = data_admin.delete_student(db, s)
    db.commit()
    return {"ok": True, **result}


# ------------------------------------------------------------------------ sujets

@router.get("/assessments")
def list_assessments(class_id: str | None = None, db: Session = Depends(get_db)):
    classes = {c.id: c.name for c in db.query(SchoolClass).all()}
    q = db.query(Assessment)
    if class_id:
        q = q.filter(Assessment.class_id == class_id)
    out = []
    for a in q.order_by(Assessment.created_at.desc()).all():
        out.append({
            "id": a.id, "title": a.title, "type": a.type, "status": a.status,
            "class_id": a.class_id, "class_name": classes.get(a.class_id, "—"),
            "created_at": str(a.created_at),
            "copy_count": db.query(Copy).filter_by(assessment_id=a.id).count(),
            "scan_batch_count": db.query(ScanBatch).filter_by(assessment_id=a.id).count(),
        })
    return out


@router.delete("/assessments/{assessment_id}")
def delete_assessment(assessment_id: str, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404, "Sujet inconnu")
    result = data_admin.delete_assessment(db, a)
    db.commit()
    return {"ok": True, **result}


# ------------------------------------------------------------------- corrections

@router.get("/corrections")
def list_corrections(class_id: str | None = None, db: Session = Depends(get_db)):
    out = []
    for b in db.query(ScanBatch).order_by(ScanBatch.created_at.desc()).all():
        a = db.get(Assessment, b.assessment_id)
        if class_id and (not a or a.class_id != class_id):
            continue
        cls = db.get(SchoolClass, a.class_id) if a else None
        out.append({
            "id": b.id, "assessment_title": a.title if a else "?",
            "class_id": a.class_id if a else None,
            "class_name": cls.name if cls else "—", "status": b.status,
            "page_count": b.page_count, "created_at": str(b.created_at),
        })
    return out


@router.delete("/corrections/{batch_id}")
def delete_correction(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404, "Lot de correction inconnu")
    result = data_admin.delete_scan_batch(db, b)
    db.commit()
    return {"ok": True, **result}

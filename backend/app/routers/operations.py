"""Établissements : supervision des professeurs accompagnés et de leurs établissements,
sans facturation. Un professeur autonome n'y figure pas : il gère seul ses classes
(Paramètres, Élèves). API réutilisable par un futur MCP."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ..db import get_db
from ..deps import current_user, require_role
from ..models import (School, SchoolProgram, Country, Program, Subject, CurriculumPath,
    TeacherProfile, TeacherSchool, TeacherInvitation, User, SchoolClass, SchoolYear,
    ClassTeaching, TeachingGroup, GroupStudent, Student, Assessment, Copy, ScanBatch,
    AuditLog, CompetencyFramework, now)
from ..services.education import GRADES, GRADE_NAMES, framework_for, ensure_framework
from ..services.security import new_pseudonym

router = APIRouter(prefix="/api/operations", tags=["établissements"],
                   dependencies=[Depends(current_user)])
# Atelier (rôle corrector) : logistique et lecture. Gestion : administrateur seul.
atelier = require_role("admin", "corrector")
admin = require_role("admin")


def record(db, user, action, kind, entity_id, values):
    db.add(AuditLog(actor_id=user.id, action=action, entity_type=kind,
                    entity_id=entity_id, after_json=values))


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    return {"countries": [{"id": x.id, "name": x.name} for x in db.query(Country)],
        "programs": [{"id": x.id, "name": x.name} for x in db.query(Program)],
        "subjects": [{"id": x.id, "name": x.name} for x in db.query(Subject).filter_by(active=True)],
        "grades": [{"id": g, "name": GRADE_NAMES.get(g, g)} for g in GRADES],
        "paths": [{"id": x.id, "program_id": x.program_id, "name": x.name,
                   "kind": x.kind, "grades": x.grades, "source_url": x.source_url}
                  for x in db.query(CurriculumPath)]}


class PathIn(BaseModel):
    program_id: str
    name: str = Field(min_length=1, max_length=120)
    kind: str = "series"
    grades: list[str]
    source_url: str = ""


@router.post("/paths", dependencies=[Depends(admin)])
def add_path(body: PathIn, db: Session = Depends(get_db)):
    if not db.get(Program, body.program_id) or body.kind not in ("series", "option") or not body.grades or set(body.grades) - set(GRADES[4:]):
        raise HTTPException(422, "Programme, type ou cycles invalides")
    path = CurriculumPath(**body.model_dump())
    db.add(path); db.flush()
    subjects = db.query(Subject).filter_by(active=True).all() if path.kind == "series" else [db.get(Subject, "math")]
    for subject in subjects:
        for grade in path.grades: ensure_framework(db, path.program_id, grade, subject, path)
    db.commit()
    return {"id": path.id}


class SubjectIn(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)


@router.post("/subjects", dependencies=[Depends(admin)])
def add_subject(body: SubjectIn, db: Session = Depends(get_db)):
    if db.get(Subject, body.id):
        raise HTTPException(409, "Cette matière existe déjà")
    subject = Subject(**body.model_dump())
    db.add(subject); db.flush()
    for program in db.query(Program):
        for grade in GRADES: ensure_framework(db, program.id, grade, subject)
    for path in db.query(CurriculumPath).filter_by(kind="series"):
        for grade in path.grades: ensure_framework(db, path.program_id, grade, subject, path)
    db.commit()
    return {"id": body.id}


class SchoolIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country_id: str
    program_ids: list[str]
    city: str = ""
    address: str = ""
    contact: str = ""
    notes: str = ""
    service_active: bool = True


def save_school(db, body, school):
    if not body.name.strip() or not db.get(Country, body.country_id):
        raise HTTPException(422, "Nom et pays requis")
    if not body.program_ids or any(not db.get(Program, p) for p in body.program_ids):
        raise HTTPException(422, "Choisissez au moins un programme valide")
    used = {c.program_id for c in db.query(SchoolClass).filter_by(school_id=school.id)} if school.id else set()
    if used - set(body.program_ids):
        raise HTTPException(409, "Un programme est encore utilisé par une classe")
    for k, v in body.model_dump(exclude={"program_ids"}).items():
        setattr(school, k, v.strip() if isinstance(v, str) else v)
    db.add(school); db.flush()
    db.query(SchoolProgram).filter_by(school_id=school.id).delete()
    for program_id in set(body.program_ids):
        db.add(SchoolProgram(school_id=school.id, program_id=program_id))


@router.get("/schools")
def schools(db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "country_id": s.country_id,
             "city": s.city, "address": s.address, "contact": s.contact,
             "notes": s.notes, "service_active": s.service_active,
             "program_ids": [p.program_id for p in db.query(SchoolProgram).filter_by(school_id=s.id)],
             "class_count": db.query(SchoolClass).filter_by(school_id=s.id, archived_at=None).count()}
            for s in db.query(School).order_by(School.name)]


@router.post("/schools")
def create_school(body: SchoolIn, db: Session = Depends(get_db), user=Depends(admin)):
    s = School(); save_school(db, body, s)
    record(db, user, "school.create", "school", s.id, {"name": s.name})
    db.commit(); return {"id": s.id}


@router.put("/schools/{school_id}")
def update_school(school_id: str, body: SchoolIn, db: Session = Depends(get_db), user=Depends(admin)):
    s = db.get(School, school_id)
    if not s: raise HTTPException(404)
    save_school(db, body, s)
    record(db, user, "school.update", "school", s.id, {"service_active": s.service_active})
    db.commit(); return {"ok": True}


class TeacherIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    school_ids: list[str]
    email: str = ""
    phone: str = ""
    active: bool = True


def save_teacher(db, body, teacher):
    if not body.name.strip():
        raise HTTPException(422, "Nom requis")
    if any(not db.get(School, s) for s in body.school_ids): raise HTTPException(422, "Établissement inconnu")
    used = {s.school_id for s in db.query(SchoolClass).join(ClassTeaching, ClassTeaching.class_id == SchoolClass.id)
            .filter(ClassTeaching.teacher_id == teacher.id)} if teacher.id else set()
    if used - set(body.school_ids): raise HTTPException(409, "Le professeur enseigne encore dans cet établissement")
    for k, v in body.model_dump(exclude={"school_ids"}).items(): setattr(teacher, k, v)
    db.add(teacher); db.flush()
    db.query(TeacherSchool).filter_by(teacher_id=teacher.id).delete()
    for school_id in set(body.school_ids): db.add(TeacherSchool(teacher_id=teacher.id, school_id=school_id))
    if teacher.user_id:
        account = db.get(User, teacher.user_id)
        account.active = teacher.active
        account.display_name = teacher.name


def accompanied_teacher(db, teacher_id) -> TeacherProfile:
    t = db.get(TeacherProfile, teacher_id)
    if not t or t.service_mode != "accompanied": raise HTTPException(404, "Professeur inconnu")
    return t


@router.get("/teachers", dependencies=[Depends(atelier)])
def teachers(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "service_mode": t.service_mode, "email": t.email,
        "phone": t.phone, "active": t.active, "has_account": bool(t.user_id),
        "school_ids": [r.school_id for r in db.query(TeacherSchool).filter_by(teacher_id=t.id)]}
        for t in db.query(TeacherProfile).filter_by(service_mode="accompanied").order_by(TeacherProfile.name)]


@router.post("/teachers")
def create_teacher(body: TeacherIn, db: Session = Depends(get_db), user=Depends(admin)):
    t = TeacherProfile(service_mode="accompanied"); save_teacher(db, body, t)
    record(db, user, "teacher.create", "teacher", t.id, {"name": t.name})
    db.commit(); return {"id": t.id}


@router.put("/teachers/{teacher_id}")
def update_teacher(teacher_id: str, body: TeacherIn, db: Session = Depends(get_db), user=Depends(admin)):
    t = accompanied_teacher(db, teacher_id)
    save_teacher(db, body, t)
    record(db, user, "teacher.update", "teacher", t.id, {"active": t.active})
    db.commit(); return {"ok": True}


@router.post("/teachers/{teacher_id}/invitation")
def invite(teacher_id: str, db: Session = Depends(get_db), user=Depends(admin)):
    return issue_invitation(db, accompanied_teacher(db, teacher_id), user)


def issue_invitation(db, t: TeacherProfile, user) -> dict:
    """Lien personnel d'inscription, à transmettre manuellement."""
    if not t.active: raise HTTPException(404, "Professeur inactif ou inconnu")
    if t.user_id: raise HTTPException(409, "Ce professeur a déjà un accès")
    db.query(TeacherInvitation).filter_by(teacher_id=t.id, used_at=None).update({"used_at": now()})
    token = secrets.token_urlsafe(32)
    expiry = now() + timedelta(days=14)
    db.add(TeacherInvitation(teacher_id=t.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            expires_at=expiry, created_by=user.id))
    record(db, user, "teacher.invite", "teacher", t.id, {})
    db.commit()
    return {"path": f"/inscription#invitation={token}", "expires_at": expiry.isoformat()}


class TeachingIn(BaseModel):
    subject_id: str = "math"
    teacher_id: str


@router.put("/classes/{class_id}/teaching")
def teaching(class_id: str, body: TeachingIn, db: Session = Depends(get_db), user=Depends(admin)):
    cls = db.get(SchoolClass, class_id)
    if not cls: raise HTTPException(404)
    if not db.get(Subject, body.subject_id) or not db.query(TeacherSchool).filter_by(
        teacher_id=body.teacher_id, school_id=cls.school_id).first():
        raise HTTPException(422, "Professeur non affecté à cet établissement ou matière inconnue")
    row = db.query(ClassTeaching).filter_by(class_id=class_id, subject_id=body.subject_id).first()
    if not row:
        row = ClassTeaching(class_id=class_id, **body.model_dump()); db.add(row)
    else: row.teacher_id = body.teacher_id
    record(db, user, "teaching.assign", "class", class_id, body.model_dump())
    db.commit(); return {"id": row.id}


@router.get("/classes/{class_id}/teachings")
def teachings(class_id: str, db: Session = Depends(get_db)):
    cls = db.get(SchoolClass, class_id)
    if not cls: raise HTTPException(404)
    out = []
    for t in db.query(ClassTeaching).filter_by(class_id=class_id):
        subject = db.get(Subject, t.subject_id)
        fw = framework_for(db, cls, t.subject_id)
        groups = [{"id": g.id, "name": g.name, "path_id": g.path_id,
                   "student_ids": [s.student_id for s in db.query(GroupStudent).filter_by(group_id=g.id)]}
                  for g in db.query(TeachingGroup).filter_by(teaching_id=t.id)]
        out.append({"id": t.id, "subject_id": t.subject_id, "subject_name": subject.name if subject else t.subject_id,
                    "teacher_id": t.teacher_id, "framework_id": fw.id if fw else None, "groups": groups})
    return out


class GroupIn(BaseModel):
    teaching_id: str
    name: str = Field(min_length=1, max_length=150)
    path_id: str
    student_ids: list[str]


@router.post("/groups")  # admin, ou prof sur ses classes ; pas l'Atelier (access.check_route)
def create_group(body: GroupIn, db: Session = Depends(get_db)):
    t = db.get(ClassTeaching, body.teaching_id)
    if not t: raise HTTPException(404)
    cls = db.get(SchoolClass, t.class_id); path = db.get(CurriculumPath, body.path_id)
    if not path or path.kind != "option" or path.program_id != cls.program_id or cls.grade_level not in path.grades:
        raise HTTPException(422, "Enseignement incompatible avec cette classe")
    ids = set(body.student_ids)
    if not ids or db.query(Student).filter(Student.id.in_(ids), Student.class_id == cls.id, Student.active.is_(True)).count() != len(ids):
        raise HTTPException(422, "Sélectionnez des élèves de cette classe")
    group = TeachingGroup(teaching_id=t.id, name=body.name, path_id=path.id)
    db.add(group); db.flush()
    for student_id in ids: db.add(GroupStudent(group_id=group.id, student_id=student_id))
    db.commit(); return {"id": group.id}


class YearIn(BaseModel):
    label: str = Field(min_length=1, max_length=30)


@router.post("/years", dependencies=[Depends(admin)])
def create_year(body: YearIn, db: Session = Depends(get_db)):
    if db.query(SchoolYear).filter_by(label=body.label.strip()).first(): raise HTTPException(409, "Année existante")
    year = SchoolYear(label=body.label.strip(), active=True); db.add(year); db.commit()
    return {"id": year.id}


@router.post("/years/{year_id}/archive")
def archive_year(year_id: str, db: Session = Depends(get_db), user=Depends(admin)):
    year = db.get(SchoolYear, year_id)
    if not year: raise HTTPException(404)
    year.active = False
    db.query(SchoolClass).filter_by(school_year_id=year.id, archived_at=None).update({"archived_at": now()})
    record(db, user, "year.archive", "year", year.id, {})
    db.commit(); return {"ok": True}


class RolloverIn(BaseModel):
    year_id: str
    name: str
    grade_level: str
    series_id: str | None = None


@router.post("/classes/{class_id}/rollover")
def rollover(class_id: str, body: RolloverIn, db: Session = Depends(get_db), user=Depends(admin)):
    from .org import ClassIn, create_class
    source = db.get(SchoolClass, class_id)
    if not source: raise HTTPException(404)
    assignments = db.query(ClassTeaching).filter_by(class_id=class_id).all()
    if not assignments: raise HTTPException(422, "Affectez un professeur avant de reprendre la classe")
    result = create_class(ClassIn(name=body.name, grade_level=body.grade_level,
        school_id=source.school_id, program_id=source.program_id, series_id=body.series_id,
        school_year_id=body.year_id, teacher_profile_id=assignments[0].teacher_id,
        subject_id=assignments[0].subject_id,
        students_text="\n".join(s.name for s in source.students if s.active)), db, user)
    for old in assignments[1:]: db.add(ClassTeaching(class_id=result["id"], subject_id=old.subject_id, teacher_id=old.teacher_id))
    record(db, user, "class.rollover", "class", result["id"], {"source_class_id": source.id})
    db.commit(); return result


class TrackingIn(BaseModel):
    subjects_delivered: bool | None = None
    originals_received: bool | None = None
    returned: bool | None = None
    delivery_due_at: datetime | None = None
    note: str | None = None


@router.patch("/assessments/{assessment_id}")
def track(assessment_id: str, body: TrackingIn, db: Session = Depends(get_db), user=Depends(atelier)):
    a = db.get(Assessment, assessment_id)
    if not a: raise HTTPException(404)
    if body.subjects_delivered is True and a.status not in ("printed", "scanning", "finalized"):
        raise HTTPException(409, "Imprimez les sujets avant de les marquer livrés")
    for field, target in (("subjects_delivered", "subjects_delivered_at"), ("originals_received", "originals_received_at")):
        value = getattr(body, field)
        if value is not None: setattr(a, target, (getattr(a, target) or now()) if value else None)
    if "delivery_due_at" in body.model_fields_set: a.delivery_due_at = body.delivery_due_at
    if body.note is not None: a.operations_note = body.note
    if body.returned is not None:
        batch = db.query(ScanBatch).filter_by(assessment_id=a.id).first()
        if not batch or body.returned and (not batch.overlay_printed or batch.refine_rev > batch.overlay_rev):
            raise HTTPException(409, "Imprimez l’overlay à jour avant de restituer les copies")
        batch.overlay_distributed = body.returned
    record(db, user, "assessment.track", "assessment", a.id, body.model_dump(mode="json", exclude_unset=True))
    db.commit(); return {"ok": True}


@router.get("/dashboard", dependencies=[Depends(atelier)])
def dashboard(school_id: str | None = None, year_id: str | None = None, db: Session = Depends(get_db)):
    rows = []; counts = {}
    current_time = now()
    collection_delay = timedelta(days=7)
    for a in db.query(Assessment).order_by(Assessment.created_at.desc()):
        cls = db.get(SchoolClass, a.class_id)
        if not cls or school_id and cls.school_id != school_id or year_id and cls.school_year_id != year_id: continue
        if not year_id and cls.archived_at: continue
        school = db.get(School, cls.school_id) if cls.school_id else None
        teacher = db.get(TeacherProfile, a.teacher_profile_id) if a.teacher_profile_id else None
        # Seuls les professeurs accompagnés et leurs établissements sont supervisés.
        if not school or teacher and teacher.service_mode != "accompanied": continue
        subject = db.get(Subject, a.subject_id)
        batch = db.query(ScanBatch).filter_by(assessment_id=a.id).first()
        stage = "prepare"
        if a.status in ("queued", "generating"): stage = "generating"
        elif a.status == "error": stage = "blocked"
        elif batch:
            if batch.overlay_distributed: stage = "complete"
            elif batch.error: stage = "blocked"
            elif batch.overlay_printed and batch.overlay_rev >= batch.refine_rev: stage = "return"
            elif batch.status == "overlay_ready" and batch.overlay_rev >= batch.refine_rev: stage = "overlay"
            else: stage = "correct"
        elif a.originals_received_at: stage = "scan"
        elif a.subjects_delivered_at: stage = "collect"
        elif a.status == "printed": stage = "deliver"
        elif a.status == "ready": stage = "print"
        counts[stage] = counts.get(stage, 0) + 1
        # La livraison démarre le séjour ; récupération ou arrivée des scans
        # le termine, même si l'opérateur a omis le bouton « Copies récupérées ».
        delivered_at = a.subjects_delivered_at
        if delivered_at and delivered_at.tzinfo is None:
            delivered_at = delivered_at.replace(tzinfo=timezone.utc)
        at_school = bool(delivered_at and not a.originals_received_at and not batch)
        elapsed = current_time - delivered_at if at_school else None
        rows.append({"id": a.id, "title": a.title, "class_id": cls.id, "class_name": cls.name,
            "grade_level": cls.grade_level, "school_id": cls.school_id, "school_name": school.name if school else "Sans établissement",
            "teacher_name": teacher.name if teacher else "", "subject_name": subject.name if subject else a.subject_id,
            "subject_id": a.subject_id, "stage": stage, "status": a.status, "duplex": a.duplex,
            "service_active": (school.service_active if school else True) and (teacher.active if teacher else True),
            "service_mode": teacher.service_mode if teacher else "autonomous",
            "copy_count": db.query(Copy).filter_by(assessment_id=a.id).count(),
            "absent_count": db.query(Copy).filter_by(assessment_id=a.id, status="absent").count(),
            "batch_id": batch.id if batch else None, "error": (batch.error if batch else None) or a.error_message,
            "subjects_delivered_at": delivered_at, "at_school": at_school,
            "days_at_school": max(0, elapsed.days) if elapsed is not None else None,
            "collection_overdue": elapsed > collection_delay if elapsed is not None else False,
            "delivery_due_at": a.delivery_due_at, "note": a.operations_note, "created_at": a.created_at})
    return {"counts": counts, "assessments": rows,
            "lots_at_schools": sum(row["at_school"] for row in rows),
            "overdue_collection_count": sum(row["collection_overdue"] for row in rows)}

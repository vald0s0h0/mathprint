"""Identifiants pédagogiques stables, indépendants des libellés et du pays d'usage."""
from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..models import (Country, Program, Subject, CurriculumPath, CompetencyFramework,
                      SchoolClass, ClassTeaching, TeachingGroup, GroupStudent, Student,
                      Assessment, School, TeacherProfile)

GRADES = ("6e", "5e", "4e", "3e", "2nde", "1re", "tle")
GRADE_NAMES = {"2nde": "Seconde", "1re": "Première", "tle": "Terminale"}
COLLEGE = GRADES[:4]


def ensure_framework(db, program_id, grade, subject, path=None):
    if not db.query(CompetencyFramework).filter_by(program_id=program_id, subject_id=subject.id,
            grade_level=grade, path_id=path.id if path else None).first():
        db.add(CompetencyFramework(program_id=program_id, subject_id=subject.id,
            grade_level=grade, path_id=path.id if path else None,
            name=f"{subject.name} · {program_id.upper()} · {GRADE_NAMES.get(grade, grade)}"
                 + (f" · {path.name}" if path else ""), status="draft", source="structure"))


def seed_structure(db: Session):
    for key, name in (("FR", "France"), ("CI", "Côte d’Ivoire")):
        if not db.get(Country, key):
            db.add(Country(id=key, name=name))
    db.flush()
    for key, name, country in (("fr", "Programme français", "FR"), ("ci", "Programme ivoirien", "CI")):
        if not db.get(Program, key):
            db.add(Program(id=key, name=name, origin_country_id=country))
    if not db.get(Subject, "math"):
        db.add(Subject(id="math", name="Mathématiques"))
    db.flush()
    general_source = "https://dpfc-ci.net/documents/programmes-educatifs-et-guides-dexecution/programmes-educatifs-et-guides-dexecution-du-secondaire/"
    technical_source = "https://dpsimetfpa.com/docs/annuaire/annuaire_2021.pdf"
    paths = [("ci-A", "ci", "A", "series", ["2nde"], general_source),
             ("ci-C", "ci", "C", "series", ["2nde", "1re", "tle"], general_source)]
    paths += [(f"ci-{name}", "ci", name, "series", ["1re", "tle"], general_source)
              for name in ("A1", "A2", "D")]
    paths += [(f"ci-{name}", "ci", name, "series", list(grades), technical_source)
              for name, grades in (("AB", ("2nde",)), ("B", ("1re", "tle")),
                  ("E", ("1re", "tle")), ("F1", ("1re", "tle")),
                  ("F2", ("2nde", "1re", "tle")), ("F3", ("1re", "tle")),
                  ("F4", ("1re", "tle")), ("F7", ("1re", "tle")),
                  ("G1", ("2nde", "1re", "tle")), ("G2", ("2nde", "1re", "tle")),
                  ("T1", ("2nde",)), ("T2", ("2nde",)), ("T3", ("2nde",)))]
    fr_source = "https://www.education.gouv.fr/bo/23/Hebdo2/MENE2300946N.htm"
    paths += [(f"fr-{key}", "fr", label, "option", grades, fr_source)
              for key, label, grades in (
                  ("specialite", "Spécialité mathématiques", ["1re", "tle"]),
                  ("complementaires", "Mathématiques complémentaires", ["tle"]),
                  ("expertes", "Mathématiques expertes", ["tle"]),
                  ("specifique", "Mathématiques spécifiques", ["1re"]))]
    for key, program, name, kind, grades, source in paths:
        if not db.get(CurriculumPath, key):
            db.add(CurriculumPath(id=key, program_id=program, name=name, kind=kind,
                                  grades=grades, source_url=source))
    db.flush()
    # Les référentiels nouveaux sont vides et en brouillon. Aucun exercice inventé.
    contexts = [(p, grade, None) for p in ("fr", "ci") for grade in GRADES
                if grade in COLLEGE or p == "fr" and grade == "2nde"]
    contexts += [(p, grade, key) for key, p, _, _, grades, _ in paths for grade in grades]
    for program, grade, path in contexts:
        if not db.query(CompetencyFramework).filter_by(program_id=program, subject_id="math",
                                                      grade_level=grade, path_id=path).first():
            db.add(CompetencyFramework(program_id=program, subject_id="math", grade_level=grade,
                path_id=path, name=f"Mathématiques · {program.upper()} · {GRADE_NAMES.get(grade, grade)}"
                                  + (f" · {path.split('-', 1)[1]}" if path else ""),
                status="draft", source="structure"))
    db.commit()


def framework_for(db: Session, cls: SchoolClass, subject_id="math", group_id=None):
    path_id = cls.series_id
    if group_id:
        group = db.get(TeachingGroup, group_id)
        teaching = db.get(ClassTeaching, group.teaching_id) if group else None
        if not teaching or teaching.class_id != cls.id or teaching.subject_id != subject_id:
            raise HTTPException(422, "Groupe incompatible avec la classe et la matière")
        path_id = group.path_id
    return (db.query(CompetencyFramework)
            .filter_by(program_id=cls.program_id or "fr", subject_id=subject_id,
                       grade_level=cls.grade_level, path_id=path_id)
            .filter(CompetencyFramework.status != "archived")
            .order_by(CompetencyFramework.program_year.desc(), CompetencyFramework.id).first())


def assert_service_active(db: Session, assessment: Assessment):
    cls = db.get(SchoolClass, assessment.class_id)
    if not cls or cls.archived_at:
        raise HTTPException(409, "Classe archivée ou inaccessible")
    school = db.get(School, cls.school_id) if cls.school_id else None
    if school and not school.service_active:
        raise HTTPException(409, "Le service de cet établissement est suspendu")
    teacher = db.get(TeacherProfile, assessment.teacher_profile_id) if assessment.teacher_profile_id else None
    if teacher and not teacher.active:
        raise HTTPException(409, "Le service de ce professeur est suspendu")


def assessment_students(db: Session, assessment: Assessment):
    q = db.query(Student).filter_by(class_id=assessment.class_id, active=True)
    if assessment.group_id:
        q = q.filter(Student.id.in_(db.query(GroupStudent.student_id)
                                   .filter_by(group_id=assessment.group_id)))
    return q.order_by(Student.order_index, Student.id).all()


def require_student_subject(db: Session, student: Student, subject_id: str):
    if not db.get(Subject, subject_id):
        raise HTTPException(422, "Matière inconnue")
    if db.info.get("teacher_scope") and not db.query(ClassTeaching).filter_by(
            class_id=student.class_id, subject_id=subject_id).first():
        raise HTTPException(404, "Enseignement inaccessible")

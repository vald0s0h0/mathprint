"""Périmètre de lecture/écriture d'une session HTTP ou d'un dépôt de scans.

Les sous-requêtes Core sont volontairement indépendantes des critères ORM :
elles ne se réappliquent pas récursivement. Les workers de génération utilisent
un sujet déjà autorisé ; le worker d'ingestion réinstalle le périmètre de l'auteur.
"""
from fastapi import HTTPException
from sqlalchemy import event, select, or_, and_, tuple_
from sqlalchemy.orm import with_loader_criteria
from .. import models as m
from ..config import settings

# Zone de l'interface d'où part une requête (en-tête ZONE_HEADER, posé par le
# frontend selon l'onglet ouvert) :
#   • user    : onglets du haut (Dashboard, Sujets, Corrections, Élèves…) ;
#   • atelier : onglet Atelier (logistique du centre) ;
#   • admin   : onglets d'administration.
# Pour l'administrateur, la zone « user » est filtrée sur SON profil de
# professeur, comme pour n'importe quel prof : il y teste ce que voit un prof.
# C'est un confort de lecture, pas une frontière de sécurité (l'admin a tous
# les droits dans les autres zones) : les droits, eux, suivent le rôle.
ZONE_HEADER = "X-KN-Zone"
ZONES = ("user", "atelier", "admin")
ROLES = ("admin", "corrector", "teacher")   # corrector = rôle « Atelier » à l'écran


def is_centre() -> bool:
    return settings.edition != "autonome"


def scoped_as_teacher(user, zone: str | None) -> bool:
    return user.role == "teacher" or (user.role == "admin" and zone == "user")


def scope_session(db, user, zone: str | None = None):
    if not scoped_as_teacher(user, zone) or db.info.get("teacher_scope"):
        return
    profile = db.query(m.TeacherProfile).filter_by(user_id=user.id, active=True).first()
    teacher_id = profile.id if profile else "__none__"
    t = m.ClassTeaching.__table__.c
    cl = m.SchoolClass.__table__.c
    a = m.Assessment.__table__.c
    st = m.Student.__table__.c
    comp = m.Competency.__table__.c
    fw = m.CompetencyFramework.__table__.c
    teaches = select(t.id).where(t.teacher_id == teacher_id)
    classes = select(t.class_id).where(t.teacher_id == teacher_id)
    # La fiche portée par le sujet est une trace de son destinataire ; les
    # droits actuels suivent l'affectation classe + matière.
    assessments = select(a.id).where(a.class_id.in_(classes), a.subject_id.in_(
        select(t.subject_id).where(t.teacher_id == teacher_id, t.class_id == a.class_id)))
    students = select(st.id).where(st.class_id.in_(classes))
    copies = select(m.Copy.__table__.c.id).where(m.Copy.__table__.c.assessment_id.in_(assessments))
    items = select(m.CopyItem.__table__.c.id).where(m.CopyItem.__table__.c.copy_id.in_(copies))
    pages = select(m.DocumentPage.__table__.c.id).where(m.DocumentPage.__table__.c.copy_id.in_(copies))
    zones = select(m.ResponseZone.__table__.c.id).where(m.ResponseZone.__table__.c.page_id.in_(pages))
    responses = select(m.StudentResponse.__table__.c.id).where(m.StudentResponse.__table__.c.copy_item_id.in_(items))
    decisions = select(m.GradingDecision.__table__.c.id).where(m.GradingDecision.__table__.c.response_id.in_(responses))
    batches = select(m.ScanBatch.__table__.c.id).where(m.ScanBatch.__table__.c.assessment_id.in_(assessments))
    student_subjects = (select(st.id, t.subject_id).select_from(m.Student.__table__)
        .join(m.ClassTeaching.__table__, t.class_id == st.class_id).where(t.teacher_id == teacher_id))
    student_comps = (select(st.id, comp.id).select_from(m.Student.__table__)
        .join(m.ClassTeaching.__table__, t.class_id == st.class_id)
        .join(m.CompetencyFramework.__table__, fw.subject_id == t.subject_id)
        .join(m.Competency.__table__, comp.framework_id == fw.id).where(t.teacher_id == teacher_id))
    school_ids = select(m.TeacherSchool.__table__.c.school_id).where(m.TeacherSchool.__table__.c.teacher_id == teacher_id)
    rules = [(m.SchoolClass, m.SchoolClass.id.in_(classes)),
        (m.ClassTeaching, m.ClassTeaching.teacher_id == teacher_id),
        (m.TeachingGroup, m.TeachingGroup.teaching_id.in_(teaches)),
        (m.GroupStudent, m.GroupStudent.group_id.in_(select(m.TeachingGroup.__table__.c.id).where(m.TeachingGroup.__table__.c.teaching_id.in_(teaches)))),
        (m.Student, m.Student.id.in_(students)),
        (m.Assessment, m.Assessment.id.in_(assessments)),
        (m.Copy, m.Copy.id.in_(copies)), (m.CopyItem, m.CopyItem.id.in_(items)),
        (m.DocumentPage, m.DocumentPage.id.in_(pages)),
        (m.ResponseZone, m.ResponseZone.id.in_(zones)),
        (m.StudentResponse, m.StudentResponse.id.in_(responses)),
        (m.GradingDecision, m.GradingDecision.id.in_(decisions)),
        (m.ManualReview, m.ManualReview.decision_id.in_(decisions)),
        (m.ScanBatch, m.ScanBatch.id.in_(batches)),
        (m.ScannedPage, m.ScannedPage.batch_id.in_(batches)),
        (m.OcrAttempt, m.OcrAttempt.zone_id.in_(zones)),
        (m.CopyResult, m.CopyResult.assessment_id.in_(assessments)),
        (m.CopyItemResult, m.CopyItemResult.copy_item_id.in_(items)),
        (m.Annotation, m.Annotation.copy_id.in_(copies)),
        (m.StudentCompetencyState, tuple_(m.StudentCompetencyState.student_id, m.StudentCompetencyState.competency_id).in_(student_comps)),
        (m.CompetencyEvidence, tuple_(m.CompetencyEvidence.student_id, m.CompetencyEvidence.competency_id).in_(student_comps)),
        (m.StudentLearningEvent, m.StudentLearningEvent.copy_id.in_(copies)),
        (m.StudentLevel, tuple_(m.StudentLevel.student_id, m.StudentLevel.subject_id).in_(student_subjects)),
        (m.StudentReport, tuple_(m.StudentReport.student_id, m.StudentReport.subject_id).in_(student_subjects)),
        (m.SandboxUpload, m.SandboxUpload.uploaded_by == user.id),
        (m.Job, or_(m.Job.assessment_id.in_(assessments), m.Job.created_by == user.id)),
        (m.School, m.School.id.in_(school_ids)),
        (m.TeacherProfile, m.TeacherProfile.id == teacher_id),
        (m.TeacherSchool, m.TeacherSchool.teacher_id == teacher_id)]
    db.info["teacher_scope"] = teacher_id
    db.info["actor_id"] = user.id
    def apply_scope(execute_state):
        if execute_state.is_select or execute_state.is_update or execute_state.is_delete:
            execute_state.statement = execute_state.statement.options(*[
                with_loader_criteria(model, criterion, include_aliases=True) for model, criterion in rules])
    event.listen(db, "do_orm_execute", apply_scope)


# Préfixes réservés à l'administrateur, quelle que soit l'édition.
_ADMIN_ONLY = ("/api/admin/", "/api/indigo", "/api/data/", "/api/settings/", "/api/costs",
               "/api/system/")
_CALIBRATION = "/api/system/calibration"
# Gestion des établissements, profs, classes, élèves : admin seul, le rôle
# Atelier n'y a qu'un accès en lecture. Un prof gère ses propres classes
# (autonome) : le filtrage de session borne ce qu'il peut toucher.
_MANAGEMENT = ("/api/operations/", "/api/classes", "/api/students/")
# Seule écriture de /api/operations laissée à l'Atelier : le suivi logistique
# d'un sujet (livré, récupéré, restitué, note, date de livraison).
_ATELIER_TRACKING = "/api/operations/assessments/"
_READ = ("GET", "HEAD")


def check_edition(request):
    """Édition autonome (NAS) : ni établissements accompagnés, ni Atelier, ni
    comptes Atelier. 404 plutôt que 403 : ces routes n'existent pas ici."""
    if is_centre():
        return
    path, method = request.url.path, request.method
    if path.startswith("/api/operations/") and not (path == "/api/operations/catalog" and method in _READ):
        raise HTTPException(404, "Indisponible sur cette instance")
    if path == "/api/admin/users/correctors":
        raise HTTPException(404, "Indisponible sur cette instance")


def check_route(user, request):
    """Droits par rôle. admin : tout. corrector (Atelier) : la logistique
    (lots, reprise OCR, impression, distribution, calibration, suivi des
    sujets), en lecture seule sur la gestion. teacher : ses classes."""
    check_edition(request)
    if user.role == "admin":
        return
    path, method = request.url.path, request.method
    if path.startswith(_CALIBRATION):
        # La calibration appartient à l'Atelier ; sans Atelier (édition
        # autonome), le prof calibre lui-même son imprimante et son scanner.
        if user.role == "corrector" or not is_centre():
            return
        raise HTTPException(403, "Réservé à l’Atelier")
    if path.startswith(_ADMIN_ONLY):
        raise HTTPException(403, "Réservé à l’administrateur")
    if path.startswith("/api/content/") and method not in _READ:
        if path not in ("/api/content/card-preview.png", "/api/content/figures/render"):
            raise HTTPException(403, "Seul l’administrateur peut modifier les exercices")
    if path == "/api/assessments/exercises/ai-prepare":
        raise HTTPException(403, "Seul l’administrateur peut générer des exercices")
    if user.role == "corrector" and method not in _READ and path.startswith(_MANAGEMENT):
        if not (path.startswith(_ATELIER_TRACKING) and method == "PATCH"):
            raise HTTPException(403, "Réservé à l’administrateur")


def check_path_resources(db, request):
    models = {"assessment_id": m.Assessment, "class_id": m.SchoolClass,
        "student_id": m.Student, "batch_id": m.ScanBatch, "copy_id": m.Copy,
        "report_id": m.StudentReport, "response_id": m.StudentResponse,
        "review_id": m.ManualReview, "scanned_page_id": m.ScannedPage,
        "school_id": m.School, "teacher_id": m.TeacherProfile}
    for key, model in models.items():
        value = request.path_params.get(key)
        if value and not db.get(model, value):
            raise HTTPException(404, "Ressource inconnue ou inaccessible")

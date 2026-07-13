"""Onglet Paramètres → Données (§9.6) : vue d'ensemble et suppression
définitive des classes, élèves, sujets et corrections — y compris les
fichiers stockés sur le volume. Contrairement aux suppressions "douces"
existantes (archivage de classe, désactivation d'élève), tout ici est
irréversible : pas de corbeille, pas d'audit de restauration.
"""
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Annotation, Assessment, Copy, CopyItem, CompetencyEvidence,
    CompetencyStateHistory, DocumentPage, FileObject, GradingDecision, Job,
    ManualReview, OcrAttempt, ResponseZone, SchoolClass, ScanBatch,
    ScannedPage, Student, StudentCompetencyState, StudentLevel, StudentReport,
    StudentResponse,
)


def _delete_file_objects(db: Session, owner_type: str, owner_ids: list[str]) -> int:
    rows = (db.query(FileObject)
            .filter(FileObject.owner_type == owner_type, FileObject.owner_id.in_(owner_ids))
            .all()) if owner_ids else []
    for fo in rows:
        Path(fo.storage_path).unlink(missing_ok=True)
    n = len(rows)
    if owner_ids:
        db.query(FileObject).filter(
            FileObject.owner_type == owner_type, FileObject.owner_id.in_(owner_ids),
        ).delete(synchronize_session=False)
    return n


def _delete_copies(db: Session, copy_ids: list[str]) -> None:
    """Supprime des copies et tout ce qui en dérive (items, pages, zones,
    réponses, décisions, revues, annotations). Ne touche pas aux lots de
    scan eux-mêmes : d'autres copies du même lot peuvent y survivre (cas
    d'une suppression d'élève au sein d'une classe intacte)."""
    if not copy_ids:
        return
    item_ids = [i.id for i in db.query(CopyItem.id).filter(CopyItem.copy_id.in_(copy_ids)).all()]
    page_ids = [p.id for p in db.query(DocumentPage.id).filter(DocumentPage.copy_id.in_(copy_ids)).all()]
    zone_ids = [z.id for z in db.query(ResponseZone.id).filter(ResponseZone.item_id.in_(item_ids)).all()]
    response_ids = [r.id for r in
                    db.query(StudentResponse.id).filter(StudentResponse.copy_item_id.in_(item_ids)).all()]
    decision_ids = [d.id for d in
                    db.query(GradingDecision.id).filter(GradingDecision.response_id.in_(response_ids)).all()]

    db.query(ManualReview).filter(ManualReview.decision_id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(GradingDecision).filter(GradingDecision.id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(StudentResponse).filter(StudentResponse.id.in_(response_ids)).delete(synchronize_session=False)
    db.query(OcrAttempt).filter(OcrAttempt.zone_id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(Annotation).filter(Annotation.copy_id.in_(copy_ids)).delete(synchronize_session=False)
    db.query(ScannedPage).filter(ScannedPage.page_id.in_(page_ids)).delete(synchronize_session=False)
    db.query(ResponseZone).filter(ResponseZone.id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(DocumentPage).filter(DocumentPage.id.in_(page_ids)).delete(synchronize_session=False)
    db.query(CopyItem).filter(CopyItem.id.in_(item_ids)).delete(synchronize_session=False)

    cache_dirs = {c[0] for c in db.query(Assessment.id).join(
        Copy, Copy.assessment_id == Assessment.id).filter(Copy.id.in_(copy_ids)).all()}
    for aid in cache_dirs:
        for cid in copy_ids:
            (settings.data_dir / "assessments" / aid / "generated" / "copies" / f"{cid}.pdf") \
                .unlink(missing_ok=True)

    db.query(Copy).filter(Copy.id.in_(copy_ids)).delete(synchronize_session=False)


def delete_assessment(db: Session, assessment: Assessment) -> dict:
    """Supprime un sujet (brouillon ou déjà généré/corrigé) et l'intégralité
    de ses données : copies, lots de scan, fichiers PDF/images sur le
    volume."""
    copy_ids = [c.id for c in db.query(Copy.id).filter_by(assessment_id=assessment.id).all()]
    batch_ids = [b.id for b in db.query(ScanBatch.id).filter_by(assessment_id=assessment.id).all()]

    _delete_copies(db, copy_ids)
    # lots blocked/non identifiés : pas rattachés à une page, donc pas couverts
    # par _delete_copies (qui filtre par page_id) — nettoyage explicite ici.
    db.query(ScannedPage).filter(ScannedPage.batch_id.in_(batch_ids)).delete(synchronize_session=False)
    _delete_file_objects(db, "scan_batch", batch_ids)
    db.query(ScanBatch).filter(ScanBatch.id.in_(batch_ids)).delete(synchronize_session=False)
    _delete_file_objects(db, "assessment", [assessment.id])
    db.query(Job).filter_by(assessment_id=assessment.id).delete(synchronize_session=False)

    n_copies, n_batches = len(copy_ids), len(batch_ids)
    db.delete(assessment)
    shutil.rmtree(settings.data_dir / "assessments" / assessment.id, ignore_errors=True)
    return {"copies": n_copies, "scan_batches": n_batches}


def delete_student(db: Session, student: Student) -> dict:
    """Supprime définitivement un élève : copies (et leurs corrections),
    historique pédagogique (compétences, niveaux, comptes rendus)."""
    copy_ids = [c.id for c in db.query(Copy.id).filter_by(student_id=student.id).all()]
    _delete_copies(db, copy_ids)
    db.query(StudentCompetencyState).filter_by(student_id=student.id).delete(synchronize_session=False)
    db.query(CompetencyEvidence).filter_by(student_id=student.id).delete(synchronize_session=False)
    db.query(CompetencyStateHistory).filter_by(student_id=student.id).delete(synchronize_session=False)
    db.query(StudentLevel).filter_by(student_id=student.id).delete(synchronize_session=False)
    db.query(StudentReport).filter_by(student_id=student.id).delete(synchronize_session=False)
    n_copies = len(copy_ids)
    db.delete(student)
    return {"copies": n_copies}


def delete_class(db: Session, cls: SchoolClass) -> dict:
    """Supprime une classe : tous ses sujets (et leurs corrections) puis
    tous ses élèves (y compris ceux déjà désactivés)."""
    assessments = db.query(Assessment).filter_by(class_id=cls.id).all()
    n_copies = n_batches = 0
    for a in assessments:
        r = delete_assessment(db, a)
        n_copies += r["copies"]
        n_batches += r["scan_batches"]
    students = db.query(Student).filter_by(class_id=cls.id).all()
    for s in students:
        delete_student(db, s)
    n_students = len(students)
    db.delete(cls)
    return {"assessments": len(assessments), "students": n_students,
            "copies": n_copies, "scan_batches": n_batches}


def delete_scan_batch(db: Session, batch: ScanBatch) -> dict:
    """Supprime un seul lot de scans (une « correction ») sans toucher au
    sujet ni aux autres lots : réponses, décisions, revues et fichiers
    (scan original + recadrages) de ce lot uniquement. Les copies
    concernées repassent au statut "generated"."""
    assessment = db.get(Assessment, batch.assessment_id)
    scanned_pages = db.query(ScannedPage).filter_by(batch_id=batch.id).all()
    page_ids = [sp.page_id for sp in scanned_pages if sp.page_id]

    zone_ids = [z.id for z in db.query(ResponseZone.id).filter(ResponseZone.page_id.in_(page_ids)).all()]
    response_ids = [r.id for r in
                    db.query(StudentResponse.id).filter(StudentResponse.zone_id.in_(zone_ids)).all()]
    decision_ids = [d.id for d in
                    db.query(GradingDecision.id).filter(GradingDecision.response_id.in_(response_ids)).all()]

    db.query(ManualReview).filter(ManualReview.decision_id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(GradingDecision).filter(GradingDecision.id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(StudentResponse).filter(StudentResponse.id.in_(response_ids)).delete(synchronize_session=False)
    db.query(OcrAttempt).filter(OcrAttempt.zone_id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(Annotation).filter(Annotation.zone_id.in_(zone_ids)).delete(synchronize_session=False)

    for dp in (db.query(DocumentPage).filter(DocumentPage.id.in_(page_ids)).all() if page_ids else []):
        copy = db.get(Copy, dp.copy_id)
        if copy and copy.status in ("graded", "finalized"):
            copy.status = "generated"
            copy.appreciation_json = None

    db.query(ScannedPage).filter_by(batch_id=batch.id).delete(synchronize_session=False)
    _delete_file_objects(db, "scan_batch", [batch.id])

    if assessment:
        derived_dir = settings.data_dir / "assessments" / assessment.id / "scans" / "derived"
        for zid in zone_ids:
            (derived_dir / f"{zid}.png").unlink(missing_ok=True)
        remaining = (db.query(ScanBatch)
                     .filter(ScanBatch.assessment_id == assessment.id, ScanBatch.id != batch.id)
                     .count())
        if remaining == 0 and assessment.status == "finalized":
            assessment.status = "printed"
            shutil.rmtree(settings.data_dir / "assessments" / assessment.id / "overlays",
                          ignore_errors=True)

    db.delete(batch)
    return {"pages": len(scanned_pages), "responses": len(response_ids)}

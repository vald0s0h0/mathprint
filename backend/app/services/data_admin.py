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
    Annotation, Assessment, Competency, Copy, CopyItem, CopyItemResult,
    CopyResult, CompetencyEvidence, CompetencyStateHistory, DocumentPage,
    FileObject, GeneratedExercise, GradingDecision, Job, ManualReview,
    OcrAttempt, ResponseZone, SchoolClass, ScanBatch, ScannedPage, Student,
    StudentCompetencyState, StudentLevel, StudentReport, StudentResponse,
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

    # résultats consolidés (§ barème) : dérivés des copies, ils en deviennent
    # orphelins si on ne les supprime pas ici (CopyItemResult d'abord, il pointe
    # vers CopyResult).
    result_ids = [r.id for r in db.query(CopyResult.id).filter(CopyResult.copy_id.in_(copy_ids)).all()]

    db.query(ManualReview).filter(ManualReview.decision_id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(GradingDecision).filter(GradingDecision.id.in_(decision_ids)).delete(synchronize_session=False)
    db.query(StudentResponse).filter(StudentResponse.id.in_(response_ids)).delete(synchronize_session=False)
    db.query(OcrAttempt).filter(OcrAttempt.zone_id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(Annotation).filter(Annotation.copy_id.in_(copy_ids)).delete(synchronize_session=False)
    db.query(ScannedPage).filter(ScannedPage.page_id.in_(page_ids)).delete(synchronize_session=False)
    db.query(ResponseZone).filter(ResponseZone.id.in_(zone_ids)).delete(synchronize_session=False)
    db.query(DocumentPage).filter(DocumentPage.id.in_(page_ids)).delete(synchronize_session=False)
    db.query(CopyItemResult).filter(CopyItemResult.copy_result_id.in_(result_ids)).delete(synchronize_session=False)
    db.query(CopyItemResult).filter(CopyItemResult.copy_item_id.in_(item_ids)).delete(synchronize_session=False)
    db.query(CopyResult).filter(CopyResult.copy_id.in_(copy_ids)).delete(synchronize_session=False)
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

    affected_copy_ids: set[str] = set()
    for dp in (db.query(DocumentPage).filter(DocumentPage.id.in_(page_ids)).all() if page_ids else []):
        copy = db.get(Copy, dp.copy_id)
        if copy and copy.status in ("graded", "finalized"):
            copy.status = "generated"
            copy.appreciation_json = None
            affected_copy_ids.add(copy.id)

    # résultats consolidés de ces copies : ils faisaient partie de la correction
    # supprimée, ils repartiront à la prochaine finalisation (dérivés, jamais
    # corrigés à la main) — les garder laisserait un suivi fantôme.
    if affected_copy_ids:
        cids = list(affected_copy_ids)
        result_ids = [r.id for r in db.query(CopyResult.id).filter(CopyResult.copy_id.in_(cids)).all()]
        db.query(CopyItemResult).filter(
            CopyItemResult.copy_result_id.in_(result_ids)).delete(synchronize_session=False)
        db.query(CopyResult).filter(CopyResult.copy_id.in_(cids)).delete(synchronize_session=False)

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


# ============================================================ données orphelines

# Chaque entrée : (libellé, colonne FK enfant, colonne clé parent, FK nullable ?).
# Une ligne est orpheline quand sa FK non nulle ne pointe vers aucun parent
# existant. L'ordre va des feuilles vers les racines pour qu'une passe de purge
# nettoie un maximum d'un coup ; les orphelins transitifs restants (un parent
# supprimé rend ses enfants orphelins) sont rattrapés par une 2e passe.
_ORPHAN_CHECKS: list[tuple] = [
    ("Revues de correction sans décision", ManualReview.decision_id, GradingDecision.id, False),
    ("Décisions sans réponse élève", GradingDecision.response_id, StudentResponse.id, False),
    ("Réponses élève sans exercice de copie", StudentResponse.copy_item_id, CopyItem.id, False),
    ("Lectures OCR sans zone", OcrAttempt.zone_id, ResponseZone.id, False),
    ("Zones sans page", ResponseZone.page_id, DocumentPage.id, False),
    ("Zones sans exercice de copie", ResponseZone.item_id, CopyItem.id, False),
    ("Résultats d'exercice sans résultat de copie", CopyItemResult.copy_result_id, CopyResult.id, False),
    ("Résultats de copie sans copie", CopyResult.copy_id, Copy.id, False),
    ("Résultats de copie sans sujet", CopyResult.assessment_id, Assessment.id, False),
    ("Annotations sans copie", Annotation.copy_id, Copy.id, False),
    ("Pages scannées sans lot", ScannedPage.batch_id, ScanBatch.id, False),
    ("Pages de copie sans copie", DocumentPage.copy_id, Copy.id, False),
    ("Exercices de copie sans copie", CopyItem.copy_id, Copy.id, False),
    ("Lots de scan sans sujet", ScanBatch.assessment_id, Assessment.id, False),
    ("Copies sans sujet", Copy.assessment_id, Assessment.id, False),
    ("Copies sans élève", Copy.student_id, Student.id, False),
    ("Sujets sans classe", Assessment.class_id, SchoolClass.id, False),
    ("Élèves sans classe", Student.class_id, SchoolClass.id, True),
    ("Preuves de compétence sans élève", CompetencyEvidence.student_id, Student.id, False),
    ("Historique de compétence sans élève", CompetencyStateHistory.student_id, Student.id, False),
    ("Niveaux sans élève", StudentLevel.student_id, Student.id, False),
    ("Comptes rendus sans élève", StudentReport.student_id, Student.id, False),
    ("Exercices de banque sans compétence", GeneratedExercise.competency_id, Competency.id, False),
    ("Jobs sans sujet", Job.assessment_id, Assessment.id, True),
]

# owner_type de FileObject dont l'owner_id EST une clé de table (donc vérifiable).
# "sesamaths_manual" est exclu : son owner_id est un niveau ("5e"), pas un id.
_FILE_OWNERS = {"assessment": Assessment, "scan_batch": ScanBatch}


def _orphan_query(db: Session, fk_col, parent_pk, nullable: bool):
    q = db.query(fk_col.class_).filter(fk_col.notin_(db.query(parent_pk)))
    if nullable:
        q = q.filter(fk_col.isnot(None))
    return q


def _orphan_file_query(db: Session, owner_type: str, parent_model):
    return (db.query(FileObject)
            .filter(FileObject.owner_type == owner_type,
                    FileObject.owner_id.notin_(db.query(parent_model.id))))


def find_orphans(db: Session) -> list[dict]:
    """Recense les lignes pointant vers un parent disparu, table par table —
    pour un ménage propre de la base sans deviner où sont les restes. Ne
    supprime rien : c'est purge_orphans qui agit."""
    out = []
    for label, fk_col, parent_pk, nullable in _ORPHAN_CHECKS:
        n = _orphan_query(db, fk_col, parent_pk, nullable).count()
        if n:
            out.append({"label": label, "count": n})
    files = sum(_orphan_file_query(db, ot, pm).count() for ot, pm in _FILE_OWNERS.items())
    if files:
        out.append({"label": "Fichiers sans propriétaire (PDF/images)", "count": files})
    return out


def purge_orphans(db: Session) -> dict:
    """Supprime toutes les lignes orphelines (et les fichiers orphelins sur le
    volume). Plusieurs passes : supprimer un orphelin peut en révéler d'autres
    (ses propres enfants). S'arrête dès qu'une passe ne supprime plus rien."""
    total = 0
    for _ in range(6):
        pass_deleted = 0
        for _label, fk_col, parent_pk, nullable in _ORPHAN_CHECKS:
            pass_deleted += _orphan_query(db, fk_col, parent_pk, nullable).delete(
                synchronize_session=False)
        for owner_type, parent_model in _FILE_OWNERS.items():
            for fo in _orphan_file_query(db, owner_type, parent_model).all():
                Path(fo.storage_path).unlink(missing_ok=True)
                db.delete(fo)
                pass_deleted += 1
        db.flush()
        total += pass_deleted
        if not pass_deleted:
            break
    return {"deleted": total}

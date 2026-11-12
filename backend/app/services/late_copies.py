"""Copies tardives : nouvel enchaînement de scans sur un sujet déjà corrigé.

Un sujet reste UN lot (règle de services.scan_intake), mais chaque dépôt arrivé
après la validation est un JOB distinct, une « vague » : son propre enchaînement,
sa ligne « À faire », son PDF d'overlay et ses états imprimé / distribué.

L'enchaînement scanné reste la source de vérité de l'impression : une feuille de
la pile garde toujours sa place. Celle qui n'a rien à recevoir (déjà corrigée,
version non retenue) y reçoit une page sans correction, jamais une suppression.

Une page d'élève déjà enregistrée peut revenir pour trois raisons :
- même feuille rescannée : rien de neuf, elle n'ouvre pas de job à elle seule ;
- copie restée vide et sans note : la nouvelle la remplace d'office ;
- l'élève a déjà une note (même 0) : le professeur tranche, rien ne change avant.
"""
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Annotation, AuditLog, Assessment, CompetencyEvidence, Copy, CopyItem, CopyItemResult,
    CopyResult, DocumentPage, GradingDecision, ManualReview, OcrAttempt, ResponseZone,
    ScanBatch, ScannedPage, Student, StudentResponse,
)
from . import error_journal, scoring, worker_cv
from .forgetting import replay_states

# Feuilles présentes dans l'enchaînement sans rien à y corriger.
PASS_THROUGH = ("late_duplicate", "superseded", "conflict_rejected")
# Pages que le pipeline ne relit pas : leur sort est déjà décidé.
SKIPPED_BY_PIPELINE = (*PASS_THROUGH, "conflict")

# Comparaison de deux scans : on tolère l'épaisseur du trait et un léger décalage,
# on retient une coche fine. Double seuil : un trait n'est nouveau que s'il est
# franchement sombre là où l'autre scan n'a aucune trace, même pâle ; une écriture
# proche du seuil apparaîtrait sinon d'un scan à l'autre. Calé sur de vraies pages
# scannées, rescans simulés (flou, décalage, rotation, luminosité, bruit, JPEG).
_INK_GRAY = 165
_TRACE_GRAY = 215
_TOLERANCE_PX = 9
_MIN_STROKE_PX = 12
_MIN_CHANGED_PX = 40

_REWOUND_STEPS = ("identified", "registered", "cropped", "ocr_complete", "ocr_review_pending",
                  "ocr_confirmed", "graded", "review_pending", "finalized", "overlay_ready")


def _derived_dir(assessment_id: str) -> Path:
    return settings.data_dir / "assessments" / assessment_id / "scans" / "derived"


def _ink_masks(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(encre franche, moindre trace) d'une zone, sans le rouge de l'overlay."""
    gray = cv2.cvtColor(worker_cv.dropout_filter(crop), cv2.COLOR_BGR2GRAY)
    return (gray < _INK_GRAY).astype(np.uint8), (gray < _TRACE_GRAY).astype(np.uint8)


def _strokes_px(mask: np.ndarray) -> int:
    """Pixels des traits réels : les poussières isolées du scanner sont écartées."""
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = stats[1:count, cv2.CC_STAT_AREA]
    return int(areas[areas >= _MIN_STROKE_PX].sum())


def _answers_changed(new: np.ndarray, old: np.ndarray, zones: list[ResponseZone]) -> bool:
    if new.shape[:2] != old.shape[:2]:
        new = cv2.resize(new, (old.shape[1], old.shape[0]), interpolation=cv2.INTER_AREA)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_TOLERANCE_PX, _TOLERANCE_PX))
    changed = 0
    for zone in zones:
        box = (zone.x_pt, zone.y_pt, zone.w_pt, zone.h_pt, 0.0)
        new_crop, old_crop = worker_cv.crop_zone(new, *box), worker_cv.crop_zone(old, *box)
        if not new_crop.size or not old_crop.size:
            continue
        new_ink, new_trace = _ink_masks(new_crop)
        old_ink, old_trace = _ink_masks(old_crop)
        added = new_ink & (1 - cv2.dilate(old_trace, kernel))
        removed = old_ink & (1 - cv2.dilate(new_trace, kernel))
        changed += max(_strokes_px(added), _strokes_px(removed))
    return changed >= _MIN_CHANGED_PX


def _page_is_blank(img: np.ndarray, zones: list[ResponseZone]) -> bool:
    for zone in zones:
        crop = worker_cv.crop_zone(img, zone.x_pt, zone.y_pt, zone.w_pt, zone.h_pt, 0.0)
        if crop.size and worker_cv.ink_ratio(worker_cv.dropout_filter(crop)) >= worker_cv.BLANK_INK_THRESHOLD:
            return False
    return True


def redeposit_kind(db: Session, page_id: str, img: np.ndarray) -> str | None:
    """None pour une page jamais vue, sinon same | replace | conflict."""
    page = db.get(DocumentPage, page_id)
    copy = db.get(Copy, page.copy_id) if page else None
    if copy is None:
        return None
    derived = _derived_dir(copy.assessment_id)
    zones = db.query(ResponseZone).filter_by(page_id=page_id).all()

    # Version pas encore tranchée, ou tranchée et pas encore lue : l'élève a déjà
    # une feuille en cours, redéposer la même n'ouvre rien de plus.
    pending = (db.query(ScannedPage)
               .join(ScanBatch, ScannedPage.batch_id == ScanBatch.id)
               .filter(ScanBatch.assessment_id == copy.assessment_id,
                       ScannedPage.status.in_(("conflict", "pending")),
                       ScannedPage.replaces_page_id == page_id).all())
    for row in pending:
        previous = cv2.imread(str(derived / f"source-{row.source_index}.png"), cv2.IMREAD_COLOR)
        if previous is None or not _answers_changed(img, previous, zones):
            return "same"

    registered = db.query(ScannedPage).filter(
        ScannedPage.page_id == page_id,
        ScannedPage.status.in_(("registered", "graded", "finalized")),
    ).first()
    if registered is None:
        return "conflict" if pending else None
    old = cv2.imread(str(derived / f"page-{page_id}.png"), cv2.IMREAD_COLOR)
    # Sans ancienne image, rien ne prouve une différence : ne touche à aucune note.
    if old is None or not _answers_changed(img, old, zones):
        return "same"
    if db.query(CopyResult).filter_by(copy_id=copy.id).first():
        return "conflict"
    return "replace" if copy.status == "absent" or _page_is_blank(old, zones) else "conflict"


# --------------------------------------------------------------- vagues

def wave_state(batch: ScanBatch, wave: int) -> dict:
    return dict((batch.waves_json or {}).get(str(wave)) or {})


def set_wave_state(batch: ScanBatch, wave: int, **changes) -> None:
    waves = dict(batch.waves_json or {})
    waves[str(wave)] = {**(waves.get(str(wave)) or {}), **changes}
    batch.waves_json = waves


def open_wave(batch: ScanBatch) -> int:
    batch.overlay_wave = (batch.overlay_wave or 0) + 1
    set_wave_state(batch, batch.overlay_wave, printed=False, distributed=False,
                   created_at=datetime.now(timezone.utc).isoformat())
    return batch.overlay_wave


def rewind_progress(batch: ScanBatch) -> None:
    """Le lot repasse par la correction : une relance reprend alors la lecture, et
    non la seule génération de l'overlay (cf. routers.scans.retry_batch)."""
    progress = dict(batch.progress_json or {})
    for step in _REWOUND_STEPS:
        progress.pop(step, None)
    batch.progress_json = progress
    batch.status = "uploaded"


def is_late(db: Session, assessment_id: str) -> bool:
    assessment = db.get(Assessment, assessment_id)
    return bool(assessment and assessment.status == "finalized")


def mark_wave_printed(db: Session, assessment_id: str, wave: int) -> None:
    if wave:
        for batch in db.query(ScanBatch).filter_by(assessment_id=assessment_id).all():
            if str(wave) in (batch.waves_json or {}):
                set_wave_state(batch, wave, printed=True)
    else:
        (db.query(ScanBatch).filter(ScanBatch.assessment_id == assessment_id)
         .update({ScanBatch.overlay_printed: True}, synchronize_session=False))


def note_refined(db: Session, batch: ScanBatch, copy: Copy) -> None:
    """Retient les vagues où cette copie figure : régénérer l'overlay ne doit
    rouvrir l'impression que de ces piles-là, pas de toutes."""
    waves = {wave for (wave,) in
             db.query(ScannedPage.wave).join(DocumentPage, ScannedPage.page_id == DocumentPage.id)
               .filter(ScannedPage.batch_id == batch.id, DocumentPage.copy_id == copy.id,
                       ScannedPage.status == "registered")}
    state = dict(batch.waves_json or {})
    state["refined"] = sorted(set(state.get("refined") or []) | {w or 0 for w in waves})
    batch.waves_json = state


def reopen_refined_waves(batch: ScanBatch) -> None:
    """Après un overlay régénéré : ces piles sont à réimprimer, les autres non."""
    state = dict(batch.waves_json or {})
    refined = state.pop("refined", None)
    batch.waves_json = state
    if refined is None:
        batch.overlay_printed = False
        return
    for wave in refined:
        if wave:
            set_wave_state(batch, wave, printed=False)
        else:
            batch.overlay_printed = False


# ------------------------------------------------- remplacement d'une page

def _note_text(result: CopyResult | None) -> str | None:
    if result is None or result.note is None:
        return None
    return f"{scoring.format_points(result.note)}/{result.note_base}"


def release_page(db: Session, batch: ScanBatch, page_id: str, *,
                 actor_id: str | None = None) -> None:
    """Retire la correction d'UNE page pour que sa nouvelle version soit lue et
    notée comme au premier passage. L'ancienne ligne garde sa place dans son
    enchaînement : la feuille correspondante existe toujours dans cette pile."""
    page = db.get(DocumentPage, page_id)
    copy = db.get(Copy, page.copy_id)
    zones = db.query(ResponseZone).filter_by(page_id=page_id).all()
    zone_ids = [z.id for z in zones]
    item_ids = [z.item_id for z in zones]
    responses = (db.query(StudentResponse).filter(StudentResponse.zone_id.in_(zone_ids)).all()
                 if zone_ids else [])
    response_ids = [r.id for r in responses]
    decisions = (db.query(GradingDecision).filter(GradingDecision.response_id.in_(response_ids)).all()
                 if response_ids else [])
    decision_ids = [d.id for d in decisions]

    result = db.query(CopyResult).filter_by(copy_id=copy.id).first()
    # Décisions effacées : le journal garde ce qui était noté (RM-006).
    db.add(AuditLog(
        actor_id=actor_id, action="late_copy_replaced", entity_type="copy", entity_id=copy.id,
        before_json={"note": _note_text(result), "page_id": page_id,
                     "decisions": [{"response_id": d.response_id, "score": d.score,
                                    "max_score": d.max_score, "source": d.source}
                                   for d in decisions]},
        after_json={"wave": batch.overlay_wave or 0}))

    # La page repart en lecture : ses erreurs ne comptent plus jusqu'à sa nouvelle
    # correction, mais restent dans l'historique.
    error_journal.withdraw_items(db, [z.item_id for z in zones if z.item_id])
    if decision_ids:
        db.query(ManualReview).filter(ManualReview.decision_id.in_(decision_ids)).delete(synchronize_session=False)
        db.query(GradingDecision).filter(GradingDecision.id.in_(decision_ids)).delete(synchronize_session=False)
    if response_ids:
        db.query(StudentResponse).filter(StudentResponse.id.in_(response_ids)).delete(synchronize_session=False)
    if zone_ids:
        db.query(OcrAttempt).filter(OcrAttempt.zone_id.in_(zone_ids)).delete(synchronize_session=False)
        db.query(Annotation).filter(Annotation.zone_id.in_(zone_ids)).delete(synchronize_session=False)

    if result is not None:
        db.query(CopyItemResult).filter_by(copy_result_id=result.id).delete(synchronize_session=False)
        db.delete(result)

    competency_ids: list[str] = []
    if item_ids:
        evidences = db.query(CompetencyEvidence).filter(CompetencyEvidence.item_id.in_(item_ids)).all()
        competency_ids = sorted({e.competency_id for e in evidences})
        for evidence in evidences:
            db.delete(evidence)
    db.flush()
    if competency_ids:
        replay_states(db, copy.student_id, competency_ids)

    for row in db.query(ScannedPage).filter_by(batch_id=batch.id, page_id=page_id, status="registered"):
        row.status = "superseded"
    copy.status = "read"
    copy.appreciation_json = None
    db.flush()


# -------------------------------------------------- doubles corrections

def _conflict_view(db: Session, row: ScannedPage) -> dict:
    page = db.get(DocumentPage, row.replaces_page_id)
    copy = db.get(Copy, page.copy_id)
    student = db.get(Student, copy.student_id)
    result = db.query(CopyResult).filter_by(copy_id=copy.id).first()
    zones = db.query(ResponseZone).filter_by(page_id=page.id).all()
    previous = (db.query(ScannedPage)
                .filter_by(batch_id=row.batch_id, page_id=page.id, status="registered").first())
    old = cv2.imread(str(_derived_dir(copy.assessment_id) / f"page-{page.id}.png"), cv2.IMREAD_COLOR)
    return {
        "id": row.id, "wave": row.wave or 0, "student": student.name if student else "?",
        "page_no": page.page_no, "existing_note": _note_text(result),
        # Une note de 0 sur une copie vide appelle presque toujours le remplacement.
        "existing_blank": bool(old is not None and _page_is_blank(old, zones)),
        "existing_image": f"/api/scans/scanned-pages/{previous.id}/image" if previous else None,
        "new_image": f"/api/scans/scanned-pages/{row.id}/image",
    }


def list_conflicts(db: Session, batch: ScanBatch, *, wave: int | None = None) -> list[dict]:
    query = db.query(ScannedPage).filter_by(batch_id=batch.id, status="conflict")
    if wave is not None:
        query = query.filter_by(wave=wave)
    return [_conflict_view(db, row) for row in query.order_by(ScannedPage.source_index).all()]


def keep_existing(db: Session, batch: ScanBatch, row: ScannedPage, *,
                  actor_id: str | None = None) -> None:
    """La note reste. La feuille reste aussi dans la pile : elle passera sans
    correction."""
    row.status = "conflict_rejected"
    db.add(AuditLog(actor_id=actor_id, action="late_copy_rejected", entity_type="scanned_page",
                    entity_id=row.id, after_json={"page_id": row.replaces_page_id}))
    close_wave_if_empty(db, batch, row.wave)


def replace_existing(db: Session, batch: ScanBatch, row: ScannedPage, *,
                     actor_id: str | None = None) -> None:
    release_page(db, batch, row.replaces_page_id, actor_id=actor_id)
    # Une version précédente pas encore lue cède la place : une seule sera notée.
    for earlier in db.query(ScannedPage).filter_by(batch_id=batch.id, status="pending",
                                                   replaces_page_id=row.replaces_page_id):
        earlier.status = "superseded"
        close_wave_if_empty(db, batch, earlier.wave)
    row.status = "pending"
    rewind_progress(batch)
    db.flush()


# ------------------------------------------------------------- affichage

def sheets_to_correct(db: Session, batch: ScanBatch, wave: int) -> list[ScannedPage]:
    return (db.query(ScannedPage)
            .filter(ScannedPage.batch_id == batch.id, ScannedPage.wave == wave,
                    ScannedPage.dismissed.is_(False),
                    ScannedPage.status.notin_(PASS_THROUGH))
            .order_by(ScannedPage.source_index).all())


def close_wave_if_empty(db: Session, batch: ScanBatch, wave: int) -> None:
    """Un job où plus rien n'est à corriger se referme seul."""
    db.flush()
    if wave and not sheets_to_correct(db, batch, wave):
        set_wave_state(batch, wave, printed=True, distributed=True, closed=True)


def wave_overlay_ready(db: Session, batch: ScanBatch, wave: int) -> bool:
    """Tous les scans de la vague sont lus et son PDF est publié. L'état du lot ne
    suffit pas : il peut être finalisé avec une feuille de la vague encore à lire,
    dont l'overlay ne porterait alors aucune correction."""
    from .overlay_queue import overlay_file

    if any(row.status == "pending" for row in sheets_to_correct(db, batch, wave)):
        return False
    return overlay_file(batch.assessment_id, wave).exists()


def wave_copy_ids(db: Session, batch: ScanBatch) -> dict[int, set[str]]:
    """Copies de chaque enchaînement tardif (vague 1, 2…) : ces copies ont leur
    propre job et ne comptent plus dans le lot principal."""
    out: dict[int, set[str]] = {}
    for wave in range(1, (batch.overlay_wave or 0) + 1):
        ids: set[str] = set()
        for row in sheets_to_correct(db, batch, wave):
            page_id = row.page_id or row.replaces_page_id
            page = db.get(DocumentPage, page_id) if page_id else None
            if page:
                ids.add(page.copy_id)
        out[wave] = ids
    return out


def scope_copy_ids(db: Session, batch: ScanBatch, wave: int | None) -> tuple[set[str], bool] | None:
    """Filtre de copies pour la file de correction : None = tout le sujet ;
    wave=N → (copies de la vague N, inclusion) ; wave=0 → (copies tardives, exclusion)."""
    if wave is None:
        return None
    by_wave = wave_copy_ids(db, batch)
    if wave == 0:
        return set().union(*by_wave.values()) if by_wave else set(), False
    return by_wave.get(wave, set()), True


def main_student_names(db: Session, batch: ScanBatch) -> list[str]:
    """Élèves du lot de scan principal (vague 0), dans l'ordre de la pile."""
    rows = (db.query(ScannedPage)
            .filter(ScannedPage.batch_id == batch.id, ScannedPage.wave == 0,
                    ScannedPage.dismissed.is_(False), ScannedPage.page_id.is_not(None))
            .order_by(ScannedPage.source_index).all())
    names: list[str] = []
    for row in rows:
        page = db.get(DocumentPage, row.page_id)
        copy = db.get(Copy, page.copy_id) if page else None
        student = db.get(Student, copy.student_id) if copy else None
        if student and student.name not in names:
            names.append(student.name)
    return names


def job_segments(stage: str, *, printed: bool) -> list[dict]:
    """Pipeline visuel d'un enchaînement tardif (mêmes étapes que le lot principal)."""
    order = ["scan", "read", "ocrize", "correct", "validate", "done", "overlay_print"]
    labels = {"scan": "Scan déposé", "read": "Lecture des copies", "ocrize": "OCRisation",
              "correct": "Correction", "validate": "Validation", "done": "Copies corrigées",
              "overlay_print": "Overlay imprimé" if printed else "Overlay à imprimer"}
    # étape en cours (index) et sa couleur, selon l'état du job
    current, color = {
        "conflict": (3, "orange"), "error": (3, "red"), "ocr_review": (2, "orange"),
        "review": (3, "orange"), "processing": (3, "blue"), "print": (6, "orange"),
    }.get(stage, (len(order), "green"))
    return [{"phase": phase, "label": labels[phase],
             "state": "green" if i < current else (color if i == current else "gray")}
            for i, phase in enumerate(order)]


def jobs_view(db: Session, batch: ScanBatch) -> list[dict]:
    """Une entrée par enchaînement tardif : c'est elle qui fait une ligne à part."""
    from .pipeline import pending_ocr_units

    if not batch.overlay_wave:
        return []
    pending_ocr = pending_ocr_units(db, batch.assessment_id)
    open_items = {item_id for (item_id,) in
                  db.query(StudentResponse.copy_item_id)
                    .join(GradingDecision, GradingDecision.response_id == StudentResponse.id)
                    .join(ManualReview, ManualReview.decision_id == GradingDecision.id)
                    .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
                    .join(Copy, CopyItem.copy_id == Copy.id)
                    .filter(Copy.assessment_id == batch.assessment_id,
                            ManualReview.resolved_at.is_(None))}
    jobs = []
    for wave in range(1, batch.overlay_wave + 1):
        state = wave_state(batch, wave)
        rows = sheets_to_correct(db, batch, wave)
        names: list[str] = []
        item_ids: set[str] = set()
        for row in rows:
            page = db.get(DocumentPage, row.page_id or row.replaces_page_id) if (
                row.page_id or row.replaces_page_id) else None
            copy = db.get(Copy, page.copy_id) if page else None
            student = db.get(Student, copy.student_id) if copy else None
            if student and student.name not in names:
                names.append(student.name)
            if page:
                item_ids |= {z.item_id for z in db.query(ResponseZone).filter_by(page_id=page.id)}
        conflicts = sum(row.status == "conflict" for row in rows)
        pages = len({row.page_id or row.replaces_page_id for row in rows})
        ocr = sum(pending_ocr.get(i, 0) for i in item_ids)
        reviews = len(item_ids & open_items)
        ready = wave_overlay_ready(db, batch, wave)
        if state.get("closed"):
            stage = "done"
        elif conflicts:
            stage = "conflict"
        elif batch.error and not ready:
            stage = "error"
        elif ocr:
            stage = "ocr_review"
        elif reviews:
            stage = "review"
        elif not ready:
            stage = "processing"
        elif not state.get("printed"):
            stage = "print"
        elif not state.get("distributed"):
            stage = "distribute"
        else:
            stage = "done"
        jobs.append({
            "wave": wave, "created_at": state.get("created_at"), "stage": stage,
            "students": names, "sheets": len(rows), "conflicts": conflicts,
            "page_count": pages, "segments": job_segments(stage, printed=bool(state.get("printed"))),
            "pending_ocr": ocr, "pending_reviews": reviews,
            "overlay_printed": bool(state.get("printed")),
            "overlay_distributed": bool(state.get("distributed")),
        })
    return jobs

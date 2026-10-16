"""« Affiner la correction » d'un lot terminé (§9.5), avant impression de
l'overlay.

Le professeur parcourt les copies scannées AVEC leur overlay de correction,
élève par élève, et corrige une note en réécrivant simplement la réponse de
l'élève (cases cochées, liaisons, cases d'un tableau, réponse courte) : le
moteur déterministe renote alors la réponse, exactement comme au premier
passage. Les réponses que seul un humain peut noter (raisonnements, tracés)
reçoivent des points directement.

Chaque modification :
- ajoute une lecture `teacher_ocr` (la plus récente : elle fait foi pour la
  note ET pour les marques d'overlay) et une décision professeur (append-only,
  RM-006) ;
- reconsolide la copie (CopyResult), ses preuves de compétence et la courbe
  d'oubli de l'élève, sans toucher aux autres copies ;
- incrémente `ScanBatch.refine_rev` : l'overlay imprimable est alors marqué
  « à régénérer » jusqu'au prochain `pipeline.build_overlays`.

L'aperçu montre la page scannée et ses marques telles qu'elles seront
imprimées (`pipeline.copy_overlay_pages` + `pdfgen.render_page_marks`, le
même dessin que l'overlay).
"""
import hashlib
import json
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import fitz  # PyMuPDF
import numpy as np
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import (
    Assessment, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItem,
    CopyResult, ExerciseCompetency, GradingDecision, ManualReview, OcrAttempt,
    ResponseZone, ScanBatch, Student, StudentCompetencyState, StudentResponse,
)
from . import grading as grader
from . import scoring
from .appreciation import compute_competency_progress
from .forgetting import apply_evidence
from .pdfgen import PAGE_H, PAGE_W, render_page_marks
from .pipeline import (
    _copies_in_scan_order, _grading_contract_issue, _recognized_answer_issue,
    _scan_page_positions, copy_overlay_pages, correction_ink, effective_reading,
)

# Résolution de l'aperçu : assez fine pour lire une coche au zoom plein écran,
# assez légère (~300 Ko en JPEG) pour précharger les copies suivantes.
PREVIEW_DPI = 150
PREVIEW_JPEG_QUALITY = 84

# Types de réponse dont le professeur réécrit la RÉPONSE (renotée par le
# moteur) ; les autres (raisonnement rédigé, tracé) se notent en points.
TEXT_TYPES = ("integer", "decimal", "rational", "expression", "text")


class RefineError(ValueError):
    """Modification refusée, message lisible par le professeur."""


def batch_is_refinable(batch: ScanBatch) -> bool:
    """Seul un lot finalisé (copies corrigées générées) s'affine : avant, la
    modale « Corriger » et la finalisation automatique font foi."""
    return batch.status == "overlay_ready" or "finalized" in (batch.progress_json or {})


def overlay_stale(batch: ScanBatch) -> bool:
    return (batch.refine_rev or 0) > (batch.overlay_rev or 0)


def edit_kind(item: CopyItem) -> str:
    """Éditeur proposé pour une réponse : qcm | grid | matching | cells | text
    (réponse réécrite puis renotée) ou points (note directe)."""
    rtype = item.response_type
    if rtype.startswith("qcm"):
        return "qcm"
    if rtype == "checkbox_grid":
        return "grid"
    if rtype == "matching":
        return "matching"
    if rtype in ("table_fill", "multi_blank"):
        return "cells"
    if (rtype == "short_text"
            and (item.grading_json or {}).get("comparator") != "manual"
            and (item.expected_json or {}).get("type") in TEXT_TYPES):
        return "text"
    return "points"


# ------------------------------------------------------------------ lecture

def _latest_decision(db: Session, response_id: str) -> GradingDecision | None:
    return (db.query(GradingDecision).filter_by(response_id=response_id)
            .order_by(GradingDecision.created_at.desc()).first())


def _hotspot(z: dict) -> dict:
    """Rectangle cliquable d'une réponse, normalisé 0..1 depuis le coin HAUT
    gauche de la page : la zone de réponse et sa bande de note."""
    x0, y0 = float(z["x_pt"]), float(z["y_pt"])
    x1, y1 = x0 + float(z["w_pt"]), y0 + float(z["h_pt"])
    strip = z.get("strip")
    if strip and strip.get("w_pt"):
        x0 = min(x0, float(strip["x_pt"]))
        y0 = min(y0, float(strip["y_pt"]))
        x1 = max(x1, float(strip["x_pt"]) + float(strip["w_pt"]))
        y1 = max(y1, float(strip["y_pt"]) + float(strip["h_pt"]))
    return {"x": x0 / PAGE_W, "y": (PAGE_H - y1) / PAGE_H,
            "w": (x1 - x0) / PAGE_W, "h": (y1 - y0) / PAGE_H}


def _render_payload(page: dict) -> dict:
    """La page telle que le moteur de rendu la dessine (sans repères internes)."""
    return {k: v for k, v in page.items()
            if k not in ("page_id", "background") and not k.startswith("_")}


def _page_version(page: dict, color: str | None) -> str:
    """Empreinte de TOUT ce qui se voit sur l'aperçu d'une page : marques, note,
    appréciation, encre et fond scanné. Clé des caches (navigateur et serveur)."""
    bg = page.get("background")
    bg_sig = None
    if bg:
        try:
            st = Path(bg).stat()
            bg_sig = [bg, st.st_mtime_ns, st.st_size]
        except OSError:
            bg_sig = None
    raw = json.dumps({"page": _render_payload(page), "color": color, "bg": bg_sig},
                     sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _note_texts(result: CopyResult | None) -> tuple[str | None, str | None]:
    if result is None:
        return None, None
    note = (f"{scoring.format_points(result.note)}/{result.note_base}"
            if result.note is not None else None)
    points = (f"{scoring.format_points(result.points_earned)}/"
              f"{scoring.format_points(result.points_total)}")
    return note, points


def copy_view(db: Session, assessment: Assessment, copy: Copy,
              positions: dict[str, int], color: str | None) -> dict | None:
    """Une copie scannée pour la modale : élève, note, et ses pages dans l'ordre
    ADF, chacune avec sa version d'aperçu et ses réponses cliquables."""
    pages = copy_overlay_pages(db, assessment, copy, positions, persist=False)
    if not pages:
        return None
    student = db.get(Student, copy.student_id)
    note, points = _note_texts(db.query(CopyResult).filter_by(copy_id=copy.id).first())
    out_pages = []
    for k, page in enumerate(pages):
        out_pages.append({
            "page_id": page["page_id"], "index": k, "page_no": page["_page_no"],
            "version": _page_version(page, color),
            "has_scan": page["background"] is not None,
            "zones": [{
                "response_id": z["response_id"], "sequence": z["sequence"],
                "response_type": z["response_type"],
                "points": round(float(z["score"]), 3),
                "bareme": round(float(z["max_score"]), 3),
                "full": bool(z["full_credit"]),
                "rect": _hotspot(z),
            } for z in page["page_zones"]],
        })
    return {"copy_id": copy.id, "student": student.name if student else "?",
            "note": note, "points": points, "pages": out_pages}


def batch_overview(db: Session, batch: ScanBatch) -> dict:
    """Liste LÉGÈRE des copies scannées du lot (élève, note), dans l'ordre de la
    pile — celui de l'overlay imprimé. Les pages d'une copie (marques, versions
    d'aperçu) se chargent à la demande (`copy_view`) : calculer les marques
    de toute une classe retarderait l'ouverture de plusieurs secondes."""
    assessment = db.get(Assessment, batch.assessment_id)
    copies = _copies_in_scan_order(db, batch, assessment.id)
    ids = [c.id for c in copies]
    scanned = {copy_id for (copy_id,) in
               db.query(CopyItem.copy_id)
                 .join(StudentResponse, StudentResponse.copy_item_id == CopyItem.id)
                 .filter(CopyItem.copy_id.in_(ids), StudentResponse.zone_id.is_not(None))
                 .distinct()} if ids else set()
    results = ({r.copy_id: r for r in
                db.query(CopyResult).filter(CopyResult.copy_id.in_(ids))} if ids else {})
    students = ({s.id: s for s in db.query(Student).filter(
        Student.id.in_([c.student_id for c in copies]))} if copies else {})
    out = []
    for copy in copies:
        if copy.id not in scanned:
            continue
        student = students.get(copy.student_id)
        note, points = _note_texts(results.get(copy.id))
        out.append({"copy_id": copy.id, "student": student.name if student else "?",
                    "note": note, "points": points})
    return {"batch_id": batch.id, "assessment_id": assessment.id,
            "title": assessment.title, "copies": out}


def single_copy_view(db: Session, batch: ScanBatch, copy: Copy) -> dict | None:
    assessment = db.get(Assessment, batch.assessment_id)
    return copy_view(db, assessment, copy, _scan_page_positions(db, batch),
                     correction_ink(db))


# ------------------------------------------------------------------ aperçu

_PREVIEW_CACHE: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()
_PREVIEW_CACHE_MAX = 48          # ~15 Mo : une classe entière en aller-retour
_PREVIEW_LOCK = threading.Lock()


def _composite(marks_pdf: bytes, background: str | None) -> bytes:
    """Marques rastérisées posées sur le scan en MULTIPLICATION — exactement ce
    que fait l'encre de l'overlay imprimée sur la copie : le crayon de l'élève
    reste visible sous le rouge. Sans scan : marques sur page blanche."""
    with fitz.open(stream=marks_pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=PREVIEW_DPI, alpha=False)
        marks = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3]
        marks = cv2.cvtColor(marks, cv2.COLOR_RGB2BGR)
    base = cv2.imread(background, cv2.IMREAD_COLOR) if background else None
    if base is None:
        out = marks
    else:
        base = cv2.resize(base, (marks.shape[1], marks.shape[0]),
                          interpolation=cv2.INTER_AREA)
        out = cv2.multiply(base, marks, scale=1 / 255)
    ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
    if not ok:
        raise RuntimeError("Encodage de l'aperçu impossible")
    return encoded.tobytes()


def page_preview(db: Session, batch: ScanBatch, copy: Copy,
                 page_id: str) -> tuple[bytes, str] | None:
    """Aperçu JPEG (scan + overlay) d'une page de copie, et sa version. Rendu à
    la demande depuis l'état courant de la base, mis en cache par version."""
    assessment = db.get(Assessment, batch.assessment_id)
    positions = _scan_page_positions(db, batch)
    page = next((p for p in copy_overlay_pages(db, assessment, copy, positions,
                                               persist=False)
                 if p["page_id"] == page_id), None)
    if page is None:
        return None
    color = correction_ink(db)
    version = _page_version(page, color)
    key = (page_id, version)
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(key)
            return cached, version
    jpeg = _composite(render_page_marks(_render_payload(page), color), page["background"])
    with _PREVIEW_LOCK:
        _PREVIEW_CACHE[key] = jpeg
        _PREVIEW_CACHE.move_to_end(key)
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_MAX:
            _PREVIEW_CACHE.popitem(last=False)
    return jpeg, version


# ------------------------------------------------------------------ réponse

_MATH_WRAPPERS = (("\\(", "\\)"), ("\\[", "\\]"), ("$$", "$$"), ("$", "$"))


def _unwrap_math(value: str) -> str:
    """« \\( 3 \\times 6 \\) » -> « 3 \\times 6 » : la lecture Mathpix entoure
    chaque formule de délimiteurs, illisibles à corriger à la main. Sans effet
    sur la note — grading.normalize les retire de toute façon."""
    s = value.strip()
    for open_, close in _MATH_WRAPPERS:
        if (s.startswith(open_) and s.endswith(close) and len(s) > len(open_) + len(close)
                and close not in s[len(open_):-len(close)]):
            return s[len(open_):-len(close)].strip()
    return s


def current_answer(db: Session, resp: StudentResponse, item: CopyItem) -> dict:
    """Réponse de l'élève telle que LUE (lecture effective : OCR, CV ou
    reprise professeur) — pas la sélection fictive réécrite par une ancienne
    correction case par case pour caler les marques."""
    kind = edit_kind(item)
    reading = effective_reading(db, resp.zone_id)
    raw = (reading.raw_json or {}) if reading else {}
    if kind == "qcm":
        return {"selected_choices": list(resp.selected_choices or [])}
    if kind == "grid":
        sel = raw.get("grid_selected")
        if sel is None:
            sel = resp.selected_choices or []
        n = len((item.grading_json or {}).get("rows") or [])
        sel = [int(v) if isinstance(v, int) else -1 for v in list(sel)[:n]]
        return {"selected_choices": sel + [-1] * (n - len(sel))}
    if kind == "matching":
        pairs = raw.get("pairs")
        if pairs is None:
            pairs = resp.selected_pairs or []
        return {"selected_pairs": [list(p) for p in pairs
                                   if isinstance(p, (list, tuple)) and len(p) == 2]}
    if kind == "cells":
        n = len(grader.fillable_cells(item.grading_json or {}))
        cells = [("" if c is None else _unwrap_math(str(c)))
                 for c in (raw.get("cells") or [])][:n]
        return {"cells": cells + [""] * (n - len(cells))}
    text = resp.final_text or ((reading.text or "") if reading else "")
    return {"text": _unwrap_math(text)}


def _record_teacher_decision(db: Session, resp: StudentResponse, *, score: float,
                             max_score: float, reason_code: str,
                             evidence: dict) -> GradingDecision:
    """Décision professeur append-only (RM-006), comme routers.scans
    ._apply_resolution : la précédente passe « revised » et une revue encore
    ouverte sur la réponse est close."""
    old = _latest_decision(db, resp.id)
    decision = GradingDecision(
        response_id=resp.id, source="teacher", score=score, max_score=max_score,
        confidence=1.0, tier="D", reason_code=reason_code, status="validated",
        evidence_json={"previous_decision": old.id if old else None,
                       "via": "refine", **evidence})
    if old is not None:
        old.status = "revised"
    db.add(decision)
    open_reviews = (db.query(ManualReview)
                    .join(GradingDecision, ManualReview.decision_id == GradingDecision.id)
                    .filter(GradingDecision.response_id == resp.id,
                            ManualReview.resolved_at.is_(None)).all())
    for review in open_reviews:
        review.resolution = reason_code
        review.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return decision


def apply_answer(db: Session, resp: StudentResponse, item: CopyItem,
                 zone: ResponseZone, payload: dict) -> dict:
    """Réécrit la réponse de l'élève puis la renote avec le moteur
    déterministe. La réécriture du professeur fait foi : une valeur que le
    moteur ne sait pas reconnaître juste (texte non lu comme le corrigé,
    nombre illisible) compte faux — `uncertain` invite alors à vérifier, et
    les points restent ajustables à la main."""
    kind = edit_kind(item)
    if kind == "points":
        raise RefineError("Cette réponse se note en points, pas en la réécrivant")
    issue = _grading_contract_issue(db, item, zone)
    if issue:
        raise RefineError(f"Exercice non recorrigeable automatiquement ({issue}) : "
                          "attribuez les points à la main")
    policy = item.grading_json or {}
    previous = effective_reading(db, resp.zone_id)
    # `cell_credits` : verdicts d'une ancienne correction case par case. La
    # nouvelle lecture les remplace — les marques se recalculent sur les cases.
    raw = {k: v for k, v in ((previous.raw_json or {}) if previous else {}).items()
           if k != "cell_credits"}
    text = ""
    selected = cells = pairs = None
    if kind == "qcm":
        selected = payload.get("selected_choices")
        if not isinstance(selected, list):
            raise RefineError("Cases cochées manquantes")
        selected = sorted({int(i) for i in selected})
        raw["selected"] = selected
    elif kind == "grid":
        selected = payload.get("selected_choices")
        if not isinstance(selected, list):
            raise RefineError("Colonnes cochées manquantes")
        selected = [int(i) for i in selected]
        raw["grid_selected"] = selected
    elif kind == "matching":
        pairs = payload.get("selected_pairs")
        if not isinstance(pairs, list):
            raise RefineError("Liaisons manquantes")
        pairs = [[int(p[0]), int(p[1])] for p in pairs
                 if isinstance(p, (list, tuple)) and len(p) == 2]
        raw["pairs"] = pairs
    elif kind == "cells":
        cells = payload.get("cells")
        if not isinstance(cells, list):
            raise RefineError("Cases du tableau manquantes")
        cells = ["" if c is None else str(c).strip() for c in cells]
        raw.update(cells=cells, cell_latex=list(cells),
                   cell_confidences=[1.0] * len(cells))
    else:
        value = payload.get("text")
        if not isinstance(value, str):
            raise RefineError("Réponse manquante")
        text = value.strip()
    issue = _recognized_answer_issue(item, selected=selected, cell_texts=cells,
                                     selected_pairs=pairs)
    if issue:
        raise RefineError(f"Réponse incohérente avec l'exercice ({issue})")

    if kind in ("qcm", "grid"):
        resp.selected_choices = selected
    elif kind == "matching":
        resp.selected_pairs = pairs
    elif kind == "text":
        resp.final_text = text
    db.add(OcrAttempt(zone_id=resp.zone_id, provider="teacher_ocr", raw_json=raw,
                      text=text, latex=text, confidence=1.0))

    verdict = grader.grade(item.expected_json or {}, policy, text, 1.0, selected,
                           cell_texts=cells, selected_pairs=pairs)
    evidence = {"verdict": verdict["reason_code"]}
    # Les modales de correction relisent ces verdicts (scans._cell_units /
    # _grid_units) : elles rouvrent ainsi sur la note posée ici.
    if kind == "cells":
        evidence["cell_verdicts"] = [c or 0.0 for c in grader.table_credits(policy, cells)]
    elif kind == "grid":
        evidence["cell_verdicts"] = [1.0 if ok else 0.0
                                     for ok in grader.grid_rows_ok(policy, selected)]
    decision = _record_teacher_decision(
        db, resp, score=float(verdict["score"]), max_score=float(verdict["max_score"]),
        reason_code="teacher_answer", evidence=evidence)
    return {"decision": decision,
            "uncertain": verdict["tier"] not in ("A", "B"),
            "reason": verdict["reason_code"]}


def apply_points(db: Session, resp: StudentResponse, item: CopyItem,
                 ratio: float) -> GradingDecision:
    """Note directe d'une réponse : `ratio` du barème (0..1)."""
    if not 0.0 <= ratio <= 1.0:
        raise RefineError("Points hors barème")
    old = _latest_decision(db, resp.id)
    max_score = (old.max_score if old and old.max_score
                 else float((item.grading_json or {}).get("max_score") or 1.0))
    return _record_teacher_decision(db, resp, score=ratio * max_score,
                                    max_score=max_score,
                                    reason_code="teacher_set_ratio", evidence={})


# ------------------------------------------------------- suivi de l'élève

def _replay_states(db: Session, student_id: str, competency_ids: list[str]) -> None:
    """Reconstruit la courbe d'oubli de l'élève sur ces compétences en rejouant
    ses preuves dans l'ordre des devoirs (même règle que
    data_admin.purge_correction_tracking, restreinte aux compétences touchées :
    un état ne dépend que des preuves de SA compétence)."""
    (db.query(CompetencyStateHistory)
     .filter(CompetencyStateHistory.student_id == student_id,
             CompetencyStateHistory.competency_id.in_(competency_ids))
     .delete(synchronize_session=False))
    (db.query(StudentCompetencyState)
     .filter(StudentCompetencyState.student_id == student_id,
             StudentCompetencyState.competency_id.in_(competency_ids))
     .delete(synchronize_session="fetch"))
    db.flush()
    evidences = (db.query(CompetencyEvidence)
                 .filter(CompetencyEvidence.student_id == student_id,
                         CompetencyEvidence.competency_id.in_(competency_ids))
                 .order_by(CompetencyEvidence.observed_at, CompetencyEvidence.id).all())
    for ev in evidences:
        apply_evidence(db, ev)
        db.flush()


def refresh_copy(db: Session, copy: Copy, item: CopyItem,
                 decision: GradingDecision) -> None:
    """Reporte la nouvelle note d'UNE réponse sur le suivi de l'élève : résultat
    consolidé (note du carnet), preuves de compétence de l'exercice, courbe
    d'oubli et barres de progrès de l'appréciation. La synthèse rédigée (LLM)
    est conservée telle quelle."""
    assessment = db.get(Assessment, copy.assessment_id)
    result = scoring.compute_copy_result(db, copy, assessment)
    ratio = (max(0.0, min(1.0, decision.score / decision.max_score))
             if decision.max_score else 0.0)
    changed: list[str] = []
    observed_at = scoring.assessment_date(assessment, copy)
    for ec in db.query(ExerciseCompetency).filter_by(exercise_id=item.catalog_id):
        evidences = (db.query(CompetencyEvidence)
                     .filter_by(item_id=item.id, competency_id=ec.competency_id).all())
        if not evidences:
            db.add(CompetencyEvidence(
                student_id=copy.student_id, competency_id=ec.competency_id,
                item_id=item.id, mode=assessment.type, score_ratio=ratio,
                observed_at=observed_at, difficulty=item.difficulty,
                weight=ec.weight * ec.evidence_strength))
            changed.append(ec.competency_id)
            continue
        for ev in evidences:
            if abs((ev.score_ratio or 0.0) - ratio) > 1e-9:
                ev.score_ratio = ratio
                if ec.competency_id not in changed:
                    changed.append(ec.competency_id)
    db.flush()
    appreciation = dict(copy.appreciation_json or {})
    if changed:
        _replay_states(db, copy.student_id, changed)
        appreciation["progress"] = compute_competency_progress(
            db, assessment.id, copy.student_id)
        appreciation.setdefault("synthesis", "")
        copy.appreciation_json = appreciation
    if result is not None:
        result.appreciation = appreciation.get("synthesis") or ""
        result.progress_json = {"progress": appreciation.get("progress") or []}
    db.flush()


def mark_changed(db: Session, batch_id: str) -> None:
    """Overlay désormais à régénérer. Incrément en SQL (jamais via l'instance
    chargée) : un rendu concurrent ne peut pas l'écraser."""
    db.execute(update(ScanBatch).where(ScanBatch.id == batch_id)
               .values(refine_rev=ScanBatch.refine_rev + 1))

"""Pipeline de correction d'un lot de scans — machine d'états §6.1.

uploaded → split → identified → registered → cropped → ocr_complete → graded
→ review_pending → finalized → overlay_ready

Deux chemins :
- lot avec fichier PDF déposé : chemin réel (worker_cv : raster, QR, homographie,
  crops, dropout, QCM) ; l'OCR texte passe par Mathpix (ou son repli sans clé) ;
- lot sans fichier (batch créé sans scan, cas des tests) : zones traitées comme si
  elles avaient été recadrées, pour exercer tout le chemin décisionnel (tiers A-E).
"""
import hashlib
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib.units import mm
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Annotation, Assessment, Copy, CopyItem, CopyResult, DocumentPage, FileObject,
    GradingDecision, ManualReview, OcrAttempt, ResponseZone, ScanBatch,
    ScannedPage, Student, StudentResponse, CompetencyEvidence, ExerciseCompetency,
)
from . import grading as grader
from . import error_journal, llm_grader, microcorrection, ocr_cascade, ocr_shadow, providers, scoring, typography
from .runtime_settings import (construction_audit_share, construction_confidence_threshold,
                               ocr_confidence_threshold, ocr_mode)
from .appreciation import build_appreciation
from . import construction, late_copies
from .forgetting import apply_evidence, replay_states, update_level_after_assessment
from .overlay_queue import overlay_file
from .pdfgen import (CARD_PAD, EDGE_MARK_SIZE, qcm_answer_rects, render_copy_review,
                     render_overlay)

PHASES = ["uploaded", "split", "identified", "registered", "ocr_local_pending", "cropped",
          "ocr_complete", "ocr_review_pending", "ocr_confirmed", "graded",
          "review_pending", "finalized", "overlay_ready"]


_STRUCTURED_COMPARATORS = {
    "qcm_single": "qcm", "qcm_multiple": "qcm",
    "checkbox_grid": "grid", "matching": "matching",
    "table_fill": "table_cells", "multi_blank": "table_cells",
    "manual_drawing": "manual",
}


def _grading_contract_issue(db: Session, item: CopyItem, zone: ResponseZone) -> str | None:
    """Vérifie que le crop, la réponse attendue et l'échelle de notation parlent
    bien du MÊME exercice. Toute divergence est envoyée au professeur : tenter
    de la « réparer » pendant la correction pourrait noter la bonne lecture avec
    le barème d'une autre question.

    Les contrôles portent sur les invariants figés dans la copie, pas sur la
    banque courante (qui peut avoir été éditée depuis l'impression).
    """
    page = db.get(DocumentPage, zone.page_id)
    # `ResponseZone.type` a porté des alias historiques ("text", "table",
    # "drawing") : l'identité fiable est la FK item_id, puis la copie de la
    # page. Le rendre strict casserait les sujets déjà imprimés sans améliorer
    # le câblage effectif, piloté par CopyItem.response_type.
    if zone.item_id != item.id or page is None or page.copy_id != item.copy_id:
        return "zone_item_mismatch"
    expected, policy, meta = item.expected_json or {}, item.grading_json or {}, zone.meta_json or {}
    comparator = policy.get("comparator")
    required = _STRUCTURED_COMPARATORS.get(item.response_type)
    if required and comparator != required:
        return "comparator_mismatch"
    try:
        internal_max = float(policy.get("max_score", 1))
    except (TypeError, ValueError):
        return "grading_scale_invalid"
    if not math.isfinite(internal_max) or internal_max <= 0:
        return "grading_scale_invalid"
    try:
        bareme = scoring.item_bareme(policy, item.response_type)
    except Exception:  # contrat JSON mal formé
        return "bareme_invalid"
    if not math.isfinite(bareme) or bareme <= 0:
        return "bareme_invalid"

    if comparator == "qcm":
        choices = policy.get("choices") or []
        correct = expected.get("correct") or []
        if not choices or not correct or any(not isinstance(i, int) or i < 0 or i >= len(choices)
                                             for i in correct):
            return "qcm_contract_mismatch"
        boxes = meta.get("boxes")
        if boxes is not None:
            indices = [b.get("index") for b in boxes if isinstance(b, dict)]
            if (len(indices) != len(boxes) or any(not isinstance(i, int) for i in indices)
                    or sorted(indices) != list(range(len(choices)))):
                return "qcm_zone_mismatch"

    elif comparator == "table_cells":
        policy_cells = policy.get("cells") or []
        expected_cells = expected.get("cells") or []
        if not policy_cells or policy_cells != expected_cells or not grader.fillable_cells(policy):
            return "table_contract_mismatch"
        zone_cells = meta.get("cells")
        if zone_cells is not None:
            if [len(r) for r in zone_cells] != [len(r) for r in expected_cells]:
                return "table_zone_mismatch"

    elif comparator == "grid":
        rows, cols = policy.get("rows") or [], policy.get("cols") or []
        if not rows or not cols or any(not isinstance(r.get("correct"), int)
                                       or not 0 <= r["correct"] < len(cols) for r in rows):
            return "grid_contract_mismatch"
        boxes = meta.get("boxes")
        if boxes is not None:
            positions = {(b.get("row"), b.get("col")) for b in boxes if isinstance(b, dict)}
            expected_positions = {(ri, ci) for ri in range(len(rows)) for ci in range(len(cols))}
            if len(positions) != len(boxes) or positions != expected_positions:
                return "grid_zone_mismatch"

    elif comparator == "matching":
        pairs = expected.get("pairs") or []
        if (not pairs or any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in pairs)
                or len({tuple(p) for p in pairs}) != len(pairs)):
            return "matching_contract_mismatch"
        left, right = meta.get("left_points"), meta.get("right_points")
        if left is not None and right is not None:
            left_ids = {p.get("index") for p in left}
            right_ids = {p.get("index") for p in right}
            if any(p[0] not in left_ids or p[1] not in right_ids for p in pairs):
                return "matching_zone_mismatch"
    return None


def _recognized_answer_issue(item: CopyItem, *, selected: list[int] | None,
                             cell_texts: list[str] | None,
                             selected_pairs: list[list[int]] | None) -> str | None:
    """Valide la sortie du lecteur avant de la brancher au barème.

    Une valeur structurellement impossible indique un défaut CV/OCR ou un
    décalage de métadonnées, jamais une faute mathématique de l'élève.
    """
    policy, expected = item.grading_json or {}, item.expected_json or {}
    comparator = policy.get("comparator")
    if comparator == "qcm" and selected is not None:
        n = len(policy.get("choices") or [])
        if (len(set(selected)) != len(selected)
                or any(not isinstance(i, int) or i < 0 or i >= n for i in selected)):
            return "qcm_reading_mismatch"
    elif comparator == "grid" and selected is not None:
        rows, cols = policy.get("rows") or [], policy.get("cols") or []
        if (len(selected) != len(rows)
                or any(not isinstance(i, int) or i < -1 or i >= len(cols) for i in selected)):
            return "grid_reading_mismatch"
    elif comparator == "table_cells" and cell_texts is not None:
        if len(cell_texts) != len(grader.fillable_cells(policy)):
            return "table_reading_mismatch"
    elif comparator == "matching" and selected_pairs is not None:
        pairs = expected.get("pairs") or []
        # Les colonnes du contrat font foi : un point de droite sans paire attendue
        # est un piège, et le relier est une réponse fausse, pas une lecture invalide.
        left, right = policy.get("left"), policy.get("right")
        left_ids = set(range(len(left))) if left else {p[0] for p in pairs}
        right_ids = set(range(len(right))) if right else {p[1] for p in pairs}
        valid = all(isinstance(p, (list, tuple)) and len(p) == 2 for p in selected_pairs)
        if not valid:
            return "matching_reading_mismatch"
        left_seen = [p[0] for p in selected_pairs]
        right_seen = [p[1] for p in selected_pairs]
        if (len(set(left_seen)) != len(left_seen) or len(set(right_seen)) != len(right_seen)
                or any(p[0] not in left_ids or p[1] not in right_ids for p in selected_pairs)):
            return "matching_reading_mismatch"
    return None


def read_choice_cell(warped, cell: dict, threshold) -> tuple[str, float]:
    """Une décision CV par cellule ; double coche/ambiguïté restent à relire."""
    from . import worker_cv
    boxes = cell["boxes"]
    densities = worker_cv.qcm_densities(warped, boxes)
    selected, _, _ = worker_cv.select_qcm(boxes, densities, threshold)
    confidence = worker_cv.qcm_decision_confidence(densities, threshold)
    if selected is None or len(selected) > 1:
        return "?", 0.0
    by_index = {b["index"]: b["value"] for b in boxes}
    return (by_index[selected[0]] if selected else ""), confidence


def _set_status(db: Session, batch: ScanBatch, status: str, **progress):
    batch.status = status
    p = dict(batch.progress_json or {})
    p[status] = {"done": True, **progress}
    batch.progress_json = p
    db.commit()


def _box_diag(meas, thr) -> dict:
    """Mesures des 2es passes et seuils de page, journalisés avec la lecture
    (recalibrages sur copies réelles : scripts/calibrate_checkboxes.py)."""
    return {"densities_color": [round(float(d), 4) for d in meas.color],
            "densities_ring": [round(float(d), 4) for d in meas.ring],
            "threshold_color": round(thr.color.value, 4),
            "threshold_ring": round(thr.ring.value, 4)}


def _qcm_exclusive(item: CopyItem) -> bool:
    """QCM à choix UNIQUE ? Même règle que le moteur (services.grading) :
    `exclusive` du contrat, sinon une seule bonne réponse attendue."""
    if not item.response_type.startswith("qcm"):
        return False
    if item.response_type == "qcm_single":
        return True
    exclusive = (item.grading_json or {}).get("exclusive")
    if exclusive is None:
        exclusive = len((item.expected_json or {}).get("correct", [])) == 1
    return bool(exclusive)


def _decide_and_store(db: Session, *, item: CopyItem, zone: ResponseZone,
                      student: Student, ocr_text: str, conf: float,
                      selected: list[int] | None, corr_id: str,
                      cell_texts: list[str] | None = None,
                      selected_pairs: list[list[int]] | None = None,
                      queue: list | None = None, defer_grading: bool = False) -> bool:
    """Décision de correction pour une zone. Retourne True si revue créée.

    `queue` : file du correcteur LLM (services.llm_grader). Ce qui lui revient
    (raisonnement rédigé, réponse écrite fausse ET longue) n'est PAS corrigé
    ici : la décision déterministe est écrite telle quelle, en revue
    professeur, et le correcteur la réécrira en fin de lot — par paquets, une
    fois toutes les copies lues. Une panne du correcteur laisse donc la réponse
    au professeur, jamais sans note."""
    expected, gpolicy = item.expected_json, item.grading_json
    resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id,
                           selected_choices=selected or [], final_text=ocr_text,
                           selected_pairs=selected_pairs or [])
    db.add(resp)
    db.flush()

    # Nouveaux lots : la lecture de TOUTES les copies est persistée avant la
    # moindre comparaison au corrigé. Une confiance faible interrompt ainsi la
    # pipeline avant le déterministe et, surtout, avant tout envoi au LLM.
    # L'option reste explicite pour préserver l'API interne utilisée par les
    # tests et la reprise des anciens lots.
    if defer_grading:
        return not math.isfinite(float(conf)) or float(conf) < ocr_confidence_threshold(db)

    return _decide_existing(
        db, resp=resp, item=item, zone=zone, ocr_text=ocr_text, conf=conf,
        selected=selected, cell_texts=cell_texts,
        selected_pairs=selected_pairs, queue=queue)


def _audit_pick(response_id: str, share: float) -> bool:
    """Tirage stable : la même réponse est toujours (ou jamais) contrôlée."""
    if share <= 0:
        return False
    return int(hashlib.sha1(f"audit:{response_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < share


def _construction_verdict(db: Session, resp: StudentResponse, zone: ResponseZone,
                          gpolicy: dict) -> dict:
    """Géométrie à tracer : note de la lecture CV au-dessus du seuil réglé, sinon
    professeur. Une part des notes automatiques passe quand même chez lui
    (contrôle par échantillon) : c'est ce qui permet de calibrer le seuil."""
    reading = effective_reading(db, zone.id)
    raw = ((reading.raw_json or {}).get("construction") if reading else None)
    try:
        max_score = float((gpolicy or {}).get("max_score", 1))
    except (TypeError, ValueError):
        max_score = 1.0
    verdict = construction.verdict(raw, max_score, construction_confidence_threshold(db))
    if verdict["tier"] in ("A", "B") and _audit_pick(resp.id, construction_audit_share(db)):
        verdict = {**verdict, "tier": "D", "reason_code": "construction_audit"}
    return verdict


def _decide_existing(db: Session, *, resp: StudentResponse, item: CopyItem,
                     zone: ResponseZone, ocr_text: str, conf: float,
                     selected: list[int] | None,
                     cell_texts: list[str] | None = None,
                     selected_pairs: list[list[int]] | None = None,
                     queue: list | None = None) -> bool:
    """Note une lecture déjà validée, sans recréer la réponse élève."""
    expected, gpolicy = item.expected_json, item.grading_json

    contract_issue = (_grading_contract_issue(db, item, zone)
                      or _recognized_answer_issue(
                          item, selected=selected, cell_texts=cell_texts,
                          selected_pairs=selected_pairs))
    if contract_issue:
        # Aucun score automatique n'est fiable si la réponse reconnue n'est pas
        # câblée sans ambiguïté à son contrat et à son barème.
        try:
            safe_max = float((gpolicy or {}).get("max_score", 1))
        except (TypeError, ValueError):
            safe_max = 1.0
        if not math.isfinite(safe_max) or safe_max <= 0:
            safe_max = 1.0
        verdict = {"max_score": safe_max, "score": 0.0, "tier": "D",
                   "confidence": 0.0, "reason_code": contract_issue}
    elif item.response_type == "manual_drawing" and (gpolicy or {}).get("construction"):
        verdict = _construction_verdict(db, resp, zone, gpolicy)
    else:
        verdict = grader.grade(expected, gpolicy, ocr_text, conf, selected,
                               cell_texts=cell_texts, selected_pairs=selected_pairs)

    task = None
    if queue is not None:
        task = llm_grader.plan(item, verdict, ocr_text=ocr_text, cell_texts=cell_texts)
    if task is not None:
        task.zone_id = zone.id
        verdict = {**verdict, "tier": "D", "reason_code": "llm_pending"}

    decision = GradingDecision(
        response_id=resp.id,
        source="deterministic" if verdict["tier"] in ("A", "B") else
               ("deepseek" if verdict["tier"] == "C" else "deterministic"),
        score=verdict["score"], max_score=verdict["max_score"],
        confidence=verdict["confidence"], reason_code=verdict["reason_code"],
        tier=verdict["tier"],
        status="auto" if verdict["tier"] in ("A", "B", "C") else "review_pending",
        evidence_json={"contract_issue": contract_issue} if contract_issue else {},
    )
    db.add(decision)
    db.flush()
    if task is not None:
        task.decision_id = decision.id
        queue.append(task)
    if decision.status == "review_pending":
        cat = ("double_coche" if verdict["reason_code"] == "qcm_double_check"
               else "ocr_ambigu" if "ocr" in verdict["reason_code"]
               else "rature" if verdict["reason_code"] == "qcm_unreadable"
               else "trace_dessin" if (verdict["reason_code"] == "no_structured_answer"
                                       or verdict["reason_code"].startswith("construction_"))
               else "points_a_relier" if verdict["reason_code"].startswith("matching_")
               else "ocr_ambigu" if verdict["reason_code"].startswith("table_")
               else "bareme")
        db.add(ManualReview(decision_id=decision.id, category=cat))
        return True
    return False


# cv_ocr : tableau avec case à choix (sinon il finissait toujours en OCRiser, et
# update_ocr_reading refusait la reprise) ; ocr_fusion : lecture de la cascade locale.
_READING_PROVIDERS = ("teacher_ocr", "mathpix", "mock", "cv_local", "cv_ocr", "ocr_fusion")


def effective_reading(db: Session, zone_id: str | None) -> OcrAttempt | None:
    if not zone_id:
        return None
    return (db.query(OcrAttempt)
            .filter(OcrAttempt.zone_id == zone_id,
                    OcrAttempt.provider.in_(_READING_PROVIDERS))
            .order_by(OcrAttempt.created_at.desc()).first())


def pending_ocr_responses(db: Session, assessment_id: str) -> list[StudentResponse]:
    """Réponses dont la dernière lecture CV/OCR reste sous le seuil réglé."""
    return [resp for resp, _ in _pending_ocr(db, assessment_id)]


def pending_ocr_units(db: Session, assessment_id: str) -> dict[str, int]:
    """Lectures à vérifier par question (`copy_item_id` -> nombre), comptées comme
    l'assistant OCRiser les présente : une case de tableau douteuse = une lecture,
    une zone = une lecture. Le compteur du lot et celui de l'assistant concordent."""
    threshold = ocr_confidence_threshold(db)
    out: dict[str, int] = {}
    for resp, reading in _pending_ocr(db, assessment_id):
        raw = (reading.raw_json or {}) if reading else {}
        n = 1
        if raw.get("cells") is not None and isinstance(raw.get("cells"), list):
            confs = raw.get("cell_confidences") or []
            n = (sum(1 for c in confs if not _finite_at_least(c, threshold)) if confs
                 else len(raw["cells"])) or 1
        out[resp.copy_item_id] = n
    return out


def _finite_at_least(value, threshold: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v >= threshold


def _pending_ocr(db: Session, assessment_id: str) -> list[tuple[StudentResponse, OcrAttempt | None]]:
    threshold = ocr_confidence_threshold(db)
    # Une copie finalisée est close : une copie tardive ne rouvre pas sa lecture.
    copies = (db.query(Copy).filter(Copy.assessment_id == assessment_id,
                                    Copy.status != "finalized").all())
    copy_ids = [c.id for c in copies]
    if not copy_ids:
        return []
    items = db.query(CopyItem).filter(CopyItem.copy_id.in_(copy_ids)).all()
    out = []
    for item in items:
        resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
        if not resp:
            continue
        reading = effective_reading(db, resp.zone_id)
        try:
            confidence = float(reading.confidence) if reading else 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence) or confidence < threshold:
            out.append((resp, reading))
    return out


def copy_is_blank(db: Session, copy: Copy) -> bool:
    """Absence uniquement si TOUTES les réponses ont été lues et confirmées vides.
    Un scan incomplet ou une lecture incertaine ne constitue pas une absence.
    """
    from . import worker_cv  # import tardif (OpenCV), comme read_choice_cell
    items = db.query(CopyItem).filter_by(copy_id=copy.id).all()
    if not items: return False
    for item in items:
        response = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
        reading = effective_reading(db, response.zone_id) if response else None
        if not response or not reading or reading.confidence < ocr_confidence_threshold(db):
            return False
        raw = reading.raw_json or {}
        if item.response_type.startswith("qcm"):
            if response.selected_choices: return False
        elif item.response_type == "checkbox_grid":
            if any(v is not None and v != -1 for v in (response.selected_choices or [])): return False
        elif item.response_type == "matching":
            if response.selected_pairs: return False
        elif item.response_type in ("table_fill", "multi_blank"):
            if "cells" not in raw or any(str(v).strip() for v in raw["cells"] if v is not None): return False
        elif item.response_type == "manual_drawing":
            if "empty_score" not in raw or raw["empty_score"] >= worker_cv.BLANK_INK_THRESHOLD: return False
        elif (response.final_text or reading.text or reading.latex or "").strip():
            return False
    return True


def grade_stored_responses(db: Session, batch: ScanBatch) -> int:
    """Lance correction déterministe puis LLM après validation de la lecture."""
    assessment = db.get(Assessment, batch.assessment_id)
    queue: list[llm_grader.Task] = []
    copies = db.query(Copy).filter_by(assessment_id=assessment.id).all()
    for copy in copies:
        if copy.status == "finalized":
            continue   # copie close, recorrigée seulement si le professeur le demande
        if copy.status == "absent" or copy_is_blank(db, copy):
            copy.status = "absent"
            continue
        items = db.query(CopyItem).filter_by(copy_id=copy.id).all()
        for item in items:
            resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
            if not resp or db.query(GradingDecision).filter_by(response_id=resp.id).first():
                continue
            zone = db.get(ResponseZone, resp.zone_id) if resp.zone_id else None
            if not zone:
                continue
            reading = effective_reading(db, resp.zone_id)
            raw = (reading.raw_json or {}) if reading else {}
            cells = raw.get("cells") if item.response_type in ("table_fill", "multi_blank") else None
            selected = resp.selected_choices if (item.response_type.startswith("qcm")
                                                   or item.response_type == "checkbox_grid") else None
            pairs = resp.selected_pairs if item.response_type == "matching" else None
            _decide_existing(
                db, resp=resp, item=item, zone=zone,
                # Arriver ici signifie que la lecture satisfait le seuil réglé.
                # Le moteur historique possède encore son garde-fou fixe à 90 % :
                # on lui passe donc une confiance validée pour ne pas réouvrir la
                # même erreur OCR dans l'assistant de correction.
                ocr_text=resp.final_text or (reading.text if reading else ""), conf=1.0,
                selected=selected, cell_texts=cells, selected_pairs=pairs, queue=queue)
    # Toutes les décisions déterministes existent avant le premier appel LLM ;
    # celui-ci conserve ensuite son regroupement par exercice à travers élèves.
    queue.extend(llm_grader.requeue_unavailable(db, assessment.id))
    _grade_with_llm(db, queue, batch)
    audit_automatic_decisions(db, assessment.id)
    n_review = open_reviews(db, assessment.id)
    _set_status(db, batch, "graded")
    if n_review:
        _set_status(db, batch, "review_pending", pending=n_review)
        db.commit()
    else:
        # Aucune revue à trancher : la halte « Valider la correction » ne
        # faisait alors que confirmer des notes déjà closes, sans offrir au
        # professeur la moindre action réelle. On enchaîne directement sur
        # la finalisation (résultats, preuves de compétence) et les copies
        # corrigées, cf. demande du 02/09.
        try:
            finalize_batch(db, batch)
        except FinalizeInProgress:
            pass   # une requête de la page Corrections finalise déjà ce lot
    return n_review


def _expected_as_text(expected: dict) -> str:
    t = expected.get("type")
    if t == "rational":
        n, d = expected["value"]
        return f"{n}/{d}" if d != 1 else str(n)
    if t == "expression":
        return expected["value"].replace("*", "")
    if t == "rubric":
        # Un raisonnement n'a pas de « valeur » : sans ce repli, l'OCR simulé
        # d'un multiline_text ne rendait jamais rien (copie réputée blanche) et
        # tout le chemin du correcteur LLM restait inatteignable hors ligne.
        return " ".join(str(s.get("expected_text", ""))
                        for s in expected.get("steps") or []).strip()
    v = expected.get("value")
    return "" if v is None else str(v)


def _wrong_answer(right: str, h: int) -> str:
    try:
        return str(int(right) + 1 + h % 3)
    except ValueError:
        return right + " + 1"


# --------------------------------------------------------------- chemin réel

def _process_real(db: Session, batch: ScanBatch, assessment: Assessment,
                  mode_override: str | None = None) -> int:
    import cv2

    from . import worker_cv  # import tardif : OpenCV chargé seulement si nécessaire

    # sans clé Mathpix, l'OCR tourne sur son repli déterministe : on lui souffle
    # alors la réponse attendue pour simuler une lecture ; jamais avec une vraie clé.
    ocr_offline = providers.offline(db, "mathpix")
    # lecture multicouche : `mathpix` (inchangé), `shadow` (Mathpix décide, le local enregistre)
    # ou `cascade` (le local décide, Mathpix en dernier recours). Voir services.ocr_cascade.
    ocr_run_mode = mode_override or ocr_mode(db)
    cascade = ocr_run_mode == "cascade"
    shadow = ocr_run_mode == "shadow"
    src = db.get(FileObject, batch.source_file_id)
    if not src or not Path(src.storage_path).exists():
        raise ValueError("Fichier scan introuvable")

    derived_dir = settings.data_dir / "assessments" / assessment.id / "scans" / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)

    # index : page_id -> (DocumentPage, Copy)
    pages = (db.query(DocumentPage, Copy)
             .join(Copy, DocumentPage.copy_id == Copy.id)
             .filter(Copy.assessment_id == assessment.id).all())
    page_index = {p.id: (p, c) for p, c in pages}

    # Pages rastérisées UNE PAR UNE (générateur) et page recalée écrite sur
    # disque aussitôt : garder tout le scan en mémoire (brut + recalé, ~23 Mo
    # par page) faisait tuer le conteneur API sur le NAS pour une classe entière,
    # laissant le lot figé sur « Lecture des copies ».
    n_pages = worker_cv.count_pages(src.storage_path)
    images = worker_cv.iter_raster_any(src.storage_path)
    batch.page_count = n_pages
    _set_status(db, batch, "split", pages=n_pages)

    analyses: list[tuple[int, "worker_cv.PageAnalysis"]] = []
    identified = 0
    seen_page_ids: set[str] = set()
    for i, img in enumerate(images):
        sp = (db.query(ScannedPage).filter_by(batch_id=batch.id, source_index=i).first()
              or ScannedPage(batch_id=batch.id, source_index=i))
        db.add(sp)
        if sp.dismissed or sp.status in late_copies.SKIPPED_BY_PIPELINE:
            # confirmé par le professeur comme n'étant pas une vraie copie
            # (résolution des scans bloqués) : hors flux, aucun overlay attendu.
            # Une feuille tardive sans correction à lire garde sa place, sans lecture.
            continue
        if (sp.status == "registered" and sp.page_id in page_index
                and page_index[sp.page_id][1].status == "finalized"
                and (derived_dir / f"page-{sp.page_id}.png").exists()):
            # Copie déjà corrigée : une nouvelle vague ne la relit pas.
            seen_page_ids.add(sp.page_id)
            identified += 1
            continue
        # `manual_page_id` : identité posée à la main sur une page jusqu'ici
        # bloquée (QR illisible) — on ne retente pas la lecture QR, seulement
        # le recalage géométrique sur cette identité connue.
        res = worker_cv.analyze_page(img, forced_page_id=sp.manual_page_id)
        # Fond indexé par POSITION, y compris si QR/repères illisibles. Il sert
        # à l'aperçu de la page de sécurité sans jamais tenter de lui inventer
        # une identité.
        preview_img = res.warped if res.warped is not None else img
        (derived_dir / f"source-{i}.png").write_bytes(worker_cv.encode_png(preview_img))
        sp.quality_json = {"blur": round(res.blur, 1), "marker_count": res.marker_count,
                           "reprojection_error_px": round(res.reprojection_error_px, 2),
                           "warnings": res.warnings}
        if res.page_id and res.page_id in page_index:
            sp.page_id = res.page_id
            sp.status = res.status
            if res.status == "registered":
                if res.page_id in seen_page_ids:
                    # La feuille existe physiquement deux fois : on garde sa
                    # position mais une seule lecture/correction. L'autre place
                    # recevra l'overlay de sécurité « Non identifié ». Dans une
                    # pile tardive, elle la traverse sans marque : sa correction
                    # est sur l'autre exemplaire.
                    sp.status = "late_duplicate" if sp.wave else "duplicate"
                    sp.quality_json = {**sp.quality_json, "warnings":
                                       sp.quality_json["warnings"] + ["duplicate_in_batch"]}
                    late_copies.close_wave_if_empty(db, batch, sp.wave)
                else:
                    seen_page_ids.add(res.page_id)
                    identified += 1
                    # page recalée persistée : sert de FOND à l'aperçu « copie +
                    # overlay » (services.pdfgen.render_copy_review) et est
                    # relue au 2e passage — on ne la garde pas en mémoire.
                    if res.warped is not None:
                        (derived_dir / f"page-{res.page_id}.png").write_bytes(
                            worker_cv.encode_png(res.warped))
                        res.warped = None
                    analyses.append((i, res))
        else:
            # page inconnue ou d'un autre lot : bloquée, jamais attribuée (RM-001)
            sp.status = "blocked"
            if res.page_id:
                sp.quality_json = {**sp.quality_json, "warnings":
                                   sp.quality_json["warnings"] + ["page_from_other_assessment"]}
    _set_status(db, batch, "identified", identified=identified, total=n_pages)
    _set_status(db, batch, "registered")

    # Aucune page reconnue : ne PAS filer en silence vers un lot « corrigé » vide
    # (pas de réponse, pas d'overlay) — c'était le symptôme « rien à corriger,
    # overlays vides » sans explication. On bloque avec un message actionnable.
    if identified == 0:
        batch.error = (
            "Aucune page reconnue sur ce scan : le QR ou les repères de coin sont "
            "illisibles. Rescannez les copies bien à plat, nettes et bien "
            "éclairées (ou vérifiez qu'il s'agit de copies imprimées depuis "
            "Kiro & Nina), puis re-déposez.")
        db.commit()
        return 0

    n_review = 0
    queue: list[llm_grader.Task] = []
    prev = None
    for i, res in analyses:
        if prev is not None:
            prev.warped = None   # page précédente traitée : libérée
        prev = res
        page, copy = page_index[res.page_id]
        student = db.get(Student, copy.student_id)
        # page recalée écrite au 1er passage : relue ici (PNG sans perte), une
        # seule page en mémoire à la fois.
        warped_path = derived_dir / f"page-{res.page_id}.png"
        if warped_path.exists():
            res.warped = cv2.imread(str(warped_path), cv2.IMREAD_UNCHANGED)
        zones = db.query(ResponseZone).filter_by(page_id=page.id).all()
        # Seuil QCM ADAPTATIF par page : on met en commun les densités de TOUTES
        # les cases QCM de la page pour caler un seuil unique sur le style de coche
        # de l'élève (trait fin / aplat), quand deux groupes se détachent nettement.
        box_meas: dict[str, worker_cv.BoxMeasures] = {}
        for zone in zones:
            zitem = db.get(CopyItem, zone.item_id)
            # QCM et grille cochée partagent la même détection de cases : leurs
            # mesures sont mises en commun pour caler les seuils adaptatifs de la
            # page (cf. worker_cv.decide_boxes).
            if zitem.response_type.startswith("qcm") or zitem.response_type == "checkbox_grid":
                box_meas[zone.id] = worker_cv.box_measures(
                    res.warped, (zone.meta_json or {}).get("boxes", []))
        choice_dark: list[float] = []
        for zone in zones:
            for row in (zone.meta_json or {}).get("cells", []):
                for cell in row:
                    if cell and cell.get("input") == "single_choice":
                        choice_dark.extend(worker_cv.qcm_densities(res.warped, cell["boxes"]))
        box_thr = worker_cv.page_box_thresholds(list(box_meas.values()), choice_dark)
        qcm_thr = box_thr.dark
        for zone in zones:
            item = db.get(CopyItem, zone.item_id)
            if db.query(StudentResponse).filter_by(copy_item_id=item.id).first():
                continue  # idempotence
            corr_id = f"{copy.id[:8]}-{item.sequence}"

            crop = worker_cv.crop_zone(res.warped, zone.x_pt, zone.y_pt,
                                       zone.w_pt, zone.h_pt, zone.padding_pt)
            filtered = worker_cv.dropout_filter(crop)
            crop_path = derived_dir / f"{zone.id}.png"
            crop_path.write_bytes(worker_cv.encode_png(filtered))

            if item.response_type.startswith("qcm"):
                boxes = (zone.meta_json or {}).get("boxes", [])
                meas = box_meas.get(zone.id) or worker_cv.box_measures(res.warped, boxes)
                densities = meas.dark
                selected, cv_conf, why = worker_cv.decide_boxes(
                    boxes, meas, box_thr, grid=False, exclusive=_qcm_exclusive(item))
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                  raw_json={"densities": densities, "selected": selected,
                                            "threshold": round(qcm_thr.value, 4),
                                            "adapted": qcm_thr.adapted,
                                            **_box_diag(meas, box_thr), **why},
                                  confidence=cv_conf))
                n_review += _decide_and_store(
                    db, item=item, zone=zone, student=student,
                    ocr_text="", conf=cv_conf, selected=selected, corr_id=corr_id,
                    queue=queue, defer_grading=True)
            elif item.response_type == "checkbox_grid":
                boxes = (zone.meta_json or {}).get("boxes", [])
                meas = box_meas.get(zone.id) or worker_cv.box_measures(res.warped, boxes)
                _dens = meas.dark
                row_states = worker_cv.grid_row_states(boxes, _dens, qcm_thr)
                selected, cv_conf, why = worker_cv.decide_boxes(
                    boxes, meas, box_thr, grid=True, exclusive=False)
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                  raw_json={"grid_selected": selected,
                                            # conservé même sur une double coche : la revue
                                            # et la micro-correction en ont besoin par ligne
                                            "densities": [round(float(d), 4) for d in _dens],
                                            "row_states": row_states,
                                            "threshold": round(qcm_thr.value, 4),
                                            "adapted": qcm_thr.adapted,
                                            **_box_diag(meas, box_thr), **why},
                                  confidence=cv_conf))
                n_review += _decide_and_store(
                    db, item=item, zone=zone, student=student,
                    ocr_text="", conf=cv_conf, selected=selected, corr_id=corr_id,
                    queue=queue, defer_grading=True)
            elif item.response_type == "manual_drawing":
                # tracé/dessin libre : jamais de correction automatique, aucun appel
                # Mathpix (§ tracés géométriques). Géométrie à tracer : la CV lit le
                # tracé ; sa note s'applique au-dessus du seuil réglé (cf.
                # _construction_verdict), le professeur corrige le reste.
                raw = {"manual": True}
                if (zone.meta_json or {}).get("construction"):
                    try:
                        raw["construction"] = construction.read(res.warped, zone.meta_json)
                    except Exception as exc:  # noqa: BLE001 - une lecture ratée part chez le professeur
                        raw["construction"] = {"status": "error", "reasons": [str(exc)[:200]]}
                    # copie entièrement blanche (absence) : même mesure que les autres zones
                    raw["empty_score"] = (0.0 if raw["construction"].get("status") == "blank"
                                          else 1.0)
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                  raw_json=raw, confidence=1.0))
                n_review += _decide_and_store(
                    db, item=item, zone=zone, student=student,
                    ocr_text="", conf=1.0, selected=None, corr_id=corr_id,
                    queue=queue, defer_grading=True)
            elif item.response_type == "matching":
                left_pts = (zone.meta_json or {}).get("left_points", [])
                right_pts = (zone.meta_json or {}).get("right_points", [])
                # `cv` : motif de la décision (revue ou non) — sert aux recalibrages
                cv_diag: dict = {}
                pairs, conf_m = worker_cv.detect_matching(res.warped, left_pts, right_pts,
                                                          diag=cv_diag)
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                  raw_json={"pairs": pairs, "cv": cv_diag}, confidence=conf_m))
                n_review += _decide_and_store(
                    db, item=item, zone=zone, student=student,
                    ocr_text="", conf=conf_m, selected=None, corr_id=corr_id,
                    selected_pairs=pairs, queue=queue, defer_grading=True)
            elif item.response_type in ("table_fill", "multi_blank"):
                # multi_blank : mêmes cellules qu'un table_fill à 1 ligne
                # (meta["cells"] rempli en une seule "ligne" dans pdfgen), donc
                # exactement la même logique de découpe/OCR par cellule.
                cells_meta = (zone.meta_json or {}).get("cells", [])
                expected_cells = item.expected_json.get("cells", [])
                cell_texts, cell_latex, confs = [], [], []
                ocr_slots: list[tuple[int, "ocr_cascade.CropSpec"]] = []   # (position, découpe) pour le multicouche
                k = 0  # index PLAT des cases non-"given" (row-major) : aligne
                       # cell_texts, le crop `-c{k}.png` et scans._cell_units.
                for ri, row in enumerate(cells_meta):
                    for ci, cell in enumerate(row):
                        # cellule "given" : déjà imprimée dans le manuel, non
                        # éditable par l'élève, exclue de l'OCR et de la notation
                        # (cf. grading.table_cells qui filtre la même liste).
                        if (ri < len(expected_cells) and ci < len(expected_cells[ri])
                                and expected_cells[ri][ci].get("given")):
                            continue
                        ccrop = worker_cv.crop_zone(res.warped, cell["x_pt"], cell["y_pt"],
                                                    cell["w_pt"], cell["h_pt"], padding_pt=0)
                        cfiltered = worker_cv.dropout_filter(ccrop)
                        # crop DÉDIÉ à la relecture manuelle : la modale montre la
                        # SEULE case corrigée (pas tout le tableau). Un peu de marge
                        # pour ne pas rogner l'écriture qui déborde. Toutes les cases
                        # non-"given" (même vides) pour que l'index k reste aligné.
                        disp = worker_cv.crop_zone(res.warped, cell["x_pt"], cell["y_pt"],
                                                   cell["w_pt"], cell["h_pt"], padding_pt=2.5)
                        if disp.size:  # bord de page/géométrie dégénérée : repli sur le tableau entier
                            (derived_dir / f"{zone.id}-c{k}.png").write_bytes(
                                worker_cv.encode_png(worker_cv.dropout_filter(disp)))
                        k += 1
                        if cell.get("input") == "single_choice":
                            value, conf_cv = read_choice_cell(res.warped, cell, qcm_thr)
                            cell_texts.append(value); cell_latex.append(""); confs.append(conf_cv)
                            continue
                        cell_ink = worker_cv.ink_ratio(cfiltered)
                        if cell_ink < 0.01:
                            cell_texts.append("")
                            cell_latex.append("")
                            # Une case « vide » est elle aussi une décision CV.
                            # Une trace pâle proche du seuil ne devient jamais
                            # automatiquement une absence de réponse.
                            confs.append(worker_cv.blank_decision_confidence(
                                cell_ink, threshold=0.01))
                            continue
                        if shadow or cascade:
                            ctype = (expected_cells[ri][ci].get("type") if ri < len(expected_cells)
                                     and ci < len(expected_cells[ri]) else None)
                            ocr_slots.append((len(cell_texts), ocr_cascade.make_spec(
                                cfiltered, ocr_cascade.zone_class_for(ctype), cell_index=len(cell_texts))))
                        if cascade:
                            # le local décide : aucun appel Mathpix ici, la case attend sa lecture
                            cell_texts.append(""); cell_latex.append(""); confs.append(0.0)
                            continue
                        hint = None
                        if ocr_offline and ri < len(expected_cells) and ci < len(expected_cells[ri]):
                            hint = str(expected_cells[ri][ci]["value"])
                        ocr_c = providers.mathpix_ocr(db, worker_cv.encode_png(cfiltered),
                                                      f"{corr_id}-c{ri}-{ci}", expected_hint=hint)
                        cell_texts.append(ocr_c["text"])
                        cell_latex.append(ocr_c.get("latex") or "")
                        confs.append(ocr_c["confidence"])
                if cascade and ocr_slots:
                    # squelette de la zone : ce que la CV a lu + la place de chaque découpe ;
                    # la lecture `ocr_fusion` sera écrite quand toutes les découpes seront décidées
                    by_pos = {pos: spec for pos, spec in ocr_slots}
                    ocr_cascade.begin_zone(
                        db, batch, zone, kind="table", corr_id=corr_id, mode="cascade",
                        positions=[{"spec": by_pos[i]} if i in by_pos else
                                   {"fixed": {"text": cell_texts[i], "latex": cell_latex[i],
                                              "conf": confs[i]}} for i in range(len(cell_texts))])
                    continue
                # mode ombre : une part des zones ACCEPTÉES par Mathpix passe quand même chez le
                # professeur (« Contrôle »), pour mesurer la lecture locale sur les cas faciles aussi
                audit_extra: dict = {}
                if (shadow and ocr_slots and min(confs, default=0.0) >= ocr_confidence_threshold(db)
                        and ocr_cascade.shadow_audit_pick(db, zone, batch)):
                    low = max(0.0, ocr_confidence_threshold(db) - 0.01)
                    for pos, _spec in ocr_slots:
                        confs[pos] = min(confs[pos], low)
                    audit_extra = {"audit": True}
                min_conf = min(confs) if confs else 1.0
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_ocr" if any(c.get("input") == "single_choice" for row in expected_cells for c in row) else "mathpix",
                                  raw_json={"cells": cell_texts,
                                            "cell_latex": cell_latex,
                                            "cell_confidences": confs, **audit_extra},
                                  confidence=min_conf))
                if shadow and ocr_slots:
                    db.flush()
                    ocr_cascade.begin_zone(
                        db, batch, zone, kind="table", corr_id=corr_id, mode="shadow",
                        positions=[{"spec": dict(ocr_slots).get(i)} if i in dict(ocr_slots) else
                                   {"fixed": {"text": cell_texts[i], "latex": cell_latex[i],
                                              "conf": confs[i]}} for i in range(len(cell_texts))])
                n_review += _decide_and_store(
                    db, item=item, zone=zone, student=student,
                    ocr_text="", conf=min_conf, selected=None, corr_id=corr_id,
                    cell_texts=cell_texts, queue=queue, defer_grading=True)
            else:
                ink = worker_cv.ink_ratio(filtered)
                if ink < worker_cv.BLANK_INK_THRESHOLD:  # zone vide : aucun appel Mathpix (§8.3)
                    cv_conf = worker_cv.blank_decision_confidence(ink)
                    db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                      raw_json={"empty_score": ink}, confidence=cv_conf))
                    n_review += _decide_and_store(
                        db, item=item, zone=zone, student=student,
                        ocr_text="", conf=cv_conf, selected=None, corr_id=corr_id,
                    queue=queue, defer_grading=True)
                elif cascade:
                    # le local décide : une découpe par zone, ou par ligne non vide pour une rédaction
                    multi = item.response_type == "multiline_text"
                    if multi:
                        specs = ocr_cascade.line_specs(res.warped, zone, item, worker_cv.BLANK_INK_THRESHOLD)
                    else:
                        specs = [ocr_cascade.make_spec(
                            filtered, ocr_cascade.zone_class_for(item.expected_json.get("type")))]
                    if not specs:
                        ocr_cascade.no_line_zone(db, zone, item, student, corr_id)
                    else:
                        ocr_cascade.begin_zone(db, batch, zone, kind="text", corr_id=corr_id, mode="cascade",
                                               positions=[{"spec": sp} for sp in specs])
                else:
                    hint = _expected_as_text(item.expected_json) if ocr_offline else None
                    ocr = providers.mathpix_ocr(db, crop_path.read_bytes(), corr_id,
                                                expected_hint=hint)
                    ocr_conf, ocr_raw = ocr["confidence"], ocr["raw"]
                    if (shadow and ocr_conf >= ocr_confidence_threshold(db)
                            and ocr_cascade.shadow_audit_pick(db, zone, batch)):
                        # mode ombre : lecture sûre tirée pour le contrôle (voir plus haut)
                        ocr_conf = max(0.0, ocr_confidence_threshold(db) - 0.01)
                        ocr_raw = {**(ocr_raw or {}), "audit": True}
                    db.add(OcrAttempt(zone_id=zone.id, provider="mathpix",
                                      raw_json=ocr_raw, latex=ocr["latex"],
                                      text=ocr["text"], confidence=ocr_conf))
                    ocr_shadow.record(db, item=item, zone=zone, image=crop_path.read_bytes(),
                                      ocr_text=ocr["text"], ocr_confidence=float(ocr["confidence"]),
                                      threshold=ocr_confidence_threshold(db), correlation_id=corr_id)
                    if shadow:
                        multi = item.response_type == "multiline_text"
                        specs = (ocr_cascade.line_specs(res.warped, zone, item, worker_cv.BLANK_INK_THRESHOLD)
                                 if multi else [ocr_cascade.make_spec(
                                     filtered, ocr_cascade.zone_class_for(item.expected_json.get("type")))])
                        if specs:
                            db.flush()
                            ocr_cascade.begin_zone(db, batch, zone, kind="text", corr_id=corr_id, mode="shadow",
                                                   positions=[{"spec": sp} for sp in specs])
                    n_review += _decide_and_store(
                        db, item=item, zone=zone, student=student,
                        ocr_text=ocr["text"], conf=ocr_conf,
                        selected=None, corr_id=corr_id, queue=queue, defer_grading=True)
        copy.status = "read"
    if cascade:
        db.flush()
        if ocr_cascade.has_pending(db, batch):
            # la lecture locale (Mac) n'est pas finie : le lot attend, puis reprend seul
            _set_status(db, batch, "ocr_local_pending", since=datetime.now(timezone.utc).isoformat())
            ocr_cascade._wake.set()
            return n_review
    _set_status(db, batch, "cropped")
    _set_status(db, batch, "ocr_complete")
    return n_review


def _grade_with_llm(db: Session, queue: list, batch: ScanBatch) -> None:
    """Correcteur LLM du lot, en fin de lecture : toutes les copies sont lues,
    donc les réponses d'un même exercice partent dans le même appel (§ économie
    de tokens). Les réponses qu'il tranche voient leur revue provisoire retirée
    — d'où le recomptage des revues ouvertes par `process_batch`."""
    if not queue:
        return
    llm_grader.grade_tasks(db, queue, correlation_id=batch.id[:8])
    db.commit()


# ----------------------------------------------- chemin sans scan (tests)

def _process_mock(db: Session, batch: ScanBatch, assessment: Assessment) -> int:
    copies = (db.query(Copy).join(Student, Copy.student_id == Student.id)
              .filter(Copy.assessment_id == assessment.id)
              .order_by(Student.order_index, Student.id).all())
    pages = (db.query(DocumentPage).join(Copy, DocumentPage.copy_id == Copy.id)
             .filter(Copy.assessment_id == assessment.id).all())
    batch.page_count = len(pages)
    _set_status(db, batch, "split", pages=len(pages))

    identified = 0
    for i, page in enumerate(pages):
        sp = (db.query(ScannedPage).filter_by(batch_id=batch.id, source_index=i).first()
              or ScannedPage(batch_id=batch.id, source_index=i))
        db.add(sp)
        # Ce chemin ne lit aucun fichier physique : les pages du sujet servent
        # directement de flux ADF synthétique. Elles doivent donc conserver le
        # même lien page_id/position que les réponses que ce mock va corriger.
        # Simuler ici un QR illisible produirait simultanément une page bloquée
        # et une correction identifiée, situation impossible dans le vrai flux.
        sp.page_id = page.id
        sp.status = "registered"
        sp.quality_json = {"reprojection_error_px": 1.1, "marker_count": 4, "blur": 250}
        identified += 1
    _set_status(db, batch, "identified", identified=identified, total=len(pages))
    _set_status(db, batch, "registered")

    n_review = 0
    queue: list[llm_grader.Task] = []
    for copy in copies:
        student = db.get(Student, copy.student_id)
        items = db.query(CopyItem).filter_by(copy_id=copy.id).order_by(CopyItem.sequence).all()
        for item in items:
            zone = db.query(ResponseZone).filter_by(item_id=item.id).first()
            if zone is None or db.query(StudentResponse).filter_by(copy_item_id=item.id).first():
                continue
            corr_id = f"{copy.id[:8]}-{item.sequence}"
            expected, gpolicy = item.expected_json, item.grading_json

            if item.response_type.startswith("qcm"):
                h = int(hashlib.sha256(corr_id.encode()).hexdigest(), 16)
                if h % 10 < 7:
                    selected = expected.get("correct", [])
                elif h % 10 < 9:
                    n = len(gpolicy.get("choices", [])) or 4
                    selected = [(expected.get("correct", [0])[0] + 1) % n]
                else:
                    selected = list({expected.get("correct", [0])[0], 1})  # double coche
                db.add(OcrAttempt(zone_id=zone.id, provider="cv_local",
                                  raw_json={"selected": selected}, confidence=1.0))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text="", conf=1.0, selected=selected,
                                              corr_id=corr_id, queue=queue)
            elif item.response_type == "checkbox_grid":
                rows = expected.get("rows", [])
                ncols = len(gpolicy.get("cols", [])) or 2
                h = int(hashlib.sha256(corr_id.encode()).hexdigest(), 16)
                # majorité juste, quelques erreurs déterministes (une case par ligne)
                selected = [r.get("correct", 0) if (h >> i) % 5 != 0
                            else (r.get("correct", 0) + 1) % ncols
                            for i, r in enumerate(rows)]
                db.add(OcrAttempt(zone_id=zone.id, provider="mock",
                                  raw_json={"grid_selected": selected}, confidence=1.0))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text="", conf=1.0, selected=selected,
                                              corr_id=corr_id, queue=queue)
            elif item.response_type == "manual_drawing":
                db.add(OcrAttempt(zone_id=zone.id, provider="mock",
                                  raw_json={"manual": True}, confidence=1.0))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text="", conf=1.0, selected=None,
                                              corr_id=corr_id, queue=queue)
            elif item.response_type == "matching":
                h = int(hashlib.sha256(corr_id.encode()).hexdigest(), 16)
                expected_pairs = expected.get("pairs", [])
                if h % 10 < 8:
                    pairs, conf_m = expected_pairs, 1.0
                elif h % 10 < 9:
                    pairs, conf_m = expected_pairs[:-1] if expected_pairs else [], 1.0
                else:
                    pairs, conf_m = None, 0.0
                db.add(OcrAttempt(zone_id=zone.id, provider="mock",
                                  raw_json={"pairs": pairs}, confidence=conf_m))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text="", conf=conf_m, selected=None,
                                              corr_id=corr_id, selected_pairs=pairs,
                                              queue=queue)
            elif item.response_type in ("table_fill", "multi_blank"):
                cells = expected.get("cells", [])
                h = int(hashlib.sha256(corr_id.encode()).hexdigest(), 16)
                cell_texts = []
                for ri, row in enumerate(cells):
                    for ci, cell in enumerate(row):
                        ok = (h >> (ri * 7 + ci)) % 5 != 0
                        val = str(cell["value"])
                        cell_texts.append(val if ok else _wrong_answer(val, h))
                db.add(OcrAttempt(zone_id=zone.id, provider="mock",
                                  raw_json={"cells": cell_texts}, confidence=1.0))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text="", conf=1.0, selected=None,
                                              corr_id=corr_id, cell_texts=cell_texts,
                                              queue=queue)
            else:
                hint = _expected_as_text(expected)
                h = int(hashlib.sha256((corr_id + "ans").encode()).hexdigest(), 16)
                actual = hint if h % 4 != 0 else _wrong_answer(hint, h)
                ocr = providers.mathpix_ocr(db, corr_id.encode(), corr_id, expected_hint=actual)
                db.add(OcrAttempt(zone_id=zone.id, provider="mock",
                                  raw_json=ocr["raw"], latex=ocr["latex"],
                                  text=ocr["text"], confidence=ocr["confidence"]))
                n_review += _decide_and_store(db, item=item, zone=zone, student=student,
                                              ocr_text=ocr["text"], conf=ocr["confidence"],
                                              selected=None, corr_id=corr_id, queue=queue)
        copy.status = "graded"
    _set_status(db, batch, "cropped")
    _grade_with_llm(db, queue, batch)
    _set_status(db, batch, "ocr_complete")
    return n_review


def open_reviews(db: Session, assessment_id: str) -> int:
    """Nombre de validations professeur encore ouvertes sur un sujet."""
    # Session sans autoflush : la dernière revue créée doit compter. Sans cela, un
    # lot à une seule revue se croyait terminé et sa validation échouait.
    db.flush()
    return (db.query(ManualReview).join(GradingDecision)
            .join(StudentResponse, GradingDecision.response_id == StudentResponse.id)
            .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
            .join(Copy, CopyItem.copy_id == Copy.id)
            .filter(Copy.assessment_id == assessment_id,
                    ManualReview.resolved_at.is_(None)).count())


def audit_automatic_decisions(db: Session, assessment_id: str) -> int:
    """Rattrape les décisions créées avant les garde-fous actuels.

    La pipeline saute volontairement une réponse déjà traitée (idempotence).
    Sans cet audit, relancer un ancien lot conserverait donc une confiance CV/
    Mathpix < 90 % ou une échelle table/grille/matching périmée. On ne recalcule
    pas silencieusement la note : on conserve le score proposé et on demande au
    professeur de le valider. Ses décisions (`source=teacher`) sont immuables.
    """
    # Commence par aligner les décisions LLM, y compris celles déjà marquées
    # review_pending par une ancienne version de la pipeline.
    changed = llm_grader.sync_confidence_reviews(db, assessment_id)
    copies = db.query(Copy).filter_by(assessment_id=assessment_id).all()
    # Une copie tardive ne fait pas rouvrir les copies déjà rendues de la classe.
    if db.query(ScanBatch).filter(ScanBatch.assessment_id == assessment_id,
                                  ScanBatch.overlay_wave > 0).first():
        copies = [c for c in copies if c.status != "finalized"]
    copy_ids = [c.id for c in copies]
    if not copy_ids:
        return changed
    items = db.query(CopyItem).filter(CopyItem.copy_id.in_(copy_ids)).all()
    opened = changed
    for item in items:
        resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
        if resp is None:
            continue
        decision = (db.query(GradingDecision).filter_by(response_id=resp.id)
                    .order_by(GradingDecision.created_at.desc()).first())
        if decision is None or decision.source == "teacher" or decision.status != "auto":
            continue
        zone = db.get(ResponseZone, resp.zone_id) if resp.zone_id else None
        issue = (_grading_contract_issue(db, item, zone) if zone else "zone_item_mismatch")

        # Lecture source seulement : un essai DeepSeek postérieur ne doit pas
        # masquer la confiance du texte/cases que Mathpix ou le CV lui a fourni.
        source_ocr = effective_reading(db, resp.zone_id)
        # OCRiser est une étape distincte et antérieure. Une décision DeepSeek
        # validée selon son propre seuil ne doit jamais être rouverte ensuite à
        # cause de l'ancienne confiance du scan source.
        if issue is None and source_ocr is not None and decision.source != "deepseek":
            try:
                source_conf = float(source_ocr.confidence)
            except (TypeError, ValueError):
                source_conf = 0.0
            if not math.isfinite(source_conf) or source_conf < ocr_confidence_threshold(db):
                issue = "ocr_low_confidence"

        # Les comparateurs à plusieurs unités doivent être notés sur leur
        # cardinalité réelle, jamais sur un ancien max_score arbitraire.
        comparator = (item.grading_json or {}).get("comparator")
        canonical_max = None
        if comparator == "table_cells":
            canonical_max = len(grader.fillable_cells(item.grading_json or {}))
        elif comparator == "grid":
            canonical_max = len((item.grading_json or {}).get("rows") or [])
        elif comparator == "matching":
            canonical_max = len((item.expected_json or {}).get("pairs") or [])
        if (issue is None and canonical_max is not None
                and abs(float(decision.max_score or 0) - canonical_max) > 1e-9):
            issue = "grading_scale_mismatch"

        if issue is None:
            continue
        decision.evidence_json = {**(decision.evidence_json or {}),
                                  "audit_previous_reason": decision.reason_code,
                                  "audit_issue": issue}
        decision.reason_code = issue
        decision.tier = "D"
        decision.status = "review_pending"
        if not db.query(ManualReview).filter_by(decision_id=decision.id).first():
            category = "ocr_ambigu" if issue == "ocr_low_confidence" else "bareme"
            db.add(ManualReview(decision_id=decision.id, category=category))
        opened += 1
    return opened


def process_batch(db: Session, batch: ScanBatch, ocr_mode_override: str | None = None):
    """Exécute le pipeline jusqu'à graded/review_pending. Idempotent et reprenable."""
    assessment = db.get(Assessment, batch.assessment_id)
    _set_status(db, batch, "uploaded")
    if not db.query(Copy).filter_by(assessment_id=assessment.id).count():
        batch.error = "Aucune copie générée pour cette évaluation"
        db.commit()
        return

    if batch.source_file_id:
        _process_real(db, batch, assessment, mode_override=ocr_mode_override)
        # scan illisible (aucune page reconnue) : _process_real a posé un message
        # d'erreur clair et s'est arrêté — on ne marque pas « corrigé » un lot vide.
        if batch.error:
            return
        if batch.status == "ocr_local_pending":
            return          # la lecture locale reprendra la pipeline (ocr_cascade.resume_ready_batches)
    else:
        _process_mock(db, batch, assessment)
        # Chemin synthétique historique des tests : aucune copie physique à
        # reprendre dans OCRiser, on conserve sa correction bout en bout.
        _grade_with_llm(db, llm_grader.requeue_unavailable(db, assessment.id), batch)
        audit_automatic_decisions(db, assessment.id)
        n_review = open_reviews(db, assessment.id)
        _set_status(db, batch, "ocr_confirmed")
        _set_status(db, batch, "graded")
        if n_review:
            _set_status(db, batch, "review_pending", pending=n_review)
            db.commit()
        else:
            finalize_batch(db, batch)
        return

    _after_reading(db, batch, assessment)


def _after_reading(db: Session, batch: ScanBatch, assessment: Assessment) -> None:
    """Suite commune de la lecture : porte OCRiser puis notation. Appelée par process_batch
    (lecture Mathpix) et par resume_after_local (lecture locale terminée)."""
    # Halte explicite entre LECTURE et CORRECTION. Le professeur réécrit ici la
    # réponse de l'élève ; aucun corrigé, comparateur Python ou LLM n'a encore été
    # consulté. Une relance recompte les lectures effectives (teacher_ocr inclus),
    # elle ne perd donc pas les reprises déjà validées.
    pending_ocr = len(pending_ocr_responses(db, assessment.id))
    if pending_ocr:
        _set_status(db, batch, "ocr_review_pending", pending=pending_ocr,
                    threshold=ocr_confidence_threshold(db))
        return
    _set_status(db, batch, "ocr_confirmed")
    grade_stored_responses(db, batch)


def resume_after_local(db: Session, batch: ScanBatch) -> None:
    """Reprise d'un lot `ocr_local_pending` : toutes les découpes sont décidées et les lectures
    `ocr_fusion` écrites. On enchaîne exactement comme après une lecture Mathpix."""
    assessment = db.get(Assessment, batch.assessment_id)
    _set_status(db, batch, "cropped")
    _set_status(db, batch, "ocr_complete")
    _after_reading(db, batch, assessment)


class FinalizeInProgress(RuntimeError):
    """Une finalisation du même lot tourne déjà dans ce processus."""


# Lots en cours de finalisation (API mono-processus). Sur le NAS, finaliser une
# classe entière dure assez longtemps pour que les rafraîchissements de la page
# Corrections (auto-réparation de `_batch_view`) en relancent une 2e en
# parallèle : double INSERT copy_results -> UniqueViolation, lot « bloqué »
# alors que la 1re finalisation avait réussi.
_FINALIZING: set[str] = set()
_FINALIZING_LOCK = threading.Lock()


def finalize_batch(db: Session, batch: ScanBatch) -> dict:
    """Verrouille les décisions, consolide les résultats (points de barème,
    note sur la base choisie — services.scoring), crée les preuves de
    compétence et met à jour la courbe d'oubli (§7.5). Refuse s'il reste des
    revues ouvertes, ou si une finalisation du même lot est déjà en cours."""
    with _FINALIZING_LOCK:
        if batch.id in _FINALIZING:
            raise FinalizeInProgress("Validation déjà en cours pour ce lot")
        _FINALIZING.add(batch.id)
    try:
        return _finalize_batch(db, batch)
    finally:
        with _FINALIZING_LOCK:
            _FINALIZING.discard(batch.id)


def _finalize_batch(db: Session, batch: ScanBatch) -> dict:
    # Un ancien lot peut contenir des décisions automatiques produites avant le
    # seuil à 90 % ou avant la réparation des échelles. L'audit est persisté
    # AVANT de refuser la finalisation, afin que la file manuelle soit visible
    # dès le retour 409 de l'API.
    if audit_automatic_decisions(db, batch.assessment_id):
        db.commit()
    pending = (db.query(ManualReview).join(GradingDecision)
               .join(StudentResponse, GradingDecision.response_id == StudentResponse.id)
               .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
               .join(Copy, CopyItem.copy_id == Copy.id)
               .filter(Copy.assessment_id == batch.assessment_id,
                       ManualReview.resolved_at.is_(None)).count())
    if pending:
        raise ValueError(f"{pending} validation(s) professeur restante(s)")

    assessment = db.get(Assessment, batch.assessment_id)
    # Sujet déjà validé (copies tardives, Recalculer) : une preuve arrivée après
    # coup doit être rejouée à sa date, pas appliquée après des devoirs plus récents.
    already_final = assessment.status == "finalized"
    copies = db.query(Copy).filter_by(assessment_id=assessment.id).all()
    n_evidence = 0
    n_results = 0
    for copy in copies:
        if copy.status in ("generated", "printed"):
            # La correction est finalisée sans que cette copie soit revenue :
            # elle devient explicitement absente dans le carnet de notes.
            copy.status = "absent"
            continue
        if copy.status != "finalized" and (copy.status == "absent" or copy_is_blank(db, copy)):
            copy.status = "absent"
            continue  # absents et copies non scannées : jamais pénalisés
        # Résultats consolidés de l'élève à ce sujet (points de barème par
        # exercice + note sur la base choisie) : le suivi personnalisé est
        # écrit ICI, à la finalisation, et pas à la création de l'overlay —
        # un professeur qui finalise sans imprimer d'overlay a quand même
        # corrigé, l'élève a quand même une note (§ barème).
        if scoring.compute_copy_result(db, copy, assessment) is not None:
            n_results += 1
        error_journal.sync_copy(db, copy, assessment)
        items = db.query(CopyItem).filter_by(copy_id=copy.id).all()
        # Une preuve par exercice et compétence, quel que soit le nombre de
        # validations : la courbe d'oubli ne compte jamais deux fois un devoir.
        known = {(e.item_id, e.competency_id) for e in db.query(CompetencyEvidence).filter(
            CompetencyEvidence.item_id.in_([i.id for i in items]))} if items else set()
        replayed: set[str] = set()
        for item in items:
            resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
            if not resp:
                continue
            decision = (db.query(GradingDecision).filter_by(response_id=resp.id)
                        .order_by(GradingDecision.created_at.desc()).first())
            if not decision or decision.status == "review_pending":
                continue
            ratio = decision.score / decision.max_score if decision.max_score else 0
            # Preuve datée du JOUR DU DEVOIR, jamais du jour de la correction :
            # c'est cette date que la courbe de l'oubli mesure, et un lot corrigé
            # dix jours plus tard repousserait sinon la date due d'autant.
            observed_at = scoring.assessment_date(assessment, copy)
            for ec in db.query(ExerciseCompetency).filter_by(exercise_id=item.catalog_id):
                if (item.id, ec.competency_id) in known:
                    continue
                ev = CompetencyEvidence(
                    student_id=copy.student_id, competency_id=ec.competency_id,
                    item_id=item.id, mode=assessment.type, score_ratio=ratio,
                    observed_at=observed_at,
                    difficulty=item.difficulty, weight=ec.weight * ec.evidence_strength)
                db.add(ev)
                db.flush()
                if already_final:
                    replayed.add(ec.competency_id)
                else:
                    apply_evidence(db, ev)
                n_evidence += 1
        if replayed:
            replay_states(db, copy.student_id, sorted(replayed))
        # Les états de maîtrise ajoutés ci-dessus doivent être visibles du
        # calcul malgré l'autoflush désactivé de la session applicative.
        db.flush()
        student = db.get(Student, copy.student_id)
        if student is not None:
            update_level_after_assessment(db, student, assessment.id)
        copy.status = "finalized"
    assessment.status = "finalized"
    _set_status(db, batch, "finalized", evidence=n_evidence, results=n_results)
    # Enchaînement AUTOMATIQUE validation → copies corrigées : les résultats et
    # preuves de compétence sont écrits, on génère dans la foulée l'overlay et
    # l'aperçu « copie + overlay » (status → overlay_ready). Le professeur n'a
    # donc qu'UNE action manuelle (valider) ; la suite coule toute seule.
    #
    # Une panne de rendu ne détruit pas la finalisation (résultats déjà en base,
    # overlay régénérable), mais elle ne doit PAS être avalée en silence : sans
    # signal, le professeur voyait « prêt à imprimer » sans aucun PDF. On la
    # remonte donc sur batch.error — l'UI affiche alors « bloqué » avec un bouton
    # de relance qui ne refait QUE les overlays (cf. scans.retry_batch).
    batch.error = None
    overlay_error = None
    try:
        build_overlays(db, batch)
    except Exception as e:  # noqa: BLE001 — on veut remonter tout échec de rendu
        overlay_error = f"Copies corrigées non générées : {e}"
        # Un échec de rendu qui provient d'une écriture en base (contrainte,
        # type — cf. piège SQLite/Postgres) laisse la transaction AVORTÉE :
        # sans rollback, le db.commit() ci-dessous re-lève (PendingRollbackError)
        # et l'endpoint renvoie un 500 au lieu de dégrader proprement — c'est CE
        # 500 que voyait le professeur en cliquant « Valider ». La finalisation
        # (résultats, preuves) est déjà committée par _set_status plus haut ; le
        # rollback ne défait donc QUE la tentative d'overlay, puis on persiste
        # l'erreur sur batch.error dans une transaction propre.
        db.rollback()
        batch.error = overlay_error
        db.commit()
    return {"evidence_created": n_evidence, "results_created": n_results,
            "overlay_error": overlay_error}


def _latest_ocr(db: Session, zone_id: str) -> OcrAttempt | None:
    return (db.query(OcrAttempt).filter_by(zone_id=zone_id)
            .order_by(OcrAttempt.created_at.desc()).first())


def _card_right(zone: ResponseZone) -> float:
    """Abscisse du bord DROIT du cadre de la carte exercice : la bande de
    correction (posée sous toute carte simple) est en retrait de CARD_PAD de
    chaque côté ; à défaut (partie d'un composite), la zone couvre la carte."""
    strip = (zone.meta_json or {}).get("correction_strip")
    if strip and strip.get("w_pt"):
        return float(strip["x_pt"]) + float(strip["w_pt"]) + CARD_PAD
    return float(zone.x_pt) + float(zone.w_pt)


def _center_y(geo: dict) -> float:
    return float(geo["y_pt"]) + float(geo["h_pt"]) / 2


def _rows_by_y(geos: list[dict], tol: float = 3.0) -> list[list[dict]]:
    """Regroupe des rectangles par LIGNE (centres à moins de `tol` pt), de haut
    en bas."""
    rows: list[list[dict]] = []
    for g in sorted(geos, key=lambda g: -_center_y(g)):
        if rows and abs(_center_y(rows[-1][0]) - _center_y(g)) <= tol:
            rows[-1].append(g)
        else:
            rows.append([g])
    return rows


def _student_pairs(db: Session, zone: ResponseZone, decision: GradingDecision) -> set:
    ocr = _latest_ocr(db, zone.id)
    pairs = (ocr.raw_json or {}).get("pairs") if ocr else None
    if pairs is None:
        resp = db.get(StudentResponse, decision.response_id)
        pairs = resp.selected_pairs if resp else []
    return {tuple(p) for p in (pairs or []) if isinstance(p, (list, tuple)) and len(p) == 2}


def _zone_marks(db: Session, item: CopyItem, zone: ResponseZone,
                decision: GradingDecision) -> dict | None:
    """Marques d'overlay d'une réponse — règle UNIQUE pour tous les types : sur
    le bord droit du cadre de la carte, une petite marque PAR LIGNE de réponse,
    alignée sur elle — coche (trait fin) si la ligne est juste, croix (trait
    épais, pour repérer les erreurs d'un coup d'œil) sinon. Plus de récap global
    par exercice : la note de barème sous la carte suffit.

    Corrections visibles, uniquement là où c'est faux :
    - QCM : la bonne réponse ENTOURÉE (case + libellé) et sa case REMPLIE ; en
      choix multiple, les réponses fausses de l'élève sont en plus BARRÉES ;
    - grille cochée : la bonne case REMPLIE sur chaque ligne fausse ;
    - points à relier : le trait attendu en pointillés, seulement pour les
      liaisons fausses ; marques alignées sur la colonne de DROITE ;
    - tableaux / réponses écrites : la marque de ligne seule.
    Une réponse partiellement créditée n'est pas « juste » : croix (les points
    obtenus restent lisibles dans la note de barème).

    Retourne {edge_x, edge: [{y, ok}], circles, strikes, fills, links} ; None si
    la géométrie manque (copies antérieures aux repères)."""
    rtype = item.response_type
    meta = zone.meta_json or {}
    expected = item.expected_json or {}
    full = decision.score >= decision.max_score
    marks = {"edge_x": _card_right(zone), "edge": [], "circles": [], "strikes": [],
             "fills": [], "links": []}

    if rtype.startswith("qcm"):
        boxes = meta.get("boxes") or []
        if not boxes:
            return None
        correct = set(expected.get("correct", []))
        resp = db.get(StudentResponse, decision.response_id)
        selected = set(resp.selected_choices or []) if resp else set()
        if full:
            # crédit plein (y compris « Juste » du professeur, qui ne réécrit pas
            # la sélection) : la sélection VAUT les bonnes réponses
            selected = set(correct)
        by_index = {b.get("index"): b for b in boxes}
        anchor = next((by_index[i] for i in sorted(selected) if i in by_index), boxes[0])
        marks["edge"].append({"y": _center_y(anchor), "ok": full})
        if not full:
            rects = qcm_answer_rects((item.grading_json or {}).get("choices") or [],
                                     {"x_pt": zone.x_pt, "y_pt": zone.y_pt,
                                      "w_pt": zone.w_pt, "h_pt": zone.h_pt}, boxes)
            marks["circles"] = [rects[i] for i in sorted(correct) if i in rects]
            # bonne case aussi REMPLIE (comme les grilles) : la réponse attendue
            # est montrée exactement comme l'élève aurait dû la cocher
            marks["fills"] = [by_index[i] for i in sorted(correct) if i in by_index]
            if not _qcm_exclusive(item):
                marks["strikes"] = [rects[i] for i in sorted(selected - correct) if i in rects]
        return marks

    if rtype == "checkbox_grid":
        rows = expected.get("rows") or []
        ocr = _latest_ocr(db, zone.id)
        selected = ((ocr.raw_json or {}).get("grid_selected") if ocr else None)
        if selected is None:
            resp = db.get(StudentResponse, decision.response_id)
            selected = list(resp.selected_choices or []) if resp else []
        if full:                        # crédit plein : chaque ligne vaut sa bonne colonne
            selected = [r.get("correct") for r in rows]
        by_row: dict[int, list[dict]] = {}
        for b in meta.get("boxes") or []:
            if b.get("row") is not None:
                by_row.setdefault(int(b["row"]), []).append(b)
        if not by_row:
            return None
        for ri in sorted(by_row):
            correct = rows[ri].get("correct") if ri < len(rows) else None
            sel = selected[ri] if ri < len(selected) else -1
            ok = sel == correct
            marks["edge"].append({"y": _center_y(by_row[ri][0]), "ok": ok})
            if not ok:
                marks["fills"] += [b for b in by_row[ri] if b.get("col") == correct]
        return marks

    if rtype in ("table_fill", "multi_blank"):
        ocr = _latest_ocr(db, zone.id)
        raw_json = (ocr.raw_json or {}) if ocr else {}
        # verdicts explicites du professeur (correction case par case) : ils
        # font foi sur la relecture du texte
        credits = grader.cell_marks(item.grading_json, raw_json.get("cells") or [],
                                    raw_json.get("cell_credits"))
        exp_cells = expected.get("cells", [])
        cells, k = [], 0
        for ri, row in enumerate(meta.get("cells", [])):
            for ci, cell in enumerate(row):
                given = (ri < len(exp_cells) and ci < len(exp_cells[ri])
                         and exp_cells[ri][ci].get("given"))
                if given or not cell:
                    continue
                credit = 1.0 if full else (credits[k] if k < len(credits) else 0.0)
                if cell.get("input") == "single_choice" and credit < 1.0:
                    want = exp_cells[ri][ci]["value"]
                    marks["fills"] += [box for box in cell.get("boxes", []) if box["value"] == want]
                cells.append({**cell, "_ok": credit >= 1.0})
                k += 1
        if not cells:
            return None
        for line in _rows_by_y(cells):
            marks["edge"].append({"y": _center_y(line[0]), "ok": all(c["_ok"] for c in line)})
        return marks

    if rtype == "matching":
        left = {p.get("index"): p for p in meta.get("left_points", [])}
        right = {p.get("index"): p for p in meta.get("right_points", [])}
        if not right:
            return None
        exp_pairs = [tuple(p) for p in expected.get("pairs", [])]
        got = set(exp_pairs) if full else _student_pairs(db, zone, decision)
        by_right = {rj: (li, rj) for li, rj in exp_pairs}
        horizontal = meta.get("orientation") == "horizontal"
        trap_links = {pair[1] for pair in got} - set(by_right)
        for rj in sorted(right, key=lambda j: -_center_y(right[j])):
            if rj in trap_links:
                # piège relié par l'élève : une croix sur ce point, sans trait attendu
                rp = right[rj]
                if horizontal:
                    marks["edge"].append({"x": rp["x_pt"] + rp["w_pt"] + EDGE_MARK_SIZE / 2 + 0.6 * mm,
                                          "y": _center_y(rp), "ok": False})
                else:
                    marks["edge"].append({"y": _center_y(rp), "ok": False})
                continue
            if rj not in by_right:
                continue
            pair = by_right[rj]
            ok = pair in got
            rp = right[rj]
            if horizontal:
                # rangée du bas : marque à droite de chaque point (une seule
                # ordonnée pour toutes les réponses, le bord de carte ne suffit pas)
                marks["edge"].append({"x": rp["x_pt"] + rp["w_pt"] + EDGE_MARK_SIZE / 2 + 0.6 * mm,
                                      "y": _center_y(rp), "ok": ok})
            else:
                marks["edge"].append({"y": _center_y(rp), "ok": ok})
            lp = left.get(pair[0])
            if not ok and lp:
                rp = right[rj]
                marks["links"].append({"x1": lp["x_pt"] + lp["w_pt"] / 2, "y1": _center_y(lp),
                                       "x2": rp["x_pt"] + rp["w_pt"] / 2, "y2": _center_y(rp)})
        return marks

    if rtype in ("multiline_text", "manual_drawing"):
        # réponse sur plusieurs lignes : marque face à la 1re ligne d'écriture
        y = float(zone.y_pt) + float(zone.h_pt) - 4.0 * mm
    else:                                   # short_text (en ligne ou en zone) et repli
        y = float(zone.y_pt) + float(zone.h_pt) / 2
    marks["edge"].append({"y": y, "ok": full})
    return marks


def _copies_in_scan_order(db: Session, batch: ScanBatch,
                          assessment_id: str) -> list[Copy]:
    """Copies scannées dans l'ordre ADF, avec repli ordre de classe uniquement
    pour les anciens lots simulés ou incomplets qui n'ont pas de ScannedPage."""
    copies = (db.query(Copy).join(Student, Copy.student_id == Student.id)
              .filter(Copy.assessment_id == assessment_id)
              .order_by(Student.order_index, Student.id).all())
    scan_positions: dict[str, int] = {}
    scanned = (db.query(ScannedPage, DocumentPage)
               .join(DocumentPage, ScannedPage.page_id == DocumentPage.id)
               .filter(ScannedPage.batch_id == batch.id,
                       ScannedPage.status == "registered")
               .order_by(ScannedPage.source_index).all())
    for scanned_page, document_page in scanned:
        scan_positions.setdefault(document_page.copy_id, scanned_page.source_index)
    class_fallback = {copy.id: i for i, copy in enumerate(copies)}
    copies.sort(key=lambda copy: (
        0 if copy.id in scan_positions else 1,
        scan_positions.get(copy.id, class_fallback[copy.id]),
        class_fallback[copy.id],
    ))
    return copies


def _scan_page_positions(db: Session, batch: ScanBatch) -> dict[str, int]:
    """page_id -> position absolue dans le flux numérique de l'ADF."""
    return {page_id: source_index for page_id, source_index in
            db.query(ScannedPage.page_id, ScannedPage.source_index)
              .filter(ScannedPage.batch_id == batch.id,
                      ScannedPage.status == "registered",
                      ScannedPage.page_id.is_not(None)).all()}


def copy_overlay_pages(db: Session, assessment: Assessment, copy: Copy,
                       scan_page_positions: dict[str, int], *,
                       persist: bool = True) -> list[dict]:
    """Pages de correction d'UNE copie, dans l'ordre ADF : une entrée par page
    scannée portant au moins une réponse notée, l'en-tête (note, appréciation)
    sur la première. Liste vide pour une copie non scannée.

    Source unique du contenu imprimé : `build_overlays` l'assemble pour tout le
    lot, l'aperçu « Affiner la correction » (services.refine) le rend page par
    page — l'écran montre donc exactement ce que l'overlay imprimera.

    `persist=False` (aperçu) : aucune écriture en base et jamais d'appel LLM —
    l'appréciation en cache est reprise telle quelle.

    Chaque page porte `page_id`, `background` (scan recalé, ou None) et les clés
    d'ordre `_scan_index` / `_page_no`, que l'appelant retire avant le rendu."""
    derived_dir = settings.data_dir / "assessments" / assessment.id / "scans" / "derived"
    student = db.get(Student, copy.student_id)
    if copy.status == "absent":
        # Conserver exactement les positions scannées, sans note ni pénalité.
        return [{"page_id": p.id, "student": student.name, "assessment_type": assessment.type,
                 "page_zones": [], "comment": "Absent" if p.page_no == 1 else "",
                 "background": str(derived_dir / f"page-{p.id}.png"),
                 "_scan_index": scan_page_positions[p.id], "_page_no": p.page_no}
                for p in db.query(DocumentPage).filter_by(copy_id=copy.id)
                if p.id in scan_page_positions]
    items = db.query(CopyItem).filter_by(copy_id=copy.id).order_by(CopyItem.sequence).all()
    # zones regroupées par page du document (pour l'aperçu copie+overlay,
    # une page PDF = une page scannée), et n° de page pour l'ordre
    zones_by_page: dict[str, list] = {}
    page_no: dict[str, int] = {}
    group_messages: dict[tuple, list[str]] = {}
    group_zones: dict[tuple, tuple] = {}
    for item in items:
        resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
        zone = db.query(ResponseZone).filter_by(item_id=item.id).first()
        if not resp or not zone:
            continue
        decision = (db.query(GradingDecision).filter_by(response_id=resp.id)
                    .order_by(GradingDecision.created_at.desc()).first())
        if not decision:
            continue
        # Points affichés à côté de l'exercice = points de BARÈME, pas le
        # score interne du moteur (3/4 cellules justes n'est pas « 3 points »
        # si l'exercice en vaut 2) : c'est ce qui rend l'overlay lisible,
        # les points des exercices s'additionnant alors exactement jusqu'à
        # la note de l'en-tête.
        bareme = scoring.item_bareme(item.grading_json, item.response_type)
        earned = scoring.earned_points(decision.score, decision.max_score, bareme)
        full = decision.score >= decision.max_score
        zdict = {"x_pt": zone.x_pt, "y_pt": zone.y_pt, "w_pt": zone.w_pt,
                 "h_pt": zone.h_pt, "score": earned,
                 "max_score": bareme, "full_credit": full,
                 "response_type": item.response_type,
                 "strip": (zone.meta_json or {}).get("correction_strip"),
                 # plus de guide à l'overlay : les guides sont des encadrés
                 # de l'énoncé, imprimés (ou non) dès le sujet
                 "text": "",
                 "marks": _zone_marks(db, item, zone, decision),
                 # repères de l'aperçu interactif, ignorés par le rendu PDF
                 "response_id": resp.id, "sequence": item.sequence}
        # Micro-correction : recalculée à chaque rendu, jamais stockée. Absente des
        # exercices sans contrat, dont l'overlay reste donc strictement identique.
        selection = microcorrection.select(item, decision, resp, _latest_ocr(db, zone.id))
        if selection is not None and selection.text:
            if zdict["strip"]:
                zdict["feedback"] = typography.french(selection.text)
                zdict["dyslexic"] = bool(student.dyslexic)
            else:
                group_messages.setdefault((zone.page_id, item.sequence), []).append(selection.text)
        group_strip = (zone.meta_json or {}).get("group_correction_strip")
        if group_strip:
            group_zones[(zone.page_id, item.sequence)] = (zdict, group_strip)
        zones_by_page.setdefault(zone.page_id, []).append(zdict)
        if zone.page_id not in page_no:
            dp = db.get(DocumentPage, zone.page_id)
            page_no[zone.page_id] = dp.page_no if dp else 0
        if persist:
            db.add(Annotation(copy_id=copy.id, page_id=zone.page_id, zone_id=zone.id,
                              content="",
                              color=settings.correction_color,
                              geometry_json={"x_pt": zone.x_pt, "y_pt": zone.y_pt}))
    if not zones_by_page:
        return []  # copie non scannée : pas d'overlay
    # Parties d'un composite : une seule bande, messages dans l'ordre tant qu'ils tiennent.
    for key, messages in group_messages.items():
        if key in group_zones:
            first, strip = group_zones[key]
            text = microcorrection.group_text(messages)
            if text:
                first.update(group_strip=strip, group_feedback=typography.french(text),
                             dyslexic=bool(student.dyslexic))

    # Résultats consolidés à la finalisation (services.scoring) : la note
    # imprimée est CELLE STOCKÉE, jamais un second calcul — deux formules
    # pour une même note finiraient par diverger.
    result = (scoring.copy_result(db, copy, assessment) if persist
              else db.query(CopyResult).filter_by(copy_id=copy.id).first())
    note = None
    note_raw = None
    if result is not None and result.note is not None:
        note = f"{scoring.format_points(result.note)}/{result.note_base}"
        note_raw = (f"{scoring.format_points(result.points_earned)}/"
                    f"{scoring.format_points(result.points_total)}")

    if copy.appreciation_json is None and persist:
        appreciation = build_appreciation(db, assessment.id, student)
        copy.appreciation_json = appreciation
        db.add(copy)
    else:
        appreciation = copy.appreciation_json or {}

    # l'appréciation imprimée rejoint le résultat consolidé : le suivi d'un
    # élève tient alors dans une seule ligne (points, note, appréciation)
    if result is not None and persist:
        result.appreciation = appreciation.get("synthesis") or ""
        result.progress_json = {"progress": appreciation.get("progress") or []}
        db.add(result)

    comment = ""
    if result is not None:
        comment = (f"Score {scoring.format_points(result.points_earned)}/"
                   f"{scoring.format_points(result.points_total)} points")
    header = {"note": note, "note_raw": note_raw, "progress": appreciation.get("progress"),
              "synthesis": appreciation.get("synthesis"), "comment": comment}
    # Une page d'overlay PAR page scannée — indispensable en recto-verso et
    # sur les sujets longs. L'ordre interne suit lui aussi l'ADF ; page_no
    # n'est qu'un repli pour les anciens lots sans positions persistées.
    ordered_page_ids = sorted(zones_by_page, key=lambda p: (
        0 if p in scan_page_positions else 1,
        scan_page_positions.get(p, page_no.get(p, 0)),
        page_no.get(p, 0),
    ))
    # l'en-tête de correction (note, appréciation…) se pose sur la 1re page de la copie
    # (celle qui porte la bande d'identité), sinon sur la première page rendue
    head_id = next((p for p in ordered_page_ids if page_no.get(p) == 1),
                   ordered_page_ids[0] if ordered_page_ids else None)
    pages = []
    for k, page_id in enumerate(ordered_page_ids):
        bg = derived_dir / f"page-{page_id}.png"
        pages.append({
            "page_id": page_id, "student": student.name,
            "assessment_type": assessment.type,
            "page_zones": zones_by_page[page_id],
            "background": str(bg) if bg.exists() else None,
            **(header if page_id == head_id else {}),
            "_scan_index": scan_page_positions.get(page_id),
            "_page_no": page_no.get(page_id, 0),
        })
    return pages


def _publish(path: Path, render) -> None:
    """Rend dans un fichier temporaire voisin, puis le substitue à `path`
    (os.replace, atomique) : un rendu interrompu ne laisse jamais un PDF tronqué
    à la place de l'overlay imprimable, et deux rendus concurrents ne se
    marchent pas dessus."""
    fd, raw = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(raw)
    try:
        render(str(tmp))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_overlays(db: Session, batch: ScanBatch) -> str:
    """Génère, après finalisation (§5.6) :
    - correction_overlay.pdf : pages blanches, marques seules (à imprimer et
      surimposer physiquement sur la copie via les fiduciels) ;
    - correction_review.pdf : scan recalé de l'élève EN FOND + marques (aperçu
      « copie + overlay », pour relire la correction à l'écran).

    Reconstruit TOUT le lot depuis la base : une copie affinée change seulement
    ses propres pages, les autres sont rendues à l'identique, à la même place.
    Chaque enchaînement scanné (vague, cf. services.late_copies) a son propre PDF
    imprimable : l'overlay d'une pile ne contient que les feuilles de cette pile.
    """
    assessment = db.get(Assessment, batch.assessment_id)
    # Version des corrections affinées couverte par ce rendu, relue en base (un
    # affinage concurrent pendant le rendu laisse le lot marqué « à régénérer »).
    refine_rev = (db.query(ScanBatch.refine_rev).filter(ScanBatch.id == batch.id)
                  .scalar() or 0)
    out_dir = settings.data_dir / "assessments" / assessment.id / "overlays"
    out_dir.mkdir(parents=True, exist_ok=True)
    review_path = out_dir / "correction_review.pdf"
    derived_dir = settings.data_dir / "assessments" / assessment.id / "scans" / "derived"
    # page_id -> (DocumentPage, Copy), pour nommer un placeholder « bloqué »
    # dont l'identité EST connue (lue ou reliée à la main) même sans recalage.
    page_index = {p.id: (p, c) for p, c in
                  db.query(DocumentPage, Copy)
                    .join(Copy, DocumentPage.copy_id == Copy.id)
                    .filter(Copy.assessment_id == assessment.id).all()}

    copies = _copies_in_scan_order(db, batch, assessment.id)
    scan_page_positions = _scan_page_positions(db, batch)
    # Une annotation par zone et par rendu : sans purge, chaque régénération
    # (relance, affinage) empilait des doublons.
    if copies:
        (db.query(Annotation).filter(Annotation.copy_id.in_([c.id for c in copies]))
         .delete(synchronize_session=False))
    # Autorité d'ordre : la séquence des pages réellement produite par l'ADF.
    # Elle peut être totalement différente de l'ordre de classe. Le QR fait le
    # lien avec l'élève ; il ne doit jamais servir de prétexte à retrier la pile.
    pages_annotations = []
    review_pages = []
    for copy in copies:
        for page in copy_overlay_pages(db, assessment, copy, scan_page_positions):
            page.pop("page_id")
            background = page.pop("background")
            page["_copy_index"] = len(pages_annotations)
            pages_annotations.append(page)
            review_pages.append({**page, "background": background})

    # INVARIANT PHYSIQUE : une page entrée ADF = une page overlay, à la même
    # position. Tout scan bloqué, doublon dans le lot, QR illisible ou page
    # reconnue mais non corrigée reçoit une page blanche « Non identifié ».
    # Il est interdit de la supprimer : les corrections suivantes seraient alors
    # posées sur les mauvais élèves. Seule exception : une page que le
    # professeur a explicitement écartée (résolution des scans bloqués, « erreur
    # de scan ») — elle n'a jamais été une vraie copie, elle sort donc du flux.
    scan_rows = (db.query(ScannedPage).filter_by(batch_id=batch.id, dismissed=False)
                 .order_by(ScannedPage.source_index).all())
    represented = {p.get("_scan_index") for p in pages_annotations
                   if p.get("_scan_index") is not None}
    for scanned_page in scan_rows:
        if scanned_page.source_index in represented:
            continue
        order_meta = {"_scan_index": scanned_page.source_index,
                      "_copy_index": scanned_page.source_index,
                      "_page_no": 0, "_wave": scanned_page.wave or 0}
        # Une identité CONNUE (lue ou reliée à la main) mais dont le recalage a
        # échoué (page abîmée) garde son nom sur le placeholder, au lieu du
        # « Non identifié » générique — la correction reste manuelle mais le
        # bon élève reçoit sa feuille.
        label = "Non identifié"
        known_page = scanned_page.page_id or scanned_page.replaces_page_id
        if known_page and known_page in page_index:
            _, placeholder_copy = page_index[known_page]
            placeholder_student = db.get(Student, placeholder_copy.student_id)
            if placeholder_student:
                label = f"{placeholder_student.name} — à corriger manuellement"
        # Feuille à sa place dans la pile, mais rien à y poser : page sans marque.
        if scanned_page.status in late_copies.SKIPPED_BY_PIPELINE:
            order_meta["blank"] = True
        placeholder = {
            "student": label, "unidentified": True,
            "assessment_type": assessment.type, "page_zones": [], **order_meta,
        }
        pages_annotations.append(placeholder)
        bg = derived_dir / f"source-{scanned_page.source_index}.png"
        review_pages.append({**placeholder,
                             "background": str(bg) if bg.exists() else None})

    # Même si un ADF produit un flux inhabituel (rectos de plusieurs copies puis
    # versos), le PDF de correction reproduit exactement sa séquence globale.
    def physical_key(page: dict) -> tuple:
        pos = page.get("_scan_index")
        return (0, pos) if pos is not None else (
            1, page.get("_copy_index", 0), page.get("_page_no", 0))

    wave_of = {row.source_index: row.wave or 0 for row in scan_rows}
    for page in pages_annotations:
        page["_wave"] = wave_of.get(page.get("_scan_index"), 0)
    pages_annotations.sort(key=physical_key)
    review_pages.sort(key=physical_key)
    if scan_rows and (len(pages_annotations) != len(scan_rows)
                      or len(review_pages) != len(scan_rows)):
        raise ValueError(
            "Invariant d'ordre rompu : le nombre de pages de correction ne "
            "correspond pas au nombre de pages sorties de l'ADF")
    for page in [*pages_annotations, *review_pages]:
        page.pop("_scan_index", None)
        page.pop("_copy_index", None)
        page.pop("_page_no", None)
    waves: dict[int, list[dict]] = {}
    for page in pages_annotations:
        waves.setdefault(page.pop("_wave", 0), []).append(page)
    for page in review_pages:
        page.pop("_wave", None)
    # Lot sans aucune page (cas limite) : l'overlay initial reste publié, vide.
    waves.setdefault(0, [])

    color = correction_ink(db)
    # Rattrape les décisions tranchées en revue, qui ne repassent pas par le calcul
    # du résultat : le journal suit toujours la correction imprimée.
    for copy in db.query(Copy).filter(Copy.assessment_id == assessment.id,
                                      Copy.status.notin_(("absent", "generated", "printed"))).all():
        error_journal.sync_copy(db, copy, assessment)
    # Annotations et appréciations écrites : on valide AVANT le rendu pour ne
    # pas garder la base verrouillée pendant que le professeur continue
    # d'affiner d'autres copies.
    db.commit()
    # 1) L'overlay IMPRIMABLE d'abord (marques seules, rapide) : dès qu'il est
    # publié, il fait foi et l'impression est débloquée.
    for wave, wave_pages in sorted(waves.items()):
        _publish(overlay_file(assessment.id, wave), lambda p, pages=wave_pages: render_overlay(
            p, copies_annotations=pages, color=color))
    path = overlay_file(assessment.id, 0)
    # Un overlay régénéré APRÈS affinage remplace une impression devenue fausse :
    # l'étape repasse « à imprimer ». Rendu initial ou simple relance : inchangé.
    previous_rev = (db.query(ScanBatch.overlay_rev).filter(ScanBatch.id == batch.id)
                    .scalar() or 0)
    changes = {ScanBatch.overlay_rev: refine_rev}
    if refine_rev > previous_rev:
        # Seules les piles contenant une copie affinée sont à réimprimer.
        late_copies.reopen_refined_waves(batch)
        changes[ScanBatch.overlay_printed] = batch.overlay_printed
        changes[ScanBatch.waves_json] = batch.waves_json
    db.query(ScanBatch).filter(ScanBatch.id == batch.id).update(
        changes, synchronize_session=False)
    batch.overlay_rev = refine_rev
    db.commit()
    # 2) Puis l'aperçu écran « copie + overlay » (scans en fond, plus lourd).
    _publish(review_path, lambda p: render_copy_review(
        p, review_pages=review_pages, color=color))
    _set_status(db, batch, "overlay_ready", path=str(path))
    return str(path)


def correction_ink(db: Session) -> str | None:
    """Couleur d'encre de correction réglée (Paramètres), None = défaut."""
    from .runtime_settings import get_setting
    return (get_setting(db, "correction_color") or {}).get("value")

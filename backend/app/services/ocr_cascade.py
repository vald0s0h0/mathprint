"""Orchestration de la lecture OCR multicouche (moteurs locaux -> consensus -> Mathpix).

Ce module ne charge aucun modèle : il inscrit les découpes et les tâches, fait
avancer chaque découpe selon `ocr_fusion` quand des lectures arrivent, appelle
Mathpix en dernier recours (mode cascade), agrège par zone, puis reprend la
pipeline là où elle s'était arrêtée.

Modes (`ocr_mode`) :
- `mathpix` : rien n'est inscrit, la pipeline est inchangée ;
- `shadow`  : Mathpix décide encore (en ligne) ; les découpes sont aussi inscrites,
              la fusion locale tourne et ses décisions sont enregistrées, comparées
              ensuite à Mathpix et au professeur. Aucune note n'en dépend ;
- `cascade` : la fusion décide. Le lot attend à l'état `ocr_local_pending`.

La valeur attendue n'est jamais lue ici.
"""
import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import (
    CopyItem, OcrAttempt, OcrCrop, OcrDecision, OcrEngineRead, OcrJob, ResponseZone,
    ScanBatch, StudentResponse,
)
from . import ocr_fusion as fusion
from . import ocr_queue
from . import runtime_settings as rs
from .ocr_surface import to_surface

logger = logging.getLogger(__name__)

NUM_CHARSET = "0123456789,.-−/() "
MM = 72.0 / 25.4
AUTO_DECISIONS = ("local_consensus", "mathpix_confirmed")
_CLASS_BY_TYPE = {"integer": "num_line", "decimal": "num_line", "rational": "math_line",
                  "expression": "math_line", "text": "text_line", "rubric": "text_line"}


def zone_class_for(expected_type: str | None, multiline: bool = False) -> str:
    """Classe de zone déduite du TYPE attendu et de la géométrie, jamais de la valeur."""
    if multiline:
        return "multiline_line"
    return _CLASS_BY_TYPE.get(expected_type or "", "text_line")


def charset_for(zone_class: str) -> str:
    return NUM_CHARSET if zone_class == "num_line" else ""


def class_roles(plan: dict, zone_class: str) -> dict:
    return (plan.get("classes") or {}).get(zone_class) or {"stage1": [], "stage2": [], "observe": []}


def _draw(share: float, key: str) -> bool:
    """Tirage stable (même clé, même résultat) avec la probabilité `share`."""
    if share <= 0:
        return False
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < share


def shadow_audit_pick(db: Session, zone: ResponseZone, batch: ScanBatch) -> bool:
    """Mode ombre : cette zone, acceptée par Mathpix, est-elle tirée pour le contrôle ? Tirage stable."""
    return _draw(float(rs.ocr_setting(db, "ocr_shadow_audit_share")), f"saudit:{zone.id}:{batch.id}")


# ------------------------------------------------------------- découpes

@dataclass
class CropSpec:
    png: bytes
    zone_class: str
    cell_index: int | None = None
    line_index: int | None = None
    ink_ratio: float = 0.0
    ink_width_px: int = 0
    width_px: int = 0
    height_px: int = 0


def ink_extent_px(bgr) -> int:
    """Étendue horizontale de l'encre (colonnes avec au moins 2 pixels sombres)."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    cols = np.where((gray < 128).sum(axis=0) >= 2)[0]
    return int(cols[-1] - cols[0] + 1) if len(cols) else 0


def make_spec(filtered_bgr, zone_class: str, *, cell_index: int | None = None,
              line_index: int | None = None) -> CropSpec:
    from . import worker_cv
    return CropSpec(png=worker_cv.encode_png(filtered_bgr), zone_class=zone_class,
                    cell_index=cell_index, line_index=line_index,
                    ink_ratio=worker_cv.ink_ratio(filtered_bgr), ink_width_px=ink_extent_px(filtered_bgr),
                    width_px=int(filtered_bgr.shape[1]), height_px=int(filtered_bgr.shape[0]))


def line_bands(y_pt: float, h_pt: float, n_lines: int) -> list[tuple[float, float]]:
    """Bande (bas, hauteur) en points PDF de chaque ligne d'une zone multiligne, déduite
    de la géométrie des lignes guides (pdfgen : interligne 9 mm, première ligne guide à
    1 mm + 9 mm sous le haut ; la dernière ligne descend jusqu'à 1 mm du bas). Aucune
    détection : l'élève écrit AU-DESSUS de chaque guide."""
    row = 9 * MM
    top = y_pt + h_pt - 1 * MM
    out = []
    for k in range(n_lines):
        t = top - row * k
        b = (t - row) if k < n_lines - 1 else (y_pt + 1 * MM)
        out.append((b, t - b))
    return out


def multiline_count(item: CopyItem) -> int:
    try:
        return max(3, min(12, int((item.grading_json or {}).get("lines", 5))))
    except (TypeError, ValueError):
        return 5


def line_specs(warped, zone: ResponseZone, item: CopyItem, blank_threshold: float) -> list[CropSpec]:
    """Une découpe par ligne NON vide d'une zone multiligne (les lignes vides sont ignorées)."""
    from . import worker_cv
    out = []
    for k, (b, h) in enumerate(line_bands(zone.y_pt, zone.h_pt, multiline_count(item))):
        crop = worker_cv.crop_zone(warped, zone.x_pt, b, zone.w_pt, h, padding_pt=0)
        if crop.size == 0:
            continue
        filtered = worker_cv.dropout_filter(crop)
        if worker_cv.ink_ratio(filtered) < blank_threshold:
            continue
        out.append(make_spec(filtered, "multiline_line", line_index=k))
    return out


# ------------------------------------------------------------- inscription

def _engines_to_read(roles: dict) -> list[str]:
    """À l'inscription : les moteurs de l'étape 1 et les observateurs. L'étape 2 n'est
    demandée que par la cascade elle-même ; l'échantillon complet ajoute ses lectures
    APRÈS la décision réelle, pour ne jamais la modifier."""
    return list(roles.get("stage1", [])) + list(roles.get("observe", []))


def register_crop(db: Session, batch: ScanBatch, zone: ResponseZone, spec: CropSpec, mode: str) -> OcrCrop:
    """Écrit l'image, inscrit la découpe et ses tâches de lecture. Idempotent."""
    crop = (db.query(OcrCrop).filter_by(batch_id=batch.id, zone_id=zone.id,
                                        cell_index=spec.cell_index, line_index=spec.line_index).first())
    if crop is not None:
        return crop
    sha = hashlib.sha256(spec.png).hexdigest()
    suffix = (f"-c{spec.cell_index}" if spec.cell_index is not None
              else f"-l{spec.line_index}" if spec.line_index is not None else "")
    d = settings.data_dir / "assessments" / batch.assessment_id / "scans" / "derived" / "ocr"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{zone.id}{suffix}.png"
    path.write_bytes(spec.png)
    plan = rs.ocr_engine_plan(db)
    full = _draw(rs.ocr_setting(db, "ocr_full_sample_share"), f"full:{sha}:{zone.id}{suffix}")
    crop = OcrCrop(batch_id=batch.id, zone_id=zone.id, cell_index=spec.cell_index,
                   line_index=spec.line_index, image_path=str(path.relative_to(settings.data_dir)),
                   image_sha256=sha, zone_class=spec.zone_class, charset=charset_for(spec.zone_class),
                   width_px=spec.width_px, height_px=spec.height_px, ink_ratio=spec.ink_ratio,
                   ink_width_px=spec.ink_width_px, mode=mode, status="pending", full_sample=full)
    db.add(crop)
    db.flush()
    ocr_queue.enqueue_jobs(db, crop, _engines_to_read(class_roles(plan, spec.zone_class)))
    return crop


def begin_zone(db: Session, batch: ScanBatch, zone: ResponseZone, *, kind: str, corr_id: str,
               positions: list[dict], mode: str) -> None:
    """Inscrit les découpes d'une zone et mémorise son squelette (`ocr_pending`) :
    ce que la CV a déjà lu (cases vides, cases à choix) et la place de chaque découpe.

    positions : [{"fixed": {"text", "latex", "conf"}}] ou [{"spec": CropSpec}]."""
    out = []
    for pos in positions:
        if "spec" in pos:
            crop = register_crop(db, batch, zone, pos["spec"], mode)
            out.append({"crop": crop.id})
        else:
            out.append({"fixed": pos["fixed"]})
    existing = [a for a in db.query(OcrAttempt).filter_by(zone_id=zone.id, provider="ocr_pending").all()
                if (a.raw_json or {}).get("batch_id") == batch.id]
    skeleton = {"batch_id": batch.id, "kind": kind, "corr_id": corr_id, "positions": out}
    if existing:
        existing[0].raw_json = skeleton
    else:
        db.add(OcrAttempt(zone_id=zone.id, provider="ocr_pending", raw_json=skeleton, confidence=0.0))
    db.flush()


def has_pending(db: Session, batch: ScanBatch) -> bool:
    return (db.query(OcrCrop.id).filter(OcrCrop.batch_id == batch.id, OcrCrop.mode == "cascade",
                                        OcrCrop.status != "decided").first() is not None
            or _unfinished_zones(db, batch.id) != [])


def local_progress(db: Session, batch: ScanBatch) -> dict:
    """Pour l'interface : « Lecture en cours sur le Mac (n/N) » ou « Mac hors ligne », et l'alerte
    quand le lot attend depuis plus de `ocr_local_timeout_hours`."""
    q = db.query(OcrCrop).filter(OcrCrop.batch_id == batch.id, OcrCrop.mode == "cascade")
    total = q.count()
    done = q.filter(OcrCrop.status == "decided").count()
    since = ((batch.progress_json or {}).get("ocr_local_pending") or {}).get("since")
    hours = 0.0
    try:
        if since:
            hours = (datetime.now(timezone.utc) - datetime.fromisoformat(since)).total_seconds() / 3600
    except ValueError:
        pass
    timeout = int(rs.ocr_setting(db, "ocr_local_timeout_hours"))
    return {"decided": done, "total": total, "worker_online": ocr_queue.worker_online(db),
            "waiting_hours": round(hours, 1), "timeout_hours": timeout, "overdue": hours >= timeout}


def cancel_local(db: Session, batch: ScanBatch) -> int:
    """Abandonne la lecture locale d'un lot (Mac absent trop longtemps) AVANT toute réponse écrite :
    les tâches en attente sont annulées, les découpes non décidées passent en manuel (historique
    conservé), les squelettes de zone sont mis de côté. Le lot peut alors être relu avec Mathpix."""
    n = 0
    crops = db.query(OcrCrop).filter(OcrCrop.batch_id == batch.id, OcrCrop.status != "decided").all()
    for crop in crops:
        db.query(OcrJob).filter(OcrJob.crop_id == crop.id, OcrJob.status.in_(("pending", "claimed"))
                                ).update({"status": "cancelled", "lease_until": None}, synchronize_session=False)
        prev = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
        _write_decision(db, crop, decision="manual", stage="local", reason="mac_fallback_mathpix",
                        cands=prev.candidates_json if prev else [])
        crop.status = "decided"
        n += 1
    for att in db.query(OcrAttempt).filter_by(provider="ocr_pending").all():
        if (att.raw_json or {}).get("batch_id") == batch.id:
            att.provider = "ocr_pending_cancelled"
    db.flush()
    return n


# ------------------------------------------------------------- avancement

def _role_map(roles: dict) -> dict[str, str]:
    m: dict[str, str] = {}
    for role in ("observe", "stage2", "stage1"):
        for e in roles.get(role, []):
            m[e] = role
    return m


def _reads(db: Session, crop: OcrCrop, plan: dict, roles: dict) -> list[fusion.ReadIn]:
    rolemap = _role_map(roles)
    out = []
    for r in db.query(OcrEngineRead).filter_by(crop_id=crop.id, kind="read").order_by(OcrEngineRead.engine).all():
        if r.error or r.engine not in rolemap:
            continue
        if not r.surface and r.raw_text:
            r.surface = to_surface(r.raw_text, crop.zone_class)
        out.append(fusion.ReadIn(
            engine=r.engine, family=r.family or plan.get("families", {}).get(r.engine, r.engine),
            raw=r.raw_text, surface=r.surface or "", illegible=bool(r.illegible) or not (r.surface or "").strip(),
            confidence_raw=r.confidence_raw, logprob_mean=r.logprob_mean, logprob_min=r.logprob_min,
            role=rolemap[r.engine]))
    return out


def _scores(db: Session, crop: OcrCrop) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in db.query(OcrEngineRead).filter_by(crop_id=crop.id, kind="score").all():
        if r.error:
            continue
        table = out.setdefault(r.engine, {})
        for raw, val in (r.scores_json or {}).items():
            if val is not None:
                table[to_surface(raw, crop.zone_class)] = float(val)
    return out


def _terminal(job: OcrJob | None) -> bool:
    return job is not None and job.status in ("done", "error")


def _accepted_texts(db: Session, crop: OcrCrop, reads: list[fusion.ReadIn], out: fusion.Outcome,
                    roles: dict) -> tuple[str, str]:
    """(texte brut, forme LaTeX) d'un moteur qui a lu la surface acceptée. Ni valeur
    inventée ni valeur corrigée : on reprend ce qu'un moteur a écrit."""
    order = {e: i for i, e in enumerate(roles.get("stage1", []) + roles.get("stage2", []))}
    sup = sorted((r for r in reads if r.surface == out.surface and fusion.has_text(r)
                  and r.role != "observe"), key=lambda r: order.get(r.engine, 99))
    raw = sup[0].raw if sup else (out.surface or "")
    latex = next((r.raw for r in sup if "\\" in r.raw), out.surface or raw)
    return raw, latex


def _write_decision(db: Session, crop: OcrCrop, *, decision: str, stage: str, reason: str,
                    cands: list, surface: str | None = None, raw: str | None = None,
                    latex: str | None = None, mathpix: dict | None = None) -> OcrDecision:
    row = (db.query(OcrDecision).filter_by(crop_id=crop.id).first())
    if row is None:
        row = OcrDecision(crop_id=crop.id, zone_id=crop.zone_id, batch_id=crop.batch_id)
        db.add(row)
    row.stage_reached, row.decision, row.reason = stage, decision, reason[:200]
    row.candidates_json = cands
    row.accepted_surface, row.accepted_raw, row.accepted_latex = surface, raw, latex
    row.mode = crop.mode or "shadow"
    if mathpix:
        row.mathpix_used = True
        row.cost_eur = float(mathpix.get("cost", 0.0))
        row.montage_id = mathpix.get("montage_id")
    return row


def advance_crop(db: Session, crop: OcrCrop) -> str:
    """Fait avancer UNE découpe d'un cran si ses lectures sont arrivées. Sans réseau ni
    note : seulement des écritures en base. Retourne le statut de la découpe."""
    if crop.status in ("decided", "mathpix_pending"):
        return crop.status
    plan = rs.ocr_engine_plan(db)
    th = rs.ocr_engine_thresholds(db)
    roles = class_roles(plan, crop.zone_class)
    s1, s2 = roles.get("stage1", []), roles.get("stage2", [])
    jobs = db.query(OcrJob).filter_by(crop_id=crop.id).all()
    read_jobs = {j.engine: j for j in jobs if j.kind == "read"}
    score_jobs = {j.engine: j for j in jobs if j.kind == "score"}
    if not all(_terminal(read_jobs.get(e)) for e in s1):
        return crop.status
    s2_requested = bool(s2) and any(e in read_jobs for e in s2)
    if s2_requested and not all(_terminal(read_jobs.get(e)) for e in s2 if e in read_jobs):
        return crop.status
    if score_jobs and not all(_terminal(j) for j in score_jobs.values()):
        return crop.status

    reads = _reads(db, crop, plan, roles)
    ctx = fusion.CropCtx(crop.zone_class, crop.ink_width_px, crop.height_px)
    margin = float(rs.ocr_setting(db, "ocr_score_margin"))
    out = fusion.decide_local(ctx, reads, plan, th, phase=2 if s2_requested else 1,
                              scores=_scores(db, crop) if score_jobs else None, margin=margin)

    if out.action == "need_stage2":
        ocr_queue.enqueue_jobs(db, crop, s2)
        crop.status = "pending"
    elif out.action == "need_score":
        class_engines = s1 + s2
        for eng in sorted(e for e in plan.get("scorers", []) if e in class_engines):
            ocr_queue.enqueue_jobs(db, crop, [eng], kind="score",
                                   candidates=fusion.score_candidates_for(eng, reads, out.score_surfaces))
        crop.status = "pending"
    elif out.action == "accept":
        raw, latex = _accepted_texts(db, crop, reads, out, roles)
        _write_decision(db, crop, decision="local_consensus", stage="local", reason=out.reason,
                        cands=out.candidates, surface=out.surface, raw=raw, latex=latex)
        crop.status = "decided"
    elif out.action == "manual":
        _write_decision(db, crop, decision="manual", stage="local", reason=out.reason, cands=out.candidates)
        crop.status = "decided"
    else:                                              # mathpix
        _write_decision(db, crop, decision="mathpix_pending", stage="mathpix", reason=out.reason,
                        cands=out.candidates)
        crop.status = "mathpix_pending"
        if crop.mode == "shadow":
            resolve_shadow_mathpix(db, crop)
    db.flush()
    return crop.status


def _apply_mathpix(db: Session, crop: OcrCrop, text: str, latex: str, conf: float, cost: float = 0.0,
                   montage_id: str | None = None) -> None:
    plan = rs.ocr_engine_plan(db)
    th = rs.ocr_engine_thresholds(db)
    roles = class_roles(plan, crop.zone_class)
    reads = _reads(db, crop, plan, roles)
    ctx = fusion.CropCtx(crop.zone_class, crop.ink_width_px, crop.height_px)
    out = fusion.decide_mathpix(ctx, reads, text, conf, plan, th)
    prev = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
    local_reason = prev.reason if prev else ""
    mp = {"cost": cost, "montage_id": montage_id}
    # La lecture Mathpix est gardée telle quelle (kind « mathpix », hors du vote et des
    # statistiques des moteurs locaux) : OCRiser la propose au professeur même quand la
    # découpe part en manuel.
    db.add(OcrEngineRead(crop_id=crop.id, engine="mathpix", family="mathpix", backend="api",
                         kind="mathpix", raw_text=latex or text or "",
                         surface=to_surface(text or latex or "", crop.zone_class),
                         illegible=not (text or latex or "").strip(), confidence_raw=conf))
    if out.action == "accept":
        _write_decision(db, crop, decision="mathpix_confirmed", stage="mathpix",
                        reason=f"{local_reason}>{out.reason}", cands=out.candidates, surface=out.surface,
                        raw=text, latex=latex or text, mathpix=mp)
    else:
        _write_decision(db, crop, decision="manual", stage="mathpix",
                        reason=f"{local_reason}>{out.reason}", cands=out.candidates, mathpix=mp)
    crop.status = "decided"


def shadow_mathpix_reading(db: Session, crop: OcrCrop) -> tuple[str, str, float] | None:
    """Lecture Mathpix EN LIGNE déjà payée (mode ombre) : rien n'est rappelé. Absente pour
    une ligne de zone multiligne (Mathpix a lu la zone entière)."""
    if crop.line_index is not None:
        return None
    att = (db.query(OcrAttempt).filter(OcrAttempt.zone_id == crop.zone_id, OcrAttempt.provider == "mathpix")
           .order_by(OcrAttempt.created_at.desc()).first())
    if att is None:
        return None
    raw = att.raw_json or {}
    if crop.cell_index is not None:
        cells = raw.get("cells")
        if not isinstance(cells, list) or crop.cell_index >= len(cells):
            return None
        confs = raw.get("cell_confidences") or []
        lat = (raw.get("cell_latex") or [""] * len(cells))[crop.cell_index]
        conf = float(confs[crop.cell_index]) if crop.cell_index < len(confs) else 0.0
        return str(cells[crop.cell_index]), str(lat or ""), conf
    return att.text or "", att.latex or "", float(att.confidence or 0.0)


def resolve_shadow_mathpix(db: Session, crop: OcrCrop) -> None:
    got = shadow_mathpix_reading(db, crop)
    if got is None:
        prev = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
        _write_decision(db, crop, decision="manual", stage="mathpix",
                        reason=f"{prev.reason if prev else ''}>shadow_no_mathpix_reading", cands=prev.candidates_json if prev else [])
        crop.status = "decided"
        return
    _apply_mathpix(db, crop, got[0], got[1], got[2])


# ------------------------------------------------------------- échantillon complet

def finish_full_sample(db: Session, crop: OcrCrop) -> None:
    """Échantillon complet : APRÈS la décision réelle, tous les moteurs de la classe lisent puis
    évaluent, quoi qu'il arrive ; on enregistre ce qu'aurait décidé ce passage complet
    (`full_decision`). La décision réelle n'est jamais touchée.
    États : None -> {"state": "reading"} -> {"state": "scoring"} -> résultat (clé `action`)."""
    row = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
    if not crop.full_sample or row is None or crop.status != "decided" or row.decision == "mathpix_pending":
        return
    state = row.full_decision or {}
    if "action" in state:
        return
    plan = rs.ocr_engine_plan(db)
    th = rs.ocr_engine_thresholds(db)
    roles = class_roles(plan, crop.zone_class)
    jobs = db.query(OcrJob).filter_by(crop_id=crop.id).all()
    if not state:
        have = {j.engine for j in jobs if j.kind == "read"}
        wanted = [e for k in ("stage1", "stage2", "observe") for e in roles.get(k, []) if e not in have]
        if wanted:
            ocr_queue.enqueue_jobs(db, crop, wanted)
        row.full_decision = {"state": "reading"}
        return
    reads_jobs = [j for j in jobs if j.kind == "read"]
    if not all(_terminal(j) for j in reads_jobs):
        return
    reads = _reads(db, crop, plan, roles)
    surfaces = sorted({r.surface for r in reads if fusion.has_text(r)})
    scorers = sorted(e for e in plan.get("scorers", [])
                     if e in sum((roles.get(k, []) for k in ("stage1", "stage2", "observe")), []))
    if state.get("state") == "reading":
        if len(surfaces) > 1 and scorers:
            for eng in scorers:
                ocr_queue.enqueue_jobs(db, crop, [eng], kind="score",
                                       candidates=fusion.score_candidates_for(eng, reads, surfaces))
            row.full_decision = {"state": "scoring"}
            return
    elif state.get("state") == "scoring":
        if not all(_terminal(j) for j in jobs if j.kind == "score"):
            return
    ctx = fusion.CropCtx(crop.zone_class, crop.ink_width_px, crop.height_px)
    row.full_decision = fusion.full_decision(ctx, reads, plan, th, _scores(db, crop) or None,
                                             float(rs.ocr_setting(db, "ocr_score_margin")))


# ------------------------------------------------------------- zones

def _unfinished_zones(db: Session, batch_id: str) -> list[str]:
    """Zones du lot dont les découpes (mode cascade) sont inscrites mais dont la réponse n'a
    pas encore été écrite."""
    zone_ids = {z for (z,) in db.query(OcrCrop.zone_id).filter(OcrCrop.batch_id == batch_id,
                                                               OcrCrop.mode == "cascade").distinct()}
    out = []
    for zid in zone_ids:
        item_id = db.get(ResponseZone, zid).item_id
        if db.query(StudentResponse.id).filter_by(copy_item_id=item_id).first() is None:
            out.append(zid)
    return sorted(out)


def _crop_text(db: Session, crop: OcrCrop, accept_conf: float) -> dict:
    d = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
    if d is not None and d.decision in AUTO_DECISIONS and d.accepted_surface is not None:
        return {"text": d.accepted_surface, "latex": d.accepted_latex or d.accepted_surface,
                "conf": accept_conf, "auto": True}
    return {"text": "", "latex": "", "conf": 0.0, "auto": False}      # manuel : rien d'inventé


def assemble_zone(db: Session, zone: ResponseZone, skeleton: dict) -> dict | None:
    """Lecture agrégée d'une zone, ou None tant qu'une découpe n'est pas décidée."""
    accept = float(rs.ocr_setting(db, "ocr_accept_confidence"))
    items = []
    for pos in skeleton["positions"]:
        if "fixed" in pos:
            f = pos["fixed"]
            items.append({"text": f.get("text", ""), "latex": f.get("latex", ""),
                          "conf": float(f.get("conf", 1.0)), "auto": False, "cv": True})
            continue
        crop = db.get(OcrCrop, pos["crop"])
        if crop is None or crop.status != "decided":
            return None
        items.append(_crop_text(db, crop, accept))
    return {"items": items}


def finish_zone(db: Session, batch: ScanBatch, zone: ResponseZone) -> bool:
    """Agrège une zone dont toutes les découpes sont décidées : décision de zone, audit, et
    en cascade la lecture `ocr_fusion` puis la décision existante (porte OCRiser, notation)."""
    from . import pipeline
    skel_row = next((a for a in db.query(OcrAttempt).filter_by(zone_id=zone.id, provider="ocr_pending").all()
                     if (a.raw_json or {}).get("batch_id") == batch.id), None)
    if skel_row is None:
        return False
    skeleton = skel_row.raw_json
    asm = assemble_zone(db, zone, skeleton)
    if asm is None:
        return False
    mode = "cascade" if any(c.mode == "cascade" for c in db.query(OcrCrop).filter_by(batch_id=batch.id, zone_id=zone.id)) else "shadow"
    crops = [db.get(OcrCrop, p["crop"]) for p in skeleton["positions"] if "crop" in p]
    decisions = [db.query(OcrDecision).filter_by(crop_id=c.id).first() for c in crops]
    names = [d.decision for d in decisions if d is not None]
    all_auto = bool(names) and all(n in AUTO_DECISIONS for n in names)
    zone_decision = ("manual" if any(n == "manual" for n in names)
                     else "mathpix_confirmed" if any(n == "mathpix_confirmed" for n in names)
                     else "local_consensus")
    if mode == "cascade":
        audit = all_auto and _draw(rs.ocr_setting(db, "ocr_audit_share"), f"audit:{zone.id}:{batch.id}")
    else:                                          # ombre : le tirage a été fait à la lecture Mathpix
        mp = (db.query(OcrAttempt).filter(OcrAttempt.zone_id == zone.id, OcrAttempt.provider == "mathpix")
              .order_by(OcrAttempt.created_at.desc()).first())
        audit = bool(mp and (mp.raw_json or {}).get("audit"))
    cost = sum(float(d.cost_eur or 0) for d in decisions if d is not None)
    zrow = db.query(OcrDecision).filter_by(zone_id=zone.id, crop_id=None, batch_id=batch.id).first()
    if zrow is None:
        zrow = OcrDecision(crop_id=None, zone_id=zone.id, batch_id=batch.id)
        db.add(zrow)
    zrow.stage_reached = "mathpix" if any(d and d.mathpix_used for d in decisions) else (
        "manual" if zone_decision == "manual" else "local")
    zrow.decision, zrow.reason, zrow.mode = zone_decision, f"{len(crops)} découpe(s)", mode
    crop_items = [i for i, p in zip(asm["items"], skeleton["positions"]) if "crop" in p]
    zrow.accepted_surface = ("\n" if skeleton["kind"] == "text" else "|").join(i["text"] for i in crop_items)
    zrow.mathpix_used, zrow.cost_eur, zrow.audit = any(d and d.mathpix_used for d in decisions), cost, bool(audit)
    if mode == "shadow":
        zrow.full_decision = _shadow_comparison(db, zone, skeleton, asm)
        skel_row.provider = "ocr_pending_done"
        db.flush()
        from . import ocr_truth
        ocr_truth.backfill(db, zone.id)            # le professeur a pu répondre avant la fin de la lecture locale
        return True

    # ---- cascade : lecture `ocr_fusion` au même format que Mathpix
    item = db.get(CopyItem, zone.item_id)
    from ..models import Copy, Student
    copy = db.get(Copy, item.copy_id)
    student = db.get(Student, copy.student_id)
    accept = float(rs.ocr_setting(db, "ocr_accept_confidence"))
    threshold = pipeline.ocr_confidence_threshold(db)
    items = asm["items"]
    corr_id = skeleton.get("corr_id", "")
    raw_extra: dict = {"decisions": names, "audit": bool(audit)}
    if skeleton["kind"] == "table":
        confs = [i["conf"] for i in items]
        if audit:
            confs = [min(c, max(0.0, threshold - 0.01)) for c in confs]
        min_conf = min(confs) if confs else 1.0
        db.add(OcrAttempt(zone_id=zone.id, provider="ocr_fusion", confidence=min_conf,
                          raw_json={"cells": [i["text"] for i in items],
                                    "cell_latex": [i["latex"] for i in items],
                                    "cell_confidences": confs, **raw_extra}))
        pipeline._decide_and_store(db, item=item, zone=zone, student=student, ocr_text="",
                                   conf=min_conf, selected=None, corr_id=corr_id,
                                   cell_texts=[i["text"] for i in items], queue=None, defer_grading=True)
    else:
        text = "\n".join(i["text"] for i in items)
        latex = "\n".join(i["latex"] for i in items)
        conf = min((i["conf"] for i in items), default=0.0)
        if not items:
            conf, raw_extra["no_line_detected"] = 0.0, True
        if audit:
            conf = min(conf, max(0.0, threshold - 0.01))
        db.add(OcrAttempt(zone_id=zone.id, provider="ocr_fusion", confidence=conf, text=text,
                          latex=latex, raw_json=raw_extra))
        pipeline._decide_and_store(db, item=item, zone=zone, student=student, ocr_text=text, conf=conf,
                                   selected=None, corr_id=corr_id, queue=None, defer_grading=True)
    skel_row.provider = "ocr_pending_done"
    return True


def _shadow_comparison(db: Session, zone: ResponseZone, skeleton: dict, asm: dict) -> dict:
    """Mode ombre : ce que la fusion locale aurait écrit, face à la lecture Mathpix déjà décisive."""
    att = (db.query(OcrAttempt).filter(OcrAttempt.zone_id == zone.id, OcrAttempt.provider == "mathpix")
           .order_by(OcrAttempt.created_at.desc()).first())
    local = [i["text"] for i in asm["items"]]
    out = {"local": local, "automatic": all(i.get("auto") or i.get("cv") for i in asm["items"])}
    if att is not None:
        raw = att.raw_json or {}
        mp = raw.get("cells") if skeleton["kind"] == "table" else [att.text]
        out["mathpix"] = mp
        if skeleton["kind"] == "table" and isinstance(mp, list) and len(mp) == len(local):
            out["agree"] = [to_surface(a, "text_line") == to_surface(b, "text_line") for a, b in zip(local, mp)]
        elif skeleton["kind"] == "text":
            out["agree"] = [to_surface("\n".join(local), "text_line") == to_surface(att.text or "", "text_line")]
    return out


def no_line_zone(db: Session, zone: ResponseZone, item: CopyItem, student, corr_id: str) -> None:
    """Cascade : encre dans la zone mais aucune ligne détectée -> lecture vide à confiance 0 (revue)."""
    from . import pipeline
    db.add(OcrAttempt(zone_id=zone.id, provider="ocr_fusion", confidence=0.0,
                      raw_json={"no_line_detected": True}))
    pipeline._decide_and_store(db, item=item, zone=zone, student=student, ocr_text="", conf=0.0,
                               selected=None, corr_id=corr_id, queue=None, defer_grading=True)


# ------------------------------------------------------------- Mathpix et reprise

_MATHPIX_TRIES: dict[str, int] = {}
_MATHPIX_MAX_TRIES = 3
_RESUMING: set[str] = set()
_RESUME_LOCK = threading.Lock()


def _read_unit(db: Session, crop: OcrCrop) -> bool:
    """Appel Mathpix UNITAIRE d'une découpe (la valeur attendue n'est JAMAIS transmise).
    Retourne True si la découpe est décidée (confirmée ou envoyée en manuel)."""
    from . import providers
    path = settings.data_dir / crop.image_path
    try:
        res = providers.mathpix_ocr(db, path.read_bytes(), f"ocr-{crop.id[:8]}")
    except Exception as e:                                      # budget, clé, réseau
        n = _MATHPIX_TRIES.get(crop.id, 0) + 1
        _MATHPIX_TRIES[crop.id] = n
        transient = not isinstance(e, (providers.BudgetExceeded, providers.ProviderConfigError))
        if transient and n < _MATHPIX_MAX_TRIES:
            logger.warning("mathpix (essai %d) : %s", n, type(e).__name__)
            return False
        prev = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
        _write_decision(db, crop, decision="manual", stage="mathpix",
                        reason=f"{prev.reason if prev else ''}>mathpix_unavailable:{type(e).__name__}",
                        cands=prev.candidates_json if prev else [])
        crop.status = "decided"
        db.commit()
        return True
    _apply_mathpix(db, crop, res.get("text", ""), res.get("latex", ""), float(res.get("confidence", 0.0)),
                   cost=0.004)
    db.commit()
    return True


def run_mathpix_step(db: Session) -> int:
    """Mode cascade : lit chez Mathpix les découpes que le local n'a pas tranchées, en appels
    unitaires ou, si `ocr_montage_enabled`, en planches. Retourne le nombre décidées."""
    if rs.ocr_setting(db, "ocr_montage_enabled"):
        from . import ocr_montage
        return ocr_montage.run_pending(db, _read_unit)
    crops = (db.query(OcrCrop).filter(OcrCrop.status == "mathpix_pending", OcrCrop.mode == "cascade")
             .order_by(OcrCrop.created_at).limit(50).all())
    return sum(1 for crop in crops if _read_unit(db, crop))


def resume_ready_batches(db: Session) -> int:
    """Reprend la pipeline des lots `ocr_local_pending` dont toutes les découpes sont décidées."""
    from . import pipeline
    n = 0
    for batch in db.query(ScanBatch).filter(ScanBatch.status == "ocr_local_pending").all():
        undecided = db.query(OcrCrop.id).filter(OcrCrop.batch_id == batch.id, OcrCrop.mode == "cascade",
                                                OcrCrop.status != "decided").first()
        if undecided is not None:
            continue
        with _RESUME_LOCK:
            if batch.id in _RESUMING:
                continue
            _RESUMING.add(batch.id)
        try:
            for zid in _unfinished_zones(db, batch.id):
                finish_zone(db, batch, db.get(ResponseZone, zid))
            db.commit()
            if _unfinished_zones(db, batch.id):
                continue
            pipeline.resume_after_local(db, batch)
            n += 1
        except Exception as e:                                  # le lot porte l'erreur, jamais le thread
            logger.exception("reprise du lot %s", batch.id)
            db.rollback()
            b = db.get(ScanBatch, batch.id)
            b.error = f"Reprise après lecture locale : {e}"
            db.commit()
        finally:
            with _RESUME_LOCK:
                _RESUMING.discard(batch.id)
    return n


def finish_shadow_zones(db: Session) -> int:
    """Mode ombre : enregistre la comparaison de zone quand toutes ses découpes sont décidées."""
    # Une seule requête : les zones ombre SANS décision de zone. Avant, chaque tour (20 s)
    # reparcourait toutes les zones de tous les lots déjà traités, une requête par zone :
    # intenable à 10 000 copies, surtout sur le NAS.
    from sqlalchemy import and_, exists
    zone_done = exists().where(and_(OcrDecision.zone_id == OcrCrop.zone_id,
                                    OcrDecision.batch_id == OcrCrop.batch_id,
                                    OcrDecision.crop_id.is_(None)))
    todo = (db.query(OcrCrop.batch_id, OcrCrop.zone_id)
            .filter(OcrCrop.mode == "shadow", ~zone_done).distinct().limit(500).all())
    n = 0
    for bid, zid in todo:
        if finish_zone(db, db.get(ScanBatch, bid), db.get(ResponseZone, zid)):
            n += 1
    return n


def tick(db: Session) -> dict:
    """Un tour de l'orchestrateur (thread de fond) : avance les découpes, Mathpix, zones, reprise."""
    out = {"advanced": 0, "mathpix": 0, "resumed": 0, "shadow_zones": 0}
    open_crops = (db.query(OcrCrop).filter(OcrCrop.status.in_(("pending", "local_done"))).limit(500).all())
    for crop in open_crops:
        try:
            if advance_crop(db, crop) == "decided":
                out["advanced"] += 1
            db.commit()
        except Exception:
            logger.exception("avancement de la découpe %s", crop.id)
            db.rollback()
    for crop in db.query(OcrCrop).filter(OcrCrop.status == "mathpix_pending", OcrCrop.mode == "shadow").all():
        resolve_shadow_mathpix(db, crop)
    # Seulement les échantillons complets pas encore conclus (pas de clé `action`) : avec
    # `desc().limit(500)` sur tous, les plus anciens non conclus n'étaient plus jamais repris
    # une fois 500 échantillons terminés plus récents.
    from sqlalchemy import String, cast, or_
    for crop in (db.query(OcrCrop).join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
                 .filter(OcrCrop.full_sample.is_(True), OcrCrop.status == "decided",
                         or_(OcrDecision.full_decision.is_(None),
                             ~cast(OcrDecision.full_decision, String).like('%"action"%')))
                 .order_by(OcrCrop.created_at).limit(500).all()):
        finish_full_sample(db, crop)
    db.commit()
    out["mathpix"] = run_mathpix_step(db)
    out["shadow_zones"] = finish_shadow_zones(db)
    db.commit()
    out["resumed"] = resume_ready_batches(db)
    return out


def advance_after_results(db: Session, crop_ids: list[str]) -> None:
    """Appelé par l'API du worker après un lot de résultats : transitions en base seulement."""
    for cid in crop_ids:
        crop = db.get(OcrCrop, cid)
        if crop is not None:
            advance_crop(db, crop)
    db.commit()
    _wake.set()


_wake = threading.Event()
_started = False
_TICK_S = 20


def _loop() -> None:
    while True:
        _wake.wait(_TICK_S)
        _wake.clear()
        db = SessionLocal()
        try:
            tick(db)
        except Exception:
            logger.exception("ocr_cascade.tick")
            db.rollback()
        finally:
            db.close()


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="kironina-ocr-cascade").start()

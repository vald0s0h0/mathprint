"""Journal de la revue humaine : chaque correction du professeur devient un dossier.

Quand le professeur change ce que le système avait lu (OCR, cases cochées, liaisons,
tracé) ou la note qu'il avait mise, on garde de quoi comprendre l'erreur et la
corriger en lot plus tard :

    data/review_log/<AAAA-MM-JJ>/<HHMMSS>-<id>/
        event.json          avant / après, contexte des moteurs et de la vision
        scan.png            la réponse de l'élève, découpée sur le scan recalé
        page.jpg            la page scannée entière (réduite)
        squelette-avant.svg ce qui partait en correction, remis au propre
        squelette-apres.svg la même page après la modification

La ligne `review_events` porte l'état (open -> treated). Le traitement en lot passe
par `backend/scripts/review_log.py` (lister, exporter, marquer traité).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (Copy, CopyItem, GradingDecision, OcrAttempt, OcrCrop, OcrDecision, OcrEngineRead,
                      ResponseZone, ReviewEvent, ScanBatch, StudentResponse)
from . import skeleton

ROOT = "review_log"


def category_for(response_type: str, action: str) -> str:
    if action == "points":
        return "grading"
    if response_type.startswith("qcm") or response_type == "checkbox_grid":
        return "cv_boxes"
    if response_type == "matching":
        return "cv_matching"
    if response_type == "manual_drawing":
        return "cv_drawing"
    return "ocr"


def snapshot(db: Session, resp: StudentResponse, item: CopyItem, zone: ResponseZone, copy: Copy) -> dict:
    """État d'une réponse à un instant : ce qui part en correction, la note, la page."""
    decision = (db.query(GradingDecision).filter_by(response_id=resp.id)
                .order_by(GradingDecision.created_at.desc()).first())
    return {
        "synthesis": skeleton.zone_synthesis(db, resp, item, zone),
        "page": skeleton.page_view(db, copy, zone.page_id),
        "decision": ({"score": decision.score, "max_score": decision.max_score,
                      "source": decision.source, "reason_code": decision.reason_code,
                      "evidence": decision.evidence_json} if decision else None),
    }


def _context(db: Session, item: CopyItem, zone: ResponseZone) -> dict:
    """Ce que chaque moteur et la vision avaient vu : base de l'analyse en lot."""
    attempts = [{"at": a.created_at.isoformat() if a.created_at else None, "provider": a.provider,
                 "confidence": a.confidence, "text": a.text, "latex": a.latex,
                 "raw": a.raw_json}
                for a in db.query(OcrAttempt).filter_by(zone_id=zone.id).order_by(OcrAttempt.created_at)]
    crops = []
    for c in db.query(OcrCrop).filter_by(zone_id=zone.id).order_by(OcrCrop.cell_index, OcrCrop.line_index):
        crops.append({
            "crop_id": c.id, "cell_index": c.cell_index, "line_index": c.line_index,
            "zone_class": c.zone_class, "image": c.image_path, "ink_ratio": c.ink_ratio,
            "reads": [{"engine": r.engine, "kind": r.kind, "raw": r.raw_text, "surface": r.surface,
                       "illegible": r.illegible, "confidence": r.confidence_raw}
                      for r in db.query(OcrEngineRead).filter_by(crop_id=c.id)],
            "decisions": [{"decision": d.decision, "reason": d.reason, "accepted": d.accepted_surface,
                           "mode": d.mode, "truth": d.truth_surface}
                          for d in db.query(OcrDecision).filter_by(crop_id=c.id)],
        })
    return {"statement": item.statement, "expected": item.expected_json,
            "grading": {k: v for k, v in (item.grading_json or {}).items() if k != "feedback"},
            "zone_meta": zone.meta_json, "attempts": attempts, "crops": crops}


def _svg(page: dict, highlight: str | None) -> str:
    """Squelette + synthèse d'une page, en SVG autonome (capture du dossier)."""
    w, h = page["width"], page["height"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
           f'width="{w * 1.5:.0f}" height="{h * 1.5:.0f}" font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{w:.0f}" height="{h:.0f}" fill="#fff" stroke="#9ec5fe" stroke-width="1"/>']
    for p in page["paths"]:
        out.append(f'<path d="{p["d"]}" fill="none" stroke="#9ec5fe" stroke-width="{0.9 if p["kind"] == "card" else 0.7}"/>')
    for z in page["zones"]:
        if z["response_id"] == highlight:
            r = z["rect"]
            out.append(f'<rect x="{r["x"] - 3:.1f}" y="{r["y"] - 3:.1f}" width="{r["w"] + 6:.1f}" '
                       f'height="{r["h"] + 6:.1f}" fill="none" stroke="#f08c00" stroke-width="1.5" stroke-dasharray="4 3"/>')
        for b in z.get("boxes") or []:
            if b["on"]:
                out.append(f'<rect x="{b["x"]:.1f}" y="{b["y"]:.1f}" width="{b["w"]:.1f}" height="{b["h"]:.1f}" fill="#1c3d7a"/>')
        for x1, y1, x2, y2 in z.get("links") or []:
            out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#1c3d7a" stroke-width="1.4"/>')
        for c in z.get("cells") or []:
            if c["text"]:
                out.append(f'<text x="{c["x"] + c["w"] / 2:.1f}" y="{c["y"] + c["h"] / 2 + 3.5:.1f}" '
                           f'font-size="10" text-anchor="middle" fill="#1c3d7a">{escape(c["text"])}</text>')
        for el in z.get("drawing") or []:
            pts = " ".join(f"{x},{y}" for x, y in el["points"])
            dash = "" if el["state"] == "present" else ' stroke-dasharray="3 3" opacity="0.5"'
            out.append(f'<polyline points="{pts}" fill="none" stroke="#1c3d7a" stroke-width="1"{dash}/>')
        if z.get("text") and not z.get("cells"):
            r = z["rect"]
            for i, line in enumerate(str(z["text"]).split("\n")[:12]):
                out.append(f'<text x="{r["x"] + 4:.1f}" y="{r["y"] + 13 + i * 13:.1f}" font-size="10" '
                           f'fill="#1c3d7a">{escape(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _scan_files(folder: Path, copy: Copy, zone: ResponseZone) -> list[str]:
    import cv2
    from . import worker_cv
    page = (settings.data_dir / "assessments" / copy.assessment_id / "scans" / "derived"
            / f"page-{zone.page_id}.png")
    img = cv2.imread(str(page)) if page.exists() else None
    if img is None:
        return []
    files = []
    crop = worker_cv.crop_zone(img, zone.x_pt, zone.y_pt, zone.w_pt, zone.h_pt, padding_pt=14)
    if crop.size:
        cv2.imwrite(str(folder / "scan.png"), crop)
        files.append("scan.png")
    small = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(folder / "page.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    files.append("page.jpg")
    return files


def record(db: Session, *, stage: str, action: str, resp: StudentResponse, item: CopyItem,
           zone: ResponseZone, copy: Copy, batch: ScanBatch | None, before: dict, after: dict,
           user_id: str | None = None) -> ReviewEvent | None:
    """Écrit l'événement et son dossier. Rien si la modification ne change rien."""
    score = lambda s: ((s.get("decision") or {}).get("score"), (s.get("decision") or {}).get("max_score"))  # noqa: E731
    seen = lambda s: {k: v for k, v in s["synthesis"].items()                                       # noqa: E731
                      if k not in ("source", "teacher", "points", "bareme")}
    if seen(before) == seen(after) and score(before) == score(after):
        return None
    ev = ReviewEvent(stage=stage, action=action, user_id=user_id,
                     batch_id=batch.id if batch else None, assessment_id=copy.assessment_id,
                     copy_id=copy.id, response_id=resp.id, zone_id=zone.id, sequence=item.sequence,
                     response_type=item.response_type,
                     category=category_for(item.response_type, action),
                     before_json={"synthesis": before["synthesis"], "decision": before.get("decision")},
                     after_json={"synthesis": after["synthesis"], "decision": after.get("decision")},
                     context_json=_context(db, item, zone))
    db.add(ev)
    db.flush()
    stamp = datetime.now(timezone.utc)
    rel = Path(ROOT) / stamp.strftime("%Y-%m-%d") / f"{stamp.strftime('%H%M%S')}-{ev.id[:8]}"
    folder = settings.data_dir / rel
    try:
        folder.mkdir(parents=True, exist_ok=True)
        files = _scan_files(folder, copy, zone)
        (folder / "squelette-avant.svg").write_text(_svg(before["page"], resp.id))
        (folder / "squelette-apres.svg").write_text(_svg(after["page"], resp.id))
        files += ["squelette-avant.svg", "squelette-apres.svg"]
        (folder / "event.json").write_text(json.dumps(event_dict(ev, files), ensure_ascii=False,
                                                      indent=2, default=str))
        ev.folder = str(rel)
    except OSError:
        ev.folder = None          # l'événement reste en base même sans dossier
    return ev


def event_dict(ev: ReviewEvent, files: list[str] | None = None) -> dict:
    return {"id": ev.id, "created_at": ev.created_at, "stage": ev.stage, "action": ev.action,
            "category": ev.category, "status": ev.status, "batch_id": ev.batch_id,
            "assessment_id": ev.assessment_id, "copy_id": ev.copy_id, "response_id": ev.response_id,
            "zone_id": ev.zone_id, "sequence": ev.sequence, "response_type": ev.response_type,
            "before": ev.before_json, "after": ev.after_json, "context": ev.context_json,
            "files": files, "folder": ev.folder, "treated_at": ev.treated_at, "treated_note": ev.treated_note}

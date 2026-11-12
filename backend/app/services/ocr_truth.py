"""Vérité terrain de la lecture OCR multicouche et suggestions pour OCRiser.

Chaque fois que le professeur écrit une lecture (`teacher_ocr`, depuis OCRiser ou Affiner),
`record_truth` remplit `truth_surface` / `truth_source` sur la décision de la zone et de ses
cases ou lignes, puis calcule `auto_error` : vrai si la décision était AUTOMATIQUE
(`local_consensus`, `mathpix_confirmed`) et que la surface du professeur diffère de la surface
acceptée. Seules les cases que le professeur a réellement vues reçoivent une vérité : une case
lue automatiquement et jamais relue n'est pas « juste », elle est simplement non contrôlée.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import OcrCrop, OcrDecision
from .ocr_surface import to_surface

AUTO_DECISIONS = ("local_consensus", "mathpix_confirmed")


def _latest_zone_row(db: Session, zone_id: str) -> OcrDecision | None:
    return (db.query(OcrDecision).filter(OcrDecision.zone_id == zone_id, OcrDecision.crop_id.is_(None))
            .order_by(OcrDecision.created_at.desc()).first())


def record_truth(db: Session, zone_id: str, texts: dict, now: datetime | None = None) -> int:
    """texts : {cell_index: texte} pour un tableau, {None: texte} pour une zone de texte (une
    ligne ou une rédaction : le texte est alors découpé par lignes). Retourne le nombre de
    décisions mises à jour. Sans décision multicouche pour la zone (mode `mathpix`) : rien."""
    zrow = _latest_zone_row(db, zone_id)
    if zrow is None:
        return 0
    now = now or datetime.now(timezone.utc)
    source = "audit" if zrow.audit else "teacher_ocr"
    crops = (db.query(OcrCrop).filter_by(zone_id=zone_id, batch_id=zrow.batch_id)
             .order_by(OcrCrop.cell_index, OcrCrop.line_index).all())
    updated = 0
    pairs: list[tuple[OcrCrop, str]] = []
    line_crops = [c for c in crops if c.line_index is not None]
    for c in crops:
        if c.cell_index is not None and c.cell_index in texts:
            pairs.append((c, texts[c.cell_index]))
        elif c.cell_index is None and c.line_index is None and None in texts:
            pairs.append((c, texts[None]))
    if line_crops and None in texts:
        lines = str(texts[None]).split("\n")
        if len(lines) == len(line_crops):                    # sinon l'alignement est ambigu : ligne par ligne ignorée
            pairs += list(zip(line_crops, lines))
    for crop, text in pairs:
        d = db.query(OcrDecision).filter_by(crop_id=crop.id).first()
        if d is None or d.decision == "mathpix_pending":
            continue
        d.truth_surface = to_surface(text, crop.zone_class)
        d.truth_source, d.truth_at = source, now
        d.auto_error = bool(d.decision in AUTO_DECISIONS and d.truth_surface != (d.accepted_surface or ""))
        updated += 1
    # zone entière : seulement quand TOUTES ses découpes ont une vérité
    decisions = [db.query(OcrDecision).filter_by(crop_id=c.id).first() for c in crops]
    if crops and all(d is not None and d.truth_surface is not None for d in decisions):
        sep = "\n" if line_crops else "|"
        zrow.truth_surface = sep.join(d.truth_surface for d in decisions)
        zrow.truth_source, zrow.truth_at = source, now
        zrow.auto_error = any(bool(d.auto_error) for d in decisions)
        updated += 1
    return updated


def suggestions(db: Session, zone_id: str) -> dict:
    """Pour OCRiser : drapeau « Contrôle » et candidats des moteurs (le plus soutenu d'abord),
    par case (`cells`) ou pour la zone (`zone`, une seule découpe)."""
    zrow = _latest_zone_row(db, zone_id)
    if zrow is None:
        return {"audit": False, "zone": [], "cells": {}}
    out = {"audit": bool(zrow.audit), "zone": [], "cells": {}}
    for c in db.query(OcrCrop).filter_by(zone_id=zone_id, batch_id=zrow.batch_id).all():
        d = db.query(OcrDecision).filter_by(crop_id=c.id).first()
        if d is None:
            continue
        cands = [{"surface": x["surface"], "engines": x["engines"], "voices": x["voices"]}
                 for x in (d.candidates_json or []) if x.get("surface")]
        cands.sort(key=lambda x: (-x["voices"], x["surface"]))
        if c.cell_index is not None:
            out["cells"][c.cell_index] = cands
        elif c.line_index is None:
            out["zone"] = cands
    return out


def backfill(db: Session, zone_id: str) -> int:
    """Rattrape la vérité d'une zone dont le professeur a répondu AVANT que ses décisions
    multicouche n'existent (mode ombre : OCRiser s'ouvre dès la lecture Mathpix, la lecture locale
    arrive après). Seules les cases marquées `touched` par le professeur comptent."""
    from ..models import OcrAttempt
    att = (db.query(OcrAttempt).filter_by(zone_id=zone_id, provider="teacher_ocr")
           .order_by(OcrAttempt.created_at.desc()).first())
    if att is None:
        return 0
    raw = att.raw_json or {}
    touched = raw.get("touched") or []
    texts: dict = {}
    cells = raw.get("cells")
    for t in touched:
        if t == "zone":
            texts[None] = att.text or ""
        elif isinstance(t, int) and isinstance(cells, list) and 0 <= t < len(cells):
            texts[t] = str(cells[t] or "")
    return record_truth(db, zone_id, texts) if texts else 0

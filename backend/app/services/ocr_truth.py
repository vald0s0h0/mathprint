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


# ------------------------------------------------------------ options de l'assistant OCRiser

# Ordre d'affichage des moteurs, et moteur dont la forme brute sert au rendu LaTeX
# d'une lecture partagée (les VLM et Mathpix écrivent du LaTeX, TrOCR du texte).
_DISPLAY_PRIORITY = ("mathpix", "mock", "unimumer", "lighton", "apple_vision", "trocr")


def _prio(engine: str) -> int:
    return _DISPLAY_PRIORITY.index(engine) if engine in _DISPLAY_PRIORITY else len(_DISPLAY_PRIORITY)


def group_options(entries: list[tuple[str, str]], zone_class: str) -> list[dict]:
    """Regroupe des lectures (moteur, texte brut) par surface canonique. Chaque option porte
    TOUS les moteurs qui l'ont lue : la choisir n'est pas un vote pour un moteur, la vérité
    est une surface et elle crédite chacun d'eux. Les plus partagées d'abord."""
    by: dict[str, dict] = {}
    for engine, raw in entries:
        raw = (raw or "").strip()
        if not raw:
            continue
        surface = to_surface(raw, zone_class) if zone_class != "multiline" else \
            "\n".join(to_surface(line, "multiline_line") for line in raw.split("\n"))
        if not surface.strip():
            continue
        g = by.setdefault(surface, {"surface": surface, "raws": {}})
        g["raws"].setdefault(engine, raw)
    out = []
    for g in by.values():
        engines = sorted(g["raws"], key=lambda e: (_prio(e), e))
        out.append({"surface": g["surface"], "text": g["raws"][engines[0]], "engines": engines})
    out.sort(key=lambda o: (-len(o["engines"]), min(_prio(e) for e in o["engines"]), o["surface"]))
    return out


def _crop_reads(db: Session, crop: OcrCrop) -> dict[str, str]:
    """Dernière lecture exploitable de chaque moteur (et de Mathpix) pour une découpe."""
    from ..models import OcrEngineRead
    out: dict[str, str] = {}
    for r in (db.query(OcrEngineRead)
              .filter(OcrEngineRead.crop_id == crop.id, OcrEngineRead.kind.in_(("read", "mathpix")))
              .order_by(OcrEngineRead.created_at).all()):
        if r.error or r.illegible or not (r.raw_text or "").strip():
            out.pop(r.engine, None)
            continue
        out[r.engine] = r.raw_text
    return out


def review_options(db: Session, zone_id: str, expected_type: str | None = None,
                   multiline: bool = False) -> dict:
    """Lectures proposées au professeur dans OCRiser, quel que soit le mode : moteurs locaux
    (cascade, ombre) et Mathpix (tous modes). {"zone": [...], "cells": {index: [...]}}.
    Jamais la valeur attendue : seulement ce que les moteurs ont lu librement."""
    from ..models import OcrAttempt
    from .ocr_cascade import zone_class_for
    zone_entries: list[tuple[str, str]] = []
    cell_entries: dict[int, list[tuple[str, str]]] = {}
    cell_class: dict[int, str] = {}
    zone_class = "multiline" if multiline else zone_class_for(expected_type)

    zrow = _latest_zone_row(db, zone_id)
    if zrow is not None:
        crops = (db.query(OcrCrop).filter_by(zone_id=zone_id, batch_id=zrow.batch_id)
                 .order_by(OcrCrop.cell_index, OcrCrop.line_index).all())
        lines = [c for c in crops if c.line_index is not None]
        for c in crops:
            if c.line_index is not None:
                continue
            reads = _crop_reads(db, c)
            if c.cell_index is not None:
                cell_class[c.cell_index] = c.zone_class
                cell_entries.setdefault(c.cell_index, []).extend(reads.items())
            else:
                zone_class = c.zone_class
                zone_entries.extend(reads.items())
        if lines:                       # rédaction : un moteur propose la zone s'il a lu chaque ligne
            per_line = [_crop_reads(db, c) for c in lines]
            for engine in set().union(*per_line):
                if all(engine in r for r in per_line):
                    zone_entries.append((engine, "\n".join(r[engine] for r in per_line)))

    att = (db.query(OcrAttempt).filter(OcrAttempt.zone_id == zone_id,
                                       OcrAttempt.provider.in_(("mathpix", "mock")))
           .order_by(OcrAttempt.created_at.desc()).first())
    if att is not None:
        raw = att.raw_json or {}
        cells = raw.get("cells")
        if isinstance(cells, list) and cells:
            lat = raw.get("cell_latex") or []
            for i, text in enumerate(cells):
                value = (lat[i] if i < len(lat) and lat[i] else text) or ""
                if str(value).strip():
                    cell_entries.setdefault(i, []).append((att.provider, str(value)))
        else:
            value = att.latex or att.text or ""
            if value.strip():
                zone_entries.append((att.provider, value))

    def dedupe(entries):            # un moteur, une lecture (Mathpix déjà lu par découpe en cascade)
        seen, out = set(), []
        for e, v in entries:
            if e not in seen:
                seen.add(e)
                out.append((e, v))
        return out
    return {"zone": group_options(dedupe(zone_entries), zone_class),
            "cells": {i: group_options(dedupe(v), cell_class.get(i, "math_line"))
                      for i, v in cell_entries.items()}}

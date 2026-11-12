"""Planches Mathpix : plusieurs découpes sur UNE image, un seul appel `v3/text`.

Pour réduire le coût quand les volumes augmentent. Chaque ligne renvoyée par Mathpix
(`include_line_data`) revient à sa découpe PAR SA BOÎTE (`cnt`), qui doit être
entièrement contenue dans la bande verticale de la découpe :
- une ligne à cheval sur deux bandes invalide les deux découpes ;
- une découpe sans ligne, ou avec plusieurs lignes, est relue SEULE par l'appel unitaire.
Mathpix ne décide jamais seul : la décision passe ensuite par `ocr_fusion.decide_mathpix`.

Désactivé par défaut (`ocr_montage_enabled=false`) tant que le banc
`scripts/mathpix_montage_bench.py` n'a pas montré 0 écart avec la lecture unitaire.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from ..models import OcrCrop, OcrDecision, OcrMontage
from . import runtime_settings as rs

logger = logging.getLogger(__name__)

MIN_GAP_PX = 80                # minimum exigé
DEFAULT_GAP_PX = 250           # mesuré : à 80 px Mathpix fusionne la colonne en un seul tableau (1 ligne)
MAX_WIDTH_PX = 1600
MARGIN_PX = 20
COST_PER_CALL_EUR = 0.004


@dataclass
class Montage:
    png: bytes
    boxes: list[tuple[int, int, int, int]]        # (x0, y0, x1, y1) de chaque découpe, dans la planche
    scales: list[float] = field(default_factory=list)
    width: int = 0
    height: int = 0


def build_montage(images: list[bytes], *, gap: int = DEFAULT_GAP_PX, max_width: int = MAX_WIDTH_PX,
                  stagger: int = 0) -> Montage:
    """Colonne verticale sur fond blanc : `gap` px (>= 80, 250 par défaut) entre deux découpes, largeur
    totale <= `max_width` (une découpe plus large est réduite, jamais rognée)."""
    import cv2
    decoded = []
    for data in images:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("image de découpe illisible")
        decoded.append(img)
    if gap < MIN_GAP_PX:
        raise ValueError(f"écart minimal entre découpes : {MIN_GAP_PX} px")
    inner = max_width - 2 * MARGIN_PX
    scaled, scales = [], []
    for img in decoded:
        k = min(1.0, inner / img.shape[1])
        scaled.append(img if k == 1.0 else cv2.resize(img, (max(1, int(img.shape[1] * k)),
                                                            max(1, int(img.shape[0] * k))), interpolation=cv2.INTER_AREA))
        scales.append(k)
    # `stagger` : décalage horizontal alterné d'une découpe à l'autre, pour que Mathpix ne les prenne pas
    # pour les lignes d'un même tableau (il fusionne sinon une colonne de courts nombres en `array`)
    width = min(max_width, max(i.shape[1] for i in scaled) + 2 * MARGIN_PX + stagger)
    height = 2 * MARGIN_PX + sum(i.shape[0] for i in scaled) + gap * (len(scaled) - 1)
    canvas = np.full((height, width, 3), 255, np.uint8)
    y, boxes = MARGIN_PX, []
    for k, img in enumerate(scaled):
        h, w = img.shape[:2]
        x = MARGIN_PX + (stagger if k % 2 else 0)
        canvas[y:y + h, x:x + w] = img
        boxes.append((x, y, x + w, y + h))
        y += h + gap
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("encodage PNG de la planche")
    return Montage(png=buf.tobytes(), boxes=boxes, scales=scales, width=width, height=height)


@dataclass
class Assignment:
    status: str                       # ok | no_line | ambiguous | straddle
    lines: list = field(default_factory=list)


def _bbox(cnt) -> tuple[float, float, float, float] | None:
    try:
        xs = [float(p[0]) for p in cnt]
        ys = [float(p[1]) for p in cnt]
    except (TypeError, ValueError, IndexError):
        return None
    return (min(xs), min(ys), max(xs), max(ys)) if xs and ys else None


def assign_lines(boxes: list[tuple[int, int, int, int]], lines: list[dict]) -> list[Assignment]:
    """Réattribue chaque ligne à sa découpe par sa boîte. Une ligne sans contour exploitable,
    ou qui touche plusieurs bandes, ou qui déborde de sa bande, invalide les découpes touchées."""
    status = [Assignment("no_line") for _ in boxes]
    owned: list[list[dict]] = [[] for _ in boxes]
    bad = [False] * len(boxes)
    for ln in lines:
        bb = _bbox(ln.get("cnt") or [])
        if bb is None:
            for i in range(len(boxes)):          # ligne inlocalisable : on ne sait pas à qui elle est
                bad[i] = True
            continue
        _lx0, ly0, _lx1, ly1 = bb
        touched = [i for i, b in enumerate(boxes) if ly0 < b[3] and ly1 > b[1]]
        if not touched:
            continue                              # dans l'écart entre deux découpes : sans propriétaire
        inside = len(touched) == 1 and ly0 >= boxes[touched[0]][1] and ly1 <= boxes[touched[0]][3]
        if not inside:
            for i in touched:
                bad[i] = True
            continue
        owned[touched[0]].append(ln)
    for i in range(len(boxes)):
        if bad[i]:
            status[i] = Assignment("straddle")
        elif len(owned[i]) == 1:
            status[i] = Assignment("ok", owned[i])
        elif len(owned[i]) == 0:
            status[i] = Assignment("no_line")
        else:
            status[i] = Assignment("ambiguous", owned[i])
    return status


def should_send(*, pending: int, oldest_wait: timedelta | None, batch_only_mathpix: bool,
                max_crops: int, flush_minutes: int) -> bool:
    """Une planche part dès qu'il y a `max_crops` découpes en attente, ou que la plus ancienne
    attend depuis `flush_minutes`, ou qu'un lot n'attend plus que Mathpix."""
    if pending <= 0:
        return False
    return (pending >= max_crops or batch_only_mathpix
            or (oldest_wait is not None and oldest_wait >= timedelta(minutes=flush_minutes)))


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def run_pending(db: Session, read_unit, now: datetime | None = None) -> int:
    """Envoie les planches dues. `read_unit(db, crop)` relit UNE découpe seule (appel unitaire
    existant) ; il sert pour toute découpe que la planche n'a pas permis d'attribuer.
    Retourne le nombre de découpes décidées."""
    from . import ocr_cascade, providers
    now = now or datetime.now(timezone.utc)
    max_crops = int(rs.ocr_setting(db, "ocr_montage_max_crops"))
    flush = int(rs.ocr_setting(db, "ocr_montage_flush_minutes"))
    rows = (db.query(OcrCrop, OcrDecision.created_at)
            .join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
            .filter(OcrCrop.status == "mathpix_pending", OcrCrop.mode == "cascade",
                    OcrDecision.decision == "mathpix_pending")
            .order_by(OcrDecision.created_at, OcrCrop.id).all())
    if not rows:
        return 0
    crops = [c for c, _t in rows]
    open_batches = {b for (b,) in db.query(OcrCrop.batch_id).filter(
        OcrCrop.mode == "cascade", OcrCrop.status.in_(("pending", "local_done"))).distinct()}
    only_mathpix = any(c.batch_id not in open_batches for c in crops)
    oldest = now - _aware(rows[0][1])
    if not should_send(pending=len(crops), oldest_wait=oldest, batch_only_mathpix=only_mathpix,
                       max_crops=max_crops, flush_minutes=flush):
        return 0
    send_all = only_mathpix or oldest >= timedelta(minutes=flush)
    chunks = [crops[i:i + max_crops] for i in range(0, len(crops), max_crops)]
    if not send_all:
        chunks = [c for c in chunks if len(c) == max_crops]          # le reliquat attend
    done = 0
    for chunk in chunks:
        done += _send_chunk(db, chunk, read_unit, providers, ocr_cascade)
    return done


def _send_chunk(db, chunk: list[OcrCrop], read_unit, providers, ocr_cascade) -> int:
    if len(chunk) == 1:                                   # une planche d'une découpe ne coûte pas moins
        return int(bool(read_unit(db, chunk[0])))
    images = [(settings.data_dir / c.image_path).read_bytes() for c in chunk]
    try:
        montage = build_montage(images)
        res = providers.mathpix_montage_ocr(db, montage.png, f"montage-{chunk[0].id[:8]}")
    except Exception as e:                                # budget, clé, réseau : tout part en lecture seule
        logger.warning("planche Mathpix : %s", type(e).__name__)
        return sum(1 for c in chunk if read_unit(db, c))
    assigned = assign_lines(montage.boxes, res["lines"])
    mid = _save_montage(db, chunk, montage, res)
    share = float(res.get("cost", COST_PER_CALL_EUR)) / len(chunk)
    done = 0
    for crop, a in zip(chunk, assigned):
        if a.status == "ok":
            ln = a.lines[0]
            ocr_cascade._apply_mathpix(db, crop, ln["text"], ln.get("latex") or ln["text"],
                                       float(ln["confidence"]), cost=share, montage_id=mid)
            db.commit()
            done += 1
        elif read_unit(db, crop):
            done += 1
    return done


def _save_montage(db: Session, chunk: list[OcrCrop], montage: Montage, res: dict) -> str:
    row = OcrMontage(crops_json=[{"crop_id": c.id, "box": list(b)} for c, b in zip(chunk, montage.boxes)],
                     response_json=res.get("raw") or {}, cost_eur=float(res.get("cost", 0.0)))
    db.add(row)
    db.flush()
    d = settings.data_dir / "ocr_montages"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{row.id}.png"
    path.write_bytes(montage.png)
    row.image_path = str(path.relative_to(settings.data_dir))
    db.commit()
    return row.id

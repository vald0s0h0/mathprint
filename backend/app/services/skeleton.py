"""Squelette d'une page de copie et synthèse de ce qui a été lu, pour la revue manuelle.

Le squelette est la page imprimée réduite à ses contours : cadres des cartes, cases de
réponse, tableaux, cases à cocher, points à relier — sans texte ni figure. Il est tiré
des tracés vectoriels du PDF réellement imprimé (`generated/subject_batch.pdf`) : les
cartes ont un contour gris rempli de blanc, les champs de l'élève sont en saumon (la
couleur de masquage). Une page absente du PDF (copie régénérée à part) retombe sur
les rectangles des zones de réponse.

Par-dessus, la synthèse : ce qui part en correction, remis au propre — valeurs lues ou
choisies dans chaque case, coches vues, liaisons vues, éléments de tracé retrouvés.

Toutes les coordonnées sont en points PDF depuis le coin HAUT gauche de la page.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Copy, CopyItem, DocumentPage, GradingDecision, OcrAttempt, ResponseZone, StudentResponse
from . import construction, scoring
from .pdfgen import PAGE_H, PAGE_W

_LOCK = threading.Lock()
_INDEX: dict[str, tuple[float, dict[str, int]]] = {}       # assessment -> (mtime, page_id -> n° de page PDF)
_SKEL: dict[tuple[str, int, float], list[dict]] = {}       # (assessment, n°, mtime) -> chemins


def _generated(assessment_id: str) -> Path:
    return settings.data_dir / "assessments" / assessment_id / "generated"


def _page_index(assessment_id: str) -> dict[str, int]:
    path = _generated(assessment_id) / "copy_manifest.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _LOCK:
        hit = _INDEX.get(assessment_id)
        if hit and hit[0] == mtime:
            return hit[1]
    manifest = json.loads(path.read_text())
    index, n = {}, 0
    for copy in manifest.get("copies") or []:
        for page in copy.get("pages") or []:
            index[str(page["page_id"])] = n
            n += 1
    with _LOCK:
        _INDEX[assessment_id] = (mtime, index)
    return index


def _hex_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))      # type: ignore[return-value]


def _close(a, b, tol: float = 0.06) -> bool:
    return a is not None and len(a) >= 3 and all(abs(x - y) <= tol for x, y in zip(a[:3], b))


def _path_d(items) -> str:
    """Éléments d'un tracé PyMuPDF -> chemin SVG (coordonnées déjà en haut-gauche)."""
    parts, cur = [], None
    f = lambda p: f"{p.x:.1f} {p.y:.1f}"            # noqa: E731
    for it in items:
        op = it[0]
        if op == "l":
            if cur is None or (abs(cur.x - it[1].x) > .05 or abs(cur.y - it[1].y) > .05):
                parts.append(f"M{f(it[1])}")
            parts.append(f"L{f(it[2])}")
            cur = it[2]
        elif op == "c":
            if cur is None or (abs(cur.x - it[1].x) > .05 or abs(cur.y - it[1].y) > .05):
                parts.append(f"M{f(it[1])}")
            parts.append(f"C{f(it[2])} {f(it[3])} {f(it[4])}")
            cur = it[4]
        elif op == "re":
            r = it[1]
            parts.append(f"M{r.x0:.1f} {r.y0:.1f}H{r.x1:.1f}V{r.y1:.1f}H{r.x0:.1f}Z")
            cur = None
        elif op == "qu":
            q = it[1]
            parts.append(f"M{f(q.ul)}L{f(q.ur)}L{f(q.lr)}L{f(q.ll)}Z")
            cur = None
    return "".join(parts)


def _pdf_skeleton(assessment_id: str, page_no: int) -> list[dict] | None:
    pdf = _generated(assessment_id) / "subject_batch.pdf"
    try:
        mtime = pdf.stat().st_mtime
    except OSError:
        return None
    key = (assessment_id, page_no, mtime)
    with _LOCK:
        if key in _SKEL:
            return _SKEL[key]
    import fitz
    salmon = _hex_rgb(settings.dropout_color)
    out: list[dict] = []
    with fitz.open(pdf) as doc:
        if page_no >= doc.page_count:
            return None
        for d in doc[page_no].get_drawings():
            stroke, fill = d.get("color"), d.get("fill")
            if stroke is None:
                continue                                   # aplats : ombres, figures, codes
            if _close(stroke, salmon, 0.08):
                kind = "field"                             # cases, tableaux, coches, points
            elif fill is not None and _close(fill, (1, 1, 1), 0.02) and not _close(stroke, (0, 0, 0), 0.25):
                kind = "card"                              # cadre de carte (gris, fond blanc)
            else:
                continue                                   # figures, texte dessiné, en-tête
            d_attr = _path_d(d.get("items") or [])
            if d_attr:
                out.append({"kind": kind, "d": d_attr})
    with _LOCK:
        _SKEL[key] = out
    return out


def _top(y_pt: float, h_pt: float = 0.0) -> float:
    return PAGE_H - (float(y_pt) + float(h_pt))


def _rect(g: dict) -> dict:
    return {"x": float(g["x_pt"]), "y": _top(g["y_pt"], g["h_pt"]),
            "w": float(g["w_pt"]), "h": float(g["h_pt"])}


def _zone_rect(z: ResponseZone) -> dict:
    return {"x": float(z.x_pt), "y": _top(z.y_pt, z.h_pt), "w": float(z.w_pt), "h": float(z.h_pt)}


def _fallback_skeleton(zones: list[ResponseZone]) -> list[dict]:
    out = []
    for z in zones:
        r = _zone_rect(z)
        out.append({"kind": "field", "d": f"M{r['x']:.1f} {r['y']:.1f}h{r['w']:.1f}v{r['h']:.1f}h{-r['w']:.1f}Z"})
    return out


# ------------------------------------------------------------------ synthèse

def _reading(db: Session, zone_id: str) -> OcrAttempt | None:
    from .pipeline import effective_reading
    return effective_reading(db, zone_id)


def _construction_marks(z: ResponseZone, reading: OcrAttempt | None) -> list[dict]:
    """Éléments de tracé attendus, avec l'état retrouvé par la vision (présent, absent…)."""
    meta = z.meta_json or {}
    info = meta.get("construction") or {}
    spec = info.get("spec")
    raw = ((reading.raw_json or {}).get("construction") if reading else None) or {}
    if not spec:
        return []
    spec = construction.normalize(spec)
    states = {e.get("name"): e.get("state") for e in raw.get("elements") or []}
    ox, oy = info["origin_pt"]
    u = info["unit_pt"]
    move = construction.Placement.from_dict(raw.get("placement"))
    out = []
    for prim in construction.primitives(spec.get("expected"), spec, "expected"):
        prim = move.prim(prim)
        if prim.kind == "point":
            continue
        if prim.infinite:
            clipped = construction._clip(prim.a, prim.b, spec["cols"], spec["rows"], ray=prim.ray)
            if clipped is None:
                continue
            from dataclasses import replace
            prim = replace(prim, a=clipped[0], b=clipped[1])
        samples = construction._samples_units(prim, 0.1 if prim.kind == "circle" else 1e3)
        pts = [(ox + sx * u, oy + sy * u) for sx, sy in samples]
        if prim.kind == "circle":
            pts.append(pts[0])
        out.append({"name": prim.name, "state": states.get(prim.name) or raw.get("status") or "unknown",
                    "points": [[round(x, 1), round(_top(y), 1)] for x, y in pts]})
    return out


def zone_synthesis(db: Session, resp: StudentResponse, item: CopyItem, zone: ResponseZone) -> dict:
    """Ce qui part (ou est parti) en correction pour une réponse, posé sur le squelette."""
    reading = _reading(db, zone.id)
    raw = (reading.raw_json or {}) if reading else {}
    meta = zone.meta_json or {}
    rtype = item.response_type
    out: dict = {"response_id": resp.id, "sequence": item.sequence, "response_type": rtype,
                 "rect": _zone_rect(zone), "source": reading.provider if reading else None,
                 "teacher": bool(reading and reading.provider in ("teacher_ocr", "teacher"))}
    decision = (db.query(GradingDecision).filter_by(response_id=resp.id)
                .order_by(GradingDecision.created_at.desc()).first())
    if decision is not None:
        bareme = scoring.item_bareme(item.grading_json, rtype)
        out["points"] = round(scoring.earned_points(decision.score, decision.max_score, bareme), 3)
        out["bareme"] = round(float(bareme), 3)
    if rtype.startswith("qcm"):
        chosen = set(resp.selected_choices or raw.get("selected") or [])
        out["boxes"] = [{**_rect(b), "on": b.get("index") in chosen} for b in meta.get("boxes") or []]
    elif rtype == "checkbox_grid":
        sel = raw.get("grid_selected")
        if sel is None:
            sel = list(resp.selected_choices or [])
        out["boxes"] = [{**_rect(b), "on": (b.get("row") is not None and b.get("row") < len(sel)
                                             and sel[b["row"]] == b.get("col"))}
                        for b in meta.get("boxes") or []]
    elif rtype == "matching":
        pairs = raw.get("pairs")
        if pairs is None:
            pairs = resp.selected_pairs or []
        left = {p.get("index"): p for p in meta.get("left_points") or []}
        right = {p.get("index"): p for p in meta.get("right_points") or []}
        centre = lambda p: [float(p["x_pt"]) + float(p["w_pt"]) / 2,          # noqa: E731
                            _top(p["y_pt"], p["h_pt"]) + float(p["h_pt"]) / 2]
        out["links"] = [[*centre(left[a]), *centre(right[b])] for a, b in pairs
                        if a in left and b in right]
    elif rtype in ("table_fill", "multi_blank"):
        values = list(raw.get("cells") or [])
        exp = (item.expected_json or {}).get("cells") or []
        cells, k = [], 0
        for ri, row in enumerate(meta.get("cells") or []):
            for ci, cell in enumerate(row):
                given = ri < len(exp) and ci < len(exp[ri]) and exp[ri][ci].get("given")
                if given or not cell:
                    continue
                cells.append({**_rect(cell), "index": k, "text": str(values[k] if k < len(values) else "") or ""})
                k += 1
        out["cells"] = cells
    elif rtype == "manual_drawing":
        out["drawing"] = _construction_marks(zone, reading)
        status = ((raw.get("construction") or {}).get("status")) if raw else None
        out["text"] = {"blank": "vide", "complete": "tracé complet", "partial": "tracé partiel"}.get(status or "", "")
    else:
        out["text"] = resp.final_text or (reading.text if reading else "") or ""
    return out


def page_view(db: Session, copy: Copy, page_id: str) -> dict:
    """Squelette + synthèse d'une page de copie."""
    zones = db.query(ResponseZone).filter_by(page_id=page_id).all()
    index = _page_index(copy.assessment_id)
    paths = _pdf_skeleton(copy.assessment_id, index[page_id]) if page_id in index else None
    synth = []
    for z in zones:
        item = db.get(CopyItem, z.item_id)
        resp = db.query(StudentResponse).filter_by(copy_item_id=z.item_id).first() if item else None
        if item is None or resp is None or item.copy_id != copy.id:
            continue
        synth.append(zone_synthesis(db, resp, item, z))
    return {"width": PAGE_W, "height": PAGE_H,
            "paths": paths if paths is not None else _fallback_skeleton(zones),
            "exact": paths is not None, "zones": synth}


def copy_pages(db: Session, copy: Copy) -> list[DocumentPage]:
    return (db.query(DocumentPage).filter_by(copy_id=copy.id)
            .order_by(DocumentPage.page_no).all())

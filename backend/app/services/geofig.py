"""Moteur de figures GÉOMÉTRIQUES déclaratives : `{"type": "geo", "params": {...}}`.

Écrit pour qu'un modèle (Astra) REDESSINE une figure du manuel — ou l'adapte
pour qu'une réponse se coche ou se relie — à partir d'une spec contrôlée :
points nommés en coordonnées, puis tout ce qui s'y rattache par NOM.

    {"points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
     "polygons": [["A", "B", "C"]],
     "angles": [{"at": "A", "from": "B", "to": "C", "right": true}],
     "lengths": [{"seg": ["A", "B"], "label": "4 cm"}],
     "width_mm": 50}

Clés reconnues (toutes facultatives sauf `points`) :
- points    : {"A": [x, y]} ou {"A": {"xy": [x, y], "label": "A", "pos": "ne", "hide": false,
               "dot": true}} — label "" = sommet anonyme (ni nom ni point)
- segments  : [["A", "B"]] ou [{"from": "A", "to": "B", "style": "dashed", "label": "$d$"}]
- lines     : droite (infinie, rognée au cadre) [{"through": ["A", "B"], "label": "$(d)$"}]
- rays      : demi-droite [{"from": "A", "through": "B"}]
- polygons  : [["A", "B", "C"]] ou [{"vertices": [...], "fill": true, "style": "solid"}]
- circles   : [{"center": "O", "radius": 3} | {"center": "O", "through": "A"}]
- arcs      : [{"center": "O", "radius": 2, "from_deg": 0, "to_deg": 90}]
- angles    : [{"at": "B", "from": "A", "to": "C", "label": "$35^\\circ$", "right": false, "marks": 1}]
- lengths   : cote [{"seg": ["A", "B"], "label": "5 cm", "side": "auto"|"left"|"right"}]
               (left/right : à gauche/droite en allant de A vers B ; auto = vers l'extérieur)
- equal_marks    : codage [{"segs": [["A", "B"], ["A", "C"]], "ticks": 1}]
- parallel_marks : [{"segs": [["A", "B"], ["C", "D"]], "arrows": 1}]
- texts     : [{"at": [x, y], "text": "..."}]
- axes      : repère (cf. figkit.draw_axes) — figure en repère orthonormé
- functions : [{"expr": "2*x+1", "domain": [-3, 3], "label": "$f$"}]
- width_mm  : largeur imprimée (25–93 mm, défaut 70)

Toute erreur lève figkit.FigureSpecError avec un message qui nomme la clé.
"""
from __future__ import annotations

import math

import numpy as np
from matplotlib import patches

from . import figkit as fk
from .figkit import fail

MAX_POINTS = 40
MAX_ITEMS = 40
_POS = {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0),
        "ne": (0.8, 0.8), "nw": (-0.8, 0.8), "se": (0.8, -0.8), "sw": (-0.8, -0.8)}
KNOWN_KEYS = {"points", "segments", "lines", "rays", "polygons", "circles", "arcs",
              "angles", "lengths", "equal_marks", "parallel_marks", "texts", "axes",
              "functions", "width_mm"}


# ------------------------------------------------------------- lecture spec
def _points(spec: dict) -> dict[str, dict]:
    raw = fk.as_dict(spec.get("points"), "points")
    if not 1 <= len(raw) <= MAX_POINTS:
        fail("points", f"entre 1 et {MAX_POINTS} points")
    out = {}
    for name, val in raw.items():
        where = f"points.{name}"
        if not isinstance(name, str) or not 1 <= len(name) <= 4:
            fail(where, "nom de point de 1 à 4 caractères")
        if isinstance(val, dict):
            x, y = fk.xy(val.get("xy"), where)
            lab = val.get("label", name)
            pos = val.get("pos")
            hide = bool(val.get("hide", False))
            # un sommet sans nom (label "") n'a pas de point dessiné, sauf demande
            dot = bool(val.get("dot", lab not in ("", None)))
        else:
            x, y = fk.xy(val, where)
            lab, pos, hide, dot = name, None, False, True
        if pos is not None and pos not in _POS:
            fail(f"{where}.pos", f"une de {sorted(_POS)}")
        out[name] = {"xy": np.array([x, y]), "label": fk.label(
            lab if lab is not None else "", f"{where}.label", max_len=12),
            "pos": pos, "hide": hide, "dot": dot}
    return out


def _ref(pts: dict, name, where: str) -> np.ndarray:
    if name not in pts:
        fail(where, f"point inconnu {name!r} (points définis : {', '.join(pts)})")
    return pts[name]["xy"]


def _pair(pts, val, where: str) -> tuple[np.ndarray, np.ndarray]:
    if not (isinstance(val, (list, tuple)) and len(val) == 2):
        fail(where, f"couple de noms de points attendu, reçu {val!r}")
    a, b = _ref(pts, val[0], f"{where}[0]"), _ref(pts, val[1], f"{where}[1]")
    if np.allclose(a, b):
        fail(where, f"{val[0]} et {val[1]} sont confondus")
    return a, b


def _items(spec: dict, key: str) -> list:
    return fk.as_list(spec.get(key), key, max_len=MAX_ITEMS)


def validate(params: dict) -> dict:
    """Contrôle complet de la spec (sans rendu). Lève FigureSpecError."""
    spec = fk.as_dict(params, "params")
    unknown = set(spec) - KNOWN_KEYS
    if unknown:
        fail("params", f"clé(s) inconnue(s) {sorted(unknown)} ; connues : {sorted(KNOWN_KEYS)}")
    fk.width_mm(spec)
    # le rendu à blanc fait l'essentiel : chaque tracé relit sa propre entrée
    render(spec, dry=True)
    return spec


# ------------------------------------------------------------------ rendu
def _bbox(pts: dict, spec: dict) -> tuple[float, float, float, float]:
    arr = np.array([p["xy"] for p in pts.values()])
    xs, ys = list(arr[:, 0]), list(arr[:, 1])
    for i, c in enumerate(_items(spec, "circles")):
        c = fk.as_dict(c, f"circles[{i}]")
        o = _ref(pts, c.get("center"), f"circles[{i}].center")
        r = _circle_radius(pts, c, f"circles[{i}]", o)
        xs += [o[0] - r, o[0] + r]
        ys += [o[1] - r, o[1] + r]
    for i, t in enumerate(_items(spec, "texts")):
        x, y = fk.xy(fk.as_dict(t, f"texts[{i}]").get("at"), f"texts[{i}].at")
        xs.append(x)
        ys.append(y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1e-6)
    pad = span * 0.12
    return x0 - pad, x1 + pad, y0 - pad, y1 + pad


def _circle_radius(pts, c: dict, where: str, center) -> float:
    if "through" in c:
        r = float(np.linalg.norm(_ref(pts, c["through"], f"{where}.through") - center))
    else:
        r = fk.num(c.get("radius"), f"{where}.radius", 1e-3, 1e4)
    if r <= 0:
        fail(where, "rayon nul")
    return r


def _clip_line(a, b, box) -> tuple[np.ndarray, np.ndarray]:
    """Droite (a, b) rognée au cadre box=(x0, x1, y0, y1)."""
    x0, x1, y0, y1 = box
    d = b - a
    ts = []
    for k, (lo, hi) in enumerate(((x0, x1), (y0, y1))):
        if abs(d[k]) > 1e-12:
            ts += [(lo - a[k]) / d[k], (hi - a[k]) / d[k]]
    pts = [a + t * d for t in ts]
    pts = [p for p in pts if x0 - 1e-9 <= p[0] <= x1 + 1e-9 and y0 - 1e-9 <= p[1] <= y1 + 1e-9]
    if len(pts) < 2:
        return a, b
    pts.sort(key=lambda p: float(np.dot(p - a, d)))
    return pts[0], pts[-1]


def render(params: dict, *, dry: bool = False) -> bytes:
    spec = fk.as_dict(params, "params")
    pts = _points(spec)
    has_axes = spec.get("axes") is not None
    w_mm = fk.readable_width(spec, labels=sum(bool(p["label"]) and not p["hide"]
                                             for p in pts.values()), axes=spec.get("axes"))
    if has_axes:
        axes_spec = fk.as_dict(spec["axes"], "axes")
        xr = fk.as_dict(axes_spec.get("x") or {}, "axes.x")
        yr = fk.as_dict(axes_spec.get("y") or {}, "axes.y")
        box = (fk.num(xr.get("min", -5), "axes.x.min"), fk.num(xr.get("max", 5), "axes.x.max"),
               fk.num(yr.get("min", -5), "axes.y.min"), fk.num(yr.get("max", 5), "axes.y.max"))
    else:
        box = _bbox(pts, spec)
    bw, bh = box[1] - box[0], box[3] - box[2]
    if bw <= 0 or bh <= 0:
        fail("points", "figure dégénérée (tous les points alignés sur un axe)")
    h_mm = w_mm * bh / bw
    if h_mm > fk.MAX_HEIGHT_MM - 12:
        # figure très haute : on réduit la largeur pour tenir en hauteur
        w_mm = w_mm * (fk.MAX_HEIGHT_MM - 12) / h_mm
        h_mm = fk.MAX_HEIGHT_MM - 12
    fig = fk.new_figure(w_mm, h_mm)
    ax = fig.add_axes([0.06, 0.06, 0.88, 0.88])
    labels = fk.labels_for(ax)
    if has_axes:
        fk.draw_axes(ax, spec["axes"], "axes", equal=True)
    else:
        ax.set_xlim(box[0], box[1])
        ax.set_ylim(box[2], box[3])
        ax.set_aspect("equal")
        ax.axis("off")
    scale = bw / (w_mm / 25.4 * 72)          # unités de données par point typographique
    centroid = np.mean([p["xy"] for p in pts.values() if not p["hide"]] or
                       [np.zeros(2)], axis=0)
    ink = dict(color=fk.INK, linewidth=1.0)

    for i, f in enumerate(_items(spec, "functions")):
        f = fk.as_dict(f, f"functions[{i}]")
        fn = fk.compile_expr(f.get("expr"), f"functions[{i}].expr")
        dom = f.get("domain") or [box[0], box[1]]
        a, b = fk.xy(dom, f"functions[{i}].domain")
        if b <= a:
            fail(f"functions[{i}].domain", "borne haute ≤ borne basse")
        xs = np.linspace(a, b, 400)
        ys = fn(xs)
        if not np.isfinite(ys).any():
            fail(f"functions[{i}].expr", "aucune valeur définie sur le domaine")
        ax.plot(xs, ys, color=fk.SERIES_COLORS[i % 6], linewidth=1.2,
                linestyle=fk.line_style(f.get("style"), f"functions[{i}].style"))
        if f.get("label"):
            k = int(len(xs) * 0.85)
            labels.add(fk.label(f["label"], f"functions[{i}].label", max_len=20),
                       (xs[k], ys[k]), color=fk.SERIES_COLORS[i % 6])

    for i, poly in enumerate(_items(spec, "polygons")):
        where = f"polygons[{i}]"
        if isinstance(poly, dict):
            verts, fill, style = poly.get("vertices"), bool(poly.get("fill")), poly.get("style")
        else:
            verts, fill, style = poly, False, None
        verts = fk.as_list(verts, f"{where}.vertices", min_len=3, max_len=12)
        xy = np.array([_ref(pts, v, f"{where}.vertices") for v in verts])
        ax.add_patch(patches.Polygon(xy, closed=True, fill=fill,
                                     facecolor="#E8EEF7" if fill else "none",
                                     edgecolor=fk.INK, linewidth=1.0,
                                     linestyle=fk.line_style(style, f"{where}.style")))

    for i, s in enumerate(_items(spec, "segments")):
        where = f"segments[{i}]"
        if isinstance(s, dict):
            a, b = _pair(pts, [s.get("from"), s.get("to")], where)
            style, lab = s.get("style"), s.get("label")
        else:
            a, b = _pair(pts, s, where)
            style, lab = None, None
        ax.plot([a[0], b[0]], [a[1], b[1]], linestyle=fk.line_style(style, f"{where}.style"), **ink)
        if lab:
            _side_label(ax, a, b, fk.label(lab, f"{where}.label", max_len=20), centroid, scale, "auto")

    for i, ln in enumerate(_items(spec, "lines")):
        where = f"lines[{i}]"
        ln = {"through": ln} if isinstance(ln, list) else fk.as_dict(ln, where)
        a, b = _pair(pts, ln.get("through"), f"{where}.through")
        p, q = _clip_line(a, b, box)
        ax.plot([p[0], q[0]], [p[1], q[1]],
                linestyle=fk.line_style(ln.get("style"), f"{where}.style"), **ink)
        if ln.get("label"):
            d = (q - p) / np.linalg.norm(q - p)
            anchor = q - d * (bw * 0.06)
            labels.add(fk.label(ln["label"], f"{where}.label", max_len=20), anchor)

    for i, r in enumerate(_items(spec, "rays")):
        where = f"rays[{i}]"
        r = {"from": r[0], "through": r[1]} if isinstance(r, list) and len(r) == 2 \
            else fk.as_dict(r, where)
        a, b = _pair(pts, [r.get("from"), r.get("through")], where)
        _, q = _clip_line(a, b, box)
        if np.dot(q - a, b - a) < 0:
            q = b
        ax.plot([a[0], q[0]], [a[1], q[1]],
                linestyle=fk.line_style(r.get("style"), f"{where}.style"), **ink)

    for i, c in enumerate(_items(spec, "circles")):
        where = f"circles[{i}]"
        c = fk.as_dict(c, where)
        o = _ref(pts, c.get("center"), f"{where}.center")
        rad = _circle_radius(pts, c, where, o)
        ax.add_patch(patches.Circle(o, rad, fill=False, edgecolor=fk.INK, linewidth=1.0,
                                    linestyle=fk.line_style(c.get("style"), f"{where}.style")))

    for i, a in enumerate(_items(spec, "arcs")):
        where = f"arcs[{i}]"
        a = fk.as_dict(a, where)
        o = _ref(pts, a.get("center"), f"{where}.center")
        rad = fk.num(a.get("radius"), f"{where}.radius", 1e-3, 1e4)
        t1 = fk.num(a.get("from_deg", 0), f"{where}.from_deg", -720, 720)
        t2 = fk.num(a.get("to_deg", 360), f"{where}.to_deg", -720, 720)
        ax.add_patch(patches.Arc(o, 2 * rad, 2 * rad, theta1=t1, theta2=t2,
                                 edgecolor=fk.INK, linewidth=1.0))

    for i, g in enumerate(_items(spec, "angles")):
        _angle(ax, pts, fk.as_dict(g, f"angles[{i}]"), f"angles[{i}]", bw)

    for i, L in enumerate(_items(spec, "lengths")):
        where = f"lengths[{i}]"
        L = fk.as_dict(L, where)
        a, b = _pair(pts, L.get("seg"), f"{where}.seg")
        side = L.get("side", "auto")
        if side not in ("auto", "left", "right"):
            fail(f"{where}.side", "« auto », « left » ou « right »")
        _side_label(ax, a, b, fk.label(L.get("label"), f"{where}.label", max_len=20),
                    centroid, scale, side)

    for i, m in enumerate(_items(spec, "equal_marks")):
        where = f"equal_marks[{i}]"
        m = fk.as_dict(m, where)
        ticks = int(fk.num(m.get("ticks", 1), f"{where}.ticks", 1, 3))
        for j, seg in enumerate(fk.as_list(m.get("segs"), f"{where}.segs", min_len=1, max_len=8)):
            a, b = _pair(pts, seg, f"{where}.segs[{j}]")
            _ticks(ax, a, b, ticks, bw)

    for i, m in enumerate(_items(spec, "parallel_marks")):
        where = f"parallel_marks[{i}]"
        m = fk.as_dict(m, where)
        n = int(fk.num(m.get("arrows", 1), f"{where}.arrows", 1, 3))
        for j, seg in enumerate(fk.as_list(m.get("segs"), f"{where}.segs", min_len=2, max_len=6)):
            a, b = _pair(pts, seg, f"{where}.segs[{j}]")
            _chevrons(ax, a, b, n, bw)

    for i, t in enumerate(_items(spec, "texts")):
        where = f"texts[{i}]"
        t = fk.as_dict(t, where)
        x, y = fk.xy(t.get("at"), f"{where}.at")
        ax.text(x, y, fk.label(t.get("text"), f"{where}.text", max_len=60),
                fontsize=fk.FONT_SIZE, ha="center", va="center", color=fk.INK)

    for name, p in pts.items():
        if p["hide"]:
            continue
        if p["dot"]:
            ax.plot(*p["xy"], "o", color=fk.INK, markersize=2.2)
        if p["label"]:
            d = np.array(_POS[p["pos"]]) if p["pos"] else p["xy"] - centroid
            n = np.linalg.norm(d)
            d = d / n if n > 1e-9 else np.array([0.7, 0.7])
            labels.add(p["label"], p["xy"], direction=d, fontsize=fk.FONT_SIZE + 0.5)
    if has_axes:
        fk.space_tick_labels(ax)
    labels.place()
    if dry:
        import matplotlib.pyplot as plt
        fig.canvas.draw()          # force le rendu mathtext : une formule qui casse casse ici
        plt.close(fig)
        return b""
    return fk.to_png(fig)


def _side_label(ax, a, b, text, centroid, scale, side):
    if not text:
        return
    mid = (a + b) / 2
    d = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-d[1], d[0]])
    if side == "right" or (side == "auto" and np.dot(normal, mid - centroid) < 0):
        normal = -normal
    if side == "left":
        normal = np.array([-d[1], d[0]])
    fk.labels_for(ax).add(text, mid, direction=normal, radial=True)


def _angle(ax, pts, g: dict, where: str, span: float) -> None:
    o = _ref(pts, g.get("at"), f"{where}.at")
    a = _ref(pts, g.get("from"), f"{where}.from")
    b = _ref(pts, g.get("to"), f"{where}.to")
    u, v = a - o, b - o
    if np.linalg.norm(u) < 1e-9 or np.linalg.norm(v) < 1e-9:
        fail(where, "un côté de l'angle est de longueur nulle")
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    # Un arc dépend de SES côtés, pas de la largeur de toute une planche.
    size = min(np.linalg.norm(a-o), np.linalg.norm(b-o), span) * 0.18 * fk.num(
        g.get("size", 1) or 1, f"{where}.size", 0.1, 4)
    if g.get("right"):
        p1, p3 = o + u * size * 0.8, o + v * size * 0.8
        p2 = p1 + v * size * 0.8
        ax.plot([p1[0], p2[0], p3[0]], [p1[1], p2[1], p3[1]], color=fk.INK, linewidth=0.8)
        return
    t1 = math.degrees(math.atan2(u[1], u[0]))
    t2 = math.degrees(math.atan2(v[1], v[0]))
    if (t2 - t1) % 360 > 180:           # toujours l'angle saillant
        t1, t2 = t2, t1
    marks = int(fk.num(g.get("marks", 1), f"{where}.marks", 1, 3))
    for k in range(marks):
        r = size * (1 + 0.22 * k)
        ax.add_patch(patches.Arc(o, 2 * r, 2 * r, theta1=t1, theta2=t2,
                                 edgecolor=fk.INK, linewidth=0.8))
    if g.get("label"):
        bis = u + v
        bis = bis / np.linalg.norm(bis) if np.linalg.norm(bis) > 1e-9 else np.array([-u[1], u[0]])
        fk.labels_for(ax).add(fk.label(g["label"], f"{where}.label", max_len=20),
                             o + bis * size * (1 + 0.22 * (marks-1)),
                             direction=bis, radial=True, distance=5)


def _ticks(ax, a, b, n: int, span: float) -> None:
    mid = (a + b) / 2
    d = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-d[1], d[0]])
    h = span * 0.022
    gap = span * 0.014
    for k in range(n):
        c = mid + d * gap * (k - (n - 1) / 2)
        p, q = c - normal * h + d * h * 0.35, c + normal * h - d * h * 0.35
        ax.plot([p[0], q[0]], [p[1], q[1]], color=fk.INK, linewidth=0.8)


def _chevrons(ax, a, b, n: int, span: float) -> None:
    mid = (a + b) / 2
    d = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-d[1], d[0]])
    h = span * 0.02
    for k in range(n):
        tip = mid + d * h * 0.9 * (k - (n - 1) / 2)
        p, q = tip - d * h + normal * h, tip - d * h - normal * h
        ax.plot([p[0], tip[0], q[0]], [p[1], tip[1], q[1]], color=fk.INK, linewidth=0.8)

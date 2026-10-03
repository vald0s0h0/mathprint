"""Moteur de GRAPHIQUES déclaratifs : `{"type": "chart", "params": {...}}`.

Recrée un graphique d'énoncé — courbe, lecture graphique, diagramme en barres,
histogramme, diagramme circulaire, nuage de points, arbre de probabilités — à
partir des MÊMES données que le manuel, relues sur l'image par le modèle.

Un graphique est de l'une de trois familles, jamais mélangées :

1. REPÈRE (`axes` obligatoire) + tracés superposables :
   - functions : [{"expr": "0.5*x+1", "domain": [0, 6], "label": "$f$", "style": "solid"}]
                 ou [{"pieces": [{"expr": .., "domain": [..]}, ...], "label": ..}] (par morceaux)
   - curves    : [{"points": [[0, 12], [2, 15], ...], "smooth": true, "markers": true, "label": ..}]
   - points    : [{"xy": [2, 3], "label": "A", "reading": true, "pos": "ne"}]
                 (`reading` : pointillés de LECTURE GRAPHIQUE vers les deux axes)
   - scatter   : [{"points": [[x, y], ...], "label": ..}]
   - bars      : {"categories": ["Lun", ...], "series": [{"values": [..], "label": ..}],
                  "style": "bars"|"sticks", "value_labels": false}
                  (axe x = catégories ; seul `axes.y` compte)
   - histogram : {"edges": [0, 10, 20, 30], "values": [4, 7, 2]}
2. CIRCULAIRE : pie = {"values": [..], "labels": [..], "half": false,
                       "show": "none"|"value"|"percent", "unit": "angle"|"count"}
3. ARBRE : tree = {"children": [{"label": "R", "prob": "$\\frac{1}{3}$", "children": [...]}],
                   "outcomes": true}

Communs : `legend` (bool), `title`, `width_mm` (25–93), `height_mm` (facultatif).
Toute erreur lève figkit.FigureSpecError avec un message qui nomme la clé.
"""
from __future__ import annotations

import math

import numpy as np

from . import figkit as fk
from .figkit import fail

AXES_KEYS = {"functions", "curves", "points", "scatter", "bars", "histogram"}
KNOWN_KEYS = AXES_KEYS | {"axes", "pie", "tree", "legend", "title", "width_mm", "height_mm"}
MAX_SERIES, MAX_POINTS, MAX_CATEGORIES = 6, 60, 16
_POS = {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0),
        "ne": (0.8, 0.8), "nw": (-0.8, 0.8), "se": (0.8, -0.8), "sw": (-0.8, -0.8)}


def validate(params: dict) -> dict:
    spec = fk.as_dict(params, "params")
    unknown = set(spec) - KNOWN_KEYS
    if unknown:
        fail("params", f"clé(s) inconnue(s) {sorted(unknown)} ; connues : {sorted(KNOWN_KEYS)}")
    render(spec, dry=True)
    return spec


def _family(spec: dict) -> str:
    has_axes_plot = any(spec.get(k) for k in AXES_KEYS)
    fams = [f for f, on in (("axes", has_axes_plot or spec.get("axes") is not None),
                            ("pie", spec.get("pie") is not None),
                            ("tree", spec.get("tree") is not None)) if on]
    if len(fams) != 1:
        fail("params", "un graphique est UN repère (axes + tracés), OU un `pie`, "
                       f"OU un `tree` — reçu : {fams or 'rien'}")
    if fams[0] == "axes" and spec.get("axes") is None:
        fail("axes", "obligatoire pour functions/curves/points/scatter/bars/histogram")
    return fams[0]


def render(params: dict, *, dry: bool = False) -> bytes:
    spec = fk.as_dict(params, "params")
    family = _family(spec)
    w_mm = fk.readable_width(spec, labels=len(spec.get("points") or []),
                             axes=spec.get("axes"))
    h_mm = fk.num(spec["height_mm"], "height_mm", 15, fk.MAX_HEIGHT_MM) \
        if spec.get("height_mm") is not None else None
    if family == "pie":
        fig = _pie(spec, w_mm, h_mm)
    elif family == "tree":
        fig = _tree(spec, w_mm, h_mm)
    else:
        fig = _axes_chart(spec, w_mm, h_mm)
    if spec.get("title"):
        fig.suptitle(fk.label(spec["title"], "title", max_len=80), fontsize=fk.FONT_SIZE)
    for ax in fig.axes:
        if family == "axes":
            fk.space_tick_labels(ax)
        fk.labels_for(ax).place()
    if dry:
        import matplotlib.pyplot as plt
        fig.canvas.draw()
        plt.close(fig)
        return b""
    return fk.to_png(fig)


# ------------------------------------------------------------------ repère
def _axes_chart(spec: dict, w_mm: float, h_mm: float | None):
    axes = fk.as_dict(spec["axes"], "axes")
    has_bars = spec.get("bars") is not None
    fig = fk.new_figure(w_mm, h_mm or w_mm * 0.78)
    ax = fig.add_axes([0.1, 0.12, 0.86, 0.82])
    if has_bars:
        _bars(ax, spec, axes)
    else:
        fk.draw_axes(ax, axes, "axes", equal=bool(axes.get("equal", False)))
    handles = []
    for i, f in enumerate(fk.as_list(spec.get("functions"), "functions", max_len=MAX_SERIES)):
        handles += _function(ax, fk.as_dict(f, f"functions[{i}]"), f"functions[{i}]", i)
    for i, c in enumerate(fk.as_list(spec.get("curves"), "curves", max_len=MAX_SERIES)):
        handles += _curve(ax, fk.as_dict(c, f"curves[{i}]"), f"curves[{i}]", i)
    for i, s in enumerate(fk.as_list(spec.get("scatter"), "scatter", max_len=MAX_SERIES)):
        s = fk.as_dict(s, f"scatter[{i}]")
        pts = [fk.xy(p, f"scatter[{i}].points[{j}]") for j, p in enumerate(
            fk.as_list(s.get("points"), f"scatter[{i}].points", min_len=1, max_len=MAX_POINTS))]
        h = ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=9,
                       color=fk.SERIES_COLORS[i % 6], zorder=4,
                       label=fk.label(s.get("label"), f"scatter[{i}].label", max_len=30) or None)
        handles.append(h)
    if spec.get("histogram") is not None:
        _histogram(ax, fk.as_dict(spec["histogram"], "histogram"))
    for i, p in enumerate(fk.as_list(spec.get("points"), "points", max_len=MAX_POINTS)):
        _point(ax, fk.as_dict(p, f"points[{i}]"), f"points[{i}]")
    if spec.get("legend"):
        _h, labels = ax.get_legend_handles_labels()
        if labels:
            ax.legend(fontsize=fk.FONT_SIZE - 1.5, frameon=False, loc="best")
    return fig


def _function(ax, f: dict, where: str, i: int) -> list:
    pieces = f.get("pieces")
    if pieces is None:
        pieces = [{"expr": f.get("expr"), "domain": f.get("domain")}]
    pieces = fk.as_list(pieces, f"{where}.pieces", min_len=1, max_len=6)
    lab = fk.label(f.get("label"), f"{where}.label", max_len=30)
    style = fk.line_style(f.get("style"), f"{where}.style")
    color = fk.SERIES_COLORS[i % 6]
    x0, x1 = ax.get_xlim()
    handles = []
    for j, p in enumerate(pieces):
        p = fk.as_dict(p, f"{where}.pieces[{j}]")
        fn = fk.compile_expr(p.get("expr"), f"{where}.pieces[{j}].expr")
        a, b = fk.xy(p.get("domain") or [x0, x1], f"{where}.pieces[{j}].domain")
        if b <= a:
            fail(f"{where}.pieces[{j}].domain", "borne haute ≤ borne basse")
        xs = np.linspace(a, b, 400)
        ys = fn(xs)
        if not np.isfinite(ys).any():
            fail(f"{where}.pieces[{j}].expr", "aucune valeur définie sur le domaine")
        (h,) = ax.plot(xs, ys, color=color, linewidth=1.3, linestyle=style,
                       label=(lab or None) if j == 0 else None, zorder=3)
        handles.append(h)
    if lab and not f.get("legend_only"):
        xs, ys = handles[-1].get_data()
        k = max(0, int(len(xs) * 0.9) - 1)
        y0, y1 = ax.get_ylim()
        if np.isfinite(ys[k]) and y0 <= ys[k] <= y1:
            fk.labels_for(ax).add(lab, (xs[k], ys[k]), color=color)
    return handles


def _curve(ax, c: dict, where: str, i: int) -> list:
    pts = [fk.xy(p, f"{where}.points[{j}]") for j, p in enumerate(
        fk.as_list(c.get("points"), f"{where}.points", min_len=2, max_len=MAX_POINTS))]
    xs = [p[0] for p in pts]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        fail(f"{where}.points", "abscisses strictement croissantes attendues")
    color = fk.SERIES_COLORS[i % 6]
    lab = fk.label(c.get("label"), f"{where}.label", max_len=30)
    if c.get("smooth", True):
        cx, cy = fk.monotone_curve(pts)
    else:
        cx, cy = np.array(xs), np.array([p[1] for p in pts])
    (h,) = ax.plot(cx, cy, color=color, linewidth=1.3, zorder=3, label=lab or None,
                   linestyle=fk.line_style(c.get("style"), f"{where}.style"))
    if c.get("markers", False):
        ax.plot(xs, [p[1] for p in pts], "o", color=color, markersize=2.6, zorder=4)
    return [h]


def _point(ax, p: dict, where: str) -> None:
    x, y = fk.xy(p.get("xy"), f"{where}.xy")
    ax.plot(x, y, "o", color=fk.INK, markersize=2.8, zorder=5)
    if p.get("reading"):
        x0 = 0 if ax.get_xlim()[0] <= 0 <= ax.get_xlim()[1] else ax.get_xlim()[0]
        y0 = 0 if ax.get_ylim()[0] <= 0 <= ax.get_ylim()[1] else ax.get_ylim()[0]
        ax.plot([x, x], [y0, y], ":", color=fk.INK, linewidth=0.9, zorder=4)
        ax.plot([x0, x], [y, y], ":", color=fk.INK, linewidth=0.9, zorder=4)
    if p.get("label"):
        pos = p.get("pos", "ne")
        if pos not in _POS:
            fail(f"{where}.pos", f"une de {sorted(_POS)}")
        d = _POS[pos]
        fk.labels_for(ax).add(fk.label(p["label"], f"{where}.label", max_len=20),
                             (x, y), direction=d)


def _bars(ax, spec: dict, axes: dict) -> None:
    b = fk.as_dict(spec["bars"], "bars")
    cats = [fk.label(c, f"bars.categories[{j}]", max_len=24) for j, c in enumerate(
        fk.as_list(b.get("categories"), "bars.categories", min_len=1, max_len=MAX_CATEGORIES))]
    series = fk.as_list(b.get("series"), "bars.series", min_len=1, max_len=MAX_SERIES)
    style = b.get("style", "bars")
    if style not in ("bars", "sticks"):
        fail("bars.style", "« bars » (diagramme en barres) ou « sticks » (en bâtons)")
    # axe vertical : même graduation qu'un repère, axe horizontal = catégories
    y = fk.as_dict(axes.get("y") or {}, "axes.y")
    y_min = fk.num(y.get("min", 0), "axes.y.min")
    y_max = fk.num(y.get("max", 10), "axes.y.max")
    step = fk.num(y.get("step", 1), "axes.y.step", 1e-6, 1e5)
    if y_max <= y_min or (y_max - y_min) / step > 60:
        fail("axes.y", "bornes/pas incohérents (max > min, au plus 60 graduations)")
    n = len(cats)
    width = 0.7 / len(series) if style == "bars" else 0.0
    for i, s in enumerate(series):
        s = fk.as_dict(s, f"bars.series[{i}]")
        vals = [fk.num(v, f"bars.series[{i}].values[{j}]", -1e6, 1e6) for j, v in enumerate(
            fk.as_list(s.get("values"), f"bars.series[{i}].values", min_len=n, max_len=n))]
        xs = np.arange(n) + (i - (len(series) - 1) / 2) * (width if style == "bars" else 0.12)
        lab = fk.label(s.get("label"), f"bars.series[{i}].label", max_len=30) or None
        if style == "bars":
            ax.bar(xs, vals, width=width * 0.92, color=fk.SERIES_COLORS[i % 6],
                   hatch=fk.SERIES_HATCH[i % 6], edgecolor=fk.INK, linewidth=0.6,
                   label=lab, zorder=3)
        else:
            ax.vlines(xs, 0, vals, color=fk.SERIES_COLORS[i % 6], linewidth=2.2,
                      label=lab, zorder=3)
        if b.get("value_labels"):
            for xv, v in zip(xs, vals):
                ax.annotate(fk.fmt(v), (xv, v), xytext=(0, 2), textcoords="offset points",
                            ha="center", va="bottom", fontsize=fk.FONT_SIZE - 1.5)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_ylim(y_min, y_max)
    ax.set_xticks(range(n))
    ax.set_xticklabels(cats, fontsize=fk.FONT_SIZE - 1.5,
                       rotation=30 if sum(len(c) for c in cats) > 48 else 0,
                       ha="right" if sum(len(c) for c in cats) > 48 else "center")
    first = math.ceil(y_min / step - 1e-9) * step
    ticks = [round(first + k * step, 10) for k in range(int((y_max - first) / step + 1e-9) + 1)]
    ax.set_yticks(ticks)
    ax.set_yticklabels([fk.fmt(t) for t in ticks], fontsize=fk.FONT_SIZE - 1.5)
    if y.get("minor"):
        mstep = fk.num(y["minor"], "axes.y.minor", 1e-6, 1e5)
        mfirst = math.ceil(y_min / mstep - 1e-9) * mstep
        ax.set_yticks([mfirst + k * mstep for k in range(int((y_max - mfirst) / mstep + 1e-9) + 1)],
                      minor=True)
    ax.grid(True, axis="y", color="#C9D0DA", linewidth=0.5,
            which="both" if axes.get("grid") == "both" else "major")
    if axes.get("grid") == "none":
        ax.grid(False)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=2.5, width=0.6, pad=1.5)
    x = fk.as_dict(axes.get("x") or {}, "axes.x")
    if x.get("label"):
        ax.set_xlabel(fk.label(x["label"], "axes.x.label", max_len=40), fontsize=fk.FONT_SIZE - 1)
    if y.get("label"):
        ax.set_ylabel(fk.label(y["label"], "axes.y.label", max_len=40), fontsize=fk.FONT_SIZE - 1)


def _histogram(ax, h: dict) -> None:
    edges = [fk.num(e, f"histogram.edges[{j}]") for j, e in enumerate(
        fk.as_list(h.get("edges"), "histogram.edges", min_len=2, max_len=MAX_CATEGORIES + 1))]
    if any(b <= a for a, b in zip(edges, edges[1:])):
        fail("histogram.edges", "bornes de classes strictement croissantes (classes contiguës)")
    vals = [fk.num(v, f"histogram.values[{j}]", 0, 1e6) for j, v in enumerate(
        fk.as_list(h.get("values"), "histogram.values",
                   min_len=len(edges) - 1, max_len=len(edges) - 1))]
    ax.bar(edges[:-1], vals, width=np.diff(edges), align="edge", color=fk.SERIES_COLORS[0],
           edgecolor=fk.INK, linewidth=0.7, zorder=3)


# --------------------------------------------------------------- circulaire
def _pie(spec: dict, w_mm: float, h_mm: float | None):
    p = fk.as_dict(spec["pie"], "pie")
    vals = [fk.num(v, f"pie.values[{j}]", 0, 1e7) for j, v in enumerate(
        fk.as_list(p.get("values"), "pie.values", min_len=2, max_len=MAX_CATEGORIES))]
    if sum(vals) <= 0:
        fail("pie.values", "somme nulle")
    half = bool(p.get("half", False))
    if p.get("unit") == "angle":
        total = 180 if half else 360
        if abs(sum(vals) - total) > 0.5:
            fail("pie.values", f"angles de somme {sum(vals):g}°, attendu {total}°")
    labels = [fk.label(t, f"pie.labels[{j}]", max_len=30) for j, t in enumerate(
        fk.as_list(p.get("labels"), "pie.labels", min_len=len(vals), max_len=len(vals)))]
    show = p.get("show", "none")
    if show not in ("none", "value", "percent"):
        fail("pie.show", "« none », « value » ou « percent »")
    fig = fk.new_figure(w_mm, h_mm or (w_mm * (0.62 if half else 0.9)))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    total = sum(vals)
    span = 180 if half else 360
    start = 180 if half else 90
    angle = start
    for j, (v, lab) in enumerate(zip(vals, labels)):
        sweep = span * v / total
        from matplotlib.patches import Wedge
        t1, t2 = (angle - sweep, angle) if not half else (angle - sweep, angle)
        ax.add_patch(Wedge((0, 0), 1, t1, t2, facecolor=fk.SERIES_COLORS[j % 6],
                           hatch=fk.SERIES_HATCH[j % 6], edgecolor="white", linewidth=1.0))
        mid = math.radians((t1 + t2) / 2)
        ax.annotate(lab, (1.18 * math.cos(mid), 1.18 * math.sin(mid)),
                    ha="left" if math.cos(mid) > 0.1 else "right" if math.cos(mid) < -0.1
                    else "center", va="center", fontsize=fk.FONT_SIZE - 0.5)
        if show != "none":
            txt = fk.fmt(v) if show == "value" else f"{fk.fmt(round(100 * v / total, 1))} %"
            ax.annotate(txt, (0.62 * math.cos(mid), 0.62 * math.sin(mid)), ha="center",
                        va="center", fontsize=fk.FONT_SIZE - 1.5, color=fk.INK,
                        bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.85))
        angle -= sweep
    ax.set_xlim(-1.7, 1.7)
    ax.set_ylim(-0.15 if half else -1.35, 1.35)
    return fig


# -------------------------------------------------------------------- arbre
def _tree(spec: dict, w_mm: float, h_mm: float | None):
    t = fk.as_dict(spec["tree"], "tree")
    leaves: list = []

    def walk(node: dict, where: str, depth: int, path: list[str]) -> dict:
        kids = fk.as_list(node.get("children"), f"{where}.children", max_len=6)
        if depth > 4:
            fail(where, "arbre trop profond (4 niveaux au plus)")
        out = {"label": fk.label(node.get("label"), f"{where}.label", max_len=20),
               "prob": fk.label(node.get("prob"), f"{where}.prob", max_len=24),
               "children": [], "depth": depth}
        path = path + ([out["label"]] if out["label"] else [])
        for j, k in enumerate(kids):
            out["children"].append(walk(fk.as_dict(k, f"{where}.children[{j}]"),
                                        f"{where}.children[{j}]", depth + 1, path))
        if not out["children"]:
            out["y"] = len(leaves)
            out["path"] = path
            leaves.append(out)
        else:
            out["y"] = sum(c["y"] for c in out["children"]) / len(out["children"])
        return out

    root = walk({"children": t.get("children"), "label": ""}, "tree", 0, [])
    if not root["children"]:
        fail("tree.children", "au moins une branche")
    if len(leaves) > 16:
        fail("tree", "16 issues au plus")
    depth = max(l["depth"] for l in leaves)
    fig = fk.new_figure(w_mm, h_mm or max(22.0, min(fk.MAX_HEIGHT_MM, 7.5 * len(leaves) + 8)))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    n = len(leaves)
    extra = 1.1 if t.get("outcomes") else 0.3

    def ypos(node):
        return (n - 1) / 2 - node["y"]

    def draw(node):
        x0, y0 = node["depth"], ypos(node)
        for k in node["children"]:
            x1, y1 = k["depth"], ypos(k)
            ax.plot([x0 + 0.12, x1 - 0.12], [y0, y1], color=fk.INK, linewidth=0.9)
            if k["prob"]:
                # au-dessus d'une branche montante, en dessous d'une descendante :
                # deux branches issues du même nœud ne se disputent jamais la place
                up = y1 >= y0
                ax.annotate(k["prob"], (x0 + 0.55 * (x1 - x0), y0 + 0.55 * (y1 - y0)),
                            xytext=(0, 4 if up else -4), textcoords="offset points",
                            ha="center", va="bottom" if up else "top",
                            fontsize=fk.FONT_SIZE - 1, color="#2F5D9E")
            if k["label"]:
                ax.text(x1, y1, k["label"], ha="center", va="center", fontsize=fk.FONT_SIZE,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))
            draw(k)

    draw(root)
    if t.get("outcomes"):
        for leaf in leaves:
            ax.text(depth + 0.35, ypos(leaf), f"({' ; '.join(leaf['path'])})", ha="left",
                    va="center", fontsize=fk.FONT_SIZE - 1.5)
    ax.set_xlim(-0.2, depth + extra)
    ax.set_ylim(-(n - 1) / 2 - 0.6, (n - 1) / 2 + 0.6)
    return fig

"""Outils communs aux moteurs de figures DÉCLARATIVES (geofig, chartfig).

Une figure déclarative est un JSON écrit par un modèle (Astra) : chaque valeur
y est contrôlée AVANT le rendu, et chaque refus lève `FigureSpecError` avec un
message lisible (« segments[2] : point inconnu 'E' ») — le modèle doit pouvoir
corriger sa spec, un `None` muet ne lui apprendrait rien.

Les textes (étiquettes, légendes, titres d'axes) suivent le contrat des énoncés :
texte libre + formules `$...$` sur la liste blanche de services.mathrender,
converties pour matplotlib mathtext.
"""
from __future__ import annotations

import io
import math
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import mathrender  # noqa: E402

# Résolution de rendu : écrite dans les métadonnées du PNG, relue par pdfgen
# pour imprimer la figure à sa taille physique voulue (`width_mm`).
RENDER_DPI = 300
# version du rendu : entre dans la clé du cache disque (services.figures), pour
# qu'une amélioration du moteur ne serve jamais une ancienne image
ENGINE_VERSION = "3.4"
DEFAULT_WIDTH_MM = 70.0
MIN_WIDTH_MM, MAX_WIDTH_MM = 25.0, 93.0
MAX_HEIGHT_MM = 90.0            # plafond de pdfgen._figure_image
INK = "#1A1A1A"
FONT_SIZE = 10.0


class FigureSpecError(ValueError):
    """Spec de figure refusée — message destiné à l'auteur de la spec."""


def fail(where: str, msg: str) -> None:
    raise FigureSpecError(f"{where} : {msg}")


def as_dict(value, where: str) -> dict:
    if not isinstance(value, dict):
        fail(where, f"objet attendu, reçu {type(value).__name__}")
    return value


def as_list(value, where: str, *, max_len: int, min_len: int = 0) -> list:
    if value is None:
        value = []
    if not isinstance(value, list):
        fail(where, f"liste attendue, reçu {type(value).__name__}")
    if not min_len <= len(value) <= max_len:
        fail(where, f"{len(value)} élément(s), attendu entre {min_len} et {max_len}")
    return value


def num(value, where: str, lo: float = -1e4, hi: float = 1e4) -> float:
    """Nombre fini dans [lo, hi]. Accepte « 3,5 » (virgule française)."""
    if isinstance(value, str):
        try:
            value = float(value.replace(",", ".").strip())
        except ValueError:
            fail(where, f"nombre attendu, reçu {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(where, f"nombre attendu, reçu {value!r}")
    value = float(value)
    if not math.isfinite(value) or not lo <= value <= hi:
        fail(where, f"{value:g} hors de [{lo:g}, {hi:g}]")
    return value


def xy(value, where: str) -> tuple[float, float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        fail(where, f"couple [x, y] attendu, reçu {value!r}")
    return num(value[0], f"{where}.x"), num(value[1], f"{where}.y")


def label(value, where: str, *, max_len: int = 60) -> str:
    """Texte d'étiquette validé et converti pour matplotlib (mathtext).

    Même contrat qu'un énoncé : formules entre `$...$`, commandes LaTeX sur
    liste blanche, aucune commande hors formule."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f"{value:g}".replace(".", ",")
    if not isinstance(value, str):
        fail(where, f"texte attendu, reçu {value!r}")
    text = value.strip()
    if len(text) > max_len:
        fail(where, f"texte trop long ({len(text)} > {max_len})")
    if text.count("$") % 2:
        fail(where, f"formule $...$ mal fermée : {text!r}")
    out = []
    for content, is_math in mathrender.split_math_spans(text):
        if not is_math:
            if "\\" in content:
                fail(where, f"commande LaTeX hors $...$ : {content!r}")
            out.append(content)
            continue
        clean = mathrender.sanitize_latex(content)
        if clean is None:
            fail(where, f"formule refusée : ${content}$")
        out.append(f"${mathrender.to_mathtext(clean)}$")
    return "".join(out)


def fmt(v: float) -> str:
    """Nombre au format français (graduation) : 7,5 ; 4 ; -0,25."""
    if abs(v - round(v)) < 1e-9:
        v = round(v)
    return f"{v:g}".replace(".", ",").replace("-", "−")


def width_mm(params: dict, where: str = "width_mm") -> float:
    if params.get("width_mm") is None:
        return DEFAULT_WIDTH_MM
    return num(params["width_mm"], where, MIN_WIDTH_MM, MAX_WIDTH_MM)


def new_figure(w_mm: float, h_mm: float):
    h_mm = max(20.0, min(h_mm, MAX_HEIGHT_MM - 10))
    fig = plt.figure(figsize=(w_mm / 25.4, h_mm / 25.4), dpi=RENDER_DPI)
    return fig


def readable_width(params: dict, *, labels: int = 0, axes: dict | None = None) -> float:
    """La largeur demandée est un minimum ; la densité peut l'augmenter.

    On garde la taille des lettres, même sur une petite figure. Les graduations
    conservent toutes leurs valeurs et leur pas : on agrandit le repère.
    """
    wanted = max(width_mm(params), min(MAX_WIDTH_MM, 48 + labels * 2.5))
    if axes:
        for axis in ("x", "y"):
            a = as_dict(axes.get(axis) or {}, f"axes.{axis}")
            lo = num(a.get("min", -5), f"axes.{axis}.min")
            hi = num(a.get("max", 5), f"axes.{axis}.max")
            step = num(a.get("step", 1), f"axes.{axis}.step", 1e-6, 1e4)
            if a.get("tick_labels", True):
                wanted = max(wanted, 18 + (hi - lo) / step * 6)
    return min(MAX_WIDTH_MM, wanted)


class LabelLayout:
    """Place les étiquettes en coordonnées d'impression, après tous les tracés.

    Les boîtes mesurées par matplotlib évitent textes, points, traits et arcs.
    La direction fournie est une préférence, pas un placement aveugle. Les
    cotes et angles limitent leurs candidats à leur normale/bissectrice pour
    rester associés au bon objet. Aucun point de la figure n'est déplacé.
    """

    def __init__(self, ax):
        self.ax = ax
        self.items = []

    def add(self, text, xy, *, direction=(1, 1), distance=4, radial=False,
            fontsize=FONT_SIZE, color=INK, max_extra=12):
        if not text:
            return
        artist = self.ax.annotate(
            text, xy, xytext=(0, 0), textcoords="offset points",
            ha="center", va="center", fontsize=fontsize, color=color,
            zorder=10, annotation_clip=False,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.12})
        self.items.append((artist, np.asarray(direction, dtype=float), distance, radial, max_extra))
        return artist

    def place(self, *, outside_penalty=100):
        if not self.items:
            return
        from matplotlib.transforms import Bbox

        fig = self.ax.figure
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        px = fig.dpi / 72
        movable = {a for a, *_ in self.items}
        occupied = [t.get_window_extent(renderer).expanded(1.06, 1.15)
                    for t in self.ax.texts if t not in movable and t.get_text()]
        occupied += [t.get_window_extent(renderer).expanded(1.08, 1.15)
                     for axis in (self.ax.xaxis, self.ax.yaxis)
                     for t in axis.get_ticklabels() if t.get_visible() and t.get_text()]
        paths, dots = [], []
        for line in self.ax.lines:
            path = line.get_transform().transform_path(line.get_path())
            if line.get_linestyle() not in ("None", "none", "", " "):
                paths.append(path)
            if line.get_marker() not in ("None", "none", "", " ", None):
                r = (line.get_markersize() / 2 + 1.5) * px
                dots += [Bbox.from_extents(x-r, y-r, x+r, y+r)
                         for x, y in path.vertices if np.isfinite([x, y]).all()]
        for patch in self.ax.patches:
            paths.append(patch.get_transform().transform_path(patch.get_path()))
        for spine in self.ax.spines.values():
            if spine.get_visible() and self.ax.axison:
                paths.append(spine.get_transform().transform_path(spine.get_path()))
        for collection in self.ax.collections:
            offsets = collection.get_offset_transform().transform(collection.get_offsets())
            r = 3 * px
            dots += [Bbox.from_extents(x-r, y-r, x+r, y+r) for x, y in offsets]

        # Les points les plus proches d'autres points sont traités d'abord.
        anchors = [self.ax.transData.transform(a.xy) for a, *_ in self.items]
        def clearance(i):
            return min((np.linalg.norm(anchors[i]-p) for j, p in enumerate(anchors)
                        if i != j), default=float("inf"))

        for i in sorted(range(len(self.items)), key=clearance):
            artist, direction, distance, radial, max_extra = self.items[i]
            norm = np.linalg.norm(direction)
            direction = direction / norm if norm > 1e-9 else np.array([0., 1.])
            initial = artist.get_window_extent(renderer)
            half_w, half_h = initial.width / (2*px), initial.height / (2*px)
            candidates = []
            rotations = [0] if radial else [0, 45, -45, 90, -90, 135, -135, 180]
            for extra in (v for v in (0, 4, 8, 12, 20, 28, 36) if v <= max_extra):
                for turn in rotations:
                    theta = math.radians(turn)
                    d = np.array([direction[0]*math.cos(theta)-direction[1]*math.sin(theta),
                                  direction[0]*math.sin(theta)+direction[1]*math.cos(theta)])
                    # Distance au bord réel du texte, pas seulement à son centre.
                    radius = distance + abs(d[0])*half_w + abs(d[1])*half_h + extra
                    off = d * radius
                    artist.set_position(tuple(off))
                    bb = artist.get_window_extent(renderer).padded(1.1 * px)
                    overlap = sum(max(0, min(bb.x1,b.x1)-max(bb.x0,b.x0)) *
                                  max(0, min(bb.y1,b.y1)-max(bb.y0,b.y0))
                                  for b in occupied + dots) / px**2
                    crossings = sum(path.intersects_bbox(bb, filled=False) for path in paths)
                    outside = (max(0, fig.bbox.x0-bb.x0) + max(0, bb.x1-fig.bbox.x1) +
                               max(0, fig.bbox.y0-bb.y0) + max(0, bb.y1-fig.bbox.y1)) / px
                    score = overlap * 1000 + crossings * 200 + outside * outside_penalty + extra + abs(turn)/45
                    candidates.append((score, tuple(off), bb))
            _, offset, bb = min(candidates, key=lambda v: v[0])
            artist.set_position(offset)
            occupied.append(bb)


def labels_for(ax) -> LabelLayout:
    if not hasattr(ax, "_figure_labels"):
        ax._figure_labels = LabelLayout(ax)
    return ax._figure_labels


def to_png(fig) -> bytes:
    """PNG fond blanc, rogné au contenu, dpi écrit dans les métadonnées."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=RENDER_DPI, facecolor="white",
                bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)
    return buf.getvalue()


def space_tick_labels(ax) -> None:
    """Espace les valeurs d'un repère dense en conservant ses traits/graduations.

    Les valeurs sont écrites à intervalles réguliers (2, 5, 10… graduations),
    sans réduire leur police. Les bornes et zéro sont prioritaires.
    """
    ax.figure.canvas.draw()
    renderer = ax.figure.canvas.get_renderer()
    for axis in (ax.xaxis, ax.yaxis):
        labels = axis.get_ticklabels()
        boxes = [t.get_window_extent(renderer).padded(2 * ax.figure.dpi / 72)
                 for t in labels]
        stride = 1
        for candidate in (1, 2, 5, 10, 20, 50, 100):
            selected = [i for i, t in enumerate(labels) if t.get_text() and i % candidate == 0]
            if all(not boxes[a].overlaps(boxes[b]) for a, b in zip(selected, selected[1:])):
                stride = candidate
                break
        priority = [i for i, t in enumerate(labels) if t.get_text() in ("0", "O")]
        priority += [i for i in (0, len(labels)-1) if 0 <= i < len(labels)]
        priority += [i for i in range(len(labels)) if i % stride == 0]
        visible = []
        for i in dict.fromkeys(priority):
            if labels[i].get_text() and not any(boxes[i].overlaps(boxes[j]) for j in visible):
                visible.append(i)
        for i, t in enumerate(labels):
            t.set_visible(i in visible)


# ------------------------------------------------------------ expressions
# Expressions de fonctions : SymPy, jamais `eval` d'une chaîne libre. Les
# identifiants sont sur liste blanche AVANT le parsing, la seule variable est x.
_ALLOWED_NAMES = {"x", "sqrt", "abs", "exp", "log", "ln", "sin", "cos", "tan",
                  "pi", "floor", "ceiling", "e"}
_EXPR_CHARS = re.compile(r"^[0-9x+\-*/^().,\s a-z]*$")


def compile_expr(expr, where: str):
    """Expression en x -> fonction numpy vectorisée."""
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor, implicit_multiplication_application, parse_expr,
        standard_transformations,
    )
    if not isinstance(expr, str) or not expr.strip() or len(expr) > 120:
        fail(where, f"expression attendue (≤120 caractères), reçu {expr!r}")
    src = expr.strip().replace(",", ".").replace("−", "-").replace("×", "*")
    if not _EXPR_CHARS.match(src):
        fail(where, f"caractère non autorisé dans {expr!r}")
    for name in re.findall(r"[a-z]+", src):
        if name not in _ALLOWED_NAMES:
            fail(where, f"nom inconnu {name!r} (autorisés : x, sqrt, abs, exp, "
                        "log, sin, cos, tan, pi)")
    x = sp.Symbol("x")
    local = {"x": x, "sqrt": sp.sqrt, "abs": sp.Abs, "exp": sp.exp, "log": sp.log,
             "ln": sp.log, "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
             "pi": sp.pi, "floor": sp.floor, "ceiling": sp.ceiling, "e": sp.E}
    try:
        parsed = parse_expr(src, local_dict=local, global_dict={"__builtins__": {}, **{
            k: getattr(sp, k) for k in ("Integer", "Float", "Rational", "Symbol")}},
            transformations=standard_transformations
            + (implicit_multiplication_application, convert_xor))
    except Exception as exc:  # noqa: BLE001 — message renvoyé à l'auteur
        fail(where, f"expression illisible {expr!r} ({exc.__class__.__name__})")
    if parsed.free_symbols - {x}:
        fail(where, f"seule la variable x est permise dans {expr!r}")
    fn = sp.lambdify(x, parsed, modules="numpy")

    def _f(xs):
        with np.errstate(all="ignore"):
            ys = np.asarray(fn(xs), dtype=float)
        if ys.shape == ():
            ys = np.full_like(xs, float(ys), dtype=float)
        return ys
    return _f


def monotone_curve(pts: list[tuple[float, float]], samples: int = 40):
    """Interpolation cubique MONOTONE (Fritsch–Carlson) : lisse sans jamais
    dépasser les points relevés — une température ne « rebondit » pas au-delà
    de ses mesures. Les x doivent être strictement croissants."""
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    n = len(xs)
    if n < 3:
        return xs, ys
    h = np.diff(xs)
    delta = np.diff(ys) / h
    m = np.zeros(n)
    m[0], m[-1] = delta[0], delta[-1]
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] <= 0:
            m[k] = 0.0
        else:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])
    out_x, out_y = [], []
    for k in range(n - 1):
        t = np.linspace(0, 1, samples, endpoint=(k == n - 2))
        h00 = 2 * t**3 - 3 * t**2 + 1
        h10 = t**3 - 2 * t**2 + t
        h01 = -2 * t**3 + 3 * t**2
        h11 = t**3 - t**2
        out_x.extend(xs[k] + t * h[k])
        out_y.extend(h00 * ys[k] + h10 * h[k] * m[k] + h01 * ys[k + 1] + h11 * h[k] * m[k + 1])
    return np.array(out_x), np.array(out_y)


def draw_axes(ax, spec: dict, where: str, *, equal: bool) -> dict:
    """Repère : graduations, grille, titres d'axes, flèches, origine.

    spec = {"x": {"min","max","step","minor","label","tick_labels"}, "y": {...},
            "grid": "major"|"both"|"none", "origin": "O"|"" (défaut ""),
            "arrows": bool, "break": bool (cassure d'axe), "equal": bool}
    Retourne les bornes {x:(min,max), y:(min,max)}."""
    spec = as_dict(spec, where)
    bounds = {}
    for axis in ("x", "y"):
        a = as_dict(spec.get(axis) or {}, f"{where}.{axis}")
        lo = num(a.get("min", -5), f"{where}.{axis}.min")
        hi = num(a.get("max", 5), f"{where}.{axis}.max")
        if hi <= lo:
            fail(f"{where}.{axis}", "max doit être > min")
        step = num(a.get("step", 1), f"{where}.{axis}.step", 1e-6, 1e4)
        if (hi - lo) / step > 60:
            fail(f"{where}.{axis}", f"trop de graduations ({(hi - lo) / step:.0f} > 60)")
        minor = a.get("minor")
        minor = num(minor, f"{where}.{axis}.minor", 1e-6, 1e4) if minor is not None else None
        if minor and (hi - lo) / minor > 240:
            fail(f"{where}.{axis}", "graduation secondaire trop fine")
        bounds[axis] = (lo, hi, step, minor, label(a.get("label"), f"{where}.{axis}.label",
                                                    max_len=40),
                        bool(a.get("tick_labels", True)))
    grid = spec.get("grid", "major")
    if grid not in ("major", "both", "none"):
        fail(f"{where}.grid", "« major », « both » ou « none »")

    (x0, x1, xs, xm, xl, xtl), (y0, y1, ys, ym, yl, ytl) = bounds["x"], bounds["y"]
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    if equal:
        ax.set_aspect("equal")

    def ticks(lo, hi, step):
        first = math.ceil(lo / step - 1e-9) * step
        return [round(first + k * step, 10) for k in range(int((hi - first) / step + 1e-9) + 1)]

    xt, yt = ticks(x0, x1, xs), ticks(y0, y1, ys)
    ax.set_xticks(xt)
    ax.set_yticks(yt)
    # le « 0 » commun aux deux axes ne s'écrit qu'une fois (sous l'axe des
    # abscisses), et pas du tout quand l'origine est nommée (« O »)
    origin = spec.get("origin", "")
    crosses = x0 <= 0 <= x1 and y0 <= 0 <= y1
    ax.set_xticklabels([fmt(v) if xtl and not (abs(v) < 1e-12 and (origin or (crosses and x0 < 0)))
                        else "" for v in xt], fontsize=FONT_SIZE - 1)
    ax.set_yticklabels([fmt(v) if ytl and not (abs(v) < 1e-12 and (crosses or origin))
                        else "" for v in yt], fontsize=FONT_SIZE - 1)
    if xm:
        ax.set_xticks(ticks(x0, x1, xm), minor=True)
    if ym:
        ax.set_yticks(ticks(y0, y1, ym), minor=True)
    if grid != "none":
        ax.grid(True, which="major", color="#B8C0CC", linewidth=0.5)
        if grid == "both":
            ax.grid(True, which="minor", color="#DDE2E8", linewidth=0.35)
    ax.set_axisbelow(True)
    # axes passant par l'origine quand elle est dans le cadre, sinon au bord
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_position(("data", 0 if x0 <= 0 <= x1 else x0))
    ax.spines["bottom"].set_position(("data", 0 if y0 <= 0 <= y1 else y0))
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.9)
        ax.spines[side].set_color(INK)
    ax.tick_params(which="both", length=2.5, width=0.6, colors=INK, pad=3)
    if spec.get("arrows", True):
        ax.plot(1, 0 if y0 <= 0 <= y1 else y0, ">", color=INK, markersize=3.5,
                transform=ax.get_yaxis_transform(), clip_on=False)
        ax.plot(0 if x0 <= 0 <= x1 else x0, 1, "^", color=INK, markersize=3.5,
                transform=ax.get_xaxis_transform(), clip_on=False)
    if xl:
        ax.set_xlabel(xl, fontsize=FONT_SIZE - 1, labelpad=9)
        ax.xaxis.set_label_coords(1, -0.11)
        ax.xaxis.label.set_horizontalalignment("right")
    if yl:
        ax.text(0, 1.06, yl, transform=ax.transAxes, ha="left", va="bottom",
                fontsize=FONT_SIZE - 1)
    if origin and crosses:
        labels_for(ax).add(label(origin, f"{where}.origin", max_len=4), (0, 0),
                           direction=(-1, -1), fontsize=FONT_SIZE - 1)
    if spec.get("break"):
        # cassure d'axe : double zigzag près de l'origine du cadre
        for axis in ("x", "y"):
            if axis == "x":
                bx = x0 + (x1 - x0) * 0.04
                by = 0 if y0 <= 0 <= y1 else y0
                ax.plot([bx - (x1 - x0) * 0.01, bx, bx + (x1 - x0) * 0.01],
                        [by - (y1 - y0) * 0.015, by + (y1 - y0) * 0.015, by - (y1 - y0) * 0.015],
                        color=INK, linewidth=0.8, clip_on=False, zorder=5)
            else:
                bx = 0 if x0 <= 0 <= x1 else x0
                by = y0 + (y1 - y0) * 0.04
                ax.plot([bx - (x1 - x0) * 0.015, bx + (x1 - x0) * 0.015, bx - (x1 - x0) * 0.015],
                        [by - (y1 - y0) * 0.01, by, by + (y1 - y0) * 0.01],
                        color=INK, linewidth=0.8, clip_on=False, zorder=5)
    return {"x": (x0, x1), "y": (y0, y1)}


LINE_STYLES = {"solid": "-", "dashed": "--", "dotted": ":"}


def line_style(value, where: str) -> str:
    value = value or "solid"
    if value not in LINE_STYLES:
        fail(where, "« solid », « dashed » ou « dotted »")
    return LINE_STYLES[value]


# palette sobre et lisible en niveaux de gris (photocopie)
SERIES_COLORS = ["#2F5D9E", "#D9822B", "#5A8F3E", "#8E4A9E", "#B83B3B", "#6B6B6B"]
SERIES_HATCH = ["", "///", "...", "xx", "\\\\", "--"]

"""Gabarits de figures : les dessins fréquents, décrits par leurs seuls paramètres.

Solides, patrons, fractions visuelles, droite graduée, balance : le modèle donne
des valeurs, jamais des traits, et le script contrôle chacune avant le rendu.
Spec : {"name": "cube", "width_mm": 40, ...paramètres du gabarit...}
"""
from __future__ import annotations

import matplotlib.patches as patches
import numpy as np

from . import figkit as fk
from .figkit import fail

_CAVALIER = (0.45, 0.3)     # fuyante de la perspective cavalière


def _canvas(w_mm: float, h_mm: float):
    fig = fk.new_figure(w_mm, h_mm)
    return fig, fig.add_axes([0, 0, 1, 1])


def _edges(ax, segments, *, hidden=()) -> None:
    for (x1, y1), (x2, y2) in segments:
        ax.plot([x1, x2], [y1, y2], color=fk.INK, lw=1.0, solid_capstyle="round")
    for (x1, y1), (x2, y2) in hidden:
        ax.plot([x1, x2], [y1, y2], color=fk.INK, lw=0.8, ls=(0, (3, 2)))


def _text(ax, x, y, value, **kw) -> None:
    if value:
        ax.text(x, y, fk.label(value, "étiquette"), fontsize=fk.FONT_SIZE, color=fk.INK,
                ha=kw.pop("ha", "center"), va=kw.pop("va", "center"), **kw)


def _box(ax, w, h, d, labels) -> tuple[float, float]:
    dx, dy = d * _CAVALIER[0], d * _CAVALIER[1]
    a, b, c, e = (0, 0), (w, 0), (w, h), (0, h)
    a2, b2, c2, e2 = (dx, dy), (w + dx, dy), (w + dx, h + dy), (dx, h + dy)
    _edges(ax, [(a, b), (b, c), (c, e), (e, a), (e, e2), (c, c2), (b, b2), (e2, c2), (c2, b2)],
           hidden=[(a, a2), (a2, e2), (a2, b2)])
    _text(ax, w / 2, -0.09 * h - 0.1, labels.get("width"))
    _text(ax, -0.08 * w - 0.1, h / 2, labels.get("height"), ha="right")
    _text(ax, w + dx / 2 + 0.15, dy / 2 - 0.05, labels.get("depth"), ha="left")
    return w + dx, h + dy


def _ellipse(ax, cx, cy, rx, ry, *, hidden_back=True) -> None:
    ax.add_patch(patches.Arc((cx, cy), 2 * rx, 2 * ry, theta1=180, theta2=360, color=fk.INK, lw=1.0))
    style = {"ls": (0, (3, 2)), "lw": 0.8} if hidden_back else {"lw": 1.0}
    ax.add_patch(patches.Arc((cx, cy), 2 * rx, 2 * ry, theta1=0, theta2=180, color=fk.INK, **style))


def _solid(name: str, spec: dict):
    labels = fk.as_dict(spec.get("labels") or {}, "labels")
    w_mm = fk.width_mm(spec)
    fig, ax = _canvas(w_mm, w_mm)
    if name == "cube":
        w, h = _box(ax, 2, 2, 2, labels)
    elif name == "pave":
        w, h = _box(ax, fk.num(spec.get("width", 3), "width", 0.5, 10),
                    fk.num(spec.get("height", 2), "height", 0.5, 10),
                    fk.num(spec.get("depth", 2), "depth", 0.5, 10), labels)
    elif name == "cylindre":
        rx, ry, h = 1.0, 0.3, 2.4
        ax.add_patch(patches.Ellipse((0, h), 2 * rx, 2 * ry, fill=False, color=fk.INK, lw=1.0))
        _ellipse(ax, 0, 0, rx, ry)
        _edges(ax, [((-rx, 0), (-rx, h)), ((rx, 0), (rx, h))])
        _text(ax, rx + 0.12, h / 2, labels.get("height"), ha="left")
        if labels.get("radius"):
            # Le rayon est tracé : une longueur écrite désigne un segment visible.
            _edges(ax, [((0, h), (rx, h))])
            ax.plot([0], [h], marker="o", color=fk.INK, ms=2)
            _text(ax, rx / 2, h + ry + 0.15, labels.get("radius"))
        w, h = rx, h + ry
    elif name == "cone":
        rx, ry, h = 1.0, 0.3, 2.4
        _ellipse(ax, 0, 0, rx, ry)
        _edges(ax, [((-rx, 0), (0, h)), ((rx, 0), (0, h))], hidden=[((0, 0), (0, h))])
        _text(ax, 0.1, h / 2, labels.get("height"), ha="left")
        if labels.get("radius"):
            _edges(ax, [((0, 0), (rx, 0))])
            ax.plot([0], [0], marker="o", color=fk.INK, ms=2)
            _text(ax, rx / 2, -ry - 0.15, labels.get("radius"))
        w, h = rx, h
    elif name == "pyramide":
        a, b, c, d = (0, 0), (2.2, 0), (3.0, 0.7), (0.8, 0.7)
        top = (1.5, 2.6)
        _edges(ax, [(a, b), (b, c), (a, top), (b, top), (c, top)],
               hidden=[(c, d), (d, a), (d, top), ((1.5, 0.35), top)])
        _text(ax, 1.6, 1.5, labels.get("height"), ha="left")
        w, h = 3.0, 2.6
    elif name == "boule":
        ax.add_patch(patches.Circle((0, 0), 1.2, fill=False, color=fk.INK, lw=1.0))
        _ellipse(ax, 0, 0, 1.2, 0.3)
        _edges(ax, [((0, 0), (1.2, 0))])
        _text(ax, 0.6, 0.15, labels.get("radius"))
        w, h = 1.2, 1.2
    else:  # pragma: no cover - filtré par render
        fail("name", f"gabarit inconnu {name!r}")
    _finish(ax, w, h)
    return fig


def _finish(ax, w, h) -> None:
    ax.set_aspect("equal")
    ax.axis("off")
    ax.margins(0.08)
    ax.autoscale_view()


def _patron_cube(spec: dict):
    w_mm = fk.width_mm(spec)
    fig, ax = _canvas(w_mm, w_mm * 0.8)
    default = [[1, 0], [0, 1], [1, 1], [2, 1], [3, 1], [1, 2]]
    cells = fk.as_list(spec.get("cells") or default, "cells", min_len=6, max_len=6)
    for i, cell in enumerate(cells):
        x, y = fk.xy(cell, f"cells[{i}]")
        ax.add_patch(patches.Rectangle((x, y), 1, 1, fill=False, edgecolor=fk.INK, lw=1.0))
    marks = fk.as_dict(spec.get("marks") or {}, "marks")
    for key, value in marks.items():
        index = fk.num(key, "marks", 0, 5)
        x, y = fk.xy(cells[int(index)], "cells")
        _text(ax, x + 0.5, y + 0.5, value)
    _finish(ax, 4, 3)
    return fig


def _fraction(spec: dict):
    total = int(fk.num(spec.get("total"), "total", 2, 24))
    filled = int(fk.num(spec.get("filled"), "filled", 0, total))
    shape = spec.get("shape", "bar")
    w_mm = fk.width_mm(spec)
    if shape == "disc":
        fig, ax = _canvas(w_mm, w_mm)
        for k in range(total):
            a1, a2 = 90 - 360 * k / total - 360 / total, 90 - 360 * k / total
            ax.add_patch(patches.Wedge((0, 0), 1, a1, a2, facecolor="#BDBDBD" if k < filled else "white",
                                       edgecolor=fk.INK, lw=0.9))
        _finish(ax, 1, 1)
        return fig
    if shape != "bar":
        fail("shape", "« bar » ou « disc » attendu")
    fig, ax = _canvas(w_mm, 14)
    for k in range(total):
        ax.add_patch(patches.Rectangle((k, 0), 1, 1, facecolor="#BDBDBD" if k < filled else "white",
                                       edgecolor=fk.INK, lw=0.9))
    _finish(ax, total, 1)
    return fig


def _number_line(spec: dict):
    lo = fk.num(spec.get("min"), "min")
    hi = fk.num(spec.get("max"), "max")
    step = fk.num(spec.get("step"), "step", 1e-3, 1e4)
    if hi <= lo or (hi - lo) / step > 40:
        fail("max", "intervalle vide, ou plus de 40 graduations")
    w_mm = fk.width_mm(spec)
    fig, ax = _canvas(w_mm, 16)
    end = hi + step * 0.6
    ax.plot([lo - step * 0.3, end], [0, 0], color=fk.INK, lw=1.0)
    ax.plot([end], [0], marker=">", color=fk.INK, ms=4)
    every = int(fk.num(spec.get("label_every", 1), "label_every", 1, 40))
    for k, x in enumerate(np.arange(lo, hi + step / 2, step)):
        ax.plot([x, x], [-0.12, 0.12], color=fk.INK, lw=0.9)
        if k % every == 0:
            _text(ax, x, -0.38, fk.fmt(float(x)))
    for i, point in enumerate(fk.as_list(spec.get("points"), "points", max_len=8)):
        point = fk.as_dict(point, f"points[{i}]")
        x = fk.num(point.get("x"), f"points[{i}].x", lo, hi)
        ax.plot([x], [0], marker="o", color=fk.INK, ms=3.5)
        _text(ax, x, 0.42, point.get("label"))
    ax.set_ylim(-0.7, 0.8)
    ax.axis("off")
    return fig


def _balance(spec: dict):
    w_mm = fk.width_mm(spec)
    fig, ax = _canvas(w_mm, w_mm * 0.55)
    state = spec.get("state", "equal")
    if state not in ("equal", "left", "right"):
        fail("state", "« equal », « left » ou « right » attendu")
    tilt = {"equal": 0.0, "left": -0.25, "right": 0.25}[state]
    _edges(ax, [((0, 0), (0, 1.5)), ((-0.6, 0), (0.6, 0)), ((-1.6, 1.5 + tilt), (1.6, 1.5 - tilt))])
    for x, y, value in ((-1.6, 1.5 + tilt, spec.get("left")),
                        (1.6, 1.5 - tilt, spec.get("right"))):
        _edges(ax, [((x, y), (x - 0.5, y - 0.5)), ((x, y), (x + 0.5, y - 0.5)),
                    ((x - 0.6, y - 0.5), (x + 0.6, y - 0.5))])
        _text(ax, x, y - 0.72, value)
    _finish(ax, 2.2, 1.8)
    return fig


_SOLIDS = ("cube", "pave", "cylindre", "cone", "pyramide", "boule")
NAMES = _SOLIDS + ("patron_cube", "fraction", "droite_graduee", "balance")


def render(params: dict, *, dry: bool = False) -> bytes:
    import matplotlib.pyplot as plt
    spec = fk.as_dict(params, "params")
    name = spec.get("name")
    if name not in NAMES:
        fail("name", f"gabarit inconnu {name!r} ; connus : {', '.join(NAMES)}")
    if name in _SOLIDS:
        fig = _solid(name, spec)
    else:
        fig = {"patron_cube": _patron_cube, "fraction": _fraction,
               "droite_graduee": _number_line, "balance": _balance}[name](spec)
    if dry:
        fig.canvas.draw()
        plt.close(fig)
        return b""
    return fk.to_png(fig)


def validate(params: dict) -> dict:
    render(params, dry=True)
    return params

"""Génération de figures géométriques paramétrées (sans tracé d'élève).

Figures supportées :
- rectangle(length, width, unit, show_diagonal)
- triangle(sides, right_angle_at)
- circle(radius, unit, show_diameter)
- angle(degrees, label)
- number_line(min, max, points)
- coordinate_plane(points, grid)

Chaque figure est rasterisée en PNG et mise en cache disque.
"""

import hashlib
from pathlib import Path
from io import BytesIO

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

from ..config import settings


FIGURE_TYPES = {"rectangle", "triangle", "circle", "angle", "number_line",
                "coordinate_plane", "image"}


def _fmt(v: float) -> str:
    """Nombre au format français : 7,5 ; 4 (jamais 4.0)."""
    return f"{v:g}".replace(".", ",")


def _validate_bounds(value, min_val: float = 0.1, max_val: float = 999.0, name: str = "value") -> float:
    """Valide qu'une valeur est dans les bornes raisonnables pour du collège.
    Tolère les nombres envoyés en chaîne ("4,5") par le LLM."""
    if isinstance(value, str):
        try:
            value = float(value.replace(",", "."))
        except ValueError:
            raise ValueError(f"{name} must be numeric, got {value!r}")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, got {type(value)}")
    if not (min_val <= value <= max_val):
        raise ValueError(f"{name} must be in [{min_val}, {max_val}], got {value}")
    return float(value)


def validate_figure(figure_json) -> dict | None:
    """Normalise et valide une figure produite par le LLM.

    Retourne le JSON normalisé si la figure est d'un type connu, avec des
    paramètres plausibles ET se rend sans erreur (rendu à blanc, mis en
    cache pour l'impression). None sinon — l'exercice reste utilisable sans figure.
    """
    if not isinstance(figure_json, dict):
        return None
    ftype = figure_json.get("type")
    params = figure_json.get("params")
    if ftype not in FIGURE_TYPES or not isinstance(params, (dict, type(None))):
        return None
    params = dict(params or {})
    if ftype == "image":
        # figure extraite d'un manuel (Sésamaths) : chemin de fichier direct,
        # pas de rendu procédural — confiance interne (jamais fourni par un LLM)
        if not isinstance(params.get("path"), str) or not params["path"]:
            return None
    # bornes de listes : jamais plus de 8 points annotés
    for key in ("points",):
        if key in params:
            if not isinstance(params[key], list):
                return None
            params[key] = params[key][:8]
    norm = {"type": ftype, "params": params}
    try:
        render_figure(norm)
    except Exception:
        return None
    return norm


def render_rectangle(length: float, width: float, unit: str = "cm", show_diagonal: bool = False) -> bytes:
    """Rectangle annoté (longueur, largeur, diagonale optionnelle)."""
    length = _validate_bounds(length, name="length")
    width = _validate_bounds(width, name="width")

    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
    ax.set_aspect('equal')

    # Rectangle
    rect = patches.Rectangle((0, 0), length, width, linewidth=2, edgecolor='black', facecolor='#EDF1F4', alpha=1.0)
    ax.add_patch(rect)

    # Annotations
    ax.text(length / 2, -0.35, f'{_fmt(length)} {unit}', ha='center', va='top', fontsize=13)
    ax.text(-0.35, width / 2, f'{_fmt(width)} {unit}', ha='right', va='center', fontsize=13)

    # Diagonale optionnelle
    if show_diagonal:
        diag = np.sqrt(length**2 + width**2)
        ax.plot([0, length], [0, width], 'k--', linewidth=1, label=f'diagonale')
        ax.legend()

    ax.set_xlim(-0.8, length + 0.5)
    ax.set_ylim(-0.8, width + 0.5)
    ax.axis('off')

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_triangle(base: float, height: float, unit: str = "cm", right_angle_at: str | None = None) -> bytes:
    """Triangle avec base et hauteur annotées, angle droit optionnel."""
    base = _validate_bounds(base, name="base")
    height = _validate_bounds(height, name="height")

    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(6, 5), dpi=150)
    ax.set_aspect('equal')

    # Triangle isocèle pour la symétrie
    vertices = np.array([[0, 0], [base, 0], [base / 2, height]])
    triangle = patches.Polygon(vertices, linewidth=2, edgecolor='black', facecolor='#EDF1F4', alpha=1.0)
    ax.add_patch(triangle)

    # Base et hauteur
    ax.text(base / 2, -0.35, f'{_fmt(base)} {unit}', ha='center', va='top', fontsize=13)
    ax.plot([base / 2, base / 2], [0, height], 'k--', linewidth=1)
    ax.text(base / 2 + 0.3, height / 2, f'h = {_fmt(height)} {unit}', fontsize=13)

    # Angle droit (si spécifié)
    if right_angle_at == 'base':
        square_size = 0.2 * height
        square = patches.Rectangle((0, 0), square_size, square_size, linewidth=1, edgecolor='black', facecolor='none')
        ax.add_patch(square)

    ax.set_xlim(-0.8, base + 0.8)
    ax.set_ylim(-0.8, height + 0.5)
    ax.axis('off')

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_circle(radius: float, unit: str = "cm", show_diameter: bool = False) -> bytes:
    """Cercle avec rayon (et diamètre optionnel)."""
    radius = _validate_bounds(radius, name="radius")

    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(5, 5), dpi=150)
    ax.set_aspect('equal')

    # Cercle
    circle = patches.Circle((0, 0), radius, linewidth=2, edgecolor='black', facecolor='#EDF1F4', alpha=1.0)
    ax.add_patch(circle)

    # Rayon
    ax.plot([0, radius], [0, 0], 'k-', linewidth=2)
    ax.text(radius / 2, -0.25, f'{_fmt(radius)} {unit}', ha='center', va='top', fontsize=13)

    # Diamètre optionnel
    if show_diameter:
        ax.plot([-radius, radius], [0, 0], 'k--', linewidth=1)
        ax.text(0, 0.3, f'd = {_fmt(2 * radius)} {unit}', ha='center', fontsize=12)

    ax.set_xlim(-radius - 0.5, radius + 0.5)
    ax.set_ylim(-radius - 0.5, radius + 0.5)
    ax.axis('off')

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_angle(degrees: float, label: str | None = None) -> bytes:
    """Arc d'angle annoté."""
    degrees = _validate_bounds(degrees, min_val=1, max_val=360, name="degrees")

    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(5, 5), dpi=150)
    ax.set_aspect('equal')

    # Rayon du dessin
    radius = 2.0

    # Deux rayons formant l'angle
    ax.plot([0, radius], [0, 0], 'k-', linewidth=2)
    angle_rad = np.radians(degrees)
    ax.plot([0, radius * np.cos(angle_rad)], [0, radius * np.sin(angle_rad)], 'k-', linewidth=2)

    # Arc
    arc_radius = 0.6
    angles = np.linspace(0, angle_rad, 50)
    arc_x = arc_radius * np.cos(angles)
    arc_y = arc_radius * np.sin(angles)
    ax.plot(arc_x, arc_y, 'k-', linewidth=1.5)

    # Label
    label_angle = angle_rad / 2
    label_radius = 1.0
    label_x = label_radius * np.cos(label_angle)
    label_y = label_radius * np.sin(label_angle)
    ax.text(label_x, label_y, label or f'{_fmt(degrees)}°', fontsize=14)

    ax.set_xlim(-0.5, radius + 0.5)
    ax.set_ylim(-0.5, radius + 0.5)
    ax.axis('off')

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_number_line(min_val: float, max_val: float, points: list[dict] | None = None) -> bytes:
    """Droite graduée avec points annotés optionnels."""
    min_val = _validate_bounds(min_val, min_val=-999, max_val=999, name="min")
    max_val = _validate_bounds(max_val, min_val=-999, max_val=999, name="max")
    if min_val >= max_val:
        raise ValueError(f"min ({min_val}) must be < max ({max_val})")

    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(10, 2), dpi=150)

    # Droite principale
    ax.plot([min_val, max_val], [0, 0], 'k-', linewidth=2)

    # Graduation
    step = (max_val - min_val) / 10
    for i in range(11):
        x = min_val + i * step
        ax.plot([x, x], [-0.1, 0.1], 'k-', linewidth=1)
        ax.text(x, -0.25, _fmt(round(x, 2)), ha='center', fontsize=11)

    # Points annotés
    if points:
        for pt in points:
            x = _validate_bounds(pt.get('value', 0), min_val=min_val-100, max_val=max_val+100, name="point value")
            label = pt.get('label', '')
            ax.plot([x, x], [0, 0.2], 'k-', linewidth=2)
            ax.text(x, 0.35, label or _fmt(round(x, 2)), ha='center', fontsize=12, weight='bold')

    ax.set_xlim(min_val - 0.5, max_val + 0.5)
    ax.set_ylim(-0.5, 0.6)
    ax.axis('off')

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_coordinate_plane(points: list[dict] | None = None, grid: bool = True) -> bytes:
    """Repère cartésien avec points annotés optionnels."""
    matplotlib.use('Agg')
    fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
    ax.set_aspect('equal')

    # Axes
    ax.axhline(0, color='k', linewidth=0.5)
    ax.axvline(0, color='k', linewidth=0.5)

    # Grille optionnelle
    if grid:
        ax.grid(True, alpha=0.3)

    # Points
    if points:
        for pt in points:
            x = _validate_bounds(pt.get('x', 0), min_val=-999, max_val=999, name="x")
            y = _validate_bounds(pt.get('y', 0), min_val=-999, max_val=999, name="y")
            label = pt.get('label', f'({x},{y})')
            ax.plot(x, y, 'ko', markersize=5)
            ax.text(x + 0.25, y + 0.25, label, fontsize=12, weight='bold')

    ax.set_xlim(-10, 10)
    ax.set_ylim(-10, 10)
    ax.set_xlabel('x', fontsize=11)
    ax.set_ylabel('y', fontsize=11)

    buffer = BytesIO()
    fig.savefig(buffer, format='png', transparent=True, bbox_inches='tight', dpi=150)
    plt.close(fig)
    return buffer.getvalue()


def render_figure(figure_json: dict) -> bytes:
    """Rend une figure paramétrée en PNG. Cache disque automatique.

    Args:
        figure_json: {"type": "rectangle"|"triangle"|..., "params": {...}}

    Returns:
        PNG bytes

    Raises:
        ValueError: paramètres invalides
    """
    if not figure_json or 'type' not in figure_json:
        raise ValueError("figure_json must have 'type' key")

    fig_type = figure_json['type']
    params = figure_json.get('params', {})

    if fig_type == "image":
        # figure extraite d'un manuel (Sésamaths) : pas de rendu procédural,
        # pas de cache disque (le fichier référencé est déjà le cache)
        path = params.get("path")
        if not path or not Path(path).exists():
            raise ValueError(f"Image figure introuvable : {path!r}")
        return Path(path).read_bytes()

    # Clé de cache
    import json as _json
    cache_key = hashlib.sha256(
        _json.dumps(figure_json, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_dir = Path(settings.data_dir) / "figcache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key}.png"

    if cache_file.exists():
        return cache_file.read_bytes()

    # Dispatch par type
    try:
        if fig_type == 'rectangle':
            png = render_rectangle(
                length=params.get('length', 5),
                width=params.get('width', 3),
                unit=params.get('unit', 'cm'),
                show_diagonal=params.get('show_diagonal', False)
            )
        elif fig_type == 'triangle':
            png = render_triangle(
                base=params.get('base', 4),
                height=params.get('height', 3),
                unit=params.get('unit', 'cm'),
                right_angle_at=params.get('right_angle_at', None)
            )
        elif fig_type == 'circle':
            png = render_circle(
                radius=params.get('radius', 2),
                unit=params.get('unit', 'cm'),
                show_diameter=params.get('show_diameter', False)
            )
        elif fig_type == 'angle':
            png = render_angle(
                degrees=params.get('degrees', 45),
                label=params.get('label', None)
            )
        elif fig_type == 'number_line':
            png = render_number_line(
                min_val=params.get('min', 0),
                max_val=params.get('max', 10),
                points=params.get('points', None)
            )
        elif fig_type == 'coordinate_plane':
            png = render_coordinate_plane(
                points=params.get('points', None),
                grid=params.get('grid', True)
            )
        else:
            raise ValueError(f"Unknown figure type: {fig_type}")
    except ValueError as e:
        raise ValueError(f"Figure rendering failed: {e}")

    cache_file.write_bytes(png)
    return png


__all__ = ['render_figure', 'validate_figure', 'render_rectangle', 'render_triangle',
           'render_circle', 'render_angle', 'render_number_line', 'render_coordinate_plane']

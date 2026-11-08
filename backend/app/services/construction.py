"""Géométrie à tracer (format forge `geo_construction`) : contrat, impression,
lecture CV notée au-dessus d'un seuil de confiance, banc synthétique.

Dans l'application, une carte à tracer reste un `manual_drawing` : la file de
correction, le barème et la modale du professeur la connaissent déjà. Ce qui
s'ajoute vit dans `grading.construction`.

Deux modes, déduits de `given` :

- **ancré** : une figure de départ imprimée en noir (`given`), sur quadrillage
  saumon ou dans une case saumon. Le tracé attendu a une place exacte.

      {"cols": 16, "rows": 9,
       "points": {"A": [1, 1], "B": [6, 2], "C": [2, 7], "P": [8, 0], "Q": [8, 9],
                  "A'": [15, 1], "B'": [10, 2], "C'": [14, 7]},
       "given": {"points": ["A", "B", "C"], "polygons": [["A", "B", "C"]],
                 "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
       "expected": {"polygons": [["A'", "B'", "C'"]]}}

- **libre** : `given` vide, case saumon à coins arrondis, sans quadrillage.
  L'élève place sa figure où il veut, comme il veut : la lecture cherche le
  déplacement (rotation, retournement, translation) qui pose la figure attendue
  sur son tracé, puis mesure longueurs, angles et rayons.

      {"cols": 16, "rows": 8, "points": {"O": [2, 2], "X": [9, 2], "Y": [6.5, 7.36]},
       "given": {}, "expected": {"rays": [["O", "X"], ["O", "Y"]]}}

Coordonnées en carreaux de 5 mm, origine en bas à gauche de la zone.
`alternatives` : autres tracés justes. `errors` : tracés d'erreurs typiques,
reconnus pour proposer la micro-correction.

La lecture rend une proposition (part du tracé retrouvée) et une confiance
continue. Au-dessus du seuil réglé (calibré sur copies réelles), la note est
appliquée ; en dessous, le professeur corrige.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

import numpy as np

from ..config import settings

PT_PER_MM = 72.0 / 25.4
STEP_MM = 5.0
UNIT_PT = STEP_MM * PT_PER_MM
COLS_RANGE = (4, 16)          # 16 carreaux = 8 cm : tient dans une colonne
ROWS_RANGE = (3, 14)
TOLERANCE_MM = (1.0, 3.0)
DEFAULT_TOLERANCE_MM = 1.5
ZONE_PAD_MM = 2.0             # entre le bord de la zone et l'aire de tracé
BOX_RADIUS_MM = 2.0           # case saumon : congés, comme une case à remplir
BOX_WIDTH_PT = 0.9
ELEMENT_KEYS = ("points", "segments", "polygons", "lines", "rays", "circles")
MIN_SEGMENT_UNITS = 1.5       # un trait plus court ne se mesure pas
MIN_RADIUS_UNITS = 1.0
LABEL_FONT, LABEL_SIZE = "Helvetica-Oblique", 8.5
LABEL_GAP_MM = 1.4
CROSS_MM = 1.0                # demi-branche de la croix d'un point donné
GIVEN_WIDTH_PT = 0.8
GRID_WIDTH_PT = 0.4
_POS = {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0),
        "ne": (0.7, 0.7), "nw": (-0.7, 0.7), "se": (0.7, -0.7), "sw": (-0.7, -0.7)}

# Lecture, à 200 dpi (1 mm ≈ 7,9 px)
DPI = 200
INK_ON = 0.45                 # force d'encre retenue (cf. worker_cv._ink_strength)
SPECK_PX = 8                  # tache isolée plus petite : poussière
PRINT_HALO_MM = 0.6           # bande autour d'un trait imprimé : l'élève y est caché
REG_SEARCH_MM = 1.5           # recalage local sur la figure imprimée
REG_MIN_PX = 120              # en dessous, figure imprimée trop maigre pour recaler
REG_FOUND = 0.5               # part du trait imprimé retrouvée au meilleur décalage
BLANK_PX = 60                 # encre élève totale sous ce seuil : copie blanche
COVER_PRESENT = 0.85
COVER_ABSENT = 0.35
GAP_MAX_MM = 3.0
VISIBLE_MIN = 0.4             # part visible d'un élément (hors figure imprimée)
LINE_RUN_MM = 20.0            # une droite tracée sur au moins ça (ou 60 % du visible)
POINT_INK_PX = 12             # croix d'un point à placer
POINT_EMPTY_PX = 4
VERTEX_ZONE_MM = 7.0          # encre près d'un sommet : nom écrit, arcs de compas
EXTRA_FREE = 0.15             # encre hors du tracé : sans effet jusque-là ...
EXTRA_SLOPE = 2.5             # ... puis la confiance baisse vite
GRID_RESIDUE_MAX = 0.2        # quadrillage resté visible (copieur qui grise le saumon)
LENGTH_OK_MM = 2.0            # tracé libre : écart de longueur toléré
LENGTH_BAD_MM = 3.5           # ... au-delà, la longueur est fausse
CAP_DOUBT = 0.4               # plafond de confiance d'une lecture douteuse
SELFTEST_CONFIDENCE = 0.8     # une carte doit être lue nettement en conditions idéales


class SpecError(ValueError):
    pass


# ------------------------------------------------------------------ contrat
def _xy(value, where: str) -> tuple[float, float]:
    if isinstance(value, dict):
        value = value.get("xy")
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise SpecError(f"{where} : [x, y] attendu")
    return float(value[0]), float(value[1])


def _pair(item, keys: tuple[str, ...], where: str) -> tuple[str, str]:
    if isinstance(item, dict):
        for key in keys:
            if key in item:
                item = item[key] if isinstance(item[key], (list, tuple)) else [item.get("from"), item.get("to")]
                break
        else:
            item = [item.get("from"), item.get("to")]
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise SpecError(f"{where} : deux noms de points attendus")
    return str(item[0]), str(item[1])


@dataclass
class Prim:
    """Élément géométrique en carreaux : segment (a, b), cercle (centre a, rayon)
    ou point. Une droite ou une demi-droite est un segment `infinite` : rognée à
    la zone en mode ancré, prolongée loin en mode libre (`ray` : issue de a)."""
    kind: str                     # seg | circle | point
    a: tuple[float, float] = (0.0, 0.0)
    b: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    name: str = ""
    label: str = ""
    infinite: bool = False        # droite ou demi-droite : longueur tracée libre
    ray: bool = False
    measured: bool = False        # tracé libre : la longueur du segment compte


def _clip(p, q, cols, rows, *, ray: bool) -> tuple[tuple, tuple] | None:
    """Droite (p, q) — ou demi-droite issue de p — rognée au cadre [0, cols]×[0, rows]."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = (0.0 if ray else -1e9), 1e9
    for pv, qv in ((-dx, x0), (dx, cols - x0), (-dy, y0), (dy, rows - y0)):
        if abs(pv) < 1e-12:
            if qv < 0:
                return None
            continue
        t = qv / pv
        if pv < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 >= t1:
        return None
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def points_of(spec: dict) -> dict[str, tuple[float, float]]:
    raw = spec.get("points") or {}
    if not isinstance(raw, dict) or not raw:
        raise SpecError("points : {nom: [x, y]} attendu")
    out = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 4:
            raise SpecError(f"points.{name} : nom de 1 à 4 caractères")
        out[name] = _xy(value, f"points.{name}")
    return out


def is_free(spec: dict) -> bool:
    """Tracé libre : aucune figure de départ imprimée."""
    given = spec.get("given") or {}
    return not any(given.get(key) for key in ELEMENT_KEYS) if isinstance(given, dict) else False


def primitives(block: dict | None, spec: dict, where: str = "") -> list[Prim]:
    """Éléments d'un bloc (`given`, `expected`, une alternative, une trace d'erreur)."""
    block = block or {}
    if not isinstance(block, dict):
        raise SpecError(f"{where} : objet attendu")
    unknown = set(block) - set(ELEMENT_KEYS)
    if unknown:
        raise SpecError(f"{where} : clé(s) inconnue(s) {sorted(unknown)} ; {', '.join(ELEMENT_KEYS)}")
    pts = points_of(spec)
    cols, rows = spec["cols"], spec["rows"]
    free = is_free(spec)
    far = 2.0 * math.hypot(cols, rows)

    def at(name, w):
        if name not in pts:
            raise SpecError(f"{w} : point {name!r} absent de points")
        return pts[name]

    out: list[Prim] = []
    for name in block.get("points") or []:
        out.append(Prim("point", at(str(name), f"{where}.points"), name=str(name)))
    for i, item in enumerate(block.get("segments") or []):
        a, b = _pair(item, ("seg",), f"{where}.segments[{i}]")
        label = item.get("label", "") if isinstance(item, dict) else ""
        out.append(Prim("seg", at(a, f"{where}.segments"), at(b, f"{where}.segments"),
                        name=f"[{a}{b}]", label=str(label or ""), measured=free))
    for i, item in enumerate(block.get("polygons") or []):
        names = item.get("vertices") if isinstance(item, dict) else item
        if not isinstance(names, (list, tuple)) or len(names) < 3:
            raise SpecError(f"{where}.polygons[{i}] : au moins 3 sommets")
        for k, a in enumerate(names):
            b = names[(k + 1) % len(names)]
            out.append(Prim("seg", at(str(a), f"{where}.polygons"), at(str(b), f"{where}.polygons"),
                            name=f"[{a}{b}]", measured=free))
    for key, ray in (("lines", False), ("rays", True)):
        for i, item in enumerate(block.get(key) or []):
            a, b = _pair(item, ("through", "from"), f"{where}.{key}[{i}]")
            label = item.get("label", "") if isinstance(item, dict) else ""
            pa, pb = at(a, f"{where}.{key}"), at(b, f"{where}.{key}")
            if pa == pb:
                raise SpecError(f"{where}.{key}[{i}] : deux points distincts")
            name = f"[{a}{b})" if ray else f"({a}{b})"
            if free:
                d = np.subtract(pb, pa) / math.dist(pa, pb)
                start = pa if ray else tuple(np.subtract(pa, d * far))
                end = tuple(np.add(pa, d * far))
                out.append(Prim("seg", tuple(map(float, start)), tuple(map(float, end)), name=name,
                                label=str(label or ""), infinite=True, ray=ray))
                continue
            clipped = _clip(pa, pb, cols, rows, ray=ray)
            if clipped is None:
                raise SpecError(f"{where}.{key}[{i}] : hors de la zone")
            out.append(Prim("seg", clipped[0], clipped[1], name=name, label=str(label or ""),
                            infinite=True, ray=ray))
    for i, item in enumerate(block.get("circles") or []):
        if not isinstance(item, dict) or "center" not in item:
            raise SpecError(f"{where}.circles[{i}] : {{center, radius | through}} attendu")
        center = at(str(item["center"]), f"{where}.circles")
        if "through" in item:
            radius = math.dist(center, at(str(item["through"]), f"{where}.circles"))
        else:
            radius = item.get("radius")
            if not isinstance(radius, (int, float)) or isinstance(radius, bool):
                raise SpecError(f"{where}.circles[{i}] : radius (en carreaux) ou through")
        out.append(Prim("circle", center, radius=float(radius), name=f"cercle de centre {item['center']}"))
    return out


def normalize(spec) -> dict:
    """Contrat propre, ou SpecError avec un message court qui nomme la clé."""
    if not isinstance(spec, dict):
        raise SpecError("construction : objet attendu")
    out = dict(spec)
    for key, (low, high) in (("cols", COLS_RANGE), ("rows", ROWS_RANGE)):
        value = spec.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise SpecError(f"{key} : entier de {low} à {high} (carreaux de {STEP_MM:g} mm)")
    if not isinstance(spec.get("expected"), dict):
        raise SpecError("expected : objet attendu")
    if spec.get("given") is not None and not isinstance(spec.get("given"), dict):
        raise SpecError("given : objet attendu ({} pour un tracé libre)")
    out["given"] = spec.get("given") or {}
    out["grid"] = bool(spec.get("grid", not is_free(out)))
    tol = spec.get("tolerance_mm", DEFAULT_TOLERANCE_MM)
    if not isinstance(tol, (int, float)) or not TOLERANCE_MM[0] <= tol <= TOLERANCE_MM[1]:
        raise SpecError(f"tolerance_mm : entre {TOLERANCE_MM[0]:g} et {TOLERANCE_MM[1]:g}")
    out["tolerance_mm"] = float(tol)
    points_of(out)
    alternatives = spec.get("alternatives") or []
    errors = spec.get("errors") or []
    if not isinstance(alternatives, list) or not isinstance(errors, list):
        raise SpecError("alternatives et errors : listes")
    out["alternatives"], out["errors"] = alternatives, errors
    return out


def problems(spec) -> list[str]:
    """Ce qui empêche d'imprimer ou de lire cette construction. Vide si tout va bien."""
    try:
        spec = normalize(spec)
        given = primitives(spec["given"], spec, "given")
        expected = primitives(spec["expected"], spec, "expected")
        alternatives = [primitives(b, spec, f"alternatives[{i}]")
                        for i, b in enumerate(spec["alternatives"])]
        traces = []
        for i, err in enumerate(spec["errors"]):
            if not isinstance(err, dict) or not err.get("id"):
                raise SpecError(f"errors[{i}] : {{id, trace}} attendu")
            traces.append((str(err["id"]), primitives(err.get("trace"), spec, f"errors[{i}].trace")))
    except SpecError as exc:
        return [f"construction : {exc}"]
    out = []
    cols, rows = spec["cols"], spec["rows"]
    pts = points_of(spec)
    free = is_free(spec)
    printed = set(spec["given"].get("points") or [])
    if not expected:
        out.append("construction : expected vide")
    if free:
        if spec["grid"]:
            out.append("construction : un tracé libre se fait sans quadrillage (grid: false)")
        if not any(p.kind in ("seg", "circle") for p in expected):
            out.append("construction : un tracé libre a au moins un segment, une droite ou un cercle")
        for label, block in [("expected", expected)] + [(f"alternatives[{i}]", b)
                                                        for i, b in enumerate(alternatives)]:
            (x0, y0), (x1, y1) = _bbox(block)
            if x0 < 0.5 or y0 < 0.5 or x1 > cols - 0.5 or y1 > rows - 0.5:
                out.append(f"construction : {label} déborde de la case {cols}×{rows} "
                           f"(laisse un demi-carreau de marge)")
    for name in printed:
        x, y = pts[str(name)]
        if not (0.5 <= x <= cols - 0.5 and 0.5 <= y <= rows - 0.5):
            out.append(f"construction : point imprimé {name} trop près du bord (nom illisible)")
    if not free:
        for name, (x, y) in pts.items():
            if not (-0.01 <= x <= cols + 0.01 and -0.01 <= y <= rows + 0.01) and name not in _line_only(spec):
                out.append(f"construction : point {name} hors de la zone {cols}×{rows}")
    for prim in expected:
        if prim.kind == "point" and prim.name in printed:
            out.append(f"construction : le point à placer {prim.name} est imprimé (réponse donnée)")
    for label, block in [("expected", expected)] + [(f"alternatives[{i}]", b) for i, b in enumerate(alternatives)]:
        for prim in block:
            out += [f"construction : {label} {m}" for m in _prim_problems(prim, cols, rows, free)]
            share = _visible_share(prim, given)
            if share < VISIBLE_MIN:
                out.append(f"construction : {label} {prim.name} caché sous la figure imprimée "
                           f"({share:.0%} visible)")
    for err_id, trace in traces:
        if not trace:
            out.append(f"construction : errors {err_id} sans trace")
            continue
        overlap = (_congruent_overlap(trace, expected, spec["tolerance_mm"]) if free else
                   min(_overlap(p, expected, spec["tolerance_mm"]) for p in trace if p.kind != "point")
                   if any(p.kind != "point" for p in trace) else 0.0)
        if overlap > 0.8:
            out.append(f"construction : la trace de l'erreur {err_id} se confond avec le tracé attendu")
    return out


def _bbox(prims: list[Prim]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Encombrement d'une figure, sans le prolongement des droites."""
    xs, ys = [], []
    for p in prims:
        if p.kind == "circle":
            xs += [p.a[0] - p.radius, p.a[0] + p.radius]
            ys += [p.a[1] - p.radius, p.a[1] + p.radius]
        elif p.infinite:
            xs.append(p.a[0])
            ys.append(p.a[1])
        else:
            xs += [p.a[0], p.b[0]] if p.kind == "seg" else [p.a[0]]
            ys += [p.a[1], p.b[1]] if p.kind == "seg" else [p.a[1]]
    if not xs:
        return (0.0, 0.0), (0.0, 0.0)
    return (min(xs), min(ys)), (max(xs), max(ys))


def _line_only(spec: dict) -> set[str]:
    """Points qui ne servent qu'à porter une droite : ils peuvent sortir de la zone."""
    names = set()
    for block in [spec.get("given"), spec.get("expected"), *(spec.get("alternatives") or []),
                  *[(e or {}).get("trace") for e in spec.get("errors") or []]]:
        for key in ("lines", "rays"):
            for item in (block or {}).get(key) or []:
                try:
                    names.update(_pair(item, ("through", "from"), key))
                except SpecError:
                    pass
    return names


def _prim_problems(prim: Prim, cols: int, rows: int, free: bool) -> list[str]:
    if prim.kind == "seg" and not prim.infinite and math.dist(prim.a, prim.b) < MIN_SEGMENT_UNITS:
        return [f"{prim.name} trop court pour être mesuré ({math.dist(prim.a, prim.b):.1f} carreau)"]
    if prim.kind == "circle":
        if prim.radius < MIN_RADIUS_UNITS:
            return [f"{prim.name} : rayon trop petit"]
        if not free:
            samples = _samples_units(prim, 0.25)
            inside = np.mean([(0 <= x <= cols and 0 <= y <= rows) for x, y in samples])
            if inside < 0.7:
                return [f"{prim.name} : sort de la zone ({inside:.0%} dedans)"]
    return []


def _samples_units(prim: Prim, step: float) -> np.ndarray:
    """Points d'un élément, tous les `step` carreaux, dans l'ordre (de a vers b)."""
    if prim.kind == "point":
        return np.array([prim.a], float)
    if prim.kind == "seg":
        n = max(2, int(math.dist(prim.a, prim.b) / step) + 1)
        t = np.linspace(0, 1, n)[:, None]
        return np.asarray(prim.a) + t * (np.asarray(prim.b) - np.asarray(prim.a))
    n = max(8, int(2 * math.pi * prim.radius / step))
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.stack([prim.a[0] + prim.radius * np.cos(t), prim.a[1] + prim.radius * np.sin(t)], 1)


def _distance_to(points: np.ndarray, prim: Prim) -> np.ndarray:
    points = np.atleast_2d(points)
    if prim.kind == "point":
        return np.linalg.norm(points - np.asarray(prim.a), axis=1)
    if prim.kind == "circle":
        return np.abs(np.linalg.norm(points - np.asarray(prim.a), axis=1) - prim.radius)
    a, b = np.asarray(prim.a), np.asarray(prim.b)
    d = b - a
    length2 = float(d @ d)
    t = np.zeros(len(points)) if length2 == 0 else np.clip(((points - a) @ d) / length2, 0, 1)
    return np.linalg.norm(points - (a + t[:, None] * d), axis=1)


def _visible_share(prim: Prim, given: list[Prim]) -> float:
    """Part d'un élément attendu qui ne tombe pas sous un trait imprimé."""
    lines = [g for g in given if g.kind != "point"]
    if prim.kind == "point" or not lines:
        return 1.0
    halo = (PRINT_HALO_MM + 0.5) / STEP_MM
    samples = _samples_units(prim, 0.1)
    hidden = np.zeros(len(samples), bool)
    for g in lines:
        hidden |= _distance_to(samples, g) <= halo
    return 1.0 - hidden.mean()


def _overlap(prim: Prim, block: list[Prim], tol_mm: float) -> float:
    """Part de `prim` à moins de la tolérance d'un élément de `block`."""
    tol = (tol_mm + 1.0) / STEP_MM
    samples = _samples_units(prim, 0.1)
    near = np.zeros(len(samples), bool)
    for q in block:
        near |= _distance_to(samples, q) <= tol
    return float(near.mean())


def _congruent_overlap(trace: list[Prim], expected: list[Prim], tol_mm: float) -> float:
    """Tracé libre : une erreur qui, déplacée, se pose sur le tracé juste ne se
    distingue pas de lui. Meilleur recouvrement mutuel sur tous les déplacements."""
    best = 0.0
    for placement in _hypotheses_between(trace, expected):
        moved = [placement.prim(p) for p in trace]
        there = min((_overlap(p, expected, tol_mm) for p in moved if p.kind != "point"), default=0.0)
        back = min((_overlap(p, moved, tol_mm) for p in expected if p.kind != "point"), default=0.0)
        best = max(best, min(there, back))
    return best


# ------------------------------------------------------------------ déplacement (tracé libre)
@dataclass
class Placement:
    """Déplacement de la figure modèle, en carreaux : retournement (y -> -y),
    rotation, puis translation."""
    theta: float = 0.0
    mirror: bool = False
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, pts) -> np.ndarray:
        pts = np.array(pts, float, ndmin=2)
        if self.mirror:
            pts[:, 1] = -pts[:, 1]
        c, s = math.cos(self.theta), math.sin(self.theta)
        return np.stack([c * pts[:, 0] - s * pts[:, 1] + self.tx,
                         s * pts[:, 0] + c * pts[:, 1] + self.ty], 1)

    def point(self, p) -> tuple[float, float]:
        q = self.apply(p)[0]
        return float(q[0]), float(q[1])

    def prim(self, prim: Prim) -> Prim:
        return replace(prim, a=self.point(prim.a), b=self.point(prim.b))

    def as_dict(self) -> dict:
        return {"theta_deg": round(math.degrees(self.theta), 2), "mirror": self.mirror,
                "tx": round(self.tx, 3), "ty": round(self.ty, 3)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Placement":
        d = d or {}
        return cls(math.radians(float(d.get("theta_deg", 0))), bool(d.get("mirror")),
                   float(d.get("tx", 0)), float(d.get("ty", 0)))


def _anchor_pairs(prims: list[Prim]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Segments porteurs d'une figure, pour poser un déplacement dessus."""
    out = []
    for p in prims:
        if p.kind != "seg":
            continue
        a, b = np.asarray(p.a), np.asarray(p.b)
        if p.infinite:                    # une droite se pose par son point et sa direction
            d = (b - a) / (np.linalg.norm(b - a) or 1.0)
            b = a + d * 4.0
        out.append((a, b))
    return out


def _placement_onto(a, b, p, q, mirror: bool) -> Placement:
    """Déplacement qui pose le segment modèle (a, b) sur (p, q)."""
    a, b = np.array(a, float), np.array(b, float)
    if mirror:
        a[1], b[1] = -a[1], -b[1]
    theta = math.atan2(q[1] - p[1], q[0] - p[0]) - math.atan2(b[1] - a[1], b[0] - a[0])
    c, s = math.cos(theta), math.sin(theta)
    return Placement(theta, mirror, p[0] - (c * a[0] - s * a[1]), p[1] - (s * a[0] + c * a[1]))


def _hypotheses_between(source: list[Prim], target: list[Prim]) -> list[Placement]:
    out = [Placement()]
    for a, b in _anchor_pairs(source):
        for p, q in _anchor_pairs(target):
            for mirror in (False, True):
                out.append(_placement_onto(a, b, p, q, mirror))
                out.append(_placement_onto(a, b, q, p, mirror))
    return out


# ------------------------------------------------------------------ impression
def zone_height(spec: dict) -> float:
    """Hauteur de la zone de réponse, en points."""
    return spec["rows"] * UNIT_PT + 2 * ZONE_PAD_MM * PT_PER_MM + 2 * PT_PER_MM


def _label_box(text: str, anchor: tuple[float, float], direction: tuple[float, float]):
    """Nom posé à côté de son point, dans la direction donnée. (x, y, w, h) en pt."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    w = stringWidth(text, LABEL_FONT, LABEL_SIZE)
    h = LABEL_SIZE * 0.72
    gap = LABEL_GAP_MM * PT_PER_MM
    dx, dy = direction
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    cx = anchor[0] + dx * (gap + w / 2)
    cy = anchor[1] + dy * (gap + h / 2)
    return cx - w / 2, cy - h / 2, w, h


def draw_zone(c, x: float, y: float, w: float, h: float, spec: dict, dropout) -> dict:
    """Quadrillage saumon, ou case saumon à coins arrondis ; figure de départ en
    noir. Retourne la méta de lecture."""
    from reportlab.lib.colors import black
    spec = normalize(spec)
    cols, rows = spec["cols"], spec["rows"]
    u = UNIT_PT
    pad = ZONE_PAD_MM * PT_PER_MM
    ox = x + (w - cols * u) / 2
    oy = y + 1 * PT_PER_MM + pad
    c.saveState()
    c.setStrokeColor(dropout)
    if spec["grid"]:
        c.setLineWidth(GRID_WIDTH_PT)
        for i in range(cols + 1):
            c.line(ox + i * u, oy, ox + i * u, oy + rows * u)
        for j in range(rows + 1):
            c.line(ox, oy + j * u, ox + cols * u, oy + j * u)
    else:
        # case de réponse : même trait saumon et mêmes congés qu'une case à remplir
        c.setLineWidth(BOX_WIDTH_PT)
        c.roundRect(ox - pad, oy - pad, cols * u + 2 * pad, rows * u + 2 * pad,
                    BOX_RADIUS_MM * PT_PER_MM, stroke=1, fill=0)

    def to_pt(p):
        return ox + p[0] * u, oy + p[1] * u

    given = primitives(spec["given"], spec, "given")
    pts = points_of(spec)
    printed = [str(n) for n in spec["given"].get("points") or []]
    center = (np.mean([pts[n][0] for n in printed]) if printed else cols / 2,
              np.mean([pts[n][1] for n in printed]) if printed else rows / 2)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(GIVEN_WIDTH_PT)
    c.setLineCap(1)
    labels: list[list[float]] = []
    for prim in given:
        if prim.kind == "seg":
            (ax, ay), (bx, by) = to_pt(prim.a), to_pt(prim.b)
            c.line(ax, ay, bx, by)
            if prim.label:
                # nom de droite près de son extrémité haute, décalé à droite du trait
                end = (bx, by) if by >= ay else (ax, ay)
                other = (ax, ay) if end == (bx, by) else (bx, by)
                vx, vy = end[0] - other[0], end[1] - other[1]
                norm = math.hypot(vx, vy) or 1.0
                anchor = (end[0] - vx / norm * 3 * PT_PER_MM, end[1] - vy / norm * 3 * PT_PER_MM)
                labels.append(_draw_label(c, prim.label, _label_box(prim.label, anchor, (vy / norm, -vx / norm))))
        elif prim.kind == "circle":
            cx, cy = to_pt(prim.a)
            c.circle(cx, cy, prim.radius * u, stroke=1, fill=0)
    arm = CROSS_MM * PT_PER_MM
    for name in printed:
        px, py = to_pt(pts[name])
        c.line(px - arm, py - arm, px + arm, py + arm)
        c.line(px - arm, py + arm, px + arm, py - arm)
        raw = (spec.get("points") or {}).get(name)
        pos = raw.get("pos") if isinstance(raw, dict) else None
        direction = _POS.get(pos) if pos else (pts[name][0] - center[0], pts[name][1] - center[1])
        if not direction or math.hypot(*direction) < 1e-6:
            direction = _POS["ne"]
        labels.append(_draw_label(c, name, _label_box(name, (px, py), direction)))
    c.restoreState()
    return {"construction": {"origin_pt": [ox, oy], "unit_pt": u, "label_boxes": labels,
                             "spec": spec}}


def _draw_label(c, text: str, box) -> list[float]:
    bx, by, bw, bh = box
    c.setFont(LABEL_FONT, LABEL_SIZE)
    c.drawString(bx, by, text)
    return [round(bx, 2), round(by - LABEL_SIZE * 0.2, 2), round(bw, 2), round(bh + LABEL_SIZE * 0.4, 2)]


def expected_polylines_pt(meta: dict, placement: dict | None = None) -> list[list[tuple[float, float]]]:
    """Tracé attendu en points PDF (aperçu de relecture, surimpression de la modale).
    Tracé libre : `placement` (lu sur la copie) le pose là où l'élève a tracé."""
    info = meta.get("construction") or {}
    spec = info.get("spec")
    if not spec:
        return []
    spec = normalize(spec)
    ox, oy = info["origin_pt"]
    u = info["unit_pt"]
    move = Placement.from_dict(placement)
    out = []
    for prim in primitives(spec.get("expected"), spec, "expected"):
        prim = move.prim(prim)
        if prim.kind == "point":
            x, y = prim.a
            arm = CROSS_MM / STEP_MM
            out.append([(ox + (x - arm) * u, oy + (y - arm) * u), (ox + (x + arm) * u, oy + (y + arm) * u)])
            out.append([(ox + (x - arm) * u, oy + (y + arm) * u), (ox + (x + arm) * u, oy + (y - arm) * u)])
            continue
        if prim.infinite:
            clipped = _clip(prim.a, prim.b, spec["cols"], spec["rows"], ray=prim.ray)
            if clipped is None:
                continue
            prim = replace(prim, a=clipped[0], b=clipped[1])
        samples = _samples_units(prim, 0.1 if prim.kind == "circle" else 1e3)
        pts = [(ox + sx * u, oy + sy * u) for sx, sy in samples]
        if prim.kind == "circle":
            pts.append(pts[0])
        out.append(pts)
    return out


def draw_expected(c, meta: dict, color, width: float = 1.4) -> None:
    """Tracé attendu dessiné sur l'aperçu (comme un élève qui a juste)."""
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.setLineCap(1)
    c.setLineJoin(1)
    for line in expected_polylines_pt(meta):
        path = c.beginPath()
        path.moveTo(*line[0])
        for p in line[1:]:
            path.lineTo(*p)
        c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


# ------------------------------------------------------------------ lecture
@dataclass
class _Frame:
    """Repère de lecture : carreaux <-> pixels du recadrage."""
    ox: float
    oy: float
    u: float
    s: float
    page_h: float
    x0: int
    y0: int
    dx: float = 0.0
    dy: float = 0.0

    def px(self, p) -> tuple[float, float]:
        q = self.px_array(np.array([p], float))[0]
        return float(q[0]), float(q[1])

    def px_array(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, float)
        return np.stack([(self.ox + pts[:, 0] * self.u) * self.s - self.x0 + self.dx,
                         (self.page_h - (self.oy + pts[:, 1] * self.u)) * self.s - self.y0 + self.dy], 1)

    def units(self, px: np.ndarray) -> np.ndarray:
        px = np.asarray(px, float)
        return np.stack([((px[:, 0] - self.dx + self.x0) / self.s - self.ox) / self.u,
                         ((self.page_h - (px[:, 1] - self.dy + self.y0) / self.s) - self.oy) / self.u], 1)

    @property
    def mm_px(self) -> float:
        return PT_PER_MM * self.s

    def pt_box(self, box) -> tuple[int, int, int, int]:
        bx, by, bw, bh = box
        x_a = bx * self.s - self.x0 + self.dx
        y_a = (self.page_h - (by + bh)) * self.s - self.y0 + self.dy
        return int(x_a), int(y_a), int(x_a + bw * self.s) + 1, int(y_a + bh * self.s) + 1


def _render_prims(shape, prims: list[Prim], frame: _Frame, thickness: int, *, cross: bool = False):
    import cv2
    mask = np.zeros(shape, np.uint8)
    for prim in prims:
        if prim.kind == "point":
            if cross:
                cx, cy = frame.px(prim.a)
                arm = CROSS_MM * frame.mm_px
                for sx, sy in ((1, 1), (1, -1)):
                    cv2.line(mask, (int(cx - arm * sx), int(cy - arm * sy)),
                             (int(cx + arm * sx), int(cy + arm * sy)), 255, thickness)
            continue
        pts = frame.px_array(_samples_units(prim, 0.05 if prim.kind == "circle" else 1e3)).astype(np.int32)
        cv2.polylines(mask, [pts], prim.kind == "circle", 255, thickness, lineType=cv2.LINE_8)
    return mask


def _printed_masks(shape, given: list[Prim], printed_points: list[Prim], labels, frame: _Frame):
    import cv2
    thin = _render_prims(shape, given + printed_points, frame, 2, cross=True)
    wide = thin.copy()
    for box in labels:
        x_a, y_a, x_b, y_b = frame.pt_box(box)
        cv2.rectangle(wide, (x_a - 2, y_a - 2), (x_b + 2, y_b + 2), 255, -1)
    halo = max(1, int(round(PRINT_HALO_MM * frame.mm_px)))
    wide = cv2.dilate(wide, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * halo + 1, 2 * halo + 1)))
    return thin, wide


def _register(ink: np.ndarray, thin: np.ndarray, search: int) -> tuple[int, int, float]:
    """Décalage (dx, dy) qui pose la figure imprimée attendue sur l'encre lue."""
    import cv2
    mass = float((thin > 0).sum())
    if mass < REG_MIN_PX:
        return 0, 0, -1.0
    padded = cv2.copyMakeBorder(ink, search, search, search, search, cv2.BORDER_CONSTANT, value=0)
    score = cv2.matchTemplate(padded, (thin > 0).astype(np.float32), cv2.TM_CCORR)
    _, best, _, loc = cv2.minMaxLoc(score)
    found = best / mass
    if found < REG_FOUND:
        return 0, 0, found
    return loc[0] - search, loc[1] - search, found


@dataclass
class _Scene:
    """Ce que la lecture sait d'une zone : repère, encre de l'élève, distances."""
    frame: _Frame
    student: np.ndarray           # encre élève (0/1), figure imprimée retirée
    dist: np.ndarray              # distance (px) à l'encre élève la plus proche
    hidden: np.ndarray            # sous la figure imprimée : invisible
    tol_px: float

    def lookup(self, pts_px: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(dans la zone, caché, distance) pour des points en pixels."""
        h, w = self.dist.shape
        ix, iy = np.rint(pts_px[:, 0]).astype(int), np.rint(pts_px[:, 1]).astype(int)
        inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        ixc, iyc = np.clip(ix, 0, w - 1), np.clip(iy, 0, h - 1)
        return inside, self.hidden[iyc, ixc] & inside, np.where(inside, self.dist[iyc, ixc], 1e9)


def _certainty(value: float, low: float, high: float) -> float:
    """0,6 au seuil de décision, 1 quand la mesure s'en éloigne nettement."""
    return 0.6 + 0.4 * float(np.clip((value - low) / max(1e-9, high - low), 0, 1))


LENGTH_BIN_MM = 0.25
LENGTH_GAP_MM = 1.0           # trou toléré dans un trait (stylo qui saute)


def _length_check(prim: Prim, scene: _Scene) -> tuple[float, float]:
    """Tracé libre : de combien le trait de l'élève dépasse (+) ou s'arrête avant (−)
    chaque bout d'un segment, en mm. L'encre proche de la droite du segment est
    projetée sur sa direction ; on suit le trait continu qui le porte."""
    frame = scene.frame
    a, b = frame.px_array(np.array([prim.a, prim.b]))
    length = float(np.linalg.norm(b - a))
    if length < 1:
        return 0.0, 0.0
    d = (b - a) / length
    reach = (LENGTH_BAD_MM + 3.0) * frame.mm_px
    lo, hi = np.minimum(a, b) - reach, np.maximum(a, b) + reach
    h, w = scene.student.shape
    x_a, y_a = max(0, int(lo[0])), max(0, int(lo[1]))
    x_b, y_b = min(w, int(hi[0]) + 1), min(h, int(hi[1]) + 1)
    ys, xs = np.nonzero(scene.student[y_a:y_b, x_a:x_b])
    if not len(xs):
        return -length / frame.mm_px, 0.0
    rel = np.stack([xs + x_a, ys + y_a], 1) - a
    t = rel @ d
    perp = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0])
    t = t[(perp <= scene.tol_px) & (t > -reach) & (t < length + reach)]
    if not len(t):
        return -length / frame.mm_px, 0.0
    bin_px = LENGTH_BIN_MM * frame.mm_px
    edges = np.arange(-reach, length + reach + bin_px, bin_px)
    filled = np.histogram(t, edges)[0] > 0
    # point de départ : la case encrée la plus proche du milieu du segment
    middle = int(np.searchsorted(edges, length / 2)) - 1
    inked = np.nonzero(filled)[0]
    k0 = int(inked[np.argmin(np.abs(inked - middle))])
    gap = max(1, int(LENGTH_GAP_MM / LENGTH_BIN_MM))

    def walk(step: int) -> int:
        k, last = k0, k0
        while 0 <= k + step < len(filled):
            k += step
            if filled[k]:
                last = k
            elif abs(k - last) > gap:
                break
        return last

    start_px = edges[walk(-1)]
    end_px = edges[walk(1) + 1]
    return float(-start_px / frame.mm_px), float((end_px - length) / frame.mm_px)


def _measure(prim: Prim, scene: _Scene, others: list[Prim]) -> dict:
    frame = scene.frame
    if prim.kind == "point":
        cx, cy = frame.px(prim.a)
        r = int(scene.tol_px) + 2
        h, w = scene.student.shape
        x_a, x_b = max(0, int(cx) - r), min(w, int(cx) + r + 1)
        y_a, y_b = max(0, int(cy) - r), min(h, int(cy) + r + 1)
        ink = int(scene.student[y_a:y_b, x_a:x_b].sum()) if x_b > x_a and y_b > y_a else 0
        if ink >= POINT_INK_PX:
            state, cert = "present", _certainty(ink, POINT_INK_PX, 3 * POINT_INK_PX)
        elif ink < POINT_EMPTY_PX:
            state, cert = "absent", _certainty(POINT_EMPTY_PX - ink, 0, POINT_EMPTY_PX)
        else:
            state, cert = "ambiguous", 0.3
        return {"name": prim.name, "kind": "point", "ink_px": ink, "state": state,
                "cover": 1.0 if state == "present" else 0.0, "certainty": round(cert, 3)}
    step_units = 2.0 / (frame.u * frame.s)              # un échantillon tous les 2 px
    pts = _samples_units(prim, step_units)
    inside, hidden, dist = scene.lookup(frame.px_array(pts))
    seen_mask = inside & ~hidden
    flags = dist[seen_mask] <= scene.tol_px
    n_inside = int(inside.sum())
    visible_share = float(seen_mask.sum()) / max(1, n_inside)
    cover = float(flags.mean()) if len(flags) else 0.0
    gap = run = best_run = gap_max = 0
    for f in flags:
        gap, run = (0, run + 1) if f else (gap + 1, 0)
        best_run, gap_max = max(best_run, run), max(gap_max, gap)
    mm_per_sample = 2.0 / frame.mm_px
    errs = dist[seen_mask][flags]
    out = {"name": prim.name, "kind": "line" if prim.infinite else prim.kind,
           "cover": round(cover, 3), "visible": round(visible_share, 3),
           "gap_mm": round(gap_max * mm_per_sample, 1),
           "err_mm": round(float(errs.mean()) / frame.mm_px, 2) if len(errs) else None}
    if n_inside == 0 or visible_share < VISIBLE_MIN:
        out.update(state="hidden", certainty=0.2)
        return out
    if prim.infinite:
        need = min(LINE_RUN_MM, 0.6 * len(flags) * mm_per_sample)
        run_mm = best_run * mm_per_sample
        out["run_mm"] = round(run_mm, 1)
        if run_mm >= need:
            out.update(state="present", certainty=_certainty(run_mm, need, 1.6 * need))
        elif run_mm < 0.3 * need:
            out.update(state="absent", certainty=_certainty(0.3 * need - run_mm, 0, 0.3 * need))
        else:
            out.update(state="ambiguous", certainty=0.3)
        if prim.ray and out["state"] == "present":
            # une demi-droite part de son origine
            first = flags[: max(2, int(3.0 / mm_per_sample))]
            if first.mean() < 0.5:
                out.update(state="ambiguous", certainty=0.3)
        return out
    if cover >= COVER_PRESENT and out["gap_mm"] <= GAP_MAX_MM:
        out.update(state="present", certainty=min(_certainty(cover, COVER_PRESENT, 0.97),
                                                  _certainty(GAP_MAX_MM - out["gap_mm"], 0, GAP_MAX_MM)))
    elif cover <= COVER_ABSENT:
        out.update(state="absent", certainty=_certainty(COVER_ABSENT - cover, 0, COVER_ABSENT))
    else:
        out.update(state="ambiguous", certainty=0.3)
    return out


def _fit_line(prim: Prim, scene: _Scene) -> tuple[np.ndarray, np.ndarray] | None:
    """Droite ajustée sur le trait de l'élève le long d'un segment (pixels), loin
    des sommets où les côtés se croisent. (point, direction) ou None."""
    frame = scene.frame
    a, b = frame.px_array(np.array([prim.a, prim.b]))
    length = float(np.linalg.norm(b - a))
    d = (b - a) / (length or 1.0)
    lo, hi = np.minimum(a, b) - scene.tol_px, np.maximum(a, b) + scene.tol_px
    h, w = scene.student.shape
    x_a, y_a = max(0, int(lo[0])), max(0, int(lo[1]))
    x_b, y_b = min(w, int(hi[0]) + 1), min(h, int(hi[1]) + 1)
    ys, xs = np.nonzero(scene.student[y_a:y_b, x_a:x_b])
    pts = np.stack([xs + x_a, ys + y_a], 1).astype(float)
    if len(pts) < 10:
        return None
    rel = pts - a
    t = rel @ d
    perp = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0])
    keep = (perp <= scene.tol_px) & (t > 0.15 * length) & (t < 0.85 * length)
    if keep.sum() < 10:
        return None
    sel = pts[keep]
    center = sel.mean(axis=0)
    _, _, vt = np.linalg.svd(sel - center, full_matrices=False)
    direction = vt[0] if vt[0] @ d >= 0 else -vt[0]
    return center, direction


def _intersect(p1, d1, p2, d2) -> np.ndarray | None:
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < math.sin(math.radians(15)):
        return None                                # côtés presque parallèles
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
    return p1 + t * d1


def _check_lengths(prims: list[Prim], elements: list[dict], scene: _Scene) -> None:
    """Tracé libre : longueur de chaque segment mesurée comme un professeur, d'un
    sommet à l'autre. Un sommet partagé par deux côtés est l'intersection de leurs
    traits (les traits qui se croisent et dépassent au coin ne comptent pas) ; un
    bout libre est l'extrémité du trait."""
    frame = scene.frame
    fits = {i: _fit_line(p, scene) for i, p in enumerate(prims)
            if p.measured and elements[i]["state"] == "present"}
    for i, prim in enumerate(prims):
        if i not in fits:
            continue
        element = elements[i]
        ends = []
        start, end = _length_check(prim, scene)
        for k, (corner, extent) in enumerate(((prim.a, start), (prim.b, end))):
            vertex = None
            for j, other in enumerate(prims):
                if j == i or j not in fits or fits[i] is None or fits[j] is None:
                    continue
                if math.dist(corner, other.a) < 1e-6 or math.dist(corner, other.b) < 1e-6:
                    vertex = _intersect(*fits[i], *fits[j])
                    if vertex is not None:
                        break
            if vertex is None:                     # bout libre : extrémité du trait
                a, b = frame.px_array(np.array([prim.a, prim.b]))
                d = (b - a) / (np.linalg.norm(b - a) or 1.0)
                vertex = (a - d * extent * frame.mm_px) if k == 0 else (b + d * extent * frame.mm_px)
            ends.append(vertex)
        drawn = float(np.linalg.norm(ends[1] - ends[0])) / frame.mm_px
        delta = abs(drawn - math.dist(prim.a, prim.b) * STEP_MM)
        element["length_mm"] = round(drawn, 1)
        element["expected_mm"] = round(math.dist(prim.a, prim.b) * STEP_MM, 1)
        if delta <= LENGTH_OK_MM:
            element["certainty"] = min(element["certainty"], _certainty(LENGTH_OK_MM - delta, 0, 1.0))
        elif delta >= LENGTH_BAD_MM:
            element.update(state="wrong_length", certainty=_certainty(delta - LENGTH_BAD_MM, 0, 1.0))
        else:
            element.update(state="ambiguous", certainty=0.3)


def _evaluate(prims: list[Prim], scene: _Scene) -> dict:
    elements = [_measure(p, scene, prims) for p in prims]
    if any(p.measured for p in prims):
        _check_lengths(prims, elements, scene)
    present = sum(e["state"] == "present" for e in elements)
    return {"elements": elements, "present": present, "total": len(elements),
            "fraction": round(present / len(elements), 3) if elements else 0.0,
            "mean_cover": round(float(np.mean([e["cover"] for e in elements])), 3) if elements else 0.0}


def _fit_score(prims: list[Prim], placement: Placement, scene: _Scene) -> float:
    """Recouvrement rapide d'une figure déplacée : moyenne, élément par élément."""
    scores = []
    for prim in prims:
        if prim.kind == "point":
            continue
        moved = placement.prim(prim)
        pts = _samples_units(moved, 1.0 / STEP_MM)
        inside, _hidden, dist = scene.lookup(scene.frame.px_array(pts))
        if not inside.any():
            scores.append(0.0)
            continue
        covered = dist[inside] <= scene.tol_px
        scores.append(min(1.0, covered.sum() / LINE_RUN_MM) if prim.infinite else float(covered.mean()))
    return float(np.mean(scores)) if scores else 0.0


def _detected_segments(scene: _Scene, limit: int = 12) -> list[tuple[np.ndarray, np.ndarray]]:
    import cv2
    mm = scene.frame.mm_px
    lines = cv2.HoughLinesP((scene.student * 255).astype(np.uint8), 1, math.pi / 360,
                            threshold=int(4 * mm), minLineLength=int(6 * mm), maxLineGap=int(1.5 * mm))
    if lines is None:
        return []
    # (N, 1, 4) sous OpenCV 4, (N, 4) sous OpenCV 5
    segs = sorted((tuple(map(float, l)) for l in np.asarray(lines).reshape(-1, 4)),
                  key=lambda s: -math.hypot(s[2] - s[0], s[3] - s[1]))
    out = []
    for x1, y1, x2, y2 in segs[:limit]:
        p, q = scene.frame.units(np.array([[x1, y1], [x2, y2]], float))
        out.append((p, q))
    return out


def _detected_circles(scene: _Scene, radius_units: float) -> list[np.ndarray]:
    import cv2
    r_px = radius_units * scene.frame.u * scene.frame.s
    img = cv2.GaussianBlur((scene.student * 255).astype(np.uint8), (5, 5), 1.5)
    found = cv2.HoughCircles(img, cv2.HOUGH_GRADIENT, dp=1.5, minDist=max(10, r_px / 2), param1=100,
                             param2=15, minRadius=int(0.7 * r_px), maxRadius=int(1.3 * r_px) + 1)
    if found is None:
        return []
    return [scene.frame.units(np.array([[x, y]], float))[0] for x, y, _r in found[0][:6]]


def _align(prims: list[Prim], scene: _Scene) -> Placement:
    """Tracé libre : le déplacement qui pose la figure attendue sur l'encre de l'élève."""
    candidates: list[Placement] = []
    detected = _detected_segments(scene)
    for a, b in _anchor_pairs(prims):
        for p, q in detected:
            for mirror in (False, True):
                candidates.append(_placement_onto(a, b, p, q, mirror))
                candidates.append(_placement_onto(a, b, q, p, mirror))
    ys, xs = np.nonzero(scene.student)
    if len(xs):
        ink_center = scene.frame.units(np.array([[xs.mean(), ys.mean()]]))[0]
        defining = np.array([p.a for p in prims if not p.infinite]
                            + [p.b for p in prims if p.kind == "seg" and not p.infinite] or [prims[0].a])
        model_center = defining.mean(axis=0)
        for circle in [p for p in prims if p.kind == "circle"][:1]:
            for center in _detected_circles(scene, circle.radius):
                candidates.append(Placement(0.0, False, *(center - np.asarray(circle.a))))
        for deg in range(0, 360, 15):
            for mirror in (False, True):
                move = Placement(math.radians(deg), mirror)
                shift = ink_center - move.apply(model_center)[0]
                candidates.append(Placement(move.theta, mirror, *shift))
    if not candidates:
        return Placement()
    scored = sorted(candidates, key=lambda m: -_fit_score(prims, m, scene))[:3]
    best, best_score = scored[0], -1.0
    for start in scored:
        move, score = _refine(prims, start, scene)
        if score > best_score:
            best, best_score = move, score
    return best


def _refine(prims: list[Prim], move: Placement, scene: _Scene) -> tuple[Placement, float]:
    """Ajustement fin : petites rotations (autour de la figure) et translations."""
    pivot = np.mean([move.point(p.a) for p in prims], axis=0)
    score = _fit_score(prims, move, scene)
    for step_deg, step_u in ((2.0, 0.2), (0.5, 0.06)):
        improved = True
        while improved:
            improved = False
            for d_theta, dx, dy in ((step_deg, 0, 0), (-step_deg, 0, 0), (0, step_u, 0),
                                    (0, -step_u, 0), (0, 0, step_u), (0, 0, -step_u)):
                t = math.radians(d_theta)
                c, s = math.cos(t), math.sin(t)
                # rotation de d_theta autour du pivot, puis translation
                tx = c * (move.tx - pivot[0]) - s * (move.ty - pivot[1]) + pivot[0] + dx
                ty = s * (move.tx - pivot[0]) + c * (move.ty - pivot[1]) + pivot[1] + dy
                trial = Placement(move.theta + t, move.mirror, tx, ty)
                trial_score = _fit_score(prims, trial, scene)
                if trial_score > score + 1e-4:
                    move, score, improved = trial, trial_score, True
    return move, score


def _place(prims: list[Prim], scene: _Scene, free: bool) -> tuple[list[Prim], Placement]:
    if not free:
        return prims, Placement()
    move = _align(prims, scene)
    return [move.prim(p) for p in prims], move


def read(image: np.ndarray, meta: dict, *, page_h_pt: float | None = None, dpi: int = DPI) -> dict:
    """Lit le tracé de l'élève dans une page recalée (BGR, `dpi`). Rend la part du
    tracé attendu retrouvée (`fraction`) et une confiance continue (0 à 1)."""
    import cv2
    from . import worker_cv
    info = meta.get("construction") or meta
    spec = normalize(info["spec"])
    if page_h_pt is None:
        page_h_pt = worker_cv.PAGE_H
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    s = dpi / 72.0
    ox, oy = info["origin_pt"]
    u = info["unit_pt"]
    cols, rows = spec["cols"], spec["rows"]
    free = is_free(spec)
    margin = 4 * PT_PER_MM * s
    H, W = image.shape[:2]
    x0 = max(0, int(ox * s - margin))
    x1 = min(W, int((ox + cols * u) * s + margin))
    y0 = max(0, int((page_h_pt - (oy + rows * u)) * s - margin))
    y1 = min(H, int((page_h_pt - oy) * s + margin))
    crop = image[y0:y1, x0:x1]
    frame = _Frame(ox, oy, u, s, page_h_pt, x0, y0)
    if crop.size == 0:
        return {"status": "unreadable", "confidence": 0.0, "fraction": 0.0, "reasons": ["zone hors de la page"]}
    flat = worker_cv.flatten_background(crop)
    ink = worker_cv._ink_strength(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY))
    shape = ink.shape

    pts = points_of(spec)
    given = [p for p in primitives(spec["given"], spec, "given") if p.kind != "point"]
    printed_points = [Prim("point", pts[str(n)], name=str(n)) for n in spec["given"].get("points") or []]
    labels = info.get("label_boxes") or []
    thin, _ = _printed_masks(shape, given, printed_points, labels, frame)
    search = int(round(REG_SEARCH_MM * frame.mm_px))
    dx, dy, found = _register(ink.astype(np.float32), thin, search)
    frame.dx, frame.dy = dx, dy
    thin, wide = _printed_masks(shape, given, printed_points, labels, frame)
    # en mode libre, le bord de la case (saumon, normalement effacé) ne compte pas
    if not spec["grid"]:
        pad = ZONE_PAD_MM / STEP_MM
        box = frame.px_array(np.array([[-pad, -pad], [cols + pad, rows + pad]]))
        edge = np.zeros(shape, np.uint8)
        cv2.rectangle(edge, tuple(int(v) for v in box[0]), tuple(int(v) for v in box[1]), 255,
                      max(3, int(1.2 * frame.mm_px)))
        wide = np.maximum(wide, edge)

    student = ((ink >= INK_ON) & (wide == 0)).astype(np.uint8)
    n, comp, stats, _ = cv2.connectedComponentsWithStats(student, connectivity=8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < SPECK_PX:
            student[comp == i] = 0
    # Quadrillage resté visible : un copieur qui rend le saumon en gris.
    grid_residue = 0.0
    if spec["grid"]:
        lines = np.zeros(shape, np.uint8)
        for i in range(cols + 1):
            a, b = frame.px((i, 0)), frame.px((i, rows))
            cv2.line(lines, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), 255, 1)
        for j in range(rows + 1):
            a, b = frame.px((0, j)), frame.px((cols, j))
            cv2.line(lines, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), 255, 1)
        far = (lines > 0) & (wide == 0)
        if far.sum():
            grid_residue = float(((ink >= INK_ON) & far).sum() / far.sum())
            if grid_residue > GRID_RESIDUE_MAX:
                band = cv2.dilate(lines, np.ones((5, 5), np.uint8))
                student[band > 0] = 0
    total = int(student.sum())
    tol_px = spec["tolerance_mm"] * frame.mm_px
    caps, reasons = [], []
    if grid_residue > GRID_RESIDUE_MAX:
        caps.append(CAP_DOUBT)
        reasons.append("quadrillage resté visible")
    if 0 <= found < REG_FOUND:
        caps.append(CAP_DOUBT)
        reasons.append("figure imprimée introuvable (recalage)")
    base = {"ink_px": total, "shift_px": [int(dx), int(dy)], "registration": round(found, 3),
            "grid_residue": round(grid_residue, 3), "mode": "libre" if free else "ancré"}
    if total < BLANK_PX:
        confidence = min([_certainty(BLANK_PX - total, 0, BLANK_PX)] + caps)
        return {**base, "status": "blank", "fraction": 0.0, "elements": [], "error_id": None,
                "confidence": round(confidence, 3), "reasons": reasons}
    dist = cv2.distanceTransform((student == 0).astype(np.uint8), cv2.DIST_L2, 5)
    scene = _Scene(frame, student, dist, wide > 0, tol_px)

    candidates = [primitives(spec["expected"], spec, "expected")]
    candidates += [primitives(b, spec, "alternatives") for b in spec["alternatives"]]
    placed = [_place(c, scene, free) for c in candidates]
    results = [_evaluate(prims, scene) for prims, _ in placed]
    best_i = max(range(len(results)), key=lambda i: (results[i]["present"], results[i]["mean_cover"]))
    best = results[best_i]
    keep, placement = placed[best_i]
    error_id, errors, error_prims = None, {}, []
    for err in spec["errors"]:
        prims, _move = _place(primitives(err.get("trace"), spec, "errors"), scene, free)
        result = _evaluate(prims, scene)
        errors[str(err["id"])] = {"fraction": result["fraction"], "mean_cover": result["mean_cover"]}
        if (result["present"] == result["total"] and best["fraction"] < 1
                and result["mean_cover"] > best["mean_cover"]
                and (error_id is None or result["mean_cover"] > errors[error_id]["mean_cover"])):
            error_id, error_prims = str(err["id"]), prims
    if error_id:
        # L'erreur reconnue explique l'encre : un élément attendu qu'elle frôle
        # n'est pas « à moitié tracé », il est absent.
        for element in best["elements"]:
            if element["state"] == "ambiguous":
                element.update(state="absent", certainty=0.6)
    # Encre hors du tracé retenu (ou de l'erreur reconnue) : traits en trop, rature.
    explained = keep + error_prims
    band = _render_prims(shape, explained, frame, max(3, int(2 * (tol_px + 4))))
    zone_r = int(VERTEX_ZONE_MM * frame.mm_px)
    for prim in explained:
        ends = ([prim.a] if prim.infinite and prim.ray else [] if prim.infinite else
                [prim.a, prim.b] if prim.kind == "seg" else [prim.a])
        for p in ends:
            cx, cy = frame.px(p)
            cv2.circle(band, (int(cx), int(cy)), zone_r, 255, -1)
    extra = float(((student > 0) & (band == 0)).sum()) / total
    states = [e["state"] for e in best["elements"]]
    if "ambiguous" in states:
        reasons.append("tracé partiel ou imprécis")
    if "hidden" in states:
        reasons.append("tracé caché sous la figure imprimée")
    if "wrong_length" in states:
        reasons.append("longueur fausse")
    extra_cert = 1.0 if extra <= EXTRA_FREE else max(0.0, 1.0 - (extra - EXTRA_FREE) * EXTRA_SLOPE)
    if extra_cert < 0.6:
        reasons.append(f"encre hors du tracé attendu ({extra:.0%})")
    confidence = min([e.get("certainty", 0.6) for e in best["elements"]] + [extra_cert] + caps)
    out = {**base, "status": "read", "fraction": best["fraction"], "candidate": best_i,
           "elements": best["elements"], "error_id": error_id, "errors": errors,
           "extra_share": round(extra, 3), "confidence": round(confidence, 3), "reasons": reasons}
    if free:
        out["placement"] = placement.as_dict()
    return out


# ------------------------------------------------------------------ banc synthétique
PEN_BGR = (150, 60, 20)          # stylo bleu
PENCIL_BGR = (110, 110, 110)     # crayon à papier


def _free_placement(prims: list[Prim], spec: dict, rng: random.Random) -> Placement:
    """Où un élève poserait la figure libre : un peu tournée, parfois retournée,
    n'importe où dans la case, sans en sortir."""
    cols, rows = spec["cols"], spec["rows"]
    for _ in range(40):
        move = Placement(math.radians(rng.uniform(-25, 25)), rng.random() < 0.3)
        (x0, y0), (x1, y1) = _bbox([move.prim(p) for p in prims])
        if x1 - x0 > cols - 1 or y1 - y0 > rows - 1:
            continue
        tx = rng.uniform(0.5 - x0, cols - 0.5 - x1)
        ty = rng.uniform(0.5 - y0, rows - 0.5 - y1)
        return Placement(move.theta, move.mirror, tx, ty)
    return Placement()


def synthetic_page(spec: dict, trace: str | dict = "expected", *, seed: int = 0, style: str = "pen",
                   shift_mm: tuple[float, float] = (0.0, 0.0), copier_gray: bool = False,
                   jitter_mm: float = 0.4, labels: bool = True,
                   offset_units: tuple[float, float] = (0.0, 0.0), scale: float = 1.0,
                   arcs: bool = False) -> tuple[np.ndarray, dict]:
    """Page A4 rasterisée à 200 dpi avec la zone imprimée, puis le tracé simulé d'un
    élève : `expected`, `blank`, `alternative:<i>`, `error:<id>` ou un bloc d'éléments.
    `offset_units` décale tout le tracé, `scale` l'agrandit (figure fausse, mais
    propre) ; `arcs` ajoute des arcs de compas aux sommets. Un tracé libre est posé
    au hasard dans la case, comme le ferait un élève."""
    import cv2
    import fitz
    from io import BytesIO
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas

    spec = normalize(spec)
    _page_w, page_h = A4
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    w = 93.5 * PT_PER_MM
    h = zone_height(spec)
    meta = draw_zone(c, 40, page_h - 120 - h, w, h, spec, HexColor(settings.dropout_color))
    c.save()
    with fitz.open(stream=buf.getvalue(), filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR).copy()
    if copier_gray:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        salmon = ((hsv[..., 0] <= 18) | (hsv[..., 0] >= 165)) & (hsv[..., 1] > 30)
        img[salmon] = (175, 175, 175)
    rng = random.Random(seed)
    s = DPI / 72.0
    frame = _Frame(*meta["construction"]["origin_pt"], meta["construction"]["unit_pt"], s, page_h, 0, 0)
    frame.dx = offset_units[0] * frame.u * s
    frame.dy = -offset_units[1] * frame.u * s
    if trace != "blank":
        if isinstance(trace, dict):
            block = trace
        elif trace == "expected":
            block = spec["expected"]
        elif trace.startswith("alternative:"):
            block = spec["alternatives"][int(trace.split(":")[1])]
        else:
            err_id = trace.split(":", 1)[1]
            block = next(e for e in spec["errors"] if str(e["id"]) == err_id)["trace"]
        prims = primitives(block, spec, "trace")
        if scale != 1.0:
            center = np.mean([p.a for p in prims], axis=0)
            prims = [replace(p, a=tuple(center + (np.asarray(p.a) - center) * scale),
                             b=tuple(center + (np.asarray(p.b) - center) * scale),
                             radius=p.radius * scale) for p in prims]
        move = _free_placement(prims, spec, rng) if is_free(spec) else Placement()
        prims = [move.prim(p) for p in prims]
        color = PEN_BGR if style == "pen" else PENCIL_BGR
        thick = 3 if style == "pen" else 2
        mm_px = frame.mm_px
        for prim in prims:
            if prim.kind == "point":
                cx, cy = frame.px(prim.a)
                arm = 1.3 * mm_px
                for sx, sy in ((1, 1), (1, -1)):
                    cv2.line(img, (int(cx - arm * sx), int(cy - arm * sy)),
                             (int(cx + arm * sx), int(cy + arm * sy)), color, thick, cv2.LINE_AA)
                continue
            off = (rng.uniform(-jitter_mm, jitter_mm) * mm_px, rng.uniform(-jitter_mm, jitter_mm) * mm_px)
            if prim.kind == "circle":
                cx, cy = frame.px(prim.a)
                r = prim.radius * frame.u * s + rng.uniform(-jitter_mm, jitter_mm) * mm_px
                cv2.circle(img, (int(cx + off[0]), int(cy + off[1])), int(r), color, thick, cv2.LINE_AA)
                continue
            a, b = np.array(frame.px(prim.a)), np.array(frame.px(prim.b))
            v = (b - a) / (np.linalg.norm(b - a) or 1.0)
            if prim.infinite and prim.ray and is_free(spec):   # demi-droite : depuis son origine
                b = a + v * rng.uniform(30, 50) * mm_px
            elif prim.infinite:          # une droite se trace sur une longueur libre
                if is_free(spec):
                    mid, half = a, rng.uniform(15, 25) * mm_px
                else:
                    mid, half = (a + b) / 2, np.linalg.norm(b - a) * rng.uniform(0.3, 0.48)
                a, b = mid - v * half, mid + v * half
            elif is_free(spec):          # tracé libre à la règle : au millimètre près
                a = a - v * rng.uniform(-0.6, 0.6) * mm_px
                b = b + v * rng.uniform(-0.6, 0.6) * mm_px
            else:                         # dépasse ou s'arrête un peu court
                a = a - v * rng.uniform(-0.5, 1.2) * mm_px
                b = b + v * rng.uniform(-0.5, 1.2) * mm_px
            cv2.line(img, tuple(int(t) for t in a + off), tuple(int(t) for t in b + off), color, thick,
                     cv2.LINE_AA)
        if arcs:                          # arcs de compas laissés aux sommets
            for prim in prims:
                if prim.kind == "seg" and not prim.infinite:
                    cx, cy = frame.px(prim.b)
                    ang = math.degrees(math.atan2(prim.b[1] - prim.a[1], prim.b[0] - prim.a[0]))
                    radius = int(math.dist(prim.a, prim.b) * frame.u * s)
                    ax, ay = frame.px(prim.a)
                    cv2.ellipse(img, (int(ax), int(ay)), (radius, radius), 0, -ang - 8, -ang + 8,
                                PENCIL_BGR, 1, cv2.LINE_AA)
        if labels:                        # l'élève nomme ses points
            names = (block.get("points") or []) + [v for poly in block.get("polygons") or []
                                                   for v in (poly.get("vertices") if isinstance(poly, dict) else poly)]
            for name in names:
                cx, cy = frame.px(move.point(points_of(spec)[str(name)]))
                cv2.putText(img, str(name), (int(cx + 1.5 * mm_px), int(cy - 1.5 * mm_px)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    if shift_mm != (0.0, 0.0):            # recalage de page imparfait
        m = np.float32([[1, 0, shift_mm[0] * PT_PER_MM * s], [0, 1, shift_mm[1] * PT_PER_MM * s]])
        img = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderValue=(255, 255, 255))
    return img, meta


SELFTEST_FREE_SEEDS = ((1, "pen"), (2, "pencil"), (3, "pen"), (4, "pencil"), (5, "pen"), (6, "pencil"))


def selftest(spec: dict) -> list[str]:
    """Banc d'une carte : le tracé juste doit être lu juste et net, la copie blanche
    blanche, chaque erreur reconnue. Retourne les échecs, en clair."""
    out = []
    spec = normalize(spec)
    # Tracé libre : la figure se pose au hasard dans la case, et une seule position
    # ne dit rien des autres. On en essaie plusieurs ; une lecture fausse fait
    # toujours échouer, une confiance basse (copie renvoyée au professeur, jamais
    # mal notée) est tolérée une fois.
    free = is_free(spec)
    seeds = SELFTEST_FREE_SEEDS if free else ((1, "pen"), (2, "pencil"))
    tolerated = 1 if free else 0
    cases = ["expected"] + [f"alternative:{i}" for i in range(len(spec["alternatives"]))]
    for trace in cases:
        unsure = []
        for seed, style in seeds:
            img, meta = synthetic_page(spec, trace, seed=seed, style=style)
            got = read(img, meta)
            why = ", ".join(got.get("reasons") or []) or (
                f"{got.get('fraction', 0):.0%} retrouvé, confiance {got.get('confidence', 0):.0%}")
            if got["status"] != "read" or got["fraction"] < 1:
                out.append(f"tracé juste ({trace}, {style}, position {seed}) mal lu : {why}")
                break
            if got["confidence"] < SELFTEST_CONFIDENCE:
                unsure.append(f"position {seed} : {why}")
                if len(unsure) > tolerated:
                    out.append(f"tracé juste ({trace}) peu sûr sur {len(unsure)} positions sur "
                               f"{len(seeds)} : " + " ; ".join(unsure))
                    break
    img, meta = synthetic_page(spec, "blank")
    got = read(img, meta)
    if got["status"] != "blank":
        out.append(f"copie blanche lue comme tracée ({got['ink_px']} px d'encre)")
    for err in spec["errors"]:
        missed = []
        for seed in ((3, 4, 5) if free else (3,)):
            img, meta = synthetic_page(spec, f"error:{err['id']}", seed=seed)
            got = read(img, meta)
            if got.get("fraction", 0) >= 1:
                out.append(f"l'erreur {err['id']} est lue comme un tracé juste (position {seed})")
                break
            if got.get("error_id") != str(err["id"]):
                missed.append(f"position {seed} lue {got.get('error_id')}")
        else:
            if len(missed) > tolerated:
                out.append(f"l'erreur {err['id']} n'est pas reconnue (" + ", ".join(missed) + ")")
    return out


# ------------------------------------------------------------------ micro-correction
def _cm(mm: float) -> str:
    text = f"{mm / 10:.1f}".replace(".", ",")
    return text[:-2] if text.endswith(",0") else text


def feedback_text(reading: dict | None) -> str:
    """Micro-correction tirée des mesures, quand aucune erreur typique n'est
    reconnue : une longueur fausse (la plus fausse), sinon ce qui manque. Vide si
    la lecture ne dit rien de précis (le message générique de la carte prend le
    relais). Court : il tient dans les deux lignes réservées."""
    reading = reading or {}
    if reading.get("status") != "read":
        return ""
    elements = reading.get("elements") or []
    lengths = [e for e in elements if e.get("state") == "wrong_length"
               and e.get("length_mm") is not None and e.get("expected_mm")]
    if lengths:
        worst = max(lengths, key=lambda e: abs(e["length_mm"] - e["expected_mm"]))
        return (f"Ton côté {worst['name']} mesure {_cm(worst['length_mm'])} cm au lieu de "
                f"{_cm(worst['expected_mm'])} cm : pars du 0 de la règle.")
    present = [e for e in elements if e.get("state") == "present"]
    missing = [e for e in elements if e.get("state") == "absent"]
    if present and missing:
        if len(missing) == 1:
            return f"Il manque {missing[0]['name']} : ton tracé est juste ailleurs."
        names = ", ".join(e["name"] for e in missing[:3])
        return f"Il manque {len(missing)} éléments du tracé ({names})."
    return ""


# ------------------------------------------------------------------ notation
def verdict(reading: dict | None, max_score: float, threshold: float | None) -> dict:
    """Décision de note à partir d'une lecture. Au-dessus du seuil : note appliquée
    (part du tracé retrouvée × max_score). Sans seuil réglé (pas encore calibré) ou
    en dessous : professeur, avec la proposition gardée comme score indicatif."""
    reading = reading or {}
    status = reading.get("status")
    try:
        confidence = float(reading.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    fraction = float(reading.get("fraction") or 0.0) if status == "read" else 0.0
    score = round(fraction * max_score, 6)
    out = {"max_score": max_score, "score": score, "confidence": confidence}
    if status not in ("read", "blank"):
        return {**out, "score": 0.0, "tier": "D", "reason_code": "construction_unreadable"}
    if threshold is None or not math.isfinite(confidence) or confidence < threshold:
        return {**out, "tier": "D", "reason_code": "construction_low_confidence"}
    if status == "blank":
        return {**out, "score": 0.0, "tier": "A", "reason_code": "construction_blank"}
    return {**out, "tier": "A" if fraction in (0.0, 1.0) else "B", "reason_code": "construction_cv"}

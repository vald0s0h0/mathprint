"""Banc d'essai synthétique de la géométrie à tracer (services/construction).

Fabrique des copies avec vérité terrain — tracé juste au stylo ou au crayon,
tremblé, avec arcs de compas, copie blanche, tracé partiel, décalé d'un
carreau, aux mauvaises dimensions, erreur typique, recalage de page imparfait,
copieur qui grise le saumon — en mode ancré (figure de départ imprimée) et
libre (case vide, figure posée n'importe où par l'élève).

Pour chaque seuil de confiance, une lecture est :
- auto juste : au-dessus du seuil et conforme à la vérité ;
- revue      : sous le seuil, envoyée au professeur (acceptable) ;
- ERREUR     : au-dessus du seuil mais fausse. Objectif : 0.

Le banc ne remplace pas le calibrage sur copies réelles
(scripts/calibrate_construction.py) : il dit seulement à partir de quel seuil
la lecture ne se trompe jamais sur ces cas fabriqués.

Usage (depuis backend/) :
    .venv/bin/python scripts/construction_bench.py [répétitions=4] [--details]
"""
import collections
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import construction as C     # noqa: E402

THRESHOLDS = (0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)


def _polar(cx, cy, r, deg):
    return [round(cx + r * math.cos(math.radians(deg)), 3), round(cy + r * math.sin(math.radians(deg)), 3)]


SPECS = {
    # --------------------------------------------------------------- ancrés
    "symetrie_axiale": {
        "cols": 16, "rows": 9,
        "points": {"A": [1, 1], "B": [6, 2], "C": [2, 7], "P": [8, 0], "Q": [8, 9],
                   "A'": [15, 1], "B'": [10, 2], "C'": [14, 7],
                   "A1": [9, 1], "B1": [14, 2], "C1": [10, 7]},
        "given": {"points": ["A", "B", "C"], "polygons": [["A", "B", "C"]],
                  "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
        "expected": {"polygons": [["A'", "B'", "C'"]]},
        "errors": [{"id": "translation", "trace": {"polygons": [["A1", "B1", "C1"]]}}]},
    "symetrie_centrale": {
        "cols": 16, "rows": 10,
        "points": {"A": [2, 6], "B": [6, 8], "C": [5, 4], "O": [8, 5],
                   "A'": [14, 4], "B'": [10, 2], "C'": [11, 6],
                   "A2": [14, 6], "B2": [10, 8], "C2": [11, 4]},
        "given": {"points": ["A", "B", "C", "O"], "polygons": [["A", "B", "C"]]},
        "expected": {"polygons": [["A'", "B'", "C'"]]},
        "errors": [{"id": "axiale", "trace": {"polygons": [["A2", "B2", "C2"]]}}]},
    "perpendiculaire": {
        "grid": False, "cols": 16, "rows": 9,
        "points": {"P": [0, 1], "Q": [16, 5], "A": [6, 7], "H": [7.29, 1.82]},
        "given": {"points": ["A"], "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
        "expected": {"lines": [["A", "H"]]},
        "tolerance_mm": 2.0},
    "parallele": {
        "grid": False, "cols": 16, "rows": 9,
        "points": {"P": [0, 1], "Q": [16, 5], "A": [5, 6], "A3": [9, 7]},
        "given": {"points": ["A"], "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
        "expected": {"lines": [["A", "A3"]]},
        "tolerance_mm": 2.0},
    "cercle_ancre": {
        "grid": False, "cols": 14, "rows": 10,
        "points": {"O": [7, 5], "A": [10, 7]},
        "given": {"points": ["O", "A"]},
        "expected": {"circles": [{"center": "O", "through": "A"}]}},
    "point_milieu": {
        "cols": 14, "rows": 6,
        "points": {"A": [2, 2], "B": [12, 4], "M": [7, 3]},
        "given": {"points": ["A", "B"], "segments": [["A", "B"]]},
        "expected": {"points": ["M"]}},
    "triangle_compas_ancre": {
        "grid": False, "cols": 16, "rows": 12,
        "points": {"A": [3, 6], "B": [13, 6], "C": [6.2, 11.3], "D": [6.2, 0.7]},
        "given": {"points": ["A", "B"], "segments": [["A", "B"]]},
        "expected": {"segments": [["A", "C"], ["B", "C"]]},
        "alternatives": [{"segments": [["A", "D"], ["B", "D"]]}]},
    # --------------------------------------------------------------- libres
    "libre_segment_6cm": {
        "cols": 16, "rows": 5, "given": {},
        "points": {"A": [2, 2], "B": [14, 2]},
        "expected": {"segments": [["A", "B"]]}},
    "libre_triangle_5_4_3": {     # AB = 5 cm, AC = 4 cm, BC = 3 cm (rectangle en C)
        "cols": 14, "rows": 10, "given": {},
        "points": {"A": [2, 2], "B": [12, 2], "C": [8.4, 6.8]},
        "expected": {"polygons": [["A", "B", "C"]]}},
    "libre_angle_50": {
        "cols": 16, "rows": 9, "given": {},
        "points": {"O": [2, 1.5], "X": [12, 1.5], "Y": _polar(2, 1.5, 9, 50), "Z": _polar(2, 1.5, 7, 130)},
        "expected": {"rays": [["O", "X"], ["O", "Y"]]},
        "errors": [{"id": "rapporteur_lu_a_l_envers", "trace": {"rays": [["O", "X"], ["O", "Z"]]}}]},
    "libre_cercle_3cm": {
        "cols": 16, "rows": 14, "given": {},
        "points": {"O": [8, 7], "R": [14, 7]},
        "expected": {"circles": [{"center": "O", "through": "R"}]}},
    "libre_carre_4cm": {
        "cols": 14, "rows": 12, "given": {},
        "points": {"A": [3, 2], "B": [11, 2], "C": [11, 10], "D": [3, 10]},
        "expected": {"polygons": [["A", "B", "C", "D"]]}},
}


def _partial(block: dict) -> dict | None:
    """Le même tracé, privé d'un élément : l'élève s'est arrêté avant."""
    for key in ("polygons", "segments", "lines", "rays", "circles", "points"):
        items = block.get(key) or []
        if key == "polygons" and items:
            names = items[0]
            return {"segments": [[names[k], names[k + 1]] for k in range(len(names) - 1)]}
        if len(items) > 1:
            return {**block, key: items[1:]}
    return None


def _really_off(spec: dict, offset) -> bool:
    """Un décalage n'est faux que s'il écarte vraiment chaque droite de sa place."""
    norm = (offset[0] ** 2 + offset[1] ** 2) ** 0.5
    limit = 2 * C.normalize(spec)["tolerance_mm"]
    for prim in C.primitives(spec["expected"], C.normalize(spec)):
        if prim.infinite:
            dx, dy = prim.b[0] - prim.a[0], prim.b[1] - prim.a[1]
            across = abs(offset[0] * dy - offset[1] * dx) / ((dx * dx + dy * dy) ** 0.5)
            if across * C.STEP_MM < limit:
                return False
    return norm * C.STEP_MM >= limit


def _scale_matters(spec: dict, scale: float) -> bool:
    """Une figure agrandie n'est fausse que si une longueur ou un rayon compte, et
    s'écarte vraiment : en mode ancré, un sommet déplacé de plus de deux tolérances ;
    en mode libre, une longueur fausse d'au moins LENGTH_BAD_MM."""
    norm = C.normalize(spec)
    prims = C.primitives(spec["expected"], norm)
    sized = [p for p in prims if (p.kind == "seg" and not p.infinite) or p.kind == "circle"]
    if not sized:
        return False
    if C.is_free(norm):
        longest = max(C.math.dist(p.a, p.b) if p.kind == "seg" else 2 * C.math.pi * p.radius / 4
                      for p in sized) * C.STEP_MM
        return abs(scale - 1) * longest >= C.LENGTH_BAD_MM + 1
    center = sum((C.np.asarray(p.a) for p in prims), C.np.zeros(2)) / len(prims)
    moved = max(C.np.linalg.norm(C.np.asarray(p.a) - center) for p in prims) * abs(scale - 1) * C.STEP_MM
    return moved >= 2 * norm["tolerance_mm"]


def scenarios(spec: dict):
    """(étiquette, kwargs de synthetic_page, vérité) ; vérité ∈ juste | blanc | faux | erreur:<id>."""
    free = C.is_free(spec)
    out = [("juste-stylo", {"trace": "expected", "style": "pen"}, "juste"),
           ("juste-crayon", {"trace": "expected", "style": "pencil"}, "juste"),
           ("juste-tremble", {"trace": "expected", "jitter_mm": 0.9}, "juste"),
           ("juste-arcs-compas", {"trace": "expected", "arcs": True}, "juste"),
           ("juste-recalage", {"trace": "expected", "shift_mm": (0.9, -0.7)}, "juste"),
           ("juste-copieur-gris", {"trace": "expected", "copier_gray": True}, "juste"),
           ("blanc", {"trace": "blank"}, "blanc"),
           ("blanc-copieur-gris", {"trace": "blank", "copier_gray": True}, "blanc")]
    if not free:
        for label, offset in (("decale-1-carreau", (1, 0)), ("decale-1-carreau-vertical", (0, 1)),
                              ("decale-diagonal", (1, -1))):
            if _really_off(spec, offset):
                out.append((label, {"trace": "expected", "offset_units": offset}, "faux"))
    for scale in (0.9, 1.12, 0.8):
        if _scale_matters(spec, scale):
            out.append((f"dimensions-{round(scale * 100)}%", {"trace": "expected", "scale": scale}, "faux"))
    partial = _partial(spec["expected"])
    if partial:
        out.append(("partiel", {"trace": partial}, "faux"))
    for i in range(len(spec.get("alternatives") or [])):
        out.append((f"alternative-{i}", {"trace": f"alternative:{i}"}, "juste"))
    for err in spec.get("errors") or []:
        out.append(("erreur-typique", {"trace": f"error:{err['id']}"}, f"erreur:{err['id']}"))
    return out


def judge(got: dict, truth: str, threshold: float) -> str:
    if got.get("confidence", 0) < threshold:
        return "revue"
    if truth == "blanc":
        return "juste" if got["status"] == "blank" else "ERREUR"
    if truth == "juste":
        return "juste" if got["status"] == "read" and got["fraction"] >= 1 else "ERREUR"
    if truth.startswith("erreur:"):
        return "ERREUR" if got.get("fraction", 0) >= 1 or got["status"] == "blank" else "juste"
    # faux : une lecture au-dessus du seuil ne doit jamais accorder tout le tracé
    return "ERREUR" if got.get("fraction", 0) >= 1 or got["status"] == "blank" else "juste"


def main(reps: int = 4, details: bool = False) -> int:
    readings = []
    for name, spec in SPECS.items():
        problems = C.problems(spec)
        if problems:
            print(f"{name} : spec refusée : {problems}")
            return 2
        for label, kwargs, truth in scenarios(spec):
            for seed in range(reps):
                img, meta = C.synthetic_page(spec, seed=seed, **kwargs)
                readings.append((name, label, seed, truth, C.read(img, meta)))
    print(f"{len(readings)} lectures synthétiques")
    print("seuil   auto juste   revue   ERREURS")
    safe = None
    for t in THRESHOLDS:
        counts = collections.Counter(judge(r[4], r[3], t) for r in readings)
        print(f"{t:5.2f}   {counts['juste']:5d} ({counts['juste'] / len(readings):4.0%})   "
              f"{counts['revue']:5d}   {counts['ERREUR']:4d}")
        if counts["ERREUR"] == 0 and safe is None:
            safe = t
    print(f"Plus petit seuil sans erreur sur ce banc : {safe if safe is not None else 'aucun'}")
    t = safe if safe is not None else THRESHOLDS[-1]
    by_label = collections.defaultdict(collections.Counter)
    for name, label, _seed, truth, got in readings:
        by_label[label][judge(got, truth, t)] += 1
    print(f"\nDétail au seuil {t} :")
    for label in sorted(by_label):
        row = by_label[label]
        print(f"  {label:28s} auto juste {row['juste']:3d}  revue {row['revue']:3d}  ERREUR {row['ERREUR']:3d}")
    by_spec = collections.defaultdict(collections.Counter)
    for name, label, _seed, truth, got in readings:
        if truth == "juste" and "copieur" not in label:
            by_spec[name][judge(got, truth, t)] += 1
    print(f"\nTracés justes notés automatiquement, par exercice (seuil {t}) :")
    for name, row in by_spec.items():
        total = sum(row.values())
        print(f"  {name:28s} {row['juste']:3d}/{total}")
    worst = THRESHOLDS[0]
    for name, label, seed, truth, got in readings:
        verdict = judge(got, truth, worst)
        if details or (verdict == "ERREUR" and got.get("confidence", 0) >= (safe or 1)):
            print(f"  {name}/{label}/{seed} vérité={truth} : {got.get('status')} {got.get('fraction')} "
                  f"conf {got.get('confidence')} {got.get('reasons')} {got.get('error_id')}")
    return 0 if safe is not None else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(int(args[0]) if args else 4, "--details" in sys.argv))

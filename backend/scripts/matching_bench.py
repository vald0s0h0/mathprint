"""Banc d'essai synthétique de la lecture « points à relier » (worker_cv).

Fabrique des copies avec vérité terrain — traits à la règle, à main levée,
ondulés, au crayon, repassés, redessinés, effacés au blanco (mini-traits aux
croisements), 3 droites concourantes, recalage décalé, ratures (gribouillis,
hachures), deux partenaires pour un point, trait inachevé — et mesure la
lecture : auto juste / revue / ERREUR (lecture certaine mais fausse, ou
certaine sur un cas qui exige la revue). Objectif : 0 ERREUR.

Usage (depuis backend/) :
    .venv/bin/python scripts/matching_bench.py [N=40] [black|salmon|graycopy] [scénarios|all] [graine0]
    MATCH_OVERRIDES=MATCH_DENSE_PX=150 ... : essaie un seuil sans toucher au code
    TRANSPOSE=1 ... : disposition horizontale (points du haut reliés au bas)

Complète, sans la remplacer, la calibration sur copies réelles
(scripts/calibrate_matching.py) : les seuils de worker_cv y ont été réglés.
"""
import collections
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import cv2                                        # noqa: E402
import numpy as np                                # noqa: E402

from app.services import worker_cv as W           # noqa: E402
from app.services.pdfgen import PAGE_H            # noqa: E402

for _kv in filter(None, os.environ.get("MATCH_OVERRIDES", "").split(",")):
    _k, _v = _kv.split("=")
    setattr(W, _k, type(getattr(W, _k))(_v))

DPI = 200
S = DPI / 72.0
MM = 72 / 25.4            # points par mm
PX_MM = DPI / 25.4        # pixels par mm
PAGE_H_PT = 841.89
IMG_W, IMG_H = 1700, int(PAGE_H_PT * S)

INKS = {
    "blue": (150, 70, 35),
    "black": (40, 35, 30),
    "pencil": (125, 125, 125),
    "lightpencil": (160, 160, 160),
}


def pt_to_px(x, y):
    return x * S, (PAGE_H_PT - y) * S


def layout(n, *, lane_mm=14.0, row_mm=6.5, rng=None, uneven=False):
    """Pastilles comme pdfgen._draw_matching_zone (dot 2,2 mm, couloir)."""
    p = 2.2 * MM
    ldot = 393.0
    rdot = ldot + p + lane_mm * MM
    top = 600.0
    left, right = [], []
    for i in range(n):
        rh = row_mm * MM
        if uneven and rng is not None and rng.random() < 0.35:
            rh = rng.uniform(8.0, 12.0) * MM
        cy = top - rh / 2
        left.append({"index": i, "x_pt": ldot, "y_pt": cy - p / 2, "w_pt": p, "h_pt": p})
        right.append({"index": i, "x_pt": rdot, "y_pt": cy - p / 2, "w_pt": p, "h_pt": p})
        top -= rh
    return left, right


def center(p):
    return np.array(pt_to_px(p["x_pt"] + p["w_pt"] / 2, p["y_pt"] + p["h_pt"] / 2))


def stroke_path(A, B, rng, kind="hand", off=4.0, overshoot=(-6, 8)):
    """Polyligne d'un trait de A vers B (pixels)."""
    A = A + rng.normal(0, off, 2)
    B = B + rng.normal(0, off, 2)
    d = B - A
    L = np.linalg.norm(d)
    u = d / L
    nrm = np.array([-u[1], u[0]])
    B = B + u * rng.uniform(*overshoot)
    A = A - u * rng.uniform(-3, 4)
    d = B - A
    L = np.linalg.norm(d)
    t = np.linspace(0, 1, max(20, int(L / 2)))
    disp = np.zeros_like(t)
    if kind in ("hand", "wavy"):
        bow = rng.normal(0, 5.0 if kind == "hand" else 7.0)
        bow = float(np.clip(bow, -15, 15))
        disp += bow * np.sin(math.pi * t)
        amp = 1.5 if kind == "hand" else 4.0
        for _ in range(2):
            f = rng.uniform(1, 3.5)
            disp += rng.uniform(0.3, 1) * amp * np.sin(2 * math.pi * f * t + rng.uniform(0, 6.3)) \
                * np.sin(math.pi * t) ** 0.5
        if rng.random() < 0.3:          # crochet au poser du stylo
            disp += rng.normal(0, 3) * np.exp(-t / 0.05)
        trem = np.cumsum(rng.normal(0, 0.35, len(t)))
        trem -= np.linspace(trem[0], trem[-1], len(t))
        disp += trem
    else:  # règle
        disp += rng.normal(0, 0.2, len(t))
    return A[None, :] + t[:, None] * d[None, :] + disp[:, None] * nrm[None, :]


def draw_path(img, pts, rng, ink="blue", thick=None, skips=0):
    color = INKS[ink]
    thick = thick or int(rng.choice([2, 2, 3, 3, 4]))
    segs = [pts]
    if skips:
        segs = []
        cut = sorted(rng.choice(np.arange(5, len(pts) - 5), size=skips, replace=False))
        prev = 0
        for c in cut:
            g = int(rng.integers(1, 4))
            segs.append(pts[prev:c])
            prev = c + g
        segs.append(pts[prev:])
    for s in segs:
        if len(s) >= 2:
            cv2.polylines(img, [np.round(s).astype(np.int32)], False, color, thick, cv2.LINE_AA)
    if ink in ("pencil", "lightpencil"):
        mask = np.zeros(img.shape[:2], np.uint8)
        for s in segs:
            if len(s) >= 2:
                cv2.polylines(mask, [np.round(s).astype(np.int32)], False, 255, thick + 1)
        ys, xs = np.nonzero(mask)
        k = rng.random(len(ys)) < 0.25
        img[ys[k], xs[k]] = np.minimum(255, img[ys[k], xs[k]].astype(int) + 60).astype(np.uint8)


def _dist_to_paths(p, paths):
    best = 1e9
    for q in paths:
        best = min(best, float(np.min(np.linalg.norm(q - p[None, :], axis=1))))
    return best


def blanco(img, wrong_pts, keep_paths, dot_centers, rng, outline=True):
    """Bande de correcteur sur `wrong_pts`, interrompue près des traits gardés
    (l'élève les évite -> mini-traits résiduels aux intersections) et des
    points imprimés."""
    keep_r = rng.uniform(5, 11)
    width = int(rng.uniform(9, 15))
    on = []
    for p in wrong_pts:
        near_keep = _dist_to_paths(p, keep_paths) < keep_r if keep_paths else False
        near_dot = min(np.linalg.norm(p - c) for c in dot_centers) < rng.uniform(8, 13)
        on.append(not (near_keep or near_dot))
    on = np.array(on)
    runs, start = [], None
    for k, v in enumerate(on):
        if v and start is None:
            start = k
        if (not v or k == len(on) - 1) and start is not None:
            end = k if not v else k + 1
            if end - start >= 2:
                runs.append(wrong_pts[start:end])
            start = None
    for r in runs:
        cv2.polylines(img, [np.round(r).astype(np.int32)], False, (247, 248, 249), width, cv2.LINE_AA)
        if outline and rng.random() < 0.5:
            d = r[-1] - r[0]
            if np.linalg.norm(d) > 1:
                u = d / np.linalg.norm(d)
                nrm = np.array([-u[1], u[0]]) * width / 2
                for sgn in (-1, 1):
                    cv2.polylines(img, [np.round(r + sgn * nrm).astype(np.int32)], False,
                                  (222, 222, 222), 1, cv2.LINE_AA)


def scribble(img, pts, rng, ink="blue", style="gribouillis"):
    """Rature par-dessus un trait : gribouillis dense ou hachures en travers
    (l'élève l'annule) ; « tick » : petite marque isolée (issue libre)."""
    k0 = int(len(pts) * rng.uniform(0.3, 0.7))
    mid = pts[k0]
    d = pts[-1] - pts[0]
    u = d / np.linalg.norm(d)
    nv = np.array([-u[1], u[0]])
    thick = int(rng.choice([2, 3]))
    if style == "gribouillis":
        span = rng.uniform(40, 70)
        amp = rng.uniform(10, 18)
        m = int(rng.integers(8, 15))
        zz = [mid + u * (-span / 2 + span * k / m + rng.normal(0, 2))
              + nv * ((1 if k % 2 else -1) * amp + rng.normal(0, 3)) for k in range(m + 1)]
        cv2.polylines(img, [np.round(np.array(zz)).astype(np.int32)], False, INKS[ink], thick, cv2.LINE_AA)
    elif style == "hachures":
        m = int(rng.integers(4, 8))
        gap = rng.uniform(5, 8)
        ang = np.radians(rng.uniform(55, 90)) * rng.choice([-1, 1])
        dirv = u * np.cos(ang) + nv * np.sin(ang)
        for k in range(m):
            c = mid + u * gap * (k - m / 2)
            half = rng.uniform(10, 15)
            cv2.line(img, tuple(np.round(c - dirv * half).astype(int)),
                     tuple(np.round(c + dirv * half).astype(int)), INKS[ink], thick, cv2.LINE_AA)
    else:  # tick
        ang = np.radians(rng.uniform(40, 90))
        dirv = u * np.cos(ang) + nv * np.sin(ang)
        half = rng.uniform(5, 9)
        cv2.line(img, tuple(np.round(mid - dirv * half).astype(int)),
                 tuple(np.round(mid + dirv * half).astype(int)), INKS[ink], 2, cv2.LINE_AA)


def draw_dots(img, centers, style):
    for c in centers:
        cx, cy = int(round(c[0])), int(round(c[1]))
        if style == "black":
            cv2.circle(img, (cx, cy), int(round(0.8 * PX_MM)), (25, 25, 25), -1, cv2.LINE_AA)
        elif style == "salmon":
            cv2.circle(img, (cx, cy), int(round(1.1 * PX_MM)), (168, 183, 245), 2, cv2.LINE_AA)
        elif style == "graycopy":
            cv2.circle(img, (cx, cy), int(round(1.1 * PX_MM)), (175, 175, 175), 2, cv2.LINE_AA)


def draw_labels(img, Lc, Rc, rng):
    words = ["3 cm", "x+2", "45°", "a=b", "7,5", "angle aigu", "2x", "triangle"]
    for cs, side in ((Lc, -1), (Rc, 1)):
        for c in cs:
            w = str(rng.choice(words))
            (tw, th), _ = cv2.getTextSize(w, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
            x = c[0] - 30 - tw if side < 0 else c[0] + 30
            cv2.putText(img, w, (int(x), int(c[1] + th / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                        (30, 30, 30), 2, cv2.LINE_AA)


def degrade(img, rng):
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.4, 0.9))
    noise = rng.normal(0, rng.uniform(2, 6), img.shape)
    img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.uniform(55, 90))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


SCENARIOS = [
    ("ruler", "auto"),
    ("hand", "auto"),
    ("wavy", "auto"),
    ("double", "auto"),
    ("redraw", "either"),
    ("blanco", "auto"),
    ("concurrent", "auto"),
    ("partial", "auto"),
    ("blank", "auto"),
    ("pencil", "auto"),
    ("misreg", "auto"),
    ("skips", "auto"),
    ("two_partners", "review"),
    ("scribble", "review"),
    ("hatch", "review"),
    ("tick", "either"),
    ("half_line", "either"),
]


def make(scenario, seed, dot_style="black", flatten=None):
    rng = np.random.default_rng(seed)
    n = int(rng.choice([3, 4, 4, 5, 6]))
    lane = float(rng.choice([8.0, 10.0, 14.0, 14.0, 14.0]))
    left, right = layout(n, lane_mm=lane, rng=rng, uneven=rng.random() < 0.4)
    img = np.full((IMG_H, IMG_W, 3), 255, np.uint8)
    mis = 6.0 if scenario == "misreg" else 2.0       # recalage de page imparfait
    shift = rng.uniform(-mis, mis, 2)
    Lc = [center(p) + shift + rng.normal(0, 0.7, 2) for p in left]
    Rc = [center(p) + shift + rng.normal(0, 0.7, 2) for p in right]
    draw_labels(img, Lc, Rc, rng)
    draw_dots(img, Lc + Rc, dot_style)

    perm = list(rng.permutation(n))
    if scenario == "concurrent":
        perm = list(range(n))[::-1]
        left, right = layout(n, lane_mm=lane)        # rangées égales -> concourantes
        Lc = [center(p) + shift for p in left]
        Rc = [center(p) + shift for p in right]
        img[:] = 255
        draw_labels(img, Lc, Rc, rng)
        draw_dots(img, Lc + Rc, dot_style)
    pairs = [(i, int(perm[i])) for i in range(n)]
    if scenario == "partial":
        drop = set(rng.choice(n, size=int(rng.integers(1, n)), replace=False).tolist())
        pairs = [p for p in pairs if p[0] not in drop]
    if scenario == "blank":
        pairs = []

    kind = {"ruler": "ruler", "wavy": "wavy", "concurrent": "ruler"}.get(scenario, "hand")
    if scenario in ("double", "redraw", "blanco", "partial", "misreg", "skips", "two_partners",
                    "scribble", "hatch", "tick", "half_line", "pencil"):
        kind = str(rng.choice(["ruler", "hand", "hand"]))
    ink = str(rng.choice(["blue", "blue", "black"]))
    if scenario == "pencil":
        ink = str(rng.choice(["pencil", "lightpencil"]))
    paths = {(i, j): stroke_path(Lc[i], Rc[j], rng, kind=kind) for (i, j) in pairs}

    expected = "auto"
    if scenario == "blanco":
        i = int(rng.integers(0, n))
        jw = int(rng.choice([j for j in range(n) if j != perm[i]]))
        wrong = stroke_path(Lc[i], Rc[jw], rng, kind=kind)
        draw_path(img, wrong, rng, ink=ink)
        blanco(img, wrong, list(paths.values()), Lc + Rc, rng)
    for (i, j), pts in paths.items():
        draw_path(img, pts, rng, ink=ink, skips=int(rng.integers(1, 4)) if scenario == "skips" else 0)
        if scenario == "double" and rng.random() < 0.7:
            # trait repassé : le second passage suit le premier (écart ≤ ~0,6 mm)
            t = np.linspace(0, 1, len(pts))[:, None]
            d = pts[-1] - pts[0]
            nrm = np.array([-d[1], d[0]]) / np.linalg.norm(d)
            wob = rng.uniform(-3, 3) + rng.uniform(1, 4) * np.sin(np.pi * t * rng.uniform(1, 3)
                                                                  + rng.uniform(0, 6.3))
            again = pts + wob * nrm[None, :] + rng.normal(0, 1.5, 2)[None, :]
            draw_path(img, again, rng, ink=ink)
        if scenario == "redraw" and rng.random() < 0.7:
            again = stroke_path(Lc[i], Rc[j], rng, kind=str(rng.choice(["ruler", "hand"])), off=3.0)
            draw_path(img, again, rng, ink=ink)
    truth = sorted([list(p) for p in pairs])
    if scenario == "two_partners":
        i = int(rng.integers(0, n))
        jw = int(rng.choice([j for j in range(n) if j != perm[i]]))
        draw_path(img, stroke_path(Lc[i], Rc[jw], rng, kind=kind), rng, ink=ink)
        expected = "review"
    if scenario in ("scribble", "hatch", "tick"):
        k = list(paths)[int(rng.integers(0, len(paths)))]
        style = {"scribble": "gribouillis", "hatch": "hachures", "tick": "tick"}[scenario]
        scribble(img, paths[k], rng, ink=ink, style=style)
        expected = "either" if scenario == "tick" else "review"
    if scenario == "half_line":
        i = int(rng.integers(0, n))
        jw = int(rng.choice([j for j in range(n) if j != perm[i]]))
        pts = stroke_path(Lc[i], Rc[jw], rng, kind=kind)
        draw_path(img, pts[: int(len(pts) * rng.uniform(0.35, 0.6))], rng, ink=ink)
        expected = "either"

    img = degrade(img, rng)
    if flatten is not None:
        img = flatten(img)
    return {"img": img, "left": left, "right": right, "truth": truth,
            "expected": expected, "scenario": scenario, "n": n, "lane": lane}


def seed_of(name, k):
    return (sum(ord(c) * 31 ** i for i, c in enumerate(name)) * 1000 + k) % (2**31)


def transpose(s):
    """Disposition horizontale : image transposée, colonne gauche -> rangée du
    haut, colonne droite -> rangée du bas (étiquettes au-dessus/en dessous)."""
    S = W.SCALE

    def tr(p):
        cx = (p["x_pt"] + p["w_pt"] / 2) * S
        cy = (PAGE_H - p["y_pt"] - p["h_pt"] / 2) * S
        return {**p, "x_pt": cy / S - p["w_pt"] / 2, "y_pt": PAGE_H - cx / S - p["h_pt"] / 2}
    img = np.ascontiguousarray(s["img"].transpose(1, 0, 2))
    return {**s, "img": img, "left": [tr(p) for p in s["left"]], "right": [tr(p) for p in s["right"]]}


def one(args):
    style, name, k = args
    seed = seed_of(name, k)
    s = make(name, seed, dot_style=style, flatten=W.flatten_background)
    if os.environ.get("TRANSPOSE"):
        s = transpose(s)
    diag = {}
    pairs, conf = W.detect_matching(s["img"], s["left"], s["right"], diag=diag)
    got = sorted(pairs) if pairs is not None else None
    if conf >= 0.9:
        outcome = "ERROR" if s["expected"] == "review" or got != s["truth"] else "auto_ok"
    else:
        outcome = "review"
    return name, seed, outcome, got, s["truth"], diag.get("reason")


def main(argv):
    N = int(argv[0]) if argv else 40
    style = argv[1] if len(argv) > 1 else "black"
    only = argv[2].split(",") if len(argv) > 2 and argv[2] != "all" else None
    first = int(argv[3]) if len(argv) > 3 else 0
    jobs = [(style, name, k) for name, _ in SCENARIOS
            if not only or name in only for k in range(first, first + N)]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2)) as ex:
        results = list(ex.map(one, jobs, chunksize=4))
    stats = collections.defaultdict(collections.Counter)
    reasons = collections.defaultdict(collections.Counter)
    fails = []
    for name, seed, outcome, got, truth, reason in results:
        stats[name][outcome] += 1
        if outcome == "review":
            reasons[name][reason] += 1
        if outcome == "ERROR":
            fails.append((name, seed, got, truth))
    total = collections.Counter()
    print(f"points={style} N={N} ({time.time() - t0:.0f} s)")
    for name, expected in SCENARIOS:
        if name in stats:
            c = stats[name]
            total.update(c)
            print(f"  {name:13s} [{expected:6s}] auto_ok={c['auto_ok']:4d} review={c['review']:4d} "
                  f"ERROR={c['ERROR']:3d}   {dict(reasons[name]) if reasons[name] else ''}")
    print(f"  TOTAL auto_ok={total['auto_ok']} review={total['review']} ERROR={total['ERROR']}")
    for f in fails:
        print("  ERREUR", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

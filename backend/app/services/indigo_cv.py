"""Indigo — vision par ordinateur : lecture de la couleur d'un badge/titre.

Le manuel encode le TYPE et la DIFFICULTÉ d'un exercice par une couleur :
- pour un exercice normal : la couleur du CERCLE-BADGE portant le numéro
  (exercice = teal, flash = orange, énigme = rouge ; « expert » a la même
  couleur de badge qu'exercice, on le reconnaît à son FOND vert d'eau pâle,
  lu au coin bas-droit du numéro — cf. is_expert_background) ;
- pour un PROBLÈME : la couleur n'est pas dans le badge mais dans le TITRE
  (facile = orange, moyen = vert, difficile = gris/noir). On sait que c'est un
  problème par la présence de tags (« Raisonner », « Calculer »…), détectée
  côté texte, pas ici.

Valeurs de référence : relevées par l'utilisateur au colorimètre numérique du
Mac, en 0–1 par canal (multipliées par 255 ci-dessous). La classification se
fait par TEINTE (robuste à l'échelle et à la luminosité) avec garde-fous de
saturation/valeur ; les seuils sont exposés pour recalibrage.
"""
from __future__ import annotations

import colorsys
import logging

import cv2
import numpy as np

logger = logging.getLogger("app.indigo")

# --- couleurs de référence (RVB 0-255), depuis les relevés colorimètre (×255)
REF_RGB = {
    "exercice": (40, 180, 185),   # teal   — 0.157 0.706 0.726
    "flash":    (240, 134, 47),   # orange — 0.941 0.525 0.184
    "enigme":   (240, 70, 43),    # rouge  — 0.941 0.274 0.169
    "facile":   (244, 133, 42),   # orange — 0.957 0.521 0.165  (titre problème)
    "moyen":    (118, 198, 58),   # vert   — 0.463 0.776 0.227  (titre problème)
    "difficile": (35, 31, 32),    # gris   — 0.137 0.122 0.125  (titre problème)
}

# type de badge -> difficulté normalisée 1-3 (facile / moyen / difficile, cf.
# exercise_gen.DIFFICULTY_LEVELS). Le manuel n'encode pas davantage de nuances :
# un flash est une application directe, un expert et une énigme sont tous deux
# la marche haute.
DIFFICULTY_BY_TYPE = {"flash": 1, "exercice": 2, "expert": 3, "enigme": 3}
DIFFICULTY_BY_LEVEL = {"facile": 1, "moyen": 2, "difficile": 3}

# garde-fous : en dessous, on considère qu'il n'y a pas de couleur exploitable
MIN_SAT = 0.28          # saturation minimale d'un badge coloré
MIN_COVERAGE = 0.002    # part minimale de pixels saturés dans le crop
# Énigme = badge ROUGE FRANC uniquement (seul indicateur d'énigme, cf.
# utilisateur). Tolérance de teinte serrée autour du rouge de référence (~8°)
# pour EXCLURE l'orange flash (~27°) : mesuré sur le manuel, une énigme est à
# ~9-10°, un flash à ~27° → 14° sépare proprement les deux.
ENIGME_HUE_TOL = 14.0

# Fond « Expert » : les exercices experts ont la MÊME couleur de badge qu'un
# exercice normal (teal) mais un FOND de carte vert d'eau très pâle. On le lit
# sur le pixel en bas à droite de la boîte du numéro (fond de carte, hors texte).
# Relevé colorimètre : r0.881 v0.952 b0.952 -> RVB (225, 243, 243).
EXPERT_BG_RGB = (225, 243, 243)
EXPERT_BG_TOL = 16      # blanc (255,255,255) reste hors tolérance (Δr = 30)


def _median_rgb(crop_bgr: np.ndarray, x: int, y: int, r: int = 3) -> tuple | None:
    """Couleur médiane d'un petit carré (2r+1) autour de (x, y) — robuste au
    bruit/anti-aliasing. `crop_bgr` est en BGR (OpenCV)."""
    h, w = crop_bgr.shape[:2]
    patch = crop_bgr[max(0, y - r):min(h, y + r + 1), max(0, x - r):min(w, x + r + 1)]
    if patch.size == 0:
        return None
    b, g, rr = (float(np.median(patch[..., i])) for i in range(3))
    return (round(rr), round(g), round(b))


def is_expert_background(crop_bgr: np.ndarray, number_box: dict | None) -> bool:
    """Le fond de carte au coin bas-droit du numéro est-il le vert d'eau
    « Expert » ? (distingue un expert d'un exercice normal, même badge teal)."""
    if not number_box:
        return False
    rgb = _median_rgb(crop_bgr, int(number_box.get("x1", 0)), int(number_box.get("y1", 0)))
    if rgb is None:
        return False
    return all(abs(rgb[i] - EXPERT_BG_RGB[i]) <= EXPERT_BG_TOL for i in range(3))


def _hue_deg(rgb) -> float:
    r, g, b = (c / 255.0 for c in rgb)
    h, _s, _v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


_REF_HUE = {k: _hue_deg(v) for k, v in REF_RGB.items()}


def read_saturated_color(crop_bgr: np.ndarray, box: dict | None = None) -> dict:
    """Couleur dominante des pixels SATURÉS d'une région (le badge/titre se
    détache du texte noir sur fond blanc). `box` (x0,y0,x1,y1 en pixels du
    crop) restreint la zone — utile pour viser le badge près du numéro."""
    img = crop_bgr
    if box:
        x0, y0 = max(0, int(box["x0"])), max(0, int(box["y0"]))
        x1, y1 = int(box["x1"]), int(box["y1"])
        img = crop_bgr[y0:y1, x0:x1]
    if img.size == 0:
        return {"rgb": None, "hue_deg": None, "sat": 0.0, "value": 0.0, "coverage": 0.0}

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    mask = (s >= MIN_SAT) & (v >= 0.25)
    coverage = float(mask.mean())
    if coverage < MIN_COVERAGE:
        return {"rgb": None, "hue_deg": None, "sat": float(s.mean()),
                "value": float(v.mean()), "coverage": coverage}

    # médiane des pixels saturés (robuste aux anti-aliasing / bords)
    sel = img[mask]  # BGR
    b, g, r = (float(np.median(sel[:, i])) for i in range(3))
    rgb = (round(r), round(g), round(b))
    return {"rgb": list(rgb), "hue_deg": _hue_deg(rgb), "sat": float(s[mask].mean()),
            "value": float(v[mask].mean()), "coverage": coverage}


def is_enigme_color(color: dict) -> bool:
    """La couleur lue est-elle un ROUGE FRANC de badge d'énigme ? (teinte proche
    du rouge de référence, bien saturée). N'a de valeur que si la couleur vient
    du BADGE lui-même — cf. `analyze` (une figure rouge captée par le repli
    plein-crop ne doit PAS déclencher une énigme)."""
    hue = color.get("hue_deg")
    if hue is None or color.get("coverage", 0) < MIN_COVERAGE or color.get("sat", 0) < MIN_SAT:
        return False
    return _hue_dist(hue, _REF_HUE["enigme"]) <= ENIGME_HUE_TOL


def classify(color: dict, is_probleme: bool, from_badge: bool = True) -> tuple[str, int]:
    """→ (badge_type, difficulty 1-3). `badge_type` ∈ {exercice, flash, enigme,
    probleme}. Un badge ROUGE l'emporte sur tout (énigme, seul indicateur =
    couleur du badge), à condition que la couleur vienne bien du badge
    (`from_badge`). Sinon : problème (difficulté = couleur du titre) ou badge
    ordinaire (teal=exercice, orange=flash)."""
    if from_badge and is_enigme_color(color):
        return "enigme", DIFFICULTY_BY_TYPE["enigme"]
    if is_probleme:
        level = _classify_title(color)
        return "probleme", DIFFICULTY_BY_LEVEL.get(level, 2)
    btype = _classify_badge(color)
    return btype, DIFFICULTY_BY_TYPE.get(btype, 2)


def _classify_badge(color: dict) -> str:
    """exercice (teal ~182°) | flash (orange ~27°). L'énigme (rouge) est traitée
    à part (cf. `classify`) : ici on tranche seulement entre teal et orange, au
    plus proche. Défaut prudent : exercice (le cas de loin le plus fréquent)."""
    hue = color.get("hue_deg")
    if hue is None or color.get("coverage", 0) < MIN_COVERAGE:
        return "exercice"
    if _hue_dist(hue, _REF_HUE["exercice"]) <= _hue_dist(hue, _REF_HUE["flash"]):
        return "exercice"
    return "flash"


def _classify_title(color: dict) -> str:
    """facile (orange) | moyen (vert) | difficile (gris/noir). Le gris n'a pas
    de teinte fiable : on le détecte par faible saturation."""
    hue = color.get("hue_deg")
    if hue is None or color.get("sat", 0) < MIN_SAT:
        return "difficile"
    cands = {k: _REF_HUE[k] for k in ("facile", "moyen")}
    best = min(cands, key=lambda k: _hue_dist(hue, cands[k]))
    # garde-fou : si la teinte est loin des deux (ni orange ni vert), gris
    if _hue_dist(hue, _REF_HUE[best]) > 40:
        return "difficile"
    return best


def analyze(crop_bgr: np.ndarray, is_probleme: bool,
            number_box: dict | None = None) -> dict:
    """Analyse complète d'un crop d'exercice : couleur de badge/titre →
    type + difficulté. La détection d'icône calculette n'est pas fiable sans
    gabarit ; on renvoie le défaut « autorisee », l'admin ajuste dans l'onglet."""
    from_badge = True
    color = read_saturated_color(crop_bgr, box=number_box)
    if (color.get("rgb") is None) and number_box is not None:
        # rien près du numéro : réessaie sur tout le crop (titre décalé, etc.).
        # Couleur alors lue AILLEURS que sur le badge → on interdit l'énigme
        # (sinon une figure rouge dans le crop passerait pour une énigme).
        color = read_saturated_color(crop_bgr)
        from_badge = False
    badge_type, difficulty = classify(color, is_probleme, from_badge=from_badge)
    # fond vert d'eau => Expert (même badge teal qu'un exercice normal). Ne
    # s'applique qu'à un exercice ORDINAIRE (badge teal) : ni un problème, ni une
    # énigme (badge rouge), ni un flash ne deviennent « expert ».
    if badge_type == "exercice" and is_expert_background(crop_bgr, number_box):
        badge_type, difficulty = "expert", DIFFICULTY_BY_TYPE["expert"]
    return {
        "badge_type": badge_type,
        "difficulty": difficulty,
        "calculator": "autorisee",
        "color": color,
    }

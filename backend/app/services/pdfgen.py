"""Génération documentaire (§5) : copies A4, QR + fiduciels signés, zones
dropout, overlay.

Design des sujets :
- en-tête compact de la hauteur du QR : Note, Appréciation et identité sont
  alignées sur sa géométrie ; la classe forme un grand filigrane derrière le
  nom, le titre et la date ;
- chaque exercice dans une carte à coins arrondis avec ombre portée, badge
  numéroté coloré par difficulté (1 bleu -> 5 rouge), l'énoncé démarrant sur
  la même ligne que le badge ;
- rappels de leçon dans un cadre distinct (fond ambre clair, icône livre) ;
- zones de réponse ÉLÈVE en rouge saumon clair (dropout, supprimé avant OCR) ;
- bande de correction en pointillés gris sous chaque exercice : réservée à
  l'overlay, l'élève n'y écrit pas (distinction visuelle demandée) ;
- QCM compacts : cases en ligne, retour à la ligne automatique ;
- deux colonnes d'exercices, mise en page compacte.

La géométrie des 4 marqueurs est FIGÉE : worker_cv s'y réfère pour
l'homographie. Un seul QR (24 mm, haut droit) porte l'identité de page,
signée HMAC. Les 3 autres coins (TL/BL/BR, 11 mm) portent chacun un fiduciel
AprilTag (famille 16h5) dédié au SEUL placement géométrique (translation,
rotation, échelle) — un type de tag par coin, identique sur toutes les pages.
"""
import io
import json
import re
from contextvars import ContextVar
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import qrcode
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont

from ..config import settings
from . import scoring
from . import statement as statement_mod
from .runtime_settings import DEFAULT_TEMPLATES

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_DYSLEXIC_FONTS = {
    "regular": "OpenDyslexic",
    "bold": "OpenDyslexic-Bold",
    "italic": "OpenDyslexic-Italic",
}
for _style, _filename in {
    "regular": "OpenDyslexic-Regular.ttf",
    "bold": "OpenDyslexic-Bold.ttf",
    "italic": "OpenDyslexic-Italic.ttf",
}.items():
    pdfmetrics.registerFont(TTFont(_DYSLEXIC_FONTS[_style], str(_FONT_DIR / _filename)))

_USE_DYSLEXIC: ContextVar[bool] = ContextVar("pdf_use_dyslexic", default=False)
# Les métriques moyennes d'OpenDyslexic sont environ 15 % plus larges que
# celles d'Helvetica. Ce facteur conserve les retours à la ligne et donc la
# géométrie des cartes dans la très grande majorité des énoncés.
DYSLEXIC_FONT_SCALE = 0.85


def _font(style: str = "regular") -> str:
    if _USE_DYSLEXIC.get():
        return _DYSLEXIC_FONTS[style]
    return {"regular": "Helvetica", "bold": "Helvetica-Bold",
            "italic": "Helvetica-Oblique"}[style]


def _subject_font_size(size: float) -> float:
    return size * (DYSLEXIC_FONT_SCALE if _USE_DYSLEXIC.get() else 1.0)

PAGE_W, PAGE_H = A4  # 595.27 x 841.89 pt
MARGIN = 9 * mm
QR_MAIN = 24 * mm
HEADER_H = QR_MAIN
QR_MINI = 11 * mm
COL_GAP = 5 * mm

# fiduciels de placement (§5.4) : un type de tag AprilTag par coin, jamais
# réinterprété comme identité — seul le QR principal porte le page_id signé.
FIDUCIAL_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_16h5)
FIDUCIAL_IDS = {"TL": 0, "BL": 1, "BR": 2}

DROPOUT = HexColor(settings.dropout_color)      # rouge saumon clair — élève
CARD_BORDER = HexColor("#C7CDD4")
CARD_SHADOW = Color(0.75, 0.77, 0.80, alpha=0.5)
DOTTED_GRAY = HexColor("#9AA3AC")               # pointillés — réservé overlay
LESSON_BG = HexColor("#FFF6DF")
LESSON_BORDER = HexColor("#E4C46A")
LESSON_TEXT = HexColor("#6B5310")
TITLE_RULE = HexColor("#37474F")
DOT_ON = HexColor("#455A64")

# Badge de numéro d'exercice : la difficulté 1-5 n'est plus affichée en clair
# (pastilles), elle EST la couleur du badge. Dégradé froid -> chaud, teintes
# assez foncées pour porter un numéro blanc lisible (le jaune franc ne le
# ferait pas).
DIFFICULTY_COLORS = {
    1: HexColor("#2563EB"),   # bleu
    2: HexColor("#16A34A"),   # vert
    3: HexColor("#CA8A04"),   # jaune
    4: HexColor("#EA580C"),   # orange
    5: HexColor("#DC2626"),   # rouge
}


def _difficulty_color(level5: int) -> Color:
    try:
        lvl = int(level5)
    except (TypeError, ValueError):
        lvl = 3
    return DIFFICULTY_COLORS[min(5, max(1, lvl))]


# Couleur du badge numéroté d'un exercice. La difficulté 1-5 n'est PLUS affichée
# sur les exercices ordinaires (demande utilisateur) : ils portent tous une
# teinte NEUTRE. Seuls les PROBLÈMES gardent une difficulté, en TROIS niveaux
# (facile / moyen / difficile), lue par CV sur la couleur du titre du manuel et
# stockée en 2/3/4 (cf. indigo_cv.DIFFICULTY_BY_LEVEL).
EXERCISE_BADGE = HexColor("#455A64")            # gris-bleu neutre (= DOT_ON)
PROBLEME_COLORS = {
    2: HexColor("#16A34A"),   # facile — vert
    3: HexColor("#EA580C"),   # moyen  — orange
    4: HexColor("#DC2626"),   # difficile — rouge
}


def _probleme_color(level: int) -> Color:
    """Couleur d'un PROBLÈME selon sa difficulté (3 niveaux). Tolère une échelle
    1-5 résiduelle en la repliant sur facile/moyen/difficile."""
    try:
        lvl = int(level)
    except (TypeError, ValueError):
        lvl = 3
    if lvl <= 2:
        return PROBLEME_COLORS[2]
    if lvl == 3:
        return PROBLEME_COLORS[3]
    return PROBLEME_COLORS[4]


def _exercise_badge_color(level5: int, probleme: bool) -> Color:
    """Teinte du badge numéroté + des pastilles de sous-question : neutre pour un
    exercice ordinaire, graduée par difficulté (3 niveaux) pour un problème."""
    return _probleme_color(level5) if probleme else EXERCISE_BADGE

# encarts typés d'un rappel de leçon (§ rendu rappels) : trois icônes/couleurs
# fixes indépendantes du thème de la carte, pour rester reconnaissables quelle
# que soit la couleur choisie par l'enseignant dans l'éditeur de gabarit.
ADMONITION_GUTTER = 5.5 * mm
_ADMONITION_COLORS = {
    "conseil": {"border": HexColor("#2F9E8F"), "bg": HexColor("#E9F7F4"),
                "text": HexColor("#0F5C52")},
    "attention": {"border": HexColor("#D8531D"), "bg": HexColor("#FDECE4"),
                  "text": HexColor("#7A2E10")},
}

CARD_PAD = 2.6 * mm
# Bande de correction (réservée à l'overlay) : sa hauteur n'est plus figée, elle
# est ANTICIPÉE sur le TEXTE du corrigé de la banque (_correction_strip_layout)
# pour que l'overlay puisse l'imprimer en entier, jamais coupé. STRIP_MIN_H est
# le plancher (exercice sans corrigé, ou corrigé tenant sur une ligne courte).
STRIP_MIN_H = 6.5 * mm
STRIP_GAP = 0.4 * mm    # espace blanc visible entre la carte et sa bande de correction (rapproché)
# marge HAUTE fine (le corrigé colle à SA carte) et marge BASSE plus large (nette
# séparation d'avec la carte suivante) : le corrigé ne peut plus se confondre avec
# l'exercice du dessous. La marge basse effective = STRIP_PAD_BOT + GAP inter-carte.
STRIP_PAD_TOP = 0.8 * mm
STRIP_PAD_BOT = 2.2 * mm
STRIP_NOTE_W = 17 * mm  # réserve droite pour la note de barème (imprimée en gros/gras)
CORR_FS_DELTA = 1.0     # le corrigé s'imprime un cran plus petit que l'énoncé

# --- modes de guide (§ assistant « Créer mon sujet ») ------------------------
# Le « guide » est le court rappel d'auto-correction attaché à chaque exercice
# (GeneratedExercise.correction, alias correction_guide côté Indigo). Trois
# modes, portés par item["guides"] et figés dans la géométrie de la copie :
#   GUIDES_OVERLAY : comportement historique — la bande est dimensionnée sur le
#     texte du guide, laissée VIDE sur le sujet, et l'overlay de correction
#     l'imprime (seulement si l'élève s'est trompé).
#   GUIDES_PRINT   : le guide est imprimé DANS la bande dès le sujet (élèves
#     de niveau 1 à 4). La géométrie est rigoureusement la même qu'en overlay —
#     seule l'encre change — donc deux élèves d'une même variante gardent une
#     mise en page identique, condition du placement manuel page/colonne.
#   GUIDES_NONE    : aucun guide, ni au sujet ni à l'overlay. La bande retombe à
#     son plancher (STRIP_MIN_H) : elle ne porte plus que la note de barème, que
#     l'overlay doit bien imprimer quelque part — c'est l'espace du TEXTE du
#     guide qui est récupéré (souvent 10 à 20 mm par carte).
GUIDES_OVERLAY = "overlay"
GUIDES_PRINT = "print"
GUIDES_NONE = "none"
GUIDE_BG = HexColor("#F3F6FA")          # fond discret du guide imprimé
GUIDE_TEXT = HexColor("#3A4A5C")
RADIUS = 2.2 * mm
GAP = 3.5 * mm          # espace vertical entre deux cartes/rappels
COL_W = (PAGE_W - 2 * MARGIN - COL_GAP) / 2

# En-tête en 4 zones horizontales, pleine hauteur, séparées par une gouttière
# (jamais contiguës : c'est le chevauchement identité/appréciation qu'on
# corrige) : Note (contrôle seul) | Appréciation (élastique) | Identité+méta
# (largeur FIXE, justifiée droite) | QR/fiduciels (inchangé).
#
# La largeur de la zone méta est fixe et NON déduite du texte : l'overlay de
# correction rejoue header_geometry() sans connaître le nom de l'élève, et
# doit retomber sur exactement les mêmes rects. C'est donc la taille du nom
# qui s'adapte à la zone (_fit_size), pas l'inverse.
NOTE_W = 20 * mm
META_W = 52 * mm
HEADER_GAP = 3 * mm       # gouttière entre deux zones voisines
HEADER_PAD_V = 0 * mm     # toutes les zones suivent exactement le carré QR
HEADER_LABEL_DY = 3.8 * mm  # ligne de base des libellés NOTE/APPRÉCIATION
QR_ZONE_W = QR_MAIN
# clearance du fiduciel TL (haut-gauche) : aucune zone ne doit le recouvrir
HEADER_LEFT = MARGIN + QR_MINI + 4 * mm


def header_geometry(assessment_type: str) -> dict:
    """Rects (x, y, w, h) des 4 zones de l'en-tête, partagés entre le sujet
    (_draw_header) et l'overlay de correction (render_overlay) pour rester
    alignés physiquement (recalage par fiduciels)."""
    top = PAGE_H - MARGIN
    bottom = top - HEADER_H
    h = HEADER_H
    show_note = assessment_type == "control"
    qr_x = PAGE_W - MARGIN - QR_ZONE_W
    meta_x = qr_x - HEADER_GAP - META_W
    note_x = HEADER_LEFT
    note_w = NOTE_W if show_note else 0.0
    appreciation_x = note_x + (note_w + HEADER_GAP if show_note else 0.0)
    appreciation_w = meta_x - HEADER_GAP - appreciation_x
    return {
        "note": {"x": note_x, "y": bottom, "w": note_w, "h": h, "visible": show_note},
        "appreciation": {"x": appreciation_x, "y": bottom, "w": appreciation_w, "h": h},
        "meta": {"x": meta_x, "y": bottom, "w": META_W, "h": h},
        "qr": {"x": qr_x, "y": bottom, "w": QR_ZONE_W, "h": h},
    }


def _qr_image(payload: str, box_size: int = 8) -> ImageReader:
    img = qrcode.make(payload, box_size=box_size, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _fiducial_image(tag_id: int, side_px: int = 240, quiet_px: int = 40) -> ImageReader:
    """Tag AprilTag 16h5 avec zone blanche de garde (nécessaire au décodage)."""
    marker = cv2.aruco.generateImageMarker(FIDUCIAL_DICT, tag_id, side_px)
    padded = np.full((side_px + 2 * quiet_px, side_px + 2 * quiet_px), 255, dtype=np.uint8)
    padded[quiet_px:quiet_px + side_px, quiet_px:quiet_px + side_px] = marker
    ok, buf = cv2.imencode(".png", padded)
    return ImageReader(io.BytesIO(buf.tobytes()))


def _draw_markers(c: canvas.Canvas, page_payload: str):
    """QR principal unique (identité + HMAC, haut droit) + 3 fiduciels AprilTag
    de placement TL/BL/BR, un type par coin (§5.4). FIGÉ."""
    c.drawImage(_qr_image(page_payload), PAGE_W - MARGIN - QR_MAIN, PAGE_H - MARGIN - QR_MAIN,
                QR_MAIN, QR_MAIN)
    for role, (x, y) in {
        "TL": (MARGIN, PAGE_H - MARGIN - QR_MINI),
        "BL": (MARGIN, MARGIN),
        "BR": (PAGE_W - MARGIN - QR_MINI, MARGIN),
    }.items():
        c.drawImage(_fiducial_image(FIDUCIAL_IDS[role]), x, y, QR_MINI, QR_MINI)


# ------------------------------------------------------------------- icônes

def _icon_book(c: canvas.Canvas, x: float, y: float, size: float = 3.4 * mm,
               color=LESSON_TEXT):
    """Petit livre vectoriel (couverture pleine + reliure claire) — reste
    lisible en petite taille dans la marge, icône "rappel de leçon"."""
    c.saveState()
    w, h = size * 0.9, size * 0.72
    c.setFillColor(color)
    c.roundRect(x - w / 2, y, w, h, size * 0.09, stroke=0, fill=1)
    c.setStrokeColor(white)
    c.setLineWidth(0.7)
    c.line(x, y + size * 0.1, x, y + h - size * 0.1)
    c.restoreState()


def _icon_bulb(c: canvas.Canvas, x: float, y: float, size: float = 3.4 * mm,
               color=DOT_ON):
    """Petite ampoule vectorielle — icône "conseil"."""
    c.saveState()
    r = size * 0.34
    cx, cy = x, y + size * 0.5
    c.setFillColor(color)
    c.circle(cx, cy, r, stroke=0, fill=1)
    base_w, base_h = size * 0.32, size * 0.24
    c.roundRect(cx - base_w / 2, cy - r - base_h + 0.3, base_w, base_h,
               base_w * 0.25, stroke=0, fill=1)
    c.restoreState()


def _icon_warning(c: canvas.Canvas, x: float, y: float, size: float = 3.4 * mm,
                  color=DOT_ON):
    """Triangle d'alerte vectoriel — icône "attention"."""
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    h = size * 0.92
    p = c.beginPath()
    p.moveTo(x, y + h)
    p.lineTo(x - size * 0.52, y)
    p.lineTo(x + size * 0.52, y)
    p.close()
    c.setLineWidth(0.9)
    c.drawPath(p, stroke=1, fill=0)
    c.setLineWidth(0.9)
    c.line(x, y + h * 0.32, x, y + h * 0.64)
    c.circle(x, y + h * 0.16, 0.35 * mm, stroke=0, fill=1)
    c.restoreState()


BADGE_GAP = 1.8 * mm    # blanc entre le badge et le début de l'énoncé


def _badge_metrics(font_size: float) -> tuple[float, float, float]:
    """(largeur, hauteur, taille de police) du badge numéroté d'un exercice —
    dimensionné sur le corps de l'énoncé pour rester solidaire de la 1re ligne
    de texte quel que soit le gabarit."""
    badge_fs = max(6.5, font_size - 0.5)
    return (badge_fs + 5.4, badge_fs + 3.4, badge_fs)


def _badge_min_asc(font_size: float) -> float:
    """Ascendante minimale de la 1re ligne d'énoncé pour que le badge, centré
    sur la hauteur d'œil du texte, ne déborde pas au-dessus de la carte."""
    _bw, bh, _bfs = _badge_metrics(font_size)
    return font_size * 0.35 + bh / 2


def _draw_badge(c: canvas.Canvas, x: float, y_base: float, font_size: float,
                label: str, color: Color) -> float:
    """Pastille posée à gauche d'une ligne de texte, centrée sur sa hauteur
    d'œil. Retourne sa largeur.

    Deux usages, une seule forme — c'est voulu : le numéro de l'exercice en
    tête d'énoncé, et l'étiquette d'une sous-question (« a », « b »…) en tête
    de sa ligne. Les deux portent la couleur de DIFFICULTÉ de l'exercice, si
    bien qu'un coup d'œil rattache chaque sous-question à sa carte."""
    bw, bh, bfs = _badge_metrics(font_size)
    by = y_base + font_size * 0.35 - bh / 2
    c.setFillColor(color)
    c.roundRect(x, by, bw, bh, 1.0 * mm, stroke=0, fill=1)
    c.setFillColor(white)
    draw_bfs = _subject_font_size(bfs)
    c.setFont(_font("bold"), draw_bfs)
    c.drawCentredString(x + bw / 2, by + (bh - draw_bfs * 0.72) / 2, str(label))
    c.setFillColor(black)
    return bw


def _dotted(c: canvas.Canvas):
    c.setDash(1.6, 1.8)
    c.setStrokeColor(DOTTED_GRAY)
    c.setLineWidth(0.7)


def _solid(c: canvas.Canvas):
    c.setDash()


# ------------------------------------------------------------------- en-tête

def _fit_size(text: str, font: str, max_w: float, start: float,
              min_size: float) -> float:
    """Plus grande taille <= start telle que `text` tienne dans max_w."""
    size = start
    while size > min_size and stringWidth(text, font, size) > max_w:
        size -= 0.25
    return size


def _draw_header(c: canvas.Canvas, student_name: str, class_name: str, title: str,
                 assessment_type: str, the_date: str, tpl: dict | None = None):
    """Bande compacte, réglée par le carré QR : Note | Appréciation |
    identité | QR. La classe est un filigrane pleine hauteur dans la zone
    d'identité ; les informations utiles restent au premier plan."""
    tpl = tpl or DEFAULT_TEMPLATES["header"]
    accent = HexColor(tpl.get("accent", "#37474F"))
    y_top = PAGE_H - MARGIN
    header_bottom = y_top - HEADER_H
    label = "Contrôle" if assessment_type == "control" else "Entraînement"
    geo = header_geometry(assessment_type)
    band_bottom = header_bottom + HEADER_PAD_V
    band_h = HEADER_H - 2 * HEADER_PAD_V

    # --- zone Note (contrôle uniquement) ---
    if geo["note"]["visible"]:
        nx, nw = geo["note"]["x"], geo["note"]["w"]
        _dotted(c)
        c.roundRect(nx, band_bottom, nw, band_h, 2 * mm)
        _solid(c)
        c.setFillColor(DOTTED_GRAY)
        c.setFont(_font(), _subject_font_size(6.5))
        c.drawCentredString(nx + nw / 2, band_bottom + band_h - HEADER_LABEL_DY, "NOTE")
        c.setFillColor(black)

    # --- zone Appréciation (absorbe la largeur de la Note en entraînement) ---
    ax, aw = geo["appreciation"]["x"], geo["appreciation"]["w"]
    _dotted(c)
    c.roundRect(ax, band_bottom, aw, band_h, 2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont(_font(), _subject_font_size(6.5))
    c.drawString(ax + 2.5 * mm, band_bottom + band_h - HEADER_LABEL_DY,
                 "APPRÉCIATION — remplie à la correction")
    c.setFillColor(black)

    # --- zone Identité + méta : classe en filigrane, contenu au premier plan ---
    mx, my, mw, mh = (geo["meta"][k] for k in ("x", "y", "w", "h"))
    c.saveState()
    clip = c.beginPath()
    clip.rect(mx, my, mw, mh)
    c.clipPath(clip, stroke=0, fill=0)

    class_text = _pdf_safe(class_name)
    class_fs = _fit_size(class_text, _font("bold"), mw - 1.5 * mm,
                         _subject_font_size(90), _subject_font_size(34))
    # Helvetica-Bold a une hauteur de capitale proche de 0,72 em : cette base
    # centre visuellement le filigrane et lui donne presque les 24 mm du QR.
    class_base = my + (mh - class_fs * 0.718) / 2
    c.setFillColor(HexColor("#ECEFF1"))
    c.setFont(_font("bold"), class_fs)
    c.drawCentredString(mx + mw / 2, class_base, class_text)

    name = _pdf_safe(student_name)
    name_fs = _fit_size(name, _font("bold"), mw - 4 * mm,
                        _subject_font_size(float(tpl.get("name_size", 14))),
                        _subject_font_size(8.0))
    c.setFillColor(black)
    c.setFont(_font("bold"), name_fs)
    c.drawCentredString(mx + mw / 2, my + mh - 6.0 * mm, name)

    if title:
        title_text = _pdf_safe(title)
        title_fs = _fit_size(title_text, _font("bold"), mw - 4 * mm,
                             _subject_font_size(float(tpl.get("title_size", 8))),
                             _subject_font_size(5.5))
        c.setFillColor(accent)
        c.setFont(_font("bold"), title_fs)
        c.drawCentredString(mx + mw / 2, my + 7.0 * mm, title_text)
    if tpl.get("show_date", True):
        meta_fs = _subject_font_size(max(5.5, float(tpl.get("title_size", 8)) - 1.5))
        meta_text = _pdf_safe(f"{label}  ·  {the_date}")
        meta_fs = _fit_size(meta_text, _font(), mw - 4 * mm, meta_fs,
                            _subject_font_size(5.0))
        c.setFillColor(HexColor("#6A737C"))
        c.setFont(_font(), meta_fs)
        c.drawCentredString(mx + mw / 2, my + 2.0 * mm, meta_text)
    c.restoreState()

    # Aucun filet inférieur : la bande compacte et ses gouttières suffisent à
    # séparer l'identité des cartes d'exercices.
    c.setFillColor(black)


# --------------------------------------------------------------- exercices

def _wrap(text: str, width_pt: float, font_size: int) -> list[str]:
    max_chars = max(10, int(width_pt / (font_size * 0.5)))
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------- mise en forme maths
# Contrat exgen-3 : les textes (énoncés, corrections, rappels) contiennent des
# spans $...$ en LaTeX validé (mathrender). Chaque span est rasterisé en PNG
# haute résolution (mathtext, cache disque) et posé sur la ligne de base du
# texte — mêmes formules à l'écran (KaTeX) et sur papier.

_PDF_CHAR_MAP = {
    "\u2192": "->", "\u21d2": "=>", "\u2264": "<=", "\u2265": ">=",
    "\u2260": "!=", "\u0153": "oe", "\u0152": "OE", "\u2026": "...",
    "\u00a0": " ", "\u202f": " ", "\u2212": "-", "\u2013": "-",
}


def _pdf_safe(s: str) -> str:
    """Texte encodable en WinAnsi (Helvetica) — jamais de glyphe manquant."""
    for k, v in _PDF_CHAR_MAP.items():
        s = s.replace(k, v)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def _math_image(latex: str, fs: float):
    """(ImageReader, w_pt, h_pt, depth_pt) d'un span LaTeX, ou None si invalide."""
    from . import mathrender
    try:
        png, w, h, d = mathrender.render_math_png(latex, fs)
    except Exception:
        return None
    return (ImageReader(io.BytesIO(png)), w, h, d)


_LEGACY_FRAC_RE = re.compile(r"(?<![\w/])(\d+)\s*/\s*(\d+)(?![\w/])")


def _legacy_to_tagged(statement: str) -> str:
    """Compatibilité générateurs builtin : « Calculer : 3/4 + 5/6 = ? » sans
    balisage $ est converti vers le contrat balisé (fractions empilées).

    Réservé aux énoncés d'UNE ligne, qui sont sa seule provenance : le motif
    « consigne : tout le reste » n'a aucun sens sur un énoncé mis en lignes, où
    le « : » est celui d'une énumération et non celui d'un calcul isolé."""
    if "$" in statement or ":" not in statement or "\n" in statement:
        return statement
    head, tail = statement.split(":", 1)
    tail = tail.strip().rstrip("?").rstrip().rstrip("=").rstrip()
    if not tail or not any(ch.isdigit() for ch in tail) or len(tail) > 80:
        return statement
    from . import mathrender
    latex = _LEGACY_FRAC_RE.sub(r"\\dfrac{\1}{\2}", tail).replace("*", r" \times ")
    if mathrender.sanitize_latex(latex) is None:
        return statement
    return f"{head.strip()} : ${latex}$"


BLANK_TOKEN = statement_mod.BLANK_TOKEN
# Case à remplir : 20 x 8 mm, dimension d'écriture manuscrite d'élève de
# collège — c'est une contrainte physique (la main), pas une proportion du
# corps de texte : elle ne suit donc PAS font_size. C'est au contraire le
# texte qui grossit (BLANK_FONT_BOOST) pour rester en rapport avec sa case —
# mais LIGNE PAR LIGNE, jamais tout l'énoncé : le contexte et la consigne
# restent au corps du gabarit (c'est la taille « habituelle », celle que
# l'enseignant a réglée), seules les phrases qui portent réellement une case
# grandissent avec elle.
BLANK_W = 20 * mm
BLANK_H = 8 * mm
# Une fraction manuscrite occupe deux étages (numérateur/dénominateur) : toutes
# les zones qui en attendent une gagnent exactement 3 mm en hauteur.
FRACTION_EXTRA_H = 3 * mm
BLANK_FONT_BOOST = 2.0
# Deux variantes de case, choisies par services.indigo_fields selon la réponse
# attendue (cf. statement.MINI_TOKEN / WIDE_TOKEN) :
# - MINI : 9 x 7 mm, deux chiffres — réservée à un trou d'ÉQUATION À TROUS
#   (case collée à une formule). La case elle-même reste petite (contrainte
#   physique), mais le CORPS DE TEXTE de sa ligne grandit comme pour une case
#   standard (has_answer_field ne distingue plus les tailles) : une même phrase
#   ne mélange jamais deux corps de police selon la case qu'elle porte ;
# - RIGHT : case standard (hauteur BLANK_H) qui s'ÉTIRE jusqu'au bord droit de la
#   colonne pour maximiser la place. Sa largeur n'est pas fixe : elle est calée à
#   la mise en page (_rich_layout) sur l'espace restant de sa ligne, avec un
#   minimum de sécurité MIN_RIGHT_BLANK_W (sinon le repli met une case ridicule).
MINI_BLANK_W = 9 * mm
MINI_BLANK_H = 7 * mm
MIN_RIGHT_BLANK_W = 22 * mm
MINI_TOKEN = statement_mod.MINI_TOKEN
WIDE_TOKEN = statement_mod.WIDE_TOKEN


def _zone_font_size(response_type: str, font_size: float) -> float:
    """Corps de texte de la ZONE de réponse. Seul table_fill s'écarte du
    gabarit : ses libellés de ligne SONT les phrases à trous (« a. 7 × 8 = »),
    ils suivent donc le corps agrandi des cases — exactement comme les phrases
    à trous d'un énoncé, dont ils ne sont que la version en grille."""
    return font_size + BLANK_FONT_BOOST if response_type == "table_fill" else font_size


def _seg_w(seg: tuple, fs: float) -> float:
    if seg[0] == "word":
        return stringWidth(seg[1], _font(), _subject_font_size(fs))
    if seg[0] == "blank":
        return seg[1]
    return seg[2]


def _seg_glue(seg: tuple) -> bool:
    return bool(seg[-1])


def _blank_seg(kind: str, fs: float, glue: bool, extra_h: float = 0.0) -> tuple:
    """Segment de case selon sa variante (cf. statement tokens). Format :
    ("blank", w, asc, desc, kind, glue). Pour "right", `w` est un MINIMUM que la
    mise en page étirera ensuite jusqu'au bord de colonne."""
    desc = fs * 0.24
    if kind == "mini":
        return ("blank", MINI_BLANK_W, MINI_BLANK_H + extra_h - desc,
                desc, "mini", glue)
    if kind == "right":
        return ("blank", MIN_RIGHT_BLANK_W, BLANK_H + extra_h - desc,
                desc, "right", glue)
    return ("blank", BLANK_W, BLANK_H + extra_h - desc, desc, "normal", glue)


# Marques privées de rendu : elles préservent la variante de largeur tout en
# transportant l'information « réponse fractionnaire ». Elles ne sont jamais
# stockées en banque et sont ajoutées après la normalisation de l'énoncé.
_TALL_NORMAL_TOKEN = "{{blank_tall}}"
_TALL_RIGHT_TOKEN = "{{blank_right_tall}}"
_TALL_MINI_TOKEN = "{{mini_tall}}"
_TALL_TOKEN_BY_PUBLIC = {
    BLANK_TOKEN: _TALL_NORMAL_TOKEN,
    WIDE_TOKEN: _TALL_RIGHT_TOKEN,
    MINI_TOKEN: _TALL_MINI_TOKEN,
}
_ANSWER_SPLIT = re.compile(
    r"(\{\{blank_right_tall\}\}|\{\{blank_tall\}\}|\{\{mini_tall\}\}|"
    r"\{\{blank_right\}\}|\{\{blank\}\}|\{\{mini\}\})")
_PUBLIC_ANSWER_RE = re.compile(
    r"(\{\{blank_right\}\}|\{\{blank\}\}|\{\{mini\}\})")
_TOKEN_KIND = {
    WIDE_TOKEN: ("right", 0.0), BLANK_TOKEN: ("normal", 0.0),
    MINI_TOKEN: ("mini", 0.0),
    _TALL_RIGHT_TOKEN: ("right", FRACTION_EXTRA_H),
    _TALL_NORMAL_TOKEN: ("normal", FRACTION_EXTRA_H),
    _TALL_MINI_TOKEN: ("mini", FRACTION_EXTRA_H),
}


def _mark_fraction_blanks(text: str, indices: set[int] | None) -> str:
    """Marque uniquement les cases dont la réponse attendue est une fraction.

    Le rang suit l'ordre de lecture des marqueurs dans l'énoncé, comme
    l'appariement OCR des multi_blank.
    """
    if not indices:
        return text
    rank = -1

    def repl(match: re.Match) -> str:
        nonlocal rank
        rank += 1
        token = match.group(0)
        return _TALL_TOKEN_BY_PUBLIC[token] if rank in indices else token

    return _PUBLIC_ANSWER_RE.sub(repl, text)


def _has_render_answer_field(text: str) -> bool:
    return statement_mod.has_answer_field(text) or any(
        token in (text or "") for token in _TOKEN_KIND if token not in statement_mod.ANSWER_TOKENS)


def _paragraph_segs(text: str, fs: float, math_fs: float) -> list[tuple]:
    """Segments d'UNE ligne logique d'énoncé (elle peut encore se replier sur
    plusieurs lignes de rendu). seg = ("word", texte, glue) |
    ("math", img, w, h, d, glue) | ("blank", w, asc, desc, kind, glue) ; glue =
    collé au segment précédent SANS espace (ponctuation après une formule, etc.)."""
    from . import mathrender
    segs: list[tuple] = []
    prev_no_space = False  # le flux précédent se termine sans espace

    def _emit_words(part: str) -> None:
        nonlocal prev_no_space
        words = _pdf_safe(part).split()
        leading_ws = bool(part[:1].isspace())
        for j, w in enumerate(words):
            segs.append(("word", w,
                         j == 0 and not leading_ws and prev_no_space and bool(segs)))
        if words:
            prev_no_space = not part[-1:].isspace()
        elif part:
            prev_no_space = False

    for content, is_math in mathrender.split_math_spans(text or ""):
        if is_math:
            im = _math_image(content, math_fs)
            if im is not None:
                segs.append(("math", *im, prev_no_space and bool(segs)))
            else:  # repli : texte aplati, jamais de LaTeX brut imprimé
                for j, w in enumerate(_pdf_safe(mathrender.strip_math(f"${content}$")).split()):
                    segs.append(("word", w, j == 0 and prev_no_space and bool(segs)))
            prev_no_space = True
        elif _has_render_answer_field(content):
            # découpe en gardant chaque marque de case (blank / blank_right / mini)
            for piece in _ANSWER_SPLIT.split(content):
                spec = _TOKEN_KIND.get(piece)
                if spec is not None:
                    kind, extra_h = spec
                    segs.append(_blank_seg(kind, fs, False, extra_h))
                    prev_no_space = False
                else:
                    _emit_words(piece)
        else:
            _emit_words(content)
    return segs


def _rich_layout(text: str, width: float, fs: float, math_fs: float | None = None,
                 first_indent: float = 0.0, first_min_asc: float = 0.0,
                 blank_fs: float | None = None,
                 sub_badge_color: Color | None = None) -> dict:
    """Met en page un texte balisé $...$ : flot de mots et d'images maths.
    Retourne {lines: [{segs, asc, desc, h, w, indent, fs, badge, badge_x}],
    height} ; seg = ("word", str) ou ("math", ImageReader, w, h, d) ou
    ("blank", w, asc, desc) — case de réponse courte insérée en ligne
    (marqueur BLANK_TOKEN).

    Le texte est d'abord découpé sur ses SAUTS DE LIGNE (§ services/statement) :
    chacun est un saut DUR, jamais rejoué en espace. C'est la ligne logique, et
    non l'énoncé entier, qui est ensuite l'unité de décision — deux réglages en
    dépendent, et c'est pour ça qu'ils vivent ici plutôt que chez l'appelant :

    - `blank_fs` : corps de texte des lignes qui portent réellement une case à
      remplir. Une phrase à trous est écrite à la taille de sa case, le reste de
      l'énoncé garde le corps du gabarit.
    - `sub_badge_color` : couleur des pastilles de sous-question. Une ligne qui
      ouvre une sous-question (« a. », « b) »…) perd son étiquette du flot de
      texte et la reçoit en pastille, le reste de la ligne étant mis en retrait
      pendante sous elle.

    `first_indent` réserve de la place en tête de 1re ligne (badge numéroté de
    la carte exercice) : la ligne est raccourcie d'autant et décalée au dessin.
    `first_min_asc` force une ascendante minimale sur cette 1re ligne pour que
    le badge y tienne en entier."""
    lines: list[dict] = []
    total_h = 0.0

    for p_idx, para in enumerate(statement_mod.lines(text or "")):
        lead = first_indent if p_idx == 0 else 0.0
        badge = None
        if sub_badge_color is not None and (lab := statement_mod.subquestion_label(para)):
            badge, para = lab
        # le corps suit la case quand la ligne en porte une — décidé APRÈS
        # l'étiquette, qui ne change pas la nature de la phrase
        p_fs = blank_fs if (blank_fs and _has_render_answer_field(para)) else fs
        p_math_fs = math_fs or p_fs
        badge_w = (_badge_metrics(p_fs)[0] + BADGE_GAP) if badge is not None else 0.0
        # retrait PENDANT sous une pastille : les lignes suivantes de la
        # sous-question s'alignent sur son texte, pas sous sa pastille
        head_indent = lead + badge_w
        cont_indent = head_indent if badge is not None else 0.0

        segs = _paragraph_segs(para, p_fs, p_math_fs)
        space_w = stringWidth(" ", _font(), _subject_font_size(p_fs))

        raw_lines: list[list[tuple]] = []
        cur: list[tuple] = []
        cur_w = 0.0
        avail = max(1.0, width - head_indent)
        for seg in segs:
            w = _seg_w(seg, p_fs)
            add = w if (not cur or _seg_glue(seg)) else w + space_w
            if cur and cur_w + add > avail:
                raw_lines.append(cur)
                cur, cur_w = [seg], w
                avail = max(1.0, width - cont_indent)
            else:
                cur.append(seg)
                cur_w += add
        if cur:
            raw_lines.append(cur)
        # une ligne logique vide de segments (étiquette seule) garde quand même
        # sa pastille : sans ça, « a. » suivi d'une figure disparaîtrait
        if not raw_lines and badge is not None:
            raw_lines = [[]]

        # CASE DE DROITE ({{blank_right}}) : sa largeur n'est connue qu'ICI, une
        # fois la ligne repliée. Dernière de sa ligne, elle s'étire pour combler
        # tout l'espace restant jusqu'au bord de la colonne (avec un minimum).
        for i, line in enumerate(raw_lines):
            if not line or line[-1][0] != "blank" or line[-1][4] != "right":
                continue
            avail_i = max(1.0, width - (head_indent if i == 0 else cont_indent))
            used = 0.0
            for k, s in enumerate(line):
                if k > 0 and not _seg_glue(s):
                    used += space_w
                if k < len(line) - 1:            # tout sauf la case de droite
                    used += _seg_w(s, p_fs)
            b = line[-1]
            line[-1] = ("blank", max(MIN_RIGHT_BLANK_W, avail_i - used),
                        b[2], b[3], "right", b[5])

        for i, line in enumerate(raw_lines):
            asc, desc = p_fs * 0.78, p_fs * 0.24
            if p_idx == 0 and i == 0:
                asc = max(asc, first_min_asc)
            if badge is not None and i == 0:
                asc = max(asc, _badge_min_asc(p_fs))
            for seg in line:
                if seg[0] == "math":
                    asc = max(asc, seg[3] - seg[4])
                    desc = max(desc, seg[4])
                elif seg[0] == "blank":
                    asc = max(asc, seg[2])
                    desc = max(desc, seg[3])
            lh = asc + desc + 2.2
            n_spaces = sum(1 for j, s in enumerate(line) if j > 0 and not _seg_glue(s))
            lines.append({
                "segs": line, "asc": asc, "desc": desc, "h": lh, "fs": p_fs,
                "indent": head_indent if i == 0 else cont_indent,
                "w": sum(_seg_w(s, p_fs) for s in line) + space_w * n_spaces,
                "badge": badge if i == 0 else None,
                "badge_x": lead, "badge_color": sub_badge_color,
            })
            total_h += lh
    return {"lines": lines, "height": total_h}


def _draw_rich(c: canvas.Canvas, x: float, y_top: float, layout: dict,
               color=black, centered: bool = False, width: float | None = None,
               font: str | None = None, blanks: list | None = None) -> float:
    """Dessine un layout _rich_layout. Retourne le y sous la dernière ligne.
    `blanks`, si fourni, reçoit la géométrie PDF absolue (x_pt/y_pt/w_pt/h_pt)
    de chaque case de réponse courte insérée en ligne (BLANK_TOKEN), dans
    l'ordre de lecture — c'est l'ordre dont dépend l'appariement des cases d'un
    multi_blank avec les réponses attendues.

    Le corps de texte n'est PAS un paramètre : chaque ligne porte le sien
    (line["fs"]), posé par _rich_layout au moment de la mesure. Redonner ici une
    taille, c'était offrir de dessiner à un corps différent de celui qui a servi
    à mesurer — l'écart classique entre « ce qu'on croit faire tenir » et « ce
    qui tient » (cf. pages_needed)."""
    font = font or _font()
    y = y_top
    for line in layout["lines"]:
        fs = line["fs"]
        draw_fs = _subject_font_size(fs)
        space_w = stringWidth(" ", font, draw_fs)
        y_base = y - line["asc"]
        cx = x + line.get("indent", 0.0)
        if centered and width:
            cx += (width - line["w"]) / 2
        if line.get("badge"):
            _draw_badge(c, x + line.get("badge_x", 0.0), y_base, fs,
                        line["badge"], line["badge_color"])
        for j, seg in enumerate(line["segs"]):
            if j > 0 and not seg[-1]:
                cx += space_w
            if seg[0] == "word":
                c.setFont(font, draw_fs)
                c.setFillColor(color)
                c.drawString(cx, y_base, seg[1])
                cx += stringWidth(seg[1], font, draw_fs)
            elif seg[0] == "blank":
                _, w, asc, desc, kind, _glue = seg
                c.setStrokeColor(DROPOUT)
                c.setLineWidth(0.9)
                c.roundRect(cx, y_base - desc, w, asc + desc, 0.8 * mm)
                if blanks is not None:
                    blanks.append({"x_pt": cx, "y_pt": y_base - desc,
                                  "w_pt": w, "h_pt": asc + desc, "kind": kind})
                c.setFillColor(color)
                cx += w
            else:
                _, img, w, h, d, _glue = seg
                c.drawImage(img, cx, y_base - d, width=w, height=h,
                            mask="auto", preserveAspectRatio=True)
                cx += w
        y -= line["h"]
    return y


_FIGURE_DPI = 150  # dpi de rasterisation dans services/figures.py
# Marge verticale (au-dessus ET en dessous) d'une figure insérée AU MARQUEUR
# {{figure}}, au milieu de l'énoncé — cf. _statement_layout.
_FIG_MARKER_GAP = 1.5 * mm


def _figure_image(figure_json: dict | None, max_w: float, max_h: float):
    """(ImageReader, w_pt, h_pt) d'une figure paramétrée, à l'échelle. None si absente."""
    if not figure_json:
        return None
    from . import figures as figmod
    try:
        png = figmod.render_figure(figure_json)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            wpx, hpx = im.size
    except Exception:
        return None
    w_pt, h_pt = wpx * 72.0 / _FIGURE_DPI, hpx * 72.0 / _FIGURE_DPI
    scale = min(1.0, max_w / w_pt, max_h / h_pt)
    return (ImageReader(io.BytesIO(png)), w_pt * scale, h_pt * scale)


# « consigne : $expr$ » sur UNE ligne -> l'expression passe en display.
_DISPLAY_RE = re.compile(r"^(.*?[:?])\s*\$([^$]+)\$\s*\??\s*$")
# ...ou l'expression occupe à elle seule la DERNIÈRE ligne, la consigne étant
# au-dessus (« Calcule :\n$\dfrac{3}{4}+\dfrac{5}{6}$ »).
_ONLY_MATH_RE = re.compile(r"^\$([^$]+)\$$")


def _display_split(statement: str) -> tuple[str, str | None]:
    """(corps, expression à mettre en valeur | None) — l'expression finale d'un
    énoncé est centrée et agrandie.

    Le motif est cherché sur la seule DERNIÈRE ligne : sur l'énoncé entier, un
    « .*? » gourmand de sauts de ligne finissait par appareiller la consigne
    d'en haut avec la formule d'en bas à travers toute une énumération, et
    arrachait la dernière donnée de sa liste pour la centrer."""
    lines = statement_mod.lines(statement)
    if not lines:
        return statement, None
    head, last = lines[:-1], lines[-1].strip()
    if (m := _DISPLAY_RE.match(last)) and "$" not in m.group(1):
        return "\n".join(head + [m.group(1)]), m.group(2)
    if head and (m := _ONLY_MATH_RE.match(last)):
        return "\n".join(head), m.group(1)
    return statement, None


def _statement_layout(statement: str, width: float, font_size: float,
                      math_size: int, figure_json: dict | None = None,
                      first_indent: float = 0.0,
                      first_min_asc: float = 0.0,
                      blank_fs: float | None = None,
                      sub_badge_color: Color | None = None,
                      fraction_blank_indices: set[int] | None = None) -> dict:
    """Met en page un énoncé : texte riche + éventuelle expression finale mise
    en valeur (motif « consigne : $expr$ » -> centrée, plus grande) + figure.
    `first_indent`/`first_min_asc` réservent la place du badge numéroté en tête
    de 1re ligne ; `blank_fs`/`sub_badge_color` sont passés tels quels à
    _rich_layout (corps des phrases à trous, pastilles a./b./c.).
    Retourne {intro, display, figure, height}."""
    # Normalisation ici AUSSI, alors que la banque ne stocke déjà que du
    # normalisé (exercise_gen._validate_exercise) : les exercices créés AVANT la
    # mise en lignes y dorment toujours, sous-questions recollées, et rien ne les
    # rejoue. C'est la MÊME fonction des deux côtés, pas une seconde règle de
    # mise en lignes — et elle est idempotente, donc un énoncé déjà bien formé la
    # traverse inchangé.
    statement = statement_mod.normalize(statement)
    statement = _mark_fraction_blanks(statement, fraction_blank_indices)
    figure = _figure_image(figure_json, min(width, 62 * mm), 42 * mm)

    # PLACEMENT DE L'IMAGE (§ demande utilisateur) : si l'énoncé porte le
    # marqueur « {{figure}} » ET qu'une image est attachée, on coupe l'énoncé au
    # marqueur et on insère la figure À CET ENDROIT (avant/après). Sans image, le
    # marqueur est retiré pour ne pas s'imprimer. Sans marqueur, comportement
    # historique : la figure est placée à la fin de l'énoncé.
    if statement_mod.has_figure_marker(statement) and figure is not None:
        before, after = statement_mod.split_figure_marker(statement)
        intro = _rich_layout(_legacy_to_tagged(before), width, font_size,
                             first_indent=first_indent, first_min_asc=first_min_asc,
                             blank_fs=blank_fs, sub_badge_color=sub_badge_color)
        display, intro_after = None, None
        after = after or ""
        if after.strip():
            body_after, expr = _display_split(after)
            if expr is not None:
                im = _math_image(expr, math_size)
                if im is not None and im[1] <= width - 4:
                    display = im
                else:
                    body_after = after
            if body_after.strip():
                intro_after = _rich_layout(body_after, width, font_size,
                                           blank_fs=blank_fs, sub_badge_color=sub_badge_color)
        height = intro["height"] + 2 * _FIG_MARKER_GAP + figure[2]
        if intro_after:
            height += intro_after["height"]
        if display:
            height += display[2] + 2.5 * mm
        return {"intro": intro, "intro_after": intro_after, "display": display,
                "figure": figure, "figure_inline": True, "height": height}
    if statement_mod.has_figure_marker(statement):
        statement = statement_mod.strip_figure_marker(statement)

    statement = _legacy_to_tagged(statement)
    display = None
    body, expr = _display_split(statement)
    if expr is not None:
        im = _math_image(expr, math_size)
        if im is not None and im[1] <= width - 4:
            display = im
        else:
            body = statement
    intro = _rich_layout(body, width, font_size, first_indent=first_indent,
                         first_min_asc=first_min_asc, blank_fs=blank_fs,
                         sub_badge_color=sub_badge_color)
    height = intro["height"]
    if display:
        height += display[2] + 2.5 * mm
    if figure:
        height += figure[2] + 2 * mm
    return {"intro": intro, "intro_after": None, "display": display,
            "figure": figure, "figure_inline": False, "height": height}


# Correction QCM en overlay : à gauche de CHAQUE case élève, l'overlay peut
# imprimer une case « correction » (vide ou cochée) disant la bonne réponse —
# seulement si l'élève s'est trompé. Il faut donc réserver dès le sujet, à
# gauche de la case élève, la place de cette case + une marge : les cases élève
# sont décalées à droite d'autant (QCM_CORR_RESERVE), qu'elles soient corrigées
# ou non (la géométrie du sujet ne dépend jamais de la copie).
QCM_BOX = 2.0 * mm
QCM_CORR_GAP = 2.0 * mm
QCM_CORR_RESERVE = QCM_BOX + QCM_CORR_GAP
# Détection : la case imprimée (2 mm) est plus petite que la tolérance de
# recalage ET que le geste de l'élève (la coche déborde souvent). On mesure donc
# l'encre dans une FENÊTRE ÉLARGIE autour de la case (case + marge), posée ici
# dans la méta et relue par worker_cv.detect_qcm. La marge est ASYMÉTRIQUE :
# large à gauche/haut/bas (zone blanche), RÉDUITE à droite pour rester loin du
# label du choix (encre noire, NON retirée par le dropout ; il commence à 1,6 mm
# à droite de la case). Le corps de la coche, capté à gauche, suffit à décider.
QCM_DETECT_MARGIN = 1.0 * mm
QCM_DETECT_MARGIN_R = 0.5 * mm
# Marqueurs d'overlay : les marques QCM (coche/croix par case + récap de carte)
# sont volontairement 2 mm plus grandes que les marques de cellule (§ demande).
CELL_MARK_SIZE = 1.9 * mm
QCM_MARK_SIZE = CELL_MARK_SIZE + 2.0 * mm


def _qcm_ncols_cap(choices: list[str]) -> int:
    """Plafond absolu ; le nombre effectif est choisi par mesure typographique.

    Aucun seuil en nombre de caractères : il serait faux pour les formules et
    dépendrait de la police. `_qcm_layout` mesure les glyphes réellement rendus
    et ne conserve que le nombre de colonnes qui tient sans tronquer les choix.
    """
    return min(3, len(choices))


def _qcm_layout(choices: list[str], width: float,
                font_size: int) -> tuple[list[dict], float, int]:
    """Disposition compacte en 1 à 3 colonnes, remplies de gauche à droite.
    Les labels sont mis en page en riche (formules rendues). Retourne
    (items, hauteur, ncols) ;
    item = {index, dx, dy, lay, lw, box} en relatif (origine haut-gauche).

    Deux passes : la 1re mesure la largeur NATURELLE des labels pour choisir le
    nombre de colonnes, la 2de les remet en page à la largeur réelle de leur
    colonne. Une passe unique à `width` ignorait la place prise par la case à
    cocher et son blanc — le label, dessiné après la case, débordait alors de
    la carte d'autant. Le gutter gauche inclut QCM_CORR_RESERVE (place de la
    case de correction overlay), pour que le label ne déborde pas non plus."""
    box = QCM_BOX
    gutter = box + QCM_CORR_RESERVE               # case correction + marge + case élève
    gap_x, gap_y, pad = 3.0 * mm, 1.6 * mm, 1.6 * mm
    n = len(choices)
    solo_w = max(10 * mm, width - gutter - pad)     # label sur une seule colonne
    nat = [max((ln["w"] for ln in _rich_layout(ch, solo_w, font_size)["lines"]),
               default=0.0) for ch in choices]
    item_w = gutter + pad + (max(nat) if nat else 0.0) + gap_x
    # Le plafond géométrique vient de la largeur du plus grand libellé rendu :
    # une phrase longue reste donc en liste, tandis que Oui/Non, des nombres ou
    # de petites formules utilisent tout l'espace horizontal disponible.
    ncols = max(1, min(_qcm_ncols_cap(choices), n,
                       int(width // item_w) if item_w > 0 else 1))
    nrows = -(-n // ncols)  # ceil

    col_total = width / ncols
    lab_w = max(10 * mm, col_total - gutter - pad - (gap_x if ncols > 1 else 0.0))
    items = []
    lays = []
    for choice in choices:
        lay = _rich_layout(choice, lab_w, font_size)
        lays.append(lay)
    # Une hauteur par rangée : une formule haute dans une rangée ne doit pas
    # agrandir toutes les autres. Le calcul est partagé avec le dessin, donc
    # l'estimation des cards et le PDF ne peuvent pas diverger.
    row_heights = [6.0 * mm for _ in range(nrows)]
    for i, lay in enumerate(lays):
        row = i // ncols
        row_heights[row] = max(row_heights[row], lay["height"] + gap_y)
    row_offsets, offset = [], 0.0
    for height in row_heights:
        row_offsets.append(offset)
        offset += height
    for i, lay in enumerate(lays):
        row, col = divmod(i, ncols)
        lw = max((ln["w"] for ln in lay["lines"]), default=0.0)
        items.append({"index": i, "dx": col * col_total, "dy": row_offsets[row],
                      "lay": lay, "lw": lw, "box": box})
    return items, offset, ncols


# Interligne des zones de rédaction (multiline_text) : les élèves écrivent plus
# gros que le corps imprimé — mesure/dessin doivent lire la MÊME constante.
MULTILINE_ROW_H = 9 * mm

_TABLE_HEAD_H = 6.0 * mm
_TABLE_CELL_PAD = 1.2 * mm
_TABLE_COL_W = BLANK_W + 2 * _TABLE_CELL_PAD     # colonne « confortable » : case pleine taille
_TABLE_MINI_COL_W = MINI_BLANK_W + 2 * _TABLE_CELL_PAD    # colonne étroite : petit entier (0-99)
_TABLE_WIDE_COL_W = _TABLE_COL_W + 14 * mm                # colonne large : expression/décimal/texte
_TABLE_MIN_COL_W = 10.0 * mm                     # plancher quand les colonnes sont nombreuses
_TABLE_ROW_MIN_H = BLANK_H + 2 * _TABLE_CELL_PAD
_TABLE_ROWLAB_MIN_W = 18.0 * mm
_TABLE_BANK_GAP = 6.0 * mm         # séparation VISIBLE entre les deux bandes d'un tableau à 2 bandes
_TABLE_TWO_BANK_MIN_ROWS = 6       # au-delà, un tableau fin passe à 2 bandes (2de moitié à côté)
_MATCHING_PASTILLE = 2.2 * mm
_MATCHING_COL_GAP = 10.0 * mm
_MANUAL_DRAWING_H = 60.0 * mm


def _is_fraction_value(value) -> bool:
    """Réponse structurée qui doit être écrite sous forme de fraction."""
    return isinstance(value, dict) and (
        value.get("type") in ("rational", "fraction") or "fraction" in value)


_TEXT_FRACTION_RE = re.compile(r"\\d?frac\s*\{|(?<!\w)-?\d+\s*/\s*-?\d+(?!\w)")


def _expected_has_fraction(value) -> bool:
    """Détecte une fraction dans une réponse/rubrique structurée.

    Les réponses courtes et cellules portent normalement type=rational. Les
    raisonnements multiligne portent, eux, du texte attendu par étape : on y
    reconnaît aussi les écritures LaTeX et a/b.
    """
    if _is_fraction_value(value):
        return True
    if isinstance(value, dict):
        return any(_expected_has_fraction(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_expected_has_fraction(v) for v in value)
    return isinstance(value, str) and bool(_TEXT_FRACTION_RE.search(value))


def _inline_fraction_indices(response_type: str, expected: dict | None) -> set[int]:
    """Indices des blanks qui attendent une fraction, dans l'ordre de lecture."""
    expected = expected or {}
    if response_type == "short_text":
        return {0} if _is_fraction_value(expected) else set()
    if response_type == "multi_blank":
        cells = expected.get("cells") or []
        values = list(cells[0]) if cells else []
        return {i for i, value in enumerate(values) if _is_fraction_value(value)}
    return set()


def _cell_is_mini(v: dict) -> bool:
    """Petit entier 0-99 : la case peut être étroite (même seuil que
    services.indigo_fields._is_short_numeric, pour un rendu cohérent avec les
    cases à trous en ligne)."""
    if not isinstance(v, dict) or v.get("type") != "integer":
        return False
    try:
        return 0 <= int(v.get("value")) <= 99
    except (TypeError, ValueError):
        return False


def _cell_is_wide(v: dict) -> bool:
    """Réponse longue (expression, fraction, décimal, ou texte de plus de deux
    caractères) : a besoin d'une colonne plus large qu'une case standard."""
    if not isinstance(v, dict):
        return False
    t = v.get("type")
    if t in ("expression", "rational", "decimal"):
        return True
    if t == "text":
        return len(str(v.get("value", "")).strip()) > 2
    return False


def _col_kind(cells: list[list[dict]], j: int) -> str:
    """'mini'/'wide'/'normal' selon les réponses ÉDITABLES de la colonne j (les
    cellules "given", déjà imprimées dans le manuel, ne comptent pas) : étroite
    si TOUTES sont de petits entiers, large si au moins une est longue."""
    editable = [row[j] for row in cells if j < len(row) and not (row[j] or {}).get("given")]
    if not editable:
        return "normal"
    if all(_cell_is_mini(c) for c in editable):
        return "mini"
    if any(_cell_is_wide(c) for c in editable):
        return "wide"
    return "normal"


# Largeur DÉSIRÉE d'une colonne selon la réponse attendue : c'est elle qui
# dimensionne la cellule. La case à remplir, elle, OCCUPE ENSUITE toute sa
# cellule (cf. _draw_table_zone) — il n'y a plus de taille de case plafonnée :
# une cellule large offrait une case étroite entourée de blanc perdu, alors que
# c'est justement la place d'écriture de l'élève qu'il faut maximiser.
_COL_KIND_WIDTH = {"mini": _TABLE_MINI_COL_W, "wide": _TABLE_WIDE_COL_W, "normal": _TABLE_COL_W}


def _table_geometry(w: float, col_labels: list | None, row_labels: list | None,
                    cells: list[list[dict]], font_size: int,
                    sub_badge_color: Color | None = None) -> dict:
    """Géométrie complète d'un tableau à remplir — UNE définition, partagée par
    la mesure (_table_zone_height) et le dessin (_draw_table_zone).

    Le tableau est un vrai mini-tableau à cadre : la 1re colonne porte l'énoncé
    (row_labels[i], sans pastille a./b./c.), les suivantes les cases à remplir.
    Il occupe TOUJOURS toute la largeur de la carte (jamais un petit tableau
    compact perdu au milieu) : les colonnes de réponse gardent la taille
    adaptée à ce qu'elles contiennent (mini/normal/wide), c'est la colonne
    ÉNONCÉ qui absorbe l'espace restant — priorité aux cases élèves. Un tableau
    FIN (≤ 2 colonnes) avec BEAUCOUP de lignes est coupé en DEUX BANDES posées
    côte à côte (la 2de moitié des lignes à droite de la 1re) : moins de
    hauteur, largeur mieux occupée — d'où deux petits tableaux visuellement
    séparés, chacun pleine largeur de sa moitié.

    `sub_badge_color` n'est plus appliqué aux libellés de ligne (les pastilles
    a./b./c. par ligne sont volontairement supprimées, § présentation)."""
    rows = len(cells)
    cols = max((len(r) for r in cells), default=0) or 1
    inner = w - 2 * CARD_PAD
    lab_fs = max(6, font_size - 1)
    has_labels = bool(row_labels)
    thin = cols <= 2

    # largeur DÉSIRÉE de chaque colonne selon ce que l'élève doit y écrire
    # (petit entier -> étroite, expression/texte long -> large, cf. § adapter
    # la largeur des cellules à la réponse attendue) — mise à l'échelle
    # ci-dessous dans l'espace réellement disponible.
    col_kinds = [_col_kind(cells, j) for j in range(cols)]
    desired = [_COL_KIND_WIDTH[k] for k in col_kinds]
    sum_desired = sum(desired)

    # deux bandes ? tableau fin, assez de lignes, et la place d'y loger deux
    # blocs « libellé + case » côte à côte.
    banks = 2 if (thin and rows >= _TABLE_TWO_BANK_MIN_ROWS
                  and inner >= 2 * (_TABLE_ROWLAB_MIN_W + cols * _TABLE_MIN_COL_W)
                  + _TABLE_BANK_GAP) else 1
    avail = inner if banks == 1 else (inner - _TABLE_BANK_GAP) / 2

    # tableau TOUJOURS pleine largeur (une bande occupe tout `avail`). La colonne
    # ÉNONCÉ (rowlab_w) prend ce qu'il lui FAUT — pas plus : sinon elle absorbait
    # tout le blanc et les cases élève finissaient riquiqui (bug « colonne énoncé
    # pleine de blanc, colonne élève étroite »). On la dimensionne sur la largeur
    # NATURELLE du plus long libellé (bornée), et l'espace restant va aux CASES,
    # qui s'ÉLARGISSENT (scale ≥ 1) — priorité à l'espace d'écriture de l'élève.
    bank_w = avail
    if has_labels:
        # largeur naturelle du libellé le plus large (mesuré sans contrainte forte)
        solo_lab_w = max(8 * mm, bank_w - sum_desired - 2 * _TABLE_CELL_PAD)
        nat_lab = max((max((ln["w"] for ln in _rich_layout(str(lbl), solo_lab_w, lab_fs)["lines"]),
                           default=0.0) for lbl in row_labels), default=0.0)
        need = nat_lab + 2 * _TABLE_CELL_PAD
        # bornes : au moins _TABLE_ROWLAB_MIN_W, au plus ~55 % de la bande (et il
        # reste toujours de quoi loger les colonnes de réponse à leur minimum).
        hi = min(bank_w - cols * _TABLE_MIN_COL_W, bank_w * 0.55)
        rowlab_w = max(_TABLE_ROWLAB_MIN_W, min(need, max(_TABLE_ROWLAB_MIN_W, hi)))
    else:
        rowlab_w = 0.0
    cols_avail = bank_w - rowlab_w
    # mise à l'échelle proportionnelle : les colonnes gardent leurs proportions
    # relatives (mini < normal < wide). Le surplus (colonne énoncé bornée) ÉLARGIT
    # les cases (scale ≥ 1) au lieu d'être perdu en blanc dans la colonne énoncé.
    scale = (cols_avail / sum_desired) if sum_desired > 0 else 1.0
    col_ws = [d * scale for d in desired]
    col_offsets = [sum(col_ws[:j]) for j in range(cols)]
    total_w = banks * bank_w + (banks - 1) * _TABLE_BANK_GAP

    # bandeau de tête dimensionné sur les libellés RÉELS (répété dans chaque bande).
    col_lays = [_rich_layout(str(lbl), col_ws[j] - 2 * mm, lab_fs)
                for j, lbl in enumerate(col_labels or [])]
    head_h = (max([_TABLE_HEAD_H]
                  + [lay["height"] + 2 * _TABLE_CELL_PAD for lay in col_lays])
              if col_labels else 0.0)

    # libellés de ligne (énoncé de la case) — centrés, SANS pastille a./b./c.
    row_lays = [_rich_layout(str(lbl), max(8 * mm, rowlab_w - 2 * _TABLE_CELL_PAD), lab_fs)
                for lbl in (row_labels or [])]
    row_hs = []
    for i in range(rows):
        label_h = ((row_lays[i]["height"] + 2 * _TABLE_CELL_PAD)
                   if i < len(row_lays) else 0.0)
        # Une ligne gagne 3 mm dès qu'une de ses cellules ÉDITABLES attend une
        # fraction. Toutes les cellules de la ligne partagent nécessairement sa
        # hauteur dans un tableau.
        fraction_extra = FRACTION_EXTRA_H if any(
            _is_fraction_value(cell) and not (cell or {}).get("given")
            for cell in (cells[i] if i < len(cells) else [])) else 0.0
        row_hs.append(max(_TABLE_ROW_MIN_H + fraction_extra, label_h))

    # répartition des lignes par bande (indices d'origine conservés — l'ordre
    # row-major de cells_meta doit rester aligné sur expected_json.cells).
    if banks == 2:
        n0 = -(-rows // 2)  # ceil : la 1re bande porte la moitié haute
        bank_rows = [list(range(0, n0)), list(range(n0, rows))]
    else:
        bank_rows = [list(range(rows))]
    body_h = max((head_h + sum(row_hs[i] for i in br)) for br in bank_rows) if bank_rows else head_h

    return {"rows": rows, "cols": cols, "banks": banks, "head_h": head_h,
            "rowlab_w": rowlab_w, "grid_w": cols_avail, "col_ws": col_ws,
            "col_offsets": col_offsets, "col_kinds": col_kinds,
            "bank_w": bank_w, "total_w": total_w, "col_lays": col_lays,
            "row_lays": row_lays, "row_hs": row_hs, "lab_fs": lab_fs,
            "bank_rows": bank_rows, "height": body_h + 2 * mm}


def _table_zone_height(w: float, col_labels: list | None, row_labels: list | None,
                       cells: list[list[dict]], font_size: int,
                       sub_badge_color: Color | None = None) -> float:
    return _table_geometry(w, col_labels, row_labels, cells, font_size,
                           sub_badge_color)["height"]


def _matching_zone_height(left: list, right: list, font_size: int) -> float:
    n = max(len(left), len(right), 1)
    row_h = max(6.5 * mm, font_size + 4)
    return n * row_h + 3 * mm


def _zone_height(response_type: str, choices: list[str], width: float,
                 font_size: int, grading: dict | None = None,
                 inline: bool = False,
                 sub_badge_color: Color | None = None,
                 expected: dict | None = None) -> float:
    grading = grading or {}
    if response_type in ("qcm_single", "qcm_multiple"):
        _items, total_h, _ncols = _qcm_layout(choices, width - 2 * CARD_PAD, font_size)
        return total_h + 2.5 * mm
    if response_type == "checkbox_grid":
        return _grid_zone_height(width, grading.get("cols"), grading.get("rows"), font_size)
    if response_type == "short_text":
        return 0.0 if inline else 13 * mm + (
            FRACTION_EXTRA_H if _expected_has_fraction(expected) else 0.0)
    if response_type == "multi_blank":
        return 0.0  # cases dessinées en ligne dans l'énoncé, jamais de zone dédiée
    if response_type == "multiline_text":
        lines = max(3, min(12, int(grading.get("lines", 5))))
        return lines * MULTILINE_ROW_H + 4 * mm + (
            FRACTION_EXTRA_H if _expected_has_fraction(expected) else 0.0)
    if response_type == "table_fill":
        cells = grading.get("cells") or [[]]
        return _table_zone_height(width, grading.get("col_labels"),
                                  grading.get("row_labels"), cells, font_size,
                                  sub_badge_color)
    if response_type == "matching":
        return _matching_zone_height(grading.get("left", []), grading.get("right", []),
                                     font_size)
    if response_type == "manual_drawing":
        return _MANUAL_DRAWING_H
    return 13 * mm


def _cell_display_text(cell: dict) -> str:
    """Texte imprimé pour une cellule "given" (déjà donnée dans le manuel)."""
    ctype = cell.get("type")
    if ctype == "rational":
        num, den = cell["value"]
        return f"$\\dfrac{{{num}}}{{{den}}}$"
    if ctype == "decimal":
        return f"{cell['value']:g}"
    return str(cell.get("value", ""))


def _draw_table_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                     col_labels: list | None, row_labels: list | None,
                     cells: list[list[dict]], font_size: int,
                     sub_badge_color: Color | None = None) -> dict:
    geo = _table_geometry(w, col_labels, row_labels, cells, font_size,
                          sub_badge_color)
    cols = geo["cols"]
    head_h, lab_fs = geo["head_h"], geo["lab_fs"]
    col_ws, col_offsets = geo["col_ws"], geo["col_offsets"]
    rowlab_w, bank_w = geo["rowlab_w"], geo["bank_w"]
    inner = w - 2 * CARD_PAD
    # tableau CENTRÉ dans la carte (jamais justifié à droite)
    x0 = x + CARD_PAD + max(0.0, (inner - geo["total_w"]) / 2)
    grid_top = y + h - 1 * mm

    # géométrie par INDICE D'ORIGINE : cells_meta[i][j] doit rester aligné sur
    # expected_json.cells (correction case par case, OCR, overlay).
    cells_meta = [[None] * cols for _ in range(geo["rows"])]

    for b, rows_in_bank in enumerate(geo["bank_rows"]):
        bank_x = x0 + b * (bank_w + _TABLE_BANK_GAP)
        grid_x = bank_x + rowlab_w
        bank_body_h = head_h + sum(geo["row_hs"][i] for i in rows_in_bank)
        grid_bottom = grid_top - bank_body_h

        # cadre saumon de TOUTE la bande (colonne énoncé + colonnes cases)
        c.setStrokeColor(DROPOUT)
        c.setLineWidth(0.7)
        c.rect(bank_x, grid_bottom, bank_w, bank_body_h, stroke=1, fill=0)
        c.setLineWidth(0.5)
        if col_labels:
            c.setFillColor(black)
            for j, lay in enumerate(geo["col_lays"]):
                _draw_rich(c, grid_x + col_offsets[j] + 1 * mm,
                           grid_top - (head_h - lay["height"]) / 2, lay,
                           centered=True, width=col_ws[j] - 2 * mm)
            c.setStrokeColor(DROPOUT)
            c.line(bank_x, grid_top - head_h, bank_x + bank_w, grid_top - head_h)
        # séparateurs verticaux (toute la hauteur) : colonne énoncé | cases, puis
        # entre colonnes de cases.
        c.setStrokeColor(DROPOUT)
        if rowlab_w > 0:
            c.line(grid_x, grid_bottom, grid_x, grid_top)
        for j in range(1, cols):
            cx_sep = grid_x + col_offsets[j]
            c.line(cx_sep, grid_bottom, cx_sep, grid_top)

        ry_top = grid_top - head_h
        for local_i, i in enumerate(rows_in_bank):
            row_h = geo["row_hs"][i]
            if local_i > 0:
                c.setStrokeColor(DROPOUT)
                c.setLineWidth(0.5)
                c.line(bank_x, ry_top, bank_x + bank_w, ry_top)
            # énoncé de la ligne : centré (H et V) dans la colonne énoncé
            if rowlab_w > 0 and i < len(geo["row_lays"]):
                lay = geo["row_lays"][i]
                _draw_rich(c, bank_x + _TABLE_CELL_PAD, ry_top - (row_h - lay["height"]) / 2,
                           lay, centered=True, width=rowlab_w - 2 * _TABLE_CELL_PAD)
            for j in range(cols):
                col_w = col_ws[j]
                cx = grid_x + col_offsets[j]
                # La case REMPLIT sa cellule (un simple retrait pour ne pas
                # toucher les traits du tableau) : c'est la cellule qui est
                # dimensionnée sur la réponse attendue (cf. _col_kind /
                # _COL_KIND_WIDTH), la case n'a aucune raison d'être plus petite
                # qu'elle. Avant, une case plafonnée à 20x8 mm laissait le reste
                # de la cellule en blanc perdu, et l'élève écrivait à l'étroit.
                bw = max(4 * mm, col_w - 2 * _TABLE_CELL_PAD)
                bh = max(4 * mm, row_h - 2 * _TABLE_CELL_PAD)
                bx = cx + (col_w - bw) / 2
                by = ry_top - row_h + (row_h - bh) / 2
                cell = cells[i][j] if i < len(cells) and j < len(cells[i]) else None
                if cell and cell.get("given"):
                    c.setFillColor(black)
                    lay = _rich_layout(_cell_display_text(cell), col_w - 2 * mm, lab_fs)
                    _draw_rich(c, cx + 1 * mm, ry_top - (row_h - lay["height"]) / 2, lay,
                               centered=True, width=col_w - 2 * mm)
                else:
                    c.setStrokeColor(DROPOUT)
                    c.setLineWidth(0.7)
                    c.roundRect(bx, by, bw, bh, 0.8 * mm)
                cells_meta[i][j] = {"x_pt": bx, "y_pt": by, "w_pt": bw, "h_pt": bh}
            ry_top -= row_h
    c.setStrokeColor(black)
    c.setFillColor(black)
    return {"cells": cells_meta}


def _draw_matching_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                        left: list[str], right: list[str], font_size: int) -> dict:
    n = max(len(left), len(right), 1)
    row_h = (h - 3 * mm) / n
    col_w = (w - 2 * CARD_PAD - _MATCHING_COL_GAP) / 2
    p = _MATCHING_PASTILLE
    top = y + h - 2 * mm

    def _pastille(px: float, py: float) -> None:
        c.setStrokeColor(DROPOUT)
        c.setFillColor(white)
        c.circle(px + p / 2, py + p / 2, p / 2, stroke=1, fill=1)
        c.setFillColor(black)

    left_pts, right_pts = [], []
    for i, label in enumerate(left):
        ly = top - i * row_h - row_h / 2
        lay = _rich_layout(label, col_w - p - 3 * mm, font_size)
        _draw_rich(c, x + CARD_PAD, ly + lay["height"] / 2, lay)
        px, py = x + CARD_PAD + col_w - p - 1 * mm, ly - p / 2
        _pastille(px, py)
        left_pts.append({"index": i, "x_pt": px, "y_pt": py, "w_pt": p, "h_pt": p})
    for i, label in enumerate(right):
        ry = top - i * row_h - row_h / 2
        px = x + CARD_PAD + col_w + _MATCHING_COL_GAP
        py = ry - p / 2
        _pastille(px, py)
        lay = _rich_layout(label, col_w - p - 3 * mm, font_size)
        _draw_rich(c, px + p + 2 * mm, ry + lay["height"] / 2, lay)
        right_pts.append({"index": i, "x_pt": px, "y_pt": py, "w_pt": p, "h_pt": p})
    c.setFillColor(black)
    c.setStrokeColor(black)
    return {"left_points": left_pts, "right_points": right_pts}


# ---- grille cochée (checkbox_grid) : une case cochée par ligne, lue par CV ----
# Mêmes cases + fenêtre de détection que le QCM (worker_cv.qcm_densities/select) :
# la grille est un QCM à choix unique PAR LIGNE. La 1re colonne porte l'énoncé de
# la sous-question, les suivantes une case cochable par option (Vrai/Faux…).
_GRID_HEAD_MIN_H = 5.5 * mm
_GRID_CELL_PAD = 1.4 * mm
_GRID_BOX = 3.2 * mm                  # case cochable imprimée (confort > QCM 2 mm)
_GRID_OPT_MIN_W = 13.0 * mm           # largeur mini d'une colonne d'option
_GRID_ROWLAB_MIN_W = 24.0 * mm
_GRID_ROW_MIN_H = max(_GRID_BOX + 2 * _GRID_CELL_PAD, 7.0 * mm)
_GRID_DETECT_MARGIN = 1.0 * mm


def _grid_geometry(w: float, cols: list[str], rows: list[dict], font_size: int) -> dict:
    ncols = max(1, len(cols))
    inner = w - 2 * CARD_PAD
    lab_fs = max(6, font_size - 1)
    # colonne d'option = assez large pour son libellé de tête + la case
    head_nat = max((max((ln["w"] for ln in _rich_layout(str(cl), 30 * mm, lab_fs)["lines"]),
                        default=0.0) for cl in cols), default=0.0)
    opt_w = max(_GRID_OPT_MIN_W, head_nat + 2 * _GRID_CELL_PAD)
    rowlab_w = inner - ncols * opt_w
    if rowlab_w < _GRID_ROWLAB_MIN_W:      # colonne énoncé trop étroite : on rogne les options
        opt_w = max(_GRID_BOX + 2 * _GRID_CELL_PAD, (inner - _GRID_ROWLAB_MIN_W) / ncols)
        rowlab_w = inner - ncols * opt_w
    head_lays = [_rich_layout(str(cl), opt_w - 2 * mm, lab_fs) for cl in cols]
    head_h = max([_GRID_HEAD_MIN_H] + [lay["height"] + 2 * _GRID_CELL_PAD for lay in head_lays])
    row_lays = [_rich_layout(str(r.get("label", "")),
                             max(8 * mm, rowlab_w - 2 * _GRID_CELL_PAD), lab_fs) for r in rows]
    row_hs = [max(_GRID_ROW_MIN_H, lay["height"] + 2 * _GRID_CELL_PAD) for lay in row_lays]
    body_h = head_h + sum(row_hs)
    return {"ncols": ncols, "opt_w": opt_w, "rowlab_w": rowlab_w, "head_h": head_h,
            "head_lays": head_lays, "row_lays": row_lays, "row_hs": row_hs,
            "lab_fs": lab_fs, "height": body_h + 2 * mm}


def _grid_zone_height(w: float, cols: list, rows: list, font_size: int) -> float:
    return _grid_geometry(w, cols or [], rows or [], font_size)["height"]


def _draw_grid_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                    cols: list[str], rows: list[dict], font_size: int) -> dict:
    geo = _grid_geometry(w, cols, rows, font_size)
    ncols, opt_w, rowlab_w, head_h = geo["ncols"], geo["opt_w"], geo["rowlab_w"], geo["head_h"]
    inner = w - 2 * CARD_PAD
    grid_w = rowlab_w + ncols * opt_w
    x0 = x + CARD_PAD + max(0.0, (inner - grid_w) / 2)       # grille centrée
    grid_top = y + h - 1 * mm
    body_h = head_h + sum(geo["row_hs"])
    grid_bottom = grid_top - body_h
    grid_x = x0 + rowlab_w                                    # début des colonnes d'option

    c.setStrokeColor(DROPOUT)
    c.setLineWidth(0.7)
    c.rect(x0, grid_bottom, grid_w, body_h, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.setFillColor(black)
    for j, lay in enumerate(geo["head_lays"]):               # libellés de colonnes
        _draw_rich(c, grid_x + j * opt_w + 1 * mm, grid_top - (head_h - lay["height"]) / 2,
                   lay, centered=True, width=opt_w - 2 * mm)
    c.setStrokeColor(DROPOUT)
    c.line(x0, grid_top - head_h, x0 + grid_w, grid_top - head_h)
    c.line(grid_x, grid_bottom, grid_x, grid_top)            # séparateur énoncé | options
    for j in range(1, ncols):
        cx = grid_x + j * opt_w
        c.line(cx, grid_bottom, cx, grid_top)

    boxes = []
    ry_top = grid_top - head_h
    for i, r in enumerate(rows):
        row_h = geo["row_hs"][i]
        if i > 0:
            c.setStrokeColor(DROPOUT)
            c.setLineWidth(0.5)
            c.line(x0, ry_top, x0 + grid_w, ry_top)
        lay = geo["row_lays"][i]                              # énoncé de la sous-question
        c.setFillColor(black)
        _draw_rich(c, x0 + _GRID_CELL_PAD, ry_top - (row_h - lay["height"]) / 2, lay,
                   width=rowlab_w - 2 * _GRID_CELL_PAD)
        for j in range(ncols):
            cx = grid_x + j * opt_w
            bw = bh = _GRID_BOX
            bx = cx + (opt_w - bw) / 2
            by = ry_top - row_h + (row_h - bh) / 2
            c.setStrokeColor(DROPOUT)
            c.setLineWidth(0.8)
            c.rect(bx, by, bw, bh, stroke=1, fill=0)
            dm = _GRID_DETECT_MARGIN
            boxes.append({"row": i, "col": j, "index": i * ncols + j,
                          "x_pt": bx, "y_pt": by, "w_pt": bw, "h_pt": bh,
                          "detect": {"x_pt": bx - dm, "y_pt": by - dm,
                                     "w_pt": bw + 2 * dm, "h_pt": bh + 2 * dm}})
        ry_top -= row_h
    c.setStrokeColor(black)
    c.setFillColor(black)
    return {"boxes": boxes, "ncols": ncols}


def _draw_answer_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                      response_type: str, choices: list[str], font_size: int,
                      grading: dict | None = None,
                      sub_badge_color: Color | None = None) -> dict:
    """Zone de réponse ÉLÈVE en saumon (dropout). Retourne la méta (positions
    des cases QCM, cellules de tableau, pastilles de points à relier…)."""
    grading = grading or {}
    meta = {}
    c.setStrokeColor(DROPOUT)
    c.setLineWidth(0.9)
    if response_type in ("qcm_single", "qcm_multiple"):
        items, _total_h, _ncols = _qcm_layout(choices, w - 2 * CARD_PAD, font_size)
        boxes = []
        top = y + h - 2 * mm
        for it in items:
            # case ÉLÈVE décalée à droite de QCM_CORR_RESERVE : la place à sa
            # gauche est réservée à la case de correction que l'overlay imprime
            # systématiquement (vide si non attendue, pleine si attendue).
            bx = x + CARD_PAD + it["dx"] + QCM_CORR_RESERVE
            row_top = top - it["dy"]
            # La case se cale sur le TEXTE (centre de case sur la hauteur d'œil
            # de la 1re ligne du label), et non l'inverse : la poser à partir du
            # haut de la ligne la laissait flotter sous le texte.
            first_asc = it["lay"]["lines"][0]["asc"] if it["lay"]["lines"] else font_size * 0.78
            y_base = row_top - first_asc
            by = y_base + font_size * 0.35 - it["box"] / 2
            c.setStrokeColor(DROPOUT)
            c.rect(bx, by, it["box"], it["box"])
            _draw_rich(c, bx + it["box"] + 1.6 * mm, row_top, it["lay"])
            c.setStrokeColor(DROPOUT)
            box = it["box"]
            corr_x = bx - QCM_CORR_GAP - box   # case correction (overlay), à gauche
            dm, dmr = QCM_DETECT_MARGIN, QCM_DETECT_MARGIN_R
            boxes.append({"index": it["index"],
                          # géométrie de la case ÉLÈVE réellement imprimée : sert
                          # au marquage d'overlay (coche/croix centrée sur la case)
                          "x_pt": bx, "y_pt": by, "w_pt": box, "h_pt": box,
                          # fenêtre de DÉTECTION élargie (robuste au recalage et à
                          # la coche qui déborde), marge droite réduite (label) —
                          # lue par worker_cv.detect_qcm
                          "detect": {"x_pt": bx - dm, "y_pt": by - dm,
                                     "w_pt": box + dm + dmr, "h_pt": box + 2 * dm},
                          "correction_box": {"x_pt": corr_x, "y_pt": by,
                                             "w_pt": box, "h_pt": box}})
        meta["boxes"] = boxes
    elif response_type == "checkbox_grid":
        meta = _draw_grid_zone(c, x, y, w, h, grading.get("cols") or [],
                               grading.get("rows") or [], font_size)
    elif response_type == "table_fill":
        meta = _draw_table_zone(c, x, y, w, h, grading.get("col_labels"),
                                grading.get("row_labels"), grading.get("cells") or [],
                                font_size, sub_badge_color)
    elif response_type == "matching":
        meta = _draw_matching_zone(c, x, y, w, h, grading.get("left", []),
                                   grading.get("right", []), font_size)
    elif response_type == "manual_drawing":
        c.roundRect(x + CARD_PAD, y + 1 * mm, w - 2 * CARD_PAD, h - 2 * mm, 1.5 * mm)
    else:
        c.roundRect(x + CARD_PAD, y + 1 * mm, w - 2 * CARD_PAD, h - 2 * mm, 1.5 * mm)
        if response_type == "multiline_text":
            c.setLineWidth(0.35)
            line_gap = MULTILINE_ROW_H
            ly = y + h - 1 * mm - line_gap
            while ly > y + 3 * mm:
                c.line(x + CARD_PAD + 1.5 * mm, ly, x + w - CARD_PAD - 1.5 * mm, ly)
                ly -= line_gap
    c.setFillColor(black)
    return meta


def _correction_strip_layout(correction: str, w: float, statement_fs: float,
                             guides: str = GUIDES_OVERLAY) -> dict:
    """Cadre corrigé sous une carte, dimensionné pour contenir le TEXTE du
    corrigé de la banque — ANTICIPÉ à la composition du sujet pour que l'overlay
    de correction puisse l'imprimer en entier (jamais coupé). Le corrigé est mis
    en page comme un énoncé (flot riche : formules $...$ rasterisées, sauts de
    ligne durs de services.statement), à un corps un cran plus petit que
    l'énoncé (CORR_FS_DELTA). Une réserve droite (STRIP_NOTE_W) laisse la place à
    la note de barème, imprimée en gros et gras. Retourne
    {height, fs, text_w, lay, guides} — `lay` sert au SUJET (mesure) ; l'overlay
    le recompose à l'identique depuis le texte et `fs` stockés dans la méta.

    `guides` (cf. GUIDES_*) : GUIDES_NONE ramène la bande à son plancher (note
    de barème seule, aucun texte composé) ; GUIDES_PRINT garde exactement la
    même hauteur que GUIDES_OVERLAY et ne change que le dessin."""
    fs = max(6.0, statement_fs - CORR_FS_DELTA)
    text_w = max(10 * mm, w - 2 * CARD_PAD - STRIP_NOTE_W)
    if guides == GUIDES_NONE:
        return {"height": STRIP_MIN_H, "fs": fs, "text_w": text_w,
                "lay": _rich_layout("", text_w, fs), "guides": GUIDES_NONE}
    lay = _rich_layout(statement_mod.normalize(correction or ""), text_w, fs)
    height = max(STRIP_MIN_H, lay["height"] + STRIP_PAD_TOP + STRIP_PAD_BOT)
    return {"height": height, "fs": fs, "text_w": text_w, "lay": lay,
            "guides": guides}


def item_guides_mode(item: dict) -> str:
    """Mode de guide d'une carte, normalisé (défaut = comportement historique)."""
    g = item.get("guides") or GUIDES_OVERLAY
    return g if g in (GUIDES_OVERLAY, GUIDES_PRINT, GUIDES_NONE) else GUIDES_OVERLAY


def _exercise_card_h(layout: dict, zone_h: float, strip_h: float,
                     tpl: dict) -> float:
    """Hauteur totale de l'unité (carte + espace + bande de correction),
    toujours placée d'un bloc (jamais coupée par saut de colonne/page).

    Plus de ligne de titre : le badge numéroté vit DANS la 1re ligne de
    l'énoncé (layout["intro"], dimensionnée par _badge_min_asc), et la hauteur
    d'en-tête qu'elle coûtait est rendue au contenu. `strip_h` est la hauteur de
    la bande corrigé, anticipée sur son texte (_correction_strip_layout)."""
    return layout["height"] + zone_h + strip_h + STRIP_GAP + 3 * CARD_PAD


def _draw_calc_icon(c: canvas.Canvas, x_right: float, y_top: float, size: float,
                    forbidden: bool) -> None:
    """Petite icône calculette au coin haut-droit d'une carte. `forbidden` la
    barre en rouge (interdite) ; sinon bleue (nécessaire). Rien n'est dessiné
    pour « autorisée » (l'appelant ne nous appelle pas dans ce cas)."""
    w, h = size, size * 1.28
    x, y = x_right - w, y_top - h
    accent = HexColor("#C0392B") if forbidden else HexColor("#2D6CDF")
    c.saveState()
    c.setLineWidth(0.8)
    c.setStrokeColor(accent)
    c.setFillColor(white)
    c.roundRect(x, y, w, h, size * 0.16, stroke=1, fill=1)
    c.setFillColor(HexColor("#F7E4E1") if forbidden else HexColor("#E8EEF9"))
    c.rect(x + w * 0.16, y + h * 0.66, w * 0.68, h * 0.2, stroke=0, fill=1)   # écran
    c.setFillColor(accent)
    for r in range(3):                                                        # touches
        for col in range(3):
            c.circle(x + w * 0.26 + col * w * 0.24, y + h * 0.14 + r * h * 0.16,
                     size * 0.05, stroke=0, fill=1)
    if forbidden:
        c.setStrokeColor(HexColor("#C0392B"))
        c.setLineWidth(1.3)
        c.line(x, y, x + w, y + h)
    c.restoreState()


def _draw_exercise_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                        seq: int, layout: dict, zone_h: float, strip: dict,
                        level5: int, response_type: str, choices: list[str],
                        tpl: dict, font_size: float, zone_fs: float,
                        grading: dict | None = None,
                        calc: str = "autorisee",
                        probleme: bool = False) -> tuple[float, dict, dict]:
    """Carte exercice + bande de correction hors carte (§ correction).
    Retourne (hauteur totale, geo zone réponse, meta).

    `font_size` est le corps du gabarit — celui sur lequel _exercise_layout a
    dimensionné le badge numéroté et son retrait ; `zone_fs` celui qui a servi à
    mesurer `zone_h` ; `strip` la bande corrigé (_correction_strip_layout, sa
    hauteur anticipée sur le texte du corrigé). Tous viennent de
    _exercise_layout et ne se redérivent pas ici : les redériver, c'est les
    désaccorder de la mesure. Le corps de l'énoncé, lui, n'est plus une affaire
    de carte du tout — chaque ligne porte le sien (cf. _rich_layout)."""
    border = HexColor(tpl.get("border", "#C7CDD4"))
    radius = max(0.0, float(tpl.get("radius", 2.2))) * mm
    strip_h = strip["height"]
    card_h = _exercise_card_h(layout, zone_h, strip_h, tpl)
    y = y_top - card_h                              # bas de l'unité entière (carte + strip)
    card_bottom = y + strip_h + STRIP_GAP            # bas de la carte seule (strip exclue)
    card_h_body = card_h - strip_h - STRIP_GAP

    # ombre puis carte (la bordure s'arrête avant la bande de correction)
    if tpl.get("shadow", True):
        c.setFillColor(CARD_SHADOW)
        c.roundRect(x + 1.1, card_bottom - 1.3, w, card_h_body, radius, stroke=0, fill=1)
    c.setFillColor(white)
    c.setStrokeColor(border)
    c.setLineWidth(0.9)
    c.roundRect(x, card_bottom, w, card_h_body, radius, stroke=1, fill=1)

    # énoncé riche (texte + formules PNG), expression finale mise en valeur,
    # figure paramétrée éventuelle ; `blanks` récupère la géométrie d'une
    # éventuelle case de réponse courte insérée en ligne (short_text inline).
    # Le badge numéroté occupe le retrait réservé en tête de 1re ligne.
    ty = card_bottom + card_h_body - CARD_PAD
    inline_blanks: list = []
    badge_color = _exercise_badge_color(level5, probleme)
    first = layout["intro"]["lines"][0] if layout["intro"]["lines"] else None
    _draw_badge(c, x + CARD_PAD, ty - (first["asc"] if first else font_size * 0.78),
                font_size, str(seq), badge_color)
    line_y = _draw_rich(c, x + CARD_PAD, ty, layout["intro"], blanks=inline_blanks)
    # figure INSÉRÉE au marqueur {{figure}} : image entre l'avant et l'après de
    # l'énoncé (§ demande utilisateur — image placée au bon endroit, pas en fin).
    if layout.get("figure_inline") and layout["figure"]:
        fimg, fw, fh = layout["figure"]
        c.drawImage(fimg, x + (w - fw) / 2, line_y - _FIG_MARKER_GAP - fh, width=fw,
                    height=fh, mask="auto", preserveAspectRatio=True)
        line_y -= 2 * _FIG_MARKER_GAP + fh
        if layout.get("intro_after"):
            line_y = _draw_rich(c, x + CARD_PAD, line_y, layout["intro_after"],
                                blanks=inline_blanks)
        if layout["display"]:
            img, dw, dh, _dd = layout["display"]
            c.drawImage(img, x + (w - dw) / 2, line_y - dh - 1 * mm, width=dw,
                        height=dh, mask="auto", preserveAspectRatio=True)
            line_y -= dh + 2.5 * mm
    else:
        if layout["display"]:
            img, dw, dh, _dd = layout["display"]
            c.drawImage(img, x + (w - dw) / 2, line_y - dh - 1 * mm, width=dw,
                        height=dh, mask="auto", preserveAspectRatio=True)
            line_y -= dh + 2.5 * mm
        if layout["figure"]:
            fimg, fw, fh = layout["figure"]
            c.drawImage(fimg, x + (w - fw) / 2, line_y - fh - 0.5 * mm, width=fw,
                        height=fh, mask="auto", preserveAspectRatio=True)
    c.setFillColor(black)

    # icône calculette (exercices Indigo) au coin haut-droit de la carte :
    # nécessaire => bleue, interdite => barrée rouge, autorisée => rien.
    if calc in ("necessaire", "interdite"):
        _draw_calc_icon(c, x + w - 1.4 * mm, card_bottom + card_h_body - 1.4 * mm,
                        3.6 * mm, forbidden=(calc == "interdite"))

    # zone réponse élève (saumon) — sauf short_text/multi_blank inline : la ou
    # les case(s) font déjà partie de l'énoncé (inline_blanks), pas de zone
    # dédiée sous le texte
    zone_y = card_bottom + CARD_PAD
    if response_type == "short_text" and inline_blanks:
        b = inline_blanks[0]
        zone_geo = {"x_pt": b["x_pt"], "y_pt": b["y_pt"], "w_pt": b["w_pt"], "h_pt": b["h_pt"]}
        meta = {}
    elif response_type == "multi_blank" and inline_blanks:
        # une case par occurrence de {{blank}}, stockées comme une unique
        # "ligne" de cellules — même forme que table_fill (meta["cells"]),
        # réutilisée telle quelle par la correction (services.pipeline).
        xs0 = min(b["x_pt"] for b in inline_blanks)
        ys0 = min(b["y_pt"] for b in inline_blanks)
        xs1 = max(b["x_pt"] + b["w_pt"] for b in inline_blanks)
        ys1 = max(b["y_pt"] + b["h_pt"] for b in inline_blanks)
        zone_geo = {"x_pt": xs0, "y_pt": ys0, "w_pt": xs1 - xs0, "h_pt": ys1 - ys0}
        meta = {"cells": [[{"x_pt": b["x_pt"], "y_pt": b["y_pt"],
                            "w_pt": b["w_pt"], "h_pt": b["h_pt"]} for b in inline_blanks]]}
    else:
        meta = _draw_answer_zone(c, x, zone_y, w, zone_h, response_type, choices,
                                 zone_fs, grading, badge_color)
        zone_geo = {"x_pt": x, "y_pt": zone_y, "w_pt": w, "h_pt": zone_h}

    # bande de correction : HORS carte, collée (espace blanc visible, jamais
    # coupée par saut de colonne/page), cadre invisible sur le sujet imprimé —
    # la géométrie reste réservée pour l'overlay de correction.
    meta["correction_strip"] = _strip_meta(c, x, y, w, strip)
    c.setFillColor(black)
    return card_h, zone_geo, meta


def _strip_meta(c: canvas.Canvas, x: float, y: float, w: float,
                strip: dict) -> dict:
    """Géométrie de la bande corrigé stockée dans la méta de zone (relue par
    l'overlay, cf. services.pipeline), et — en mode GUIDES_PRINT — dessin du
    guide directement sur le sujet. `y` est le BAS de l'unité carte+bande."""
    strip_h = strip["height"]
    geo = {"x_pt": x + CARD_PAD, "y_pt": y + STRIP_PAD_BOT,
           "w_pt": w - 2 * CARD_PAD, "h_pt": strip_h - STRIP_PAD_TOP - STRIP_PAD_BOT,
           "fs": strip["fs"], "guides": strip.get("guides", GUIDES_OVERLAY)}
    if strip.get("guides") == GUIDES_PRINT and strip["lay"]["height"] > 0:
        c.setFillColor(GUIDE_BG)
        c.roundRect(geo["x_pt"] - 1.0 * mm, geo["y_pt"] - 0.6 * mm,
                    geo["w_pt"] + 2.0 * mm, geo["h_pt"] + 1.4 * mm,
                    1.2 * mm, stroke=0, fill=1)
        _draw_rich(c, geo["x_pt"], geo["y_pt"] + geo["h_pt"], strip["lay"],
                   color=GUIDE_TEXT)
    return geo


_ADMONITION_KINDS = ("rappel", "conseil", "attention")


def _lesson_layout(blocks: dict, width: float, fs: float) -> dict:
    """Met en page un rappel structuré v4. Retourne {parts, height}.
    parts = liste de (type, layout|image|str, extra) empilés verticalement.
    L'essentiel et les encarts (conseil/attention) sont mis en page en
    admonitions à icône de marge (largeur réduite de ADMONITION_GUTTER) ;
    méthode/exemple restent un flot pleine largeur avec sous-titre."""
    inner = width - 2 * CARD_PAD
    admo_w = max(10 * mm, inner - ADMONITION_GUTTER)
    parts: list[tuple] = []
    height = 0.0

    def _push(kind, text, indent=0.0, gap=1.2 * mm, font_fs=fs):
        nonlocal height
        lay = _rich_layout(text, inner - indent, font_fs)
        parts.append((kind, lay, indent, font_fs, gap))
        height += lay["height"] + gap

    def _push_subtitle(text):
        nonlocal height
        parts.append(("subtitle", text, 0.0, fs, 0.7 * mm))
        height += fs * 0.9 + 0.7 * mm

    def _push_admonition(kind, text, gap=2.3 * mm):
        nonlocal height
        lay = _rich_layout(text, admo_w, fs)
        parts.append((kind, lay, 0.0, fs, gap))
        height += lay["height"] + gap

    if blocks.get("essentiel"):
        _push_admonition("rappel", blocks["essentiel"])

    methode = blocks.get("methode") or []
    if methode:
        _push_subtitle("Méthode")
        for i, step in enumerate(methode):
            _push("methode", f"{i + 1}. {step}", indent=1.5 * mm, gap=0.6 * mm)

    ex = blocks.get("exemple") or {}
    if ex.get("enonce"):
        _push_subtitle("Exemple résolu")
        height += 1.2 * mm  # respiration avant l'encadré exemple
        parts.append(("exemple_start", None, 0.0, fs, 0.0))
        _push("exemple", ex["enonce"], indent=2 * mm)
        for step in ex.get("etapes") or []:
            _push("exemple", step, indent=4 * mm, gap=0.6 * mm)
        if ex.get("resultat"):
            _push("exemple", ex["resultat"], indent=2 * mm)
        parts.append(("exemple_end", None, 0.0, fs, 0.0))
        height += 2.2 * mm

    encarts = blocks.get("encarts")
    if not encarts and blocks.get("astuce"):  # compat rappels générés avant v4
        encarts = [{"type": "conseil", "texte": blocks["astuce"]}]
    for enc in (encarts or [])[:3]:
        etype = enc.get("type") if enc.get("type") in ("conseil", "attention") else "conseil"
        texte = str(enc.get("texte") or "").strip()
        if texte:
            _push_admonition(etype, texte)

    figure = _figure_image(blocks.get("figure"), min(inner, 55 * mm), 32 * mm)
    if figure:
        parts.append(("figure", figure, 0.0, fs, 1.5 * mm))
        height += figure[2] + 1.5 * mm
    return {"parts": parts, "height": height}


def _lesson_card_h(layout: dict, tpl: dict) -> float:
    return 5 * mm + layout["height"] + 2.5 * CARD_PAD


def _draw_lesson_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                      title: str, layout: dict, tpl: dict) -> float:
    """Cadre rappel de leçon structuré : fond ambre, icône livre, l'essentiel
    en admonition (icône de marge), sous-titres Méthode/Exemple, méthode
    numérotée, exemple résolu encadré, encarts conseil/attention à icône et
    teinte dédiées, figure éventuelle."""
    fs = max(6, float(tpl.get("font_size", 8)))
    bg = HexColor(tpl.get("bg", "#FFF6DF"))
    border = HexColor(tpl.get("border", "#E4C46A"))
    text_color = HexColor(tpl.get("text", "#6B5310"))
    head_h = 5 * mm
    card_h = _lesson_card_h(layout, tpl)
    y = y_top - card_h

    c.setFillColor(bg)
    c.setStrokeColor(border)
    c.setLineWidth(0.9)
    c.roundRect(x, y, w, card_h, RADIUS, stroke=1, fill=1)

    ty = y + card_h - head_h
    _icon_book(c, x + CARD_PAD + 1.6 * mm, ty + 0.6 * mm, color=text_color)
    c.setFillColor(text_color)
    c.setFont(_font("bold"), _subject_font_size(fs))
    c.drawString(x + CARD_PAD + 4.4 * mm, ty + 0.8 * mm, _pdf_safe(title)[:80])

    inner = w - 2 * CARD_PAD
    gutter_x = x + CARD_PAD
    line_y = ty - 1.2 * mm
    example_top = None
    for kind, payload, indent, part_fs, part_gap in layout["parts"]:
        if kind == "subtitle":
            c.setFillColor(border)
            c.setFont(_font("bold"),
                      _subject_font_size(max(6.5, part_fs - 0.5)))
            c.drawString(gutter_x, line_y - part_fs * 0.72, _pdf_safe(payload).upper())
            line_y -= part_fs * 0.9 + part_gap
            continue
        if kind == "exemple_start":
            line_y -= 1.6 * mm
            example_top = line_y
            continue
        if kind == "exemple_end":
            # encadré blanc translucide derrière l'exemple, redessiné dessous :
            # on trace seulement un filet vertical discret à gauche (citation)
            c.setStrokeColor(border)
            c.setLineWidth(1.4)
            c.line(gutter_x + 0.4 * mm, line_y + 0.6 * mm,
                   gutter_x + 0.4 * mm, example_top - 0.4 * mm)
            line_y -= 2.2 * mm
            example_top = None
            continue
        if kind == "figure":
            fimg, fw, fh = payload
            c.drawImage(fimg, x + (w - fw) / 2, line_y - fh, width=fw, height=fh,
                        mask="auto", preserveAspectRatio=True)
            line_y -= fh + 1.5 * mm
            continue
        if kind in _ADMONITION_KINDS:
            block_h = payload["height"]
            text_x = gutter_x + ADMONITION_GUTTER
            icon_y = line_y - part_fs * 0.7
            if kind == "rappel":
                txt_color = text_color
                _icon_book(c, gutter_x + 1.7 * mm, icon_y, size=3.2 * mm, color=text_color)
            else:
                style = _ADMONITION_COLORS[kind]
                pad_v = 0.9 * mm
                c.setFillColor(style["bg"])
                c.roundRect(gutter_x, line_y - block_h - pad_v,
                           inner, block_h + 2 * pad_v, 1.3 * mm, stroke=0, fill=1)
                icon_fn = _icon_bulb if kind == "conseil" else _icon_warning
                icon_fn(c, gutter_x + 1.7 * mm, icon_y, size=3.2 * mm, color=style["border"])
                txt_color = style["text"]
            font = _font("italic") if kind == "rappel" else _font()
            _draw_rich(c, text_x, line_y, payload, color=txt_color, font=font)
            line_y -= block_h + part_gap
            continue
        _draw_rich(c, gutter_x + indent, line_y, payload,
                   color=text_color, font=_font())
        line_y -= payload["height"] + part_gap
    c.setFillColor(black)
    return card_h


# ------------------------------------------------------------- copie entière

# Géométrie verticale d'une colonne — UNE seule définition, partagée par le
# placement réel (render_copy) et sa simulation (pages_needed) : deux règles
# distinctes dériveraient, et c'est précisément l'écart entre « ce qu'on croit
# faire tenir » et « ce qui tient » qui fait déborder une copie.
_BOTTOM_LIMIT = MARGIN + QR_MINI + 3 * mm


def _top_of_page(page_idx: int) -> float:
    """Ordonnée de départ d'une colonne : la 1re page porte l'en-tête élève,
    les suivantes le QR principal."""
    return (PAGE_H - MARGIN - HEADER_H - 4 * mm) if page_idx == 0 \
        else (PAGE_H - MARGIN - QR_MAIN - 6 * mm)


def pages_needed(heights: list[float]) -> int:
    """Nombre de pages qu'occuperaient des cartes de ces hauteurs (dans cet
    ordre), en appliquant EXACTEMENT la règle de placement de `render_copy` :
    on remplit la colonne de gauche, puis celle de droite, puis on change de
    page — et une carte ne se coupe JAMAIS en deux.

    C'est ce qui remplace l'ancienne `estimate_capacity` : comparer la SOMME
    des hauteurs à la hauteur totale disponible ignorait le bas de colonne
    perdu dès qu'une carte n'y rentre plus. Une copie remplie au plus près de
    la capacité théorique (99 %) débordait donc systématiquement d'une page —
    d'autant plus visible depuis que la banque offre assez d'exercices
    distincts pour vraiment remplir (cf. suppression du plafond de 3)."""
    page_idx, col = 0, 0
    y = _top_of_page(0)
    for h in heights:
        if y - h < _BOTTOM_LIMIT:
            if col == 0:
                col = 1
                y = _top_of_page(page_idx)
                if y - h < _BOTTOM_LIMIT:   # carte plus haute qu'une colonne
                    page_idx, col, y = page_idx + 1, 0, _top_of_page(page_idx + 1)
            else:
                page_idx, col, y = page_idx + 1, 0, _top_of_page(page_idx + 1)
        y -= h
    return page_idx + 1


def column_metrics(pages: int) -> dict:
    """Géométrie des colonnes d'un sujet de `pages` pages, en points PDF —
    servie telle quelle à l'assistant « Créer mon sujet », qui dessine ses
    pages à l'échelle et doit connaître EXACTEMENT la place disponible (la 1re
    page perd la hauteur de l'en-tête élève). Une seule définition, ici : une
    capacité recalculée côté navigateur dériverait du rendu réel."""
    return {
        "page_w": PAGE_W, "page_h": PAGE_H, "col_w": COL_W, "col_gap": COL_GAP,
        "margin": MARGIN, "gap": GAP, "cols_per_page": 2,
        "column_h": [_top_of_page(p) - _BOTTOM_LIMIT for p in range(max(1, pages))],
    }


def pack_reading_order(heights: list[float]) -> list[int]:
    """Ordonne des cartes (hauteurs dans l'ordre d'origine) pour un remplissage
    en colonnes SANS bas de colonne perdu, et retourne leurs index d'origine
    dans l'ordre de LECTURE (haut→bas, colonne gauche puis droite, page par
    page).

    Le placement de `render_copy` est glouton en colonnes : il remplit la
    colonne de gauche jusqu'en bas, passe à droite, puis change de page. Pris
    dans l'ordre où le LLM les a produites, les cartes laissent alors un grand
    vide en bas d'une colonne dès qu'une carte haute n'y rentre plus. On applique
    donc un First-Fit-Decreasing : les cartes, DE LA PLUS HAUTE À LA PLUS BASSE,
    sont affectées à la PREMIÈRE colonne (ordre de lecture) où elles tiennent —
    les petites viennent ainsi combler les trous laissés par les grandes.

    Rendu tel quel au placement glouton de `render_copy`, cet ordre reproduit
    EXACTEMENT l'affectation : un FFD ne laisse jamais dans une colonne un trou
    qu'une carte d'une colonne ultérieure aurait pu combler (sinon le premier
    ajustement l'y aurait mise), donc poser les cartes séquentiellement retombe
    sur les mêmes colonnes. `pages_needed` reste ainsi la mesure fidèle du rendu.
    Les hauteurs de colonne diffèrent (la 1re page porte l'en-tête élève, cf.
    `_top_of_page`), d'où une capacité par page."""
    order = sorted(range(len(heights)), key=lambda i: heights[i], reverse=True)
    cols: list[list[int]] = []      # index d'origine, par colonne (ordre de lecture)
    used: list[float] = []          # hauteur déjà occupée dans chaque colonne
    for i in order:
        h = heights[i]
        for b in range(len(cols)):
            cap = _top_of_page(b // 2) - _BOTTOM_LIMIT   # 2 colonnes par page
            if used[b] + h <= cap:
                cols[b].append(i)
                used[b] += h
                break
        else:
            cols.append([i])        # aucune colonne existante : on en ouvre une
            used.append(h)
    return [i for col in cols for i in col]


def _exercise_layout(item: dict, font_size: int,
                     math_fs: int) -> tuple[dict, float, float, dict]:
    """(layout de l'énoncé, corps de la zone de réponse, hauteur de zone, bande
    corrigé) d'un exercice — UNE définition, appelée à l'identique par la mesure
    (estimate_item_height) et par le dessin (render_copy). Deux constructions
    parallèles dériveraient, et c'est cet écart que test_page_fill traque.

    L'énoncé est mis en page au corps du GABARIT : c'est _rich_layout qui
    agrandit, ligne par ligne, les seules phrases portant une case à remplir
    (blank_fs). La zone de réponse a son propre corps (_zone_font_size). La
    bande corrigé est dimensionnée sur le texte du corrigé (banque) pour que
    l'overlay l'imprime en entier — d'où les valeurs distinctes."""
    rtype = item["response_type"]
    badge_w, _bh, _bfs = _badge_metrics(font_size)
    sub_color = _exercise_badge_color(item.get("level5", 3), item.get("is_probleme", False))
    layout = _statement_layout(item["statement"], COL_W - 2 * CARD_PAD, font_size,
                               math_fs, item.get("figure"),
                               first_indent=badge_w + BADGE_GAP,
                               first_min_asc=_badge_min_asc(font_size),
                               blank_fs=font_size + BLANK_FONT_BOOST,
                               sub_badge_color=sub_color,
                               fraction_blank_indices=_inline_fraction_indices(
                                   rtype, item.get("expected")))
    zone_fs = _zone_font_size(rtype, font_size)
    zone_h = _zone_height(rtype, item.get("choices", []), COL_W, zone_fs,
                          item.get("grading"), item.get("inline", False),
                          sub_color, item.get("expected"))
    strip = _correction_strip_layout(item.get("correction", ""), COL_W, font_size,
                                     item_guides_mode(item))
    return layout, zone_fs, zone_h, strip


# ------------------------------------------------------------- exercice composite
# Un composite = un CONTEXTE commun + N sous-questions, chacune de SON format,
# dans UNE seule carte unifiée. La distribution (services.generation) a créé une
# CopyItem PAR PARTIE (chacune un type feuille normal) : _draw_composite_card
# renvoie donc une zone PAR PARTIE, reliée à son item_id, et la correction traite
# chaque partie comme un exercice autonome (pipeline inchangée).
_COMPOSITE_PART_GAP = 2.4 * mm     # blanc avant chaque sous-question
_COMPOSITE_FRAG_GAP = 1.0 * mm     # blanc entre une sous-question et sa zone


def _composite_parts(item: dict) -> list[dict]:
    """Parties d'un composite : {response_type, statement, choices, grading,
    item_id}. Contrats lus dans item['grading']['parts'] (posés par la
    distribution), item_ids dans item['part_item_ids']."""
    parts = (item.get("grading") or {}).get("parts") or []
    ids = item.get("part_item_ids") or []
    out = []
    for k, p in enumerate(parts):
        pg = p.get("grading") or {}
        out.append({"response_type": p.get("response_type", "short_text"),
                    "statement": p.get("statement", ""),
                    "choices": pg.get("choices") or [], "grading": pg,
                    "expected": p.get("expected") or {},
                    "item_id": ids[k] if k < len(ids) else None})
    return out


def _composite_layout(item: dict, font_size: int, math_fs: int) -> dict:
    badge_w, _bh, _bfs = _badge_metrics(font_size)
    sub_color = _exercise_badge_color(item.get("level5", 3), item.get("is_probleme", False))
    stmt = _statement_layout(item.get("statement", ""), COL_W - 2 * CARD_PAD, font_size,
                             math_fs, item.get("figure"),
                             first_indent=badge_w + BADGE_GAP,
                             first_min_asc=_badge_min_asc(font_size),
                             blank_fs=font_size + BLANK_FONT_BOOST, sub_badge_color=sub_color)
    laid, body_h = [], stmt["height"]
    for k, p in enumerate(_composite_parts(item)):
        prt = p["response_type"]
        frag = _rich_layout(f"{chr(97 + k)}. " + statement_mod.normalize(p["statement"]),
                            COL_W - 2 * CARD_PAD, font_size, sub_badge_color=sub_color)
        zone_fs = _zone_font_size(prt, font_size)
        zone_h = _zone_height(prt, p["choices"], COL_W, zone_fs, p["grading"],
                              False, sub_color, p["expected"])
        laid.append({**p, "frag": frag, "zone_fs": zone_fs, "zone_h": zone_h})
        body_h += _COMPOSITE_PART_GAP + frag["height"] + _COMPOSITE_FRAG_GAP + zone_h
    strip = _correction_strip_layout(item.get("correction", ""), COL_W, font_size,
                                     item_guides_mode(item))
    return {"stmt": stmt, "parts": laid, "body_h": body_h, "strip": strip,
            "badge_color": sub_color}


def _composite_card_h(cl: dict) -> float:
    return cl["body_h"] + cl["strip"]["height"] + STRIP_GAP + 3 * CARD_PAD


def _draw_composite_card(c: canvas.Canvas, x: float, y_top: float, w: float, seq: int,
                         cl: dict, item: dict, tpl: dict, font_size: float) -> tuple[float, list]:
    """Dessine la carte unifiée d'un exercice composite. Retourne (hauteur, zones)
    — une zone PAR PARTIE : {item_id, response_type, zone_geo, meta}."""
    border = HexColor(tpl.get("border", "#C7CDD4"))
    radius = max(0.0, float(tpl.get("radius", 2.2))) * mm
    strip_h = cl["strip"]["height"]
    card_h = _composite_card_h(cl)
    y = y_top - card_h
    card_bottom = y + strip_h + STRIP_GAP
    card_h_body = card_h - strip_h - STRIP_GAP
    if tpl.get("shadow", True):
        c.setFillColor(CARD_SHADOW)
        c.roundRect(x + 1.1, card_bottom - 1.3, w, card_h_body, radius, stroke=0, fill=1)
    c.setFillColor(white)
    c.setStrokeColor(border)
    c.setLineWidth(0.9)
    c.roundRect(x, card_bottom, w, card_h_body, radius, stroke=1, fill=1)

    stmt = cl["stmt"]
    badge_color = cl["badge_color"]
    ty = card_bottom + card_h_body - CARD_PAD
    first = stmt["intro"]["lines"][0] if stmt["intro"]["lines"] else None
    _draw_badge(c, x + CARD_PAD, ty - (first["asc"] if first else font_size * 0.78),
                font_size, str(seq), badge_color)
    line_y = _draw_rich(c, x + CARD_PAD, ty, stmt["intro"])
    if stmt.get("figure_inline") and stmt["figure"]:
        fimg, fw, fh = stmt["figure"]
        c.drawImage(fimg, x + (w - fw) / 2, line_y - _FIG_MARKER_GAP - fh, width=fw, height=fh,
                    mask="auto", preserveAspectRatio=True)
        line_y -= 2 * _FIG_MARKER_GAP + fh
        if stmt.get("intro_after"):
            line_y = _draw_rich(c, x + CARD_PAD, line_y, stmt["intro_after"])
    elif stmt["figure"]:
        fimg, fw, fh = stmt["figure"]
        c.drawImage(fimg, x + (w - fw) / 2, line_y - fh - 0.5 * mm, width=fw, height=fh,
                    mask="auto", preserveAspectRatio=True)
        line_y -= fh + 1 * mm
    c.setFillColor(black)
    if item.get("calc") in ("necessaire", "interdite"):
        _draw_calc_icon(c, x + w - 1.4 * mm, card_bottom + card_h_body - 1.4 * mm, 3.6 * mm,
                        forbidden=(item.get("calc") == "interdite"))

    part_zones = []
    for p in cl["parts"]:
        line_y -= _COMPOSITE_PART_GAP
        line_y = _draw_rich(c, x + CARD_PAD, line_y, p["frag"])
        line_y -= _COMPOSITE_FRAG_GAP
        zone_y = line_y - p["zone_h"]
        meta = _draw_answer_zone(c, x, zone_y, w, p["zone_h"], p["response_type"],
                                 p["choices"], p["zone_fs"], p["grading"], badge_color)
        part_zones.append({"item_id": p["item_id"], "response_type": p["response_type"],
                           "zone_geo": {"x_pt": x, "y_pt": zone_y, "w_pt": w, "h_pt": p["zone_h"]},
                           "meta": meta})
        line_y = zone_y
    # bande corrigé du composite : les parties n'en portent pas la géométrie
    # (l'overlay imprime la note au-dessus de chaque zone, comportement
    # d'origine), mais un guide À IMPRIMER doit l'être ici aussi, une seule fois
    # pour la carte unifiée.
    if cl["strip"].get("guides") == GUIDES_PRINT:
        _strip_meta(c, x, y, w, cl["strip"])
    c.setFillColor(black)
    return card_h, part_zones


def estimate_item_height(item: dict, font_size: int, math_fs: int,
                         ex_tpl: dict, lesson_tpl: dict) -> float:
    """Hauteur (pt) qu'occuperait `item` (placement inclus), sans dessiner —
    mesure pure réutilisée par le remplissage automatique de page."""
    if item.get("kind") == "lesson":
        fs = max(6, int(lesson_tpl.get("font_size", 8)))
        blocks = item.get("blocks") or {
            "essentiel": item.get("content", ""),
            "exemple": {"enonce": item.get("example", ""), "etapes": [],
                        "resultat": ""} if item.get("example") else {},
        }
        lay = _lesson_layout(blocks, COL_W, fs)
        return _lesson_card_h(lay, lesson_tpl) + GAP
    if item.get("response_type") == "composite":
        return _composite_card_h(_composite_layout(item, font_size, math_fs)) + GAP
    layout, _fs, zone_h, strip = _exercise_layout(item, font_size, math_fs)
    return _exercise_card_h(layout, zone_h, strip["height"], ex_tpl) + GAP


def render_copy(pdf_canvas: canvas.Canvas, *, student_name: str, class_name: str,
                title: str, assessment_type: str, items: list[dict],
                pages_meta: list[dict], font_size: int = 9,
                tpl: dict | None = None,
                placement: list[tuple[int, int]] | None = None,
                min_pages: int = 0, dyslexic: bool = False) -> list[dict]:
    """Point d'entrée isolant la police par rendu, y compris si plusieurs
    requêtes PDF sont traitées simultanément."""
    token = _USE_DYSLEXIC.set(dyslexic)
    try:
        return _render_copy(
            pdf_canvas, student_name=student_name, class_name=class_name,
            title=title, assessment_type=assessment_type, items=items,
            pages_meta=pages_meta, font_size=font_size, tpl=tpl,
            placement=placement, min_pages=min_pages)
    finally:
        _USE_DYSLEXIC.reset(token)


def _render_copy(pdf_canvas: canvas.Canvas, *, student_name: str, class_name: str,
                 title: str, assessment_type: str, items: list[dict],
                 pages_meta: list[dict], font_size: int = 9,
                 tpl: dict | None = None,
                 placement: list[tuple[int, int]] | None = None,
                 min_pages: int = 0) -> list[dict]:
    """Dessine une copie complète. `items` : dicts avec kind=exercise
    (item_id, statement, response_type, choices, level5) ou kind=lesson
    (title, content, example). `tpl` : templates éditables (runtime_settings).
    Retourne les zones pour le manifeste.

    `placement` (assistant « Créer mon sujet ») : une paire (page, colonne) PAR
    item, dans l'ordre de `items` — qui doit alors être trié par (page, colonne,
    rang), le canvas reportlab étant strictement séquentiel (on ne revient
    jamais sur une page déjà close). Sans lui, le placement reste glouton :
    colonne gauche puis droite puis page suivante, comme `pages_needed` le
    simule. Une carte qui ne tient pas dans la colonne demandée bascule dans la
    suivante (le débordement est signalé par l'appelant via les page_index
    retournés), jamais dessinée à cheval.

    `min_pages` : nombre de pages à émettre même si les dernières sont vides —
    une page laissée volontairement blanche par le professeur reste une page du
    sujet (elle porte son QR signé et sera scannée comme les autres)."""
    tpl = tpl or DEFAULT_TEMPLATES
    ex_tpl, lesson_tpl = tpl["exercise"], tpl["lesson"]
    font_size = float(ex_tpl.get("font_size", font_size))
    math_fs = int(ex_tpl.get("math_size", 12))
    zones = []
    col_w = COL_W
    today = date.today().strftime("%d/%m/%Y")

    page_idx = 0
    col = 0
    y_cursor = _top_of_page(0)
    bottom_limit = _BOTTOM_LIMIT
    gap = GAP

    def top_of_page() -> float:
        return _top_of_page(page_idx)

    def new_page():
        nonlocal page_idx, col, y_cursor
        pdf_canvas.showPage()
        page_idx += 1
        if page_idx >= len(pages_meta):
            pages_meta.append({"page_id": f"overflow-{page_idx}", "payload": "MP1|overflow|0"})
        _draw_markers(pdf_canvas, pages_meta[page_idx]["payload"])
        col = 0
        y_cursor = top_of_page()

    def place(height: float):
        nonlocal col, y_cursor
        if y_cursor - height < bottom_limit:
            if col == 0:
                col = 1
                y_cursor = top_of_page()
                if y_cursor - height < bottom_limit:
                    new_page()
            else:
                new_page()

    def goto(slot: tuple[int, int], height: float):
        """Placement imposé : on avance jusqu'à (page, colonne) demandée, puis
        on retombe sur `place` — qui gère le seul cas restant, une colonne trop
        pleine pour la carte."""
        nonlocal col, y_cursor
        want_page, want_col = slot
        while page_idx < want_page:
            new_page()
        if want_col > col:
            col = want_col
            y_cursor = top_of_page()
        place(height)

    _draw_markers(pdf_canvas, pages_meta[0]["payload"])
    _draw_header(pdf_canvas, student_name, class_name, title, assessment_type, today,
                 tpl["header"])

    seq = 0
    for idx, item in enumerate(items):
        slot = placement[idx] if placement and idx < len(placement) else None
        x = MARGIN + col * (col_w + COL_GAP)
        if item.get("kind") == "lesson":
            fs = max(6, float(lesson_tpl.get("font_size", 8)))
            blocks = item.get("blocks") or {
                # compatibilité rappels v2 (deux paragraphes plats)
                "essentiel": item.get("content", ""),
                "exemple": {"enonce": item.get("example", ""), "etapes": [],
                            "resultat": ""} if item.get("example") else {},
            }
            lay = _lesson_layout(blocks, col_w, fs)
            h = _lesson_card_h(lay, lesson_tpl) + gap
            goto(slot, h) if slot else place(h)
            x = MARGIN + col * (col_w + COL_GAP)
            used = _draw_lesson_card(pdf_canvas, x, y_cursor, col_w,
                                     item.get("title", "Rappel"), lay, lesson_tpl)
            y_cursor -= used + gap
            continue

        seq += 1
        if item["response_type"] == "composite":
            cl = _composite_layout(item, font_size, math_fs)
            h = _composite_card_h(cl) + gap
            goto(slot, h) if slot else place(h)
            x = MARGIN + col * (col_w + COL_GAP)
            card_h, part_zones = _draw_composite_card(
                pdf_canvas, x, y_cursor, col_w, seq, cl, item, ex_tpl, font_size)
            for pz in part_zones:
                zones.append({
                    "item_id": pz["item_id"], "page_index": page_idx,
                    "page_id": pages_meta[page_idx]["page_id"],
                    "type": pz["response_type"], **pz["zone_geo"], "meta": pz["meta"],
                })
            y_cursor -= card_h + gap
            continue

        choices = item.get("choices", [])
        layout, zone_fs, zone_h, strip = _exercise_layout(item, font_size, math_fs)
        card_h = _exercise_card_h(layout, zone_h, strip["height"], ex_tpl)
        goto(slot, card_h + gap) if slot else place(card_h + gap)
        x = MARGIN + col * (col_w + COL_GAP)

        _, zone_geo, meta = _draw_exercise_card(
            pdf_canvas, x, y_cursor, col_w, seq, layout, zone_h, strip,
            item.get("level5", 3), item["response_type"], choices, ex_tpl,
            font_size, zone_fs, item.get("grading"), item.get("calc", "autorisee"),
            probleme=item.get("is_probleme", False))
        zones.append({
            "item_id": item["item_id"], "page_index": page_idx,
            "page_id": pages_meta[page_idx]["page_id"],
            "type": item["response_type"], **zone_geo, "meta": meta,
        })
        y_cursor -= card_h + gap

    # pages volontairement laissées vides à la fin (placement manuel) : elles
    # doivent exister dans le PDF, avec leurs fiduciels et leur QR signé.
    while page_idx < min_pages - 1:
        new_page()
    pdf_canvas.showPage()
    return zones


# ------------------------------------------------------------------ overlay

def _mark(c: canvas.Canvas, x: float, y: float, ok: bool, size: float = 2.4 * mm,
          credit: float | None = None, credit_label: str | None = None):
    """Marque vectorielle d'un champ (fiable quel que soit le lecteur PDF) :
    coche si juste, croix si faux, et **coche suivie de la part obtenue** pour un
    crédit PARTIEL (arrondi correct cf. grading.numeric_credit, QCM multiple à
    moitié coché cf. grading.qcm_credit) — l'élève doit voir d'un coup d'œil ce
    qu'il a gagné, jamais une croix là où il a des points. `credit_label` porte
    la fraction lisible (« ½ », « 2/3 ») ; à défaut, le demi."""
    half = credit is not None and 0.0 < credit < 1.0
    c.saveState()
    c.setLineWidth(1.1)
    if ok or half:
        c.line(x, y + size * 0.35, x + size * 0.35, y)
        c.line(x + size * 0.35, y, x + size, y + size * 0.9)
    else:
        c.line(x, y, x + size * 0.8, y + size * 0.8)
        c.line(x, y + size * 0.8, x + size * 0.8, y)
    if half:
        # part obtenue collée à la coche : sans ambiguïté possible
        c.setLineWidth(0.7)
        c.setFont("Helvetica-Bold", size * 0.95 / mm * 2.0)
        c.drawString(x + size * 1.05, y + size * 0.05, credit_label or "½")
    c.restoreState()


def _draw_corr_checkbox(c: canvas.Canvas, b: dict, col):
    """Case « correction » QCM imprimée par l'overlay à gauche de la case élève :
    REMPLIE (saturée) si le choix est une bonne réponse — elle affiche la réponse
    ET montre à l'élève comment saturer la case pour une meilleure lecture — sinon
    simple contour vide. Elle est imprimée pour CHAQUE choix afin que la colonne
    constitue toujours le corrigé complet, même lorsque l'élève a tout juste."""
    x, y, w, h = b["x_pt"], b["y_pt"], b["w_pt"], b["h_pt"]
    c.saveState()
    c.setStrokeColor(col)
    c.setFillColor(col)
    c.setLineWidth(0.9)
    c.rect(x, y, w, h, stroke=1, fill=1 if b.get("should_check") else 0)
    c.restoreState()


def _draw_zone_marks(c: canvas.Canvas, z: dict, col):
    """Marques par CHAMP de réponse (coche/croix, cases correction, traits de
    liaison) selon `z["marks"]` (posé par services.pipeline._zone_marks). Dessin
    partagé par l'overlay (fond blanc) et l'aperçu copie+overlay (fond scanné)."""
    marks = z.get("marks")
    if not marks:
        return
    c.setStrokeColor(col)
    c.setFillColor(col)
    kind = marks.get("kind")
    m = 0.6 * mm
    if kind == "single_tr":       # short_text : coche/croix en HAUT à droite
        _mark(c, z["x_pt"] + z["w_pt"] - m - 2.4 * mm,
              z["y_pt"] + z["h_pt"] - m - 2.4 * mm, marks["ok"],
              credit=marks.get("credit"))
    elif kind == "single_br":     # multiline_text : en BAS à droite de la zone
        _mark(c, z["x_pt"] + z["w_pt"] - m - 2.4 * mm, z["y_pt"] + m, marks["ok"],
              credit=marks.get("credit"))
    elif kind == "cells":         # table_fill/multi_blank : chaque cellule marquée
        for cell in marks["cells"]:
            _mark(c, cell["x_pt"] + cell["w_pt"] - 0.4 * mm - CELL_MARK_SIZE,
                  cell["y_pt"] + cell["h_pt"] - 0.4 * mm - CELL_MARK_SIZE,
                  cell["ok"], size=CELL_MARK_SIZE, credit=cell.get("credit"))
    elif kind == "qcm":
        # AUCUNE marque par-dessus les cases de l'élève : sa copie reste intacte
        # (coches et cases vides visibles). À gauche, la colonne « correction »
        # AFFICHE LA RÉPONSE : case REMPLIE (saturée) pour une bonne réponse,
        # contour vide sinon. Le verdict
        # juste/faux n'apparaît qu'UNE fois par carte (récap en bas à droite).
        for b in marks.get("boxes", []):
            cb = b.get("correction_box")
            if not cb:
                continue
            is_correct = b.get("state") in ("ok", "missed")
            _draw_corr_checkbox(c, {**cb, "should_check": is_correct}, col)
        # récap de la CARTE en bas à droite : coche si zéro erreur, croix si tout
        # est faux, coche + part obtenue (« 2/3 ») si le QCM multiple est
        # partiellement juste.
        _mark(c, z["x_pt"] + z["w_pt"] - QCM_MARK_SIZE - m, z["y_pt"] + m,
              not marks.get("any_error"), size=QCM_MARK_SIZE,
              credit=marks.get("credit"), credit_label=marks.get("credit_label"))
    elif kind == "grid":
        # grille cochée : sur CHAQUE case, coche (cochée à raison) / croix (cochée
        # à tort) ; une bonne réponse OUBLIÉE est montrée en case REMPLIE (comme la
        # correction QCM). Récap juste/faux une seule fois, en bas à droite.
        for b in marks.get("boxes", []):
            state = b.get("state")
            box = {"x_pt": b["x_pt"], "y_pt": b["y_pt"], "w_pt": b["w_pt"], "h_pt": b["h_pt"]}
            if state in ("ok", "wrong"):
                _mark(c, box["x_pt"] + (box["w_pt"] - CELL_MARK_SIZE) / 2,
                      box["y_pt"] + (box["h_pt"] - CELL_MARK_SIZE) / 2,
                      state == "ok", size=CELL_MARK_SIZE)
            elif state == "missed" and marks.get("any_error"):
                _draw_corr_checkbox(c, {**box, "should_check": True}, col)
        _mark(c, z["x_pt"] + z["w_pt"] - QCM_MARK_SIZE - m, z["y_pt"] + m,
              not marks.get("any_error"), size=QCM_MARK_SIZE,
              credit=marks.get("credit"), credit_label=marks.get("credit_label"))
        c.setFillColor(col)
    elif kind == "matching":
        c.saveState()
        c.setStrokeColor(col)
        c.setLineWidth(1.0)
        c.setDash(2.2, 1.8)
        for ln in marks.get("links", []):
            c.line(ln["x1"], ln["y1"], ln["x2"], ln["y2"])
        c.restoreState()
    c.setFillColor(col)


def _draw_correction_strip(c: canvas.Canvas, z: dict, col):
    """Bande de correction sous une carte : la note de barème à DROITE, en gros
    et gras ; le corrigé (banque) à gauche — mis en page comme un énoncé (riche :
    formules $...$, sauts de ligne), et imprimé SEULEMENT si l'élève s'est
    trompé (z["text"] vide sinon). La hauteur a été anticipée à la génération
    (_correction_strip_layout) pour que le corrigé ne soit jamais coupé."""
    strip = z.get("strip")
    score_txt = (f"{scoring.format_points(z['score'])}/"
                 f"{scoring.format_points(z['max_score'])}")
    if not strip:
        # copie antérieure à la bande dimensionnée : repli minimal (note seule)
        c.setFillColor(col)
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(z["x_pt"] + z["w_pt"], z["y_pt"] + z["h_pt"] + 1.5 * mm,
                          score_txt)
        return
    sx, sy, sw, sh = strip["x_pt"], strip["y_pt"], strip["w_pt"], strip["h_pt"]
    fs = float(strip.get("fs", 7.5))
    c.setFillColor(col)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(sx + sw, sy + sh / 2 - 11 * 0.34, score_txt)
    # GUIDES_NONE : la bande n'a que la hauteur de la note (le texte n'a jamais
    # été composé, l'imprimer déborderait sur la carte suivante).
    # GUIDES_PRINT : le guide est DÉJÀ imprimé sur le sujet — le repasser en
    # rouge par-dessus ne ferait qu'un pâté.
    if z.get("text") and strip.get("guides", GUIDES_OVERLAY) == GUIDES_OVERLAY:
        text_w = max(10 * mm, sw - STRIP_NOTE_W)
        lay = _rich_layout(statement_mod.normalize(z["text"]), text_w, fs)
        _draw_rich(c, sx, sy + sh, lay, color=col)
    c.setFillColor(col)


PROGRESS_GREEN = HexColor("#2E7D32")
PROGRESS_TRACK = HexColor("#DCE7DC")


def _draw_appreciation_content(c: canvas.Canvas, geo: dict, progress: list[dict],
                               synthesis: str):
    """Barres de progrès (vert uniquement, jamais de rouge) + synthèse Haiku,
    dessinées dans le rect Appréciation de header_geometry(). Les progrès sont
    répartis horizontalement : trois compétences ne rallongent donc jamais la
    bande réglée sur les 24 mm du QR."""
    ax, ay, aw, ah = geo["appreciation"]["x"], geo["appreciation"]["y"], \
        geo["appreciation"]["w"], geo["appreciation"]["h"]
    inner_x = ax + 3.5 * mm
    inner_w = aw - 7 * mm
    visible = progress[:3]
    if visible:
        col_gap = 2 * mm
        col_w = (inner_w - col_gap * (len(visible) - 1)) / len(visible)
        label_y = ay + ah - HEADER_LABEL_DY - 4.0 * mm
        bar_y = label_y - 3.0 * mm
        bar_h = 1.7 * mm
        for i, p in enumerate(visible):
            x = inner_x + i * (col_w + col_gap)
            pct = round(p["pct_acquired"] * 100)
            name = _pdf_safe(p["competency_name"])
            suffix = f"  {pct}%"
            # Coupe déterministe, mesurée dans la vraie police PDF : jamais de
            # collision entre deux colonnes, quel que soit le libellé H2/H3.
            while name and stringWidth(name + suffix, "Helvetica", 5.3) > col_w:
                name = name[:-1]
            label = (name.rstrip(" ·-") + ("…" if name != _pdf_safe(p["competency_name"]) else "")
                     + suffix)
            c.setFont("Helvetica", 5.3)
            c.setFillColor(black)
            c.drawString(x, label_y, label)
            c.setFillColor(PROGRESS_TRACK)
            c.roundRect(x, bar_y - bar_h, col_w, bar_h, bar_h / 2, stroke=0, fill=1)
            c.setFillColor(PROGRESS_GREEN)
            fill_w = max(bar_h, col_w * min(1.0, p["pct_acquired"]))
            c.roundRect(x, bar_y - bar_h, fill_w, bar_h, bar_h / 2, stroke=0, fill=1)
    if synthesis:
        c.setFillColor(HexColor("#37474F"))
        c.setFont("Helvetica-Oblique", 5.8)
        y = ay + (5.8 * mm if visible else 13.0 * mm)
        max_lines = 2 if visible else 3
        for line in _wrap(synthesis, inner_w, 5.8)[:max_lines]:
            c.drawString(inner_x, y, line)
            y -= 2.7 * mm
    c.setFillColor(black)


def _draw_correction_marks(c: canvas.Canvas, page: dict, col):
    """Dessine les marques de correction d'une page (nom, note, appréciation,
    scores par exercice) dans la couleur d'encre `col`. Partagé par l'overlay
    (fond blanc) et l'aperçu « copie + overlay » (fond = scan recalé)."""
    geo = header_geometry(page.get("assessment_type", "control"))
    c.setFillColor(col)
    c.setStrokeColor(col)
    # nom de l'élève sous le QR : l'élève vérifie que la correction est la sienne.
    # Une feuille restée dans le flux mais inexploitable porte une mention courte
    # au MÊME endroit : jamais de suppression de page, donc jamais de décalage.
    if page.get("unidentified"):
        c.setFont("Helvetica-Bold", 7)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - QR_MAIN - 4 * mm,
                          "Non identifié")
        return
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - QR_MAIN - 4 * mm,
                      f"Correction — {page.get('student', '')}")
    if page.get("note") is not None and geo["note"]["visible"]:
        nx, ny, nw, nh = geo["note"]["x"], geo["note"]["y"], geo["note"]["w"], geo["note"]["h"]
        # centrée dans le cadre imprimé, sous son libellé « NOTE »
        band_bottom, band_h = ny + HEADER_PAD_V, nh - 2 * HEADER_PAD_V
        c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(nx + nw / 2, band_bottom + (band_h - 6 * mm) / 2 - 5,
                            str(page["note"]))
    if page.get("progress") or page.get("synthesis"):
        _draw_appreciation_content(c, geo, page.get("progress") or [],
                                   page.get("synthesis") or "")
    elif page.get("comment"):
        ax, ay, aw, ah = geo["appreciation"]["x"], geo["appreciation"]["y"], \
            geo["appreciation"]["w"], geo["appreciation"]["h"]
        c.setFont("Helvetica", 8)
        for i, line in enumerate(_wrap(page["comment"], aw - 7 * mm, 8)[:2]):
            c.drawString(ax + 3.5 * mm, ay + ah - (i + 1) * 5 * mm - 3 * mm, line)
    # points de BARÈME (cf. services.pipeline.build_overlays), en demis :
    # « 1,5/2 » à la française, jamais « 1.5/2.0 » — lu par un élève de 5e sur
    # sa copie. Marques par champ d'abord (coches/croix, cases correction,
    # traits de liaison), puis la bande corrigé + note sous la carte.
    for z in page.get("page_zones", []):
        _draw_zone_marks(c, z, col)
        _draw_correction_strip(c, z, col)
        c.setFillColor(col)
        c.setStrokeColor(col)


def render_overlay(path: str, *, copies_annotations: list[dict],
                   color: str | None = None):
    """Overlay de correction (§5.6) : pages blanches, annotations seules,
    calées sur les zones de l'en-tête (case Note, zone Appréciation) et les
    bandes de correction sous chaque exercice — même géométrie que le sujet
    (header_geometry) pour un recalage physique via les fiduciels."""
    col = HexColor(color or settings.correction_color)
    c = canvas.Canvas(path, pagesize=A4)
    for page in copies_annotations:
        _draw_correction_marks(c, page, col)
        c.showPage()
    c.save()


def render_copy_review(path: str, *, review_pages: list[dict],
                       color: str | None = None):
    """Aperçu « copie + overlay » : chaque page porte en FOND l'image scannée
    recalée de l'élève (canonique A4, mêmes coordonnées que l'overlay) puis les
    marques de correction par-dessus — pour vérifier d'un coup d'œil ce qui a
    été identifié et corrigé. Repli page blanche si pas de scan (lot simulé)."""
    col = HexColor(color or settings.correction_color)
    c = canvas.Canvas(path, pagesize=A4)
    for page in review_pages:
        bg = page.get("background")
        if bg and Path(bg).exists():
            try:
                c.drawImage(ImageReader(bg), 0, 0, width=PAGE_W, height=PAGE_H)
            except Exception:
                pass
        _draw_correction_marks(c, page, col)
        c.showPage()
    c.save()


def write_manifest(path: str, manifest: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

"""Génération documentaire (§5) : copies A4, QR + fiduciels signés, zones
dropout, overlay.

Design des sujets :
- en-tête en deux colonnes : à gauche la case Note (agrandie, sans mention
  d'échelle) et la bande Appréciation (agrandie) en POINTILLÉS, remplies par
  l'overlay de correction ; à droite l'identité de l'élève (nom/prénom/classe,
  gros) puis le méta du sujet (date, type, titre du lot, petit) — un filet
  sépare clairement l'en-tête des exercices ;
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
from reportlab.pdfbase.pdfmetrics import stringWidth

from ..config import settings
from . import scoring
from . import statement as statement_mod
from .runtime_settings import DEFAULT_TEMPLATES

PAGE_W, PAGE_H = A4  # 595.27 x 841.89 pt
MARGIN = 9 * mm
HEADER_H = 36 * mm
QR_MAIN = 24 * mm
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
STRIP_H = 6.5 * mm      # bande de correction (overlay)
STRIP_GAP = 0.4 * mm    # espace blanc visible entre la carte et sa bande de correction (rapproché)
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
NOTE_W = 23 * mm
META_W = 62 * mm
HEADER_GAP = 3 * mm       # gouttière entre deux zones voisines
HEADER_PAD_V = 1.5 * mm   # inset vertical commun : cadres et bloc méta alignés
HEADER_LABEL_DY = 4.5 * mm  # ligne de base des libellés NOTE/APPRÉCIATION sous le haut de bande
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
    c.setFont("Helvetica-Bold", bfs)
    c.drawCentredString(x + bw / 2, by + (bh - bfs * 0.72) / 2, str(label))
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


def _meta_rows(student_name: str, class_name: str, title: str, label: str,
               the_date: str, width: float, tpl: dict,
               accent: Color) -> list[tuple]:
    """Lignes (texte, police, corps, couleur) du bloc identité/méta, ajustées
    pour tenir dans `width`. L'identité passe sur une seule ligne
    « Nom / Classe » tant que la réduire reste raisonnable ; au-delà (noms
    longs) elle se scinde en deux lignes plutôt que de déborder sur la zone
    Appréciation."""
    name_fs = float(tpl.get("name_size", 14))
    title_fs = float(tpl.get("title_size", 8))
    ident = _pdf_safe(f"{student_name}  /  {class_name}")
    ident_fs = _fit_size(ident, "Helvetica-Bold", width, name_fs, 9.0)
    if stringWidth(ident, "Helvetica-Bold", ident_fs) <= width:
        rows = [(ident, "Helvetica-Bold", ident_fs, black)]
    else:
        name = _pdf_safe(student_name)
        rows = [
            (name, "Helvetica-Bold",
             _fit_size(name, "Helvetica-Bold", width, name_fs, 7.0), black),
            (_pdf_safe(class_name), "Helvetica-Bold",
             _fit_size(_pdf_safe(class_name), "Helvetica-Bold", width,
                       title_fs + 1, 6.5), accent),
        ]
    if title:
        t = _pdf_safe(title)
        rows.append((t, "Helvetica-Bold",
                     _fit_size(t, "Helvetica-Bold", width, title_fs, 6.0), accent))
    if tpl.get("show_date", True):
        meta_fs = max(6.0, title_fs - 1)
        rows.append((_pdf_safe(f"{label}  ·  {the_date}"), "Helvetica", meta_fs,
                     HexColor("#6A737C")))
    return rows


def _draw_header(c: canvas.Canvas, student_name: str, class_name: str, title: str,
                 assessment_type: str, the_date: str, tpl: dict | None = None):
    """En-tête en 4 zones, gauche -> droite : Note (contrôle seul) |
    Appréciation | Identité+méta | QR. Les trois premières partagent la même
    bande verticale (HEADER_PAD_V) : cadres alignés haut et bas, bloc méta
    centré dessus, gouttière entre chaque zone."""
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
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(nx + nw / 2, band_bottom + band_h - HEADER_LABEL_DY, "NOTE")
        c.setFillColor(black)

    # --- zone Appréciation (absorbe la largeur de la Note en entraînement) ---
    ax, aw = geo["appreciation"]["x"], geo["appreciation"]["w"]
    _dotted(c)
    c.roundRect(ax, band_bottom, aw, band_h, 2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 6.5)
    c.drawString(ax + 2.5 * mm, band_bottom + band_h - HEADER_LABEL_DY,
                 "APPRÉCIATION — remplie à la correction")
    c.setFillColor(black)

    # --- zone Identité + méta : justifiée à droite, bloc centré sur la bande ---
    meta_right = geo["meta"]["x"] + geo["meta"]["w"]
    rows = _meta_rows(student_name, class_name, title, label, the_date,
                      geo["meta"]["w"], tpl, accent)
    row_gap = 1.6 * mm
    block_h = sum(fs * 0.72 for _t, _f, fs, _c in rows) + row_gap * (len(rows) - 1)
    y = band_bottom + (band_h + block_h) / 2
    for text, font, fs, color in rows:
        y -= fs * 0.72
        c.setFont(font, fs)
        c.setFillColor(color)
        c.drawRightString(meta_right, y, text)
        y -= row_gap

    # filet séparateur en-tête / exercices (pas de séparateur vertical entre zones)
    c.setStrokeColor(accent)
    c.setLineWidth(1.1)
    c.line(MARGIN, header_bottom, PAGE_W - MARGIN, header_bottom)
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
BLANK_FONT_BOOST = 2.0


def _zone_font_size(response_type: str, font_size: float) -> float:
    """Corps de texte de la ZONE de réponse. Seul table_fill s'écarte du
    gabarit : ses libellés de ligne SONT les phrases à trous (« a. 7 × 8 = »),
    ils suivent donc le corps agrandi des cases — exactement comme les phrases
    à trous d'un énoncé, dont ils ne sont que la version en grille."""
    return font_size + BLANK_FONT_BOOST if response_type == "table_fill" else font_size


def _seg_w(seg: tuple, fs: float) -> float:
    if seg[0] == "word":
        return stringWidth(seg[1], "Helvetica", fs)
    if seg[0] == "blank":
        return seg[1]
    return seg[2]


def _seg_glue(seg: tuple) -> bool:
    return bool(seg[-1])


def _paragraph_segs(text: str, fs: float, math_fs: float) -> list[tuple]:
    """Segments d'UNE ligne logique d'énoncé (elle peut encore se replier sur
    plusieurs lignes de rendu). seg = ("word", texte, glue) |
    ("math", img, w, h, d, glue) | ("blank", w, asc, desc, glue) ; glue = collé
    au segment précédent SANS espace (ponctuation après une formule, etc.)."""
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
        elif BLANK_TOKEN in content:
            parts = content.split(BLANK_TOKEN)
            for pi, part in enumerate(parts):
                _emit_words(part)
                if pi < len(parts) - 1:
                    segs.append(("blank", BLANK_W, BLANK_H - fs * 0.24,
                                 fs * 0.24, False))
                    prev_no_space = False
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
        p_fs = blank_fs if (blank_fs and statement_mod.has_blank(para)) else fs
        p_math_fs = math_fs or p_fs
        badge_w = (_badge_metrics(p_fs)[0] + BADGE_GAP) if badge is not None else 0.0
        # retrait PENDANT sous une pastille : les lignes suivantes de la
        # sous-question s'alignent sur son texte, pas sous sa pastille
        head_indent = lead + badge_w
        cont_indent = head_indent if badge is not None else 0.0

        segs = _paragraph_segs(para, p_fs, p_math_fs)
        space_w = stringWidth(" ", "Helvetica", p_fs)

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
               font: str = "Helvetica", blanks: list | None = None) -> float:
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
    y = y_top
    for line in layout["lines"]:
        fs = line["fs"]
        space_w = stringWidth(" ", font, fs)
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
                c.setFont(font, fs)
                c.setFillColor(color)
                c.drawString(cx, y_base, seg[1])
                cx += stringWidth(seg[1], font, fs)
            elif seg[0] == "blank":
                _, w, asc, desc, _glue = seg
                c.setStrokeColor(DROPOUT)
                c.setLineWidth(0.9)
                c.roundRect(cx, y_base - desc, w, asc + desc, 0.8 * mm)
                if blanks is not None:
                    blanks.append({"x_pt": cx, "y_pt": y_base - desc,
                                  "w_pt": w, "h_pt": asc + desc})
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
                      sub_badge_color: Color | None = None) -> dict:
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
    statement = _legacy_to_tagged(statement_mod.normalize(statement))
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
    figure = _figure_image(figure_json, min(width, 62 * mm), 42 * mm)
    height = intro["height"]
    if display:
        height += display[2] + 2.5 * mm
    if figure:
        height += figure[2] + 2 * mm
    return {"intro": intro, "display": display, "figure": figure, "height": height}


def _qcm_layout(choices: list[str], width: float,
                font_size: int) -> tuple[list[dict], float, int]:
    """Disposition en colonnes (remplies colonne par colonne). Les labels sont
    mis en page en riche (formules rendues). Retourne (items, hauteur, ncols) ;
    item = {index, dx, dy, lay, lw, box} en relatif (origine haut-gauche).

    Deux passes : la 1re mesure la largeur NATURELLE des labels pour choisir le
    nombre de colonnes, la 2de les remet en page à la largeur réelle de leur
    colonne. Une passe unique à `width` ignorait la place prise par la case à
    cocher et son blanc — le label, dessiné après la case, débordait alors de
    la carte d'autant."""
    box = 2.0 * mm
    gap_x, gap_y, pad = 6.0 * mm, 1.6 * mm, 1.6 * mm
    n = len(choices)
    solo_w = max(10 * mm, width - box - pad)     # label sur une seule colonne
    nat = [max((ln["w"] for ln in _rich_layout(ch, solo_w, font_size)["lines"]),
               default=0.0) for ch in choices]
    item_w = box + pad + (max(nat) if nat else 0.0) + gap_x
    ncols = max(1, min(3, n, int(width // item_w) if item_w > 0 else 1))
    nrows = -(-n // ncols)  # ceil

    col_total = width / ncols
    lab_w = max(10 * mm, col_total - box - pad - (gap_x if ncols > 1 else 0.0))
    items, max_h = [], 6.0 * mm
    lays = []
    for choice in choices:
        lay = _rich_layout(choice, lab_w, font_size)
        lays.append(lay)
        max_h = max(max_h, lay["height"] + gap_y)
    for i, lay in enumerate(lays):
        col, row = divmod(i, nrows)
        lw = max((ln["w"] for ln in lay["lines"]), default=0.0)
        items.append({"index": i, "dx": col * col_total, "dy": row * max_h,
                      "lay": lay, "lw": lw, "box": box})
    return items, nrows * max_h, ncols


# Interligne des zones de rédaction (multiline_text) : les élèves écrivent plus
# gros que le corps imprimé — mesure/dessin doivent lire la MÊME constante.
MULTILINE_ROW_H = 9 * mm

_TABLE_HEAD_H = 6.0 * mm
_TABLE_CELL_PAD = 1.2 * mm
_TABLE_COL_W = BLANK_W + 2 * _TABLE_CELL_PAD     # colonne « confortable » : case pleine taille
_TABLE_MIN_COL_W = 10.0 * mm                     # plancher quand les colonnes sont nombreuses
_TABLE_ROW_MIN_H = BLANK_H + 2 * _TABLE_CELL_PAD
_TABLE_ROWLAB_MIN_W = 18.0 * mm
_MATCHING_PASTILLE = 2.2 * mm
_MATCHING_COL_GAP = 10.0 * mm
_MANUAL_DRAWING_H = 60.0 * mm


def _table_geometry(w: float, col_labels: list | None, row_labels: list | None,
                    cells: list[list[dict]], font_size: int,
                    sub_badge_color: Color | None = None) -> dict:
    """Géométrie complète d'un tableau à remplir — UNE définition, partagée par
    la mesure (_table_zone_height) et le dessin (_draw_table_zone).

    Le cas dominant est cols=1 : le générateur y met la sous-question entière
    dans row_labels[i] (« a. 4,8 + ... = 12,5 ») et la grille ne porte qu'une
    case. La largeur va donc en priorité au libellé, la colonne réponse étant
    dimensionnée sur la case (20 mm) et non sur la place restante — c'est
    l'inverse de l'ancienne règle (libellé figé à 15 mm), qui écrasait le
    libellé sous une case démesurée."""
    rows = len(cells)
    cols = max((len(r) for r in cells), default=0) or 1
    inner = w - 2 * CARD_PAD
    lab_fs = max(6, font_size - 1)

    rowlab_w = 0.0
    if row_labels:
        room = inner - cols * _TABLE_MIN_COL_W       # place cessible au libellé
        rowlab_w = max(0.0, min(room, max(_TABLE_ROWLAB_MIN_W,
                                          inner - cols * _TABLE_COL_W)))
    grid_w = inner - rowlab_w
    col_w = grid_w / cols

    # bandeau de tête dimensionné sur les libellés RÉELS : « Nombre manquant »
    # sur une colonne de 22 mm passe à la ligne, et une hauteur figée le faisait
    # retomber dans la 1re case.
    col_lays = [_rich_layout(str(lbl), col_w - 2 * mm, lab_fs)
                for lbl in (col_labels or [])]
    head_h = (max([_TABLE_HEAD_H]
                  + [lay["height"] + 2 * _TABLE_CELL_PAD for lay in col_lays])
              if col_labels else 0.0)

    # un libellé de ligne EST une sous-question (« a. 4,8 + ... = 12,5 ») : il
    # porte donc la même pastille a./b./c. qu'une sous-question d'énoncé — la
    # grille sépare déjà les lignes, la pastille dit de quelle question il s'agit
    row_lays = [_rich_layout(str(lbl), max(8 * mm, rowlab_w - 2 * _TABLE_CELL_PAD),
                             lab_fs, sub_badge_color=sub_badge_color)
                for lbl in (row_labels or [])]
    row_hs = [max(_TABLE_ROW_MIN_H,
                  (row_lays[i]["height"] + 2 * _TABLE_CELL_PAD) if i < len(row_lays) else 0.0)
              for i in range(rows)]
    return {"rows": rows, "cols": cols, "head_h": head_h, "rowlab_w": rowlab_w,
            "grid_w": grid_w, "col_w": col_w, "col_lays": col_lays,
            "row_lays": row_lays, "row_hs": row_hs, "lab_fs": lab_fs,
            "height": head_h + sum(row_hs) + 2 * mm}


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
                 sub_badge_color: Color | None = None) -> float:
    grading = grading or {}
    if response_type in ("qcm_single", "qcm_multiple"):
        _items, total_h, _ncols = _qcm_layout(choices, width - 2 * CARD_PAD, font_size)
        return total_h + 2.5 * mm
    if response_type == "short_text":
        return 0.0 if inline else 13 * mm
    if response_type == "multi_blank":
        return 0.0  # cases dessinées en ligne dans l'énoncé, jamais de zone dédiée
    if response_type == "multiline_text":
        lines = max(3, min(12, int(grading.get("lines", 5))))
        return lines * MULTILINE_ROW_H + 4 * mm
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
    rows, cols = geo["rows"], geo["cols"]
    head_h, col_w, lab_fs = geo["head_h"], geo["col_w"], geo["lab_fs"]
    grid_x = x + CARD_PAD + geo["rowlab_w"]
    grid_w = geo["grid_w"]
    grid_top = y + h - 1 * mm
    grid_bottom = y + 1 * mm

    c.setStrokeColor(CARD_BORDER)
    c.setLineWidth(0.7)
    c.rect(grid_x, grid_bottom, grid_w, grid_top - head_h - grid_bottom, stroke=1, fill=0)
    if col_labels:
        c.setFillColor(black)
        for j, lay in enumerate(geo["col_lays"]):
            _draw_rich(c, grid_x + j * col_w + 1 * mm,
                       grid_top - (head_h - lay["height"]) / 2, lay,
                       centered=True, width=col_w - 2 * mm)
        c.line(grid_x, grid_top - head_h, grid_x + grid_w, grid_top - head_h)

    cells_meta = []
    ry_top = grid_top - head_h
    for i in range(rows):
        row_h = geo["row_hs"][i]
        row_meta = []
        if i > 0:
            c.setStrokeColor(CARD_BORDER)
            c.setLineWidth(0.5)
            c.line(grid_x, ry_top, grid_x + grid_w, ry_top)
        if i < len(geo["row_lays"]):
            lay = geo["row_lays"][i]
            _draw_rich(c, x + CARD_PAD, ry_top - (row_h - lay["height"]) / 2, lay)
        for j in range(cols):
            cx = grid_x + j * col_w
            if j > 0:
                c.setStrokeColor(CARD_BORDER)
                c.setLineWidth(0.5)
                c.line(cx, grid_bottom, cx, grid_top - head_h)
            # case centrée dans la cellule, plafonnée à la taille d'écriture
            # manuscrite (BLANK_W x BLANK_H) — une cellule large ne l'étire pas
            bw = min(BLANK_W, col_w - 2 * _TABLE_CELL_PAD)
            bh = min(BLANK_H, row_h - 2 * _TABLE_CELL_PAD)
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
            row_meta.append({"x_pt": bx, "y_pt": by, "w_pt": bw, "h_pt": bh})
        cells_meta.append(row_meta)
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
            bx = x + CARD_PAD + it["dx"]
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
            inner = 1.1 * mm
            boxes.append({"index": it["index"], "x_pt": bx + inner, "y_pt": by + inner,
                          "w_pt": it["box"] - 2 * inner, "h_pt": it["box"] - 2 * inner})
        meta["boxes"] = boxes
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


def _exercise_card_h(layout: dict, zone_h: float, tpl: dict) -> float:
    """Hauteur totale de l'unité (carte + espace + bande de correction),
    toujours placée d'un bloc (jamais coupée par saut de colonne/page).

    Plus de ligne de titre : le badge numéroté vit DANS la 1re ligne de
    l'énoncé (layout["intro"], dimensionnée par _badge_min_asc), et la hauteur
    d'en-tête qu'elle coûtait est rendue au contenu."""
    return layout["height"] + zone_h + STRIP_H + STRIP_GAP + 3 * CARD_PAD


def _draw_exercise_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                        seq: int, layout: dict, zone_h: float,
                        level5: int, response_type: str, choices: list[str],
                        tpl: dict, font_size: float, zone_fs: float,
                        grading: dict | None = None) -> tuple[float, dict, dict]:
    """Carte exercice + bande de correction hors carte (§ correction).
    Retourne (hauteur totale, geo zone réponse, meta).

    `font_size` est le corps du gabarit — celui sur lequel _exercise_layout a
    dimensionné le badge numéroté et son retrait ; `zone_fs` celui qui a servi à
    mesurer `zone_h`. Tous deux viennent de _exercise_layout et ne se redérivent
    pas ici : les redériver, c'est les désaccorder de la mesure. Le corps de
    l'énoncé, lui, n'est plus une affaire de carte du tout — chaque ligne porte
    le sien (cf. _rich_layout)."""
    border = HexColor(tpl.get("border", "#C7CDD4"))
    radius = max(0.0, float(tpl.get("radius", 2.2))) * mm
    card_h = _exercise_card_h(layout, zone_h, tpl)
    y = y_top - card_h                              # bas de l'unité entière (carte + strip)
    card_bottom = y + STRIP_H + STRIP_GAP            # bas de la carte seule (strip exclue)
    card_h_body = card_h - STRIP_H - STRIP_GAP

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
    first = layout["intro"]["lines"][0] if layout["intro"]["lines"] else None
    _draw_badge(c, x + CARD_PAD, ty - (first["asc"] if first else font_size * 0.78),
                font_size, str(seq), _difficulty_color(level5))
    line_y = _draw_rich(c, x + CARD_PAD, ty, layout["intro"], blanks=inline_blanks)
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
                                 zone_fs, grading, _difficulty_color(level5))
        zone_geo = {"x_pt": x, "y_pt": zone_y, "w_pt": w, "h_pt": zone_h}

    # bande de correction : HORS carte, collée (espace blanc visible, jamais
    # coupée par saut de colonne/page), cadre invisible sur le sujet imprimé —
    # la géométrie reste réservée pour l'overlay de correction.
    c.setFillColor(black)

    meta["correction_strip"] = {"x_pt": x + CARD_PAD, "y_pt": y + 1.2 * mm,
                                "w_pt": w - 2 * CARD_PAD, "h_pt": STRIP_H - 2 * mm}
    return card_h, zone_geo, meta


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
    fs = max(6, int(tpl.get("font_size", 8)))
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
    c.setFont("Helvetica-Bold", fs)
    c.drawString(x + CARD_PAD + 4.4 * mm, ty + 0.8 * mm, _pdf_safe(title)[:80])

    inner = w - 2 * CARD_PAD
    gutter_x = x + CARD_PAD
    line_y = ty - 1.2 * mm
    example_top = None
    for kind, payload, indent, part_fs, part_gap in layout["parts"]:
        if kind == "subtitle":
            c.setFillColor(border)
            c.setFont("Helvetica-Bold", max(6.5, part_fs - 0.5))
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
            font = "Helvetica-Oblique" if kind == "rappel" else "Helvetica"
            _draw_rich(c, text_x, line_y, payload, color=txt_color, font=font)
            line_y -= block_h + part_gap
            continue
        _draw_rich(c, gutter_x + indent, line_y, payload,
                   color=text_color, font="Helvetica")
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


def _exercise_layout(item: dict, font_size: int, math_fs: int) -> tuple[dict, float, float]:
    """(layout de l'énoncé, corps de la zone de réponse, hauteur de zone) d'un
    exercice — UNE définition, appelée à l'identique par la mesure
    (estimate_item_height) et par le dessin (render_copy). Deux constructions
    parallèles dériveraient, et c'est cet écart que test_page_fill traque.

    L'énoncé est mis en page au corps du GABARIT : c'est _rich_layout qui
    agrandit, ligne par ligne, les seules phrases portant une case à remplir
    (blank_fs). La zone de réponse a son propre corps (_zone_font_size), d'où
    les deux valeurs distinctes."""
    rtype = item["response_type"]
    badge_w, _bh, _bfs = _badge_metrics(font_size)
    layout = _statement_layout(item["statement"], COL_W - 2 * CARD_PAD, font_size,
                               math_fs, item.get("figure"),
                               first_indent=badge_w + BADGE_GAP,
                               first_min_asc=_badge_min_asc(font_size),
                               blank_fs=font_size + BLANK_FONT_BOOST,
                               sub_badge_color=_difficulty_color(item.get("level5", 3)))
    zone_fs = _zone_font_size(rtype, font_size)
    zone_h = _zone_height(rtype, item.get("choices", []), COL_W, zone_fs,
                          item.get("grading"), item.get("inline", False),
                          _difficulty_color(item.get("level5", 3)))
    return layout, zone_fs, zone_h


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
    layout, _fs, zone_h = _exercise_layout(item, font_size, math_fs)
    return _exercise_card_h(layout, zone_h, ex_tpl) + GAP


def render_copy(pdf_canvas: canvas.Canvas, *, student_name: str, class_name: str,
                title: str, assessment_type: str, items: list[dict],
                pages_meta: list[dict], font_size: int = 9,
                tpl: dict | None = None) -> list[dict]:
    """Dessine une copie complète. `items` : dicts avec kind=exercise
    (item_id, statement, response_type, choices, level5) ou kind=lesson
    (title, content, example). `tpl` : templates éditables (runtime_settings).
    Retourne les zones pour le manifeste."""
    tpl = tpl or DEFAULT_TEMPLATES
    ex_tpl, lesson_tpl = tpl["exercise"], tpl["lesson"]
    font_size = int(ex_tpl.get("font_size", font_size))
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

    _draw_markers(pdf_canvas, pages_meta[0]["payload"])
    _draw_header(pdf_canvas, student_name, class_name, title, assessment_type, today,
                 tpl["header"])

    seq = 0
    for item in items:
        x = MARGIN + col * (col_w + COL_GAP)
        if item.get("kind") == "lesson":
            fs = max(6, int(lesson_tpl.get("font_size", 8)))
            blocks = item.get("blocks") or {
                # compatibilité rappels v2 (deux paragraphes plats)
                "essentiel": item.get("content", ""),
                "exemple": {"enonce": item.get("example", ""), "etapes": [],
                            "resultat": ""} if item.get("example") else {},
            }
            lay = _lesson_layout(blocks, col_w, fs)
            place(_lesson_card_h(lay, lesson_tpl) + gap)
            x = MARGIN + col * (col_w + COL_GAP)
            used = _draw_lesson_card(pdf_canvas, x, y_cursor, col_w,
                                     item.get("title", "Rappel"), lay, lesson_tpl)
            y_cursor -= used + gap
            continue

        seq += 1
        choices = item.get("choices", [])
        layout, zone_fs, zone_h = _exercise_layout(item, font_size, math_fs)
        card_h = _exercise_card_h(layout, zone_h, ex_tpl)
        place(card_h + gap)
        x = MARGIN + col * (col_w + COL_GAP)

        _, zone_geo, meta = _draw_exercise_card(
            pdf_canvas, x, y_cursor, col_w, seq, layout, zone_h,
            item.get("level5", 3), item["response_type"], choices, ex_tpl,
            font_size, zone_fs, item.get("grading"))
        zones.append({
            "item_id": item["item_id"], "page_index": page_idx,
            "page_id": pages_meta[page_idx]["page_id"],
            "type": item["response_type"], **zone_geo, "meta": meta,
        })
        y_cursor -= card_h + gap

    pdf_canvas.showPage()
    return zones


# ------------------------------------------------------------------ overlay

def _mark(c: canvas.Canvas, x: float, y: float, ok: bool, size: float = 2.4 * mm):
    """Coche ou croix vectorielle (fiable quel que soit le lecteur PDF)."""
    c.saveState()
    c.setLineWidth(1.1)
    if ok:
        c.line(x, y + size * 0.35, x + size * 0.35, y)
        c.line(x + size * 0.35, y, x + size, y + size * 0.9)
    else:
        c.line(x, y, x + size * 0.8, y + size * 0.8)
        c.line(x, y + size * 0.8, x + size * 0.8, y)
    c.restoreState()


PROGRESS_GREEN = HexColor("#2E7D32")
PROGRESS_TRACK = HexColor("#DCE7DC")


def _draw_appreciation_content(c: canvas.Canvas, geo: dict, progress: list[dict],
                               synthesis: str):
    """Barres de progrès (vert uniquement, jamais de rouge) + synthèse Haiku,
    dessinées dans le rect Appréciation de header_geometry()."""
    ax, ay, aw, ah = geo["appreciation"]["x"], geo["appreciation"]["y"], \
        geo["appreciation"]["w"], geo["appreciation"]["h"]
    inner_x = ax + 3.5 * mm
    inner_w = aw - 7 * mm
    # sous le libellé « APPRÉCIATION » imprimé sur le sujet, jamais dessus
    y = ay + ah - HEADER_PAD_V - HEADER_LABEL_DY - 3.5 * mm
    bar_h = 2.2 * mm
    for p in progress:
        label = f"{p['competency_name']}  {round(p['pct_acquired'] * 100)}%"
        c.setFont("Helvetica", 6.5)
        c.setFillColor(black)
        c.drawString(inner_x, y, _pdf_safe(label)[:48])
        y -= 3 * mm
        c.setFillColor(PROGRESS_TRACK)
        c.roundRect(inner_x, y - bar_h, inner_w, bar_h, bar_h / 2, stroke=0, fill=1)
        c.setFillColor(PROGRESS_GREEN)
        fill_w = max(bar_h, inner_w * min(1.0, p["pct_acquired"]))
        c.roundRect(inner_x, y - bar_h, fill_w, bar_h, bar_h / 2, stroke=0, fill=1)
        y -= bar_h + 2.5 * mm
    if synthesis:
        c.setFillColor(HexColor("#37474F"))
        c.setFont("Helvetica-Oblique", 6.5)
        for line in _wrap(synthesis, inner_w, 6.5)[:3]:
            c.drawString(inner_x, y, line)
            y -= 3 * mm
    c.setFillColor(black)


def _draw_correction_marks(c: canvas.Canvas, page: dict, col):
    """Dessine les marques de correction d'une page (nom, note, appréciation,
    scores par exercice) dans la couleur d'encre `col`. Partagé par l'overlay
    (fond blanc) et l'aperçu « copie + overlay » (fond = scan recalé)."""
    geo = header_geometry(page.get("assessment_type", "control"))
    c.setFillColor(col)
    c.setStrokeColor(col)
    # nom de l'élève sous le QR : l'élève vérifie que la correction est la sienne
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
    for z in page.get("page_zones", []):
        strip = z.get("strip")
        ok = bool(z.get("full_credit"))
        # points de BARÈME (cf. services.pipeline.build_overlays), donc des
        # demis : « 1,5/2 » à la française, jamais « 1.5/2.0 » — c'est lu
        # par un élève de 5e sur sa copie.
        score_txt = (f"{scoring.format_points(z['score'])}/"
                     f"{scoring.format_points(z['max_score'])}")
        if strip:
            sx, sy, sw, _sh = strip["x_pt"], strip["y_pt"], strip["w_pt"], strip["h_pt"]
            c.setFont("Helvetica-Bold", 8)
            c.drawRightString(sx + sw - 1.5 * mm, sy + 1.6 * mm, score_txt)
            _mark(c, sx + sw - 15 * mm, sy + 1.4 * mm, ok)
            if z.get("text"):
                c.setFont("Helvetica", 7.5)
                line = _wrap(z["text"], sw - 24 * mm, 7.5)[0]
                c.drawString(sx + 1.5 * mm, sy + 1.6 * mm, line)
        else:
            _mark(c, z["x_pt"] + z["w_pt"] - 16 * mm, z["y_pt"] + z["h_pt"] + 1.5 * mm, ok)
            c.setFont("Helvetica", 9)
            c.drawString(z["x_pt"] + z["w_pt"] - 12 * mm,
                         z["y_pt"] + z["h_pt"] + 1.5 * mm, score_txt)


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

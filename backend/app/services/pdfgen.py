"""Génération documentaire (§5) : copies A4, QR + fiduciels signés, zones
dropout, overlay.

Design des sujets :
- en-tête en deux colonnes : à gauche la case Note (agrandie, sans mention
  d'échelle) et la bande Appréciation (agrandie) en POINTILLÉS, remplies par
  l'overlay de correction ; à droite l'identité de l'élève (nom/prénom/classe,
  gros) puis le méta du sujet (date, type, titre du lot, petit) — un filet
  sépare clairement l'en-tête des exercices ;
- chaque exercice dans une carte à coins arrondis avec ombre portée, icône
  crayon, pastilles de difficulté (1-5) ;
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
DOT_OFF = HexColor("#D3DCE3")

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

# En-tête en 4 zones horizontales contiguës, pleine hauteur, sans séparateur
# vertical : Note (23mm, contrôle seul) | Appréciation (80mm, absorbe la Note
# en entraînement) | Métadonnées (justifié droite) | QR/fiduciels (inchangé).
NOTE_W, NOTE_H = 23 * mm, 15 * mm
APPRECIATION_W = 80 * mm
QR_ZONE_W = QR_MAIN + 2 * MARGIN + 2 * mm
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
    note_w = NOTE_W if show_note else 0.0
    note_x = HEADER_LEFT
    appreciation_w = APPRECIATION_W + (0.0 if show_note else NOTE_W)
    appreciation_x = note_x + note_w
    meta_x = appreciation_x + appreciation_w
    meta_w = max(20 * mm, PAGE_W - MARGIN - QR_ZONE_W - meta_x)
    qr_x = PAGE_W - MARGIN - QR_ZONE_W
    return {
        "note": {"x": note_x, "y": bottom, "w": note_w, "h": h, "visible": show_note},
        "appreciation": {"x": appreciation_x, "y": bottom, "w": appreciation_w, "h": h},
        "meta": {"x": meta_x, "y": bottom, "w": meta_w, "h": h},
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

def _icon_pencil(c: canvas.Canvas, x: float, y: float, size: float = 3.2 * mm,
                 color=DOT_ON):
    """Petit crayon vectoriel (corps incliné + pointe)."""
    c.saveState()
    c.translate(x, y)
    c.rotate(45)
    c.setFillColor(color)
    c.setStrokeColor(color)
    body_w, body_h = size * 0.32, size * 0.85
    c.rect(-body_w / 2, 0, body_w, body_h, stroke=0, fill=1)
    p = c.beginPath()
    p.moveTo(-body_w / 2, 0)
    p.lineTo(body_w / 2, 0)
    p.lineTo(0, -size * 0.32)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


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


def _difficulty_dots(c: canvas.Canvas, x_right: float, y: float, level5: int,
                     color=DOT_ON):
    """5 pastilles, remplies jusqu'au niveau de difficulté."""
    r = 0.85 * mm
    for i in range(5):
        cx = x_right - (4 - i) * 2.6 * mm
        c.setFillColor(color if i < level5 else DOT_OFF)
        c.circle(cx, y, r, stroke=0, fill=1)


def _dotted(c: canvas.Canvas):
    c.setDash(1.6, 1.8)
    c.setStrokeColor(DOTTED_GRAY)
    c.setLineWidth(0.7)


def _solid(c: canvas.Canvas):
    c.setDash()


# ------------------------------------------------------------------- en-tête

def _draw_header(c: canvas.Canvas, student_name: str, class_name: str, title: str,
                 assessment_type: str, the_date: str, tpl: dict | None = None):
    """En-tête en 4 zones pleine hauteur, gauche -> droite, sans séparateur
    vertical : Note (contrôle seul) | Appréciation | Métadonnées | QR."""
    tpl = tpl or DEFAULT_TEMPLATES["header"]
    accent = HexColor(tpl.get("accent", "#37474F"))
    name_fs = float(tpl.get("name_size", 14))
    title_fs = float(tpl.get("title_size", 8))
    y_top = PAGE_H - MARGIN
    header_bottom = y_top - HEADER_H
    label = "Contrôle" if assessment_type == "control" else "Entraînement"
    geo = header_geometry(assessment_type)

    # --- zone Note (23mm, contrôle uniquement) ---
    if geo["note"]["visible"]:
        nx, ny, nw, nh = geo["note"]["x"], geo["note"]["y"], geo["note"]["w"], geo["note"]["h"]
        _dotted(c)
        c.roundRect(nx + 1.5 * mm, ny + 1.5 * mm, nw - 3 * mm, nh - 3 * mm, 2 * mm)
        _solid(c)
        c.setFillColor(DOTTED_GRAY)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(nx + nw / 2, ny + nh - 5 * mm, "NOTE")
        c.setFillColor(black)

    # --- zone Appréciation (absorbe la Note en mode entraînement) ---
    ax, ay, aw, ah = geo["appreciation"]["x"], geo["appreciation"]["y"], \
        geo["appreciation"]["w"], geo["appreciation"]["h"]
    _dotted(c)
    c.roundRect(ax + 1.5 * mm, ay + 1.5 * mm, aw - 3 * mm, ah - 3 * mm, 2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 6.5)
    c.drawString(ax + 3.5 * mm, ay + ah - 5 * mm,
                 "APPRÉCIATION — remplie à la correction")
    c.setFillColor(black)

    # --- zone Métadonnées, justifiée à droite (nom/prénom, classe, titre, date) ---
    mx, my, mw, mh = geo["meta"]["x"], geo["meta"]["y"], geo["meta"]["w"], geo["meta"]["h"]
    meta_right = geo["qr"]["x"] - 2 * mm
    line_y = my + mh - name_fs
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", name_fs)
    c.drawRightString(meta_right, line_y, f"{student_name}  /  {class_name}")
    line_y -= name_fs * 0.55 + 2 * mm
    c.setFont("Helvetica-Bold", title_fs)
    c.setFillColor(accent)
    c.drawRightString(meta_right, line_y, title)
    if tpl.get("show_date", True):
        line_y -= title_fs * 0.7 + 1.6 * mm
        c.setFont("Helvetica", max(6.0, title_fs - 1))
        c.setFillColor(HexColor("#6A737C"))
        c.drawRightString(meta_right, line_y, f"{label}  ·  {the_date}")

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
    balisage $ est converti vers le contrat balisé (fractions empilées)."""
    if "$" in statement or ":" not in statement:
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


BLANK_TOKEN = "{{blank}}"
BLANK_W = 18 * mm


def _rich_layout(text: str, width: float, fs: float, math_fs: float | None = None) -> dict:
    """Met en page un texte balisé $...$ : flot de mots et d'images maths.
    Retourne {lines: [{segs, asc, desc, h, w}], height} ; seg = ("word", str)
    ou ("math", ImageReader, w, h, d) ou ("blank", w, asc, desc) — case de
    réponse courte insérée en ligne (marqueur BLANK_TOKEN)."""
    from . import mathrender
    math_fs = math_fs or fs
    # seg = ("word", texte, glue) | ("math", img, w, h, d, glue) |
    # ("blank", w, asc, desc, glue) ; glue = collé au segment précédent SANS
    # espace (ponctuation après une formule, etc.)
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
                    segs.append(("blank", BLANK_W, fs * 0.78, fs * 0.24, False))
                    prev_no_space = False
        else:
            _emit_words(content)

    space_w = stringWidth(" ", "Helvetica", fs)

    def _seg_w(seg: tuple) -> float:
        if seg[0] == "word":
            return stringWidth(seg[1], "Helvetica", fs)
        if seg[0] == "blank":
            return seg[1]
        return seg[2]

    def _seg_glue(seg: tuple) -> bool:
        return bool(seg[-1])

    raw_lines: list[list[tuple]] = []
    cur: list[tuple] = []
    cur_w = 0.0
    for seg in segs:
        w = _seg_w(seg)
        add = w if (not cur or _seg_glue(seg)) else w + space_w
        if cur and cur_w + add > width:
            raw_lines.append(cur)
            cur, cur_w = [seg], w
        else:
            cur.append(seg)
            cur_w += add
    if cur:
        raw_lines.append(cur)

    lines, total_h = [], 0.0
    for line in raw_lines:
        asc, desc = fs * 0.78, fs * 0.24
        for seg in line:
            if seg[0] == "math":
                asc = max(asc, seg[3] - seg[4])
                desc = max(desc, seg[4])
            elif seg[0] == "blank":
                asc = max(asc, seg[2])
                desc = max(desc, seg[3])
        lh = asc + desc + 2.2
        n_spaces = sum(1 for j, s in enumerate(line) if j > 0 and not _seg_glue(s))
        lines.append({"segs": line, "asc": asc, "desc": desc, "h": lh,
                      "w": sum(_seg_w(s) for s in line) + space_w * n_spaces})
        total_h += lh
    return {"lines": lines, "height": total_h}


def _draw_rich(c: canvas.Canvas, x: float, y_top: float, layout: dict, fs: float,
               color=black, centered: bool = False, width: float | None = None,
               font: str = "Helvetica", blanks: list | None = None) -> float:
    """Dessine un layout _rich_layout. Retourne le y sous la dernière ligne.
    `blanks`, si fourni, reçoit la géométrie PDF absolue (x_pt/y_pt/w_pt/h_pt)
    de chaque case de réponse courte insérée en ligne (BLANK_TOKEN)."""
    space_w = stringWidth(" ", font, fs)
    y = y_top
    for line in layout["lines"]:
        y_base = y - line["asc"]
        cx = x + ((width - line["w"]) / 2 if centered and width else 0)
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


_DISPLAY_RE = re.compile(r"^(.*?[:?])\s*\$([^$]+)\$\s*\??\s*$", re.DOTALL)


def _statement_layout(statement: str, width: float, font_size: int,
                      math_size: int, figure_json: dict | None = None) -> dict:
    """Met en page un énoncé : texte riche + éventuelle expression finale mise
    en valeur (motif « consigne : $expr$ » -> centrée, plus grande) + figure.
    Retourne {intro, display, figure, height}."""
    statement = _legacy_to_tagged(statement or "")
    display = None
    body = statement
    m = _DISPLAY_RE.match(statement.strip())
    if m and "$" not in m.group(1):
        im = _math_image(m.group(2), math_size)
        if im is not None and im[1] <= width - 4:
            body, display = m.group(1), im
    intro = _rich_layout(body, width, font_size)
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
    item = {index, dx, dy, lay, lw, box} en relatif (origine haut-gauche)."""
    box = 2.0 * mm
    gap_x, gap_y, pad = 6.0 * mm, 1.6 * mm, 1.6 * mm
    lays = []
    max_lw, max_h = 0.0, 6.0 * mm
    for choice in choices:
        lay = _rich_layout(choice, max(width, 10 * mm), font_size)
        lw = max((ln["w"] for ln in lay["lines"]), default=0.0)
        lays.append((lay, lw))
        max_lw = max(max_lw, lw)
        max_h = max(max_h, lay["height"] + gap_y)
    n = len(choices)
    item_w = box + pad + max_lw + gap_x
    ncols = max(1, min(3, n, int(width // item_w) if item_w > 0 else 1))
    nrows = -(-n // ncols)  # ceil
    items = []
    for i, (lay, lw) in enumerate(lays):
        col, row = divmod(i, nrows)
        items.append({"index": i, "dx": col * item_w, "dy": row * max_h,
                      "lay": lay, "lw": lw, "box": box})
    return items, nrows * max_h, ncols


_TABLE_HEAD_H = 6.0 * mm
_TABLE_ROWLAB_W = 15.0 * mm
_TABLE_ROW_H = 7.5 * mm
_MATCHING_PASTILLE = 2.2 * mm
_MATCHING_COL_GAP = 10.0 * mm
_MANUAL_DRAWING_H = 60.0 * mm


def _table_zone_height(rows: int, col_labels: list | None) -> float:
    head_h = _TABLE_HEAD_H if col_labels else 0.0
    return head_h + rows * _TABLE_ROW_H + 2 * mm


def _matching_zone_height(left: list, right: list, font_size: int) -> float:
    n = max(len(left), len(right), 1)
    row_h = max(6.5 * mm, font_size + 4)
    return n * row_h + 3 * mm


def _zone_height(response_type: str, choices: list[str], width: float,
                 font_size: int, grading: dict | None = None,
                 inline: bool = False) -> float:
    grading = grading or {}
    if response_type in ("qcm_single", "qcm_multiple"):
        _items, total_h, _ncols = _qcm_layout(choices, width - 2 * CARD_PAD, font_size)
        return total_h + 2.5 * mm
    if response_type == "short_text":
        return 0.0 if inline else 13 * mm
    if response_type == "multiline_text":
        lines = max(3, min(12, int(grading.get("lines", 5))))
        return lines * 7 * mm + 4 * mm
    if response_type == "table_fill":
        cells = grading.get("cells") or [[]]
        return _table_zone_height(len(cells), grading.get("col_labels"))
    if response_type == "matching":
        return _matching_zone_height(grading.get("left", []), grading.get("right", []),
                                     font_size)
    if response_type == "manual_drawing":
        return _MANUAL_DRAWING_H
    return 13 * mm


def _draw_table_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                     col_labels: list | None, row_labels: list | None,
                     cells: list[list[dict]], font_size: int) -> dict:
    rows, cols = len(cells), len(cells[0]) if cells else 0
    head_h = _TABLE_HEAD_H if col_labels else 0.0
    rowlab_w = _TABLE_ROWLAB_W if row_labels else 0.0
    grid_x = x + CARD_PAD + rowlab_w
    grid_w = w - 2 * CARD_PAD - rowlab_w
    grid_top = y + h - 1 * mm
    grid_bottom = y + 1 * mm
    row_h = (grid_top - head_h - grid_bottom) / max(1, rows)
    col_w = grid_w / max(1, cols)

    c.setStrokeColor(CARD_BORDER)
    c.setLineWidth(0.7)
    c.rect(grid_x, grid_bottom, grid_w, grid_top - head_h - grid_bottom, stroke=1, fill=0)
    if col_labels:
        c.setFillColor(black)
        for j, label in enumerate(col_labels):
            lay = _rich_layout(str(label), col_w - 2 * mm, max(6, font_size - 1))
            _draw_rich(c, grid_x + j * col_w + 1 * mm, grid_top - 1.6 * mm, lay,
                       max(6, font_size - 1), centered=True, width=col_w - 2 * mm)
        c.line(grid_x, grid_top - head_h, grid_x + grid_w, grid_top - head_h)
    if row_labels:
        for i, label in enumerate(row_labels):
            ry = grid_top - head_h - i * row_h - row_h / 2
            lay = _rich_layout(str(label), rowlab_w - 3 * mm, max(6, font_size - 1))
            _draw_rich(c, x + CARD_PAD, ry + lay["height"] / 2, lay, max(6, font_size - 1))

    cells_meta = []
    for i in range(rows):
        row_meta = []
        ry_top = grid_top - head_h - i * row_h
        if i > 0:
            c.setStrokeColor(CARD_BORDER)
            c.setLineWidth(0.5)
            c.line(grid_x, ry_top, grid_x + grid_w, ry_top)
        for j in range(cols):
            cx = grid_x + j * col_w
            if j > 0:
                c.setStrokeColor(CARD_BORDER)
                c.setLineWidth(0.5)
                c.line(cx, grid_bottom, cx, grid_top - head_h)
            inset = 1.2 * mm
            bx, by = cx + inset, ry_top - row_h + inset
            bw, bh = col_w - 2 * inset, row_h - 2 * inset
            c.setStrokeColor(DROPOUT)
            c.setLineWidth(0.7)
            c.roundRect(bx, by, bw, bh, 0.8 * mm)
            row_meta.append({"x_pt": bx, "y_pt": by, "w_pt": bw, "h_pt": bh})
        cells_meta.append(row_meta)
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
        _draw_rich(c, x + CARD_PAD, ly + lay["height"] / 2, lay, font_size)
        px, py = x + CARD_PAD + col_w - p - 1 * mm, ly - p / 2
        _pastille(px, py)
        left_pts.append({"index": i, "x_pt": px, "y_pt": py, "w_pt": p, "h_pt": p})
    for i, label in enumerate(right):
        ry = top - i * row_h - row_h / 2
        px = x + CARD_PAD + col_w + _MATCHING_COL_GAP
        py = ry - p / 2
        _pastille(px, py)
        lay = _rich_layout(label, col_w - p - 3 * mm, font_size)
        _draw_rich(c, px + p + 2 * mm, ry + lay["height"] / 2, lay, font_size)
        right_pts.append({"index": i, "x_pt": px, "y_pt": py, "w_pt": p, "h_pt": p})
    c.setFillColor(black)
    c.setStrokeColor(black)
    return {"left_points": left_pts, "right_points": right_pts}


def _draw_answer_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                      response_type: str, choices: list[str], font_size: int,
                      grading: dict | None = None) -> dict:
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
            by = top - it["dy"] - it["box"]
            c.setStrokeColor(DROPOUT)
            c.rect(bx, by, it["box"], it["box"])
            # label riche (formules rendues), 1re ligne de base alignée sur la case
            first_asc = it["lay"]["lines"][0]["asc"] if it["lay"]["lines"] else font_size
            _draw_rich(c, bx + it["box"] + 1.6 * mm, by + 0.8 * mm + first_asc,
                       it["lay"], font_size)
            c.setStrokeColor(DROPOUT)
            inner = 1.1 * mm
            boxes.append({"index": it["index"], "x_pt": bx + inner, "y_pt": by + inner,
                          "w_pt": it["box"] - 2 * inner, "h_pt": it["box"] - 2 * inner})
        meta["boxes"] = boxes
    elif response_type == "table_fill":
        meta = _draw_table_zone(c, x, y, w, h, grading.get("col_labels"),
                                grading.get("row_labels"), grading.get("cells") or [],
                                font_size)
    elif response_type == "matching":
        meta = _draw_matching_zone(c, x, y, w, h, grading.get("left", []),
                                   grading.get("right", []), font_size)
    elif response_type == "manual_drawing":
        c.roundRect(x + CARD_PAD, y + 1 * mm, w - 2 * CARD_PAD, h - 2 * mm, 1.5 * mm)
    else:
        c.roundRect(x + CARD_PAD, y + 1 * mm, w - 2 * CARD_PAD, h - 2 * mm, 1.5 * mm)
        if response_type == "multiline_text":
            c.setLineWidth(0.35)
            line_gap = 7 * mm
            ly = y + h - 1 * mm - line_gap
            while ly > y + 3 * mm:
                c.line(x + CARD_PAD + 1.5 * mm, ly, x + w - CARD_PAD - 1.5 * mm, ly)
                ly -= line_gap
    c.setFillColor(black)
    return meta


def _exercise_head_h(tpl: dict) -> float:
    return max(5.2 * mm, float(tpl.get("title_size", 9)) + 6)


def _exercise_card_h(layout: dict, zone_h: float, tpl: dict) -> float:
    """Hauteur totale de l'unité (carte + espace + bande de correction),
    toujours placée d'un bloc (jamais coupée par saut de colonne/page)."""
    return (_exercise_head_h(tpl) + layout["height"] + zone_h
            + STRIP_H + STRIP_GAP + 3 * CARD_PAD)


def _draw_exercise_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                        seq: int, layout: dict, zone_h: float,
                        level5: int, response_type: str, choices: list[str],
                        tpl: dict, grading: dict | None = None) -> tuple[float, dict, dict]:
    """Carte exercice + bande de correction hors carte (§ correction). Retourne
    (hauteur totale, geo zone réponse, meta)."""
    font_size = int(tpl.get("font_size", 9))
    title_fs = float(tpl.get("title_size", font_size))
    border = HexColor(tpl.get("border", "#C7CDD4"))
    accent = HexColor(tpl.get("accent", "#455A64"))
    radius = max(0.0, float(tpl.get("radius", 2.2))) * mm
    head_h = _exercise_head_h(tpl)
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

    # ligne de titre : icône + "Exercice N" + pastilles difficulté
    ty = card_bottom + card_h_body - head_h
    _icon_pencil(c, x + CARD_PAD + 1.2 * mm, ty + 1.2 * mm, color=accent)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", title_fs)
    c.drawString(x + CARD_PAD + 3.6 * mm, ty + 0.8 * mm, f"Exercice {seq}")
    _difficulty_dots(c, x + w - CARD_PAD - 1 * mm, ty + 1.8 * mm, level5, color=accent)
    c.setStrokeColor(HexColor("#E4E8EC"))
    c.setLineWidth(0.5)
    c.line(x + CARD_PAD, ty - 0.6 * mm, x + w - CARD_PAD, ty - 0.6 * mm)

    # énoncé riche (texte + formules PNG), expression finale mise en valeur,
    # figure paramétrée éventuelle ; `blanks` récupère la géométrie d'une
    # éventuelle case de réponse courte insérée en ligne (short_text inline)
    inline_blanks: list = []
    line_y = _draw_rich(c, x + CARD_PAD, ty - 1.2 * mm, layout["intro"], font_size,
                        blanks=inline_blanks)
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

    # zone réponse élève (saumon) — sauf short_text inline : la case fait déjà
    # partie de l'énoncé (inline_blanks), pas de zone dédiée sous le texte
    zone_y = card_bottom + CARD_PAD
    if response_type == "short_text" and inline_blanks:
        b = inline_blanks[0]
        zone_geo = {"x_pt": b["x_pt"], "y_pt": b["y_pt"], "w_pt": b["w_pt"], "h_pt": b["h_pt"]}
        meta = {}
    else:
        meta = _draw_answer_zone(c, x, zone_y, w, zone_h, response_type, choices,
                                 font_size, grading)
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
            _draw_rich(c, text_x, line_y, payload, part_fs, color=txt_color, font=font)
            line_y -= block_h + part_gap
            continue
        _draw_rich(c, gutter_x + indent, line_y, payload, part_fs,
                   color=text_color, font="Helvetica")
        line_y -= payload["height"] + part_gap
    c.setFillColor(black)
    return card_h


# ------------------------------------------------------------- copie entière

def estimate_capacity(pages_target: int) -> float:
    """Hauteur totale (pt) disponible pour des cartes sur `pages_target` pages,
    deux colonnes chacune — sert au remplissage automatique de la page
    (§ remplissage) pour savoir combien d'exercices supplémentaires tiennent."""
    bottom_limit = MARGIN + QR_MINI + 3 * mm
    first_page = (PAGE_H - MARGIN - HEADER_H - 4 * mm) - bottom_limit
    other_page = (PAGE_H - MARGIN - QR_MAIN - 6 * mm) - bottom_limit
    pages = max(1, pages_target)
    per_page = first_page + max(0, pages - 1) * other_page
    return per_page * 2  # deux colonnes par page


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
    choices = item.get("choices", [])
    layout = _statement_layout(item["statement"], COL_W - 2 * CARD_PAD,
                               font_size, math_fs, item.get("figure"))
    zone_h = _zone_height(item["response_type"], choices, COL_W, font_size,
                          item.get("grading"), item.get("inline", False))
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
    y_cursor = PAGE_H - MARGIN - HEADER_H - 4 * mm
    bottom_limit = MARGIN + QR_MINI + 3 * mm
    gap = GAP

    def top_of_page() -> float:
        return (PAGE_H - MARGIN - HEADER_H - 4 * mm) if page_idx == 0 \
            else (PAGE_H - MARGIN - QR_MAIN - 6 * mm)

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
        layout = _statement_layout(item["statement"], col_w - 2 * CARD_PAD,
                                   font_size, math_fs, item.get("figure"))
        zone_h = _zone_height(item["response_type"], choices, col_w, font_size,
                              item.get("grading"), item.get("inline", False))
        card_h = _exercise_card_h(layout, zone_h, ex_tpl)
        place(card_h + gap)
        x = MARGIN + col * (col_w + COL_GAP)

        _, zone_geo, meta = _draw_exercise_card(
            pdf_canvas, x, y_cursor, col_w, seq, layout, zone_h,
            item.get("level5", 3), item["response_type"], choices, ex_tpl,
            item.get("grading"))
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
    y = ay + ah - 8 * mm
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


def render_overlay(path: str, *, copies_annotations: list[dict],
                   color: str | None = None):
    """Overlay de correction (§5.6) : pages blanches, annotations seules,
    calées sur les zones de l'en-tête (case Note, zone Appréciation) et les
    bandes de correction sous chaque exercice — même géométrie que le sujet
    (header_geometry) pour un recalage physique via les fiduciels."""
    col = HexColor(color or settings.correction_color)
    c = canvas.Canvas(path, pagesize=A4)
    for page in copies_annotations:
        geo = header_geometry(page.get("assessment_type", "control"))
        c.setFillColor(col)
        c.setStrokeColor(col)
        # nom de l'élève sous le QR : l'élève vérifie que la correction est la sienne
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - QR_MAIN - 4 * mm,
                          f"Correction — {page.get('student', '')}")
        if page.get("note") is not None and geo["note"]["visible"]:
            nx, ny, nw, nh = geo["note"]["x"], geo["note"]["y"], geo["note"]["w"], geo["note"]["h"]
            box_h = 15 * mm
            box_y = ny + nh - box_h - 3 * mm
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(nx + nw / 2, box_y + box_h / 2 - 2 * mm, str(page["note"]))
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
            score_txt = f"{z.get('score', '')}/{z.get('max_score', '')}"
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
        c.showPage()
    c.save()


def write_manifest(path: str, manifest: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

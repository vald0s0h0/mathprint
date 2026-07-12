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

CARD_PAD = 2.6 * mm
STRIP_H = 6.5 * mm      # bande de correction (overlay)
STRIP_GAP = 1.6 * mm    # espace blanc visible entre la carte et sa bande de correction
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


def _icon_book(c: canvas.Canvas, x: float, y: float, size: float = 3.4 * mm):
    """Petit livre ouvert vectoriel."""
    c.saveState()
    c.setStrokeColor(LESSON_TEXT)
    c.setLineWidth(0.8)
    h = size * 0.7
    c.line(x, y, x, y + h)                       # reliure
    p = c.beginPath()
    p.moveTo(x, y + h)
    p.curveTo(x - size * 0.55, y + h * 1.15, x - size * 0.55, y + h * 0.15, x, y)
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(x, y + h)
    p.curveTo(x + size * 0.55, y + h * 1.15, x + size * 0.55, y + h * 0.15, x, y)
    c.drawPath(p, stroke=1, fill=0)
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
    class_fs = float(tpl.get("class_size", 10))
    title_fs = float(tpl.get("title_size", 8))
    y_top = PAGE_H - MARGIN
    header_bottom = y_top - HEADER_H
    label = "Contrôle" if assessment_type == "control" else "Entraînement"
    geo = header_geometry(assessment_type)

    # --- zone Note (23mm, contrôle uniquement) ---
    if geo["note"]["visible"]:
        nx, ny, nw, nh = geo["note"]["x"], geo["note"]["y"], geo["note"]["w"], geo["note"]["h"]
        box_h = 15 * mm
        box_y = ny + nh - box_h - 3 * mm
        _dotted(c)
        c.roundRect(nx + 1.5 * mm, box_y, nw - 3 * mm, box_h, 2 * mm)
        _solid(c)
        c.setFillColor(DOTTED_GRAY)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(nx + nw / 2, box_y + box_h + 1.2 * mm, "NOTE")
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
    meta_right = mx + mw - 2 * mm
    line_y = my + mh - name_fs
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", name_fs)
    c.drawRightString(meta_right, line_y, student_name)
    line_y -= name_fs * 0.55 + 2 * mm
    c.setFont("Helvetica-Bold", class_fs)
    c.setFillColor(HexColor("#455A64"))
    c.drawRightString(meta_right, line_y, f"Classe {class_name}")
    line_y -= class_fs * 0.65 + 2 * mm
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


def _rich_layout(text: str, width: float, fs: float, math_fs: float | None = None) -> dict:
    """Met en page un texte balisé $...$ : flot de mots et d'images maths.
    Retourne {lines: [{segs, asc, desc, h, w}], height} ; seg = ("word", str)
    ou ("math", ImageReader, w, h, d)."""
    from . import mathrender
    math_fs = math_fs or fs
    # seg = ("word", texte, glue) | ("math", img, w, h, d, glue) ; glue = collé
    # au segment précédent SANS espace (ponctuation après une formule, etc.)
    segs: list[tuple] = []
    prev_no_space = False  # le flux précédent se termine sans espace
    for content, is_math in mathrender.split_math_spans(text or ""):
        if is_math:
            im = _math_image(content, math_fs)
            if im is not None:
                segs.append(("math", *im, prev_no_space and bool(segs)))
            else:  # repli : texte aplati, jamais de LaTeX brut imprimé
                for j, w in enumerate(_pdf_safe(mathrender.strip_math(f"${content}$")).split()):
                    segs.append(("word", w, j == 0 and prev_no_space and bool(segs)))
            prev_no_space = True
        else:
            words = _pdf_safe(content).split()
            leading_ws = bool(content[:1].isspace())
            for j, w in enumerate(words):
                segs.append(("word", w,
                             j == 0 and not leading_ws and prev_no_space and bool(segs)))
            if words:
                prev_no_space = not content[-1:].isspace()
            elif content:
                prev_no_space = False

    space_w = stringWidth(" ", "Helvetica", fs)

    def _seg_w(seg: tuple) -> float:
        return stringWidth(seg[1], "Helvetica", fs) if seg[0] == "word" else seg[2]

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
        lh = asc + desc + 2.2
        n_spaces = sum(1 for j, s in enumerate(line) if j > 0 and not _seg_glue(s))
        lines.append({"segs": line, "asc": asc, "desc": desc, "h": lh,
                      "w": sum(_seg_w(s) for s in line) + space_w * n_spaces})
        total_h += lh
    return {"lines": lines, "height": total_h}


def _draw_rich(c: canvas.Canvas, x: float, y_top: float, layout: dict, fs: float,
               color=black, centered: bool = False, width: float | None = None,
               font: str = "Helvetica") -> float:
    """Dessine un layout _rich_layout. Retourne le y sous la dernière ligne."""
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


def _qcm_layout(choices: list[str], width: float, font_size: int) -> tuple[list[dict], float]:
    """Disposition compacte : cases en ligne, retour à la ligne auto. Les labels
    sont mis en page en riche (formules rendues). Retourne (items, hauteur) ;
    item = {index, dx, dy, lay, lw, box} en relatif (origine haut-gauche)."""
    box = 4.0 * mm
    gap_x, pad = 4.5 * mm, 1.6 * mm
    x, row = 0.0, 0
    items = []
    row_heights: dict[int, float] = {}
    for i, choice in enumerate(choices):
        lay = _rich_layout(choice, max(width, 10 * mm), font_size)
        lw = max((ln["w"] for ln in lay["lines"]), default=0.0)
        item_w = box + pad + lw + gap_x
        if x + item_w > width and x > 0:
            x, row = 0.0, row + 1
        items.append({"index": i, "dx": x, "row": row, "lay": lay, "lw": lw,
                      "box": box})
        row_heights[row] = max(row_heights.get(row, 6.0 * mm),
                               lay["height"] + 1.6 * mm)
        x += item_w
    offset, row_dy = 0.0, {}
    for r in sorted(row_heights):
        row_dy[r] = offset
        offset += row_heights[r]
    for it in items:
        it["dy"] = row_dy[it["row"]]
    return items, offset


def _zone_height(response_type: str, choices: list[str], width: float,
                 font_size: int) -> float:
    if response_type in ("qcm_single", "qcm_multiple"):
        _items, total_h = _qcm_layout(choices, width - 2 * CARD_PAD, font_size)
        return total_h + 2.5 * mm
    return {"short_text": 13 * mm, "multiline_text": 37 * mm}[response_type]


def _draw_answer_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                      response_type: str, choices: list[str], font_size: int) -> dict:
    """Zone de réponse ÉLÈVE en saumon (dropout). Retourne la méta QCM."""
    meta = {}
    c.setStrokeColor(DROPOUT)
    c.setLineWidth(0.9)
    if response_type in ("qcm_single", "qcm_multiple"):
        items, _total_h = _qcm_layout(choices, w - 2 * CARD_PAD, font_size)
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
                        tpl: dict) -> tuple[float, dict, dict]:
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
    # figure paramétrée éventuelle
    line_y = _draw_rich(c, x + CARD_PAD, ty - 1.2 * mm, layout["intro"], font_size)
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

    # zone réponse élève (saumon)
    zone_y = card_bottom + CARD_PAD
    meta = _draw_answer_zone(c, x, zone_y, w, zone_h, response_type, choices, font_size)

    # bande de correction : HORS carte, collée (espace blanc visible, jamais
    # coupée par saut de colonne/page), cadre invisible sur le sujet imprimé —
    # la géométrie reste réservée pour l'overlay de correction.
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 5)
    c.drawRightString(x + w - CARD_PAD - 1 * mm, y + 1.8 * mm, "correction")
    c.setFillColor(black)

    zone_geo = {"x_pt": x, "y_pt": zone_y, "w_pt": w, "h_pt": zone_h}
    meta["correction_strip"] = {"x_pt": x + CARD_PAD, "y_pt": y + 1.2 * mm,
                                "w_pt": w - 2 * CARD_PAD, "h_pt": STRIP_H - 2 * mm}
    return card_h, zone_geo, meta


def _lesson_layout(blocks: dict, width: float, fs: float) -> dict:
    """Met en page un rappel structuré v3. Retourne {parts, height}.
    parts = liste de (type, layout|image, extra) empilés verticalement."""
    inner = width - 2 * CARD_PAD
    parts: list[tuple] = []
    height = 0.0

    def _push(kind, text, indent=0.0, gap=1.2 * mm, font_fs=fs):
        nonlocal height
        lay = _rich_layout(text, inner - indent, font_fs)
        parts.append((kind, lay, indent, font_fs, gap))
        height += lay["height"] + gap

    if blocks.get("essentiel"):
        _push("essentiel", blocks["essentiel"])
    for i, step in enumerate(blocks.get("methode") or []):
        _push("methode", f"{i + 1}. {step}", indent=1.5 * mm, gap=0.6 * mm)
    ex = blocks.get("exemple") or {}
    if ex.get("enonce"):
        height += 1.6 * mm  # respiration avant l'encadré exemple
        parts.append(("exemple_start", None, 0.0, fs, 0.0))
        _push("exemple", "Exemple : " + ex["enonce"], indent=2 * mm)
        for step in ex.get("etapes") or []:
            _push("exemple", step, indent=4 * mm, gap=0.6 * mm)
        if ex.get("resultat"):
            _push("exemple", ex["resultat"], indent=2 * mm)
        parts.append(("exemple_end", None, 0.0, fs, 0.0))
        height += 2.2 * mm
    if blocks.get("astuce"):
        _push("astuce", "Astuce : " + blocks["astuce"], gap=0.6 * mm)
    figure = _figure_image(blocks.get("figure"), min(inner, 55 * mm), 32 * mm)
    if figure:
        parts.append(("figure", figure, 0.0, fs, 1.5 * mm))
        height += figure[2] + 1.5 * mm
    return {"parts": parts, "height": height}


def _lesson_card_h(layout: dict, tpl: dict) -> float:
    return 5 * mm + layout["height"] + 2.5 * CARD_PAD


def _draw_lesson_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                      title: str, layout: dict, tpl: dict) -> float:
    """Cadre rappel de leçon structuré : fond ambre, icône livre, l'essentiel,
    méthode numérotée, exemple résolu encadré, astuce, figure éventuelle."""
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
    _icon_book(c, x + CARD_PAD + 1.6 * mm, ty + 0.6 * mm)
    c.setFillColor(text_color)
    c.setFont("Helvetica-Bold", fs)
    c.drawString(x + CARD_PAD + 4.4 * mm, ty + 0.8 * mm, _pdf_safe(title)[:80])

    inner = w - 2 * CARD_PAD
    line_y = ty - 1.2 * mm
    example_top = None
    for kind, payload, indent, part_fs, part_gap in layout["parts"]:
        if kind == "exemple_start":
            line_y -= 1.6 * mm
            example_top = line_y
            continue
        if kind == "exemple_end":
            # encadré blanc translucide derrière l'exemple, redessiné dessous :
            # on trace seulement un filet vertical discret à gauche
            c.setStrokeColor(border)
            c.setLineWidth(1.4)
            c.line(x + CARD_PAD + 0.4 * mm, line_y + 0.6 * mm,
                   x + CARD_PAD + 0.4 * mm, example_top - 0.4 * mm)
            line_y -= 2.2 * mm
            example_top = None
            continue
        if kind == "figure":
            fimg, fw, fh = payload
            c.drawImage(fimg, x + (w - fw) / 2, line_y - fh, width=fw, height=fh,
                        mask="auto", preserveAspectRatio=True)
            line_y -= fh + 1.5 * mm
            continue
        font = "Helvetica-Oblique" if kind in ("essentiel", "astuce") else "Helvetica"
        _draw_rich(c, x + CARD_PAD + indent, line_y, payload, part_fs,
                   color=text_color, font=font)
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
    zone_h = _zone_height(item["response_type"], choices, COL_W, font_size)
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
        zone_h = _zone_height(item["response_type"], choices, col_w, font_size)
        card_h = _exercise_card_h(layout, zone_h, ex_tpl)
        place(card_h + gap)
        x = MARGIN + col * (col_w + COL_GAP)

        _, zone_geo, meta = _draw_exercise_card(
            pdf_canvas, x, y_cursor, col_w, seq, layout, zone_h,
            item.get("level5", 3), item["response_type"], choices, ex_tpl)
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

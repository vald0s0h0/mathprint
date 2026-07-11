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
from datetime import date

import cv2
import numpy as np
import qrcode
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from ..config import settings

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
RADIUS = 2.2 * mm

# En-tête en deux colonnes (libre de contrainte CV, alignée aux marges/QR) :
# gauche = Note (haute, calée sur le fiduciel TL) + Appréciation (grande,
# en dessous) ; droite = identité élève (haute, calée sur le QR) + méta sujet.
HEADER_MID = PAGE_W / 2
NOTE_W, NOTE_H = 22 * mm, 15 * mm
NOTE_BOX = (MARGIN + QR_MINI + 4 * mm, PAGE_H - MARGIN - NOTE_H, NOTE_W, NOTE_H)  # x, y, w, h
_comment_top = NOTE_BOX[1] - 3 * mm
_comment_bottom = PAGE_H - MARGIN - HEADER_H + 2 * mm
COMMENT_BAND = (MARGIN, _comment_bottom,
                HEADER_MID - MARGIN - 3 * mm, _comment_top - _comment_bottom)


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

def _icon_pencil(c: canvas.Canvas, x: float, y: float, size: float = 3.2 * mm):
    """Petit crayon vectoriel (corps incliné + pointe)."""
    c.saveState()
    c.translate(x, y)
    c.rotate(45)
    c.setFillColor(DOT_ON)
    c.setStrokeColor(DOT_ON)
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


def _difficulty_dots(c: canvas.Canvas, x_right: float, y: float, level5: int):
    """5 pastilles, remplies jusqu'au niveau de difficulté."""
    r = 0.85 * mm
    for i in range(5):
        cx = x_right - (4 - i) * 2.6 * mm
        c.setFillColor(DOT_ON if i < level5 else DOT_OFF)
        c.circle(cx, y, r, stroke=0, fill=1)


def _dotted(c: canvas.Canvas):
    c.setDash(1.6, 1.8)
    c.setStrokeColor(DOTTED_GRAY)
    c.setLineWidth(0.7)


def _solid(c: canvas.Canvas):
    c.setDash()


# ------------------------------------------------------------------- en-tête

def _draw_header(c: canvas.Canvas, student_name: str, class_name: str, title: str,
                 assessment_type: str, the_date: str):
    y_top = PAGE_H - MARGIN
    header_bottom = y_top - HEADER_H
    label = "Contrôle" if assessment_type == "control" else "Entraînement"

    # --- colonne droite : identité élève (haute, grosse) puis méta (basse, petite) ---
    id_right = PAGE_W - MARGIN - QR_MAIN - 4 * mm  # calé sur le bord gauche du QR
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(id_right, y_top - 6.5 * mm, student_name)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(HexColor("#455A64"))
    c.drawRightString(id_right, y_top - 12 * mm, f"Classe {class_name}")

    meta_y = y_top - QR_MAIN - 4 * mm
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(TITLE_RULE)
    c.drawRightString(PAGE_W - MARGIN, meta_y, title)
    c.setFont("Helvetica", 7)
    c.setFillColor(HexColor("#6A737C"))
    c.drawRightString(PAGE_W - MARGIN, meta_y - 4 * mm, f"{label}  ·  {the_date}")

    # --- colonne gauche : Note (calée sur le fiduciel TL) + Appréciation (grande) ---
    nx, ny, nw, nh = NOTE_BOX
    _dotted(c)
    c.roundRect(nx, ny, nw, nh, 2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(nx + nw / 2, ny + nh + 1.2 * mm, "NOTE")
    c.setFillColor(black)

    bx, by, bw, bh = COMMENT_BAND
    _dotted(c)
    c.roundRect(bx, by, bw, bh, 2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 6.5)
    c.drawString(bx + 2 * mm, by + bh - 4.5 * mm,
                 "APPRÉCIATION — remplie à la correction")
    c.setFillColor(black)

    # filet séparateur en-tête / exercices
    c.setStrokeColor(TITLE_RULE)
    c.setLineWidth(1.1)
    c.line(MARGIN, header_bottom, PAGE_W - MARGIN, header_bottom)


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


def _qcm_layout(choices: list[str], width: float, font_size: int) -> list[dict]:
    """Disposition compacte : cases en ligne, retour à la ligne auto.
    Retourne [{index, dx, dy, label}] en coordonnées relatives (origine haut-gauche)."""
    box = 4.0 * mm
    gap_x, row_h = 4.5 * mm, 6.0 * mm
    x, row = 0.0, 0
    out = []
    for i, choice in enumerate(choices):
        text_w = len(choice) * font_size * 0.52 + 2 * mm
        item_w = box + 1.6 * mm + text_w + gap_x
        if x + item_w > width and x > 0:
            x, row = 0.0, row + 1
        out.append({"index": i, "dx": x, "dy": row * row_h, "label": choice,
                    "box": box})
        x += item_w
    return out


def _zone_height(response_type: str, choices: list[str], width: float,
                 font_size: int) -> float:
    if response_type in ("qcm_single", "qcm_multiple"):
        layout = _qcm_layout(choices, width, font_size)
        rows = (max(it["dy"] for it in layout) / (6.0 * mm) + 1) if layout else 1
        return rows * 6.0 * mm + 2.5 * mm
    return {"short_text": 10 * mm, "multiline_text": 34 * mm}[response_type]


def _draw_answer_zone(c: canvas.Canvas, x: float, y: float, w: float, h: float,
                      response_type: str, choices: list[str], font_size: int) -> dict:
    """Zone de réponse ÉLÈVE en saumon (dropout). Retourne la méta QCM."""
    meta = {}
    c.setStrokeColor(DROPOUT)
    c.setLineWidth(0.9)
    if response_type in ("qcm_single", "qcm_multiple"):
        layout = _qcm_layout(choices, w - 2 * CARD_PAD, font_size)
        boxes = []
        top = y + h - 2 * mm
        for it in layout:
            bx = x + CARD_PAD + it["dx"]
            by = top - it["dy"] - it["box"]
            c.setStrokeColor(DROPOUT)
            c.rect(bx, by, it["box"], it["box"])
            c.setFillColor(black)
            c.setFont("Helvetica", font_size)
            c.drawString(bx + it["box"] + 1.6 * mm, by + 0.8 * mm, it["label"])
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


def _draw_exercise_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                        seq: int, statement_lines: list[str], zone_h: float,
                        level5: int, response_type: str, choices: list[str],
                        font_size: int) -> tuple[float, dict, dict]:
    """Carte exercice complète. Retourne (hauteur, geo zone réponse, meta)."""
    head_h = 5.2 * mm
    text_h = len(statement_lines) * (font_size + 2.6)
    card_h = head_h + text_h + zone_h + STRIP_H + 3 * CARD_PAD
    y = y_top - card_h

    # ombre puis carte
    c.setFillColor(CARD_SHADOW)
    c.roundRect(x + 1.1, y - 1.3, w, card_h, RADIUS, stroke=0, fill=1)
    c.setFillColor(white)
    c.setStrokeColor(CARD_BORDER)
    c.setLineWidth(0.9)
    c.roundRect(x, y, w, card_h, RADIUS, stroke=1, fill=1)

    # ligne de titre : icône + "Exercice N" + pastilles difficulté
    ty = y + card_h - head_h
    _icon_pencil(c, x + CARD_PAD + 1.2 * mm, ty + 1.2 * mm)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", font_size)
    c.drawString(x + CARD_PAD + 3.6 * mm, ty + 0.8 * mm, f"Exercice {seq}")
    _difficulty_dots(c, x + w - CARD_PAD - 1 * mm, ty + 1.8 * mm, level5)
    c.setStrokeColor(HexColor("#E4E8EC"))
    c.setLineWidth(0.5)
    c.line(x + CARD_PAD, ty - 0.6 * mm, x + w - CARD_PAD, ty - 0.6 * mm)

    # énoncé
    c.setFont("Helvetica", font_size)
    c.setFillColor(black)
    line_y = ty - 1.4 * mm - font_size
    for line in statement_lines:
        c.drawString(x + CARD_PAD, line_y, line)
        line_y -= font_size + 2.6

    # zone réponse élève (saumon)
    zone_y = y + STRIP_H + CARD_PAD
    meta = _draw_answer_zone(c, x, zone_y, w, zone_h, response_type, choices, font_size)

    # bande correction — POINTILLÉS, réservée à l'overlay
    _dotted(c)
    c.roundRect(x + CARD_PAD, y + 1.2 * mm, w - 2 * CARD_PAD, STRIP_H - 2 * mm, 1.2 * mm)
    _solid(c)
    c.setFillColor(DOTTED_GRAY)
    c.setFont("Helvetica", 5)
    c.drawRightString(x + w - CARD_PAD - 1 * mm, y + 1.8 * mm, "correction")
    c.setFillColor(black)

    zone_geo = {"x_pt": x, "y_pt": zone_y, "w_pt": w, "h_pt": zone_h}
    meta["correction_strip"] = {"x_pt": x + CARD_PAD, "y_pt": y + 1.2 * mm,
                                "w_pt": w - 2 * CARD_PAD, "h_pt": STRIP_H - 2 * mm}
    return card_h, zone_geo, meta


def _draw_lesson_card(c: canvas.Canvas, x: float, y_top: float, w: float,
                      title: str, content: str, example: str,
                      font_size: int) -> float:
    """Cadre rappel de leçon : fond ambre clair + icône livre, sans zone réponse."""
    fs = max(7, font_size - 1)
    content_lines = _wrap(content, w - 2 * CARD_PAD, fs)
    example_lines = _wrap(example, w - 2 * CARD_PAD, fs) if example else []
    head_h = 5 * mm
    body_h = (len(content_lines) + len(example_lines)) * (fs + 2.4)
    card_h = head_h + body_h + 2.5 * CARD_PAD
    y = y_top - card_h

    c.setFillColor(LESSON_BG)
    c.setStrokeColor(LESSON_BORDER)
    c.setLineWidth(0.9)
    c.roundRect(x, y, w, card_h, RADIUS, stroke=1, fill=1)

    ty = y + card_h - head_h
    _icon_book(c, x + CARD_PAD + 1.6 * mm, ty + 0.6 * mm)
    c.setFillColor(LESSON_TEXT)
    c.setFont("Helvetica-Bold", fs)
    c.drawString(x + CARD_PAD + 4.4 * mm, ty + 0.8 * mm, title[:80])

    c.setFont("Helvetica-Oblique", fs)
    line_y = ty - 1.2 * mm - fs
    for line in content_lines:
        c.drawString(x + CARD_PAD, line_y, line)
        line_y -= fs + 2.4
    c.setFont("Helvetica", fs)
    for line in example_lines:
        c.drawString(x + CARD_PAD, line_y, line)
        line_y -= fs + 2.4
    c.setFillColor(black)
    return card_h


# ------------------------------------------------------------- copie entière

def render_copy(pdf_canvas: canvas.Canvas, *, student_name: str, class_name: str,
                title: str, assessment_type: str, items: list[dict],
                pages_meta: list[dict], font_size: int = 9) -> list[dict]:
    """Dessine une copie complète. `items` : dicts avec kind=exercise
    (item_id, statement, response_type, choices, level5) ou kind=lesson
    (title, content, example). Retourne les zones pour le manifeste."""
    zones = []
    col_w = (PAGE_W - 2 * MARGIN - COL_GAP) / 2
    today = date.today().strftime("%d/%m/%Y")

    page_idx = 0
    col = 0
    y_cursor = PAGE_H - MARGIN - HEADER_H - 4 * mm
    bottom_limit = MARGIN + QR_MINI + 3 * mm
    gap = 3.5 * mm

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
    _draw_header(pdf_canvas, student_name, class_name, title, assessment_type, today)

    seq = 0
    for item in items:
        x = MARGIN + col * (col_w + COL_GAP)
        if item.get("kind") == "lesson":
            fs = max(7, font_size - 1)
            n_lines = len(_wrap(item.get("content", ""), col_w - 2 * CARD_PAD, fs)) + \
                len(_wrap(item.get("example", ""), col_w - 2 * CARD_PAD, fs))
            est_h = 5 * mm + n_lines * (fs + 2.4) + 2.5 * CARD_PAD
            place(est_h + gap)
            x = MARGIN + col * (col_w + COL_GAP)
            used = _draw_lesson_card(pdf_canvas, x, y_cursor, col_w,
                                     item.get("title", "Rappel"),
                                     item.get("content", ""),
                                     item.get("example", ""), font_size)
            y_cursor -= used + gap
            continue

        seq += 1
        choices = item.get("choices", [])
        stmt_lines = _wrap(item["statement"], col_w - 2 * CARD_PAD, font_size)
        zone_h = _zone_height(item["response_type"], choices, col_w, font_size)
        card_h = 5.2 * mm + len(stmt_lines) * (font_size + 2.6) + zone_h + STRIP_H + 3 * CARD_PAD
        place(card_h + gap)
        x = MARGIN + col * (col_w + COL_GAP)

        _, zone_geo, meta = _draw_exercise_card(
            pdf_canvas, x, y_cursor, col_w, seq, stmt_lines, zone_h,
            item.get("level5", 3), item["response_type"], choices, font_size)
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


def render_overlay(path: str, *, copies_annotations: list[dict],
                   color: str | None = None):
    """Overlay de correction (§5.6) : pages blanches, annotations seules,
    calées sur les zones pointillées (case Note, bande appréciation, bandes
    de correction sous chaque exercice)."""
    col = HexColor(color or settings.correction_color)
    c = canvas.Canvas(path, pagesize=A4)
    for page in copies_annotations:
        c.setFillColor(col)
        c.setStrokeColor(col)
        # nom de l'élève sous le QR : l'élève vérifie que la correction est la sienne
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - QR_MAIN - 4 * mm,
                          f"Correction — {page.get('student', '')}")
        if page.get("note") is not None:
            nx, ny, nw, nh = NOTE_BOX
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(nx + nw / 2, ny + nh / 2 - 2 * mm, str(page["note"]))
        if page.get("comment"):
            bx, by, bw, bh = COMMENT_BAND
            c.setFont("Helvetica", 8)
            for i, line in enumerate(_wrap(page["comment"], bw - 4 * mm, 8)[:2]):
                c.drawString(bx + 2 * mm, by + bh - (i + 1) * 3.6 * mm, line)
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

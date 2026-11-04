"""Aperçu écran issu du moteur PDF : aucune seconde mise en page HTML."""
from __future__ import annotations

import io
import json
from pathlib import Path
from functools import lru_cache
from threading import RLock

import fitz
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

from . import blocks, generation, handwriting, pdfgen

_LOCK = RLock()


def shape(ex: dict, guides: bool = True) -> dict:
    expected = ex.get('expected') or {}
    grading = {**{k: v for k, v in expected.items() if k in
                 ('left', 'right', 'pairs', 'cols', 'rows', 'cells')}, **(ex.get('grading') or {})}
    if ex.get('response_type') == 'composite':
        grading['parts'] = [_legible_part(p) for p in
                            expected.get('parts') or grading.get('parts') or []]
    elif ex.get('response_type') == 'matching':
        # même ordre que la copie imprimée (generation.render_shape)
        grading, expected = generation.legible_matching(grading, expected)
    statement = ex.get('statement') or ''
    problem = ex.get('badge_type') in ('probleme', 'enigme') or ex.get('kind') == 'probleme' or ex.get('is_problem', False)
    if problem and ex.get('title'):
        statement = f"**{blocks.strip_bold(ex['title']).strip()}**\n{statement}"
    return {'kind': 'exercise', 'statement': statement, 'correction': '',
            'response_type': ex['response_type'], 'choices': ex.get('choices') or grading.get('choices') or [],
            'grading': grading, 'expected': expected, 'figure': ex.get('figure'),
            'is_probleme': problem,
            'level3': ex.get('difficulty') or ex.get('level') or 2,
            'guides': pdfgen.GUIDES_INCLUDE if guides else pdfgen.GUIDES_NONE,
            'inline': bool(expected.get('inline')),
            'figure_layout': grading.get('figure_layout'),
            'figure_scale': grading.get('figure_scale')}


def _legible_part(part: dict) -> dict:
    if part.get('response_type') != 'matching':
        return part
    grading, expected = generation.legible_matching(part.get('grading') or {},
                                                    part.get('expected') or {})
    return {**part, 'grading': grading, 'expected': expected}


def _answers(c, meta: dict, expected: dict, rtype: str, zone: dict,
             grading: dict, inline: bool = False):
    """Réponses de relecture, sans changer un seul placement du sujet."""
    c.saveState()
    c.setStrokeColor(_PEN)
    c.setFillColor(_PEN)
    c.setLineCap(1)
    c.setLineJoin(1)
    c.setLineWidth(2.6)
    for box in meta.get('boxes', []):
        yes = box['index'] in expected.get('correct', [])
        if rtype == 'checkbox_grid':
            rows = expected.get('rows') or []
            yes = box['row'] < len(rows) and rows[box['row']].get('correct') == box['col']
        if yes:
            x, y, w, h = [box[k] for k in ('x_pt', 'y_pt', 'w_pt', 'h_pt')]
            path = c.beginPath()
            path.moveTo(x + w * .08, y + h * .5)
            path.lineTo(x + w * .4, y + h * .18)
            path.lineTo(x + w * .96, y + h * .94)
            c.drawPath(path)
    if rtype == 'matching':
        c.setLineWidth(1.5)
        left, right = meta.get('left_points') or [], meta.get('right_points') or []
        for i, j in expected.get('pairs', []):
            if not (0 <= i < len(left) and 0 <= j < len(right)):
                continue
            p, q = left[i], right[j]
            c.line(p['x_pt'] + p['w_pt'] / 2, p['y_pt'] + p['h_pt'] / 2,
                   q['x_pt'] + q['w_pt'] / 2, q['y_pt'] + q['h_pt'] / 2)
    font, size = _hand_font()
    for field in _fields(rtype, zone, meta, expected, grading, inline):
        _pen_value(c, field['values'][0], field['rect'], font, size, _PEN)
    if rtype == 'multiline_text':
        _check_multiline(c, zone, expected, grading, font, size, color=_PEN)
    c.restoreState()


def render(ex: dict, guides: bool = True, show_answers: bool = False) -> bytes:
    # Matplotlib n'est pas thread-safe. Le cache évite le travail à chaque scroll.
    with _LOCK:
        return _render(json.dumps(ex, sort_keys=True, ensure_ascii=False), guides, show_answers)


@lru_cache(maxsize=96)
def _render(key: str, guides: bool, show_answers: bool) -> bytes:
    ex = json.loads(key)
    item = shape(ex, guides)
    tpl = pdfgen.DEFAULT_TEMPLATES['exercise']
    fs = float(tpl.get('font_size', 9))
    composite = item['response_type'] == 'composite'
    if composite:
        cl = pdfgen._composite_layout(item, fs, fs)
        height = pdfgen._composite_card_h(cl)
    else:
        layout, zone_fs, zone_h, strip = pdfgen._exercise_layout(item, fs, fs)
        height = pdfgen._exercise_card_h(layout, zone_h, strip['height'], tpl)
    if height > 5000:
        raise ValueError('Carte trop longue')
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdfgen.COL_W + 4, height + 4))
    if composite:
        _, zones = pdfgen._draw_composite_card(c, 2, height + 2, pdfgen.COL_W,
                                               1, cl, item, tpl, fs)
        if show_answers:
            for zone, part in zip(zones, cl['parts']):
                _answers(c, zone['meta'], part['expected'], part['response_type'],
                         zone['zone_geo'], part['grading'])
    else:
        _, zone, meta = pdfgen._draw_exercise_card(c, 2, height + 2, pdfgen.COL_W, 1,
            layout, zone_h, strip, item['level3'], item['response_type'], item['choices'],
            tpl, fs, zone_fs, item['grading'], item['is_probleme'])
        if show_answers:
            _answers(c, meta, item['expected'], item['response_type'],
                     zone, item['grading'], item['inline'])
    c.save()
    with fitz.open(stream=buf.getvalue(), filetype='pdf') as doc:
        # Retire seulement la bande invisible de correction, jamais le contenu.
        strip_h = cl['strip']['height'] if composite else strip['height']
        clip = fitz.Rect(0, 0, pdfgen.COL_W + 4, height + 4 - strip_h - pdfgen.STRIP_GAP)
        return doc[0].get_pixmap(dpi=180, clip=clip).tobytes('png')


# ------------------------------------------------------------- écriture simulée
# L'aperçu « écrit » montre la carte remplie au stylo, à la taille d'une vraie écriture
# de collégien : chaque caractère est posé sur son pas de handwriting.py, donc ce qu'on
# voit est exactement ce que le contrôle a mesuré. La police ne donne que l'allure.
_HAND_FONTS = (str(Path(__file__).resolve().parents[1] / "assets/fonts/PatrickHand-Regular.ttf"),
               "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
               "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf")
_PEN = HexColor('#3288D8')
_FAIL = HexColor('#D21F3C')


def _hand_font() -> tuple[str, float]:
    """(police, corps) tel qu'un chiffre mesure sa hauteur manuscrite."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in _HAND_FONTS:
        try:
            if 'ForgeHand' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('ForgeHand', path))
            cap = getattr(pdfmetrics.getFont('ForgeHand').face, 'capHeight', 0) or 700
            return 'ForgeHand', handwriting.DIGIT_H / (cap / 1000)
        except Exception:  # noqa: BLE001 - police absente : on essaie la suivante
            continue
    return 'Helvetica-Oblique', handwriting.DIGIT_H / 0.72


def _pen_line(c, text: str, cx: float, baseline: float, font: str, size: float,
              left: float | None = None) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    written = handwriting._written_w(text)
    x = left if left is not None else cx - written / 2
    c.setFont(font, size)
    for k, char in enumerate(text):
        if char == '\u202f':
            x += handwriting.THOUSANDS_GAP
            continue
        step = handwriting.char_w(char)
        if char != ' ':
            glyph = stringWidth(char, font, size) or step
            squeeze = min(1.0, step * 0.95 / glyph)
            c.saveState()
            # léger désalignement : personne n'écrit parfaitement droit
            c.translate(x + (step - glyph * squeeze) / 2,
                        baseline + (hash((char, k)) % 3 - 1) * 0.12 * handwriting.MM)
            c.scale(squeeze, 1)
            c.drawString(0, 0, char)
            c.restoreState()
        x += step


def _pen_value(c, value, rect: dict, font: str, size: float, color) -> None:
    written = handwriting.forms(value)
    cx = rect['x_pt'] + rect['w_pt'] / 2
    cy = rect['y_pt'] + rect['h_pt'] / 2
    c.setFillColor(color)
    c.setStrokeColor(color)
    if len(written) == 2:
        half = handwriting.FRACTION_BAR / 2
        bar = max(handwriting._written_w(written[0]), handwriting._written_w(written[1])) / 2
        _pen_line(c, written[0], cx, cy + half, font, size)
        c.setLineWidth(0.8)
        c.line(cx - bar, cy, cx + bar, cy)
        _pen_line(c, written[1], cx, cy - half - handwriting.DIGIT_H, font, size)
        return
    lines = handwriting.wrap(written[0], rect['w_pt'] - 2 * handwriting.MARGIN_X)
    block = handwriting.DIGIT_H + (len(lines) - 1) * handwriting.LINE_H
    base = cy + block / 2 - handwriting.DIGIT_H
    for k, line in enumerate(lines):
        _pen_line(c, line, cx, base - k * handwriting.LINE_H, font, size)


def _wrong_values(grading: dict, cell: tuple[int, int] | None) -> list:
    """Réponses fausses prévues : l'élève peut les écrire, elles doivent tenir aussi."""
    out = []
    for wrong in (grading.get('feedback') or {}).get('wrong_answers') or []:
        target = wrong.get('cell')
        if cell is None or target is None or tuple(target) == cell:
            out.append(wrong.get('value'))
    return [v for v in out if v not in (None, '')]


def _fields(rtype: str, zone: dict, meta: dict, expected: dict, grading: dict, inline: bool) -> list[dict]:
    """Chaque endroit où l'élève écrit, avec ce qu'il peut y écrire."""
    fields = []
    if rtype == 'short_text':
        rect = dict(zone)
        if not inline:
            rect = {'x_pt': zone['x_pt'] + pdfgen.CARD_PAD, 'y_pt': zone['y_pt'] + pdfgen.mm,
                    'w_pt': zone['w_pt'] - 2 * pdfgen.CARD_PAD, 'h_pt': zone['h_pt'] - 2 * pdfgen.mm}
        fields.append({'name': 'réponse', 'rect': rect,
                       'values': [expected, *_wrong_values(grading, None)]})
    elif rtype in ('multi_blank', 'table_fill'):
        cells = expected.get('cells') or []
        for i, row in enumerate(meta.get('cells') or []):
            for j, rect in enumerate(row):
                cell = cells[i][j] if i < len(cells) and j < len(cells[i]) else None
                if rect and cell and not cell.get('given'):
                    fields.append({'name': f'case {i + 1},{j + 1}', 'rect': rect,
                                   'values': [cell, *_wrong_values(grading, (i, j))]})
    return fields


def _check_field(c, field: dict, font: str, size: float) -> dict | None:
    rect = field['rect']
    worst, worst_over = None, -1e9
    problem = None
    for value in field['values']:
        need_w, need_h = handwriting.need(value, rect['w_pt'])
        over = max(need_w - rect['w_pt'], need_h - rect['h_pt'])
        if over > worst_over:
            worst, worst_over = value, over
        if over > 0.01 and problem is None:
            problem = (f"{field['name']} : « {handwriting.label(value)} » demande "
                       f"{handwriting.mm(need_w)} × {handwriting.mm(need_h)} mm, la case offre "
                       f"{handwriting.mm(rect['w_pt'])} × {handwriting.mm(rect['h_pt'])} mm")
    _pen_value(c, worst, rect, font, size, _FAIL if problem else _PEN)
    return {'name': field['name'], 'problem': problem}


def _check_multiline(c, zone: dict, expected: dict, grading: dict, font: str, size: float,
                     color=None) -> dict:
    """Rédaction : le corrigé entre-t-il dans les lignes tracées ?"""
    steps = [s.get('expected_text') or '' for s in expected.get('steps') or []]
    rows = max(3, min(12, int(grading.get('lines', 5))))
    left = zone['x_pt'] + pdfgen.CARD_PAD + 2 * pdfgen.mm
    width = zone['w_pt'] - 2 * pdfgen.CARD_PAD - 4 * pdfgen.mm
    written = [ln for step in steps if str(step).strip()
               for ln in handwriting.wrap(handwriting.plain(step), width)]
    top = zone['y_pt'] + zone['h_pt'] - pdfgen.mm
    over = len(written) > rows
    c.setFillColor(color or (_FAIL if over else _PEN))
    for k, line in enumerate(written[:rows]):
        _pen_line(c, line, 0, top - (k + 1) * pdfgen.MULTILINE_ROW_H + 1.0 * pdfgen.mm, font, size,
                  left=left)
    return {'name': 'rédaction',
            'problem': (f"rédaction : le corrigé demande {len(written)} ligne(s) d'écriture, "
                        f"il y en a {rows}") if over else None}


def written_preview(ex: dict) -> tuple[bytes, list[dict]]:
    """(PNG de la carte remplie au stylo, contrôle de chaque zone d'écriture). Une
    réponse qui ne tient pas s'imprime en rouge et porte un `problem`."""
    with _LOCK:
        item = shape(ex)
        tpl = pdfgen.DEFAULT_TEMPLATES['exercise']
        fs = float(tpl.get('font_size', 9))
        composite = item['response_type'] == 'composite'
        if composite:
            cl = pdfgen._composite_layout(item, fs, fs)
            height = pdfgen._composite_card_h(cl)
        else:
            layout, zone_fs, zone_h, strip = pdfgen._exercise_layout(item, fs, fs)
            height = pdfgen._exercise_card_h(layout, zone_h, strip['height'], tpl)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pdfgen.COL_W + 4, height + 4))
        if composite:
            _, zones = pdfgen._draw_composite_card(c, 2, height + 2, pdfgen.COL_W, 1, cl, item, tpl, fs)
            parts = [(z['response_type'], z['zone_geo'], z['meta'], p['expected'], p['grading'], False)
                     for z, p in zip(zones, cl['parts'])]
            strip_h = cl['strip']['height']
        else:
            _, zone, meta = pdfgen._draw_exercise_card(
                c, 2, height + 2, pdfgen.COL_W, 1, layout, zone_h, strip, item['level3'],
                item['response_type'], item['choices'], tpl, fs, zone_fs, item['grading'],
                item['is_probleme'])
            parts = [(item['response_type'], zone, meta, item['expected'], item['grading'], item['inline'])]
            strip_h = strip['height']
        font, size = _hand_font()
        results = []
        for rtype, part_zone, part_meta, expected, grading, inline in parts:
            if rtype == 'multiline_text':
                results.append(_check_multiline(c, part_zone, expected, grading, font, size))
            for field in _fields(rtype, part_zone, part_meta, expected, grading, inline):
                results.append(_check_field(c, field, font, size))
        c.save()
        with fitz.open(stream=buf.getvalue(), filetype='pdf') as doc:
            clip = fitz.Rect(0, 0, pdfgen.COL_W + 4, height + 4 - strip_h - pdfgen.STRIP_GAP)
            return doc[0].get_pixmap(dpi=180, clip=clip).tobytes('png'), results

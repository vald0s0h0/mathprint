"""Aperçu écran issu du moteur PDF : aucune seconde mise en page HTML."""
from __future__ import annotations

import io
import json
from functools import lru_cache
from threading import RLock

import fitz
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

from . import pdfgen

_LOCK = RLock()


def shape(ex: dict, guides: bool = True) -> dict:
    expected = ex.get('expected') or {}
    grading = {**{k: v for k, v in expected.items() if k in
                 ('left', 'right', 'pairs', 'cols', 'rows', 'cells')}, **(ex.get('grading') or {})}
    if ex.get('response_type') == 'composite':
        grading['parts'] = expected.get('parts') or grading.get('parts') or []
    statement = ex.get('statement') or ''
    problem = ex.get('badge_type') in ('probleme', 'enigme') or ex.get('kind') == 'probleme' or ex.get('is_problem', False)
    if problem and ex.get('title'):
        prefix = 'Énigme' if ex.get('badge_type') == 'enigme' else 'Problème'
        statement = f"{prefix} — {ex['title']}\n{statement}"
    return {'kind': 'exercise', 'statement': statement, 'correction': '',
            'response_type': ex['response_type'], 'choices': ex.get('choices') or grading.get('choices') or [],
            'grading': grading, 'expected': expected, 'figure': ex.get('figure'),
            'calc': ex.get('calculator', 'autorisee'), 'is_probleme': problem,
            'level3': ex.get('difficulty') or ex.get('level') or 2,
            'guides': pdfgen.GUIDES_INCLUDE if guides else pdfgen.GUIDES_NONE,
            'inline': bool(expected.get('inline'))}


def _answers(c, meta: dict, expected: dict, rtype: str):
    c.setStrokeColor(HexColor('#16803C'))
    c.setFillColor(HexColor('#16803C'))
    c.setLineWidth(1)
    for box in meta.get('boxes', []):
        yes = box['index'] in expected.get('correct', [])
        if rtype == 'checkbox_grid':
            rows = expected.get('rows') or []
            yes = box['row'] < len(rows) and rows[box['row']].get('correct') == box['col']
        if yes:
            x, y, w, h = [box[k] for k in ('x_pt', 'y_pt', 'w_pt', 'h_pt')]
            c.line(x, y + h * .5, x + w * .4, y + h * .15)
            c.line(x + w * .4, y + h * .15, x + w, y + h)
    if rtype == 'matching':
        for i, j in expected.get('pairs', []):
            for side, index in (('left_points', i), ('right_points', j)):
                points = meta.get(side) or []
                if index >= len(points):
                    continue
                p = points[index]
                c.setFont('Helvetica', 5)
                c.drawCentredString(p['x_pt'] + p['w_pt'] / 2, p['y_pt'] + 1, str(i + 1))


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
                _answers(c, zone['meta'], part['expected'], part['response_type'])
    else:
        _, _, meta = pdfgen._draw_exercise_card(c, 2, height + 2, pdfgen.COL_W, 1,
            layout, zone_h, strip, item['level3'], item['response_type'], item['choices'],
            tpl, fs, zone_fs, item['grading'], item['calc'], item['is_probleme'])
        if show_answers:
            _answers(c, meta, item['expected'], item['response_type'])
    c.save()
    with fitz.open(stream=buf.getvalue(), filetype='pdf') as doc:
        # Retire seulement la bande invisible de correction, jamais le contenu.
        strip_h = cl['strip']['height'] if composite else strip['height']
        clip = fitz.Rect(0, 0, pdfgen.COL_W + 4, height + 4 - strip_h - pdfgen.STRIP_GAP)
        return doc[0].get_pixmap(dpi=180, clip=clip).tobytes('png')

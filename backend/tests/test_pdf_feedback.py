"""Optional personalized feedback uses measured native geometry."""
import io
import sys
from pathlib import Path

import fitz
import pytest
from reportlab.pdfgen import canvas
from reportlab.lib.colors import red

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import pdfgen as pg


def test_feedback_is_opt_in_and_reserved_before_pagination():
    old = pg._correction_strip_layout('Bank solution ignored', pg.COL_W, 9)
    assert old['height'] == pg.STRIP_MIN_H
    feedback = 'Tu as divisé par une expression qui peut être nulle. ' * 4
    measured = pg._correction_strip_layout('', pg.COL_W, 9, feedback=feedback)
    assert measured['height'] > old['height']
    c = canvas.Canvas(io.BytesIO())
    strip = pg._strip_meta(c, 30, 100, pg.COL_W, measured)
    pg._draw_correction_strip(c, {'strip':strip,'feedback':feedback,'score':1,'max_score':3}, red)
    with pytest.raises(ValueError, match='trop haut'):
        pg._draw_correction_strip(c, {'strip':strip,'feedback':feedback * 10,'score':1,'max_score':3}, red)


def test_composite_exposes_one_shared_feedback_strip_below_all_parts():
    item = {'statement':'Contexte', 'response_type':'composite','feedback':'Retour mesuré. ' * 8,
            'part_item_ids':['a','b'], 'grading':{'parts':[
                {'statement':'Question A','response_type':'short_text'},
                {'statement':'Question B','response_type':'multiline_text','grading':{'lines':3}}]}}
    cl = pg._composite_layout(item, 9, 9)
    c = canvas.Canvas(io.BytesIO())
    h,zones=pg._draw_composite_card(c,30,750,pg.COL_W,1,cl,item,pg.DEFAULT_TEMPLATES['exercise'],9)
    strip=zones[0]['meta']['group_correction_strip']
    assert 'group_correction_strip' not in zones[1]['meta']
    assert strip['y_pt']+strip['h_pt'] < min(z['zone_geo']['y_pt'] for z in zones)
    assert h+pg.GAP == pg.estimate_item_height(item,9,9,pg.DEFAULT_TEMPLATES['exercise'])


def test_geometry_overlay_draws_only_requested_segment():
    buf=io.BytesIO(); c=canvas.Canvas(buf)
    pg._draw_zone_marks(c, {'marks':{'segments':[{'x1':30,'y1':40,'x2':80,'y2':90}]}},red)
    c.save()
    with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
        paths=doc[0].get_drawings()
        assert len(paths)==1
        assert len(paths[0]['items'])==1
        assert paths[0]['items'][0][0]=='l'


def test_guides_before_answers_keep_composite_geometry():
    item = {'statement':'Contexte\n{{aide}} Repère global.', 'response_type':'composite',
            'guides_position':'before_answer','part_item_ids':['a'],
            'grading':{'parts':[{'statement':'Question A\n{{aide}} Indice local.',
                                'response_type':'short_text'}]}}
    cl = pg._composite_layout(item,9,9)
    buf=io.BytesIO(); c=canvas.Canvas(buf)
    _,zones=pg._draw_composite_card(c,30,750,pg.COL_W,1,cl,item,pg.DEFAULT_TEMPLATES['exercise'],9)
    c.save()
    with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
        p=doc[0]
        global_rect=p.search_for('Repère global')[0]
        question=p.search_for('Question A')[0]
        local=p.search_for('Indice local')[0]
        assert global_rect.y1 < question.y0
        assert question.y1 < local.y0
        assert local.y1 < p.rect.height-zones[0]['zone_geo']['y_pt']-zones[0]['zone_geo']['h_pt']
    assert cl['body_h']==pg._composite_layout({**item,'guides_position':'after_answer'},9,9)['body_h']


def test_large_appreciation_is_complete_and_within_header():
    text='Tu réussis les durées et les mètres entiers. À reprendre : fractions, décimaux et périmètre. Un repère à la fois !'
    buf=io.BytesIO();c=canvas.Canvas(buf)
    geo=pg.header_geometry('control')
    pg._draw_appreciation_content(c,geo,[],text,font_size=8.5)
    c.save()
    with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
        p=doc[0]; spans=[s for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans']]
        assert min(s['size'] for s in spans)>=7.5
        assert 'Un repère à la fois !' in ' '.join(p.get_text().split())
        g=geo['appreciation']
        for s in spans:
            x0,y0,x1,y1=s['bbox']
            assert g['x']<=x0<x1<=g['x']+g['w']
            assert p.rect.height-g['y']-g['h']<=y0<y1<=p.rect.height-g['y']

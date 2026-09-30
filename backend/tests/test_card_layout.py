"""Régressions de lecture : aides, figures, typographie et points à relier."""
import io
import sys
from pathlib import Path

import fitz
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import card_preview, pdfgen, mathrender


def leaf(statement):
    return {'response_type':'qcm_single','statement':statement,
            'expected':{'correct':[0]},'grading':{'choices':['$4$','$5$']}}


def test_guides_follow_their_response_and_global_guide_is_last():
    ex = {'response_type':'composite','statement':'{{aide}} Aide globale de méthode.',
          'expected':{'parts':[leaf('{{aide}} Aide première de méthode.\nQuestion première ?'),
                               leaf('Question seconde ?\n{{aide}} Aide seconde de méthode.')]}}
    item = card_preview.shape(ex)
    cl = pdfgen._composite_layout(item, 9, 9)
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    _, zones = pdfgen._draw_composite_card(c, 20, 800, pdfgen.COL_W, 1, cl, item,
                                          pdfgen.DEFAULT_TEMPLATES['exercise'], 9)
    c.save()
    with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
        text=doc[0].get_text()
        assert text.index('Question première') < text.index('Aide première') < text.index('Question seconde')
        assert text.index('Aide seconde') < text.index('Aide globale')
        for name, zone in zip(('Aide première','Aide seconde'),zones):
            aid=doc[0].search_for(name)[0]
            assert aid.y0 > doc[0].rect.height-zone['zone_geo']['y_pt']
    no = pdfgen._composite_layout(card_preview.shape(ex, False), 9, 9)
    assert no['tail']['height'] == 0
    assert all(p['guides']['height'] == 0 for p in no['parts'])


def test_matching_points_align_with_multiline_and_fraction_labels():
    left=['Un texte assez long pour occuper plusieurs lignes dans cette colonne',r'$\frac{3}{7}$','Court']
    right=[r'$\frac{1}{\frac{2}{3}}$','Une autre réponse longue sur plusieurs lignes','Bref']
    geo=pdfgen._matching_geometry(left,right,9,pdfgen.COL_W)
    assert geo['height'] > 3*7*pdfgen.mm
    buf=io.BytesIO();c=canvas.Canvas(buf)
    meta=pdfgen._draw_matching_zone(c,20,100,pdfgen.COL_W,geo['height'],left,right,9)
    l,r=meta['left_points'],meta['right_points']
    assert len({p['x_pt'] for p in l}) == len({p['x_pt'] for p in r}) == 1
    assert [p['y_pt'] for p in l] == [p['y_pt'] for p in r]
    assert r[0]['x_pt'] - l[0]['x_pt'] - l[0]['w_pt'] >= 14*pdfgen.mm-0.001
    assert geo['width'] <= pdfgen.COL_W-2*pdfgen.CARD_PAD


def test_fraction_style_does_not_depend_on_latex_spelling():
    a=mathrender.render_math_png(r'\frac{12}{7}',9)
    b=mathrender.render_math_png(r'\dfrac{12}{7}',9)
    assert a[1:] == b[1:]


def test_screen_uses_pdf_and_accepts_an_empty_common_statement():
    ex={'response_type':'composite','statement':'','expected':{'parts':[leaf('$2+2$'),leaf('$3+1$')]}}
    png=card_preview.render(ex)
    assert png.startswith(b'\x89PNG')
    assert card_preview.render(ex,show_answers=True) != png

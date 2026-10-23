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
    assert r[0]['x_pt'] - l[0]['x_pt'] - l[0]['w_pt'] >= pdfgen._MATCHING_LANE_MIN-0.001
    assert geo['width'] <= pdfgen.COL_W-2*pdfgen.CARD_PAD



def test_matching_label_at_its_natural_width_never_wraps():
    # « Marron en A » est le plus large de sa colonne : posé à sa largeur
    # naturelle, il passait à la ligne sur une erreur d'arrondi.
    left=['Marron en A','Violet en E','Bleu en E','Rouge en B']
    right=[r'$\widehat{AEB}$',r'$\widehat{BAE}$',r'$\widehat{ABE}$',r'$\widehat{BED}$']
    for width in (pdfgen.COL_W, 2*pdfgen.COL_W):
        geo=pdfgen._matching_geometry(left,right,9,width)
        assert all(len(lay['lines'])==1 for side in geo['layouts'] for lay in side)
        assert geo['lane'] == pdfgen._MATCHING_LANE


def test_matching_lane_narrows_before_a_label_wraps():
    left=['Une étiquette plutôt longue ici']; right=['Une autre étiquette assez longue']
    nat=sum(pdfgen._natural_w(v,9) for v in left+right)
    width=nat+2*pdfgen.CARD_PAD+2*pdfgen._MATCHING_PASTILLE+5*pdfgen.mm+10*pdfgen.mm
    geo=pdfgen._matching_geometry(left,right,9,width)
    assert pdfgen._MATCHING_LANE_MIN <= geo['lane'] < pdfgen._MATCHING_LANE
    assert all(len(lay['lines'])==1 for side in geo['layouts'] for lay in side)


def test_matching_points_are_printed_as_black_dots():
    left=['a','b']; right=['c','d']
    geo=pdfgen._matching_geometry(left,right,9,pdfgen.COL_W)
    buf=io.BytesIO();c=canvas.Canvas(buf)
    meta=pdfgen._draw_matching_zone(c,20,100,pdfgen.COL_W,geo['height'],left,right,9)
    c.save()
    with fitz.open(stream=buf.getvalue(),filetype='pdf') as doc:
        pix=doc[0].get_pixmap(dpi=200)
    zoom=200/72
    for p in meta['left_points']+meta['right_points']:
        x=(p['x_pt']+p['w_pt']/2)*zoom; y=(pdfgen.PAGE_H-p['y_pt']-p['h_pt']/2)*zoom
        assert max(pix.pixel(int(x),int(y))) < 80     # plein et noir, pas un anneau saumon


def test_matching_goes_horizontal_only_when_short_labels_make_it_more_compact():
    short_l=['$2^3$','$3^2$','$10^2$','$5^0$','$2^{-1}$','$4^1$']
    short_r=['$9$','$1$','$8$','$0{,}5$','$100$','$4$']
    geo=pdfgen._matching_layout(short_l,short_r,9,pdfgen.COL_W)
    assert geo['orientation'] == 'horizontal'
    assert geo['height'] <= 0.85*pdfgen._matching_geometry(short_l,short_r,9,pdfgen.COL_W)['height']
    # peu d'éléments : les colonnes restent plus compactes
    assert pdfgen._matching_layout(['2','3','4','5'],['4','9','16','25'],9,
                                   pdfgen.COL_W)['orientation'] == 'vertical'
    # étiquettes longues : jamais repliées pour tenir en rangée
    long_l=['Triangle rectangle','Triangle isocèle','Losange','Carré','Trapèze','Cerf-volant']
    assert pdfgen._matching_layout(long_l,short_r,9,pdfgen.COL_W)['orientation'] == 'vertical'


def test_horizontal_matching_draws_two_rows_of_black_dots():
    l=['$2^3$','$3^2$','$10^2$','$5^0$','$2^{-1}$','$4^1$']; r=['$9$','$1$','$8$','$0{,}5$','$100$']
    geo=pdfgen._matching_layout(l,r,9,pdfgen.COL_W)
    buf=io.BytesIO();c=canvas.Canvas(buf)
    meta=pdfgen._draw_matching_zone(c,20,100,pdfgen.COL_W,geo['height'],l,r,9)
    assert meta['orientation'] == 'horizontal'
    top,bottom=meta['left_points'],meta['right_points']
    assert len({p['y_pt'] for p in top}) == len({p['y_pt'] for p in bottom}) == 1
    assert top[0]['y_pt'] > bottom[0]['y_pt']
    assert [p['index'] for p in top] == list(range(6)) and len(bottom) == 5
    assert min(b['x_pt']-a['x_pt'] for a,b in zip(top,top[1:])) >= pdfgen._MATCHING_H_PITCH_MIN-0.001


def test_legible_matching_breaks_concurrent_lines_and_keeps_the_answer():
    # Banque Indigo : 0→2, 1→1, 2→0 se croisent au même point (rangées égales)
    # et 3→3 est horizontal (l'élément est en face de sa réponse).
    left=['a','b','c','d']; right=['$-3$','$196$','$120$','$20$']
    pairs=[[0,2],[1,1],[2,0],[3,3]]
    new_right,new_pairs=pdfgen.legible_matching(left,right,pairs)
    assert {(left[i],new_right[j]) for i,j in new_pairs} == {(left[i],right[j]) for i,j in pairs}
    assert all(i != j for i,j in new_pairs)
    geo=pdfgen._matching_layout(left,new_right,9,pdfgen.COL_W)
    lpos,rpos,lane=pdfgen._matching_positions(geo,None,len(left),len(new_right))
    assert pdfgen._matching_flaws(lpos,rpos,lane,[(i,j) for i,j in new_pairs]) == 0


def test_legible_matching_also_applies_to_the_horizontal_layout():
    # 0→0, 1→1, 2→2 : trois traits droits verticaux donneraient la réponse
    left=['$2^3$','$3^2$','$10^2$','$5^0$','$2^{-1}$','$4^1$']
    right=['$8$','$9$','$100$','$1$','$0{,}5$','$4$']
    pairs=[[i,i] for i in range(6)]
    new_right,new_pairs=pdfgen.legible_matching(left,right,pairs)
    assert pdfgen._matching_layout(left,new_right,9,pdfgen.COL_W)['orientation'] == 'horizontal'
    assert {(left[i],new_right[j]) for i,j in new_pairs} == {(left[i],right[j]) for i,j in pairs}
    assert sum(1 for i,j in new_pairs if i == j) <= 1


def test_legible_matching_keeps_an_already_legible_order():
    left=['a','b','c']; right=['x','y','z']; pairs=[[0,1],[1,2],[2,0]]
    assert pdfgen.legible_matching(left,right,pairs) == (right,pairs)


def test_legible_matching_reorders_the_snapshot_and_the_answer_together():
    from app.services import generation
    grading={'comparator':'matching','left':['a','b','c'],'right':['1','2','3'],
             'pairs':[[0,2],[1,1],[2,0]],'max_score':3}
    expected={'type':'matching','left':['a','b','c'],'right':['1','2','3'],
              'pairs':[[0,2],[1,1],[2,0]]}
    g,e=generation.legible_matching(grading,expected)
    assert g['right'] == e['right'] != grading['right']
    assert g['pairs'] == e['pairs']
    assert {('abc'[i],g['right'][j]) for i,j in g['pairs']} == {('a','3'),('b','2'),('c','1')}
    assert grading['right'] == ['1','2','3']            # la banque n'est pas modifiée


def test_qcm_number_list_stays_inline_next_to_its_box():
    # « 2, 4, 3, 1 » ressemble à une série de valeurs : un choix de QCM reste
    # pourtant un libellé en ligne, collé à sa case, pas une grille étalée.
    choices=['2, 4, 3, 1','4, 2, 3, 1','1, 3, 4, 2']
    items,_h,ncols=pdfgen._qcm_layout(choices,pdfgen.COL_W-2*pdfgen.CARD_PAD,9)
    assert ncols == 3
    for it in items:
        assert len(it['lay']['lines'])==1 and it['lay']['lines'][0]['segs']
        assert it['lw'] < 25*pdfgen.mm


def test_single_line_label_is_centred_on_its_box():
    lay=pdfgen._rich_layout('Angle 1',50*pdfgen.mm,9,parse_blocks=False)
    top=pdfgen._centered_top(lay,100.0)
    baseline=top-lay['lines'][0]['asc']
    assert abs(baseline+9*0.35-100.0) < 1e-6


def test_fraction_style_does_not_depend_on_latex_spelling():
    a=mathrender.render_math_png(r'\frac{12}{7}',9)
    b=mathrender.render_math_png(r'\dfrac{12}{7}',9)
    assert a[1:] == b[1:]


def test_screen_uses_pdf_and_accepts_an_empty_common_statement():
    ex={'response_type':'composite','statement':'','expected':{'parts':[leaf('$2+2$'),leaf('$3+1$')]}}
    png=card_preview.render(ex)
    assert png.startswith(b'\x89PNG')
    assert card_preview.render(ex,show_answers=True) != png

"""Régressions des défauts rencontrés pendant la reprise fr/3e/C."""
import copy
import hashlib
import io
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest
from reportlab.pdfgen import canvas

from test_forge import db, _run, ETALONS, forge, goldfish
from app.models import IndigoExercise
from app.services import card_preview, exercise_gen, exercise_review, grading, indigo, pdfgen, pipeline, svgfig, worker_cv
import rework


def test_history_and_failed_state_survive_a_new_version(db):
    run = _run(db, [ETALONS[0]])
    forge.render(db, run); forge.persist(db, run)
    old = db.query(IndigoExercise).one()
    indigo.set_review_verdict(db, old, 'dislike', 'Consigne à préciser')
    rework.failed(db, old, 'Le moteur ne pouvait pas respecter la taille')
    assert indigo.needs_review(old)
    manifest = run / 'reprise.json'; forge.dump_json(manifest, rework.export(db, forge.disliked(db)))
    image_hash = hashlib.sha256((run / 'cards/s01.png').read_bytes()).hexdigest()
    forge.dump_json(run / 'goldfish.json', {'verdicts': {'s01': {'flag': False, 'notes': [], 'complete': True, 'reader': 'terra', 'image_sha256': image_hash}}})
    forge.persist(db, run, slots=['s01'], reprise=manifest)
    new = db.query(IndigoExercise).one()
    history = indigo.exercise_out(db, new)['review_history']
    assert [e['status'] for e in history] == ['disliked', 'failed', 'ok']
    assert history[-1]['previous']['comment'] == 'Consigne à préciser'
    assert history[-1]['goldfish']['reader'] == 'terra'
    assert not indigo.needs_review(new)
    rework.failed(db, new, 'Nouvel échec de contrôle')
    assert indigo.needs_review(new)
    indigo.set_review_verdict(db, new, 'like')
    assert not indigo.needs_review(new)


def test_manifest_rejects_changed_or_accepted_card(db):
    run = _run(db, [ETALONS[0]])
    forge.render(db, run); forge.persist(db, run)
    row = db.query(IndigoExercise).one()
    indigo.set_review_verdict(db, row, 'dislike', 'Erreur')
    manifest = rework.export(db, forge.disliked(db))
    target = {row.source_number}
    rework.verify([row], manifest, target)
    indigo.set_review_verdict(db, row, 'dislike', 'Nouveau commentaire')
    with pytest.raises(SystemExit, match='modifié'):
        rework.verify([row], manifest, target)
    indigo.set_review_verdict(db, row, 'like')
    with pytest.raises(SystemExit, match='plus dislikée'):
        rework.verify([row], manifest, target)
    with pytest.raises(SystemExit, match='acceptée'):
        forge.persist(db, run, replace=True)
    assert db.get(IndigoExercise, row.id) is row


def test_rework_proof_rejects_changed_image_or_contract(db):
    run = _run(db, [ETALONS[0]])
    rendered = forge.render(db, run)
    exercise = forge.load_slots(run)['s01']; contract = forge.check(db, run)[1]['s01']
    verdict = {'complete': True, 'image_sha256': hashlib.sha256((run / 'cards/s01.png').read_bytes()).hexdigest()}
    rework.proof(run, 's01', exercise, contract, rendered, verdict, required=True)
    with pytest.raises(SystemExit, match='après render'):
        rework.proof(run, 's01', {**exercise, 'statement': 'Différent'}, contract, rendered, verdict, required=True)
    (run / 'cards/s01.png').write_bytes(b'changed')
    with pytest.raises(SystemExit, match='dernier rendu'):
        rework.proof(run, 's01', exercise, contract, rendered, verdict, required=True)


@pytest.mark.parametrize('category', ['writing', 'leading', 'traces'])
def test_targeted_persist_checks_selected_layout_without_touching_other_cards(db, category):
    run = _run(db, [ETALONS[0], ETALONS[1]])
    rendered = forge.render(db, run)
    forge.persist(db, run)
    selected, untouched = db.query(IndigoExercise).order_by(IndigoExercise.source_number).all()
    indigo.set_review_verdict(db, selected, 'dislike', 'Reprendre cette carte')
    indigo.set_review_verdict(db, untouched, 'like')
    other_id, other_before = untouched.id, exercise_review.snapshot(untouched)
    manifest = run / 'reprise.json'
    forge.dump_json(manifest, rework.export(db, forge.disliked(db)))
    image_hash = hashlib.sha256((run / 'cards/s01.png').read_bytes()).hexdigest()
    forge.dump_json(run / 'goldfish.json', {'verdicts': {'s01': {
        'flag': False, 'complete': True, 'image_sha256': image_hash}}})
    rendered[category] = {'s02': ['défaut hors sélection']}
    forge.dump_json(run / 'render.json', rendered)
    with pytest.raises(SystemExit, match='s02'):
        forge.persist(db, run)
    rendered[category]['s01'] = ['défaut dans la sélection']
    forge.dump_json(run / 'render.json', rendered)
    with pytest.raises(SystemExit, match='s01'):
        forge.persist(db, run, slots=['s01'], reprise=manifest)
    del rendered[category]['s01']
    forge.dump_json(run / 'render.json', rendered)
    forge.persist(db, run, slots=['s01'], reprise=manifest)
    db.expire_all()
    assert exercise_review.snapshot(db.get(IndigoExercise, other_id)) == other_before
    assert db.query(IndigoExercise).count() == 2


def test_guide_never_splits_an_equality_in_multiple_answers():
    answer_line = '$AN =$ {{blank}} cm et $NC =$ {{blank}} cm'
    text = forge.place_guides('Calcule les longueurs.\n' + answer_line, [{'text': 'Utilise les rapports de Thalès.', 'position': 'apres_question'}])
    assert text.splitlines()[-1] == answer_line
    assert text.index('{{aide}}') < text.index('$AN =$')


def test_numeric_precision_and_representation_are_per_cell():
    cells = [[{'type': 'rational', 'value': [7, 10], 'answer_form': 'any'},
              {'type': 'integer', 'value': 35, 'precision': 'exact_value', 'answer_form': 'any'}]]
    policy = {'cells': cells, 'answer_form': 'exact_decimal'}
    assert grading.table_credits(policy, ['7/10', '35']) == [1, 1]
    assert grading.table_credits(policy, ['0,7', '35,2']) == [1, 0]
    assert grading.table_credits(policy, ['0.70', '34']) == [1, 0]


def test_natural_table_is_compact_and_fields_are_uniform():
    cells = [[{'type': 'integer', 'value': 35}], [{'type': 'integer', 'value': 105}]]
    compact = pdfgen._table_geometry(pdfgen.COL_W, ['Écart'], ['A et B', 'D et E'], cells, 9)
    full = pdfgen._table_geometry(pdfgen.COL_W, ['Écart'], ['A et B', 'D et E'], cells, 9, response_layout={'width': 'full'})
    assert compact['total_w'] < full['total_w'] * .8
    assert compact['col_ws'][0] - 2 * pdfgen._TABLE_CELL_PAD >= max(pdfgen.handwriting.need_w(c[0]) for c in cells)


def test_mixed_table_draws_real_boxes_and_grades_each_cell():
    decision = {'type': 'text', 'value': 'Oui', 'input': 'single_choice', 'choices': ['Oui', 'Non']}
    assert exercise_gen._validate_cell(decision) == decision
    cells = [[{'type': 'integer', 'value': 25}, decision],
             [{'type': 'integer', 'value': 26}, {**decision, 'value': 'Non'}]]
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    height = pdfgen._table_zone_height(pdfgen.COL_W, ['Carré', 'Rectangle ?'], ['ABC', 'DEF'], cells, 9)
    meta = pdfgen._draw_table_zone(c, 10, 100, pdfgen.COL_W, height, ['Carré', 'Rectangle ?'], ['ABC', 'DEF'], cells, 9)
    assert len(meta['cells'][0][1]['boxes']) == 2
    box = meta['cells'][0][1]['boxes'][0]
    c.setFillColorRGB(0, 0, 0); c.rect(box['x_pt']+.5, box['y_pt']+.5, box['w_pt']-1, box['h_pt']-1, fill=1)
    c.showPage(); c.save()
    with fitz.open(stream=buf.getvalue(), filetype='pdf') as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(worker_cv.SCALE, worker_cv.SCALE))
        image = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3), cv2.COLOR_RGB2BGR)
    choice = meta['cells'][0][1]
    threshold = worker_cv.adapt_qcm_threshold(worker_cv.qcm_densities(image, choice['boxes']))
    value, confidence = pipeline.read_choice_cell(image, choice, threshold)
    assert value == 'Oui' and confidence > .9
    assert grading.table_credits({'cells': cells}, ['25', value, '26', 'Non']) == [1, 1, 1, 1]
    assert grading.table_credits({'cells': cells}, ['24', 'Oui', '26', 'Oui']) == [0, 1, 1, 0]
    assert grading.table_credits({'cells': cells}, ['25', '?', '26', '']) == [1, None, 1, 0]
    blank = np.full_like(image, 255)
    assert pipeline.read_choice_cell(blank, choice, worker_cv.adapt_qcm_threshold([0, 0]))[0] == ''
    for b in choice['boxes']:
        x, y = map(int, worker_cv.pt_to_px(b['x_pt'], b['y_pt']+b['h_pt']))
        cv2.rectangle(blank, (x+1, y+1), (x+int(b['w_pt']*worker_cv.SCALE)-1, y+int(b['h_pt']*worker_cv.SCALE)-1), (0, 0, 0), -1)
    assert pipeline.read_choice_cell(blank, choice, worker_cv.adapt_qcm_threshold([1, 1])) == ('?', 0.0)


def test_preserve_figure_scale_survives_compaction():
    figure = {'type': 'svg', 'params': {'width_mm': 80, 'svg': '<svg viewBox="0 0 100 20"><line x1="0" y1="10" x2="100" y2="10" stroke="black"/></svg>'}}
    ex = {'response_type': 'qcm_single', 'statement': 'Repère la bonne transformation dans cette frise.', 'choices': ['A', 'B'],
          'figure': figure, 'expected': {'correct': [0]}, 'grading': {'figure_constraints': {'min_scale': 1}}}
    layout, scale, _ = forge.best_layout(ex)
    assert scale == 1 and layout not in ('left', 'right', 'grid_left', 'grid_right')


def test_svg_dashes_are_present_in_rendered_pixels():
    png = svgfig.render({'width_mm': 60, 'svg': '<svg viewBox="0 0 100 20"><line x1="5" y1="10" x2="95" y2="10" stroke="black" stroke-width="1" stroke-dasharray="4,4"/></svg>'})
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    row = image[image.shape[0]//2]
    core = row[int(len(row)*.1):int(len(row)*.9)] < 180
    assert 0.25 < core.mean() < .75
    assert np.count_nonzero(core[1:] != core[:-1]) > 10


def test_goldfish_composite_compares_parts_not_combined_text():
    exercise = {'response_type': 'composite', 'parts': [
        {'response_type': 'short_text', 'answer': {'type': 'rational', 'value': [7, 10]}},
        {'response_type': 'short_text', 'answer': {'type': 'integer', 'value': 35}}]}
    verdict = {'comprehensible': True, 'reponse': 'tan A = 7/10 et A = 35 degrés', 'reponses_parties': ['7/10', '35']}
    assert goldfish.judge_student(verdict, exercise, 2) == (None, [])
    assert goldfish.judge_student({**verdict, 'reponses_parties': ['7/10', '34']}, exercise, 2)[0] == 'blocker'


def test_geometric_name_stays_with_its_word():
    from app.services.typography import french, NBSP
    assert f'sommet{NBSP}$S$' in french('Plan à 3 cm du sommet $S$')
    text = french('Une boule de centre O.')
    assert f'centre{NBSP}O' in text and french(text) == text


def test_goldfish_does_not_store_partial_microcorrection_review(db, monkeypatch):
    run = _run(db, [ETALONS[0]]); forge.render(db, run)
    monkeypatch.setattr(goldfish, 'ask', lambda cli, system, text, images:
                        {'cartes': [{'comprehensible': True, 'reponse': '17'}]} if 'Voici' in text else {'messages': []})
    with pytest.raises(goldfish.GoldfishError, match='incomplète'):
        goldfish.run(run, cli='codex')
    assert not (run / 'goldfish.json').exists()


def test_codex_transport_passes_prompt_via_stdin(monkeypatch):
    from types import SimpleNamespace
    captured = {}
    def execute(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=0, stdout='{"cartes": []}', stderr='')
    monkeypatch.setattr(goldfish.subprocess, 'run', execute)
    assert goldfish._ask_codex('SYSTEM', 'QUESTION', []) == {'cartes': []}
    assert captured['cmd'][-2:] == ['--', '-']
    assert captured['input'] == 'SYSTEM\n\nQUESTION'


def test_session_transport_requires_both_independent_reviews(db):
    run = _run(db, [ETALONS[0]]); forge.render(db, run)
    folder = run / 'session'
    state = goldfish.run(run, slots=['s01'], session_dir=folder, model='gpt-6.1-terra')
    assert state['pending'] == ['s01'] and not state['verdicts']
    request = next(folder.glob('*.request.json'))
    forge.dump_json(Path(str(request).replace('.request.json', '.response.json')),
                    {'cartes': [{'comprehensible': True, 'reponse': '17'}]})
    state = goldfish.run(run, slots=['s01'], session_dir=folder, model='gpt-6.1-terra')
    assert state['pending'] == ['s01'] and not state['verdicts']
    for request in folder.glob('*.request.json'):
        response = Path(str(request).replace('.request.json', '.response.json'))
        if not response.exists():
            forge.dump_json(response, {'messages': [{'comprend': True, 'aide': True}]})
    state = goldfish.run(run, slots=['s01'], session_dir=folder, model='gpt-6.1-terra')
    assert not state['pending'] and state['verdicts']['s01']['complete']
    assert state['verdicts']['s01']['reader'] == 'gpt-6.1-terra'


def test_one_rule_sizes_inline_table_and_composite_fields():
    value = {'type': 'decimal', 'value': 3.2}
    ex = {'statement': '$x =$ {{blank}} cm', 'response_type': 'short_text', 'expected': {**value, 'inline': True}, 'grading': {}}
    item = card_preview.shape(ex)
    layout = pdfgen._exercise_layout(item, 9, 9)[0]
    line = next(line for line in layout['intro']['lines'] if any(s[0] == 'blank' for s in line.get('segs', [])))
    blank = next(s for s in line['segs'] if s[0] == 'blank')
    size = pdfgen.handwriting.field_size([value])
    assert blank[1] == size[0]
    # Le groupe complet (libellé, case et unité), pas seulement le rectangle, est centré.
    assert line['indent'] + line['w']/2 == pytest.approx((pdfgen.COL_W-2*pdfgen.CARD_PAD)/2)
    _, _, zone_h, _ = pdfgen._exercise_layout({**item, 'statement': 'Calcule.', 'inline': False}, 9, 9)
    assert zone_h - 2*pdfgen.mm == size[1]
    table = pdfgen._table_geometry(pdfgen.COL_W, None, None, [[value]], 9)
    assert table['col_ws'][0] - 2*pdfgen._TABLE_CELL_PAD == pytest.approx(size[0])


def test_reference_style_keeps_two_bands_and_comfortable_fields():
    cells = [[{'type': 'integer', 'value': i}, {'type': 'integer', 'value': -i}] for i in range(6)]
    geometry = pdfgen._table_geometry(pdfgen.COL_W, ['Abscisse', 'Ordonnée'], [f'$A_{i}$' for i in range(6)], cells, 9)
    assert geometry['banks'] == 2
    assert all(w-2*pdfgen._TABLE_CELL_PAD >= 14*pdfgen.mm for w in geometry['col_ws'])


def test_short_composite_uses_a_compact_centered_zone():
    value = {'type': 'rational', 'value': [7, 10]}
    part = {'statement': 'Calcule.', 'response_type': 'short_text', 'expected': value, 'grading': {}}
    exercise = {'statement': 'Réponds.', 'response_type': 'composite', 'expected': {'parts': [part, {**part, 'expected': {'type': 'integer', 'value': 35}}]}}
    item = card_preview.shape(exercise)
    cl = pdfgen._composite_layout(item, 9, 9)
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    _, zones = pdfgen._draw_composite_card(c, 0, 300, pdfgen.COL_W, 1, cl, item, pdfgen.DEFAULT_TEMPLATES['exercise'], 9)
    for zone in zones:
        geo = zone['zone_geo']
        assert geo['w_pt'] < pdfgen.COL_W / 2
        assert geo['x_pt'] + geo['w_pt']/2 == pytest.approx(pdfgen.COL_W/2)

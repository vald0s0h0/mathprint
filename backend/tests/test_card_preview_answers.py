"""Le corrigé visuel garde la géométrie de la feuille et utilise une encre bleue."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import card_preview


CASES = [
    {"statement": "Coche la bonne réponse.", "response_type": "qcm_single",
     "choices": ["2", "3", "4"], "expected": {"correct": [1]}},
    {"statement": "Coche les bonnes réponses.", "response_type": "qcm_multiple",
     "choices": ["2", "3", "4"], "expected": {"correct": [0, 2]}},
    {"statement": "Relie chaque nombre à son double.", "response_type": "matching",
     "expected": {"left": ["2", "3"], "right": ["6", "4"], "pairs": [[0, 1], [1, 0]]}},
    {"statement": "Calcule $2+3$.", "response_type": "short_text",
     "expected": {"type": "integer", "value": 5}},
    {"statement": "Calcule : $2+3 =$ {{blank}}.", "response_type": "short_text",
     "expected": {"type": "integer", "value": 5, "inline": True}},
    {"statement": "Complète : {{blank}} et {{blank}}.", "response_type": "multi_blank",
     "expected": {"cells": [[{"type": "integer", "value": 12}, {"type": "integer", "value": 24}]]}},
    {"statement": "Complète le tableau.", "response_type": "table_fill",
     "expected": {"cells": [[{"type": "integer", "value": 1, "given": True}, {"type": "rational", "value": [3, 4]}],
                              [{"type": "integer", "value": 2, "given": True}, {"type": "decimal", "value": 1.5}]]}},
    {"statement": "Justifie.", "response_type": "multiline_text",
     "expected": {"steps": [{"expected_text": "On additionne les deux valeurs."}]}, "grading": {"lines": 4}},
    {"statement": "Coche vrai ou faux.", "response_type": "checkbox_grid",
     "expected": {"cols": ["Vrai", "Faux"], "rows": [{"label": "2+2=4", "correct": 0}, {"label": "3+3=7", "correct": 1}]}},
    {"statement": "Réponds aux questions.", "response_type": "composite",
     "expected": {"parts": [
         {"statement": "Coche.", "response_type": "qcm_single", "expected": {"correct": [0]}, "grading": {"choices": ["2", "4"]}},
         {"statement": "Calcule $2+3$.", "response_type": "short_text", "expected": {"type": "integer", "value": 5}, "grading": {}},
     ]}},
]


@pytest.mark.parametrize("exercise", CASES, ids=lambda e: e['response_type'] + ('_inline' if e.get('expected', {}).get('inline') else ''))
def test_answer_ink_does_not_change_card_geometry(exercise):
    empty = np.asarray(Image.open(io.BytesIO(card_preview.render(exercise))))[:, :, :3].astype(int)
    filled = np.asarray(Image.open(io.BytesIO(card_preview.render(exercise, show_answers=True))))[:, :, :3].astype(int)
    assert filled.shape == empty.shape
    changed = np.any(filled != empty, axis=2)
    assert changed.sum() > 20
    # L’anticrénelage mélange le bleu au saumon des cases : on vérifie
    # l’encre franche et l’absence de vert, plutôt que les pixels de bord.
    ink = filled[changed]
    assert not np.any((ink[:, 1] > ink[:, 0] + 30) & (ink[:, 1] > ink[:, 2] + 30))
    assert np.any(ink[:, 2] > ink[:, 0] + 60)


def test_handwritten_preview_uses_the_solution_instead_of_a_larger_wrong_answer(monkeypatch):
    written = []
    pen_value = card_preview._pen_value
    def capture(canvas, value, *args):
        written.append(value)
        return pen_value(canvas, value, *args)
    monkeypatch.setattr(card_preview, '_pen_value', capture)
    exercise = {"statement": "Calcule $2+3$.", "response_type": "short_text",
                "expected": {"type": "integer", "value": 5},
                "grading": {"feedback": {"wrong_answers": [{"value": 12345, "text": "Recalcule."}]}}}
    card_preview.render(exercise, show_answers=True)
    assert written == [exercise['expected']]


def test_expression_answer_is_written_as_math_and_still_grades_correctly(monkeypatch):
    from app.services import grading, handwriting
    expected = {'type': 'expression', 'value': '3*x**2'}
    lines = []
    original = card_preview._pen_line
    def capture(canvas, text, *args, **kwargs):
        lines.append(text)
        return original(canvas, text, *args, **kwargs)
    monkeypatch.setattr(card_preview, '_pen_line', capture)
    exercise = {'statement': 'Écris son aire.', 'response_type': 'short_text', 'expected': expected}
    card_preview.render(exercise, show_answers=True)
    assert lines == ['3x²']
    assert grading.cell_credit(expected, lines[0]) == 1
    assert expected['value'] == '3*x**2'
    assert handwriting.forms({'type': 'expression', 'value': 'x**12'}) == ('x¹²',)


def test_problem_title_is_identical_for_admin_and_published_cards():
    base = {"statement": "Quelle est son aire ?", "response_type": "qcm_single",
            "choices": ["20", "40"], "expected": {"correct": [0]}, "difficulty": 2}
    draft = {**base, "title": "Plan du jardin", "badge_type": "probleme"}
    published = {**base, "statement": "**Plan du jardin**\nQuelle est son aire ?", "kind": "probleme"}
    assert card_preview.render(draft, show_answers=True) == card_preview.render(published, show_answers=True)

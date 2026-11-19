"""Phrase à trous MANUSCRITE : police des copies, bleu, trous = tirets saumon à bouts ronds."""
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "forge"))
from app.services import card_preview, handwriting, pdfgen  # noqa: E402

PHRASE = {"response_layout": {"style": "phrase"}}


def _exercise(grading=None):
    return {"statement": "Complète.\nDans le triangle $ABC$ rectangle en $B$, $[AC]$ est l'{{blank}}. "
                         "Donc $AC^2 =$ {{blank}}.",
            "response_type": "multi_blank",
            "expected": {"cells": [[{"type": "text", "value": "hypoténuse"},
                                    {"type": "integer", "value": 100}]]},
            "grading": grading or {}}


def _layout(exercise, style=True):
    item = card_preview.shape(exercise)
    token = pdfgen._PHRASE_STYLE.set(pdfgen._phrase_widths(item, item["grading"]))
    try:
        return pdfgen._exercise_layout_impl(item, 10, 10)[0]
    finally:
        pdfgen._PHRASE_STYLE.reset(token)


def test_font_size_is_a_student_handwriting_size():
    # un chiffre de la police a la hauteur d'un chiffre manuscrit d'élève
    cap = pdfgen._HAND_FACE.capHeight / 1000
    assert abs(pdfgen.HAND_FS * cap - handwriting.DIGIT_H) < 0.01


def test_each_blank_is_sized_from_its_own_expected_answer():
    item = card_preview.shape(_exercise(PHRASE))
    widths = pdfgen._phrase_widths(item, item["grading"])
    assert widths == [handwriting.field_size([{"type": "text", "value": "hypoténuse"}])[0],
                      handwriting.field_size([{"type": "integer", "value": 100}])[0]]
    assert widths[0] > widths[1]


def test_style_is_off_without_the_flag_and_for_other_types():
    assert pdfgen._phrase_widths(card_preview.shape(_exercise()), {}) is None
    other = {"statement": "Coche.", "response_type": "qcm_single", "choices": ["a", "b"],
             "expected": {"correct": [0]}, "grading": PHRASE}
    item = card_preview.shape(other)
    assert pdfgen._phrase_widths(item, item["grading"]) is None


def test_phrase_lines_are_handwritten_and_blanks_are_rules():
    lay = _layout(_exercise(PHRASE))
    hand = [ln for ln in lay["intro"]["lines"] if ln.get("hand")]
    assert hand and all(ln["fs"] == pdfgen.HAND_FS for ln in hand)
    assert {ln["h"] for ln in hand} == {max(ln["h"] for ln in hand)}   # interligne régulier
    kinds = [s[4] for ln in hand for s in ln["segs"] if s[0] == "blank"]
    assert kinds == ["phrase", "phrase"]
    words = [s for ln in hand for s in ln["segs"] if s[0] == "word"]
    assert words and all(s[2] == "hand" for s in words)
    # la consigne reste au corps de la carte
    assert not lay["intro"]["lines"][0].get("hand")
    assert pdfgen.broken_leading(lay) == 0


def test_rendered_card_has_blue_text_and_salmon_rules_without_boxes():
    png = card_preview.render(_exercise(PHRASE))
    px = np.asarray(Image.open(io.BytesIO(png)))[:, :, :3].astype(int)
    blue = (px[:, :, 2] > px[:, :, 0] + 70) & (abs(px[:, :, 0] - 0x2F) < 40)
    salmon = (abs(px[:, :, 0] - 0xF5) < 12) & (abs(px[:, :, 1] - 0xB7) < 14) & (abs(px[:, :, 2] - 0xA8) < 14)
    assert blue.sum() > 800 and salmon.sum() > 200
    # un tiret est mince : aucune ligne de pixels saumon n'a la hauteur d'une case
    rows = np.where(salmon.sum(axis=1) > 0)[0]
    assert rows.size and rows.max() - rows.min() > 0
    # le tiret saumon est plein (pas de pointillés) : une ligne dense, continue
    densest = salmon.sum(axis=1).max()
    longest_run = max(len(run) for run in np.split(np.where(salmon[salmon.sum(axis=1).argmax()])[0],
                      np.where(np.diff(np.where(salmon[salmon.sum(axis=1).argmax()])[0]) > 1)[0] + 1))
    assert longest_run > 0.8 * densest * 0.35


def test_answers_do_not_change_the_geometry():
    exercise = _exercise(PHRASE)
    empty = np.asarray(Image.open(io.BytesIO(card_preview.render(exercise))))
    filled = np.asarray(Image.open(io.BytesIO(card_preview.render(exercise, show_answers=True))))
    assert empty.shape == filled.shape


def test_forge_accepts_phrase_style_only_on_a_blank_sentence():
    import copy
    import json
    import forge
    etalons = json.loads((Path(__file__).resolve().parents[2] / "agents" / "forge" / "contexte"
                          / "exemples" / "math.json").read_text(encoding="utf-8"))["cartes"]
    card = next(c for c in etalons if c["exercice"]["response_type"] == "short_text")
    slot = {"slot": "s01", "kind": "exercise", "competency": "A1.2", "level": card["exercice"].get("level", 2),
            "difficulty": "moyen", "response_type": "short_text", "size": "S", "figure": False}

    def messages(mutate):
        exercise = {**copy.deepcopy(card["exercice"]), "slot": "s01"}
        mutate(exercise)
        return [p for p in forge._slot_problems(exercise, slot, {}) if "response_layout" in p]

    assert messages(lambda e: e.update(statement="Le côté est {{blank}}.",
                                       response_layout={"style": "phrase"})) == []
    assert messages(lambda e: e.update(statement="Calcule.", response_layout={"style": "phrase"}))
    assert messages(lambda e: e.update(response_layout={"style": "dotted"}))

"""Mélange anti-triche des champs de réponse d'une carte, propre à une copie.

Propositions de QCM, lignes de grille cochée, lignes OU colonnes des tableaux,
colonne de droite des points à relier. La permutation s'applique ensemble au barème, à la réponse
attendue et aux identifiants de micro-correction. La carte étant ensuite figée
sur sa CopyItem, le CV, la note et l'overlay lisent tous l'ordre imprimé.

On ne mélange pas ce qui perdrait son sens : un QCM dont une proposition désigne
une autre (« b et c », « aucune des réponses »), des lignes numérotées ou se
terminant par un total. Un ordre qui agrandirait la zone de réponse est écarté :
la carte n'est jamais plus haute que celle mesurée pour composer la feuille.
"""
import random
import re

from . import pdfgen

_ZONE_TRIES = 8

_POSITIONAL_CHOICE = re.compile(
    r"^\s*[a-e]\s*(?:et|ou|,)\s*[a-e]\b"
    r"|^\s*[a-e][.)]\s"
    r"|\b(?:toutes|aucune)\s+(?:les\s+|des\s+|de\s+ces\s+)?(?:r[ée]ponses?|propositions?)\b"
    r"|\bci-dessus\b|\bpr[ée]c[ée]dente?s?\b",
    re.IGNORECASE)
_POSITIONAL_STATEMENT = re.compile(
    r"\b(?:r[ée]ponse|proposition|choix)\s+[a-e1-5]\b", re.IGNORECASE)
_SEQUENTIAL_ROW = re.compile(
    r"^\s*(?:\(?[a-h1-9][.)]\s|[ée]tape\b|ligne\b|total\b|somme\b|moyenne\b)"
    r"|\bpr[ée]c[ée]dente?s?\b|\bci-dessus\b",
    re.IGNORECASE)


def _permuted(values: list, order: list[int]) -> list:
    return [values[old] for old in order]


def _shuffled_order(n: int, rng: random.Random) -> list[int]:
    order = list(range(n))
    rng.shuffle(order)
    return order


def _labels_are_free(labels: list) -> bool:
    if any(_SEQUENTIAL_ROW.search(str(label)) for label in labels):
        return False
    try:
        numbers = [float(str(label).replace(",", ".")) for label in labels]
    except ValueError:
        return True
    return numbers not in (sorted(numbers), sorted(numbers, reverse=True))


def _mix_qcm(grading: dict, expected: dict, rng: random.Random,
             statement: str) -> tuple[dict, dict]:
    choices = grading.get("choices") or []
    if (len(choices) < 2 or _POSITIONAL_STATEMENT.search(statement or "")
            or any(_POSITIONAL_CHOICE.search(str(c)) for c in choices)):
        return grading, expected
    correct = expected.get("correct") or []
    if any(not isinstance(i, int) or not 0 <= i < len(choices) for i in correct):
        return grading, expected
    order = _shuffled_order(len(choices), rng)
    position = {old: new for new, old in enumerate(order)}
    mixed = {**grading, "choices": _permuted(choices, order)}
    if len(grading.get("option_ids") or []) == len(choices):
        mixed["option_ids"] = _permuted(grading["option_ids"], order)
    return mixed, {**expected, "correct": sorted(position[i] for i in correct)}


def _mix_grid(grading: dict, expected: dict, rng: random.Random) -> tuple[dict, dict]:
    rows = grading.get("rows") or []
    if len(rows) < 2 or not _labels_are_free([r.get("label", "") for r in rows]):
        return grading, expected
    order = _shuffled_order(len(rows), rng)
    mixed = {**grading, "rows": _permuted(rows, order)}
    if len(grading.get("option_ids") or []) == len(rows):
        mixed["option_ids"] = _permuted(grading["option_ids"], order)
    expected_rows = expected.get("rows") or []
    if len(expected_rows) != len(rows):
        return grading, expected
    return mixed, {**expected, "rows": _permuted(expected_rows, order)}


def _mix_table(grading: dict, expected: dict, rng: random.Random) -> tuple[dict, dict]:
    cells = grading.get("cells") or []
    expected_cells = expected.get("cells") or []
    cols = len(cells[0]) if cells else 0
    if (not cols or any(len(row) != cols for row in cells)
            or len(expected_cells) != len(cells)
            or any(len(row) != cols for row in expected_cells)):
        return grading, expected
    col_labels = grading.get("col_labels") or []
    declared = grading.get("mix")
    if declared == "none":
        return grading, expected
    # Deux axes nommés : seul l'auteur sait lequel porte les entités (des produits en
    # colonnes, ou des équations en lignes dont les colonnes se lisent dans l'ordre).
    # Sans déclaration on ne devine pas : le mélange ne doit jamais casser la lecture.
    two_axes = len(col_labels) > 1 and bool(grading.get("row_labels")) and len(cells) > 1
    if two_axes and declared not in ("rows", "columns"):
        return grading, expected
    if len(col_labels) > 1 and not (two_axes and declared == "rows"):
        # Si les en-têtes imposent un ordre, on conserve le tableau entier.
        if len(col_labels) != cols or not _labels_are_free(col_labels):
            return grading, expected
        axis, count, label_key = 1, cols, "col_labels"
    else:
        labels = grading.get("row_labels")
        labelled = bool(labels) and len(labels) == len(cells) and _labels_are_free(labels)
        # Sans libellé, une valeur imprimée doit distinguer chaque ligne.
        given_rows = not labels and all(any(c.get("given") for c in row) for row in cells)
        if len(cells) < 2 or not (labelled or given_rows):
            return grading, expected
        axis, count, label_key = 0, len(cells), "row_labels"
    order = _shuffled_order(count, rng)
    position = {old: new for new, old in enumerate(order)}

    def apply(contract: dict) -> dict:
        matrix = contract["cells"]
        out = {**contract, "cells": ([_permuted(row, order) for row in matrix]
                                     if axis == 1 else _permuted(matrix, order))}
        labels = contract.get(label_key)
        if labels and len(labels) == count:
            out[label_key] = _permuted(labels, order)
        return out

    mixed = apply(grading)
    feedback = grading.get("feedback")
    if feedback and feedback.get("wrong_answers"):
        def move_cell(wrong: dict) -> dict:
            if wrong.get("cell") is None:
                return wrong
            target = list(wrong["cell"])
            target[axis] = position[target[axis]]
            return {**wrong, "cell": target}

        mixed["feedback"] = {**feedback, "wrong_answers": [
            move_cell(wrong) for wrong in feedback["wrong_answers"]]}
    return mixed, apply(expected)


def _mix_matching(grading: dict, expected: dict, rng: random.Random, width: float,
                  font_size: int) -> tuple[dict, dict]:
    left, right = grading.get("left") or [], grading.get("right") or []
    pairs = expected.get("pairs") or grading.get("pairs") or []
    order = pdfgen.shuffled_legible_order(left, right, pairs, rng, font_size, width)
    if order is None:
        return grading, expected
    position = {old: new for new, old in enumerate(order)}

    def apply(contract: dict) -> dict:
        out = dict(contract)
        if "right" in out:
            out["right"] = _permuted(out["right"], order)
        if "pairs" in out:
            out["pairs"] = [[int(a), position[int(b)]] for a, b in out["pairs"]]
        if "right_ids" in out:
            out["right_ids"] = _permuted(out["right_ids"], order)
        return out

    return apply(grading), apply(expected)


def _mix_leaf(rtype: str, grading: dict, expected: dict, statement: str, rng: random.Random,
              width: float, font_size: int) -> tuple[dict, dict]:
    if rtype not in ("qcm_single", "qcm_multiple", "checkbox_grid", "table_fill", "matching"):
        return grading, expected
    base_height = pdfgen.answer_zone_height(rtype, grading, expected, width, font_size)
    for _ in range(_ZONE_TRIES):
        if rtype in ("qcm_single", "qcm_multiple"):
            mixed = _mix_qcm(grading, expected, rng, statement)
        elif rtype == "checkbox_grid":
            mixed = _mix_grid(grading, expected, rng)
        elif rtype == "table_fill":
            mixed = _mix_table(grading, expected, rng)
        else:
            mixed = _mix_matching(grading, expected, rng, width, font_size)
        height = pdfgen.answer_zone_height(rtype, mixed[0], mixed[1], width, font_size)
        if height <= base_height:
            return mixed
    return grading, expected


def mix_shape(shape: dict, rng: random.Random, width: float, font_size: int) -> dict:
    """Carte `shape` (generation.render_shape) avec ses champs mélangés. `width` :
    pdfgen.answer_zone_width de la carte. Les sous-questions d'un composite gardent
    leur ordre, chacune mélangée à part."""
    if shape["response_type"] == "composite":
        parts = []
        for part in shape["grading"].get("parts") or []:
            grading, expected = _mix_leaf(part["response_type"], part["grading"], part["expected"],
                                          part.get("statement", ""), rng, width, font_size)
            parts.append({**part, "grading": grading, "expected": expected})
        return {**shape, "grading": {**shape["grading"], "parts": parts}}
    grading, expected = _mix_leaf(shape["response_type"], shape["grading"], shape.get("expected") or {},
                                  shape.get("statement", ""), rng, width, font_size)
    return {**shape, "grading": grading, "expected": expected,
            "choices": grading.get("choices", shape.get("choices", []))}

"""Tests : conversion MathALÉA -> contrat interne, nettoyage LaTeX, filtre CV."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mathalea_client import _expected_from_mathalea, latex_to_text
from app.services.grading import grade


def test_latex_cleanup():
    assert latex_to_text(r"$3 + \ldots = 7$") == "3 + …… = 7"
    assert latex_to_text(r"$\dfrac{2}{5} \times 10$") == "2/5 × 10"
    assert latex_to_text(r"Calcul<br>suivant : $5{,}5$") == "Calcul suivant : 5,5"
    assert "begin" not in latex_to_text(r"\begin{array}{|c|c|}1 & 2\end{array}")


def test_expected_mapping_number():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": [5]})
    assert exp == {"type": "integer", "value": 5}
    r = grade(exp, grad, "5", 0.95)
    assert r["score"] == 1


def test_expected_mapping_decimal_string():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": ["5,6"]})
    assert exp["type"] == "decimal"
    r = grade(exp, grad, "5,6", 0.95)
    assert r["score"] == 1


def test_expected_mapping_fraction():
    exp, grad = _expected_from_mathalea({"format": "fraction", "values": [{"fraction": [3, 4]}]})
    r = grade(exp, grad, "6/8", 0.9)   # équivalence, pas égalité de chaînes
    assert r["score"] == 1


def test_expected_mapping_text_expression():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": ["2\\times3\\times5"]})
    r = grade(exp, grad, "2×3×5", 0.9)
    assert r["score"] == 1  # normalisation commune \times/× -> *


def test_expected_missing_goes_manual():
    exp, grad = _expected_from_mathalea(None)
    r = grade(exp, grad, "n'importe quoi", 0.9)
    assert r["tier"] == "D"


def test_dropout_preserves_blue_ink_removes_salmon():
    from app.services.worker_cv import dropout_filter, ink_ratio
    img = np.full((60, 60, 3), 255, dtype=np.uint8)
    img[10:20, 10:50] = (196, 183, 245)   # BGR du rouge saumon #F5B7A8 (cadre)
    img[35:45, 10:50] = (120, 40, 20)     # encre bleu foncé (élève)
    out = dropout_filter(img)
    assert (out[15, 30] == 255).all(), "le cadre saumon doit être supprimé"
    assert (out[40, 30] != 255).any(), "l'encre bleue doit être conservée"
    assert ink_ratio(out) > 0.05

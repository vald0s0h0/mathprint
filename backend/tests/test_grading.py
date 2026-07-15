"""Tests unitaires du moteur déterministe (§12.4) : comparateurs, QCM, HMAC, oubli."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.grading import grade, normalize
from app.services.security import sign_page, verify_page_payload


def test_normalize_french():
    assert normalize("3,5") == "3.5"
    assert normalize("2 × 3") == "2*3"
    assert normalize("\\frac{1}{2}") == "(1)/(2)"
    assert normalize("−4") == "-4"


def test_integer_match():
    r = grade({"type": "integer", "value": 10}, {"max_score": 1, "comparator": "numeric"}, "10", 0.95)
    assert r["tier"] == "A" and r["score"] == 1


def test_integer_mismatch():
    r = grade({"type": "integer", "value": 10}, {"max_score": 1, "comparator": "numeric"}, "12", 0.95)
    assert r["score"] == 0 and r["tier"] == "A"


def test_rational_equivalence():
    # 2/4 == 1/2 : équivalence, pas comparaison de chaînes
    r = grade({"type": "rational", "value": [1, 2]},
              {"max_score": 2, "comparator": "rational_equiv"}, "2/4", 0.9)
    assert r["score"] == 2


def test_symbolic_equivalence():
    r = grade({"type": "expression", "value": "2*x + 6", "variable": "x"},
              {"max_score": 2, "comparator": "symbolic_equiv"}, "6 + 2x", 0.9)
    assert r["score"] == 2


def test_equation_solution_with_prefix():
    r = grade({"type": "integer", "value": 5, "variable": "x"},
              {"max_score": 3, "comparator": "equation_solution"}, "x = 5", 0.9)
    assert r["score"] == 3


def test_low_confidence_goes_to_review():
    r = grade({"type": "integer", "value": 10}, {"max_score": 1, "comparator": "numeric"}, "10", 0.4)
    assert r["tier"] == "D"  # faible confiance -> revue, jamais un choix silencieux (RM-005)


def test_blank_is_zero_not_review():
    r = grade({"type": "integer", "value": 10}, {"max_score": 1, "comparator": "numeric"}, "", 0.0)
    assert r["tier"] == "A" and r["score"] == 0


def test_qcm_double_check_is_exception():
    r = grade({"type": "choice", "correct": [1]}, {"max_score": 1, "comparator": "qcm"},
              "", 1.0, selected_choices=[0, 1])
    assert r["tier"] == "D" and r["reason_code"] == "qcm_double_check"


def test_qcm_correct():
    r = grade({"type": "choice", "correct": [2]}, {"max_score": 1, "comparator": "qcm"},
              "", 1.0, selected_choices=[2])
    assert r["score"] == 1


def test_manual_always_review_even_blank():
    # tracé/dessin (manual_drawing) : jamais de score deviné, même copie vide
    r = grade({"type": "manual"}, {"max_score": 1, "comparator": "manual"}, "", 1.0)
    assert r["tier"] == "D" and r["reason_code"] == "no_structured_answer"


def test_table_cells_all_correct():
    cells = [[{"type": "integer", "value": 4}, {"type": "text", "value": "pair"}]]
    r = grade({"type": "table", "cells": cells},
              {"max_score": 2, "comparator": "table_cells", "cells": cells},
              "", 1.0, cell_texts=["4", "pair"])
    assert r["score"] == 2 and r["tier"] == "A"


def test_table_cells_one_wrong():
    cells = [[{"type": "integer", "value": 4}, {"type": "integer", "value": 9}]]
    r = grade({"type": "table", "cells": cells},
              {"max_score": 2, "comparator": "table_cells", "cells": cells},
              "", 1.0, cell_texts=["4", "8"])
    assert r["score"] == 1 and r["tier"] == "B"


def test_table_cells_unreadable_goes_to_review():
    cells = [[{"type": "integer", "value": 4}]]
    r = grade({"type": "table", "cells": cells},
              {"max_score": 1, "comparator": "table_cells", "cells": cells},
              "", 1.0, cell_texts=["quatre"])
    assert r["tier"] == "D" and r["reason_code"] == "table_cell_unreadable"


def test_matching_full_match():
    r = grade({"type": "matching", "pairs": [[0, 1], [1, 0]]},
              {"max_score": 2, "comparator": "matching"}, "", 1.0,
              selected_pairs=[[0, 1], [1, 0]])
    assert r["score"] == 2 and r["tier"] == "B"


def test_matching_unreadable_goes_to_review():
    r = grade({"type": "matching", "pairs": [[0, 1]]},
              {"max_score": 1, "comparator": "matching"}, "", 1.0, selected_pairs=None)
    assert r["tier"] == "D" and r["reason_code"] == "matching_unreadable"


def test_matching_duplicate_pair_is_ambiguous():
    r = grade({"type": "matching", "pairs": [[0, 1]]},
              {"max_score": 1, "comparator": "matching"}, "", 1.0,
              selected_pairs=[[0, 1], [0, 1]])
    assert r["tier"] == "D" and r["reason_code"] == "matching_ambiguous"


def test_hmac_roundtrip_and_tamper():
    payload = sign_page("page-123")
    assert verify_page_payload(payload) == "page-123"
    assert verify_page_payload(payload.replace("page-123", "page-999")) is None
    assert verify_page_payload("garbage") is None

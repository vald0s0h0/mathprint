"""Tests unitaires du moteur déterministe (§12.4) : comparateurs, QCM, HMAC, oubli."""
import sys
from pathlib import Path

import pytest

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


def _qcm(correct, chosen, n=5, max_score=None, exclusive=False):
    """QCM à `n` cases, noté comme sur une vraie copie : max_score = une unité
    par case (échelle interne du moteur), `choices` présent."""
    g = {"max_score": n if max_score is None else max_score, "comparator": "qcm",
         "choices": [f"c{i}" for i in range(n)], "exclusive": exclusive}
    return grade({"type": "choice", "correct": correct}, g, "", 1.0,
                 selected_choices=chosen)


def test_qcm_multiple_credits_each_box():
    # 5 cases, 3 à cocher. L'élève en oublie une : les 4 autres cases sont bien
    # tranchées, il garde 4/5 des points (et non zéro).
    r = _qcm([0, 2, 3], [0, 2])
    assert r["score"] == pytest.approx(4.0)     # 4 cases justes sur 5
    assert r["tier"] == "A" and r["reason_code"] == "qcm_partial"


def test_qcm_unchecked_box_left_empty_on_purpose_earns_its_points():
    # Le cœur de la règle : une case qu'il ne fallait PAS cocher et que l'élève
    # a laissée vide est une réponse JUSTE, elle rapporte autant qu'une coche.
    # 4 cases, 1 seule à cocher (mais QCM multiple) : l'élève ne coche que la
    # bonne -> les 3 cases laissées vides à raison comptent aussi -> tout juste.
    assert _qcm([1], [1], n=4)["score"] == pytest.approx(4.0)
    # s'il coche la bonne ET une mauvaise, seule cette dernière est fausse
    assert _qcm([1], [1, 3], n=4)["score"] == pytest.approx(3.0)


def test_qcm_multiple_checking_everything_never_pays_full():
    # « je coche tout » : les cases à laisser vides deviennent autant de
    # décisions fausses -> 3 justes sur 5.
    assert _qcm([0, 2, 3], [0, 1, 2, 3, 4])["score"] == pytest.approx(3.0)


def test_qcm_multiple_all_correct_is_full_credit():
    r = _qcm([0, 2], [0, 2], n=4)
    assert r["score"] == 4 and r["reason_code"] == "qcm_match"


def test_qcm_blank_earns_nothing_despite_boxes_left_empty():
    # copie blanche : ne rien faire n'est pas répondre — surtout pas le crédit
    # des cases « bien laissées vides ».
    r = _qcm([0, 2], [], n=5)
    assert r["score"] == 0 and r["reason_code"] == "qcm_blank"


def test_qcm_single_stays_all_or_nothing():
    # choix EXCLUSIF : l'élève ne prend qu'UNE décision. Le compter case par
    # case donnerait la moitié des points à qui coche n'importe quoi (toutes
    # les cases à laisser vides le sont, sauf une).
    assert _qcm([1], [1], n=4, exclusive=True)["score"] == 4
    assert _qcm([1], [0], n=4, exclusive=True)["score"] == 0
    assert _qcm([1], [0], n=4, exclusive=True)["reason_code"] == "qcm_wrong"


def test_qcm_legacy_contract_without_choices_still_grades():
    # Contrat d'avant le comptage par case (pas de `choices`, max_score=1) :
    # repli sur les seules bonnes réponses, une coche à tort en annulant une.
    r = grade({"type": "choice", "correct": [0, 2, 3]},
              {"max_score": 1, "comparator": "qcm"}, "", 1.0,
              selected_choices=[0, 2])
    assert r["score"] == pytest.approx(2 / 3)


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


def test_table_cells_empty_counts_wrong_not_review():
    # case laissée VIDE (pas d'encre → jamais envoyée à Mathpix) : compte FAUX,
    # ne met PAS toute la réponse en revue (contraste avec "quatre" illisible).
    cells = [[{"type": "integer", "value": 4}, {"type": "integer", "value": 9}]]
    r = grade({"type": "table", "cells": cells},
              {"max_score": 2, "comparator": "table_cells", "cells": cells},
              "", 1.0, cell_texts=["4", ""])
    assert r["score"] == 1 and r["tier"] == "B" and r["reason_code"] == "table_mismatch"


def test_normalize_strips_mathpix_math_delimiters():
    from app.services.grading import normalize
    # Mathpix renvoie « \( 8 \) » : les délimiteurs doivent disparaître, sinon
    # « \(8\) » ne parse pas (incident "Motif : parse_error" sur une réponse juste).
    assert normalize(r"\( 8 \)") == "8"
    assert normalize(r"\[3{,}5\]") == "3.5"


def test_numeric_answer_with_mathpix_delimiters_is_accepted():
    r = grade({"type": "integer", "value": 8},
              {"max_score": 1, "comparator": "numeric"}, r"\( 8 \)", 0.99)
    assert r["score"] == 1 and r["reason_code"] == "numeric_match"


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


# ------------------------------------------------------- tolérance d'écriture
# L'élève écrit souvent PLUS que la réponse attendue (unité, membre de gauche).
# Ces réponses sont JUSTES : sans ce dépouillement elles ne se parsaient pas et
# partaient en revue professeur — et une seule d'entre elles suffisait à envoyer
# TOUT un tableau en revue avec 0 point.

def test_unit_and_left_member_do_not_make_the_answer_wrong():
    exp, pol = {"type": "integer", "value": 12}, {"max_score": 1, "comparator": "numeric"}
    for txt in ["12", "12 cm", "12cm", "12 €", "12 %", "= 12", "x = 12",
                "PGCD = 12", "12.", "$12$", "12 \\text{cm}"]:
        r = grade(exp, pol, txt, 0.95)
        assert r["score"] == 1, f"{txt!r} devrait être juste (obtenu {r})"


def test_expression_variable_is_not_mistaken_for_a_unit():
    """« 2m » est une expression (m = variable), pas « 2 mètres » : le
    dépouillement d'unité ne s'applique QU'AUX types numériques."""
    from app.services.grading import strip_answer_noise
    assert strip_answer_noise("12cm") == "12"
    r = grade({"type": "expression", "value": "2*m", "variable": "m"},
              {"max_score": 2, "comparator": "symbolic_equiv"}, "2m", 0.95)
    assert r["score"] == 2


def test_text_answer_tolerates_case_accents_and_final_dot():
    exp = {"type": "text", "value": "isocèle"}
    pol = {"max_score": 1, "comparator": "text_equal"}
    for txt in ["isocèle", "Isocèle", "isocele", "isocèle."]:
        assert grade(exp, pol, txt, 0.95)["score"] == 1, txt
    assert grade(exp, pol, "équilatéral", 0.95)["score"] == 0


# ------------------------------------------------------- crédit partiel (arrondi)

def test_correct_rounding_is_worth_half_the_points():
    """Un arrondi correct n'est pas une faute de calcul : demi-point, pas zéro."""
    exp = {"type": "decimal", "value": 0.6667}
    pol = {"max_score": 1, "comparator": "numeric"}
    assert grade(exp, pol, "0,6667", 0.95)["score"] == 1.0     # exact
    assert grade(exp, pol, "0,67", 0.95)["score"] == 0.5       # arrondi à 2 déc.
    assert grade(exp, pol, "0,666", 0.95)["score"] == 0.5      # troncature
    assert grade(exp, pol, "0,7", 0.95)["score"] == 0.5        # arrondi à 1 déc.
    assert grade(exp, pol, "0,5", 0.95)["score"] == 0.0        # faux
    assert grade(exp, pol, "0,67", 0.95)["reason_code"] == "numeric_rounded"


def test_more_precise_than_the_reference_is_fully_correct():
    """La référence est arrondie, pas la copie : « 0,6666 » ou « 2/3 » pour une
    attente à 0,67 valent le point PLEIN."""
    exp = {"type": "decimal", "value": 0.67}
    pol = {"max_score": 1, "comparator": "numeric"}
    assert grade(exp, pol, "0,6666", 0.95)["score"] == 1.0
    assert grade(exp, pol, "2/3", 0.95)["score"] == 1.0


def test_school_rounding_not_bankers():
    """0,25 arrondi à une décimale vaut 0,3 à l'école (pas 0,2 comme round())."""
    from fractions import Fraction
    from app.services.grading import numeric_credit
    assert numeric_credit("0.3", Fraction("0.3"), Fraction("0.25")) == 0.5


def test_one_unreadable_cell_no_longer_zeroes_the_whole_table():
    """Une case illisible met l'exercice en revue, mais le score partiel
    accompagne la décision : les cases justes ne sont plus jetées."""
    cells = [[{"type": "integer", "value": v}] for v in (2, 3, 5, 7)]
    pol = {"max_score": 4, "comparator": "table_cells", "cells": cells}
    exp = {"type": "table", "rows": 4, "cols": 1, "cells": cells}
    r = grade(exp, pol, "", 0.99, cell_texts=["2", "3", "5", "?!"])
    assert r["tier"] == "D" and r["reason_code"] == "table_cell_unreadable"
    assert r["score"] == 3.0


def test_table_cells_accept_units_and_partial_rounding():
    cells = [[{"type": "integer", "value": 7}], [{"type": "decimal", "value": 0.6667}]]
    pol = {"max_score": 2, "comparator": "table_cells", "cells": cells}
    exp = {"type": "table", "rows": 2, "cols": 1, "cells": cells}
    r = grade(exp, pol, "", 0.99, cell_texts=["7 cm", "0,67"])
    assert r["score"] == 1.5 and r["reason_code"] == "table_partial"

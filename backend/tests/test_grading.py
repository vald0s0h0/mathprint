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


def test_blank_is_zero_only_when_cv_is_confident():
    exp = {"type": "integer", "value": 10}
    pol = {"max_score": 1, "comparator": "numeric"}
    # Le blanc est une décision CV comme une autre : sûr -> zéro automatique ;
    # incertain -> professeur, sans pénaliser l'élève silencieusement.
    sure = grade(exp, pol, "", 0.95)
    assert sure["tier"] == "A" and sure["score"] == 0
    unsure = grade(exp, pol, "", 0.89)
    assert unsure["tier"] == "D" and unsure["reason_code"] == "ocr_low_confidence"


@pytest.mark.parametrize("confidence", [0.0, 0.4, 0.8999, None, "invalide"])
def test_every_comparator_rejects_confidence_below_90_percent(confidence):
    cases = [
        ({"type": "choice", "correct": [0]},
         {"comparator": "qcm", "max_score": 2, "choices": ["oui", "non"]},
         {"selected_choices": [0]}),
        ({"type": "grid"},
         {"comparator": "grid", "max_score": 1,
          "cols": ["V", "F"], "rows": [{"correct": 0}]},
         {"selected_choices": [0]}),
        ({"type": "matching", "pairs": [[0, 0]]},
         {"comparator": "matching", "max_score": 1},
         {"selected_pairs": [[0, 0]]}),
        ({"type": "table"},
         {"comparator": "table_cells", "max_score": 1,
          "cells": [[{"type": "integer", "value": 5}]]},
         {"cell_texts": ["5"]}),
    ]
    for expected, policy, kwargs in cases:
        r = grade(expected, policy, "", confidence, **kwargs)
        assert r["tier"] == "D"
        assert r["reason_code"] == "ocr_low_confidence"
        assert r["score"] == 0


def test_exactly_90_percent_is_eligible_for_automatic_grading():
    r = grade({"type": "integer", "value": 10},
              {"max_score": 1, "comparator": "numeric"}, "10", 0.90)
    assert r["tier"] == "A" and r["score"] == 1


def test_table_and_grid_repair_stale_internal_max_score_from_structure():
    table = grade(
        {"type": "table"},
        {"comparator": "table_cells", "max_score": 99,
         "cells": [[{"type": "integer", "value": 2},
                    {"type": "integer", "value": 3}]]},
        "", 0.95, cell_texts=["2", "4"])
    assert table["score"] == 1 and table["max_score"] == 2

    grid = grade(
        {"type": "grid"},
        {"comparator": "grid", "max_score": 99, "cols": ["V", "F"],
         "rows": [{"correct": 0}, {"correct": 1}]},
        "", 0.95, selected_choices=[0, 0])
    assert grid["score"] == 1 and grid["max_score"] == 2


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


def test_matching_one_wrong_link_keeps_credit_for_every_correct_link():
    r = grade({"type": "matching", "pairs": [[0, 2], [1, 0], [2, 1]]},
              {"max_score": 3, "comparator": "matching"}, "", 1.0,
              selected_pairs=[[0, 2], [1, 0], [2, 3]])
    assert r["score"] == 2
    assert r["max_score"] == 3
    assert r["tier"] == "B" and r["reason_code"] == "matching_partial"


def test_matching_repairs_legacy_max_score_to_one_unit_per_link():
    """Un ancien exercice enregistré avec max_score=1 ne doit ni plafonner
    trop tôt, ni transformer une réponse partielle en tout-ou-rien."""
    r = grade({"type": "matching", "pairs": [[0, 0], [1, 1], [2, 2]]},
              {"max_score": 1, "comparator": "matching"}, "", 1.0,
              selected_pairs=[[0, 0], [1, 1]])
    assert r["score"] == 2
    assert r["max_score"] == 3


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
    assert payload.startswith("M2:")
    assert verify_page_payload(payload[:-1] + ("A" if payload[-1] != "A" else "B")) is None
    assert verify_page_payload("garbage") is None


def test_compact_uuid_qr_keeps_hmac_strength_and_legacy_compatibility():
    page_id = "12345678-1234-5678-1234-567812345678"
    payload = sign_page(page_id)
    # MP1 faisait 57 caractères pour un UUID. Le nouveau format Base32 reste
    # alphanumérique QR et conserve les mêmes 64 bits de signature HMAC.
    assert len(payload) == 43
    assert verify_page_payload(payload) == page_id

    import hashlib
    import hmac
    from app.config import settings
    legacy_sig = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).hexdigest()[:16]
    assert verify_page_payload(f"MP1|{page_id}|{legacy_sig}") == page_id


def test_compact_uuid_token_starting_with_text_marker_is_not_misdecoded():
    """Régression aléatoire (~1 UUID/32) : un UUID dont le Base32 commence par
    T reste un UUID, pas un ancien identifiant texte préfixé par T."""
    page_id = "98000000-0000-4000-8000-000000000000"
    payload = sign_page(page_id)
    assert payload.startswith("M2:T")
    assert verify_page_payload(payload) == page_id


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

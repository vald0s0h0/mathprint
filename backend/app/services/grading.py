"""Moteur de correction déterministe (§6.4, §6.5).

Priorité au déterminisme : les égalités numériques et symboliques sont
traitées sans LLM. La chaîne OCR originale est conservée telle quelle ;
la normalisation travaille sur une copie. Jamais de comparaison de LaTeX brut.

Retourne (tier, score, confidence, reason_code) :
  A/B -> validation automatique ; C -> LLM ; D -> file professeur ; E -> blocage.
"""
import re
from fractions import Fraction

import sympy
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application,)


def normalize(raw: str) -> str:
    """Normalisation FR -> canonique : virgule décimale, ×, −, espaces, LaTeX simple."""
    s = raw.strip()
    s = s.replace("\\times", "*").replace("×", "*").replace("÷", "/")
    s = s.replace("−", "-").replace("–", "-")
    s = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", s)
    s = s.replace("\\left", "").replace("\\right", "").replace("$", "")
    s = re.sub(r"\\text\{[^}]*\}", "", s)
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)   # virgule décimale française
    s = re.sub(r"\s+", "", s)
    # réponse du type "x=5" -> garder le membre droit pour une solution demandée
    return s


def _parse_number(s: str) -> Fraction | None:
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            return Fraction(int(num.strip("()")), int(den.strip("()")))
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def _extract_answer_side(s: str, variable: str | None) -> str:
    """Pour 'x=5' ne garder que '5' quand on attend la solution d'une équation."""
    if variable and "=" in s:
        left, right = s.split("=", 1)
        if left.replace(" ", "") == variable:
            return right
    return s


def grade(expected: dict, grading: dict, ocr_text: str, ocr_confidence: float,
          selected_choices: list[int] | None = None,
          cell_texts: list[str] | None = None,
          selected_pairs: list[list[int]] | None = None) -> dict:
    """Décision déterministe. Ne choisit jamais en cas d'ambiguïté (RM-005)."""
    max_score = float(grading.get("max_score", 1))
    comparator = grading.get("comparator", "numeric")
    result = {"max_score": max_score, "score": 0.0, "tier": "D",
              "confidence": ocr_confidence, "reason_code": "unresolved"}

    # --- sans réponse structurée (manual_drawing, tracé/dessin) : toujours
    # revue professeur, même vide — jamais de score deviné sur une planche
    # blanche (§ tracés géométriques, correction humaine obligatoire) ---
    if comparator == "manual":
        result.update(tier="D", reason_code="no_structured_answer")
        return result

    # --- points à relier : détection CV du trait, jamais de choix deviné ---
    if comparator == "matching":
        expected_pairs = {tuple(p) for p in expected.get("pairs", [])}
        if selected_pairs is None:
            result.update(tier="D", reason_code="matching_unreadable")
            return result
        got_pairs = [tuple(p) for p in selected_pairs]
        if len(set(got_pairs)) != len(got_pairs):
            result.update(tier="D", reason_code="matching_ambiguous")
            return result
        got_set = set(got_pairs)
        score = float(len(got_set & expected_pairs))
        ok = got_set == expected_pairs
        result.update(tier="B", score=score, confidence=1.0,
                      reason_code="matching_match" if ok else "matching_partial")
        return result

    # --- tableau à remplir : une comparaison numérique/texte par cellule ---
    if comparator == "table_cells":
        flat_expected = [cell for row in (grading.get("cells") or []) for cell in row]
        if cell_texts is None or len(cell_texts) != len(flat_expected):
            result.update(tier="D", reason_code="table_unreadable")
            return result
        score = 0.0
        for exp_cell, raw_cell in zip(flat_expected, cell_texts):
            norm = normalize(raw_cell or "")
            if exp_cell["type"] == "text":
                ok = norm.casefold() == normalize(str(exp_cell["value"])).casefold()
            else:
                got = _parse_number(norm)
                if got is None:
                    result.update(tier="D", reason_code="table_cell_unreadable")
                    return result
                ok = got == Fraction(str(exp_cell["value"]))
            score += 1.0 if ok else 0.0
        tier = "A" if score == len(flat_expected) else "B"
        result.update(tier=tier, score=score, confidence=1.0,
                      reason_code="table_match" if score == len(flat_expected)
                      else "table_mismatch")
        return result

    # --- QCM : purement déterministe (CV local, pas de LLM) ---
    if comparator == "qcm":
        correct = set(expected.get("correct", []))
        if selected_choices is None:
            result.update(tier="D", reason_code="qcm_unreadable")
            return result
        chosen = set(selected_choices)
        if len(chosen) == 0:
            result.update(tier="A", score=0.0, confidence=1.0, reason_code="qcm_blank")
        elif expected.get("type") == "choice" and len(chosen) > 1 and len(correct) == 1:
            # double coche interdite -> exception, jamais un choix arbitraire (§4.3)
            result.update(tier="D", reason_code="qcm_double_check")
        else:
            score = max_score if chosen == correct else 0.0
            result.update(tier="A", score=score, confidence=1.0,
                          reason_code="qcm_match" if score else "qcm_wrong")
        return result

    if not ocr_text.strip():
        result.update(tier="A", score=0.0, confidence=1.0, reason_code="blank")
        return result

    if ocr_confidence < 0.55:
        result.update(tier="D", reason_code="ocr_low_confidence")
        return result

    norm = normalize(ocr_text)
    norm = _extract_answer_side(norm, expected.get("variable"))
    etype = expected.get("type")

    if comparator == "text_equal" or etype == "text":
        want_txt = normalize(str(expected.get("value") or ""))
        ok = norm.casefold() == want_txt.casefold()
        result.update(tier="B" if ok else "C",
                      score=max_score if ok else 0.0,
                      reason_code="text_match" if ok else "text_mismatch")
        # texte différent : ambiguïté possible (accents, notation) -> tier C/D
        if not ok:
            result.update(tier="D", reason_code="text_mismatch")
        return result

    try:
        if etype in ("integer", "decimal") or comparator in ("numeric", "equation_solution"):
            got = _parse_number(norm)
            want = (Fraction(*expected["value"]) if etype == "rational"
                    else Fraction(str(expected["value"])))
            if got is None:
                # tenter une évaluation symbolique du texte (ex: "2+3")
                got_expr = parse_expr(norm, transformations=TRANSFORMS)
                if got_expr.is_number:
                    got = Fraction(str(sympy.nsimplify(got_expr)))
            if got is None:
                result.update(tier="C" if comparator == "equation_solution" else "D",
                              reason_code="parse_failed")
                return result
            ok = got == want
            tier = "A" if ocr_confidence >= 0.85 else "B"
            result.update(tier=tier, score=max_score if ok else 0.0,
                          reason_code="numeric_match" if ok else "numeric_mismatch")
            return result

        if etype == "rational" or comparator == "rational_equiv":
            got = _parse_number(norm)
            want = Fraction(*expected["value"])
            if got is None:
                result.update(tier="D", reason_code="parse_failed")
                return result
            ok = got == want
            result.update(tier="B", score=max_score if ok else 0.0,
                          reason_code="rational_equiv" if ok else "rational_mismatch")
            return result

        if etype == "expression" or comparator == "symbolic_equiv":
            var = sympy.Symbol(expected.get("variable", "x"))
            got_e = parse_expr(norm, transformations=TRANSFORMS, local_dict={str(var): var})
            want_e = parse_expr(expected["value"], transformations=TRANSFORMS, local_dict={str(var): var})
            ok = sympy.simplify(got_e - want_e) == 0
            result.update(tier="B", score=max_score if ok else 0.0,
                          reason_code="symbolic_equiv" if ok else "symbolic_mismatch")
            return result

        # type non couvert par le parseur -> refuser la comparaison (§6.5)
        result.update(tier="C", reason_code="type_not_covered")
        return result

    except Exception:
        # Réponse multiligne / ambiguë -> rubrique LLM ou revue
        tier = "C" if grading.get("rubric") else "D"
        result.update(tier=tier, reason_code="parse_error")
        return result

"""ocr_surface.to_surface : uniformise la FORME, jamais la valeur."""
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr_surface import to_surface as s

N, M, T, L = "num_line", "math_line", "text_line", "multiline_line"


# ---- la valeur ne change jamais ------------------------------------------

@pytest.mark.parametrize("a,b,cls", [
    ("1,79", "1,8", N), ("3/6", "1/2", N), ("6,0", "6", N), ("0,50", "0,5", N),
    ("06", "6", N), ("1,0", "1", N), ("\\frac{3}{6}", "\\frac{1}{2}", M), ("0,10", "0,1", N),
    ("12", "21", N), ("2/3", "3/2", N), ("1,79", "179", N), ("-4", "4", N),
    ("Vrai", "vrai", T), ("Faux", "faux", T), ("É", "E", T), ("a", "A", M),
    ("2x", "x2", M), ("x", "×", M), ("0,5", "1/2", N), ("5", "5,", N),
])
def test_different_values_stay_different(a, b, cls):
    assert s(a, cls) != s(b, cls)


# ---- les formes équivalentes se rejoignent --------------------------------

@pytest.mark.parametrize("a,b,cls", [
    ("\\frac{3}{6}", "3/6", N), ("\\dfrac{3}{6}", "3/6", N), ("\\tfrac{3}{6}", "3/6", M),
    ("\\frac{1}{2}", "1/2", M), ("\\frac{-3}{4}", "-3/4", M),
    ("\\frac{x+1}{2}", "(x+1)/2", M), ("\\frac{a}{b+c}", "a/(b+c)", M),
    ("−4", "-4", N), ("–4", "-4", N), ("−12", "-12", N),
    ("x²", "x^{2}", M), ("x²", "x^2", M), ("x^{2}", "x^2", M), ("x ^ { 2 }", "x²", M),
    ("m³", "m^3", M), ("x^{10}", "x^10", M), ("x^{n+1}", "x^{n+1}", M),
    ("2 × 3", "2\\times3", M), ("2*3", "2×3", M), ("2 \\cdot 3", "2×3", M), ("2·3", "2×3", M),
    ("2 \\times x", "2×x", M), ("2 ^ { 2 } \\times 3", "2²×3", M),
    ("1 000", "1000", N), ("1\u00a0000", "1000", N), ("1\u202f000", "1000", N),
    ("1.79", "1,79", N), ("1{,}79", "1,79", N), ("0.5", "0,5", N),
    ("$12$", "12", N), ("\\(12\\)", "12", N), ("$$12$$", "12", N), ("\\left(2+3\\right)", "(2+3)", M),
    ("\\text{cm}", "cm", T), ("\\mathrm{kg}", "kg", T), ("12\\,\\text{cm}", "12cm", M),
    ("\\sqrt{16}", "√16", M), ("\\sqrt{x+1}", "√(x+1)", M),
    ("\\pi", "π", M), ("\\approx", "≈", M), ("\\div", "÷", M),
    ("2 4", "24", N), ("1 0 5", "105", N), ("\\( 24 \\)", "24", N),
    ("d’après", "d'après", T),
])
def test_equivalent_forms_meet(a, b, cls):
    assert s(a, cls) == s(b, cls), (s(a, cls), s(b, cls))


def test_exact_canonical_forms():
    assert s("\\frac{3}{6}", N) == "3/6"
    assert s("x²", M) == "x^2" and s("m³", M) == "m^3"
    assert s("2 \\times 3", M) == "2×3"
    assert s("1.79", N) == "1,79" and s("1{,}79", N) == "1,79"
    assert s("−4", N) == "-4"
    assert s("\\frac{x+1}{2}", M) == "(x+1)/2"


def test_unknown_marker_symbols_are_never_rewritten_into_operators():
    """Un `□` ou `\\square` n'est pas un `×` : il reste tel quel (la lecture est douteuse)."""
    assert "×" not in s("2□3", M) and "×" not in s("2 \\square 3", M)
    assert s("2□3", M) != s("2×3", M)
    assert s("\\boxed{2}", M) != s("2", M)


# ---- français --------------------------------------------------------------

def test_accents_cedilla_and_oe_ligature_are_kept_in_nfc():
    assert s("é", T) == "é" and s("ç", T) == "ç" and s("œ", T) == "œ" and s("Œ", T) == "Œ"
    nfd = unicodedata.normalize("NFD", "été")
    assert nfd != "été" and s(nfd, T) == "été"
    assert s("œuf", T) == "œuf" and s("œuf", T) != s("oeuf", T)
    assert s("ça marche", T) == "ça marche"


def test_case_is_kept():
    assert s("Vrai", T) == "Vrai" and s("vrai", T) == "vrai" and s("Vrai", T) != s("vrai", T)


# ---- espaces par classe ------------------------------------------------------

def test_spaces_per_class():
    assert s("1 000", N) == "1000" and s("3 / 6", N) == "3/6"
    assert s("2 + 3", M) == "2+3"
    assert s("  le   triangle \n est  ", T) == "le triangle est"
    assert s("on  calcule\u00a0BC²", L) == "on calcule BC^2"
    assert s("", N) == "" and s(None, N) == "" and s("   ", T) == ""


def test_zero_forms_untouched():
    for raw in ("06", "6,0", "0,50", "0,05", "10,0", "007"):
        assert s(raw, N) == raw


def test_idempotent():
    for raw in ("\\frac{3}{6}", "x²", "2 ^ { 2 } \\times 3", "1.79", "$12$", "−4", "d’après", "\\sqrt{x+1}"):
        for cls in (N, M, T):
            once = s(raw, cls)
            assert s(once, cls) == once, (raw, cls, once)


def test_text_wrapper_inside_math_keeps_the_words():
    assert s("12 \\text{ cm}", M) == "12cm"
    assert s("\\text{Vrai}", T) == "Vrai"


def test_degree_forms_are_one_reading():
    # lu sur une vraie copie (3e, trigonométrie) : trois moteurs d'accord semblaient diverger
    for raw in ("62^{\\circ}", "62^°", "62^{°}", "62°", "62 °", "62º", "62\\degree"):
        assert s(raw, M) == "62°", raw
    assert s("2,8^{\\circ}", M) == "2,8°"
    assert s("28°", M) != s("2,8°", M)                       # la valeur reste distincte
    assert s("x^{2}", M) == "x^2"                             # un vrai exposant reste un exposant

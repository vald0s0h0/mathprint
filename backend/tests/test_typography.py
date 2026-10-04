"""Typographie française des exercices publiés (services/typography) et son
respect au rendu (pdfgen).

Le défaut d'origine : les textes étaient bien ponctués à l'œil (« Combien ? »,
« $5$ cm », « $a$ ; $b$ ») mais avec des espaces ORDINAIRES — le PDF pouvait
donc couper la ligne juste avant « ? », « ; », « cm ». Deux familles de tests :
ce que la passe écrit dans le texte, et ce que le moteur de cartes en fait.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import pdfgen, typography
from app.services.typography import NBSP, NNBSP, french


# ------------------------------------------------------------------ le texte

@pytest.mark.parametrize("raw, expected", [
    ("Combien de tours ?", f"Combien de tours{NNBSP}?"),
    ("Vrai ou faux!", f"Vrai ou faux{NNBSP}!"),
    ("Calcule : $3+4$", f"Calcule{NBSP}: $3+4$"),
    ("$105^\\circ$ ; $65^\\circ$", f"$105^\\circ${NNBSP}; $65^\\circ$"),
    ("Quels nombres $d$ et $n$ conviennent ?", f"Quels nombres $d$ et $n$ conviennent{NNBSP}?"),
    ("$10$ cm", f"$10${NBSP}cm"),
    ("AB = 5 cm", f"AB{NBSP}= 5{NBSP}cm"),
    ("Il coûte 5€, soit 20%.", f"Il coûte 5{NBSP}€, soit 20{NBSP}%."),
    ("Il y a {{blank}} cm.", f"Il y a {{{{blank}}}}{NBSP}cm."),
    ("Combien vaut {{blank}} ?", f"Combien vaut {{{{blank}}}}{NNBSP}?"),
    ("« modulo »", f"«{NNBSP}modulo{NNBSP}»"),
    ('le bloc "avancer"', f"le bloc «{NNBSP}avancer{NNBSP}»"),
    ("C'est l'aire d'un carré.", "C’est l’aire d’un carré."),
    ("Le 2ème angle, la 1ère fois", "Le 2e angle, la 1re fois"),
    ("Les nombres sont ...", "Les nombres sont …"),
    ("Il y a 3 élèves .", "Il y a 3 élèves."),
    ("Rose,jaune;vert", f"Rose, jaune{NNBSP}; vert"),
    ("Un angle de 90°.", "Un angle de 90°."),                 # degré d'angle collé
])
def test_french_rules(raw, expected):
    assert french(raw) == expected


def test_math_rules():
    assert french("$12 500$") == "$12\\,500$"                 # sinon « 12500 »
    assert french("$3,5$") == "$3{,}5$"                       # sinon « 3, 5 »
    assert french("$(2;3)$") == "$(2\\,;\\,3)$"
    assert french("$5\\%$") == "$5\\,\\%$"
    assert french("$\\;x$") == "$\\;x$"                       # \; est une commande


def test_geometry_primes_and_markers_are_untouched():
    assert french("Le point A' est l'image de A.") == "Le point A' est l’image de A."
    for marker in ("{{blank}}", "{{mini}}", "{{blank_right}}", "{{figure}}"):
        assert marker in french(f"Écris {marker} ici.")
    assert french("{{aide}} Rappel : 180°").startswith("{{aide}} Rappel")


def test_table_separator_rows_are_untouched():
    table = "| Étape | Instruction |\n|:---|---:|\n| 1 | Avance : 50 pas |"
    out = french(table).split("\n")
    assert out[1] == "|:---|---:|"
    assert out[2] == f"| 1 | Avance{NBSP}: 50 pas |"


@pytest.mark.parametrize("raw", [
    "Combien de tours ? $x$ ; $y$ : « a » 5 cm {{blank}} ?",
    "Calcule $12 500 + 3,5$ et $(2;3)$.", "| a | b |\n|---|---|\n| 1 ; 2 | x |",
])
def test_french_is_idempotent(raw):
    once = french(raw)
    assert french(once) == once


def test_record_rewrites_displayed_texts_only():
    rec = {"id": "x ?", "statement": "Combien ?", "title": "Partage :",
           "expected": {"parts": [{"statement": "Vrai ?", "expected": {
               "type": "grid", "cols": ["Vrai", "Faux"],
               "rows": [{"label": "Rose : jaune", "correct": 0}]},
               "grading": {"choices": ["2 ; 5", "3 cm"]}}]},
           "figure_json": {"params": {"texts": [{"text": "A ?"}]}}}
    out = typography.apply_to_record(rec)
    part = out["expected"]["parts"][0]
    assert out["id"] == "x ?"                                 # identifiant intact
    assert out["figure_json"] == rec["figure_json"]           # figure intacte
    assert out["statement"] == f"Combien{NNBSP}?"
    assert out["title"] == f"Partage{NBSP}:"
    assert part["statement"] == f"Vrai{NNBSP}?"
    assert part["expected"]["rows"][0]["label"] == f"Rose{NBSP}: jaune"
    assert part["expected"]["rows"][0]["correct"] == 0
    assert part["grading"]["choices"] == [f"2{NNBSP}; 5", f"3{NBSP}cm"]


# ------------------------------------------------------------------ le rendu

def _lines(text: str, width: float) -> list[list[tuple]]:
    return [ln["segs"] for ln in pdfgen._rich_layout(text, width, 9)["lines"]]


def _words(segs: list[tuple]) -> list[str]:
    return [s[1] for s in segs if s[0] == "word"]


def test_a_non_breaking_space_welds_its_neighbours():
    segs = pdfgen._paragraph_segs(f"Combien de tours{NNBSP}?", 9, 9)
    assert _words(segs) == ["Combien", "de", "tours ?"]


@pytest.mark.parametrize("width", [w * 1.0 for w in range(70, 260, 3)])
def test_punctuation_never_opens_a_line(width):
    """Quelle que soit la largeur, ni « ? » ni « ; » ni « cm » n'ouvrent une
    ligne — y compris collés à une formule ou à une case. (Seule exception,
    inévitable : une chaîne soudée plus large que la ligne elle-même — la case
    seule fait déjà 20 mm.)"""
    text = french("Quels nombres $d$ et $n$ conviennent ? $105^\\circ$ ; $65^\\circ$ "
                  "et $10$ cm, puis {{blank}} ?")
    for segs in _lines(text, width)[1:]:
        first = segs[0]
        if first[0] == "word":
            assert not first[1].lstrip().startswith(("?", ";", "cm")), (width, _words(segs))


def test_punctuation_after_a_blank_stays_on_its_line():
    for width in range(66, 260, 2):                          # case : 20 mm ≈ 57 pt
        for segs in _lines("Il y a {{blank}}.", float(width))[1:]:
            assert not (segs[0][0] == "word" and segs[0][1].strip() == ".")


# ------------------------------------------------------- titre des problèmes

def test_problem_title_is_bold_on_the_card():
    """Le titre d'un problème/énigme Indigo précède l'énoncé EN GRAS — même
    balisage `**` que partout (services/blocks), lu par le PDF et le web."""
    from types import SimpleNamespace
    from app.services import blocks, generation
    row = SimpleNamespace(source="indigo", statement="Malo prend un quart.",
                          raw_extract_json={"indigo": {"badge_type": "probleme",
                                                       "title": "Partage"}})
    shown, _calc, is_probleme = generation.indigo_display(row)
    assert is_probleme and shown == "**Partage**\nMalo prend un quart."
    lay = pdfgen._rich_layout(shown, 200.0, 9)
    title = lay["lines"][0]["segs"]
    assert [s[1] for s in title] == ["Partage"] and all(s[2] for s in title)
    assert blocks.strip_bold(shown).startswith("Partage\n")

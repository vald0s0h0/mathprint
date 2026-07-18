"""Mise en lignes des énoncés : le saut de ligne est une DONNÉE, elle doit
survivre au trajet complet modèle -> validateur -> banque -> PDF.

Le bug d'origine : « ... en quatre jours : - Jour 1 : - Jour 2 : - Jour 3 :
Coche toutes les affirmations » — le modèle mettait bien son énoncé en lignes,
et le rendu les repliait toutes en espaces (_rich_layout découpait sur
`.split()`, qui ne distingue pas l'espace du saut de ligne). Le sens de la
liste était perdu à l'impression, et nulle part ailleurs : l'énoncé stocké,
lui, était correct. D'où deux familles de tests ici — ce que `statement`
garantit sur le texte, et ce que `pdfgen` en fait réellement à la mise en page.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Competency
from app.services import exercise_gen, pdfgen, statement
from app.services.pdfgen import COL_W, CARD_PAD

WIDTH = COL_W - 2 * CARD_PAD


# ------------------------------------------------------------- le prompt

def _contract() -> str:
    return exercise_gen.format_contract("INTRO")


def test_the_shared_contract_demands_line_breaks():
    """Les deux pipelines (création Gemini, adaptation Sésamaths) lisent le
    MÊME contrat, et c'est `statement.normalize` — partagée elle aussi — qui le
    fait respecter. Un prompt qui ne demanderait pas les sauts produirait des
    énoncés d'un seul tenant que rien ne peut recouper après coup."""
    contract = _contract()
    assert "MISE EN LIGNE DE L'ÉNONCÉ (obligatoire)" in contract
    assert "\\n dans le JSON" in contract
    assert "une donnée par ligne" in contract
    # la règle des sous-questions est explicite et sans échappatoire
    assert "avant CHAQUE sous-question a., b., c. — sans exception" in contract
    # ...et montrée sur l'énoncé exact du rapport de bug
    assert "Un randonneur parcourt un sentier de grande randonnée en quatre jours" in contract


def test_the_shared_contract_teaches_how_to_word_a_blank_sentence():
    """« Le nombre de plaques qu'il peut remplir est de (...) » n'est pas clair ;
    « Le boulanger peut remplir (...) plaques » l'est. Une règle de LANGUE : le
    validateur ne peut pas la vérifier, seuls les exemples la portent."""
    contract = _contract()
    assert "RÉDACTION DES PHRASES À TROUS" in contract
    assert "Le boulanger peut remplir {{blank}} plaques de cuisson." in contract
    assert "Il restera {{blank}} croissant(s) sur la dernière plaque." in contract
    assert "Il doit utiliser au minimum {{blank}} plaques pour cuire tous les croissants." in contract
    # la tournure à bannir est nommée, pas seulement montrée
    assert "jamais une périphrase construite autour de « Le nombre de… est de »" in contract


# --------------------------------------------------- validation -> banque

def _validated(statement_text: str, **over) -> dict | None:
    """Passe un exercice candidat par le validateur partagé, comme le ferait
    n'importe quelle pipeline. Aucune base : `competency` n'est lu que pour son
    domaine, et le dédoublonnage tient dans le set fourni."""
    comp = Competency(code="A1.1", short_id="A1.1", label="Opérations",
                      domain_code="A", domain_name="Nombres et calculs",
                      chapter_code="A1", chapter_name="Opérations", order_index=0)
    raw = {"kind": "application", "effort_points": 1, "statement": statement_text,
           "correction": "On additionne : le total vaut $5$.",
           "response_type": "short_text",
           "answer": {"type": "integer", "value": 5}}
    raw.update(over)
    return exercise_gen._validate_exercise(raw, comp, None, set())


def test_a_created_exercise_reaches_the_bank_already_laid_out():
    """« cette information saut de ligne doit clairement passer à travers la
    pipeline » : le `\\n` est stocké tel quel, il n'est pas une affaire de rendu."""
    ex = _validated("Trois nombres :\n- $2$\n- $3$\nCalcule leur somme.")
    assert ex["statement"] == "Trois nombres :\n- $2$\n- $3$\nCalcule leur somme."


def test_the_validator_lays_out_subquestions_the_model_ran_together():
    ex = _validated("Calcule. a) $2+3$ b) $1+4$")
    assert ex["statement"] == "Calcule.\na) $2+3$\nb) $1+4$"


def test_two_statements_differing_only_by_line_breaks_are_one_duplicate():
    """Le dédoublonnage tourne sur la forme normalisée : sinon le même exercice,
    écrit une fois en lignes et une fois d'un seul tenant, entrerait deux fois
    en banque."""
    comp = Competency(code="A1.1", short_id="A1.1", label="Opérations",
                      domain_code="A", domain_name="Nombres et calculs",
                      chapter_code="A1", chapter_name="Opérations", order_index=0)
    raw = {"kind": "application", "effort_points": 1,
           "statement": "Calcule. a) $2+3$ b) $1+4$",
           "correction": "On additionne : le total vaut $5$.",
           "response_type": "short_text", "answer": {"type": "integer", "value": 5}}
    seen: set[str] = set()
    assert exercise_gen._validate_exercise(dict(raw), comp, None, seen) is not None
    already_laid_out = dict(raw, statement="Calcule.\na) $2+3$\nb) $1+4$")
    assert exercise_gen._validate_exercise(already_laid_out, comp, None, seen) is None


# ------------------------------------------------------- format du texte

def test_line_breaks_survive_normalization():
    """Le cas du rapport de bug, tel que le modèle doit l'écrire."""
    raw = ("Un randonneur parcourt un sentier de grande randonnée en quatre jours :\n"
           "- Jour 1 : $12{,}4\\ \\text{km}$\n- Jour 2 : $9{,}8\\ \\text{km}$\n"
           "Coche toutes les affirmations qui sont exactes.")
    assert statement.normalize(raw) == raw


def test_normalize_is_idempotent():
    raw = "Contexte.\na. Première question ?\nb. Seconde question ?"
    once = statement.normalize(raw)
    assert statement.normalize(once) == once


def test_normalize_strips_blank_lines_and_trailing_spaces():
    got = statement.normalize("Contexte :   \n\n\n- une donnée  \n\nConclusion.\n\n")
    assert got == "Contexte :\n- une donnée\nConclusion."


def test_normalize_accepts_windows_line_endings():
    assert statement.normalize("Un.\r\nDeux.") == "Un.\nDeux."


def test_normalize_repairs_a_blank_marker_stuck_inside_a_formula():
    """Le rapport de bug : le modèle a glissé la case dans une formule, où
    « blank » s'imprimait en italique au lieu d'une case. On ressort la case du
    $...$ en marqueur propre (« $85blank$ » -> « $85${{blank}} »)."""
    got = statement.normalize("Le nombre $85blank$ est divisible par 5 mais pas par 10.")
    assert got == "Le nombre $85${{blank}} est divisible par 5 mais pas par 10."
    assert statement.BLANK_TOKEN in got


def test_normalize_repairs_bare_and_single_brace_blank_markers():
    assert statement.normalize("Il reste 85blank croissants.") == \
        "Il reste 85{{blank}} croissants."
    assert statement.normalize("Il reste {blank} croissants.") == \
        "Il reste {{blank}} croissants."
    # un « {{blank}} » déjà correct n'est pas retouché, et « blanket » non plus
    assert statement.normalize("Une case {{blank}} propre.") == "Une case {{blank}} propre."
    assert statement.normalize("Le mot blanket reste intact.") == "Le mot blanket reste intact."


def test_a_subquestion_always_opens_its_own_line():
    """« toujours sauter la ligne pour a, b, c » — même si le modèle l'oublie."""
    got = statement.normalize("Calcule les sommes suivantes. a) $2+3$ b) $4+5$ c) $6+7$")
    assert got == "Calcule les sommes suivantes.\na) $2+3$\nb) $4+5$\nc) $6+7$"


def test_subquestion_break_needs_a_real_sequence():
    """Un « a. » isolé au fil d'une phrase n'est pas une sous-question : le
    repérage est séquentiel (a, puis b…) et exige au moins deux étiquettes,
    sinon on couperait en plein milieu du français."""
    for prose in ("Il y a. Le compte est bon.",
                  "Range les nombres de a) à d) dans l'ordre croissant.",
                  "Le point b) est le seul exact."):
        assert statement.normalize(prose) == prose


def test_subquestion_break_ignores_math_spans():
    """« $f(a) = 3$ » n'ouvre pas une sous-question « a »."""
    raw = "On donne $f(a) = 3$ et $g(b) = 4$. Calcule la somme."
    assert statement.normalize(raw) == raw


def test_statement_starting_on_a_subquestion_keeps_no_leading_blank_line():
    got = statement.normalize("a. $2+3$ b. $4+5$")
    assert got == "a. $2+3$\nb. $4+5$"


def test_subquestion_label_is_split_off_the_text():
    assert statement.subquestion_label("a. Calcule $2+3$") == ("a", "Calcule $2+3$")
    assert statement.subquestion_label("b) Combien reste-t-il ?") == ("b", "Combien reste-t-il ?")
    assert statement.subquestion_label("Calcule $2+3$") is None


# ------------------------------------------------------- mise en page PDF

def _lines(text: str, **kw) -> list[dict]:
    return pdfgen._rich_layout(text, WIDTH, 9, **kw)["lines"]


def _words(line: dict) -> str:
    return " ".join(s[1] for s in line["segs"] if s[0] == "word")


def test_rich_layout_breaks_hard_on_newlines():
    """Le coeur du bug : trois lignes logiques courtes tiennent largement sur
    une ligne de rendu, et ne doivent SURTOUT pas y être rassemblées."""
    lines = _lines("Contexte.\n- Jour 1\n- Jour 2\nConclusion.")
    assert [_words(ln) for ln in lines] == ["Contexte.", "- Jour 1", "- Jour 2", "Conclusion."]


def test_a_newline_is_never_replayed_as_a_space():
    """Régression exacte du rapport : les lignes ne se recollent pas."""
    lines = _lines("Quatre jours :\n- Jour 1 :\n- Jour 2 :\nCoche les affirmations exactes.")
    assert not any("Jour 1 : - Jour 2" in _words(ln) for ln in lines)


def test_a_long_logical_line_still_wraps():
    """Le saut de ligne dur s'AJOUTE au repli automatique, il ne le remplace pas."""
    lines = _lines("mot " * 200 + "\nfin.")
    assert len(lines) > 2
    assert _words(lines[-1]) == "fin."


def test_a_blank_line_grows_but_the_rest_of_the_statement_does_not():
    """« l'énoncé dans la taille par défaut, les phrases à trous plus grandes »."""
    lines = _lines("Un carton contient 12 billes.\n5 cartons contiennent {{blank}} billes.",
                   blank_fs=11)
    assert [ln["fs"] for ln in lines] == [9, 11]


def test_without_blank_fs_nothing_grows():
    lines = _lines("Une phrase avec {{blank}} dedans.")
    assert [ln["fs"] for ln in lines] == [9]


def test_blank_box_is_8mm_high():
    """Contrainte physique (la main de l'élève), pas une proportion du texte :
    elle ne bouge pas quand le corps du gabarit change."""
    from reportlab.lib.units import mm
    for fs in (7, 9, 12):
        (blank,) = [s for s in pdfgen._paragraph_segs("{{blank}}", fs, fs) if s[0] == "blank"]
        _, w, asc, desc, _glue = blank
        assert w == pytest.approx(20 * mm)
        assert asc + desc == pytest.approx(8 * mm)


def test_subquestion_gets_a_badge_coloured_by_the_exercise_level():
    from app.services.pdfgen import DIFFICULTY_COLORS
    lines = _lines("Contexte.\na. Première ?\nb. Seconde ?",
                   sub_badge_color=DIFFICULTY_COLORS[5])
    assert [ln["badge"] for ln in lines] == [None, "a", "b"]
    assert all(ln["badge_color"] == DIFFICULTY_COLORS[5] for ln in lines[1:])
    # l'étiquette quitte le flot de texte : elle est dessinée dans la pastille
    assert _words(lines[1]) == "Première ?"


def test_subquestions_are_plain_text_when_no_colour_is_given():
    """QCM, libellés de colonne, rappels de leçon : pas de pastille — le
    paramètre est ce qui distingue « énoncé d'exercice » du reste."""
    lines = _lines("a. Première ?\nb. Seconde ?")
    assert [ln["badge"] for ln in lines] == [None, None]
    assert _words(lines[0]) == "a. Première ?"


def test_a_subquestion_wraps_with_a_hanging_indent():
    """Les lignes suivantes s'alignent sous le TEXTE, pas sous la pastille."""
    from app.services.pdfgen import DIFFICULTY_COLORS
    lines = _lines("a. " + "mot " * 120, sub_badge_color=DIFFICULTY_COLORS[3])
    assert len(lines) > 1
    assert len({ln["indent"] for ln in lines}) == 1
    assert lines[0]["indent"] > 0


def test_the_exercise_badge_indent_only_costs_the_first_line():
    lines = _lines("Contexte long.\nSuite.", first_indent=30.0)
    assert lines[0]["indent"] == 30.0
    assert lines[1]["indent"] == 0.0


# ------------------------------------------------- expression mise en valeur

def test_a_final_expression_is_still_promoted_to_display():
    body, expr = pdfgen._display_split("Calcule : $2+3$")
    assert (body, expr) == ("Calcule :", "2+3")


def test_a_display_expression_may_sit_on_its_own_line():
    body, expr = pdfgen._display_split("Calcule :\n$\\dfrac{3}{4}+\\dfrac{5}{6}$")
    assert (body, expr) == ("Calcule :", "\\dfrac{3}{4}+\\dfrac{5}{6}")


def test_a_list_does_not_have_its_last_item_torn_off_as_a_display():
    """Sur l'énoncé entier, le motif « consigne : $expr$ » traversait les sauts
    de ligne et arrachait la dernière donnée de sa liste pour aller la centrer.
    Il ne se cherche donc que sur la DERNIÈRE ligne."""
    raw = "Trois trajets :\n- aller : $12$\n- retour : $8$"
    body, expr = pdfgen._display_split(raw)
    assert body == "Trois trajets :\n- aller : $12$\n- retour :"
    assert expr == "8"


def test_an_old_bank_exercise_is_laid_out_at_render_time():
    """La banque contient encore des exercices créés avant la règle, dont les
    sous-questions sont recollées. Rien ne les rejoue : le rendu les normalise
    donc lui aussi, avec la MÊME fonction."""
    lay = pdfgen._statement_layout("Calcule. a) $2+3$ b) $4+5$", WIDTH, 9, 12,
                                   sub_badge_color=pdfgen.DIFFICULTY_COLORS[3])
    lines = lay["intro"]["lines"]
    assert [ln["badge"] for ln in lines] == [None, "a", "b"]


def test_legacy_single_line_statements_are_untouched_by_the_line_rules():
    """Les générateurs builtin (MathALÉA) écrivent d'un seul tenant : leur
    rendu ne doit pas changer d'un pouce."""
    assert pdfgen._legacy_to_tagged("Calculer : 3/4 + 5/6 = ?") \
        == "Calculer : $\\dfrac{3}{4} + \\dfrac{5}{6}$"


def test_legacy_conversion_leaves_multi_line_statements_alone():
    raw = "Complète :\n- a : 3/4\n- b : 5/6"
    assert pdfgen._legacy_to_tagged(raw) == raw

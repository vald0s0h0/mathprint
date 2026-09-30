"""Encadrés GUIDE intégrés à l'énoncé (« {{aide}} ») : une ligne guide reste une
ligne, n'est jamais coupée en pastilles, et disparaît proprement quand le sujet
ne les inclut pas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import blocks
from app.services import statement as st


def test_a_guide_marker_always_opens_its_own_line():
    out = st.normalize("Calcule. {{aide}} Pense aux priorités.")
    assert out == "Calcule.\n{{aide}} Pense aux priorités."


def test_a_guide_line_is_never_split_into_subquestion_badges():
    text = "{{aide}} Méthode : a. factorise b. simplifie\na. Coche la bonne réponse. b. Puis relie."
    out = st.normalize(text)
    assert out.split("\n")[0] == "{{aide}} Méthode : a. factorise b. simplifie"
    assert "a. Coche la bonne réponse.\nb. Puis relie." in out


def test_normalize_is_idempotent_with_guides():
    text = "Contexte.\n{{aide}}Rappel : $x^2$\n{{aide}}  suite\nQuestion ?"
    once = st.normalize(text)
    assert st.normalize(once) == once


def test_consecutive_guide_lines_make_one_box():
    text = st.normalize("Contexte.\n{{aide}} Un.\n{{aide}} Deux.\nQuestion.\n{{aide}} Trois.")
    assert st.guide_texts(text) == ["Un.\nDeux.", "Trois."]
    kinds = [b.kind for b in blocks.parse(text)]
    assert kinds == ["text", "guide", "text", "guide"]


def test_strip_guides_leaves_the_exercise_complete():
    text = st.normalize("Contexte.\n{{aide}} Un.\nQuestion ?")
    assert st.strip_guides(text) == "Contexte.\nQuestion ?"


def test_leading_guides_of_a_composite_question():
    lead, rest = st.split_leading_guides("{{aide}} Pense à ceci.\nQuelle est l'aire ?")
    assert lead == "{{aide}} Pense à ceci." and rest == "Quelle est l'aire ?"
    assert st.split_leading_guides("Question ?") == ("", "Question ?")

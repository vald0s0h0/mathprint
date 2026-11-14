"""Contrôle par script de la fiche d'une leçon (`forge contexte`) et de ses idées."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import contexte  # noqa: E402
import forge  # noqa: E402
import run as cli  # noqa: E402

from test_forge import db  # noqa: E402,F401

FICHE = """# Fiche : Test (fr, 3e, maths, leçon Z1)

## Acquis
L'élève sait additionner des entiers.

## Faits
- **F1** : la somme de deux nombres pairs est paire.
- **F2** : un nombre pair est un multiple de deux.
F3. Un produit est nul si l'un de ses facteurs est nul.

## Z1.1 : Additionner
Les erreurs fréquentes : oublier une retenue (F1).

## Z1.2 : Multiplier
Rien de plus.
"""

IDEES = """- id: i01
  competences: [Z1.1]
  mecanisme: "Deux méthodes donnent deux résultats : l'élève découvre l'erreur."
  pourquoi_bon: "L'erreur est provoquée, pas annoncée."
  type: "deux élèves en désaccord"
  ne_pas_reprendre: "contexte, personnages, valeurs"
"""

CODES = ["Z1.1", "Z1.2"]


def test_a_well_formed_sheet_passes():
    assert contexte.fiche_problems(FICHE, CODES) == []
    assert contexte.idees_problems(IDEES, CODES) == []
    assert contexte.idees_problems("", CODES) == []


def test_size_and_dates_are_refused():
    big = FICHE + "x" * (contexte.MAX_BYTES + 1)
    assert any("trop longue" in p for p in contexte.fiche_problems(big, CODES))
    for dated in ("Écrite le 14/11/2026.", "Version du 2026-11-14.", "Écrite en novembre 2026.",
                  "Relue le 3 mars 2027."):
        assert any("date" in p for p in contexte.fiche_problems(FICHE + dated, CODES)), dated
    # Une date historique est un fait, pas une date de rédaction.
    assert contexte.fiche_problems(FICHE.replace("Rien de plus.", "L'armistice du 11 novembre 1918."),
                                   CODES) == []


def test_facts_are_numbered_without_gap_or_double():
    assert any("aucun fait" in p for p in contexte.fiche_problems("## Z1.1\n## Z1.2\n", CODES))
    gap = FICHE.replace("F3.", "F5.")
    assert any("trous : F3, F4" in p for p in contexte.fiche_problems(gap, CODES))
    double = FICHE.replace("F3.", "F2.")
    assert any("deux fois : F2" in p for p in contexte.fiche_problems(double, CODES))
    # Une référence dans le texte n'est pas une définition ; un code de compétence non plus.
    assert contexte.fiche_problems(FICHE + "\n- F1.2 : une compétence\n", CODES) == []


def test_each_competency_has_its_section():
    problems = contexte.fiche_problems(FICHE, CODES + ["Z1.3"])
    assert problems == ["compétence(s) sans section (code dans un titre) : Z1.3"]
    # Le code dans le corps du texte ne suffit pas, ni un code qui le prolonge.
    text = FICHE.replace("## Z1.2 : Multiplier", "## Z1.20 : Multiplier\nVoir Z1.2.")
    assert any("Z1.2" in p for p in contexte.fiche_problems(text, CODES))


def test_figure_engines_and_copies_are_refused():
    for recipe in ("Une figure en TikZ.", "\\draw (0,0) -- (1,1);", "figure_layout: left",
                   "Le moteur geo trace le triangle."):
        assert any("moteur" in p for p in contexte.fiche_problems(FICHE + recipe, CODES)), recipe
    assert contexte.fiche_problems(FICHE + "Exercice de type geo_construction.", CODES) == []
    source = "Dans un magasin, le prix du kilogramme de riz est affiché sur une grande étiquette rouge."
    copied = FICHE + "\nLe prix du kilogramme de riz est affiché sur une grande étiquette."
    assert any("8 mots" in p for p in contexte.fiche_problems(copied, CODES, [source]))
    assert contexte.fiche_problems(FICHE, CODES, [source]) == []


def test_ideas_are_checked():
    assert any("YAML illisible" in p for p in contexte.idees_problems("- id: [", CODES))
    assert contexte.idees_problems("id: i01", CODES) == ["idées : le fichier doit être une liste YAML d'idées"]
    bad = IDEES.replace("[Z1.1]", "[Z9.9]").replace(
        "l'élève découvre l'erreur.\"", "l'élève découvre l'erreur. Il recommence. Avec 3 valeurs.\"")
    problems = contexte.idees_problems(bad + IDEES, CODES)
    assert any("hors de la leçon : Z9.9" in p for p in problems)
    assert any("plus de 2 phrases" in p for p in problems)
    assert any("chiffre" in p for p in problems)
    assert any("en double" in p for p in problems)
    partial = "- id: i02\n  competences: [Z1.2]\n  mecanisme: \"Un mécanisme.\"\n  auteur: x\n"
    problems = contexte.idees_problems(partial, CODES)
    assert any("manquant(s) : pourquoi_bon, type, ne_pas_reprendre" in p for p in problems)
    assert any("inconnu(s) : auteur" in p for p in problems)
    source = "Deux méthodes donnent deux résultats différents et l'élève découvre alors son erreur."
    copied = IDEES.replace("Deux méthodes donnent deux résultats : l'élève découvre l'erreur.",
                           "Deux méthodes donnent deux résultats différents et l'élève découvre alors.")
    assert any("8 mots" in p for p in contexte.idees_problems(copied, CODES, [source]))


def _args(**extra):
    base = {"programme": "fr", "cycle": "3e", "lecon": "Z1", "serie": None, "matiere": "math", "sources": []}
    return cli.argparse.Namespace(**(base | extra))


def test_fiche_command(db, capsys, tmp_path):  # noqa: F811
    assert cli._cmd_fiche(_args(), db) == 1
    assert "FICHE ABSENTE" in capsys.readouterr().out
    fiche = forge.fiche_path("fr", "3e", "Z1")
    fiche.parent.mkdir(parents=True, exist_ok=True)
    fiche.write_text(FICHE, encoding="utf-8")
    assert cli._cmd_fiche(_args(lecon="Z1.2"), db) == 0
    assert "Fiche conforme." in capsys.readouterr().out
    fiche.with_suffix(".idees.yaml").write_text(IDEES.replace("Z1.1", "Z7.7"), encoding="utf-8")
    source = tmp_path / "cours.md"
    source.write_text("Rappel : la somme de deux nombres pairs est paire, toujours.", encoding="utf-8")
    assert cli._cmd_fiche(_args(sources=[str(source)]), db) == 1
    out = capsys.readouterr().out
    assert "1 source(s)" in out and "Z7.7" in out and "8 mots" in out and "2 problème(s)." in out

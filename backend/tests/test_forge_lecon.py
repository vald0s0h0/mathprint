"""Contexte d'une leçon (`forge lecon`) : découpe, faits, idées, contrôles et vérification."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import forge  # noqa: E402
import lecon  # noqa: E402
import run as cli  # noqa: E402

from test_forge import db  # noqa: E402,F401

LECON = """# Test (fr, 3e)

## Acquis
L'élève sait additionner des entiers.

## Faits communs
- **F1** : la somme de deux nombres pairs est paire.
- **F2** (incertain) : un nombre pair est un multiple de deux.

## Z1.1 : Additionner
- **F3** : une retenue se reporte au rang suivant.

Les erreurs fréquentes : oublier une retenue (F3).

**Tracés : aucun.**

```yaml idees
- id: i01
  mecanisme: "Deux méthodes donnent deux résultats : l'élève découvre l'erreur."
  pourquoi_bon: "L'erreur est provoquée, pas annoncée."
  type: "deux élèves en désaccord"
  ne_pas_reprendre: "contexte, personnages, valeurs"
```

## Z1.2 : Multiplier
F4. Un produit est nul si l'un de ses facteurs est nul.

**Tracés : aucun.**

## Contextes
Courses au marché, trajets en bus.
"""

CODES = ["Z1.1", "Z1.2"]


def test_extract_keeps_header_own_section_and_tail():
    part = lecon.extract(LECON, CODES, "Z1.2")
    assert "F1" in part and "F4" in part and "Courses au marché" in part
    assert "F3" not in part and "i01" not in part
    assert lecon.extract(LECON, CODES, None) == LECON


def test_facts_and_ideas_are_read_from_the_lesson():
    facts = lecon.facts(LECON)
    assert list(facts) == ["F1", "F2", "F3", "F4"]
    assert facts["F2"]["incertain"] and not facts["F1"]["incertain"]
    assert facts["F4"]["text"].startswith("Un produit est nul")
    ideas = lecon.ideas(LECON, CODES)
    assert [(i["id"], i["competences"]) for i in ideas] == [("i01", ["Z1.1"])]


def test_a_conform_lesson_has_no_problem():
    assert lecon.lecon_problems(LECON, CODES) == []


def test_structure_problems():
    assert lecon.structure_problems("# Ancienne fiche\n", CODES) == [
        "aucun fait numéroté : écris les faits F1, F2… un par ligne"]
    assert any("trous : F5" in p for p in lecon.structure_problems(LECON + "- **F6** : x.\n", CODES))
    assert any("deux fois : F2" in p for p in lecon.structure_problems(LECON + "- **F2** : x.\n", CODES))
    assert any("Z1.3" in p for p in lecon.structure_problems(LECON, CODES + ["Z1.3"]))
    no_trace = LECON.replace("**Tracés : aucun.**\n\n## Contextes", "## Contextes")
    assert any("Z1.2 : ligne « **Tracés" in p for p in lecon.structure_problems(no_trace, CODES))


def test_slice_size_dates_and_engines():
    big = LECON.replace("Rien", "") + ("x" * (lecon.MAX_SLICE_BYTES + 1))
    assert any("extrait trop long" in p for p in lecon.lecon_problems(big, CODES))
    for dated in ("Mise à jour le 12/11/2026.", "Écrit en novembre 2026."):
        assert any("date" in p for p in lecon.lecon_problems(LECON + dated, CODES)), dated
    assert lecon.lecon_problems(LECON + "L'armistice du 11 novembre 1918.", CODES) == []
    assert any("moteur" in p for p in lecon.lecon_problems(LECON + "Trace-la en tikz.", CODES))


def test_a_lesson_never_repeats_a_higher_level():
    upper = ["Sans calculatrice. Tout calcul se fait de tête ou posé en peu de temps, toujours."]
    repeated = LECON + "\nSans calculatrice : tout calcul se fait de tête ou posé en peu de temps.\n"
    assert any("niveau au-dessus" in p for p in lecon.lecon_problems(repeated, CODES, upper=upper))
    assert lecon.lecon_problems(LECON, CODES, upper=upper) == []


def test_copies_from_a_source_but_facts_may_stay_official():
    source = ("Rappel : la somme de deux nombres pairs est paire, toujours. "
              "Les erreurs fréquentes : oublier une retenue dans le calcul posé, souvent.")
    assert lecon.lecon_problems(LECON, CODES, [source]) == []
    copied = LECON + "\nLes erreurs fréquentes : oublier une retenue dans le calcul posé.\n"
    assert any("8 mots" in p for p in lecon.lecon_problems(copied, CODES, [source]))


def test_idea_problems():
    bad = LECON.replace('mecanisme: "Deux', 'mecanisme: "3 méthodes. Deux').replace(
        "  type: \"deux élèves en désaccord\"\n", "  couleur: rouge\n")
    problems = lecon.idea_problems(bad, CODES)
    assert any("chiffre" in p for p in problems)
    assert any("manquant(s) : type" in p for p in problems)
    assert any("inconnu(s) : couleur" in p for p in problems)
    stranger = LECON.replace("  pourquoi_bon", "  competences: [Z7.7]\n  pourquoi_bon")
    assert any("Z7.7" in p for p in lecon.idea_problems(stranger, CODES))
    broken = LECON.replace("- id: i01", "- id: [")
    assert any("illisible" in p for p in lecon.idea_problems(broken, CODES))


def test_verification_blocks_until_every_fact_is_judged(tmp_path):
    path = tmp_path / "lecon.md"
    path.write_text(LECON, encoding="utf-8")
    assert len(lecon.blocking_facts(LECON, lecon.load_verif(path))) == 4
    calls = []

    def ask(system, prompt):
        calls.append(prompt)
        ids = [line.split(" : ")[0] for line in prompt.split("# Faits à juger")[1].splitlines() if line.startswith("F")]
        return {"faits": [{"id": i, "verdict": "faux" if i == "F3" else "juste", "raison": "retenue mal dite"}
                          for i in ids]}, {"input": 10, "output": 5}
    lecon.verify(path, "Référentiel", ask, model="opus")
    assert lecon.blocking_facts(LECON, lecon.load_verif(path)) == ["F3 faux : retenue mal dite"]
    lecon.verify(path, "Référentiel", ask, model="opus")
    assert len(calls) == 1                                     # rien de neuf : pas d'appel
    fixed = LECON.replace("une retenue se reporte au rang suivant", "une retenue s'ajoute au rang suivant")
    path.write_text(fixed, encoding="utf-8")
    lecon.verify(path, "Référentiel", ask, model="opus")
    assert "F3 :" in calls[1] and "F1 :" not in calls[1]       # seul le fait modifié est rejugé
    assert lecon.blocking_facts(fixed, lecon.load_verif(path)) == ["F3 faux : retenue mal dite"]
    assert lecon.accept(path, ["F3"]) == ["F3"]
    assert lecon.blocking_facts(fixed, lecon.load_verif(path)) == []


def _args(**extra):
    base = {"programme": "fr", "cycle": "3e", "lecon": "Z1", "serie": None, "matiere": "math", "sources": [],
            "verifier": False, "modele": "opus", "accepter": []}
    return cli.argparse.Namespace(**(base | extra))


def test_lesson_command(db, capsys, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forge, "upper_levels", lambda subject, programme: [])
    assert cli._cmd_lesson(_args(), db) == 1
    assert "LEÇON ABSENTE" in capsys.readouterr().out
    path = forge.lecon_path("fr", "3e", "Z1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LECON, encoding="utf-8")
    assert cli._cmd_lesson(_args(lecon="Z1.2"), db) == 1
    out = capsys.readouterr().out
    assert "4 non vérifié(s)" in out and "Incertains à relire : F2" in out
    assert cli._cmd_lesson(_args(accepter=["F1", "F2", "F3", "F4"]), db) == 0
    assert "Leçon conforme et vérifiée." in capsys.readouterr().out
    source = tmp_path / "cours.md"
    source.write_text("Les erreurs fréquentes : oublier une retenue (F3) dans le calcul posé.", encoding="utf-8")
    path.write_text(LECON.replace("(F3).", "(F3) dans le calcul posé."), encoding="utf-8")
    assert cli._cmd_lesson(_args(sources=[str(source)]), db) == 1
    out = capsys.readouterr().out
    assert "1 source(s)" in out and "8 mots" in out

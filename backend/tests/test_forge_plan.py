"""Plan d'un run forge : volume d'après le verbe et la durée, archétypes, idées,
séries du lycée, et aucun texte de manuel dans le contexte du rédacteur."""
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import forge  # noqa: E402
import plan  # noqa: E402
import run as cli  # noqa: E402

from app.models import Competency, CompetencyFramework  # noqa: E402
from test_forge import db  # noqa: E402,F401


@pytest.mark.parametrize("label,kind,count", [
    ("Connaître la définition d'une racine carrée", "savoir", 2),
    ("Identifier un triangle rectangle", "savoir", 2),
    ("Calculer une longueur avec la propriété de Thalès", "savoir_faire", 6),
    ("Démontrer que deux droites sont parallèles", "raisonnement", 4),
    ("Traiter une situation faisant appel à Thalès", "situation", 0),
    ("Fractions : additionner", "savoir_faire", 6),
])
def test_volume_follows_the_verb(label, kind, count):
    assert plan.nature(label) == kind and plan.family_count(label) == count


def test_duration_scales_a_skill_and_the_grid_is_varied():
    label = "Calculer une longueur"
    assert [plan.family_count(label, m) for m in (10, 25, 45)] == [5, 6, 7]
    grid = plan.family_grid("E2.3", label, 45)
    assert [s["slot"] for s in grid] == [f"s0{i}" for i in range(1, 8)]
    assert grid == plan.family_grid("E2.3", label, 45)                    # déterministe
    archetypes = [s["archetype"] for s in grid]
    assert len(set(archetypes)) == 6 and not {"ERR", "QUI"} & set(archetypes)   # varié, sans élève fictif
    assert grid[0]["level"] == 1 and grid[0]["response_type"] == "qcm_single"


def _lesson(n_skills=8, n_rappels=6):
    return ([(f"E1.{i + 1}", "Connaître une définition", 25) for i in range(n_rappels)]
            + [(f"E1.{i + 1 + n_rappels}", "Calculer une longueur", 25) for i in range(n_skills)])


def test_fictional_students_are_spread_over_the_lesson():
    debates = plan.lesson_debates(_lesson(), "ci-3e-E1")
    assert len(debates) == 3 and sorted(debates.values()) == ["ERR", "ERR", "QUI"]
    assert len({code for code, _rank in debates}) == 3                   # trois compétences
    assert all(code not in {f"E1.{i}" for i in range(1, 7)} for code, _ in debates)   # pas un rappel
    assert all(rank > 0 for _code, rank in debates)
    assert debates == plan.lesson_debates(_lesson(), "ci-3e-E1")         # déterministe
    assert len(plan.lesson_debates(_lesson(n_skills=3), "ci-3e-E2")) == 2
    assert plan.lesson_debates(_lesson(n_skills=0), "ci-3e-E3") == {}
    total = 0
    for code, label, minutes in _lesson():
        grid = plan.family_grid(code, label, minutes, debates=debates)
        found = [s for s in grid if s["archetype"] in ("ERR", "QUI")]
        total += len(found)
        for s in found:
            assert s["level"] >= 2 and s["response_type"] in ("qcm_single", "multiline_text")
    assert total == 3


def test_ideas_go_to_some_slots_only_and_never_to_a_direct_application():
    grid = plan.family_grid("E2.3", "Calculer une longueur", 45)
    ideas = [{"id": f"i{k}", "mecanisme": f"mécanisme {k}", "competences": ["E2.3"]} for k in range(6)]
    ideas.append({"id": "autre", "mecanisme": "ailleurs", "competences": ["E2.9"]})
    plan.assign_ideas(grid, ideas, "E2.3")
    given = [s for s in grid if s.get("idee")]
    assert 1 <= len(given) <= 3 and all(s["archetype"] != "CALC" for s in given)
    assert len({s["idee"]["id"] for s in given}) == len(given)
    assert all(s["idee"]["id"] != "autre" for s in given)


def test_calendar_week_and_upcoming_lessons():
    start = date(2026, 9, 14)
    assert plan.school_week(date(2026, 9, 14), start) == 1
    assert plan.school_week(date(2026, 11, 14), start) == 9
    assert plan.default_start(date(2026, 11, 14)) == date(2026, 9, 21)
    lessons = [{"code": "A", "week": 9}, {"code": "B", "week": 12}, {"code": "C", "week": 13},
               {"code": "D", "week": 3}]
    assert [l["code"] for l in plan.upcoming(lessons, 9, 3)] == ["A", "B"]


def _lycee(db):  # noqa: F811
    for serie in ("C", "D"):
        framework = CompetencyFramework(grade_level="tle", name=f"Tle {serie}", program_id="ci",
                                        subject_id="math", status="published", path_id=f"ci-{serie}")
        db.add(framework)
        db.flush()
        for rank, (chapter, name) in enumerate((("E2", "Thalès"), ("E1", "Triangle rectangle")), 1):
            db.add(Competency(framework_id=framework.id, code=f"{chapter}.1", short_id=f"{chapter}.1",
                              label="Calculer une longueur", chapter_code=chapter, chapter_name=name,
                              chapter_rank=rank, chapter_week=rank * 2, order_index=rank,
                              recommended_minutes=20))
    db.commit()


def test_a_level_with_series_needs_one_and_resolves_it(db):  # noqa: F811
    _lycee(db)
    with pytest.raises(SystemExit, match="précise --serie"):
        forge.resolve_target(db, "ci", "tle", "E2.1")
    with pytest.raises(SystemExit, match="inconnue"):
        forge.resolve_target(db, "ci", "tle", "E2.1", serie="Z")
    target = forge.resolve_target(db, "ci", "tle", "E2.1", serie="D")
    assert target["framework"].path_id == "ci-D"


def test_the_writer_reads_no_manual_and_sees_the_year_progression(db):  # noqa: F811
    _lycee(db)
    run = forge.prepare(db, "ci", "tle", "E2.1", model="m", serie="D", volume="formule")[0]
    payload = forge.load_json(run / "payload.json")
    context = payload["context"]
    assert run.name.startswith("ci-tle-D-E2.1-") and payload["serie"] == "D"
    assert context["fiche"].endswith("ci-tle-D-E2.md") and context["figure_images"] == []
    assert Path(context["source"]).read_text() == ""       # jamais les pages françaises
    assert "Manuel" not in Path(context["read_order"][0]).read_text()
    assert all(Path(p).exists() for p in context["read_order"]) and len(context["read_order"]) == 1
    referential = Path(context["referential"]).read_text()
    assert "Pas encore vu" in referential and "E1 Triangle rectangle" in referential
    grid = forge.load_json(run / "grid.json")
    assert len(grid) == 6 and all(s.get("archetype") for s in grid)      # toute la grille d'un coup
    rest = forge.prepare(db, "ci", "tle", "E2.1", model="m", serie="D", volume="formule", suite=True)[0]
    bundle = forge.load_json(rest / "payload.json")["context"]["read_order"]
    assert len(bundle) == 1 and "Banque existante" in Path(bundle[0]).read_text()


def test_the_declared_archetype_must_match_the_slot():
    slot = {"slot": "s01", "kind": "exercise", "competency": "Z1.1", "level": 2, "difficulty": "moyen",
            "response_type": "qcm_multiple", "size": "M", "figure": False, "archetype": "ERR"}
    base = {"response_type": "qcm_multiple", "estimated_seconds": 90, "family_id": "f", "micro_skill": "m"}
    assert any("archetype" in p for p in forge._slot_problems({**base, "archetype": "CALC"}, slot, {}))
    assert not any("archetype" in p for p in forge._slot_problems({**base, "archetype": "ERR"}, slot, {}))


def test_no_exercises_without_a_checked_lesson(db, capsys):  # noqa: F811
    import lecon
    _lycee(db)
    args = cli.argparse.Namespace(programme="ci", cycle="tle", cible="E2.1", modele="m", matiere="math",
                                  serie="D", problemes=False, pilote=False, complement=None, traces=None,
                                  facile=None, moyen=None, difficile=None, suite=False, vingt=False)
    assert cli._cmd_prepare(args, db) == 1
    out = capsys.readouterr().out
    assert "LEÇON ABSENTE" in out and "forge lecon ci tle E2 --serie D" in out
    path = forge.lecon_path("ci", "tle", "E2", "math", "D")
    assert path.parts[-2:] == ("math", "ci-tle-D-E2.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Fiche\n")                       # ancien format : sans faits
    assert cli._cmd_prepare(args, db) == 1
    assert "LEÇON À RÉÉCRIRE" in capsys.readouterr().out
    codes = [forge.comp_code(c) for c in forge.resolve_target(db, "ci", "tle", "E2", "math", "D")["chapter"]]
    body = "# Leçon\n\n- **F1** : un fait commun.\n\n" + "".join(
        f"## {code} : compétence\n\n**Tracés : aucun.**\n\n" for code in codes)
    path.write_text(body)
    assert cli._cmd_prepare(args, db) == 1
    assert "LEÇON À VÉRIFIER" in capsys.readouterr().out and True
    lecon.accept(path, ["F1"])
    assert cli._cmd_prepare(args, db) == 0
    out = capsys.readouterr().out
    assert "famille(s) en tout" in out and "sans sous-agent" in out


def test_trust_is_earned_per_format_and_lost_on_a_defect():
    import confiance
    assert [confiance.rate(n) for n in (0, 9, 10, 29, 30, 99, 100, 299, 300)] == [1, 1, .5, .5, .25, .25, .10, .10, .05]
    cards = {f"s{i:02d}": "qcm_single" for i in range(1, 21)} | {"s21": "table_fill"}
    known = {"qcm_single": {"rate": 0.25}}
    picked = confiance.sample("run", cards, {"s03"}, known)
    assert picked["s03"] == "signalée par un lecteur" and "s21" in picked       # format jamais relu
    assert sum(1 for k in picked if k not in ("s03", "s21")) == 5              # 25 % de 19, arrondi au-dessus
    assert picked == confiance.sample("run", cards, {"s03"}, known)
    assert len(confiance.sample("run", cards, set(), known, pilot=True)) == 21

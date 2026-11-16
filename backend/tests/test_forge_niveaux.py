"""Forge à niveaux : ordre de lecture, faits cités, relecture pédagogique, cartes
écartées, confiance par archétype et cause d'un refus."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "forge"))

import confiance  # noqa: E402
import forge  # noqa: E402
import goldfish  # noqa: E402
import mesure  # noqa: E402
import relecture  # noqa: E402
from app.models import IndigoExercise  # noqa: E402

from test_forge import ETALONS, _slot, db  # noqa: E402,F401


def _lesson(db, programme="fr", cycle="3e", chapter="Z1"):
    codes = [forge.comp_code(c) for c in forge.resolve_target(db, programme, cycle, chapter)["chapter"]]
    body = "# Leçon test\n\n- **F1** : un fait commun.\n\n" + "".join(
        f"## {code} : compétence\n\n- **F{i + 2}** : un fait de {code}.\n\n**Tracés : aucun.**\n\n"
        for i, code in enumerate(codes))
    path = forge.lecon_path(programme, cycle, chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path, codes


def test_read_order_goes_from_general_to_specific(db):
    _lesson(db)
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="m")[0]
    context = forge.load_json(run / "payload.json")["context"]
    names = [Path(p).relative_to(forge.REPO).as_posix() if str(p).startswith(str(forge.REPO)) else Path(p).name
             for p in context["read_order"]]
    expected = ["agents/forge/CONSIGNE-EXERCICES.md", "agents/forge/contexte/exercice.md",
                "agents/forge/contexte/formats/commun.md", "agents/forge/contexte/formats/courants.md",
                "agents/forge/contexte/figures/commun.md", "agents/forge/contexte/matieres/math.md",
                "agents/forge/contexte/exemples/math.json", "agents/forge/contexte/figures/math.md",
                "backend/app/data/misconceptions/math.json",
                "agents/forge/contexte/pays/fr.md"]
    assert names[:len(expected)] == expected
    assert names[len(expected):] == ["fr-3e-Z1-referentiel.md", "fr-3e-Z1-Z1.1-extrait.md"]
    assert "geo_construction.md" not in " ".join(names)                 # pas de tracé dans la grille
    extract = Path(context["lecon"]).read_text(encoding="utf-8")
    assert "un fait de Z1.1" in extract and "un fait de Z1.2" not in extract
    assert context["faits"] == ["F1", "F2"]
    assert forge.load_json(run / "payload.json")["relecture"] is True


def test_the_tracing_contract_is_read_only_with_traces(db):
    _lesson(db)
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="m", traces=2)[0]
    order = forge.load_json(run / "payload.json")["context"]["read_order"]
    assert str(forge.GEO_CONTEXT) in order
    assert order.index(str(forge.GEO_CONTEXT)) < len(order) - 2       # avant référentiel et leçon


def test_cards_cite_facts_of_their_lesson_extract():
    slot = {"slot": "s01", "kind": "exercise", "competency": "Z1.1", "level": 2, "difficulty": "moyen",
            "response_type": "qcm_multiple", "size": "M", "figure": False}
    base = {"slot": "s01", "response_type": "qcm_multiple", "estimated_seconds": 90, "family_id": "f",
            "micro_skill": "m", "guides": [{"text": "Regarde bien les deux nombres.", "position": "apres_question"}]}
    payload = {"relecture": True, "context": {"faits": ["F1", "F2"]}}
    assert any("faits obligatoire" in p for p in forge._slot_problems(base, slot, payload))
    assert any("absent(s) de l'extrait de la leçon : F9" in p
               for p in forge._slot_problems({**base, "faits": ["F1", "F9"]}, slot, payload))
    assert not any("fait" in p for p in forge._slot_problems({**base, "faits": ["F2"]}, slot, payload))
    assert not any("fait" in p for p in forge._slot_problems(base, slot, {}))   # run sans leçon


def _verdict(blocked=False, **criteria):
    out = {name: {"ok": True} for name in relecture.CRITERIA}
    out.update({name: {"ok": False, "note": note} for name, note in criteria.items()})
    return {**out, "bloquant": blocked, "critique": "Refais le piège b." if blocked else "",
            "remarques": None}


def test_a_wrong_or_off_programme_card_always_blocks():
    assert relecture.judge(_verdict()) == (False, [])
    assert relecture.judge(_verdict(niveau="un peu long")) == (False, ["niveau : un peu long"])
    assert relecture.judge(_verdict(juste="le corrigé est faux"))[0] is True
    assert relecture.judge(_verdict(programme="trigonométrie en 4e"))[0] is True
    with pytest.raises(goldfish.GoldfishError):
        relecture.judge({"bloquant": False})


def _lesson_run(db, cards):
    _lesson(db)
    run = forge.prepare(db, "fr", "3e", "Z1.1", model="test-model")[0]
    slots = []
    for i, card in enumerate(cards):
        key = f"s{i + 1:02d}"
        slots.append(_slot(card, key))
        forge.dump_json(run / "slots" / f"{key}.json", {**card["exercice"], "slot": key, "faits": ["F2"]})
    forge.dump_json(run / "grid.json", slots)
    return run


def test_blocked_twice_is_set_aside_and_never_persisted(db, monkeypatch):
    run = _lesson_run(db, [ETALONS[0], ETALONS[1]])
    assert forge.check(db, run)[0].ok
    forge.render(db, run)
    answers = []

    def fake_ask(cli, system, text, images, model=None):
        assert model == "opus" and "# Section de la leçon" in text and "F2 un fait de Z1.1" in text
        goldfish.USAGE.append({"model": model, "input": 100, "cache_read": 0, "cache_write": 0, "output": 50})
        return {"cartes": [answers.pop(0) for _ in images]}
    monkeypatch.setattr(goldfish, "ask", fake_ask)
    with pytest.raises(SystemExit, match="Relecture pédagogique incomplète"):
        forge.persist(db, run)
    answers[:] = [_verdict(), _verdict(blocked=True, distracteurs="b ne vient d'aucune erreur")]
    relecture.run(run)
    assert relecture.problems(run) == ["s02 : bloquée par la relecture, à réécrire une fois : Refais le piège b."]
    with pytest.raises(SystemExit, match="bloquée par la relecture"):
        forge.persist(db, run)
    relecture.run(run)                                    # même image : rien à relire
    assert answers == []
    # Réécrite : nouvelle image, nouvelle relecture, encore bloquée → écartée.
    card = forge.load_json(run / "slots" / "s02.json")
    forge.dump_json(run / "slots" / "s02.json", {**card, "statement": card["statement"] + " Justifie."})
    forge.render(db, run)
    answers[:] = [_verdict(blocked=True)]
    relecture.run(run, slots=["s02"])
    assert relecture.ecartees(run) == {"s02"} and relecture.problems(run) == []
    result = forge.persist(db, run)
    assert result["ecartees"] == ["s02"]
    rows = db.query(IndigoExercise).all()
    assert {r.source_number.rsplit("/", 1)[1] for r in rows} == {"s01"}
    raw = rows[0].raw_ocr_json
    assert raw["faits"] == ["F2"] and raw["quality"]["relecture"]["attempts"] == 1
    assert sum(u["output"] for u in relecture.load_state(run)["usage"]) == 100


def test_trust_by_archetype_and_cause(db):
    def card(i, rtype, archetype, verdict, cause=None):
        raw = {"pipeline": "forge", "subject": "math", "archetype": archetype, "review_verdict": verdict}
        if cause:
            raw["review_cause"] = cause
        db.add(IndigoExercise(id=f"x{i}", extraction_id="e", competency_id="c", grade_level="3e",
                              source_page=0, source_number=f"r/s{i}", order_index=i, badge_type="exercice",
                              difficulty=1, title="", statement="", response_type=rtype,
                              expected_json={}, grading_json={}, raw_ocr_json=raw, status="draft"))
    for i in range(40):
        card(i, "qcm_single", "CALC", "like")
    card(40, "qcm_single", "ERR", "like")
    card(41, "qcm_single", "ERR", "dislike", "pedagogie")     # touche qcm_single/ERR seulement
    db.commit()
    known = confiance.streaks(db, "math")
    assert known["qcm_single"]["streak"] == 41 and known["qcm_single/ERR"]["streak"] == 0
    assert confiance.share_for("qcm_single", "CALC", known) == 0.25
    assert confiance.share_for("qcm_single", "ERR", known) == 1.0
    assert confiance.share_for("qcm_single", "DOC", known) == 0.25           # case jamais relue
    card(42, "qcm_single", "CALC", "dislike", "format_rendu")              # touche tout le format
    db.commit()
    known = confiance.streaks(db, "math")
    assert known["qcm_single"]["streak"] == 0 and known["qcm_single/CALC"]["streak"] == 0
    picked = confiance.sample("r", {"s1": ("qcm_single", "CALC")}, set(), known)
    assert picked == {"s1": "échantillon de relecture (100 % des qcm_single CALC)"}


def test_a_dislike_keeps_its_cause_until_the_verdict_changes(db):
    from app.services import indigo
    db.add(IndigoExercise(id="x1", extraction_id="e", competency_id="c", grade_level="3e", source_page=0,
                          source_number="r/s1", order_index=0, badge_type="exercice", difficulty=1, title="",
                          statement="", response_type="qcm_single", expected_json={}, grading_json={},
                          raw_ocr_json={"pipeline": "forge"}, status="draft"))
    db.commit()
    ex = db.get(IndigoExercise, "x1")
    indigo.set_review_verdict(db, ex, "dislike", "faux", "fait_faux")
    assert ex.raw_ocr_json["review_cause"] == "fait_faux"
    indigo.set_review_verdict(db, ex, "dislike", "faux, vraiment")          # commentaire seul
    assert ex.raw_ocr_json["review_cause"] == "fait_faux"
    indigo.set_review_verdict(db, ex, "like")
    assert "review_cause" not in ex.raw_ocr_json
    with pytest.raises(ValueError):
        indigo.set_review_verdict(db, ex, "dislike", None, "autre")


def test_author_usage_is_read_from_subagent_transcripts(tmp_path):
    folder = tmp_path / "session" / "subagents"
    folder.mkdir(parents=True)
    usage = {"input_tokens": 3, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200,
             "output_tokens": 40}
    lines = [{"message": {"id": "m1", "model": "opus", "usage": usage}},
             {"message": {"id": "m1", "model": "opus", "usage": usage}},          # même message, autre bloc
             {"message": {"id": "m2", "model": "opus", "usage": usage}, "text": "run fr-3e-Z1.1-x"}]
    (folder / "agent-a.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    (folder / "agent-b.jsonl").write_text(json.dumps(lines[0]), encoding="utf-8")   # autre run
    found = mesure.author_usage("fr-3e-Z1.1-x", tmp_path)
    assert found["files"] == 1 and found["calls"] == 2
    assert mesure.total(found["usage"])["cache_read"] == 2000

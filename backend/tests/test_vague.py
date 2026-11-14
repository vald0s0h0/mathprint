"""Commande `vague` (plan forge2 §10.2) : mise en attente, reprise, essai,
validation, micro-correction rédigée, seuil de confiance, appréciation.

Aucun appel réseau ni vrai appel de CLI : le moteur est une fonction, ou un faux
exécutable pour services.cli_llm.
"""
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (
    Assessment, Copy, CopyItem, DocumentPage, GradingDecision, ManualReview, OcrAttempt,
    Program, ResponseZone, ScanBatch, SchoolClass, Student, StudentResponse, Subject,
    SystemSetting,
)
from app.routers import scans as scans_router
from app.services import (
    appreciation, cli_llm, llm_grader, microcorrection, pipeline, providers, scan_intake, vague,
)

FEEDBACK = {"lines": 2, "options": {"e1": {"text": "Message prévu.", "error": "calc.soustraction"}},
            "fallback": {"text": "Relis la consigne et reprends ton calcul."},
            "correct": {"text": "Bien."}, "blank": {"text": "Tu n'as rien écrit."}}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    monkeypatch.setattr(cfg, "correction_mode", "commande")
    monkeypatch.setattr(
        pipeline.providers, "mathpix_ocr",
        lambda *a, **k: {"latex": "4x7=28 et 50-28=19", "text": "4x7=28 et 50-28=19",
                         "confidence": 0.9, "raw": {}})

    def _no_api(*a, **k):
        raise AssertionError("aucune API de LLM en mode commande")
    monkeypatch.setattr(providers, "deepseek_json", _no_api)
    monkeypatch.setattr(providers, "claude_json", _no_api)


def _seed(db, n=2):
    db.add_all([Subject(id="math", name="Mathématiques"),
                Program(id="ci", name="Programme ivoirien", origin_country_id="CI")])
    cls = SchoolClass(name="3A", grade_level="3e", program_id="ci")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle", type="control", note_base=20)
    db.add(a)
    db.flush()
    steps = [{"description": "prix total", "expected_text": "$4 \\times 7 = 28$", "points": 1},
             {"description": "monnaie rendue", "expected_text": "$50 - 28 = 22$", "points": 1}]
    for i in range(n):
        stu = Student(class_id=cls.id, name=f"Awa Koné{i}", order_index=i, llm_pseudonym=f"p{i}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        item = CopyItem(
            copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=6,
            response_type="multiline_text", statement="Combien lui rend-on ?",
            correction="$4 \\times 7 = 28$ puis $50 - 28 = 22$",
            expected_json={"type": "rubric", "steps": steps},
            grading_json={"comparator": "rubric", "max_score": 2, "bareme_points": 2,
                          "rubric": steps, "steps": steps, "feedback": FEEDBACK})
        db.add(item)
        db.flush()
        db.add(ResponseZone(page_id=page.id, item_id=item.id, type="text",
                            x_pt=50, y_pt=50, w_pt=100, h_pt=60, meta_json={}))
    db.commit()
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)
    return a, batch


def _engine(answer=None, calls=None):
    """Faux moteur : rend un verdict par réponse reçue."""
    calls = calls if calls is not None else []

    def ask(system, text):
        payload = json.loads(text.split("\n\nRends")[0])
        calls.append((system, payload))
        rows = []
        for r in payload["reponses"]:
            row = {"id": r["id"], "verdict": "partiel", "points": r["bareme"] / 2,
                   "confiance": 0.95, "motif": "monnaie fausse",
                   "message": "Tu as bien calculé le prix total ; refais la soustraction finale."}
            row.update(answer(r) if answer else {})
            rows.append(row)
        return {"corrections": rows}
    return ask, calls


def _decisions(db):
    return db.query(GradingDecision).filter(GradingDecision.status != "revised").all()


# ------------------------------------------------------------ mise en attente

def test_commande_mode_queues_the_answers_without_any_api(db):
    a, batch = _seed(db)
    for d in _decisions(db):
        assert d.status == "review_pending" and d.reason_code == "llm_queued"
        assert d.source == "deterministic" and d.score == 0.0     # jamais une note présentée comme finie
    assert db.get(ScanBatch, batch.id).status == "review_pending"
    view = scans_router._batch_view(db, db.get(ScanBatch, batch.id))
    assert view["pending_llm_queued"] == 2 and view["pending_reviews"] == 2
    note = scans_router.list_items(batch.id, "flagged", db)[0]["llm_notes"][0]
    assert note["verdict"] == "indisponible" and "vague" in note["motif"]
    # la file est durable : elle se relit en base
    assert len(llm_grader.pending_tasks(db, assessment_id=a.id)) == 2


def test_the_prompt_is_composed_from_subject_level_and_program(db):
    _seed(db)
    task = llm_grader.pending_tasks(db)[0]
    assert task.context == {"matiere": "Mathématiques", "niveau": "3e",
                            "programme": "Programme ivoirien"}
    head = llm_grader.system_prompt(task.context, vague=True).split("\n")[0]
    assert "mathématiques (en 3e, collège, programme ivoirien)" in head
    assert "France" not in head
    assert "lycée" in llm_grader.system_prompt({"niveau": "Tle"}).split("\n")[0]


# ------------------------------------------------------------------ corriger

def test_vague_grades_by_exercise_and_finalizes(db):
    a, batch = _seed(db)
    ask, calls = _engine()
    bilan = vague.corriger(db, devoir=a.id, ask=ask, log=lambda *_: None)

    assert (bilan.paquets, bilan.corrigees, bilan.renvoyees, bilan.echecs) == (1, 2, 0, 0)
    assert len(calls) == 1                                  # un exercice = un appel
    system, payload = calls[0]
    assert len(payload["exercices"]) == 1 and len(payload["reponses"]) == 2
    assert all(r["message_max"] == 100 for r in payload["reponses"])   # 2 lignes ≈ 100 car.
    assert "Awa" not in json.dumps(payload, ensure_ascii=False)        # pseudonymisé
    assert "programme ivoirien" in system and "message_max" in system
    for d in _decisions(db):
        assert d.status == "auto" and d.source == "deepseek" and d.score == pytest.approx(1.0)
        assert d.evidence_json["llm_engine"] == "haiku"
    assert db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count() == 0
    # même suite que le pipeline : finalisation et copies corrigées
    assert db.get(ScanBatch, batch.id).status == "overlay_ready"
    assert all(c.status == "finalized" for c in db.query(Copy).all())


def test_written_microcorrection_is_the_one_printed(db):
    _seed(db, n=1)
    ask, _ = _engine(lambda r: {"message_id": "e1"})
    vague.corriger(db, ask=ask, log=lambda *_: None)
    decision = _decisions(db)[0]
    resp = db.get(StudentResponse, decision.response_id)
    item = db.get(CopyItem, resp.copy_item_id)
    selection = microcorrection.select(item, decision, resp)
    assert selection.kind == "llm"
    assert selection.text.startswith("Tu as bien calculé le prix total")
    # le message prévu désigné ne sert plus qu'au journal d'erreurs
    assert ("calc.soustraction", "e1") in selection.errors


def test_a_too_long_message_falls_back_to_the_card(db):
    _seed(db, n=1)
    ask, _ = _engine(lambda r: {"message": "Tu as " + "beaucoup " * 40 + "écrit."})
    bilan = vague.corriger(db, ask=ask, log=lambda *_: None)
    assert bilan.messages_refuses == 1
    decision = _decisions(db)[0]
    assert "message_redige" not in decision.evidence_json["llm"][0]
    resp = db.get(StudentResponse, decision.response_id)
    selection = microcorrection.select(db.get(CopyItem, resp.copy_item_id), decision, resp)
    assert selection.text == FEEDBACK["fallback"]["text"]


@pytest.mark.parametrize("message, reason", [
    ("", "vide"),
    ("Il faut soustraire le prix au billet.", "pas de tutoiement"),
    ("Tu as coché la proposition B : relis la consigne.", "cite une lettre ou une position"),
    ("Tu t'es trompé, il fallait trouver 22.", "donne la réponse"),
    ("Tu as " + "x" * 120, "trop long"),
])
def test_message_checks(message, reason):
    assert vague.check_message(message, 2, "22") == ("", reason)


def test_message_check_accepts_a_good_message():
    text = "Tu as posé la soustraction à l'envers : reprends-la."
    assert vague.check_message(text, 2, "22") == (text, "")
    # « 220 » ou « 2,2 » ne sont pas la réponse « 22 »
    assert vague.check_message("Tu as trouvé 220, vérifie l'ordre de grandeur.", 2, "22")[1] == ""


def test_low_confidence_goes_back_to_the_teacher(db):
    _seed(db)
    ask, _ = _engine(lambda r: {"confiance": 0.5})
    bilan = vague.corriger(db, ask=ask, log=lambda *_: None)
    assert (bilan.corrigees, bilan.renvoyees) == (0, 2)
    for d in _decisions(db):
        assert d.status == "review_pending" and d.reason_code == "llm_low_confidence"
        assert d.score == 0.0
    assert llm_grader.pending_tasks(db) == []     # tranchée par le professeur, pas refaite


@pytest.mark.parametrize("bad", [
    {"points": 5},                    # au-delà du barème
    {"id": "r99"},                    # identifiant inconnu
    {"confiance": None},              # confiance manquante
])
def test_invalid_answers_leave_the_packet_waiting(db, bad):
    _seed(db)
    ask, _ = _engine(lambda r: bad)
    bilan = vague.corriger(db, ask=ask, log=lambda *_: None)
    assert (bilan.paquets, bilan.echecs) == (0, 1)
    assert all(d.reason_code == "llm_queued" for d in _decisions(db))


def test_essai_writes_nothing_and_calls_nothing(db):
    _seed(db)
    ask, calls = _engine()
    lines = []
    bilan = vague.corriger(db, essai=True, ask=ask, log=lines.append)
    assert calls == [] and bilan.paquets == 0
    assert any("2 réponse(s)" in line for line in lines)
    assert all(d.reason_code == "llm_queued" for d in _decisions(db))


def test_an_interrupted_wave_resumes_without_redoing_a_packet(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(cfg, "correction_batch_size", 1)     # deux paquets
    ask, calls = _engine()

    def stopping(system, text):
        if calls:
            raise cli_llm.CliError("usage limit reached", stop=True)
        return ask(system, text)
    bilan = vague.corriger(db, ask=stopping, log=lambda *_: None)
    assert bilan.paquets == 1 and "usage limit" in bilan.arret
    assert sorted(d.reason_code for d in _decisions(db)) == ["llm_partial", "llm_queued"]

    calls.clear()
    bilan = vague.corriger(db, ask=ask, log=lambda *_: None)
    assert bilan.paquets == 1 and len(calls) == 1 and len(calls[0][1]["reponses"]) == 1
    assert all(d.reason_code == "llm_partial" for d in _decisions(db))


# ----------------------------------------------------------------- apprecier

def test_appreciation_waits_for_the_command_then_is_written(db, monkeypatch):
    a, batch = _seed(db, n=1)
    db.add(SystemSetting(key="appreciation_synthesis_enabled", value_json={"value": True}))
    db.commit()
    progress = [{"competency_name": "Calcul · Soustraire", "pct_acquired": 0.7, "delta": 0.2}]
    monkeypatch.setattr(appreciation, "compute_competency_progress", lambda *a: progress)
    monkeypatch.setattr(appreciation.forgetting, "due_competencies", lambda *a: [])
    student = db.query(Student).first()
    built = appreciation.build_appreciation(db, a.id, student)
    assert built == {"progress": progress, "synthesis": "", "synthesis_pending": True}

    ask, _ = _engine()
    vague.corriger(db, ask=ask, log=lambda *_: None)     # finalise : appréciation en attente
    copy = db.query(Copy).first()
    assert copy.appreciation_json.get("synthesis_pending") is True
    rev = db.get(ScanBatch, batch.id).refine_rev or 0

    sent = []

    def write(system, text):
        payload = json.loads(text)
        sent.append(payload)
        return {"syntheses": [{"id": c["id"], "synthesis": "Beau progrès en soustraction, continue ainsi."}
                              for c in payload["copies"]]}
    assert vague.apprecier(db, essai=True, ask=write, log=lambda *_: None).syntheses == 0 and not sent
    bilan = vague.apprecier(db, ask=write, log=lambda *_: None)
    assert bilan.syntheses == 1 and sent[0]["copies"][0]["pseudonym"] == "p0"
    db.refresh(copy)
    assert copy.appreciation_json["synthesis"].startswith("Beau progrès")
    assert "synthesis_pending" not in copy.appreciation_json
    db.expire_all()
    assert (db.get(ScanBatch, batch.id).refine_rev or 0) == rev + 1   # overlay à régénérer
    assert vague.waiting_copies(db) == []


def test_a_synthesis_naming_the_student_is_refused(db):
    student = Student(name="Awa Koné", llm_pseudonym="p")
    assert vague.check_synthesis("Bravo Awa, continue.", student) is None
    assert vague.check_synthesis("mot " * 40, student) is None
    assert vague.check_synthesis("Bravo, continue.", student) == "Bravo, continue."


# ------------------------------------------------------------------- les CLI

def _fake_bin(tmp_path, name, stdout, code=0):
    log = tmp_path / f"{name}.args"
    script = tmp_path / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, json\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]) + '\\n' + sys.stdin.read())\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    return script, log


def test_claude_cli_is_called_without_tools_and_parsed(tmp_path, monkeypatch):
    out = json.dumps({"type": "result", "is_error": False,
                      "result": "Voici :\n```json\n{\"corrections\": []}\n```"})
    script, log = _fake_bin(tmp_path, "claude", out)
    monkeypatch.setenv("CLAUDE_BIN", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ne-doit-pas-passer")
    assert cli_llm.ask("haiku", "SYSTEME", "TEXTE") == {"corrections": []}
    args, stdin = log.read_text().split("\n", 1)
    args = json.loads(args)
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args and stdin == "TEXTE"


def test_codex_cli_with_a_fake_executable(tmp_path, monkeypatch):
    script, log = _fake_bin(tmp_path, "codex", 'réponse {"syntheses": [1]} fin')
    monkeypatch.setenv("CODEX_BIN", str(script))
    assert cli_llm.ask("terra", "SYSTEME", "TEXTE") == {"syntheses": [1]}
    args, stdin = log.read_text().split("\n", 1)
    args = json.loads(args)
    assert args[:2] == ["exec", "--skip-git-repo-check"]
    assert args[args.index("--model") + 1] == "gpt-6.1-terra"
    assert stdin == "SYSTEME\n\nTEXTE"


def test_usage_limit_stops_the_wave(tmp_path, monkeypatch):
    script, _ = _fake_bin(tmp_path, "codex", "", code=1)
    script.write_text(script.read_text().replace("sys.exit(1)", "sys.stderr.write('You hit your usage limit'); sys.exit(1)"))
    monkeypatch.setenv("CODEX_BIN", str(script))
    with pytest.raises(cli_llm.CliError) as err:
        cli_llm.ask("terra", "s", "t")
    assert err.value.stop


def test_luna_needs_its_model_id(monkeypatch):
    monkeypatch.setattr(cfg, "vague_model_luna", "")
    with pytest.raises(cli_llm.CliError) as err:
        cli_llm.engine_cli("luna")
    assert err.value.stop and "MATHPRINT_VAGUE_MODEL_LUNA" in str(err.value)
    monkeypatch.setattr(cfg, "vague_model_luna", "modele-luna")
    assert cli_llm.engine_cli("luna") == ("codex", "modele-luna")

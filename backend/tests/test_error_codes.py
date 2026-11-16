"""Le code d'erreur de l'élève : choisi par le correcteur dans la taxonomie quand aucun
message prévu ne convient, et cherché par le diagnostic pour une réponse écrite
fausse que l'exercice n'avait pas prévue. La note n'en dépend jamais."""
import json

import pytest

from app.models import (
    Assessment, Copy, CopyItem, DocumentPage, GradingDecision, Program, ResponseZone, ScanBatch,
    SchoolClass, Student, StudentLearningEvent, StudentResponse, Subject,
)
from app.services import diagnostic, error_journal, forge_contract, llm_grader, microcorrection, pipeline, scan_intake, vague

from test_vague import _decisions, _engine, _isolated, _seed, db  # noqa: F401


def _selection(db, decision):
    resp = db.get(StudentResponse, decision.response_id)
    return microcorrection.select(db.get(CopyItem, resp.copy_item_id), decision, resp)


# ------------------------------------------------------------- taxonomie
def test_the_grader_sees_only_the_domains_of_the_exercise():
    codes = {e["code"] for e in forge_contract.taxonomy_for(["math.eq.transposition", "math.frac.comparaison"])}
    assert "math.eq.produit_nul" in codes and "math.frac.multiplication" in codes
    assert "math.calc.erreur_calcul" in codes and "math.raison.justification" in codes   # toujours
    assert not any(c.startswith(("math.geo.", "math.cplx.")) for c in codes)
    everything = forge_contract.taxonomy_for([])
    assert len(everything) == len(forge_contract.error_codes("math")) - 1        # sans « autre »
    assert forge_contract.error_label("math.cplx.i_carre") and not forge_contract.error_label("math.x.y")


# ------------------------------------------------------- correcteur LLM
def test_the_grader_names_an_unplanned_error_from_the_taxonomy(db):
    _seed(db, n=1)
    ask, calls = _engine(lambda r: {"code_erreur": "math.calc.erreur_calcul"})
    vague.corriger(db, ask=ask, log=lambda *_: None)
    system, payload = calls[0]
    assert "code_erreur" in system and payload["exercices"][0]["erreurs_types"]
    decision = _decisions(db)[0]
    assert decision.evidence_json["llm"][0]["code_erreur"] == "math.calc.erreur_calcul"
    assert ("math.calc.erreur_calcul", None) in _selection(db, decision).errors
    assert not any(code == "autre" for code, _ in _selection(db, decision).errors)


def test_an_invented_code_is_ignored(db):
    _seed(db, n=1)
    ask, _ = _engine(lambda r: {"code_erreur": "math.invente.nulle_part"})
    vague.corriger(db, ask=ask, log=lambda *_: None)
    decision = _decisions(db)[0]
    assert "code_erreur" not in decision.evidence_json["llm"][0]
    assert ("autre", None) in _selection(db, decision).errors


def test_a_planned_message_keeps_its_own_code(db):
    _seed(db, n=1)
    ask, _ = _engine(lambda r: {"message_id": "e1", "code_erreur": "math.calc.erreur_calcul"})
    vague.corriger(db, ask=ask, log=lambda *_: None)
    errors = _selection(db, _decisions(db)[0]).errors
    assert ("calc.soustraction", "e1") in errors and ("math.calc.erreur_calcul", None) not in errors


# ------------------------------------------------- diagnostic : réponse courte
FEEDBACK = {"lines": 2, "fallback": {"text": "Reprends ton calcul."}, "correct": {"text": "Bien."},
            "blank": {"text": "Tu n'as rien écrit."},
            "wrong_answers": [{"value": "78", "text": "Tu as additionné.", "error": "math.calc.priorite_operations"}]}


def _short(db, monkeypatch, written: str):
    """Une copie, une réponse numérique courte : 3 + 5 × 3 = 18."""
    monkeypatch.setattr(pipeline.providers, "mathpix_ocr",
                        lambda *a, **k: {"latex": written, "text": written, "confidence": 0.99, "raw": {}})
    db.add_all([Subject(id="math", name="Mathématiques"), Program(id="fr", name="Programme français", origin_country_id="FR")])
    cls = SchoolClass(name="5A", grade_level="5e", program_id="fr")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Calcul", type="control", note_base=20)
    db.add(a)
    db.flush()
    stu = Student(class_id=cls.id, name="Awa", order_index=0, llm_pseudonym="p0")
    db.add(stu)
    db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
    db.add(copy)
    db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1)
    db.add(page)
    db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat-2", sequence=1, difficulty=3, response_type="short_text",
                    statement="Calcule $3 + 5 \\times 3$.", correction="$3 + 15 = 18$",
                    expected_json={"type": "integer", "value": 18},
                    grading_json={"comparator": "numeric", "max_score": 1, "bareme_points": 1, "feedback": FEEDBACK})
    db.add(item)
    db.flush()
    db.add(ResponseZone(page_id=page.id, item_id=item.id, type="text", x_pt=50, y_pt=50, w_pt=100, h_pt=40,
                        meta_json={}))
    db.commit()
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)
    return a, item


def test_an_unplanned_wrong_answer_waits_for_its_diagnosis_without_touching_the_grade(db, monkeypatch):
    a, _item = _short(db, monkeypatch, "20")
    decision = _decisions(db)[0]
    assert decision.score == 0 and decision.status == "auto"            # note inchangée, pas de revue
    state = decision.evidence_json["diagnostic"]
    assert state["etat"] == "en_attente" and state["champs"][0]["eleve"] == "20"
    assert microcorrection.select(db.get(CopyItem, _item.id), decision,
                                  db.query(StudentResponse).one()).errors == []   # rien à journaliser encore
    calls = []

    def ask(system, text):
        data = json.loads(text.split("\n\nRends")[0])
        calls.append((system, data))
        assert data["exercices"][0]["erreurs_types"] and "Awa" not in text
        return {"diagnostics": [{"id": r["id"], "code_erreur": "math.calc.erreur_calcul",
                                 "motif": "a trouvé 20 au lieu de 18"} for r in data["reponses"]]}
    vague.corriger(db, devoir=a.id, ask=ask, log=lambda *_: None)
    assert len(calls) == 1 and "ne la notes pas" in calls[0][0]
    db.refresh(decision)
    assert decision.score == 0 and decision.evidence_json["diagnostic"]["etat"] == "fait"
    assert ("math.calc.erreur_calcul", None) in microcorrection.select(
        db.get(CopyItem, _item.id), decision, db.query(StudentResponse).one()).errors
    # la copie était déjà finalisée : le journal de l'élève reçoit l'erreur
    events = db.query(StudentLearningEvent).filter_by(error_code="math.calc.erreur_calcul").all()
    assert len(events) == 1 and events[0].kind == error_journal.KIND_ERROR and events[0].observed
    assert vague.exporter_diagnostiquer(db) == []                       # file vide : rien n'est refait


def test_a_planned_wrong_answer_or_a_right_one_needs_no_diagnosis(db, monkeypatch):
    _short(db, monkeypatch, "78")
    decision = _decisions(db)[0]
    assert "diagnostic" not in (decision.evidence_json or {})
    assert microcorrection.select(db.query(CopyItem).one(), decision,
                                  db.query(StudentResponse).one()).errors == [("math.calc.priorite_operations", None)]


def test_a_code_outside_the_list_is_not_kept(db, monkeypatch):
    a, _item = _short(db, monkeypatch, "20")

    def ask(system, text):
        data = json.loads(text.split("\n\nRends")[0])
        return {"diagnostics": [{"id": r["id"], "code_erreur": "math.geo.pythagore_carres", "motif": "?"}
                                for r in data["reponses"]]}
    vague.corriger(db, devoir=a.id, ask=ask, log=lambda *_: None)
    decision = _decisions(db)[0]
    assert decision.evidence_json["diagnostic"]["champs"][0]["code"] is None   # hors des domaines de l'exercice
    assert db.query(StudentLearningEvent).filter_by(kind=error_journal.KIND_ERROR, observed=True).count() == 0


def test_a_bad_answer_leaves_the_diagnosis_waiting(db, monkeypatch):
    a, _item = _short(db, monkeypatch, "20")
    log = []
    vague.corriger(db, devoir=a.id, ask=lambda s, t: {"diagnostics": []}, log=log.append)
    assert any("réponse refusée" in line for line in log)
    assert _decisions(db)[0].evidence_json["diagnostic"]["etat"] == "en_attente"


def test_the_field_rules(monkeypatch):
    class Item:
        grading_json = {"comparator": "numeric", "max_score": 1, "feedback": FEEDBACK}
        expected_json = {"type": "integer", "value": 18}
    verdict = {"score": 0, "max_score": 1, "reason_code": "wrong"}
    assert diagnostic.fields_for(Item(), verdict, ocr_text="20")[0]["attendu"] == "18"
    assert diagnostic.fields_for(Item(), verdict, ocr_text="78") == []              # prévue
    assert diagnostic.fields_for(Item(), {**verdict, "score": 1}, ocr_text="18") == []
    assert diagnostic.fields_for(Item(), {**verdict, "reason_code": "blank"}, ocr_text="") == []
    assert diagnostic.fields_for(Item(), verdict, ocr_text="je pense que c'est vingt car 3+5=8 puis 8x3") == []
    assert llm_grader.answer_shape("je pense que c'est vingt car 3+5=8 puis 8x3") == llm_grader.LONG
    _ = (GradingDecision, ScanBatch, pytest)

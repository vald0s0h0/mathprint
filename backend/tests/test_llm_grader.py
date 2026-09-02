"""Aiguillage et application du correcteur LLM (services.llm_grader).

Ce qui est vérifié ici, c'est la RÈGLE D'ENVOI autant que la note : chaque appel
coûte, donc tout ce que le moteur déterministe sait trancher ne doit jamais
partir — et tout ce qu'il ne sait pas noter (un raisonnement, une réponse fausse
qui porte une méthode) doit partir.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (
    Assessment, Competency, Copy, CopyItem, DocumentPage, GradingDecision,
    ManualReview, OcrAttempt, ProviderConfig, ResponseZone, SchoolClass,
    Student, StudentResponse, SystemSetting,
)
from app.routers import scans as scans_router
from app.services import exercise_gen, grading, llm_grader, pipeline, providers, scan_intake


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# --------------------------------------------------------------- aiguillage

@pytest.mark.parametrize("raw, shape", [
    ("", llm_grader.BLANK),
    ("   ", llm_grader.BLANK),
    ("?", llm_grader.ILLEGIBLE),
    ("~~ ...", llm_grader.ILLEGIBLE),
    ("12", llm_grader.SHORT),
    ("-2,5", llm_grader.SHORT),
    ("3/4", llm_grader.SHORT),
    ("12 cm", llm_grader.SHORT),
    ("x = 12", llm_grader.SHORT),          # rappel de l'énoncé à gauche
    ("isocèle", llm_grader.SHORT),
    ("12 \\text{ cm}", llm_grader.SHORT),   # unité en LaTeX : pas un mot de l'élève
    ("isocèle rectangle", llm_grader.LONG),  # deux mots = une réponse à lire
    ("3x+2", llm_grader.LONG),             # expression développée
    ("50-32=19", llm_grader.LONG),         # opération POSÉE : une méthode à créditer
    ("18/24=9/12=3/4", llm_grader.LONG),   # chaîne de simplification
    ("12+8=", llm_grader.LONG),            # calcul resté sans résultat
    ("il reste 22 euros", llm_grader.LONG),
])
def test_answer_shape(raw, shape):
    assert llm_grader.answer_shape(raw) == shape


def _item(response_type, expected, grading_json, statement="Calcule.", correction="corr"):
    return SimpleNamespace(id="i1", catalog_id="cat-1", response_type=response_type,
                           statement=statement, correction=correction,
                           expected_json=expected, grading_json=grading_json)


def _verdict(score, max_score, reason_code="numeric_mismatch"):
    return {"score": score, "max_score": max_score, "reason_code": reason_code}


def test_cv_answers_never_reach_the_llm():
    """QCM, grille, points à relier, tracé : lus par ordinateur, verdict déjà
    sûr — un LLM n'y ajouterait qu'une dépense."""
    for comparator in ("qcm", "grid", "matching", "manual"):
        item = _item("qcm_multiple", {"type": "choice", "correct": [0]},
                     {"comparator": comparator, "max_score": 4, "bareme_points": 1})
        assert llm_grader.plan(item, _verdict(1, 4, "qcm_partial")) is None


def test_weak_mathpix_reading_goes_directly_to_the_teacher():
    """Même sur un raisonnement, un texte transcrit sous 90 % ne peut pas être
    donné au correcteur LLM comme s'il s'agissait des mots certains de l'élève.
    Le professeur relit directement le crop et la transcription Mathpix."""
    item = _item("multiline_text", {"type": "rubric"},
                 {"comparator": "rubric", "max_score": 2, "bareme_points": 2,
                  "rubric": [{"description": "prix total", "expected_text": "$4 \\times 7$",
                              "points": 1}]})
    assert llm_grader.plan(item, _verdict(0, 2, "ocr_low_confidence"),
                           ocr_text="4x7=28 donc il reste 22") is None


def test_blank_reasoning_costs_nothing():
    item = _item("multiline_text", {"type": "rubric"},
                 {"comparator": "rubric", "max_score": 2, "bareme_points": 2})
    assert llm_grader.plan(item, _verdict(0, 2, "blank"), ocr_text="   ") is None


def test_short_answer_right_or_wrong_stays_deterministic():
    """Le cœur de l'économie : une réponse juste n'est pas re-vérifiée, et une
    réponse fausse COURTE (un nombre) reste fausse sans appel — il n'y a rien à
    interpréter dans « 13 » quand on attendait « 12 »."""
    item = _item("short_text", {"type": "integer", "value": 12},
                 {"comparator": "numeric", "max_score": 1, "bareme_points": 0.5})
    assert llm_grader.plan(item, _verdict(1, 1, "numeric_match"), ocr_text="12") is None
    assert llm_grader.plan(item, _verdict(0, 1), ocr_text="13") is None
    assert llm_grader.plan(item, _verdict(0, 1, "blank"), ocr_text="") is None
    assert llm_grader.plan(item, _verdict(0.5, 1, "numeric_rounded"), ocr_text="0,7") is None
    assert llm_grader.plan(item, _verdict(0, 1, "ocr_low_confidence"), ocr_text="?") is None


def test_long_wrong_answer_goes_to_the_llm():
    """« 50-32=19 » : le résultat est faux mais la méthode est écrite — c'est là
    qu'un correcteur humain donne des points, donc là qu'on appelle le LLM."""
    item = _item("short_text", {"type": "integer", "value": 18},
                 {"comparator": "numeric", "max_score": 1, "bareme_points": 1})
    task = llm_grader.plan(item, _verdict(0, 1), ocr_text="50-32=19")
    assert task is not None
    assert task.fields[0].expected == "18" and task.fields[0].student == "50-32=19"


def test_table_sends_only_the_cells_worth_an_opinion():
    """Un tableau part case par case : les justes sont acquises, les fausses
    courtes restent fausses, la case illisible va au professeur, et seule la
    case fausse ET longue est payée au correcteur."""
    cells = [[{"type": "integer", "value": 5}], [{"type": "integer", "value": 8}],
             [{"type": "integer", "value": 3}], [{"type": "integer", "value": 7}],
             [{"type": "integer", "value": 9}]]
    g = {"comparator": "table_cells", "max_score": 5, "bareme_points": 2.5,
         "cells": cells, "row_labels": ["a", "b", "c", "d", "e"]}
    item = _item("table_fill", {"type": "table", "cells": cells}, g)
    task = llm_grader.plan(item, _verdict(1, 5, "table_cell_unreadable"),
                           cell_texts=["5", "9", "12-9=4", "", "?"])
    assert [f.cell_index for f in task.fields] == [2]     # la seule case longue
    assert task.base_score == 1.0                          # la case juste
    assert task.unreadable == 1                            # « ? » -> professeur
    assert task.fields[0].bareme == 0.5                    # 2,5 pt / 5 cases
    assert task.fields[0].label == "c"


# ------------------------------------------------------------------ notation

@pytest.mark.parametrize("verdict, raw, bareme, expected", [
    ("juste", 0, 1.5, 1.5),        # le verdict prime : « juste » = tout le barème
    ("faux", 1.5, 1.5, 0.0),
    ("illisible", 1, 1.5, 0.0),
    ("partiel", 0.7, 1.5, 0.75),   # ramené au pas de 0,125
    ("partiel", 9, 1.5, 1.5),      # jamais plus que le barème
    ("partiel", -3, 1.5, 0.0),     # jamais de points négatifs
    ("partiel", "0,375", 1.5, 0.375),
    ("partiel", None, 1.5, 0.0),
])
def test_field_points(verdict, raw, bareme, expected):
    assert llm_grader.field_points(raw, verdict, bareme) == pytest.approx(expected)


def test_batches_group_by_exercise_and_quote_the_statement_once():
    """Économie de tokens : les réponses d'un même exercice partent ensemble,
    son énoncé n'est donc écrit qu'UNE fois pour toute la classe."""
    tasks = []
    for i in range(3):
        task = llm_grader.Task(exercise_key="ex-1", statement="Combien reste-t-il ?",
                               correction="corr", max_score=1, base_score=0, fields=[])
        task.fields.append(llm_grader.Field(label="", expected="18", student=f"5{i}-32=19",
                                            bareme=1, weight=1))
        tasks.append(task)
    pairs = [(t, f) for t in tasks for f in t.fields]
    for i, (_t, f) in enumerate(pairs):
        f.key = f"r{i + 1}"
    batches = llm_grader._batches(pairs, 8)
    assert len(batches) == 1
    payload = llm_grader._payload(batches[0])
    assert len(payload["exercices"]) == 1
    assert len(payload["reponses"]) == 3
    assert payload["reponses"][0]["exercice"] == payload["reponses"][2]["exercice"]
    assert payload["exercices"][0]["enonce"].count("Combien") == 1


def test_grading_json_accepts_points_only_and_key_variants():
    task = llm_grader.Task(exercise_key="ex", statement="Calcule.", correction="",
                           max_score=1, base_score=0, fields=[])
    field = llm_grader.Field(label="", expected="18", student="50-32=19",
                             bareme=1, weight=1, key="r1")
    task.fields.append(field)
    normalized = llm_grader._normalize_grading_response(
        {"results": [{"response_id": "r1", "score": "0,5",
                       "confidence": 95, "reason": "méthode juste"}]},
        [(task, field)])
    assert normalized == {"corrections": [{
        "id": "r1", "verdict": "partiel", "points": 0.5,
        "confiance": 0.95, "motif": "méthode juste",
    }]}


def test_grading_json_rejects_missing_ids_and_inconsistent_points():
    task = llm_grader.Task(exercise_key="ex", statement="Calcule.", correction="",
                           max_score=1, base_score=0, fields=[])
    field = llm_grader.Field(label="", expected="18", student="19",
                             bareme=1, weight=1, key="r1")
    with pytest.raises(ValueError, match="incohérent"):
        llm_grader._normalize_grading_response(
            {"corrections": [{"id": "r1", "verdict": "juste", "points": 0}]},
            [(task, field)])
    with pytest.raises(ValueError, match="manquants"):
        llm_grader._normalize_grading_response({"corrections": []}, [(task, field)])


def test_deepseek_schema_retry_receives_and_repairs_the_bad_output(db, monkeypatch):
    db.add(ProviderConfig(provider="deepseek-flash", model="deepseek-v4-flash",
                          encrypted_secret="test", active=True))
    db.commit()
    contents = ['{"wrong_key": []}',
                '```json\n{"corrections":[{"id":"r1"}]}\n```']
    sent = []

    class FakeResponse:
        def __init__(self, content): self.content = content
        def raise_for_status(self): return None
        def json(self):
            return {"choices": [{"message": {"content": self.content},
                                  "finish_reason": "stop"}], "usage": {}}

    def fake_post(_url, **kwargs):
        sent.append([dict(message) for message in kwargs["json_body"]["messages"]])
        return FakeResponse(contents[len(sent) - 1])

    def validator(raw):
        if not isinstance(raw, dict) or "corrections" not in raw:
            raise ValueError("clé corrections absente")
        return raw

    monkeypatch.setattr(providers, "_post_with_deadline", fake_post)
    out = providers.deepseek_json(
        db, "answer_grading", "system", {"reponses": [{"id": "r1"}]},
        validator=validator, repair_instruction="Répare le contrat demandé.")
    assert out["corrections"][0]["id"] == "r1"
    assert len(sent) == 2
    assert sent[1][-2]["role"] == "assistant"      # sortie fautive fournie au retry
    assert "clé corrections absente" in sent[1][-1]["content"]


def test_deepseek_empty_json_retries_as_text_without_thinking(db, monkeypatch):
    """Le mode JSON DeepSeek peut rendre content vide. Le retry change de
    transport, mais le validateur local garde le même contrat strict."""
    db.add(ProviderConfig(provider="deepseek-flash", model="deepseek-v4-flash",
                          encrypted_secret="test", active=True))
    db.commit()
    sent = []

    class FakeResponse:
        def __init__(self, index): self.index = index
        def raise_for_status(self): return None
        def json(self):
            if self.index == 0:
                return {"choices": [{"message": {"content": "",
                                                   "reasoning_content": "analyse"},
                                     "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 41}}
            return {"choices": [{"message": {
                        "content": '{"corrections":[{"id":"r1"}]}'},
                                     "finish_reason": "stop"}], "usage": {}}

    def fake_post(_url, **kwargs):
        sent.append(json.loads(json.dumps(kwargs["json_body"])))
        return FakeResponse(len(sent) - 1)

    monkeypatch.setattr(providers, "_post_with_deadline", fake_post)

    def validator(raw):
        if "corrections" not in raw:
            raise ValueError("schéma")
        return raw

    out = providers.deepseek_json(
        db, "answer_grading", "Réponds en json.", {"reponses": [{"id": "r1"}]},
        thinking=False, validator=validator)

    assert out["corrections"][0]["id"] == "r1"
    assert sent[0]["thinking"] == {"type": "disabled"}
    assert sent[1]["thinking"] == {"type": "disabled"}
    assert sent[0]["response_format"] == {"type": "json_object"}
    assert sent[1]["response_format"] == {"type": "text"}
    assert "contenu DeepSeek vide" in sent[1]["messages"][-1]["content"]


# ------------------------------------------------------- tableaux à ordre libre

def test_unordered_table_credits_a_shuffled_list():
    """« Écris tous les diviseurs de 6 » : l'élève les écrit dans le désordre, et
    c'est juste. Sans appariement, la même copie valait 1 case sur 4."""
    cells = [[{"type": "integer", "value": 1}, {"type": "integer", "value": 2}],
             [{"type": "integer", "value": 3}, {"type": "integer", "value": 6}]]
    g = {"comparator": "table_cells", "max_score": 4, "cells": cells, "unordered": True}
    assert grading.table_credits(g, ["6", "3", "2", "1"]) == [1.0, 1.0, 1.0, 1.0]
    # une seule réponse manquante : les trois autres restent acquises
    assert grading.table_credits(g, ["6", "3", "2", ""]) == [1.0, 1.0, 1.0, 0.0]
    # un doublon ne paie pas deux fois (chaque attendu est consommé une fois)
    assert grading.table_credits(g, ["6", "6", "2", "1"]) == [1.0, 0.0, 1.0, 1.0]
    verdict = grading.grade({"type": "table", "cells": cells}, g, "", 1.0,
                            cell_texts=["6", "3", "2", "1"])
    assert verdict["score"] == 4 and verdict["tier"] == "A"


def test_ordered_table_still_compares_in_place():
    """Le drapeau ne s'applique pas tout seul : un tableau de valeurs garde une
    réponse attendue PAR CASE."""
    cells = [[{"type": "integer", "value": 1}, {"type": "integer", "value": 2}]]
    g = {"comparator": "table_cells", "max_score": 2, "cells": cells}
    assert grading.table_credits(g, ["2", "1"]) == [0.0, 0.0]


def test_list_written_in_one_box_is_compared_as_a_set():
    """Filet de sécurité pour les exercices déjà en banque qui demandent une
    liste dans UNE case : l'ordre d'écriture ne rend pas la réponse fausse."""
    cell = {"type": "text", "value": "1, 2, 3, 6"}
    assert grading.cell_credit(cell, "6, 3, 2, 1") == 1.0
    assert grading.cell_credit(cell, "1, 2, 3") == 0.0


def test_a_cell_graded_by_the_llm_lands_in_the_note_and_on_the_copy(db):
    """La case tranchée par le correcteur compte dans la note ET porte sa marque
    sur la copie corrigée : sans les crédits joints à la copie, une case payée
    par le correcteur s'imprimait fausse (les marques se déduisent du texte)."""
    cells = [[{"type": "integer", "value": 5}], [{"type": "integer", "value": 8}]]
    g = {"comparator": "table_cells", "max_score": 2, "bareme_points": 1,
         "cells": cells, "row_labels": ["a", "b"]}
    item = _item("table_fill", {"type": "table", "cells": cells}, g)
    task = llm_grader.plan(item, _verdict(1, 2, "table_mismatch"),
                           cell_texts=["5", "3+4=7"])
    decision = GradingDecision(response_id="resp-x", source="deterministic",
                               score=1.0, max_score=2.0, tier="D",
                               reason_code="llm_pending", status="review_pending")
    db.add(decision)
    db.flush()
    task.decision_id, task.zone_id = decision.id, "zone-x"
    task.fields[0].result = {"verdict": "partiel", "points": 0.25,
                             "confiance": 0.95, "motif": "addition ratée"}

    assert llm_grader._apply(db, task) is False       # plus rien pour le professeur
    assert decision.status == "auto" and decision.source == "deepseek"
    # 1 case juste + une demi-case créditée = 1,5 unité sur 2 -> 0,75 pt de barème
    assert decision.score == pytest.approx(1.5)
    # L'UI corrige une cellule à la fois : l'identifiant atomique empêche
    # d'afficher à côté d'elle les avis LLM des autres cellules.
    assert decision.evidence_json["llm"][0]["cell_index"] == 1

    ocr = db.query(OcrAttempt).filter_by(zone_id="zone-x").first()
    assert grading.cell_marks(g, ocr.raw_json["cells"],
                              ocr.raw_json["cell_credits"]) == [1.0, 0.5]


def test_only_a_low_confidence_llm_verdict_goes_to_manual_review(db):
    item = _item("multiline_text", {"type": "rubric"},
                 {"comparator": "rubric", "max_score": 1, "bareme_points": 1})
    task = llm_grader.plan(item, _verdict(0, 1, "llm_pending"),
                           ocr_text="50-32=19 donc il reste 19")
    decision = GradingDecision(response_id="resp-low", source="deterministic",
                               score=0, max_score=1, tier="D",
                               reason_code="llm_pending", status="review_pending")
    db.add(decision); db.flush()
    task.decision_id = decision.id
    task.fields[0].result = {"verdict": "partiel", "points": 0.5,
                             "confiance": 0.62, "motif": "réponse ambiguë"}

    assert llm_grader._apply(db, task) is True
    assert decision.reason_code == "llm_low_confidence"
    assert decision.score == 0  # le score peu fiable n'est jamais appliqué

    # Le seuil est persisté, pas figé dans la configuration du processus.
    db.add(SystemSetting(key="llm_confidence_threshold", value_json={"value": 0.95}))
    db.flush()
    task.fields[0].result["confiance"] = 0.94
    assert llm_grader._apply(db, task) is True
    assert decision.reason_code == "llm_low_confidence"

    # Égal au seuil = automatique (seules les valeurs EN DESSOUS partent en
    # manuel). Une ancienne ambiguïté OCR ne peut plus annuler ce verdict.
    task.unreadable = 1
    task.fields[0].result["confiance"] = 0.95
    assert llm_grader._apply(db, task) is False
    assert decision.status == "auto" and decision.score == pytest.approx(0.5)

    # Modifier le réglage réconcilie aussi les décisions déjà enregistrées,
    # sans recalculer leurs points ni rappeler DeepSeek.
    setting = db.get(SystemSetting, "llm_confidence_threshold")
    setting.value_json = {"value": 0.96}
    db.flush()
    assert llm_grader.sync_confidence_reviews(db) == 1
    assert decision.status == "review_pending"
    assert decision.score == 0
    setting.value_json = {"value": 0.90}
    db.flush()
    assert llm_grader.sync_confidence_reviews(db) == 1
    assert decision.status == "auto"
    assert decision.score == pytest.approx(0.5)


def test_multi_cell_review_is_triggered_only_by_the_low_confidence_cell(db):
    """Une réponse peut contenir une case à 97 % et une autre à 85 %. La revue
    appartient à la seconde ; la première conserve son crédit automatique."""
    db.add(SystemSetting(key="llm_confidence_threshold", value_json={"value": 0.89}))
    decision = GradingDecision(response_id="resp-cells", source="deterministic",
                               score=0, max_score=2, tier="D",
                               reason_code="llm_pending", status="review_pending")
    db.add(decision); db.flush()
    task = llm_grader.Task(
        exercise_key="table", statement="Complète.", correction="",
        max_score=2, base_score=0,
        fields=[
            llm_grader.Field(label="Case 1", expected="5", student="5",
                             bareme=1, weight=1, cell_index=0,
                             result={"verdict": "juste", "points": 1,
                                     "confiance": 0.97, "motif": "exact"}),
            llm_grader.Field(label="Case 2", expected="8", student="3+4",
                             bareme=1, weight=1, cell_index=1,
                             result={"verdict": "partiel", "points": 0.5,
                                     "confiance": 0.85, "motif": "ambigu"}),
        ], cell_credits=[None, None], decision_id=decision.id)

    assert llm_grader._apply(db, task) is True
    assert decision.reason_code == "llm_low_confidence"
    assert decision.score == pytest.approx(1.0)  # seule la case à 97 % est appliquée
    assert task.cell_credits == [1.0, 0.0]
    assert [n["confidence"] for n in decision.evidence_json["llm"]] == [0.97, 0.85]


# ------------------------------------------------- ce que le prompt promet

def test_the_shared_contract_forbids_several_answers_in_one_box():
    """Règle de CORRECTION avant d'être une règle de rédaction : le moteur
    compare une case à UNE valeur attendue. Le validateur ne peut pas
    l'imposer (« 2, 3 et 5 » est un texte valide), seul le prompt le peut —
    et il est lu par le générateur COMME par le relecteur."""
    contract = exercise_gen.format_contract("INTRO")
    assert "UNE CASE = UNE SEULE RÉPONSE (règle absolue)" in contract
    assert "sépare les valeurs par des virgules" in contract
    assert "un par case. Il y en a $8$." in contract
    # le tableau à ordre libre est décrit là où le validateur l'attend
    assert '"unordered":true' in contract
    assert "la correction apparie" in contract
    # ...et l'écueil est nommé : une liste attendue doit être UNIQUE
    assert "cite trois multiples de $7$" in contract


def test_the_validator_keeps_the_unordered_flag():
    comp = Competency(code="A1.1", short_id="A1.1", label="Divisibilité",
                      domain_code="A", domain_name="Nombres et calculs",
                      chapter_code="A1", chapter_name="Divisibilité", order_index=0)
    cells = [[{"type": "integer", "value": 1}, {"type": "integer", "value": 2}],
             [{"type": "integer", "value": 3}, {"type": "integer", "value": 6}]]
    raw = {"kind": "application", "bareme_points": 1,
           "statement": "Écris tous les diviseurs de $6$, un par case. Il y en a $4$.",
           "correction": "Les diviseurs de $6$ sont $1$, $2$, $3$ et $6$.",
           "response_type": "table_fill",
           "answer": {"type": "table", "rows": 2, "cols": 2, "unordered": True,
                      "cells": cells}}
    ex = exercise_gen._validate_exercise(raw, comp, None, set())
    assert ex["grading"]["unordered"] is True

    # une cellule DÉJÀ imprimée fixe la place des autres : l'ordre redevient
    # signifiant, le drapeau est retiré (jamais motif de rejet).
    given = [[{"type": "integer", "value": 1, "given": True},
              {"type": "integer", "value": 2}],
             [{"type": "integer", "value": 3}, {"type": "integer", "value": 6}]]
    raw2 = dict(raw, answer={**raw["answer"], "cells": given},
                statement="Complète le tableau des diviseurs de $6$.")
    ex2 = exercise_gen._validate_exercise(raw2, comp, None, set())
    assert ex2["grading"]["unordered"] is False


# ------------------------------------------------------------ bout en bout

def _fixed_ocr(monkeypatch, text="4x7=28 et 50-28=22, il lui reste 22 euros"):
    """OCR figé. Le repli hors ligne de Mathpix tire sa réponse d'un hachage de
    l'identifiant de copie — aléatoire d'une exécution à l'autre : sans ce
    verrou, une copie sur dix-sept ressortirait « ? » et le test dépendrait du
    tirage."""
    monkeypatch.setattr(
        pipeline.providers, "mathpix_ocr",
        lambda *a, **k: {"latex": text, "text": text, "confidence": 0.9, "raw": {}})


def _stub_llm(db, monkeypatch, state=None):
    """Correcteur gréé comme en production : une clé DeepSeek configurée — sans
    elle, la plateforme refuse de noter et laisse tout au professeur (§ jamais
    de note inventée par un repli hors ligne) — et l'appel HTTP remplacé par un
    verdict canné. Retourne la liste des charges utiles envoyées."""
    state = state if state is not None else {}
    db.add(ProviderConfig(provider="deepseek-flash", model="deepseek-v4-flash",
                          encrypted_secret="clé-de-test", active=True))
    db.commit()
    sent = []

    def _call(_db, operation, system, payload, **kw):
        sent.append(payload)
        if state.get("fail"):
            raise state["fail"]
        return {"corrections": [
            {"id": r["id"], "verdict": "partiel", "points": r["bareme"] / 2,
             "confiance": 0.95, "motif": "mock"} for r in payload["reponses"]]}

    monkeypatch.setattr(llm_grader.providers, "deepseek_json", _call)
    return sent


def _seed_reasoning(db):
    """Une copie avec un problème rédigé (multiline_text) : le seul format qui
    part systématiquement au correcteur."""
    cls = SchoolClass(name="5C", grade_level="5e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle", type="control", note_base=20)
    db.add(a)
    db.flush()
    steps = [{"description": "prix total", "expected_text": "$4 \\times 7 = 28$", "points": 1},
             {"description": "monnaie rendue", "expected_text": "$50 - 28 = 22$", "points": 1}]
    for i in range(2):
        stu = Student(class_id=cls.id, name=f"E{i} X", order_index=i, llm_pseudonym=f"p{i}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        item = CopyItem(
            copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
            response_type="multiline_text", statement="Combien lui rend-on ?",
            correction="$4 \\times 7 = 28$ puis $50 - 28 = 22$",
            expected_json={"type": "rubric", "steps": steps},
            grading_json={"comparator": "rubric", "max_score": 2, "bareme_points": 2,
                          "rubric": steps, "steps": steps, "lines": 6})
        db.add(item)
        db.flush()
        db.add(ResponseZone(page_id=page.id, item_id=item.id, type="text",
                            x_pt=50, y_pt=50, w_pt=100, h_pt=60, meta_json={}))
    db.commit()
    return a


def test_reasoning_is_graded_by_the_llm_end_to_end(db, tmp_path, monkeypatch):
    """Chemin complet : le pipeline met les raisonnements en file, les corrige
    par paquets en fin de lot, réécrit les décisions et RETIRE la revue
    provisoire — le professeur ne voit pas passer ce que le correcteur a tranché."""
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    _fixed_ocr(monkeypatch)
    sent = _stub_llm(db, monkeypatch)
    a = _seed_reasoning(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()

    pipeline.process_batch(db, batch)

    decisions = db.query(GradingDecision).all()
    assert len(decisions) == 2
    for d in decisions:
        assert d.source == "deepseek" and d.tier == "C" and d.status == "auto"
        assert d.score == pytest.approx(1.0)      # repli hors ligne : moitié du barème
    assert db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count() == 0
    # les deux copies ont voyagé dans le MÊME appel (même exercice), dont
    # l'énoncé n'est écrit qu'une fois
    assert len(sent) == 1 and len(sent[0]["reponses"]) == 2
    assert len(sent[0]["exercices"]) == 1

    # le professeur relit ce que le correcteur a décidé, et pourquoi
    items = scans_router.list_items(batch.id, "all", db)
    assert [it["decision_source"] for it in items] == ["deepseek", "deepseek"]
    assert items[0]["current_points"] == pytest.approx(1.0)   # 2 pt de barème, moitié
    note = items[0]["llm_notes"][0]
    assert note["verdict"] == "partiel" and note["motif"] == "mock"
    assert note["points"] == pytest.approx(1.0) and note["bareme"] == 2


def test_llm_failure_leaves_the_answer_to_the_teacher(db, tmp_path, monkeypatch):
    """Panne du correcteur (budget atteint, délai dépassé) : aucune note
    fabriquée, la réponse reste en revue professeur avec son score déterministe."""
    monkeypatch.setattr(cfg, "data_dir", tmp_path)

    _fixed_ocr(monkeypatch)
    _stub_llm(db, monkeypatch, {"fail": RuntimeError("budget quotidien atteint")})
    a = _seed_reasoning(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()

    pipeline.process_batch(db, batch)

    for d in db.query(GradingDecision).all():
        assert d.status == "review_pending" and d.score == 0.0
        assert d.reason_code == "llm_unavailable"
    assert db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count() == 2
    assert db.get(type(batch), batch.id).status == "review_pending"
    # la CAUSE suit la réponse jusqu'à la file du professeur (§ budget atteint
    # en silence : incident du 02/08 sur la pipeline Indigo)
    note = scans_router.list_items(batch.id, "flagged", db)[0]["llm_notes"][0]
    assert note["verdict"] == "indisponible" and "budget" in note["motif"]
    assert note["confidence"] is None


def test_without_a_deepseek_key_no_note_is_invented(db, tmp_path, monkeypatch):
    """Aucune clé configurée : le repli hors ligne ne doit PAS noter. Une
    correction simulée sur de vraies copies tromperait le professeur — même
    raison qui fait déjà refuser le dépôt d'un scan sans clé Mathpix."""
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    _fixed_ocr(monkeypatch)
    a = _seed_reasoning(db)                 # aucun ProviderConfig : hors ligne
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()

    pipeline.process_batch(db, batch)

    for d in db.query(GradingDecision).all():
        assert d.status == "review_pending" and d.score == 0.0
        assert d.source == "deterministic"   # surtout pas « deepseek »
    note = scans_router.list_items(batch.id, "flagged", db)[0]["llm_notes"][0]
    assert "Aucune clé DeepSeek configurée" in note["motif"]


def test_a_retry_regrades_what_the_correcteur_missed(db, tmp_path, monkeypatch):
    """Une panne passagère ne condamne pas les copies à la correction manuelle :
    relancer le lot repasse au correcteur ce qu'il n'avait pas pu noter — sans
    refaire l'OCR, et sans toucher aux réponses déjà notées."""
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    _fixed_ocr(monkeypatch)
    state = {"fail": RuntimeError("budget quotidien atteint")}
    _stub_llm(db, monkeypatch, state)
    a = _seed_reasoning(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)
    assert db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count() == 2

    state["fail"] = None                    # correcteur de nouveau disponible
    pipeline.process_batch(db, batch)

    assert db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count() == 0
    for d in db.query(GradingDecision).all():
        assert d.source == "deepseek" and d.status == "auto"
        assert d.score == pytest.approx(1.0)
    # plus de halte « Valider » : la dernière revue résolue par le correcteur
    # LLM enchaîne directement sur la finalisation (02/09)
    assert db.get(type(batch), batch.id).status == "overlay_ready"


def test_qcm_copy_never_calls_the_correcteur(db, tmp_path, monkeypatch):
    """Une copie entièrement cochée ne déclenche AUCUN appel : la lecture CV
    suffit (§ toutes réponses CV : on ne change rien)."""
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    sent = _stub_llm(db, monkeypatch)
    cls = SchoolClass(name="5D", grade_level="5e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Ctrl", type="control", note_base=20)
    db.add(a)
    db.flush()
    stu = Student(class_id=cls.id, name="E X", llm_pseudonym="p")
    db.add(stu)
    db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
    db.add(copy)
    db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1)
    db.add(page)
    db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
                    response_type="qcm_multiple", statement="Coche.", correction="c",
                    expected_json={"type": "choice", "correct": [0, 1]},
                    grading_json={"comparator": "qcm", "max_score": 4, "choices":
                                  ["a", "b", "c", "d"], "bareme_points": 1})
    db.add(item)
    db.flush()
    db.add(ResponseZone(page_id=page.id, item_id=item.id, type="qcm",
                        x_pt=50, y_pt=50, w_pt=100, h_pt=60, meta_json={}))
    db.commit()
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()

    pipeline.process_batch(db, batch)

    assert sent == []                       # aucun appel : tout est lu par CV
    assert db.query(StudentResponse).count() == 1

"""Diagnostic des réponses écrites fausses que l'exercice n'avait pas prévues.

Le moteur déterministe note une réponse courte (« 42 ») sans modèle : juste,
fausse, ou fausse et prévue (une réponse fausse de l'exercice, avec son code
d'erreur). Une réponse fausse IMPRÉVUE n'entrait dans aucun suivi : la note était
juste, mais l'élève n'avait aucune erreur nommée.

Ce module la fait relire par un modèle, sans jamais toucher à la note : il ne
cherche que l'erreur type, dans la taxonomie fermée de la matière (les domaines de
l'exercice). La file est durable, portée par la décision elle-même
(`evidence_json["diagnostic"]`), et ne met jamais une réponse en revue
professeur. En mode api, le pipeline la traite aussitôt ; en mode commande, la
vague (`corriger`) la relève depuis le Mac, comme les corrections.

Le code retenu entre au journal de l'élève (microcorrection → error_journal).
"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Assessment, Copy, CopyItem, CopyResult, GradingDecision, StudentResponse
from . import error_journal, forge_contract, llm_grader, providers
from . import grading as grader

logger = logging.getLogger(__name__)
KEY = "diagnostic"
WAITING, DONE = "en_attente", "fait"
_SKIP_REASONS = ("blank", "ocr_low_confidence", "table_unreadable", "form_mismatch")
_NO_COMPARATORS = ("qcm", "grid", "matching", "manual", "composite", "rubric")


def _wrong_planned(feedback: dict, text: str, cell=None) -> bool:
    from .microcorrection import _wrong_answer
    return _wrong_answer(feedback, text, cell) is not None


def fields_for(item, verdict: dict, *, ocr_text: str = "", cell_texts: list[str] | None = None) -> list[dict]:
    """Les champs d'une réponse à diagnostiquer : faux, écrits, courts, et absents
    des réponses fausses prévues. Vide si rien n'est à chercher."""
    grading = item.grading_json or {}
    feedback = grading.get("feedback")
    if not isinstance(feedback, dict) or grading.get("comparator") in _NO_COMPARATORS:
        return []
    if verdict.get("reason_code") in _SKIP_REASONS or not float(verdict.get("max_score") or 0):
        return []
    if grading.get("comparator") == "table_cells":
        flat = grader.fillable_cells(grading)
        if not flat or cell_texts is None or len(cell_texts) != len(flat):
            return []
        positions = [(r, c) for r, row in enumerate(grading.get("cells") or [])
                     for c, cell in enumerate(row) if not cell.get("given")]
        out = []
        for k, (cell, raw, credit) in enumerate(zip(flat, cell_texts, grader.table_credits(grading, cell_texts))):
            if cell.get("input") == "single_choice" or credit is None or credit > 0:
                continue
            if llm_grader.answer_shape(raw) != llm_grader.SHORT or _wrong_planned(feedback, raw, positions[k]):
                continue
            out.append({"cell": k, "question": llm_grader._cell_label(grading, k),
                        "attendu": " ou ".join(grader.cell_reference_text(c)
                                               for c in grader.expected_alternatives(grading, k)),
                        "eleve": raw})
        return out
    if float(verdict.get("score") or 0) > 0 or llm_grader.answer_shape(ocr_text) != llm_grader.SHORT:
        return []
    if _wrong_planned(feedback, ocr_text):
        return []
    return [{"cell": None, "question": "",
             "attendu": llm_grader._expected_text(item.expected_json or {}), "eleve": ocr_text}]


def mark(decision: GradingDecision, fields: list[dict]) -> None:
    """Met la réponse en file de diagnostic, sans changer ni sa note ni son statut."""
    if fields:
        decision.evidence_json = {**(decision.evidence_json or {}),
                                  KEY: {"etat": WAITING, "champs": fields}}


def codes(decision, cell: int | None = None) -> list[str]:
    """Codes retenus pour une réponse (ou une case), pour le journal de l'élève."""
    state = (decision.evidence_json or {}).get(KEY) or {}
    return [f["code"] for f in state.get("champs") or []
            if f.get("code") and f.get("cell") == cell and forge_contract.error_label(f["code"])]


def all_codes(decision) -> list[str]:
    """Tous les codes retenus pour une réponse, toutes cases confondues."""
    state = (decision.evidence_json or {}).get(KEY) or {}
    return [f["code"] for f in state.get("champs") or []
            if f.get("code") and forge_contract.error_label(f["code"])]


# ------------------------------------------------------------------ file
def pending(db: Session, *, assessment_id: str | None = None, class_id: str | None = None) -> list[dict]:
    """[{decision, item, assessment_id, taxonomy}] des réponses en attente de diagnostic."""
    q = (db.query(GradingDecision, CopyItem, Copy)
         .join(StudentResponse, GradingDecision.response_id == StudentResponse.id)
         .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
         .join(Copy, CopyItem.copy_id == Copy.id))
    if assessment_id:
        q = q.filter(Copy.assessment_id == assessment_id)
    if class_id:
        q = q.join(Assessment, Copy.assessment_id == Assessment.id).filter(Assessment.class_id == class_id)
    out = []
    for decision, item, copy in q.order_by(Copy.assessment_id, CopyItem.sequence).all():
        state = (decision.evidence_json or {}).get(KEY) or {}
        if state.get("etat") != WAITING:
            continue
        if decision.reason_code and decision.reason_code.startswith("teacher_"):
            continue            # le professeur a repris la réponse : son verdict fait foi
        out.append({"decision": decision, "item": item, "assessment_id": copy.assessment_id,
                    "copy": copy,
                    "taxonomy": forge_contract.taxonomy_for(llm_grader._planned_codes(item.grading_json or {}))})
    return out


def _exercise_key(item) -> str:
    return f"{item.catalog_id}|{hashlib.sha1((item.statement or '').encode()).hexdigest()[:8]}"


def payload(entries: list[dict]) -> tuple[dict, list[dict]]:
    """(données envoyées, champs : clé → décision et rang). Un exercice n'est écrit
    qu'une fois, avec ses erreurs types ; les réponses ne portent aucun nom."""
    exercises: dict[str, dict] = {}
    answers, keys = [], []
    for entry in entries:
        item = entry["item"]
        key = _exercise_key(item)
        if key not in exercises:
            exercises[key] = {"ref": chr(ord("A") + len(exercises)), "enonce": item.statement,
                              **({"corrige": item.correction} if item.correction else {}),
                              "erreurs_types": entry["taxonomy"]}
        for rank, field in enumerate(((entry["decision"].evidence_json or {}).get(KEY) or {}).get("champs") or []):
            rid = f"d{len(answers) + 1}"
            answers.append({"id": rid, "exercice": exercises[key]["ref"], "attendu": field.get("attendu"),
                            "eleve": field.get("eleve"), **({"question": field["question"]} if field.get("question") else {})})
            keys.append({"cle": rid, "decision": entry["decision"].id, "champ": rank,
                         "sig": hashlib.sha1(f"{field.get('eleve')}|{field.get('attendu')}".encode()).hexdigest()[:10]})
    return {"exercices": list(exercises.values()), "reponses": answers}, keys


def system_prompt(context: dict | None = None) -> str:
    context = context or {}
    subject = str(context.get("matiere") or "Mathématiques").lower()
    level = str(context.get("niveau") or "").strip()
    return (f"Tu es professeur de {subject}{' en ' + level if level else ''}. Chaque réponse d'élève "
            "que tu reçois est FAUSSE et déjà notée : tu ne la notes pas. Pour chacune, trouve "
            "l'erreur que l'élève a faite, en comparant sa réponse à la réponse attendue, et "
            "choisis dans la liste \"erreurs_types\" de son exercice le code qui la décrit le "
            "mieux. N'invente jamais de code. Si aucun code ne convient vraiment, ou si la "
            "réponse ne permet pas de savoir ce que l'élève a fait, rends null : une erreur mal "
            "nommée tromperait le suivi de l'élève.\n\n"
            "Rends uniquement {\"diagnostics\":[{\"id\":str,\"code_erreur\":str|null,"
            "\"motif\":str}]}, un objet par réponse reçue, aucun de plus. \"motif\" dit en "
            "quelques mots ce que l'élève a fait.")


def normalize(raw, keys: list[dict]) -> dict[str, dict]:
    """{clé: {code_erreur, motif}} ; refuse une réponse incomplète ou inattendue."""
    rows = raw.get("diagnostics") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("la clé diagnostics doit contenir une liste")
    wanted = {k["cle"] for k in keys}
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("id")) not in wanted or str(row.get("id")) in out:
            raise ValueError("identifiant inattendu, absent ou dupliqué")
        code = row.get("code_erreur")
        out[str(row["id"])] = {"code_erreur": str(code) if code else None,
                               "motif": str(row.get("motif") or "")[:120]}
    if set(out) != wanted:
        raise ValueError("identifiants manquants : " + ", ".join(sorted(wanted - set(out))))
    return out


def apply(db: Session, entries: list[dict], keys: list[dict], results: dict[str, dict], *, engine: str) -> int:
    """Écrit les codes retenus (seulement ceux de la liste de l'exercice), clôt la
    file de ces réponses, puis met à jour le journal des copies déjà finalisées.
    Retourne le nombre de codes attribués."""
    by_decision = {e["decision"].id: e for e in entries}
    touched, coded = {}, 0
    for key in keys:
        entry = by_decision.get(key["decision"])
        if entry is None:
            continue
        decision = entry["decision"]
        state = dict((decision.evidence_json or {}).get(KEY) or {})
        fields = [dict(f) for f in state.get("champs") or []]
        if not 0 <= key["champ"] < len(fields):
            continue
        result = results.get(key["cle"]) or {}
        allowed = {e["code"] for e in entry["taxonomy"]}
        code = result.get("code_erreur")
        fields[key["champ"]].update({"code": code if code in allowed else None,
                                     "motif": result.get("motif", ""), "moteur": engine})
        coded += bool(code in allowed)
        state.update({"champs": fields, "etat": DONE})
        decision.evidence_json = {**(decision.evidence_json or {}), KEY: state}
        touched[entry["copy"].id] = entry
    db.flush()
    for entry in touched.values():
        copy = entry["copy"]
        if db.query(CopyResult).filter_by(copy_id=copy.id).first() is not None:
            assessment = db.get(Assessment, copy.assessment_id)
            error_journal.sync_copy(db, copy, assessment)
    return coded


def run_api(db: Session, assessment_id: str, correlation_id: str | None = None) -> int:
    """Mode api : diagnostique la file du sujet par paquets. Une panne laisse la
    file en attente ; elle ne touche à aucune note."""
    if settings.correction_mode != "api":
        return 0
    entries = pending(db, assessment_id=assessment_id)
    if not entries or providers.offline(db, providers.provider_for_model(settings.correction_model)):
        return 0
    context = llm_grader.assessment_context(db, assessment_id)
    size = max(1, int(settings.correction_batch_size))
    coded = 0
    for start in range(0, len(entries), size):
        pack = entries[start:start + size]
        data, keys = payload(pack)
        try:
            raw = providers.deepseek_json(
                db, "error_diagnosis", system_prompt(context), data,
                max_tokens=min(3000, 120 * len(keys) + 400), model=settings.correction_model,
                correlation_id=correlation_id, thinking=False,
                # le validateur refuse une sortie hors contrat, puis rend la sortie brute
                validator=lambda r, k=keys: (normalize(r, k), r)[1],
                repair_instruction="Rends exactement {\"diagnostics\":[...]}, un objet par id reçu.")
            coded += apply(db, pack, keys, normalize(raw, keys), engine=settings.correction_model)
        except Exception as e:  # noqa: BLE001 — un diagnostic manqué ne bloque rien
            logger.warning("diagnostic d'erreurs indisponible (%s) : %s réponse(s) en attente", e, len(keys))
    return coded

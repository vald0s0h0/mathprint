"""Commande `vague` (plan forge2 §10.2) : la correction par LLM, lancée à la main
depuis le Mac, sur abonnement.

    corriger  : les réponses écrites en attente (motif `llm_queued`, ou
                `llm_unavailable` laissé par l'ancien chemin api), regroupées par
                exercice : l'énoncé et le dossier du correcteur une fois, puis les
                réponses pseudonymisées de la classe, par paquets ;
    apprecier : la phrase de synthèse des copies finalisées qui l'attendent.

Chaque paquet est un appel neuf et court (services.cli_llm), validé puis écrit
par le MÊME chemin que l'ancien correcteur (llm_grader.apply_results : seuil de
confiance compris), puis enregistré aussitôt. Une vague interrompue reprend
donc là où elle s'est arrêtée : un paquet écrit n'est plus en attente, il n'est
jamais refait. `essai` montre les paquets sans appeler le moteur ni écrire.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Assessment, Copy, CopyResult, GradingDecision, ScanBatch, Student
from . import appreciation, cli_llm, forgetting, llm_grader, microcorrection, refine

# budget annoncé au moteur : 2 lignes ≈ 100 caractères, 4 lignes ≈ 200. La
# mesure qui fait foi reste celle de la bande imprimée (microcorrection._fits).
CHARS_PER_LINE = 50
SYNTHESIS_MAX_WORDS = 30
SYNTHESIS_MAX_CHARS = 220

_TU_RE = re.compile(r"\b(tu|ton|ta|tes|toi|te)\b|\bt['’]", re.I)
_POSITION_RE = re.compile(
    r"\b(r[ée]ponse|proposition|choix|case|ligne|colonne|option|cellule|question)s?\s+"
    r"(n°\s*)?([A-H]|\d+)\b", re.I)
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?(?:/\d+)?")

Ask = Callable[[str, str], object]


@dataclass
class Bilan:
    paquets: int = 0            # paquets écrits
    corrigees: int = 0          # réponses notées par le moteur
    renvoyees: int = 0          # réponses renvoyées au professeur (confiance, illisible)
    echecs: int = 0             # paquets en échec, restés en attente
    messages_refuses: int = 0   # micro-corrections refusées : la carte reprend la main
    syntheses: int = 0
    arret: str = ""             # vague arrêtée (moteur absent, fenêtre d'usage épuisée)
    lignes: list[str] = field(default_factory=list)

    def texte(self) -> str:
        out = [f"Paquets traités : {self.paquets}",
               f"Réponses corrigées : {self.corrigees}, renvoyées au professeur : {self.renvoyees}",
               f"Paquets en échec : {self.echecs}"]
        if self.messages_refuses:
            out.append(f"Micro-corrections refusées : {self.messages_refuses}")
        if self.syntheses:
            out.append(f"Synthèses rédigées : {self.syntheses}")
        if self.arret:
            out.append(f"Vague arrêtée : {self.arret}")
        return "\n".join(out)


def _asker(moteur: str, ask: Ask | None) -> Ask:
    return ask or (lambda system, text: cli_llm.ask(moteur, system, text))


# ------------------------------------------------------------------ corriger


def packets(tasks: list[llm_grader.Task], size: int) -> list[list[llm_grader.Task]]:
    """Paquets d'un seul exercice, de `size` réponses au plus. Une copie n'est
    jamais coupée en deux : chaque paquet s'écrit seul, sans dépendre d'un autre."""
    by_exercise: dict[str, list[llm_grader.Task]] = {}
    for task in tasks:
        by_exercise.setdefault(task.exercise_key, []).append(task)
    out: list[list[llm_grader.Task]] = []
    for group in by_exercise.values():
        current: list[llm_grader.Task] = []
        count = 0
        for task in group:
            if current and count + len(task.fields) > size:
                out.append(current)
                current, count = [], 0
            current.append(task)
            count += len(task.fields)
        if current:
            out.append(current)
    return out


def _payload(pairs: list[tuple[llm_grader.Task, llm_grader.Field]]) -> dict:
    payload = llm_grader._payload(pairs)
    for answer, (task, _field) in zip(payload["reponses"], pairs):
        if task.feedback_lines > 0:
            answer["message_max"] = task.feedback_lines * CHARS_PER_LINE
    return payload


def _rows(raw) -> dict[str, dict]:
    rows = raw.get("corrections") if isinstance(raw, dict) else raw
    return {str(r.get("id")): r for r in rows or [] if isinstance(r, dict)}


def _answer_tokens(expected: str) -> list[str]:
    """La réponse attendue, quand elle est assez simple pour être repérée dans
    un message : un nombre, une fraction ou un mot."""
    head = (expected or "").split(" (", 1)[0]
    out = []
    for part in head.split(" ou "):
        part = part.strip().strip("$").strip()
        if _NUMBER_RE.fullmatch(part) or re.fullmatch(r"[^\W\d_]{2,24}", part):
            out.append(part)
    return out


def check_message(text: str, lines: int, expected: str = "") -> tuple[str, str]:
    """(message retenu, motif du refus). Un refus rend la main au message prévu
    par la carte."""
    text = " ".join(str(text or "").split())
    if not text:
        return "", "vide"
    if lines <= 0:
        return "", "carte sans micro-correction"
    if len(text) > lines * CHARS_PER_LINE or not microcorrection._fits(text, lines):
        return "", "trop long"
    if not _TU_RE.search(text):
        return "", "pas de tutoiement"
    if _POSITION_RE.search(text):
        return "", "cite une lettre ou une position"
    for token in _answer_tokens(expected):
        pattern = (rf"(?<![\d,./]){re.escape(token)}(?![\d,/]|[.,]\d)" if token[0].isdigit()
                   or token[0] == "-" else rf"\b{re.escape(token)}\b")
        if re.search(pattern, text, re.I):
            return "", "donne la réponse"
    return text, ""


def _still_pending(db: Session, task: llm_grader.Task) -> bool:
    """Le professeur a pu noter la réponse pendant l'appel : sa note fait foi."""
    decision = db.get(GradingDecision, task.decision_id) if task.decision_id else None
    return bool(decision and decision.status == "review_pending"
                and decision.reason_code in llm_grader.PENDING_REASONS)


def _refresh(db: Session, assessment_id: str) -> None:
    """Ce que le pipeline fait après la correction LLM : audit, recomptage des
    revues, finalisation (résultats, journal d'erreurs, overlays) si plus rien
    n'attend le professeur."""
    from . import pipeline   # import tardif : le pipeline charge beaucoup de choses
    batch = (db.query(ScanBatch).filter_by(assessment_id=assessment_id)
             .order_by(ScanBatch.created_at.desc()).first())
    if batch is not None and batch.status in ("graded", "review_pending"):
        pipeline.after_llm_grading(db, batch)
    db.commit()


def corriger(db: Session, *, moteur: str = "haiku", devoir: str | None = None,
             classe: str | None = None, essai: bool = False, ask: Ask | None = None,
             log: Callable[[str], None] = print, bilan: Bilan | None = None) -> Bilan:
    bilan = bilan or Bilan()
    tasks = llm_grader.pending_tasks(db, assessment_id=devoir, class_id=classe)
    packs = packets(tasks, max(1, int(settings.correction_batch_size)))
    log(f"{len(tasks)} réponse(s) en attente, {len(packs)} paquet(s).")
    if not packs:
        return bilan
    if not essai and ask is None:
        try:
            cli_llm.engine_cli(moteur)
        except cli_llm.CliError as e:
            bilan.arret = str(e)
            return bilan
    call = _asker(moteur, ask)
    for n, pack in enumerate(packs, 1):
        pairs = [(task, f) for task in pack for f in task.fields]
        for i, (_task, f) in enumerate(pairs):
            f.key = f"r{i + 1}"
        head = (f"[{n}/{len(packs)}] {pack[0].statement[:60]!r} · "
                f"{len(pairs)} réponse(s), devoir {pack[0].assessment_id}")
        if essai:
            log(head)
            continue
        system = llm_grader.system_prompt(pack[0].context, vague=True)
        text = (json.dumps(_payload(pairs), ensure_ascii=False)
                + "\n\nRends uniquement le JSON {\"corrections\":[...]}, un objet par id reçu.")
        try:
            raw = call(system, text)
            normalized = llm_grader._normalize_grading_response(raw, pairs)
        except cli_llm.CliError as e:
            if e.stop:
                bilan.arret = str(e)
                log(f"{head} : arrêt ({e})")
                break
            bilan.echecs += 1
            log(f"{head} : échec ({e})")
            continue
        except ValueError as e:
            bilan.echecs += 1
            log(f"{head} : réponse refusée ({e})")
            continue
        rows = _rows(raw)
        results = {r["id"]: r for r in normalized["corrections"]}
        for task, f in pairs:
            result = dict(results[f.key])
            result.pop("message_libre", None)   # ici le message est rédigé, pas libre
            written = rows.get(f.key, {}).get("message")
            if written and result["verdict"] != "juste":
                kept, why = check_message(written, task.feedback_lines, f.expected)
                if kept:
                    result["message_redige"] = kept
                else:
                    bilan.messages_refuses += 1
                    log(f"  message refusé ({why}) : {str(written)[:80]!r}")
            f.result = result
            task.engine = moteur
        live = [task for task in pack if _still_pending(db, task)]
        to_teacher = llm_grader.apply_results(db, live)
        db.commit()
        bilan.paquets += 1
        bilan.renvoyees += to_teacher
        bilan.corrigees += len(live) - to_teacher
        log(f"{head} : écrit ({len(live) - to_teacher} notée(s), {to_teacher} au professeur)")
        for assessment_id in dict.fromkeys(task.assessment_id for task in live if task.assessment_id):
            _refresh(db, assessment_id)
    return bilan


# ----------------------------------------------------------------- apprecier

_APPRECIER = (
    "\n\nTu reçois plusieurs copies dans {\"copies\":[...]}. Rends "
    "{\"syntheses\":[{\"id\":str,\"synthesis\":str}]}, un objet par copie reçue, "
    "en appliquant à chacune la règle ci-dessus."
)


def waiting_copies(db: Session, *, devoir: str | None = None,
                   classe: str | None = None) -> list[Copy]:
    q = db.query(Copy).filter(Copy.status == "finalized", Copy.appreciation_json.isnot(None))
    if devoir:
        q = q.filter(Copy.assessment_id == devoir)
    if classe:
        q = q.join(Assessment, Copy.assessment_id == Assessment.id).filter(
            Assessment.class_id == classe)
    return [c for c in q.order_by(Copy.assessment_id, Copy.id).all()
            if (c.appreciation_json or {}).get("synthesis_pending")]


def check_synthesis(text, student: Student) -> str | None:
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if len(text) > SYNTHESIS_MAX_CHARS or len(text.split()) > SYNTHESIS_MAX_WORDS:
        return None
    names = [w for w in re.split(r"\W+", student.name or "") if len(w) >= 3]
    if any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in names):
        return None
    return text


def apprecier(db: Session, *, moteur: str = "haiku", devoir: str | None = None,
              classe: str | None = None, essai: bool = False, ask: Ask | None = None,
              log: Callable[[str], None] = print, bilan: Bilan | None = None) -> Bilan:
    bilan = bilan or Bilan()
    copies = waiting_copies(db, devoir=devoir, classe=classe)
    size = max(1, int(settings.correction_batch_size))
    packs = [copies[i:i + size] for i in range(0, len(copies), size)]
    log(f"{len(copies)} synthèse(s) d'appréciation en attente, {len(packs)} paquet(s).")
    if not packs or essai:
        for n, pack in enumerate(packs, 1):
            log(f"[{n}/{len(packs)}] {len(pack)} copie(s), devoir {pack[0].assessment_id}")
        return bilan
    if ask is None:
        try:
            cli_llm.engine_cli(moteur)
        except cli_llm.CliError as e:
            bilan.arret = str(e)
            return bilan
    call = _asker(moteur, ask)
    for n, pack in enumerate(packs, 1):
        students = {c.id: db.get(Student, c.student_id) for c in pack}
        keys = {f"c{i + 1}": c for i, c in enumerate(pack)}
        payload = {"copies": [
            {"id": key, **appreciation.synthesis_payload(
                students[c.id], (c.appreciation_json or {}).get("progress") or [],
                forgetting.due_competencies(db, c.student_id))}
            for key, c in keys.items()]}
        head = f"[{n}/{len(packs)}] {len(pack)} copie(s)"
        try:
            raw = call(appreciation.SYSTEM + _APPRECIER, json.dumps(payload, ensure_ascii=False))
            rows = {str(r.get("id")): r for r in (raw.get("syntheses") if isinstance(raw, dict) else None) or []
                    if isinstance(r, dict)}
            if set(rows) != set(keys):
                raise ValueError("identifiants manquants ou inattendus")
        except cli_llm.CliError as e:
            if e.stop:
                bilan.arret = str(e)
                break
            bilan.echecs += 1
            log(f"{head} : échec ({e})")
            continue
        except ValueError as e:
            bilan.echecs += 1
            log(f"{head} : réponse refusée ({e})")
            continue
        touched: set[str] = set()
        for key, copy in keys.items():
            text = check_synthesis(rows[key].get("synthesis"), students[copy.id])
            if text is None:
                log(f"  synthèse refusée pour {key} : {str(rows[key].get('synthesis'))[:80]!r}")
                continue
            data = dict(copy.appreciation_json or {})
            data.pop("synthesis_pending", None)
            data["synthesis"] = text
            copy.appreciation_json = data
            result = db.query(CopyResult).filter_by(copy_id=copy.id).first()
            if result is not None:
                result.appreciation = text
            touched.add(copy.assessment_id)
            bilan.syntheses += 1
        for assessment_id in touched:
            batch = (db.query(ScanBatch).filter_by(assessment_id=assessment_id)
                     .order_by(ScanBatch.created_at.desc()).first())
            if batch is not None:
                refine.mark_changed(db, batch.id)   # l'overlay est à régénérer
        db.commit()
        bilan.paquets += 1
        log(f"{head} : écrit")
    return bilan


def run(db: Session, taches: list[str], **kw) -> Bilan:
    """`taches` : corriger, apprecier ou tout (les deux, dans cet ordre)."""
    taches = taches or ["tout"]
    bilan = Bilan()
    if "corriger" in taches or "tout" in taches:
        corriger(db, bilan=bilan, **kw)
    if ("apprecier" in taches or "tout" in taches) and not bilan.arret:
        apprecier(db, bilan=bilan, **kw)
    return bilan

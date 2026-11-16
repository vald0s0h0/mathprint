"""Commande `vague` (plan forge2 §10.2) : la correction par LLM, lancée à la main
depuis le Mac, sur abonnement.

    corriger  : les réponses écrites en attente (motif `llm_queued`, ou
                `llm_unavailable` laissé par l'ancien chemin api), regroupées par
                exercice : l'énoncé et le dossier du correcteur une fois, puis les
                réponses pseudonymisées de la classe, par paquets ; puis le
                diagnostic des réponses fausses imprévues (services.diagnostic) :
                l'erreur type de chacune, sans toucher à sa note ;
    apprecier : la phrase de synthèse des copies finalisées qui l'attendent.

La vague tient en trois temps, qui se séparent pour relever aussi la file du NAS
(services.vague_distante) : `exporter_*` (côté base : paquets JSON, prompt déjà
composé, réponses pseudonymisées), `demander` (côté moteur, sans base), puis
`importer_paquet` (côté base : revalidation et écriture). La vague locale les
enchaîne en mémoire.

Chaque paquet est un appel neuf et court (services.cli_llm), validé puis écrit
par le MÊME chemin que l'ancien correcteur (llm_grader.apply_results : seuil de
confiance compris), puis enregistré aussitôt. Une vague interrompue reprend
donc là où elle s'est arrêtée : un paquet écrit n'est plus en attente, il n'est
jamais refait. `essai` montre les paquets sans appeler le moteur ni écrire.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Assessment, Copy, CopyResult, GradingDecision, ScanBatch, Student
from . import appreciation, cli_llm, diagnostic, forgetting, llm_grader, microcorrection, refine

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


def _sig(f: llm_grader.Field) -> str:
    """Empreinte d'un champ : l'import refuse un paquet dont la base a changé."""
    return hashlib.sha1(f"{f.student}|{f.expected}|{f.bareme}".encode()).hexdigest()[:10]


def _pid(kind: str, parts: list) -> str:
    return kind[0] + "-" + hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def exporter_corriger(db: Session, *, devoir: str | None = None,
                      classe: str | None = None) -> list[dict]:
    """Les paquets à corriger, en JSON : prompt et texte composés (réponses
    pseudonymisées), plus de quoi retrouver et valider chaque champ à l'import."""
    tasks = llm_grader.pending_tasks(db, assessment_id=devoir, class_id=classe)
    out = []
    for pack in packets(tasks, max(1, int(settings.correction_batch_size))):
        pairs = [(task, f) for task in pack for f in task.fields]
        champs = []
        for i, (task, f) in enumerate(pairs):
            f.key = f"r{i + 1}"
            champs.append({"cle": f.key, "decision": task.decision_id,
                           "champ": next(j for j, g in enumerate(task.fields) if g is f),
                           "sig": _sig(f)})
        text = (json.dumps(_payload(pairs), ensure_ascii=False)
                + "\n\nRends uniquement le JSON {\"corrections\":[...]}, un objet par id reçu.")
        out.append({"id": _pid("corriger", [[c["decision"], c["champ"], c["sig"]] for c in champs]),
                    "type": "corriger", "devoir": pack[0].assessment_id,
                    "titre": f"{pack[0].statement[:60]!r} · {len(pairs)} réponse(s), "
                             f"devoir {pack[0].assessment_id}",
                    "system": llm_grader.system_prompt(pack[0].context, vague=True),
                    "text": text, "champs": champs})
    return out


def _filtrer(raw, gardes: set[str]):
    """Retire les réponses des champs devenus sans objet (le professeur les a notées)."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict) and str(r.get("id")) in gardes]
    if isinstance(raw, dict) and isinstance(raw.get("corrections"), list):
        return {**raw, "corrections": _filtrer(raw["corrections"], gardes)}
    return raw


def importer_corriger(db: Session, paquet: dict, raw, *, moteur: str,
                      log: Callable[[str], None], bilan: Bilan) -> None:
    """Valide la réponse du moteur et l'écrit, par le chemin de toujours. Tout
    vient de la base ; le paquet ne sert qu'à retrouver ses champs."""
    head = paquet.get("titre") or paquet.get("id")
    champs = paquet.get("champs")
    if not isinstance(champs, list) or not champs or not paquet.get("devoir"):
        bilan.echecs += 1
        log(f"{head} : paquet illisible")
        return
    by_decision = {t.decision_id: t for t in
                   llm_grader.pending_tasks(db, assessment_id=str(paquet["devoir"]))}
    pairs, seen = [], set()
    for c in champs:
        try:
            cle, dec, idx, sig = str(c["cle"]), c["decision"], int(c["champ"]), c["sig"]
        except (KeyError, TypeError, ValueError):
            bilan.echecs += 1
            log(f"{head} : champ illisible")
            return
        task = by_decision.get(dec)
        if task is None:
            continue    # plus en attente : déjà écrit, ou noté par le professeur
        if (not re.fullmatch(r"r\d{1,4}", cle) or cle in seen or not 0 <= idx < len(task.fields)
                or _sig(task.fields[idx]) != sig):
            bilan.echecs += 1
            log(f"{head} : paquet refusé (champ inconnu ou modifié depuis l'export)")
            return
        seen.add(cle)
        task.fields[idx].key = cle
        pairs.append((task, task.fields[idx]))
    if not pairs:
        log(f"{head} : déjà traité")
        return
    try:
        if len(pairs) < len(champs):
            raw = _filtrer(raw, seen)
        normalized = llm_grader._normalize_grading_response(raw, pairs)
    except ValueError as e:
        bilan.echecs += 1
        log(f"{head} : réponse refusée ({e})")
        return
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
    tasks = list({id(task): task for task, _f in pairs}.values())
    live = [task for task in tasks if _still_pending(db, task)]
    to_teacher = llm_grader.apply_results(db, live)
    db.commit()
    bilan.paquets += 1
    bilan.renvoyees += to_teacher
    bilan.corrigees += len(live) - to_teacher
    log(f"{head} : écrit ({len(live) - to_teacher} notée(s), {to_teacher} au professeur)")
    for assessment_id in dict.fromkeys(task.assessment_id for task in live if task.assessment_id):
        _refresh(db, assessment_id)


# ----------------------------------------------------------------- diagnostiquer

def exporter_diagnostiquer(db: Session, *, devoir: str | None = None,
                           classe: str | None = None) -> list[dict]:
    """Les réponses fausses imprévues, par paquets d'un même devoir."""
    entries = diagnostic.pending(db, assessment_id=devoir, class_id=classe)
    by_assessment: dict[str, list[dict]] = {}
    for entry in entries:
        by_assessment.setdefault(entry["assessment_id"], []).append(entry)
    size = max(1, int(settings.correction_batch_size))
    out = []
    for assessment_id, group in by_assessment.items():
        context = llm_grader.assessment_context(db, assessment_id)
        for start in range(0, len(group), size):
            pack = group[start:start + size]
            data, keys = diagnostic.payload(pack)
            if not keys:
                continue
            text = (json.dumps(data, ensure_ascii=False)
                    + "\n\nRends uniquement le JSON {\"diagnostics\":[...]}, un objet par id reçu.")
            out.append({"id": _pid("diagnostiquer", [[k["decision"], k["champ"], k["sig"]] for k in keys]),
                        "type": "diagnostiquer", "devoir": assessment_id,
                        "titre": f"diagnostic · {len(keys)} réponse(s) fausse(s) imprévue(s), devoir {assessment_id}",
                        "system": diagnostic.system_prompt(context), "text": text, "champs": keys})
    return out


def importer_diagnostiquer(db: Session, paquet: dict, raw, *, moteur: str,
                           log: Callable[[str], None], bilan: Bilan) -> None:
    """Revalide contre la base (réponse inchangée depuis l'export), puis écrit."""
    head = paquet.get("titre") or paquet.get("id")
    keys = paquet.get("champs")
    if not isinstance(keys, list) or not keys or not paquet.get("devoir"):
        bilan.echecs += 1
        log(f"{head} : paquet illisible")
        return
    entries = diagnostic.pending(db, assessment_id=str(paquet["devoir"]))
    _data, current = diagnostic.payload(entries)
    live = {(k["decision"], k["champ"]): k["sig"] for k in current}
    kept = [k for k in keys if live.get((k.get("decision"), k.get("champ"))) == k.get("sig")]
    if not kept:
        log(f"{head} : déjà traité")
        return
    try:
        wanted = {k["cle"] for k in kept}
        rows = raw.get("diagnostics") if isinstance(raw, dict) else raw
        rows = [r for r in rows or [] if isinstance(r, dict) and str(r.get("id")) in wanted]
        results = diagnostic.normalize({"diagnostics": rows}, kept)
    except (ValueError, TypeError, AttributeError) as e:
        bilan.echecs += 1
        log(f"{head} : réponse refusée ({e})")
        return
    coded = diagnostic.apply(db, entries, kept, results, engine=moteur)
    db.commit()
    bilan.paquets += 1
    log(f"{head} : {coded} erreur(s) nommée(s) sur {len(kept)}")


def demander(paquet: dict, call: Ask):
    """Le moteur sur un paquet exporté : aucune base. Lève CliError."""
    return call(paquet["system"], paquet["text"])


def importer_paquet(db: Session, paquet: dict, raw, *, moteur: str = "haiku",
                    log: Callable[[str], None] = print, bilan: Bilan | None = None) -> Bilan:
    bilan = bilan or Bilan()
    fn = {"apprecier": importer_apprecier, "diagnostiquer": importer_diagnostiquer}.get(
        paquet.get("type"), importer_corriger)
    fn(db, paquet, raw, moteur=moteur, log=log, bilan=bilan)
    return bilan


def _verifier_moteur(moteur: str, bilan: Bilan) -> bool:
    try:
        cli_llm.engine_cli(moteur)
    except cli_llm.CliError as e:
        bilan.arret = str(e)
        return False
    return True


def executer(db: Session, paquets: list[dict], *, moteur: str, essai: bool, ask: Ask | None,
             log: Callable[[str], None], bilan: Bilan) -> None:
    """Exporter -> demander -> importer, en mémoire, paquet par paquet."""
    if essai or not paquets:
        for n, p in enumerate(paquets, 1):
            log(f"[{n}/{len(paquets)}] {p['titre']}")
        return
    if ask is None and not _verifier_moteur(moteur, bilan):
        return
    call = _asker(moteur, ask)
    for n, p in enumerate(paquets, 1):
        head = f"[{n}/{len(paquets)}] {p['titre']}"
        try:
            raw = demander(p, call)
        except cli_llm.CliError as e:
            if e.stop:
                bilan.arret = str(e)
                log(f"{head} : arrêt ({e})")
                return
            bilan.echecs += 1
            log(f"{head} : échec ({e})")
            continue
        importer_paquet(db, {**p, "titre": head}, raw, moteur=moteur, log=log, bilan=bilan)


def corriger(db: Session, *, moteur: str = "haiku", devoir: str | None = None,
             classe: str | None = None, essai: bool = False, ask: Ask | None = None,
             log: Callable[[str], None] = print, bilan: Bilan | None = None) -> Bilan:
    bilan = bilan or Bilan()
    paquets = exporter_corriger(db, devoir=devoir, classe=classe)
    log(f"{sum(len(p['champs']) for p in paquets)} réponse(s) en attente, {len(paquets)} paquet(s).")
    executer(db, paquets, moteur=moteur, essai=essai, ask=ask, log=log, bilan=bilan)
    if not bilan.arret:
        paquets = exporter_diagnostiquer(db, devoir=devoir, classe=classe)
        if paquets:
            log(f"{sum(len(p['champs']) for p in paquets)} réponse(s) fausse(s) imprévue(s) à "
                f"diagnostiquer, {len(paquets)} paquet(s).")
            executer(db, paquets, moteur=moteur, essai=essai, ask=ask, log=log, bilan=bilan)
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


def exporter_apprecier(db: Session, *, devoir: str | None = None,
                       classe: str | None = None) -> list[dict]:
    copies = waiting_copies(db, devoir=devoir, classe=classe)
    size = max(1, int(settings.correction_batch_size))
    out = []
    for i in range(0, len(copies), size):
        pack = copies[i:i + size]
        keys = {f"c{j + 1}": c for j, c in enumerate(pack)}
        payload = {"copies": [
            {"id": key, **appreciation.synthesis_payload(
                db.get(Student, c.student_id), (c.appreciation_json or {}).get("progress") or [],
                forgetting.due_competencies(db, c.student_id))}
            for key, c in keys.items()]}
        out.append({"id": _pid("apprecier", sorted(c.id for c in pack)), "type": "apprecier",
                    "devoir": pack[0].assessment_id,
                    "titre": f"{len(pack)} copie(s), devoir {pack[0].assessment_id}",
                    "system": appreciation.SYSTEM + _APPRECIER,
                    "text": json.dumps(payload, ensure_ascii=False),
                    "copies": {k: c.id for k, c in keys.items()}})
    return out


def importer_apprecier(db: Session, paquet: dict, raw, *, moteur: str,
                       log: Callable[[str], None], bilan: Bilan) -> None:
    head = paquet.get("titre") or paquet.get("id")
    ids = paquet.get("copies")
    if not isinstance(ids, dict) or not ids:
        bilan.echecs += 1
        log(f"{head} : paquet illisible")
        return
    waiting = {c.id: c for c in db.query(Copy).filter(
        Copy.id.in_([str(v) for v in ids.values()]), Copy.status == "finalized",
        Copy.appreciation_json.isnot(None)).all()
        if (c.appreciation_json or {}).get("synthesis_pending")}
    keys = {str(k): waiting[str(v)] for k, v in ids.items() if str(v) in waiting}
    if not keys:
        log(f"{head} : déjà traité")
        return
    rows = {str(r.get("id")): r for r in (raw.get("syntheses") if isinstance(raw, dict) else None) or []
            if isinstance(r, dict)}
    if not set(rows) >= set(keys) or not set(rows) <= {str(k) for k in ids}:
        bilan.echecs += 1
        log(f"{head} : réponse refusée (identifiants manquants ou inattendus)")
        return
    touched: set[str] = set()
    for key, copy in keys.items():
        text = check_synthesis(rows[key].get("synthesis"), db.get(Student, copy.student_id))
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


def apprecier(db: Session, *, moteur: str = "haiku", devoir: str | None = None,
              classe: str | None = None, essai: bool = False, ask: Ask | None = None,
              log: Callable[[str], None] = print, bilan: Bilan | None = None) -> Bilan:
    bilan = bilan or Bilan()
    paquets = exporter_apprecier(db, devoir=devoir, classe=classe)
    log(f"{sum(len(p['copies']) for p in paquets)} synthèse(s) d'appréciation en attente, "
        f"{len(paquets)} paquet(s).")
    executer(db, paquets, moteur=moteur, essai=essai, ask=ask, log=log, bilan=bilan)
    return bilan


def exporter(db: Session, taches: list[str], *, devoir: str | None = None,
             classe: str | None = None) -> list[dict]:
    taches = taches or ["tout"]
    out = []
    if "corriger" in taches or "tout" in taches:
        out += exporter_corriger(db, devoir=devoir, classe=classe)
        out += exporter_diagnostiquer(db, devoir=devoir, classe=classe)
    if "apprecier" in taches or "tout" in taches:
        out += exporter_apprecier(db, devoir=devoir, classe=classe)
    return out


def run(db: Session, taches: list[str], **kw) -> Bilan:
    """`taches` : corriger, apprecier ou tout (les deux, dans cet ordre)."""
    taches = taches or ["tout"]
    bilan = Bilan()
    if "corriger" in taches or "tout" in taches:
        corriger(db, bilan=bilan, **kw)
    if ("apprecier" in taches or "tout" in taches) and not bilan.arret:
        apprecier(db, bilan=bilan, **kw)
    return bilan

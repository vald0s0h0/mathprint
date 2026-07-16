"""Clients Mathpix / DeepSeek / Claude Haiku (§6.3, §8).

Règles appliquées ici :
- aucun nom d'élève ne transite (pseudonymes uniquement, RM-010) ;
- modèles lus depuis provider_configs / registre par défaut (RM-011) ;
- coûts et tokens enregistrés à chaque appel (api_usage_events) ;
- budgets : un dépassement suspend les appels sans bloquer les données (RM-015) ;
- mode mock : réponses simulées déterministes pour développement et tests.
"""
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ApiUsageEvent, ProviderConfig

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    pass


class LLMTimeout(Exception):
    """Appel LLM sans réponse complète dans le délai total imparti."""


# Les appels LLM passent par ce petit pool pour pouvoir imposer un délai
# TOTAL (settings.llm_call_timeout_s). Le timeout httpx (par lecture socket)
# ne protège pas d'un serveur qui maintient la connexion en envoyant des
# octets au compte-gouttes — cause observée d'un worker bloqué des heures
# sur api.deepseek.com. Un appel abandonné laisse son thread se terminer
# seul (ReadTimeout httpx ou fin de réponse) ; le pool borne l'accumulation.
_HTTP_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-http")


def _post_with_deadline(url: str, *, headers: dict, json_body: dict,
                        timeout: float, provider: str) -> httpx.Response:
    total = settings.llm_call_timeout_s
    started = time.monotonic()
    future = _HTTP_POOL.submit(httpx.post, url, headers=headers,
                               json=json_body, timeout=timeout)
    try:
        resp = future.result(timeout=total)
    except _FutureTimeout:
        future.cancel()
        logger.warning("%s : aucune réponse complète après %ss — appel abandonné",
                       provider, total)
        raise LLMTimeout(
            f"{provider} : pas de réponse complète après {total}s "
            f"(appel abandonné, il sera retenté)") from None
    elapsed = time.monotonic() - started
    if elapsed > 30:
        logger.info("%s : réponse en %.0fs", provider, elapsed)
    return resp


# premier objet {...} d'un texte (extraction tolérante de JSON)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _today_cost(db: Session, provider: str) -> float:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = db.query(ApiUsageEvent).filter(
        ApiUsageEvent.provider == provider, ApiUsageEvent.created_at >= since).all()
    return sum(r.estimated_cost for r in rows)


def _record(db: Session, provider: str, model: str, operation: str, *,
            input_tokens=0, output_tokens=0, units=0, cost=0.0, correlation_id=None):
    db.add(ApiUsageEvent(provider=provider, model=model, operation=operation,
                         input_tokens=input_tokens, output_tokens=output_tokens,
                         units=units, estimated_cost=cost, correlation_id=correlation_id))
    db.commit()


def _config(db: Session, provider: str) -> ProviderConfig | None:
    return db.query(ProviderConfig).filter_by(provider=provider, active=True).first()


# Familles de modèles Claude qui REFUSENT (400) un préremplissage assistant :
# tout 4.6 et au-delà. Haiku 4.5 (et antérieurs) l'acceptent encore. Voir
# claude_vision_json / claude_json : le prefill "{" force la sortie JSON.
_NO_PREFILL_MODELS = ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6",
                      "sonnet-5", "fable-5", "mythos-5")


def _supports_assistant_prefill(model: str | None) -> bool:
    return not any(tag in (model or "") for tag in _NO_PREFILL_MODELS)


def _mock_enabled(db: Session, cfg: ProviderConfig | None) -> bool:
    from .runtime_settings import mock_enabled
    return mock_enabled(db) or cfg is None or not cfg.encrypted_secret


def _provider_for_model(model: str) -> str:
    """Retourne le provider DeepSeek approprié selon le modèle demandé."""
    if model and "pro" in model:
        return "deepseek-pro"
    return "deepseek-flash"


# ------------------------------------------------------------------- Mathpix

def mathpix_ocr(db: Session, image_bytes: bytes, correlation_id: str,
                expected_hint: str | None = None) -> dict:
    """POST /v3/text sur un crop isolé (jamais la page complète, §6.3).
    Retourne {latex, text, confidence, raw}."""
    cfg = _config(db, "mathpix")
    if _today_cost(db, "mathpix") > settings.llm_daily_cost_limit_eur * 5:
        raise BudgetExceeded("Quota Mathpix quotidien atteint")

    if _mock_enabled(db, cfg):
        # Mock déterministe : simule un OCR correct à confiance élevée,
        # avec une faible fraction d'ambiguïtés pour exercer la file de revue.
        h = int(hashlib.sha256(image_bytes + correlation_id.encode()).hexdigest(), 16)
        conf = 0.97 if h % 10 < 8 else 0.45
        text = expected_hint if (expected_hint is not None and h % 17 != 0) else "?"
        _record(db, "mathpix", "mock", "ocr_text", units=1, cost=0.0, correlation_id=correlation_id)
        return {"latex": text, "text": text, "confidence": conf, "raw": {"mock": True}}

    app_id, app_key = (cfg.encrypted_secret.split(":", 1) + [""])[:2]
    r = _post_with_deadline(
        "https://api.mathpix.com/v3/text",
        headers={"app_id": app_id, "app_key": app_key},
        json_body={
            "src": "data:image/png;base64," + __import__("base64").b64encode(image_bytes).decode(),
            "formats": ["text", "latex_styled"],
            "metadata": {"improve_mathpix": False},  # confidentialité (§6.3)
        },
        timeout=30, provider="Mathpix",
    )
    r.raise_for_status()
    data = r.json()
    _record(db, "mathpix", cfg.model or "v3/text", "ocr_text", units=1,
            cost=0.004, correlation_id=correlation_id)
    return {"latex": data.get("latex_styled", ""), "text": data.get("text", ""),
            "confidence": data.get("confidence", 0.0), "raw": data}


# ------------------------------------------------------------------- DeepSeek

def deepseek_json(db: Session, operation: str, system: str, user_payload: dict,
                  max_tokens: int = 500, reasoning: bool = False,
                  correlation_id: str | None = None, model: str | None = None) -> dict:
    """Appel DeepSeek en sortie JSON stricte. Une seule tentative corrective (§8.5).
    `model` permet d'imposer un modèle (ex : deepseek-v4-pro pour la création
    d'exercices) ; sinon registre configurable (RM-011)."""
    model = model or (settings.deepseek_reasoning_model if reasoning else settings.deepseek_model)
    provider = _provider_for_model(model)
    cfg = _config(db, provider)

    if _today_cost(db, provider) > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded(f"Budget {provider} quotidien atteint")

    if _mock_enabled(db, cfg):
        _record(db, provider, model, operation, input_tokens=200, output_tokens=80,
                cost=0.0, correlation_id=correlation_id)
        return _deepseek_mock(operation, user_payload)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            # données OCR traitées comme non fiables : encadrées, pas d'outils (§8.5)
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    for attempt in range(2):  # un seul retry correctif
        r = _post_with_deadline("https://api.deepseek.com/chat/completions",
                                headers={"Authorization": f"Bearer {cfg.encrypted_secret}"},
                                json_body=body, timeout=60, provider="DeepSeek")
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        _record(db, provider, model, operation,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                cost=usage.get("prompt_tokens", 0) * 3e-7 + usage.get("completion_tokens", 0) * 1e-6,
                correlation_id=correlation_id)
        try:
            return json.loads(data["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError):
            if attempt == 1:
                raise ValueError("Sortie DeepSeek hors schéma après retry")
            body["messages"].append({"role": "user", "content": "Réponds uniquement en JSON valide."})
    raise ValueError("unreachable")


def _deepseek_mock(operation: str, payload: dict) -> dict:
    if operation in ("exercise_generation", "exercise_repair"):
        # Exercices déterministes au contrat exgen-3 : LaTeX balisé $...$,
        # mélange application/problème/QCM/rubric pour exercer toute la chaîne.
        import random
        level = int(payload.get("difficulty_level", payload.get("level", 3)))
        count = int(payload.get("count", 3))
        rng = random.Random(f"{payload.get('competency_code')}-{level}")
        span = 4 + level * 6
        exercises = []
        for i in range(max(1, count)):
            kind = rng.choice(
                ["add", "frac", "qcm", "prob"] if level <= 3
                else ["frac", "expr", "qcm", "prob"])
            a, b = rng.randint(2, span), rng.randint(2, span)
            if kind == "add":
                exercises.append({
                    "kind": "application",
                    "statement": f"Calculer : ${a} + {b}$",
                    "correction": f"${a} + {b} = {a + b}$",
                    "response_type": "short_text",
                    "answer": {"type": "integer", "value": a + b}})
            elif kind == "frac":
                d = rng.choice([2, 3, 4, 5])
                exercises.append({
                    "kind": "application",
                    "statement": (f"Calculer et donner le résultat sous forme de fraction "
                                  f"irréductible : $\\dfrac{{{a}}}{{{d}}} + \\dfrac{{{b}}}{{{d}}}$"),
                    "correction": (f"Les dénominateurs sont égaux : "
                                   f"$\\dfrac{{{a}}}{{{d}}} + \\dfrac{{{b}}}{{{d}}} = "
                                   f"\\dfrac{{{a + b}}}{{{d}}}$"),
                    "response_type": "short_text",
                    "answer": {"type": "rational", "value": [a + b, d]}})
            elif kind == "expr":
                exercises.append({
                    "kind": "application",
                    "statement": f"Développer puis réduire : ${a}(x + {b})$",
                    "correction": f"${a}(x + {b}) = {a}x + {a * b}$",
                    "response_type": "short_text",
                    "answer": {"type": "expression", "value": f"{a}*x + {a * b}",
                               "variable": "x"}})
            elif kind == "prob":
                unit_price, qty = rng.randint(2, 9), rng.randint(3, 8)
                total = unit_price * qty
                exercises.append({
                    "kind": "probleme",
                    "statement": (f"Lina achète {qty} cahiers à ${unit_price}\\ \\text{{€}}$ "
                                  "pièce. Elle paie avec un billet de $50\\ \\text{€}$. "
                                  "Combien la caissière doit-elle lui rendre ? "
                                  "Détaille ton raisonnement."),
                    "correction": (f"Prix total : ${qty} \\times {unit_price} = {total}\\ \\text{{€}}$. "
                                   f"Monnaie rendue : $50 - {total} = {50 - total}\\ \\text{{€}}$."),
                    "response_type": "multiline_text",
                    "answer": {"type": "rubric", "steps": [
                        {"description": "Calcul du prix total",
                         "expected_text": f"${qty} \\times {unit_price} = {total}$",
                         "points": 1},
                        {"description": "Calcul de la monnaie rendue",
                         "expected_text": f"$50 - {total} = {50 - total}$", "points": 1},
                    ]}})
            else:
                good = a * b
                choices = [f"${good}$", f"${good + a}$", f"${good - b}$", f"${a + b}$"]
                rng.shuffle(choices)
                exercises.append({
                    "kind": "application",
                    "statement": f"Que vaut ${a} \\times {b}$ ?",
                    "correction": f"${a} \\times {b} = {good}$",
                    "response_type": "qcm_single",
                    "choices": choices,
                    "answer": {"type": "choice",
                               "correct": [choices.index(f"${good}$")]}})
        return {"exercises": exercises, "confidence": 0.9, "reason_code": "mock_generation"}
    if operation == "sesamaths_structure":
        # Structuration d'une Série de manuel : quelques exercices synthétiques
        # au contrat exgen-3, seedés par chapitre+série pour rester déterministes.
        import random
        rng = random.Random(f"{payload.get('chapter_code')}-{payload.get('series_number')}")
        n = rng.randint(1, 3)
        exercises = []
        for i in range(n):
            a, b = rng.randint(2, 20), rng.randint(2, 20)
            difficulty = rng.randint(1, 5)
            if i % 2 == 0:
                exercises.append({
                    "kind": "application",
                    "statement": f"Calculer : ${a} + {b}$",
                    "correction": f"${a} + {b} = {a + b}$",
                    "response_type": "short_text",
                    "answer": {"type": "integer", "value": a + b},
                    "difficulty": difficulty})
            else:
                good = a + b
                choices = [f"${good}$", f"${good + 1}$", f"${good - 1}$"]
                rng.shuffle(choices)
                exercises.append({
                    "kind": "application",
                    "statement": f"Que vaut ${a} + {b}$ ?",
                    "correction": f"${a} + {b} = {good}$",
                    "response_type": "qcm_single",
                    "choices": choices,
                    "answer": {"type": "choice", "correct": [choices.index(f"${good}$")]},
                    "difficulty": difficulty})
        return {"exercises": exercises, "confidence": 0.9, "reason_code": "mock_structure"}
    if operation in ("lesson_snippet", "lesson_repair"):
        label = payload.get("competency_label", "la notion")
        return {
            "title": f"Rappel — {label}"[:100],
            "essentiel": f"Pour {label.lower()}, on avance pas à pas. "
                         "Relis la règle avant de commencer.",
            "methode": [
                "Repère les données utiles de l'énoncé.",
                "Applique la règle vue en classe.",
                "Vérifie que ton résultat est logique."],
            "exemple": {
                "enonce": "On veut calculer $12 + 9$.",
                "etapes": ["On pose $12 + 9$.", "On calcule : $12 + 9 = 21$."],
                "resultat": "Le résultat est $21$."},
            "encarts": [
                {"type": "conseil",
                 "texte": "Vérifie toujours l'ordre de grandeur de ton résultat."},
                {"type": "attention",
                 "texte": "Ne confonds pas l'ordre des opérations avec l'ordre de l'énoncé."}],
            "confidence": 0.9, "reason_code": "mock_lesson"}
    if operation == "rubric_grading":
        rubric = payload.get("rubric", [])
        return {"steps": [{"step": s.get("step"), "observed": True, "points": s.get("points", 1),
                           "evidence": "mock"} for s in rubric],
                "total_points": sum(s.get("points", 1) for s in rubric),
                "confidence": 0.9, "reason_code": "mock_rubric", "evidence_ids": []}
    if operation == "level_proposal":
        return {"proposed_level": payload.get("current_level", 5), "confidence": 0.7,
                "reason_code": "mock_stable", "evidence_ids": []}
    if operation == "exercise_selection":
        return {"selected": payload.get("candidates", [])[:payload.get("count", 4)],
                "confidence": 0.8, "reason_code": "mock_selection", "evidence_ids": []}
    return {"confidence": 0.5, "reason_code": "mock_default"}


# ------------------------------------------------------------------- Claude

def claude_text(db: Session, operation: str, system: str, user_text: str,
                max_tokens: int = 350, correlation_id: str | None = None) -> str:
    """Claude Haiku pour la rédaction courte — jamais pour la note primaire (§8.1)."""
    cfg = _config(db, "anthropic")
    if _today_cost(db, "anthropic") > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded("Budget Anthropic quotidien atteint")
    model = cfg.model if cfg and cfg.model else settings.claude_model

    if _mock_enabled(db, cfg):
        _record(db, "anthropic", model, operation, input_tokens=300, output_tokens=120,
                cost=0.0, correlation_id=correlation_id)
        return ("Bon travail ce mois-ci : les calculs avec les nombres relatifs progressent "
                "nettement. Continue à détailler tes étapes pour les équations ; "
                "une petite révision des fractions est prévue la semaine prochaine.")

    r = _post_with_deadline(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg.encrypted_secret, "anthropic-version": "2023-06-01"},
        json_body={
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_text}],
        },
        timeout=60, provider="Claude",
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    _record(db, "anthropic", model, operation,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cost=usage.get("input_tokens", 0) * 1e-6 + usage.get("output_tokens", 0) * 5e-6,
            correlation_id=correlation_id)
    return "".join(b.get("text", "") for b in data.get("content", []))


def claude_json(db: Session, operation: str, system: str, payload: dict,
                max_tokens: int = 500, model: str | None = None,
                correlation_id: str | None = None) -> dict:
    """Claude en mode JSON pour vérification croisée (exercices, rappels) et
    pour l'adaptateur Sésamaths (JSON brut -> contrat app, texte pur, pas
    d'image). `model` explicite (ex. repli Opus) prioritaire sur la config."""
    cfg = _config(db, "anthropic")
    if _today_cost(db, "anthropic") > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded("Budget Anthropic quotidien atteint")
    model = model or (cfg.model if cfg and cfg.model else settings.claude_model)

    if _mock_enabled(db, cfg):
        _record(db, "anthropic", model, operation, input_tokens=400, output_tokens=80,
                cost=0.0, correlation_id=correlation_id)
        # Mock pour exercice/leçon : verdict structuré favorable (contrat exgen-3)
        if operation == "exercise_verification":
            return {"valide": True,
                    "scores": {"justesse": 5, "adequation_competence": 5,
                               "adequation_niveau": 4, "clarte": 5},
                    "problemes": [], "reparable": False,
                    "raison": "Mock : verdict favorable en mode test"}
        if operation == "lesson_verification":
            return {"valide": True,
                    "scores": {"justesse": 5, "simplicite": 5, "utilite": 5},
                    "problemes": [], "reparable": False,
                    "raison": "Mock : verdict favorable en mode test"}
        if operation == "appreciation_synthesis":
            due = payload.get("due_competencies") or []
            weak = [d.get("competency_id") for d in due if (d.get("mastery") or 0) < 0.5]
            return {"synthesis": ("Bon travail sur ce sujet : les progrès mesurés sont "
                                  "nets, continue sur cette lancée pour la suite."),
                    "next_plan": {
                        "competency_ids": [d.get("competency_id") for d in due[:3]],
                        "difficulty_level": 3, "quantity": 4,
                        "kind_mix": {"application": 0.55, "probleme": 0.35, "qcm": 0.10},
                        "pacing_days": 7, "lesson_competency_ids": weak[:2]}}
        if operation == "sesamaths_adapt":
            return _sesamaths_adapt_mock(correlation_id or "")
        return {}

    # préremplissage assistant "{" (force une sortie JSON, l'API Messages n'a
    # pas de response_format JSON) : REFUSÉ par un 400 sur la famille Claude
    # 4.6+ (Opus 4.6/4.7/4.8, Sonnet 4.6/5, Fable 5), cf. claude_vision_json —
    # même garde-fou ici, sinon le repli Opus de l'adaptateur Sésamaths
    # échouerait systématiquement en 400.
    prefill_ok = _supports_assistant_prefill(model)
    messages = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    if prefill_ok:
        messages.append({"role": "assistant", "content": "{"})
    system_text = system if prefill_ok else (
        system + "\n\nRéponds UNIQUEMENT par l'objet JSON demandé, sans texte "
        "avant ni après, sans bloc de code Markdown.")

    r = _post_with_deadline(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg.encrypted_secret, "anthropic-version": "2023-06-01"},
        json_body={
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        },
        timeout=60, provider="Claude",
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    # tarifs : Opus 4.8 ~5$/25$ ; Haiku 4.5 ~1$/5$ par MTok (même barème que
    # claude_vision_json — un appel Opus ici (repli adaptateur) doit être
    # comptabilisé à son vrai coût, pas au tarif Haiku)
    in_rate, out_rate = (5e-6, 25e-6) if "opus" in (model or "") else (1e-6, 5e-6)
    _record(db, "anthropic", model, operation,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cost=usage.get("input_tokens", 0) * in_rate + usage.get("output_tokens", 0) * out_rate,
            correlation_id=correlation_id)

    if data.get("stop_reason") == "max_tokens":
        raise ValueError(
            f"Réponse Claude JSON TRONQUÉE ({model}) : dépasse "
            f"max_tokens={max_tokens}. Augmente max_tokens pour cet appel.")
    body = "".join(b.get("text", "") for b in data.get("content", []))
    content_text = ("{" + body) if prefill_ok else body
    try:
        return json.loads(content_text)
    except json.JSONDecodeError:
        # tolérer du texte résiduel après l'objet JSON
        m = _JSON_OBJ_RE.search(content_text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError("Réponse Claude hors schéma JSON")


def claude_vision_json(db: Session, operation: str, system: str, user_text: str,
                       image_png: bytes, max_tokens: int = 4000,
                       model: str | None = None,
                       correlation_id: str | None = None) -> dict:
    """Claude multimodal en mode JSON : extraction d'exercices depuis l'IMAGE
    d'une page de manuel (§ pipeline Sésamaths vision). Même transport que
    claude_json (préremplissage "{" pour forcer le JSON), l'image étant jointe
    en base64 avant la consigne texte."""
    import base64

    cfg = _config(db, "anthropic")
    model = model or (cfg.model if cfg and cfg.model else settings.claude_vision_model)
    if _today_cost(db, "anthropic") > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded("Budget Anthropic quotidien atteint")

    if _mock_enabled(db, cfg):
        _record(db, "anthropic", model, operation, input_tokens=1500, output_tokens=400,
                cost=0.0, correlation_id=correlation_id)
        return _claude_vision_mock(operation, correlation_id or "")

    b64 = base64.b64encode(image_png).decode()
    # Le préremplissage assistant "{" (qui force le JSON) est REFUSÉ par un 400
    # sur la famille Claude 4.6+ (Opus 4.6/4.7/4.8, Sonnet 4.6/5, Fable 5) ; il
    # reste valide sur Haiku 4.5. Sans ce garde-fou, tout repli Opus échoue
    # systématiquement en 400 (cf. incident extraction Sésamaths).
    prefill_ok = _supports_assistant_prefill(model)
    messages = [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png", "data": b64}},
            {"type": "text", "text": user_text},
        ]},
    ]
    if prefill_ok:
        messages.append({"role": "assistant", "content": "{"})
    else:
        system += ("\n\nRéponds UNIQUEMENT par l'objet JSON demandé, sans texte "
                   "avant ni après, sans bloc de code Markdown.")

    r = _post_with_deadline(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg.encrypted_secret, "anthropic-version": "2023-06-01"},
        json_body={
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        },
        timeout=90, provider="Claude",
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    # tarifs : Opus 4.8 ~5$/25$ ; Haiku 4.5 ~1$/5$ par MTok
    in_rate, out_rate = (5e-6, 25e-6) if "opus" in (model or "") else (1e-6, 5e-6)
    _record(db, "anthropic", model, operation,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cost=usage.get("input_tokens", 0) * in_rate + usage.get("output_tokens", 0) * out_rate,
            correlation_id=correlation_id)

    # Sortie coupée par max_tokens : le JSON est tronqué en plein milieu, donc
    # illisible. On le dit clairement au lieu de « hors schéma JSON », qui
    # laissait croire à un modèle fautif (cf. page 6 perdue sur les 2 modèles).
    if data.get("stop_reason") == "max_tokens":
        raise ValueError(
            f"Réponse Claude vision TRONQUÉE ({model}) : la page dépasse "
            f"max_tokens={max_tokens}. Augmente max_tokens pour cette page.")
    body = "".join(b.get("text", "") for b in data.get("content", []))
    content_text = ("{" + body) if prefill_ok else body
    try:
        return json.loads(content_text)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(content_text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"Réponse Claude vision hors schéma JSON ({model}, "
            f"{len(content_text)} car.) : {content_text[:200]!r}")


def _claude_vision_mock(operation: str, correlation_id: str) -> dict:
    """Extraction BRUTE simulée (appel 1, extracteur — schéma générique à
    marqueurs, aucune résolution) : quelques exercices synthétiques, seedés
    par la page pour rester déterministes en test — exerce toute la chaîne
    extraction+adaptation+validation sans réseau. Longueur (3) volontairement
    alignée sur `_sesamaths_adapt_mock` : `sesamaths._adapt_page` exige une
    correspondance 1:1 entre exercices bruts et adaptés."""
    import random
    rng = random.Random(correlation_id or "sesa-extract")
    a, b = rng.randint(2, 20), rng.randint(2, 20)
    exercises = [
        {"number": "1", "title": None,
         "text": (f"Voici une liste de nombres : $221$, $4\\,065$, $940$. "
                  f"Combien valent ${a} + {b}$ ? " + "{{blank}}")},
        {"number": "2", "title": "Division euclidienne",
         "text": "Complète le tableau de la division euclidienne.",
         "table": {"rows": 2, "cols": 2,
                   "col_labels": ["Quotient", "Reste"],
                   "row_labels": ["$87$ par $9$", "$764$ par $8$"],
                   "cells": [[{"value": None, "given": False}, {"value": None, "given": False}],
                             [{"value": None, "given": False}, {"value": None, "given": False}]]}},
        {"number": "3", "title": None,
         "text": (f"Que vaut ${a} \\times {b}$ ? " + "{{check}} " + f"${a * b}$  "
                  + "{{check}} " + f"${a * b + a}$  " + "{{check}} " + f"${a + b}$")},
    ]
    return {"exercises": exercises, "confidence": 0.9, "reason_code": "mock_extract"}


def _sesamaths_adapt_mock(correlation_id: str) -> dict:
    """Adaptation simulée (appel 2, adaptateur — contrat app) : LaTeX balisé,
    tableau, QCM. Contenu volontairement indépendant du JSON brut reçu (le
    mock n'a pas besoin de comprendre les marqueurs), mais de MÊME LONGUEUR
    que `_claude_vision_mock` (3) pour respecter la correspondance 1:1."""
    import random
    rng = random.Random(correlation_id or "sesa-adapt")
    a, b = rng.randint(2, 20), rng.randint(2, 20)
    exercises = [
        {"kind": "application",
         "statement": f"Voici une liste de nombres : $221$, $4\\,065$, $940$. "
                      f"Combien valent ${a} + {b}$ ?",
         "correction": f"${a} + {b} = {a + b}$",
         "response_type": "short_text",
         "answer": {"type": "integer", "value": a + b},
         "difficulty": rng.randint(1, 3)},
        {"kind": "application",
         "statement": "Complète le tableau de la division euclidienne de $87$ par $9$.",
         "correction": "$87 = 9 \\times 9 + 6$",
         "response_type": "table_fill",
         "answer": {"type": "table", "rows": 2, "cols": 2,
                    "col_labels": ["Quotient", "Reste"],
                    "row_labels": ["$87$ par $9$", "$764$ par $8$"],
                    "cells": [[{"type": "integer", "value": 9}, {"type": "integer", "value": 6}],
                              [{"type": "integer", "value": 95}, {"type": "integer", "value": 4}]]},
         "difficulty": 2},
        {"kind": "application",
         "statement": f"Que vaut ${a} \\times {b}$ ?",
         "correction": f"${a} \\times {b} = {a * b}$",
         "response_type": "qcm_single",
         "choices": [f"${a * b}$", f"${a * b + a}$", f"${a + b}$"],
         "answer": {"type": "choice", "correct": [0]},
         "difficulty": rng.randint(3, 5)},
    ]
    return {"exercises": exercises, "confidence": 0.9, "reason_code": "mock_adapt"}

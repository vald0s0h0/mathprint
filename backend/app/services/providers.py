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
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ApiUsageEvent, ProviderConfig


class BudgetExceeded(Exception):
    pass


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


def _mock_enabled(db: Session, cfg: ProviderConfig | None) -> bool:
    from .runtime_settings import mock_enabled
    return mock_enabled(db) or cfg is None or not cfg.encrypted_secret


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
    r = httpx.post(
        "https://api.mathpix.com/v3/text",
        headers={"app_id": app_id, "app_key": app_key},
        json={
            "src": "data:image/png;base64," + __import__("base64").b64encode(image_bytes).decode(),
            "formats": ["text", "latex_styled"],
            "metadata": {"improve_mathpix": False},  # confidentialité (§6.3)
        },
        timeout=30,
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
    cfg = _config(db, "deepseek")
    if _today_cost(db, "deepseek") > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded("Budget DeepSeek quotidien atteint")
    model = model or (cfg.model if cfg and cfg.model else
                      (settings.deepseek_reasoning_model if reasoning else settings.deepseek_model))

    if _mock_enabled(db, cfg):
        _record(db, "deepseek", model, operation, input_tokens=200, output_tokens=80,
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
        r = httpx.post("https://api.deepseek.com/chat/completions",
                       headers={"Authorization": f"Bearer {cfg.encrypted_secret}"},
                       json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        _record(db, "deepseek", model, operation,
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
            "astuce": "Vérifie toujours l'ordre de grandeur de ton résultat.",
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

    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg.encrypted_secret, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_text}],
        },
        timeout=60,
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
                max_tokens: int = 500, correlation_id: str | None = None) -> dict:
    """Claude Haiku en mode JSON pour vérification croisée (exercices, rappels)."""
    cfg = _config(db, "anthropic")
    if _today_cost(db, "anthropic") > settings.llm_daily_cost_limit_eur:
        raise BudgetExceeded("Budget Anthropic quotidien atteint")
    model = cfg.model if cfg and cfg.model else settings.claude_model

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
            return {"synthesis": ("Bon travail sur ce sujet : les progrès mesurés sont "
                                  "nets, continue sur cette lancée pour la suite."),
                    "next_plan": {
                        "competency_ids": [d.get("competency_id") for d in due[:3]],
                        "difficulty_level": 3, "quantity": 4,
                        "kind_mix": {"application": 0.55, "probleme": 0.35, "qcm": 0.10},
                        "pacing_days": 7}}
        return {}

    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": cfg.encrypted_secret, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            # préremplissage "{" : force une sortie JSON (l'API Messages n'a
            # pas de response_format JSON)
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                         {"role": "assistant", "content": "{"}],
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    _record(db, "anthropic", model, operation,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cost=usage.get("input_tokens", 0) * 1e-6 + usage.get("output_tokens", 0) * 5e-6,
            correlation_id=correlation_id)

    content_text = "{" + "".join(b.get("text", "") for b in data.get("content", []))
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

"""Aiguillage du fournisseur LLM de la pipeline Indigo (onglet Exercices).

Les étapes LLM Indigo — découpage (indigo_segment), génération
(indigo_gemini ou indigo_qcm) et vérification (indigo_verify) — passent par CE
module, qui choisit le fournisseur à l'EXÉCUTION selon un réglage persisté
(SystemSetting « indigo_llm_provider »), réglable par un toggle dans l'onglet
Exercices. Défaut : Anthropic. Trois câblages :
  • anthropic : découpage + génération = Sonnet, vérification = Opus ;
  • deepseek  : les trois étapes = DeepSeek pro v4 ;
  • qcm       : mode « QCM only » — DeepSeek pro sur les deux étapes qui
    subsistent (découpage, puis génération QCM par services.indigo_qcm). Il n'y
    a pas d'étape de relecture : la vérification y est DÉTERMINISTE et locale
    (services.indigo_check), donc gratuite et reproductible.

Un seul réglage porte À LA FOIS le fournisseur et le mode. C'est délibéré :
l'onglet n'expose qu'un sélecteur à trois positions (« Anthropic », « DeepSeek »,
« QCM only »), et « QCM only » n'aurait aucun sens sur Anthropic — le mode
IMPLIQUE son fournisseur. `provider_key` et `mode` séparent les deux lectures.

Les tailles de lot (indigo_gemini.choose_batch_size = 5-7,
indigo_verify.choose_review_batch_size = 6-8) sont dimensionnées sur le plafond
de sortie le plus BAS (DeepSeek ≈ 8k) : elles restent donc valides pour les deux
fournisseurs (Claude, qui accepte de plus larges sorties, les traite sans souci).
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from ..config import settings
from . import providers
from .runtime_settings import get_setting

PROVIDERS = ("anthropic", "deepseek", "qcm")
SETTING_KEY = "indigo_llm_provider"
# Modes de GÉNÉRATION. « classic » = adaptation libre (indigo_gemini) puis
# relecture (indigo_verify) ; « qcm » = pipeline QCM only (indigo_qcm).
MODE_CLASSIC, MODE_QCM = "classic", "qcm"

# stage -> nom d'opération (traçabilité des coûts, page Coûts)
_OPERATION = {"segment": "indigo_segment", "adapt": "indigo_adapt",
              "review": "indigo_review", "qcm": "indigo_qcm"}


def get_provider(db: Session) -> str:
    """Fournisseur choisi (persisté), ou le défaut de config si absent/invalide."""
    val = (get_setting(db, SETTING_KEY) or {}).get("value")
    return val if val in PROVIDERS else settings.indigo_llm_provider_default


def set_provider(db: Session, provider: str, updated_by: str | None = None) -> str:
    """Persiste le fournisseur (rejette une valeur inconnue). Retourne la valeur."""
    if provider not in PROVIDERS:
        raise ValueError(f"Fournisseur inconnu : {provider!r} (attendu {PROVIDERS})")
    from ..models import SystemSetting
    row = db.get(SystemSetting, SETTING_KEY)
    if row is None:
        row = SystemSetting(key=SETTING_KEY)
        db.add(row)
    row.value_json = {"value": provider}
    row.version = (row.version or 0) + 1
    row.updated_by = updated_by
    db.commit()
    return provider


def mode(db: Session) -> str:
    """Mode de génération courant : MODE_QCM ou MODE_CLASSIC."""
    return MODE_QCM if get_provider(db) == "qcm" else MODE_CLASSIC


def _anthropic_model(stage: str) -> str:
    return {"segment": settings.indigo_anthropic_segment_model,
            "adapt": settings.indigo_anthropic_adapt_model,
            "review": settings.indigo_anthropic_review_model,
            # le mode QCM ne tourne jamais sur Anthropic ; ce repli n'existe que
            # pour ne pas lever un KeyError si un appelant s'égare.
            "qcm": settings.indigo_anthropic_adapt_model}[stage]


def model_for(db: Session, stage: str) -> str:
    """Modèle réellement utilisé pour une étape selon le fournisseur choisi —
    sert AUSSI de provenance (`IndigoExercise.model`)."""
    prov = get_provider(db)
    if prov == "qcm":
        return settings.indigo_qcm_model
    if prov == "deepseek":
        return settings.indigo_deepseek_model
    return _anthropic_model(stage)


def config_provider_key(db: Session) -> str:
    """Clé de configuration de fournisseur (Paramètres → Fournisseurs) dont dépend
    l'étape LLM : « deepseek-pro » (modèle « pro ») ou « anthropic »."""
    return "anthropic" if get_provider(db) == "anthropic" else "deepseek-pro"


def label(db: Session) -> str:
    return {"deepseek": "DeepSeek pro", "qcm": "DeepSeek pro (QCM only)"}.get(
        get_provider(db), "Anthropic (Claude)")


def offline(db: Session) -> bool:
    """Vrai si le fournisseur CHOISI n'a pas de clé configurée (les étapes LLM
    tournent alors sur le repli déterministe → exercices en repli OCR brut)."""
    return providers.offline(db, config_provider_key(db))


def call(db: Session, stage: str, system: str, payload: dict,
         correlation_id: str) -> dict | None:
    """Appelle l'étape `stage` (« segment » | « adapt » | « review ») sur le
    fournisseur choisi, sortie JSON. Retry sur rate-limit ET sur réponse
    TRONQUÉE (budget de sortie doublé, cf. `_output_budgets`) ; sinon lève
    (l'appelant dégrade proprement : repli OCR brut pour l'adaptation, version
    adaptée gardée pour la vérification, découpage géométrique pour le
    découpage).

    Le délai TOTAL est celui d'Indigo (`indigo_llm_call_timeout_s`), pas le
    défaut global : un LOT d'exercices est une génération légitimement longue
    (plusieurs milliers de tokens de sortie), et l'abandonner à 180 s renvoyait
    tout le lot en adaptation UN PAR UN — 5 à 7 fois plus d'appels, budget
    quotidien épuisé en une extraction (incident A1.3 du 02/08)."""
    prov = get_provider(db)
    if prov in ("deepseek", "qcm"):
        fn, model, max_tokens = (providers.deepseek_json,
                                 model_for(db, stage),
                                 settings.deepseek_max_output_tokens)
    else:
        fn, model, max_tokens = (providers.claude_json, _anthropic_model(stage),
                                 settings.indigo_anthropic_max_output_tokens)
    op = _OPERATION[stage]
    budgets = _output_budgets(max_tokens)
    for budget in budgets:
        for attempt in range(3):
            try:
                return fn(db, op, system, payload, max_tokens=budget,
                          model=model, correlation_id=correlation_id,
                          total_timeout=settings.indigo_llm_call_timeout_s)
            except Exception as e:
                if providers.is_rate_limited(e) and attempt < 2:
                    time.sleep(providers.retry_after_s(e, attempt))
                    continue
                if providers.is_truncated(e) and budget != budgets[-1]:
                    break                       # même appel, budget de sortie plus large
                raise
    return None


def _output_budgets(max_tokens: int) -> tuple[int, ...]:
    """Budgets de sortie essayés dans l'ordre : celui de la config, puis le
    double, puis le quadruple. Un lot d'exercices riches (tableaux, grilles,
    corrigés) dépasse régulièrement le premier — sans cette échelle, la réponse
    tronquée faisait échouer le lot ENTIER."""
    return (max_tokens, max_tokens * 2, max_tokens * 4)

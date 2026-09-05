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
  • multipass : mode « QCM multipass » — DeepSeek Flash simple sur les cinq
    passes d'un exercice source (services.indigo_multipass).

Un seul réglage porte À LA FOIS le fournisseur et le mode. C'est délibéré :
l'onglet n'expose qu'un sélecteur (« Anthropic », « DeepSeek », « QCM only »,
« QCM multipass »), et les deux modes QCM n'auraient aucun sens sur Anthropic —
le mode IMPLIQUE son fournisseur. `provider_key` et `mode` séparent les deux
lectures.

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

PROVIDERS = ("anthropic", "deepseek", "qcm", "multipass")
SETTING_KEY = "indigo_llm_provider"
# Modes de GÉNÉRATION. « classic » = adaptation libre (indigo_gemini) puis
# relecture (indigo_verify) ; « qcm » = pipeline QCM only (indigo_qcm) ;
# « multipass » = pipeline QCM multipass (indigo_multipass).
MODE_CLASSIC, MODE_QCM, MODE_MULTIPASS = "classic", "qcm", "multipass"

# stage -> nom d'opération (traçabilité des coûts, page Coûts). Les cinq passes
# du mode multipass sont nommées SÉPARÉMENT : elles tournent sur le même modèle,
# mais c'est ce qui permet de voir laquelle coûte réellement. C'est cette
# ventilation qui a montré que la génération valait 48 % de la facture parce
# qu'elle était relancée quatre fois par exercice (§ indigo_multipass).
_OPERATION = {"segment": "indigo_segment", "adapt": "indigo_adapt",
              "review": "indigo_review", "qcm": "indigo_qcm",
              "vision_extract": "indigo_mp_vision_extract",
              "mp_filter": "indigo_mp_filter", "mp_context": "indigo_mp_context",
              "mp_generate": "indigo_mp_generate",
              "mp_solve": "indigo_mp_solve", "mp_layout": "indigo_mp_layout",
              "mp_repair": "indigo_mp_repair"}


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
    """Mode de génération courant : MODE_QCM, MODE_MULTIPASS ou MODE_CLASSIC."""
    return {"qcm": MODE_QCM, "multipass": MODE_MULTIPASS}.get(
        get_provider(db), MODE_CLASSIC)


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
    # Une passe `mp_*` appartient intrinsèquement au mode multipass. La forcer
    # ici la garde sur DeepSeek Flash même si le sélecteur global change pendant
    # qu'une extraction attend son créneau d'heures creuses.
    prov = ("multipass" if stage.startswith("mp_")
            else get_provider(db))
    if prov == "multipass":
        # UN seul modèle pour les cinq passes ET pour le découpage : c'est le
        # parti pris du mode, la qualité vient des relectures, pas du modèle.
        return settings.indigo_multipass_model
    if prov == "qcm":
        return settings.indigo_qcm_model
    if prov == "deepseek":
        return settings.indigo_deepseek_model
    return _anthropic_model(stage)


def config_provider_key(db: Session) -> str:
    """Clé de configuration de fournisseur (Paramètres → Fournisseurs) dont dépend
    l'étape LLM : « anthropic », ou la clé DeepSeek du MODÈLE réellement appelé.

    Elle est déduite du modèle et non du mode : « QCM multipass » tourne sur un
    FLASH, donc sur la clé « deepseek-flash ». La coder à « deepseek-pro » aurait
    fait annoncer « clé absente » à une instance parfaitement configurée — et
    lire le plafond de dépense du mauvais fournisseur."""
    prov = get_provider(db)
    if prov == "anthropic":
        return "anthropic"
    return providers.provider_for_model(model_for(db, "adapt"))


def label(db: Session) -> str:
    return {"deepseek": "DeepSeek pro", "qcm": "DeepSeek pro (QCM only)",
            "multipass": "DeepSeek Flash (QCM multipass)"}.get(
        get_provider(db), "Anthropic (Claude)")


def offline(db: Session) -> bool:
    """Vrai si le fournisseur CHOISI n'a pas de clé configurée (les étapes LLM
    tournent alors sur le repli déterministe → exercices en repli OCR brut)."""
    return providers.offline(db, config_provider_key(db))


def call(db: Session, stage: str, system: str, payload: dict,
         correlation_id: str) -> dict | None:
    """Appelle l'étape `stage` sur le fournisseur choisi, sortie JSON. Retry sur
    rate-limit ET sur réponse TRONQUÉE (budget de sortie doublé, cf.
    `_output_budgets`) ; sinon lève (l'appelant dégrade proprement : repli OCR
    brut pour l'adaptation, version adaptée gardée pour la vérification,
    découpage géométrique pour le découpage).

    Le délai TOTAL est celui d'Indigo (`indigo_llm_call_timeout_s`), pas le
    défaut global : un LOT d'exercices est une génération légitimement longue
    (plusieurs milliers de tokens de sortie), et l'abandonner à 180 s renvoyait
    tout le lot en adaptation UN PAR UN — 5 à 7 fois plus d'appels, budget
    quotidien épuisé en une extraction (incident A1.3 du 02/08)."""
    prov = ("multipass" if stage.startswith("mp_")
            else get_provider(db))
    extra: dict = {}
    if prov == "multipass":
        # budget de départ propre au mode : la passe 2 écrit TROIS exercices
        # complets d'un coup (§ config).
        fn, model, max_tokens = (providers.deepseek_json,
                                 settings.indigo_multipass_model,
                                 settings.indigo_multipass_max_output_tokens)
        # RAISONNEMENT DÉSACTIVÉ sur les cinq passes. DeepSeek V4 l'active par
        # défaut, et il consommait tout le budget de sortie sans rendre le
        # moindre JSON (extraction A1.2 du 03/09 : 25 appels de génération à
        # 8092 tokens de sortie en moyenne, soit le plafond, pour zéro exercice).
        # Ce mode ne perd rien à s'en passer : sa justesse ne vient pas d'un
        # appel qui réfléchit plus longtemps mais de cinq passes qui se
        # relisent, dont une résolution INDÉPENDANTE — un second avis qu'aucune
        # trace de raisonnement interne ne peut donner.
        extra["thinking"] = False
    elif prov in ("deepseek", "qcm"):
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
                          total_timeout=settings.indigo_llm_call_timeout_s,
                          **extra)
            except Exception as e:
                if providers.is_rate_limited(e) and attempt < 2:
                    time.sleep(providers.retry_after_s(e, attempt))
                    continue
                if providers.is_truncated(e) and budget != budgets[-1]:
                    break                       # même appel, budget de sortie plus large
                raise
    return None


def call_vision(db: Session, system: str, payload: dict, images: list[bytes],
                correlation_id: str,
                validator=None) -> dict | None:
    """Extraction multimodale du mode multipass.

    Même clé, budget, journal de coûts et politique de retry que les cinq passes
    texte, mais modèle Vision imposé. Les images sont toujours envoyées avec leur
    résolution d'origine : le texte mathématique et les petits badges de section
    deviennent vite illisibles en mode ``low`` (512 px).
    """
    budgets = _output_budgets(settings.indigo_multipass_vision_max_output_tokens)
    for budget in budgets:
        for attempt in range(3):
            try:
                return providers.deepseek_json(
                    db, _OPERATION["vision_extract"], system, payload,
                    max_tokens=budget,
                    model=settings.indigo_multipass_vision_model,
                    correlation_id=correlation_id,
                    total_timeout=settings.indigo_llm_call_timeout_s,
                    thinking=False, images=images, image_detail="original",
                    validator=validator,
                    repair_instruction=(
                        "Relis les images et rends tous les champs requis. Une figure "
                        "exige une description et un crop précis."))
            except Exception as e:
                if providers.is_rate_limited(e) and attempt < 2:
                    time.sleep(providers.retry_after_s(e, attempt))
                    continue
                if providers.is_truncated(e) and budget != budgets[-1]:
                    break
                raise
    return None


def _output_budgets(max_tokens: int) -> tuple[int, ...]:
    """Budgets de sortie essayés dans l'ordre : celui de la config, puis le
    double, puis le quadruple. Un lot d'exercices riches (tableaux, grilles,
    corrigés) dépasse régulièrement le premier — sans cette échelle, la réponse
    tronquée faisait échouer le lot ENTIER."""
    return (max_tokens, max_tokens * 2, max_tokens * 4)

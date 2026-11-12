"""Réglages persistés en base (system_settings), avec repli sur la config env.

Les fournisseurs LLM/OCR basculent sur un repli déterministe hors-ligne dès
qu'aucune clé n'est configurée (cf. providers._mock_enabled) — il n'existe
plus de « mode mock » global ni de classe fictive.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SystemSetting


def get_setting(db: Session, key: str) -> dict | None:
    row = db.get(SystemSetting, key)
    return row.value_json if row else None


def ocr_confidence_threshold(db: Session) -> float:
    """Seuil commun lecture CV/Mathpix -> reprise professeur.

    Il vit dans les réglages usuels afin que la file « OCRiser », le badge de
    lot et le démarrage de la correction appliquent toujours la même valeur.
    Les valeurs hors plage sont ignorées plutôt que de bloquer un ancien réglage.
    """
    saved = get_setting(db, "ocr_confidence_threshold") or {}
    try:
        value = float(saved.get("value", 0.90))
    except (TypeError, ValueError):
        value = 0.90
    return value if 0.0 < value <= 1.0 else 0.90


CONSTRUCTION_THRESHOLD_DEFAULT = 0.90
CONSTRUCTION_AUDIT_DEFAULT = 0.20


def construction_confidence_threshold(db: Session) -> float:
    """Seuil de la lecture CV des tracés (géométrie à tracer) : au-dessus, la note
    est appliquée ; en dessous, correction manuelle. Réglable en Pédagogie, et
    écrit par scripts/calibrate_construction.py --apply. Au-delà de 1 : aucune
    note automatique."""
    saved = get_setting(db, "construction_confidence_threshold") or {}
    try:
        value = float(saved.get("value", CONSTRUCTION_THRESHOLD_DEFAULT))
    except (TypeError, ValueError):
        value = CONSTRUCTION_THRESHOLD_DEFAULT
    return value if 0.0 < value <= 1.01 else CONSTRUCTION_THRESHOLD_DEFAULT


def construction_audit_share(db: Session) -> float:
    """Part des tracés notés automatiquement qui passent quand même chez le
    professeur, tirée au hasard (stable par réponse) : elle alimente le calibrage
    du seuil sur de vraies copies. 0 une fois le seuil établi."""
    saved = get_setting(db, "construction_audit_share") or {}
    try:
        value = float(saved.get("value", CONSTRUCTION_AUDIT_DEFAULT))
    except (TypeError, ValueError):
        value = CONSTRUCTION_AUDIT_DEFAULT
    return value if 0.0 <= value <= 1.0 else CONSTRUCTION_AUDIT_DEFAULT


def appreciation_synthesis_enabled(db: Session) -> bool:
    """Phrase encourageante Claude Haiku de la zone Appréciation (services
    .appreciation) — désactivée par défaut : la partie déterministe
    (progrès de compétences) reste affichée quoi qu'il arrive, seule la
    synthèse Haiku est concernée par ce réglage."""
    saved = get_setting(db, "appreciation_synthesis_enabled") or {}
    return bool(saved.get("value", False))


def llm_confidence_threshold(db: Session) -> float:
    """Seuil DeepSeek -> correction manuelle, réglable en Pédagogie.

    Une confiance exactement égale au seuil est acceptée : seule une valeur
    strictement inférieure doit ouvrir l'assistant professeur.
    """
    fallback = float(settings.correction_confidence_min)
    saved = get_setting(db, "llm_confidence_threshold") or {}
    try:
        value = float(saved.get("value", fallback))
    except (TypeError, ValueError):
        value = fallback
    return value if 0.0 < value <= 1.0 else fallback


# ---------------------------------------------------------------- templates

# Templates de documents (§5) éditables dans Paramètres → Documents :
# en-tête et carte exercice. Seuls les paramètres visuels
# sont exposés — la géométrie des marqueurs (QR/fiduciels) reste FIGÉE.
DEFAULT_TEMPLATES: dict = {
    "header": {
        "name_size": 14,        # ligne "Nom  /  Classe"
        "title_size": 8,        # titre du sujet
        "accent": "#37474F",    # filet séparateur + titre
        "show_date": True,
    },
    "exercise": {
        "font_size": 9,         # texte de l'énoncé
        "math_size": 9,         # expression mathématique (centrée ou inline) —
                                 # même corps que le texte : une formule ne doit
                                 # pas trancher visuellement avec l'énoncé qui
                                 # l'entoure
        "border": "#C7CDD4",    # cadre de la carte
        "radius": 2.2,          # rayon des coins (mm)
        "shadow": True,
        # pas d'accent ni de title_size : la carte n'a plus de ligne de titre,
        # le numéro vit dans un badge dont la couleur EST la difficulté
        # (pdfgen.DIFFICULTY_COLORS, non réglable).
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        elif k in out:
            out[k] = v
    return out


def doc_templates(db: Session) -> dict:
    saved = get_setting(db, "doc_templates") or {}
    return {k: _merge(DEFAULT_TEMPLATES[k], saved.get(k, {}))
            for k in DEFAULT_TEMPLATES}


# ------------------------------------------------------ lecture OCR multicouche
# Réglages de la section « Lecture OCR » (Paramètres admin). Chacun a une
# valeur par défaut sûre ; une valeur enregistrée invalide est ignorée plutôt
# que de bloquer la pipeline. `ocr_mode` vaut `mathpix` (l'existant, par défaut),
# `shadow` (la pipeline locale enregistre, Mathpix décide) ou `cascade` (la
# pipeline locale décide). Seul l'utilisateur passe en `cascade`.

OCR_MODES = ("mathpix", "shadow", "cascade")
OCR_ZONE_CLASSES = ("num_line", "math_line", "text_line", "multiline_line")

# Plan de moteurs par classe de zone. `stage1` lit toujours ; `stage2` ne lit
# que si l'étape 1 n'a pas conclu ; `observe` lit tout et ne vote jamais : ces
# lectures ne servent qu'à la mesure (apport marginal, corrélations) jusqu'à
# ce que ~10 000 copies permettent de trancher entre couples et ordres.
DEFAULT_ENGINE_PLAN: dict = {
    "families": {
        "trocr": "encdec",
        "apple_vision": "apple",
        "unimumer": "qwen35",
        "lighton": "pixtral_qwen3",    # LightOnOCR-2-1B : vision Pixtral, LM Qwen3
    },
    # Une paire corrélée compte pour deux voix seulement si aucun moteur d'une
    # autre famille ne lit une autre surface ; sinon c'est un conflit.
    "correlated_pairs": [["qwen35", "pixtral_qwen3"]],
    # Moteurs sachant calculer un score forcé (le plan de la classe décide lesquels
    # servent : seuls ceux qui votent évaluent, un observateur n'influence rien).
    "scorers": ["unimumer", "lighton", "trocr"],
    "classes": {
        "num_line": {"stage1": ["apple_vision", "unimumer"], "stage2": ["lighton"],
                     "observe": ["trocr"]},
        "math_line": {"stage1": ["unimumer", "lighton"], "stage2": ["apple_vision"],
                      "observe": ["trocr"]},
        "text_line": {"stage1": ["lighton", "apple_vision"], "stage2": [],
                      "observe": ["trocr"]},
        "multiline_line": {"stage1": ["lighton", "apple_vision"], "stage2": [],
                           "observe": ["trocr"]},
    },
}

# Seuils PROVISOIRES (banc du lot 0 : 13 écritures réelles seulement). Tant que
# des vraies copies ne les ont pas recalés, ils sont volontairement stricts.
DEFAULT_ENGINE_THRESHOLDS: dict = {
    "apple_vision": {"min_confidence": 0.5},
    "unimumer": {"min_logprob_mean": -0.5},
    "lighton": {"min_logprob_mean": -0.5},
    "trocr": {"min_logprob_mean": -0.5},
    "mathpix": {"min_confidence": 0.9},
    # garde-fou encre/longueur : largeur d'encre / hauteur de découpe, par caractère
    "ink_guard": {"min_per_char": 0.12, "max_per_char": 0.45},
}

OCR_DEFAULTS: dict = {
    "ocr_mode": "mathpix",
    "ocr_audit_share": 0.02,
    # mode ombre : part des zones ACCEPTÉES par Mathpix envoyées au professeur comme « Contrôle ».
    # Sans elle, la vérité terrain ne couvre que les cas difficiles (ceux que Mathpix doute) et le
    # taux d'erreur de la lecture locale sur les cas faciles reste inconnu.
    "ocr_shadow_audit_share": 0.05,
    # lot en attente du Mac depuis plus de N heures : alerte visible et possibilité de le relire avec Mathpix
    "ocr_local_timeout_hours": 6,
    "ocr_full_sample_share": 0.05,
    "ocr_accept_confidence": 0.95,
    "ocr_score_margin": 2.0,
    "ocr_montage_enabled": False,
    "ocr_montage_max_crops": 12,
    "ocr_montage_flush_minutes": 10,
    "ocr_lease_minutes": 15,
}


def _is_share(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def validate_ocr_plan(plan) -> dict:
    """Plan de moteurs : lève ValueError s'il est incohérent."""
    if not isinstance(plan, dict):
        raise ValueError("plan : objet attendu")
    families = plan.get("families")
    classes = plan.get("classes")
    if not isinstance(families, dict) or not all(isinstance(k, str) and isinstance(v, str) and v
                                                 for k, v in families.items()):
        raise ValueError("plan.families : {moteur: famille} attendu")
    if not isinstance(classes, dict) or set(classes) - set(OCR_ZONE_CLASSES):
        raise ValueError(f"plan.classes : clés parmi {', '.join(OCR_ZONE_CLASSES)}")
    for cls, roles in classes.items():
        if not isinstance(roles, dict) or set(roles) - {"stage1", "stage2", "observe"}:
            raise ValueError(f"plan.classes.{cls} : stage1, stage2, observe")
        seen: list[str] = []
        for role, engines in roles.items():
            if not isinstance(engines, list) or not all(isinstance(e, str) for e in engines):
                raise ValueError(f"plan.classes.{cls}.{role} : liste de moteurs")
            for e in engines:
                if e not in families:
                    raise ValueError(f"plan.classes.{cls}.{role} : moteur {e!r} sans famille")
                if e in seen:
                    raise ValueError(f"plan.classes.{cls} : {e!r} apparaît dans deux rôles")
                seen.append(e)
        if len(roles.get("stage1", [])) < 2:
            raise ValueError(f"plan.classes.{cls}.stage1 : au moins deux moteurs")
    scorers = plan.get("scorers", [])
    if not isinstance(scorers, list) or not all(isinstance(e, str) and e in families for e in scorers):
        raise ValueError("plan.scorers : liste de moteurs connus")
    pairs = plan.get("correlated_pairs", [])
    if not isinstance(pairs, list) or not all(
            isinstance(p, list) and len(p) == 2 and all(isinstance(f, str) for f in p) for p in pairs):
        raise ValueError("plan.correlated_pairs : liste de paires de familles")
    return plan


def validate_ocr_setting(key: str, value: dict) -> dict:
    """Normalise la valeur d'un réglage OCR ({"value": ...}) ou lève ValueError."""
    if key == "ocr_engine_plan":
        validate_ocr_plan(value.get("value"))
    elif key == "ocr_engine_thresholds":
        v = value.get("value")
        if not isinstance(v, dict) or not all(isinstance(x, dict) for x in v.values()):
            raise ValueError("seuils : {moteur: {seuil: nombre}} attendu")
        for sub in v.values():
            if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in sub.values()):
                raise ValueError("seuils : valeurs numériques")
    elif key == "ocr_mode":
        if value.get("value") not in OCR_MODES:
            raise ValueError(f"ocr_mode : {', '.join(OCR_MODES)}")
    elif key in ("ocr_audit_share", "ocr_shadow_audit_share", "ocr_full_sample_share", "ocr_accept_confidence"):
        if not _is_share(value.get("value")):
            raise ValueError(f"{key} : nombre entre 0 et 1")
    elif key == "ocr_score_margin":
        v = value.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= float(v) <= 50:
            raise ValueError("ocr_score_margin : nombre entre 0 et 50")
    elif key == "ocr_montage_enabled":
        if not isinstance(value.get("value"), bool):
            raise ValueError("ocr_montage_enabled : booléen")
    elif key in ("ocr_montage_max_crops", "ocr_montage_flush_minutes", "ocr_lease_minutes",
                 "ocr_local_timeout_hours"):
        if not _is_pos_int(value.get("value")):
            raise ValueError(f"{key} : entier >= 1")
    elif key == "ocr_worker_token":
        v = value.get("value")
        if not isinstance(v, str) or (v and len(v) < 24):
            raise ValueError("ocr_worker_token : 24 caractères au moins (ou vide)")
    return value


OCR_SETTING_KEYS = frozenset(OCR_DEFAULTS) | {"ocr_engine_plan", "ocr_engine_thresholds",
                                              "ocr_worker_token"}
# Jamais renvoyés par GET /settings/system.
OCR_SECRET_KEYS = frozenset({"ocr_worker_token"})


def ocr_setting(db: Session, key: str):
    """Valeur d'un réglage scalaire de la lecture OCR, défaut si absente ou invalide."""
    default = OCR_DEFAULTS[key]
    saved = get_setting(db, key)
    if not saved or "value" not in saved:
        return default
    try:
        validate_ocr_setting(key, saved)
    except ValueError:
        return default
    value = saved["value"]
    return float(value) if isinstance(default, float) else value


def ocr_mode(db: Session) -> str:
    return ocr_setting(db, "ocr_mode")


def ocr_engine_plan(db: Session) -> dict:
    saved = get_setting(db, "ocr_engine_plan")
    if saved and "value" in saved:
        try:
            return validate_ocr_plan(saved["value"])
        except ValueError:
            pass
    return DEFAULT_ENGINE_PLAN


def ocr_engine_thresholds(db: Session) -> dict:
    """Seuils par moteur, fusionnés sur les défauts (un seuil absent garde son défaut)."""
    out = {k: dict(v) for k, v in DEFAULT_ENGINE_THRESHOLDS.items()}
    saved = get_setting(db, "ocr_engine_thresholds")
    if saved and "value" in saved:
        try:
            validate_ocr_setting("ocr_engine_thresholds", saved)
        except ValueError:
            return out
        for engine, sub in saved["value"].items():
            out.setdefault(engine, {}).update(sub)
    return out


def ocr_worker_token(db: Session) -> str:
    """Jeton du worker OCR : variable d'environnement d'abord, réglage ensuite.
    Vide = API du worker fermée. Ne doit jamais être journalisé."""
    if settings.ocr_worker_token:
        return settings.ocr_worker_token
    saved = get_setting(db, "ocr_worker_token") or {}
    value = saved.get("value")
    return value if isinstance(value, str) else ""

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
        "math_size": 12,        # expression mathématique centrée
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

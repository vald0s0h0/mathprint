"""Réglages persistés en base (system_settings), avec repli sur la config env.

Les fournisseurs LLM/OCR basculent sur un repli déterministe hors-ligne dès
qu'aucune clé n'est configurée (cf. providers._mock_enabled) — il n'existe
plus de « mode mock » global ni de classe fictive.
"""
from sqlalchemy.orm import Session

from ..models import SystemSetting


def get_setting(db: Session, key: str) -> dict | None:
    row = db.get(SystemSetting, key)
    return row.value_json if row else None


# ---------------------------------------------------------------- templates

# Templates de documents (§5) éditables dans Paramètres → Documents :
# en-tête, carte exercice et rappel de leçon. Seuls les paramètres visuels
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
    "lesson": {
        "font_size": 8,
        "bg": "#FFF6DF",
        "border": "#E4C46A",
        "text": "#6B5310",
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

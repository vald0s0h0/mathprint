"""Réglages persistés en base (system_settings), avec repli sur la config env.

Le mode mock est pilotable depuis Paramètres → Système : la valeur en base
prime sur MATHPRINT_MOCK_MODE. Quand il est désactivé, les classes mock sont
archivées et plus aucune donnée simulée n'apparaît dans l'application.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SchoolClass, SchoolYear, Student, SystemSetting


def get_setting(db: Session, key: str) -> dict | None:
    row = db.get(SystemSetting, key)
    return row.value_json if row else None


def mock_enabled(db: Session) -> bool:
    v = get_setting(db, "mock_mode")
    if v is not None and "enabled" in v:
        return bool(v["enabled"])
    return settings.mock_mode


def apply_mock_mode(db: Session, enabled: bool):
    """Archive/désarchive les classes mock pour qu'aucune trace ne subsiste
    quand le mode est désactivé (et réapparaisse s'il est réactivé)."""
    from ..models import now
    from .security import new_pseudonym

    mock_classes = db.query(SchoolClass).filter_by(is_mock=True).all()
    if not enabled:
        for c in mock_classes:
            c.archived_at = c.archived_at or now()
        return
    if mock_classes:
        for c in mock_classes:
            c.archived_at = None
        return
    # aucune classe mock : en recréer une (même contenu que le seed initial)
    from ..seed import MOCK_STUDENTS
    year = db.query(SchoolYear).filter_by(active=True).first()
    cls = SchoolClass(school_year_id=year.id if year else None,
                      name="5e Mock", grade_level="5e", is_mock=True)
    db.add(cls)
    db.flush()
    for last, first in MOCK_STUDENTS:
        db.add(Student(class_id=cls.id, first_name=first, last_name=last,
                       llm_pseudonym=new_pseudonym()))


# ---------------------------------------------------------------- templates

# Templates de documents (§5) éditables dans Paramètres → Documents :
# en-tête, carte exercice et rappel de leçon. Seuls les paramètres visuels
# sont exposés — la géométrie des marqueurs (QR/fiduciels) reste FIGÉE.
DEFAULT_TEMPLATES: dict = {
    "header": {
        "name_size": 14,        # nom de l'élève (pt)
        "class_size": 10,       # ligne "Classe …"
        "title_size": 8,        # titre du sujet
        "accent": "#37474F",    # filet séparateur + titre
        "show_date": True,
    },
    "exercise": {
        "font_size": 9,         # texte de l'énoncé
        "title_size": 9,        # "Exercice N"
        "math_size": 12,        # expression mathématique centrée
        "border": "#C7CDD4",    # cadre de la carte
        "accent": "#455A64",    # icône + pastilles de difficulté
        "radius": 2.2,          # rayon des coins (mm)
        "shadow": True,
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

"""Amorçage : année scolaire, grilles de compétences officielles (extraites
des programmes cycles 3 et 4 — voir scripts/extract_competencies.py),
catalogue d'exercices et classe mock de 5 élèves imaginaires (désactivable
dans Réglages, §9.4).

Le premier compte administrateur n'est PAS créé ici : c'est l'écran de
démarrage (routers/setup.py, tant qu'aucun User n'existe) qui s'en charge,
avec les identifiants choisis par l'enseignant."""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Competency, CompetencyFramework, ExerciseCatalog, ExerciseCompetency,
    SchoolClass, SchoolYear, Student,
)
from .services.exercises import GENERATORS
from .services.security import new_pseudonym

COMPETENCIES_JSON = Path(__file__).resolve().parent / "data" / "competencies_fr.json"

MOCK_STUDENTS = [
    ("Martin", "Léa"), ("Dubois", "Noah"), ("Bernard", "Chloé"),
    ("Petit", "Adam"), ("Robert", "Inès"),
]

# Rattachement des exercices builtin aux compétences officielles, par recherche
# de mots-clés dans les libellés (robuste à une ré-extraction du programme).
EXERCISE_COMPETENCY_KEYWORDS: dict[str, tuple[str, list[str]]] = {
    "builtin:add_relatifs": ("5e", ["additionner", "relatifs"]),
    "builtin:mult_relatifs": ("4e", ["multiplier", "relatifs"]),
    "builtin:frac_somme": ("5e", ["additionner", "fractions"]),
    "builtin:eq_1d": ("5e", ["équation", "premier degré"]),
    "builtin:qcm_priorites": ("5e", ["priorités opératoires"]),
    "builtin:qcm_proportion": ("5e", ["situations de proportionnalité"]),
    "builtin:developpement": ("5e", ["distributivité"]),
}


def seed_frameworks(db: Session) -> dict[str, list[Competency]]:
    """Charge les grilles officielles. Retourne les compétences par grade."""
    data = json.loads(COMPETENCIES_JSON.read_text(encoding="utf-8"))
    by_grade: dict[str, list[Competency]] = {}
    for fw_data in data["frameworks"]:
        fw = CompetencyFramework(
            grade_level=fw_data["grade_level"],
            name=fw_data["name"], version=fw_data["version"],
            status="published", source="programme_officiel")
        db.add(fw)
        db.flush()
        order = 0
        comps = []
        for dom in fw_data["domains"]:
            for theme in dom["themes"]:
                for c in theme["competencies"]:
                    comp = Competency(
                        framework_id=fw.id, code=c["code"], label=c["label"],
                        order_index=order,
                        domain_code=dom["code"], domain_name=dom["name"],
                        theme_code=theme["code"], theme_name=theme["name"])
                    db.add(comp)
                    comps.append(comp)
                    order += 1
        db.flush()
        by_grade[fw_data["grade_level"]] = comps
    return by_grade


def _find_competency(comps: list[Competency], keywords: list[str]) -> Competency | None:
    for c in comps:
        label = c.label.lower()
        if all(k.lower() in label for k in keywords):
            return c
    # repli : premier mot-clé seulement
    for c in comps:
        if keywords and keywords[0].lower() in c.label.lower():
            return c
    return None


def seed_exercises(db: Session, by_grade: dict[str, list[Competency]]):
    for ref, (title, _fn, rtype, _legacy) in GENERATORS.items():
        diff = {"builtin:add_relatifs": 3, "builtin:mult_relatifs": 4,
                "builtin:frac_somme": 6, "builtin:eq_1d": 7,
                "builtin:qcm_priorites": 3, "builtin:qcm_proportion": 5,
                "builtin:developpement": 6}.get(ref, 5)
        grade, keywords = EXERCISE_COMPETENCY_KEYWORDS.get(ref, ("5e", []))
        ex = ExerciseCatalog(provider="builtin", provider_ref=ref, title=title,
                             grade_level=grade, difficulty=diff, response_type=rtype,
                             automation_tier="auto" if rtype != "multiline_text" else "auto_with_llm")
        db.add(ex)
        db.flush()
        comp = _find_competency(by_grade.get(grade, []), keywords)
        if comp:
            db.add(ExerciseCompetency(exercise_id=ex.id, competency_id=comp.id))


def seed(db: Session):
    if db.query(CompetencyFramework).first():
        return  # contenu déjà amorcé (indépendant de la création du 1er compte)

    year = SchoolYear(label="2026-2027", active=True)
    db.add(year)
    db.flush()

    by_grade = seed_frameworks(db)
    seed_exercises(db, by_grade)

    if settings.mock_mode:
        cls = SchoolClass(school_year_id=year.id, name="5e Mock", grade_level="5e",
                          is_mock=True)
        db.add(cls)
        db.flush()
        for last, first in MOCK_STUDENTS:
            db.add(Student(class_id=cls.id, first_name=first, last_name=last,
                           llm_pseudonym=new_pseudonym()))
    db.commit()

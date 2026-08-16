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

from .models import (
    Competency, CompetencyEvidence, CompetencyFramework, CompetencyStateHistory,
    ExerciseCatalog, ExerciseCompetency, GeneratedExercise,
    SchoolYear, StudentCompetencyState,
)
from .services.exercises import GENERATORS

COMPETENCIES_JSON = Path(__file__).resolve().parent / "data" / "competencies_fr.json"

# Rattachement des exercices builtin aux compétences officielles, par recherche
# de mots-clés dans les libellés (robuste à une ré-extraction du programme).
EXERCISE_COMPETENCY_KEYWORDS: dict[str, tuple[str, list[str]]] = {
    "builtin:add_relatifs": ("5e", ["additionner", "relatifs"]),
    "builtin:mult_relatifs": ("4e", ["multiplier", "relatifs"]),
    "builtin:frac_somme": ("5e", ["additionner", "fractions"]),
    "builtin:eq_1d": ("5e", ["résoudre", "équation"]),
    "builtin:qcm_priorites": ("5e", ["prioriser", "opérations"]),
    "builtin:qcm_proportion": ("5e", ["identifier", "situations"]),
    "builtin:developpement": ("5e", ["distributivité"]),
}


def _seed_framework(db: Session, fw_data: dict) -> list[Competency]:
    """Crée un référentiel (framework + compétences) depuis un bloc du JSON.
    Retourne les compétences créées, dans l'ordre du sommaire."""
    fw = CompetencyFramework(
        grade_level=fw_data["grade_level"], cycle=fw_data.get("cycle"),
        program_year=fw_data.get("program_year"),
        name=fw_data["name"], version=fw_data["version"],
        status="published", source="programme_officiel")
    db.add(fw)
    db.flush()
    order = 0
    comps = []
    for dom in fw_data["domains"]:
        for chap in dom["chapters"]:
            for c in chap["competencies"]:
                comp = Competency(
                    framework_id=fw.id, code=c["code"],
                    short_id=c.get("short_id", ""), label=c["label"],
                    order_index=order,
                    domain_code=dom["code"], domain_name=dom["name"],
                    chapter_code=chap["code"], chapter_name=chap["name"])
                db.add(comp)
                comps.append(comp)
                order += 1
    db.flush()
    return comps


def seed_frameworks(db: Session) -> dict[str, list[Competency]]:
    """Charge les grilles officielles. Retourne les compétences par grade."""
    data = json.loads(COMPETENCIES_JSON.read_text(encoding="utf-8"))
    return {fw_data["grade_level"]: _seed_framework(db, fw_data)
            for fw_data in data["frameworks"]}


CAHIER_VERSION = "2026-cahier"
# Niveaux refondus sur le sommaire du cahier (hiérarchie domaine > chapitre >
# compétence, IDs courts type A1.1). Les autres niveaux gardent l'ancien
# modèle (objectifs fins) en attendant leur propre refonte.
CAHIER_GRADES = ("5e", "3e")

# rétro-compat : d'anciens imports/commentaires référencent ces noms.
NEW_5E_VERSION = CAHIER_VERSION


def _purge_frameworks(db: Session, frameworks: list[CompetencyFramework]):
    """Supprime des référentiels et toutes les lignes qui en dépendent
    (liens exercices, preuves, états élèves, historiques, exercices générés,
    exercices générés)."""
    old_ids = [f.id for f in frameworks]
    comp_ids = [c.id for c in
                db.query(Competency.id).filter(Competency.framework_id.in_(old_ids))]
    if comp_ids:
        for model in (ExerciseCompetency, CompetencyEvidence, StudentCompetencyState,
                      CompetencyStateHistory, GeneratedExercise):
            db.query(model).filter(
                model.competency_id.in_(comp_ids)).delete(synchronize_session=False)
        db.query(Competency).filter(Competency.id.in_(comp_ids)).delete(synchronize_session=False)
    db.query(CompetencyFramework).filter(CompetencyFramework.id.in_(old_ids)).delete(
        synchronize_session=False)
    db.flush()


def migrate_cahier_frameworks(db: Session):
    """Purge les anciens référentiels des niveaux refondus sur le sommaire du
    cahier (cf. CAHIER_GRADES) et les recharge depuis le JSON (hiérarchie
    domaine > chapitre > compétence, IDs courts type A1.1). Ne touche pas aux
    autres niveaux. Idempotent : pour chaque niveau, ne fait rien une fois la
    version en base alignée sur `CAHIER_VERSION`."""
    data = None
    for grade in CAHIER_GRADES:
        old = (db.query(CompetencyFramework)
               .filter(CompetencyFramework.grade_level == grade,
                       CompetencyFramework.version != CAHIER_VERSION).all())
        if not old:
            continue
        _purge_frameworks(db, old)
        if data is None:
            data = json.loads(COMPETENCIES_JSON.read_text(encoding="utf-8"))
        fw_data = next(f for f in data["frameworks"] if f["grade_level"] == grade)
        _seed_framework(db, fw_data)
        db.commit()


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
    migrate_cahier_frameworks(db)
    if db.query(CompetencyFramework).first():
        return  # contenu déjà amorcé (indépendant de la création du 1er compte)

    year = SchoolYear(label="2026-2027", active=True)
    db.add(year)
    db.flush()

    by_grade = seed_frameworks(db)
    seed_exercises(db, by_grade)
    db.commit()

"""Référentiels ivoiriens de mathématiques (6e → Terminale, séries A, A1, A2, C, D).

Source : programmes éducatifs DPFC (version CND 09/2023) et progressions
annuelles 2024-2025, extraits dans `data/competencies_ci.json` par
docs/programmes/ci/outils/build.py (relecture : docs/programmes/ci/).

Une compétence = un contenu officiel, précédé du verbe de son habileté (le
verbe se répète d'une ligne à l'autre), dans l'ordre du programme. Son `code`
est un identifiant stable et lisible :

    CI-2023-MATH-TLE-C-B4.2
    pays, année du programme, matière, niveau, série (lycée), domaine B
    (thème Fonctions), 4e leçon du domaine, 2e contenu de la leçon.

`short_id` (B4.2) est le code affiché, comme pour les référentiels français.

Les référentiels existent déjà, vides, depuis `education.seed_structure` :
l'import les remplit en place, sans changer leur identifiant. Il est
idempotent et ne refait rien tant que la version en base est la bonne. Une
nouvelle version met à jour les compétences par `code` (leurs identifiants,
donc les exercices et le suivi des élèves, sont conservés) ; une compétence
disparue du programme n'est supprimée que si rien ne la référence.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import (
    Competency, CompetencyEvidence, CompetencyFramework, CompetencyStateHistory,
    CopyItemResult, ExerciseCompetency, GeneratedExercise, IndigoExercise, Program,
    StudentCompetencyState, StudentLearningEvent,
)

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[1] / "data" / "competencies_ci.json"
PROGRAM_ID = "ci"

# Tables qui pointent vers une compétence : une compétence référencée n'est
# jamais supprimée par une mise à jour du programme.
_REFERENCING = (ExerciseCompetency, CompetencyEvidence, StudentCompetencyState,
                CompetencyStateHistory, GeneratedExercise, IndigoExercise,
                CopyItemResult, StudentLearningEvent)


def load_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _rows(fw_data: dict) -> list[dict]:
    """Compétences du référentiel dans l'ordre du programme, avec leur domaine
    et leur chapitre, prêtes à écrire en base."""
    rows = []
    for dom in fw_data["domains"]:
        for chap in dom["chapters"]:
            for c in chap["competencies"]:
                rows.append({
                    "code": c["code"], "short_id": c["short_id"], "label": c["label"],
                    "description": "",
                    "domain_code": dom["code"], "domain_name": dom["name"],
                    "chapter_code": chap["code"], "chapter_name": chap["name"],
                    "chapter_hours": float(chap["hours"]), "chapter_rank": chap["progression_rank"],
                    "chapter_week": chap["progression_week"],
                    "recommended_minutes": c["recommended_minutes"], "teaching_order": c["teaching_order"],
                    "section": "common",
                    "merge_json": c.get("merge"),
                })
    for i, row in enumerate(rows):
        row["order_index"] = i
    return rows


def _referenced(db: Session, competency_id: str) -> bool:
    return any(db.query(model.id if hasattr(model, "id") else model.competency_id)
               .filter(model.competency_id == competency_id).first()
               for model in _REFERENCING)


def _framework(db: Session, data: dict, fw_data: dict) -> CompetencyFramework:
    fw = (db.query(CompetencyFramework)
          .filter_by(program_id=PROGRAM_ID, subject_id=data["subject_id"],
                     grade_level=fw_data["grade_level"], path_id=fw_data["path_id"])
          .filter(CompetencyFramework.status != "archived")
          .order_by(CompetencyFramework.program_year.desc(), CompetencyFramework.id).first())
    if fw is None:
        fw = CompetencyFramework(program_id=PROGRAM_ID, subject_id=data["subject_id"],
                                 grade_level=fw_data["grade_level"], path_id=fw_data["path_id"],
                                 name=fw_data["name"])
        db.add(fw)
        db.flush()
    return fw


def seed_ci_curriculum(db: Session, data: dict | None = None) -> int:
    """Remplit ou met à jour les référentiels ivoiriens. Retourne le nombre de
    référentiels modifiés (0 quand tout est déjà à jour)."""
    if not db.get(Program, PROGRAM_ID):
        return 0  # structure pas encore amorcée (seed_structure)
    data = data or load_data()
    changed = 0
    for fw_data in data["frameworks"]:
        fw = _framework(db, data, fw_data)
        rows = _rows(fw_data)
        existing = {c.code: c for c in db.query(Competency).filter_by(framework_id=fw.id)}
        up_to_date = (fw.version == data["version"] and fw.status == "published"
                      and all(r["code"] in existing
                              and all(getattr(existing[r["code"]], k) == v for k, v in r.items())
                              for r in rows))
        if up_to_date:
            continue
        for row in rows:
            comp = existing.pop(row["code"], None)
            if comp is None:
                comp = Competency(framework_id=fw.id, **row)
                db.add(comp)
            else:
                for key, value in row.items():
                    setattr(comp, key, value)
        for code, comp in existing.items():  # absentes de la nouvelle version
            if _referenced(db, comp.id):
                logger.warning("Compétence %s absente du programme %s mais référencée : conservée",
                               code, data["version"])
            else:
                db.delete(comp)
        fw.name = fw_data["name"]
        fw.version = data["version"]
        fw.program_year = data["program_year"]
        fw.cycle = fw_data["cycle"]
        fw.status = "published"
        fw.source = "programme_officiel"
        changed += 1
    db.commit()
    return changed

"""Banque de contenus (exercices générés & rappels de leçon) — onglet Banque.

La banque grandit à la demande (compétence × niveau réellement utilisés) ;
cet onglet donne la visibilité et le contrôle : couverture, aperçu fidèle
(mêmes formules qu'à l'impression), retrait d'un contenu douteux,
regénération ciblée. Sert aussi le rendu PNG des figures paramétrées.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role
from ..models import (
    Competency, CompetencyFramework, GeneratedExercise, LessonSnippet,
    SesamathsChapterExtraction, SesamathsLlmCache,
)
from ..services import exercise_gen, figures

router = APIRouter(prefix="/api/content", tags=["content"],
                   dependencies=[Depends(current_user)])


def _exercise_out(ex: GeneratedExercise, comp: Competency | None) -> dict:
    return {
        "id": ex.id, "competency_id": ex.competency_id,
        "competency_code": comp.code if comp else "",
        "competency_short_id": comp.short_id if comp else "",
        "competency_label": comp.label if comp else "",
        "chapter_name": comp.chapter_name if comp else "",
        "level": ex.difficulty_level, "variant": ex.variant,
        "statement": ex.statement, "correction": ex.correction,
        "response_type": ex.response_type,
        "choices": (ex.grading_json or {}).get("choices", []),
        "expected": ex.expected_json,
        "source": ex.source or "deepseek", "kind": ex.kind or "application",
        "quality": ex.quality_json or {},
        "figure": ex.figure_json,
        "status": ex.status,
        "created_at": ex.created_at.isoformat() if ex.created_at else None,
        # blocs OCR Mistral bruts (title/text/table/image/...) dont provient
        # cette ligne, source="sesamaths" uniquement — affichage "avant/après"
        # en banque
        "raw": ex.raw_extract_json,
    }


def _lesson_out(sn: LessonSnippet, comp: Competency | None) -> dict:
    return {
        "id": sn.id, "competency_id": sn.competency_id,
        "competency_code": comp.code if comp else "",
        "competency_short_id": comp.short_id if comp else "",
        "competency_label": comp.label if comp else "",
        "chapter_name": comp.chapter_name if comp else "",
        "level_min": sn.level_min, "level_max": sn.level_max,
        "title": sn.title, "blocks": sn.blocks_json or None,
        "content": sn.content_latex, "example": sn.example_latex,
        "figure": sn.figure_json, "validated": sn.validated,
        "status": sn.status,
    }


@router.get("/summary")
def summary(grade_level: str | None = None, db: Session = Depends(get_db)):
    """Couverture de la banque par compétence : nb d'exercices actifs par
    niveau 1-5 et présence des rappels (1-3 / 4-5)."""
    comp_q = (db.query(Competency, CompetencyFramework.grade_level)
              .join(CompetencyFramework,
                    Competency.framework_id == CompetencyFramework.id))
    if grade_level:
        comp_q = comp_q.filter(CompetencyFramework.grade_level == grade_level)
    comps = comp_q.all()

    ex_counts = dict()
    for cid, lvl, n in (db.query(GeneratedExercise.competency_id,
                                 GeneratedExercise.difficulty_level,
                                 func.count())
                        .filter(GeneratedExercise.status == "active")
                        .group_by(GeneratedExercise.competency_id,
                                  GeneratedExercise.difficulty_level)):
        ex_counts.setdefault(cid, {})[lvl] = n
    lessons = dict()
    for sn in db.query(LessonSnippet).filter(LessonSnippet.status == "active"):
        lessons.setdefault(sn.competency_id, []).append(
            {"level_min": sn.level_min, "level_max": sn.level_max,
             "validated": sn.validated})

    out = []
    for comp, grade in comps:
        by_level = ex_counts.get(comp.id, {})
        if not by_level and comp.id not in lessons:
            continue  # la banque s'agrandit à la demande : n'afficher que l'existant
        out.append({
            "competency_id": comp.id, "code": comp.code, "short_id": comp.short_id,
            "label": comp.label,
            "grade_level": grade, "domain_name": comp.domain_name,
            "chapter_name": comp.chapter_name,
            "by_level": {str(l): by_level.get(l, 0) for l in range(1, 6)},
            "total": sum(by_level.values()),
            "lessons": lessons.get(comp.id, []),
        })
    out.sort(key=lambda r: (r["grade_level"], r["code"]))
    return out


@router.get("/exercises")
def list_exercises(competency_id: str, level: int | None = None,
                   include_retired: bool = False,
                   db: Session = Depends(get_db)):
    comp = db.get(Competency, competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    q = db.query(GeneratedExercise).filter_by(competency_id=competency_id)
    if not include_retired:
        q = q.filter_by(status="active")
    if level:
        q = q.filter_by(difficulty_level=level)
    rows = q.order_by(GeneratedExercise.difficulty_level,
                      GeneratedExercise.variant).all()
    return [_exercise_out(ex, comp) for ex in rows]


@router.get("/lessons")
def list_lessons(competency_id: str | None = None, grade_level: str | None = None,
                 db: Session = Depends(get_db)):
    q = db.query(LessonSnippet, Competency).join(
        Competency, LessonSnippet.competency_id == Competency.id)
    if competency_id:
        q = q.filter(LessonSnippet.competency_id == competency_id)
    if grade_level:
        q = (q.join(CompetencyFramework,
                    Competency.framework_id == CompetencyFramework.id)
             .filter(CompetencyFramework.grade_level == grade_level))
    q = q.filter(LessonSnippet.status == "active")
    return [_lesson_out(sn, comp) for sn, comp in q.all()]


class GenerateIn(BaseModel):
    competency_id: str
    level: int  # 1-5


@router.post("/exercises/generate",
             dependencies=[Depends(require_role("admin", "teacher"))])
def generate_exercises(body: GenerateIn, db: Session = Depends(get_db)):
    """Complète la banque pour (compétence, niveau) jusqu'au minimum configuré."""
    comp = db.get(Competency, body.competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    try:
        rows = exercise_gen.ensure_bank(db, comp, max(1, min(5, body.level)))
    except Exception as e:
        db.commit()  # conserver ce qui a éventuellement été produit
        raise HTTPException(502, f"Génération impossible : {e}")
    db.commit()
    return {"count": len(rows), "exercises": [_exercise_out(ex, comp) for ex in rows]}


@router.post("/lessons/generate",
             dependencies=[Depends(require_role("admin", "teacher"))])
def generate_lesson(body: GenerateIn, db: Session = Depends(get_db)):
    comp = db.get(Competency, body.competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    try:
        sn = exercise_gen.ensure_lesson(db, comp, max(1, min(5, body.level)))
    except Exception as e:
        raise HTTPException(502, f"Génération impossible : {e}")
    db.commit()
    return _lesson_out(sn, comp)


@router.post("/exercises/{exercise_id}/retire",
             dependencies=[Depends(require_role("admin", "teacher"))])
def retire_exercise(exercise_id: str, db: Session = Depends(get_db)):
    """Retire un exercice de la banque (il ne sera plus jamais servi) ;
    la prochaine génération le remplacera automatiquement."""
    ex = db.get(GeneratedExercise, exercise_id)
    if not ex:
        raise HTTPException(404, "Exercice inconnu")
    ex.status = "retired"
    db.commit()
    return {"id": ex.id, "status": ex.status}


@router.post("/lessons/{lesson_id}/retire",
             dependencies=[Depends(require_role("admin", "teacher"))])
def retire_lesson(lesson_id: str, db: Session = Depends(get_db)):
    sn = db.get(LessonSnippet, lesson_id)
    if not sn:
        raise HTTPException(404, "Rappel inconnu")
    sn.status = "retired"
    db.commit()
    return {"id": sn.id, "status": sn.status}


@router.post("/bank/purge", dependencies=[Depends(require_role("admin"))])
def purge_bank(db: Session = Depends(get_db)):
    """Vide ENTIÈREMENT la banque d'exercices (toutes sources confondues) ET
    l'état d'extraction Sésamaths — action irréversible, réservée à l'admin
    (plus strict que le reste de /api/content : globale, pas ciblée à une
    compétence). Purger seulement GeneratedExercise ne suffirait pas : le
    pool mis en cache par Série (SesamathsChapterExtraction.validated_json)
    resservirait le même contenu à la prochaine génération sans jamais
    ré-extraire — cause identifiée des exercices qui "reviennent" malgré un
    retrait. Ne touche pas aux rappels de leçon (hors périmètre)."""
    n_exercises = db.query(GeneratedExercise).delete(synchronize_session=False)
    n_extractions = db.query(SesamathsChapterExtraction).delete(synchronize_session=False)
    n_cache = db.query(SesamathsLlmCache).delete(synchronize_session=False)
    db.commit()
    return {"exercises_deleted": n_exercises, "extractions_reset": n_extractions,
            "cache_cleared": n_cache}


# ------------------------------------------------------------------- figures

class FigureIn(BaseModel):
    figure_json: dict


@router.post("/figures/render")
def render_figure(body: FigureIn):
    """PNG d'une figure paramétrée (aperçu web identique à l'impression)."""
    norm = figures.validate_figure(body.figure_json)
    if norm is None:
        raise HTTPException(422, "Figure invalide")
    return Response(figures.render_figure(norm), media_type="image/png")


@router.get("/exercises/{exercise_id}/figure.png")
def exercise_figure(exercise_id: str, db: Session = Depends(get_db)):
    ex = db.get(GeneratedExercise, exercise_id)
    if not ex or not ex.figure_json:
        raise HTTPException(404, "Pas de figure")
    return Response(figures.render_figure(ex.figure_json), media_type="image/png")

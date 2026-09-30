"""Banque d'exercices — onglet Banque.

La banque grandit à la demande (compétence × niveau réellement utilisés) ;
cet onglet donne la visibilité et le contrôle : couverture, aperçu fidèle
(mêmes formules qu'à l'impression), retrait d'un contenu douteux,
regénération ciblée. Sert aussi le rendu PNG des figures paramétrées.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role
from ..models import (
    Competency, CompetencyFramework, GeneratedExercise,
    SesamathsChapterExtraction, SesamathsLlmCache,
)
from ..services import exercise_gen, figures, scoring

router = APIRouter(prefix="/api/content", tags=["content"],
                   dependencies=[Depends(current_user)])


def _exercise_out(ex: GeneratedExercise, comp: Competency | None) -> dict:
    from ..services import generation
    grading = ex.grading_json or {}
    indigo = (ex.raw_extract_json or {}).get("indigo") or {}
    display_statement, calculator, is_problem = generation.indigo_display(ex)
    return {
        "id": ex.id, "competency_id": ex.competency_id,
        "competency_code": comp.code if comp else "",
        "competency_short_id": comp.short_id if comp else "",
        "competency_label": comp.label if comp else "",
        "chapter_name": comp.chapter_name if comp else "",
        "level": ex.difficulty_level, "variant": ex.variant,
        "statement": display_statement, "correction": ex.correction,
        "response_type": ex.response_type,
        "choices": grading.get("choices", []),
        "expected": ex.expected_json or {},
        # Contrat complet du rendu feuille. Ces valeurs ne sont pas recalculées
        # dans le navigateur : ce sont exactement celles que pdfgen consomme.
        "grading": grading,
        "row_labels": grading.get("row_labels"),
        "col_labels": grading.get("col_labels"),
        "lines": grading.get("lines"),
        "source": ex.source or "deepseek", "kind": ex.kind or "application",
        "calculator": calculator, "is_problem": is_problem,
        # barème d'EFFORT (ce que l'exercice vaut, cf. services.scoring) : il
        # vit dans grading_json, avec un repli déterministe quand le LLM n'en a
        # pas produit — la banque doit l'afficher comme l'onglet Exercices.
        "bareme_points": scoring.item_bareme(ex.grading_json or {}, ex.response_type),
        "quality": ex.quality_json or {},
        "figure": ex.figure_json,
        "figure_url": (f"/api/content/exercises/{ex.id}/figure.png"
                       if ex.figure_json else None),
        # Dans la banque publiée Indigo, `correction` est le guide élève et la
        # vraie solution reste dans les métadonnées versionnées. Les autres
        # sources historiques n'ont qu'un corrigé : on l'expose comme solution.
        "correction_guide": (ex.correction
                             if ex.source in ("indigo", "gemini") else ""),
        "correction_solution": (indigo.get("correction_solution", "")
                                if ex.source == "indigo"
                                else "" if ex.source == "gemini"
                                else ex.correction),
        "status": ex.status,
        "created_at": ex.created_at.isoformat() if ex.created_at else None,
        # blocs OCR Mistral bruts (title/text/table/image/...) dont provient
        # cette ligne, source="sesamaths" uniquement — affichage "avant/après"
        # en banque
        "raw": ex.raw_extract_json,
    }


@router.get("/summary")
def summary(grade_level: str | None = None, db: Session = Depends(get_db)):
    """Couverture de la banque par compétence : nb d'exercices actifs par
    niveau 1-5, nb de "problèmes" (kind=probleme, badge probleme/énigme
    confondus). Renvoie aussi domaine/chapitre (codes ET libellés, H1/H2)
    pour que le front puisse regrouper la
    table par domaine > chapitre > compétence (H1/H2/H3)."""
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
                        .filter(GeneratedExercise.status == "active", GeneratedExercise.kind != "probleme")
                        .group_by(GeneratedExercise.competency_id,
                                  GeneratedExercise.difficulty_level)):
        ex_counts.setdefault(cid, {})[lvl] = n
    # les "problèmes" (kind=probleme, regroupe badge probleme+énigme) mobilisent
    # souvent plusieurs compétences d'un même chapitre à la fois : comptés par
    # compétence ici, mais affichés groupés au niveau du chapitre (H2) côté
    # front (zone fusionnée) plutôt que répétés/éclatés ligne par ligne.
    prob_counts = dict(
        db.query(GeneratedExercise.competency_id, func.count())
        .filter(GeneratedExercise.status == "active", GeneratedExercise.kind == "probleme")
        .group_by(GeneratedExercise.competency_id).all())
    out = []
    for comp, grade in comps:
        by_level = ex_counts.get(comp.id, {})
        out.append({
            "competency_id": comp.id, "code": comp.code, "short_id": comp.short_id,
            "label": comp.label, "order_index": comp.order_index,
            "grade_level": grade,
            "domain_code": comp.domain_code, "domain_name": comp.domain_name,
            "chapter_code": comp.chapter_code, "chapter_name": comp.chapter_name,
            "by_level": {str(l): by_level.get(l, 0) for l in range(1, 6)},
            "total": sum(by_level.values()),
            "problems": prob_counts.get(comp.id, 0),
        })
    out.sort(key=lambda r: (r["grade_level"], r["code"]))
    return out


@router.get("/exercises")
def list_exercises(competency_id: str | None = None, level: int | None = None,
                   include_retired: bool = False, chapter_id: str | None = None,
                   category: str | None = None,
                   db: Session = Depends(get_db)):
    comp = db.get(Competency, chapter_id or competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    q = db.query(GeneratedExercise)
    if chapter_id:
        ids = [c.id for c in db.query(Competency).filter_by(
            framework_id=comp.framework_id, chapter_code=comp.chapter_code,
            domain_code=comp.domain_code).all()]
        q = q.filter(GeneratedExercise.competency_id.in_(ids), GeneratedExercise.kind == "probleme")
    else:
        q = q.filter_by(competency_id=competency_id)
        if category == "exercise":
            q = q.filter(GeneratedExercise.kind != "probleme")
    if not include_retired:
        q = q.filter_by(status="active")
    if level:
        q = q.filter_by(difficulty_level=level)
    rows = q.order_by(GeneratedExercise.difficulty_level,
                      GeneratedExercise.variant).all()
    return [_exercise_out(ex, db.get(Competency, ex.competency_id)) for ex in rows]


@router.get("/sesamaths/raw")
def sesamaths_raw(competency_id: str, db: Session = Depends(get_db)):
    """État + pages OCR brutes (Mistral) de la Série d'une compétence — onglet
    diagnostic « Sésamaths » de la banque, pour vérifier ce que l'OCR a
    vraiment lu AVANT de regarder ce que l'adaptateur en a fait. Lecture
    seule : ne déclenche jamais d'extraction (cf. services.sesamaths.
    extraction_state) ; utiliser « Compléter la banque » pour ça."""
    comp = db.get(Competency, competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    from ..services import sesamaths
    return sesamaths.extraction_state(db, comp)


class GenerateExercisesIn(BaseModel):
    competency_id: str
    level: int  # 1-5
    # même choix que dans l'assistant sujet (cf. assessments.AssessmentPatch) :
    # extraction du manuel ou création Gemini — les deux pools sont séparés
    source: Literal["sesamaths", "gemini"] = "sesamaths"


@router.post("/exercises/generate",
             dependencies=[Depends(require_role("admin", "teacher"))])
def generate_exercises(body: GenerateExercisesIn, db: Session = Depends(get_db)):
    """Complète la banque pour (compétence, niveau) à partir de la source
    demandée : extraction Sésamaths (tout ce que la Série du manuel contient)
    ou création Gemini (jusqu'à settings.gemini_bank_target)."""
    comp = db.get(Competency, body.competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    try:
        rows = exercise_gen.ensure_bank(db, comp, max(1, min(5, body.level)),
                                        source=body.source)
    except Exception as e:
        db.commit()  # conserver ce qui a éventuellement été produit
        raise HTTPException(502, f"Génération impossible : {e}")
    db.commit()
    return {"count": len(rows), "exercises": [_exercise_out(ex, comp) for ex in rows]}


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


@router.post("/bank/purge", dependencies=[Depends(require_role("admin"))])
def purge_bank(db: Session = Depends(get_db)):
    """Vide ENTIÈREMENT la banque d'exercices (toutes sources confondues) ET
    l'état d'extraction Sésamaths — action irréversible, réservée à l'admin
    (plus strict que le reste de /api/content : globale, pas ciblée à une
    compétence). Purger seulement GeneratedExercise ne suffirait pas : le
    pool mis en cache par Série (SesamathsChapterExtraction.validated_json)
    resservirait le même contenu à la prochaine génération sans jamais
    ré-extraire — cause identifiée des exercices qui "reviennent" malgré un
    retrait."""
    n_exercises = db.query(GeneratedExercise).delete(synchronize_session=False)
    n_extractions = db.query(SesamathsChapterExtraction).delete(synchronize_session=False)
    n_cache = db.query(SesamathsLlmCache).delete(synchronize_session=False)
    db.commit()
    return {"exercises_deleted": n_exercises, "extractions_reset": n_extractions,
            "cache_cleared": n_cache}


# ------------------------------------------------------------------- figures

class CardPreviewIn(BaseModel):
    exercise: dict
    guides: bool = True
    show_answers: bool = False


@router.post("/card-preview.png")
def card_preview(body: CardPreviewIn, db: Session = Depends(get_db)):
    """Même carte et mêmes mesures que le sujet, avec corrections facultatives."""
    import json
    import re
    from pathlib import Path
    from ..config import settings
    from ..models import IndigoExercise
    from ..services import card_preview as preview, indigo
    ex = dict(body.exercise)
    if len(json.dumps(ex)) > 80000 or ex.get("response_type") not in exercise_gen.VALID_RESPONSE_TYPES:
        raise HTTPException(422, "Carte invalide")
    url = ex.get("figure_url") or ""
    if url:
        match = re.fullmatch(r"/api/(indigo|content)/exercises/([\w-]+)/figure\.png(?:\?v=\d+)?", url)
        if not match:
            raise HTTPException(422, "Référence de figure invalide")
        if match[1] == "indigo":
            row = db.get(IndigoExercise, match[2])
            ex["figure"] = ({"type": "image", "params": {"path": str(indigo.crop_abs_path(row.figure_path))}}
                            if row and row.figure_path else None)
        else:
            row = db.get(GeneratedExercise, match[2])
            ex["figure"] = row.figure_json if row else None

    def check_figures(value):
        if isinstance(value, dict):
            if value.get("type") == "image":
                p = Path((value.get("params") or {}).get("path", "")).resolve()
                roots = (Path(settings.data_dir).resolve(), Path(__file__).resolve().parents[1] / "data")
                if not any(p.is_relative_to(root) for root in roots):
                    raise HTTPException(422, "Figure hors du dossier de données")
            for child in value.values():
                check_figures(child)
        elif isinstance(value, list):
            for child in value:
                check_figures(child)
    check_figures(ex)
    try:
        png = preview.render(ex, body.guides, body.show_answers)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(422, "Impossible de rendre cette carte") from exc
    return Response(png, media_type="image/png")

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

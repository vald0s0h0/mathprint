"""Assistant sujets (§3.1) : création, tableau de compétences, adaptation,
génération en file de fond, fichiers."""
import copy as copylib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import (
    Assessment, Competency, CompetencyFramework, Copy, ExerciseCatalog,
    GeneratedExercise, ScanBatch, SchoolClass, Student,
    StudentCompetencyState, ClassTeaching, TeachingGroup, AuditLog, User,
)
from ..services import (
    data_admin, exercise_gen, generation, job_worker, manual_subject, scoring,
    student_history,
)
from ..services.forgetting import due_competencies
from .misc import build_competency_tree
from ..services.education import framework_for, assert_service_active, assessment_students

router = APIRouter(prefix="/api/assessments", tags=["assessments"],
                   dependencies=[Depends(current_user)])


class AssessmentIn(BaseModel):
    subject_id: str = "math"
    group_id: str | None = None
    class_id: str
    type: str = "training"          # control | training
    title: str
    pages: int = 1                  # 1 = recto, 2 = recto/verso, etc.
    # base de scoring de tout sujet ; un entraînement ne l'imprime pas.
    note_base: int = scoring.DEFAULT_NOTE_BASE


class AssessmentPatch(BaseModel):
    title: str | None = None
    pages: int | None = None
    note_base: int | None = None
    personalization_mode: Literal["common", "common_variants", "individual"] | None = None
    competency_ids: list[str] | None = None
    # source des exercices : "sesamaths" = extraction du manuel, "gemini" =
    # création par LLM (services.gemini_gen), "indigo" = manuel 3e publié en
    # dur (services.indigo). "auto" est une valeur héritée, sans pipeline
    # derrière depuis le 16/07.
    exercise_source: Literal["auto", "sesamaths", "gemini", "indigo"] | None = None
    # guides (encadrés intégrés aux énoncés) : inclus ou retirés pour tout le sujet
    guides: Literal["include", "none"] | None = None


class GenerateIn(BaseModel):
    font_size: int = 10


# ------------------------------------------------- assistant « Créer un sujet » (mode manuel)

class PlacedItem(BaseModel):
    exercise_id: str
    page: int = 0            # index de page, 0 = 1re page
    col: int = 0             # 0 = colonne gauche, 1 = colonne droite
    rank: int = 0            # rang dans la colonne, de haut en bas


class VariantIn(BaseModel):
    key: str                 # "A"/"B"/… (anti-triche) ou facile|moyen|difficile
    label: str = ""
    items: list[PlacedItem] = []


class AutoPlanIn(BaseModel):
    """Création AUTOMATIQUE : le professeur fixe le périmètre, la plateforme
    compose une copie personnalisée par élève (services.student_history)."""
    competency_ids: list[str]
    # réglage du dégradé selon le niveau (student_history.guide_ratio)
    guides_reach_pct: int = student_history.DEFAULT_GUIDE_REACH
    # « auto » : selon le niveau ; « include » : pour tous les élèves
    guides: Literal["auto", "include", "none"] = "auto"
    # problèmes des chapitres touchés, dosés selon le niveau (7+ seulement)
    problems: bool = False


class ManualPlanIn(BaseModel):
    """Plan complet composé à la main : ce que le professeur a posé sur ses
    pages, variante par variante. Remplace intégralement le plan précédent."""
    competency_ids: list[str] = []
    # « overlay » / « print_fragile » : valeurs d'avant la refonte des guides,
    # encore envoyées par un onglet resté ouvert — lues « include ».
    # « auto » : guides imprimés selon le niveau de l'élève, dosés par guides_reach_pct.
    guides: Literal["include", "none", "auto", "overlay", "print_fragile"] = "include"
    guides_reach_pct: int = student_history.DEFAULT_GUIDE_REACH
    variant_kind: Literal["none", "anticheat", "level"] = "none"
    variants: list[VariantIn] = []
    # « engine » : seules les cartes comptent, le moteur les place et chaque copie
    # a son propre ordre. « placed » : plan figé page, colonne et rang.
    composition: Literal["placed", "engine"] = "placed"


def _has_correction(a: Assessment, scan_batch: ScanBatch | None) -> bool:
    """Une correction commencée (même bloquée) interdit de supprimer le sujet ;
    « Effacer la correction » (Corrections) lève ce verrou."""
    return scan_batch is not None or a.status in ("scanning", "finalized")


@router.get("")
def list_assessments(db: Session = Depends(get_db)):
    out = []
    for a in db.query(Assessment).order_by(Assessment.created_at.desc()).all():
        cls = db.get(SchoolClass, a.class_id)
        if cls and cls.archived_at is not None:
            continue  # classes archivées : aucune trace
        scan_batch = db.query(ScanBatch).filter_by(assessment_id=a.id).first()
        out.append({"id": a.id, "title": a.title, "type": a.type, "status": a.status,
                    "class_name": cls.name if cls else "?",
                    "class_id": a.class_id, "subject_id": a.subject_id,
                    "school_id": cls.school_id if cls else None,
                    "grade_level": cls.grade_level if cls else "",
                    "duplex": bool(a.duplex),
                    "personalization_mode": a.personalization_mode,
                    # sujet composé à la main (assistant « Créer un sujet »,
                    # mode manuel) ou personnalisé par élève (mode automatique)
                    "manual": (a.blueprint_json or {}).get("mode") == "manual",
                    "auto": (a.blueprint_json or {}).get("mode") == "auto",
                    "problems": bool((a.blueprint_json or {}).get("problems")),
                    "variant_kind": (a.blueprint_json or {}).get("variant_kind") or "",
                    "duplicate_version": int((a.blueprint_json or {}).get(
                        "duplicate_version") or 1),
                    "note_base": scoring.assessment_note_base(a),
                    # Une distribution clôt visuellement la même carte dans
                    # Sujets et Corrections.
                    "overlay_distributed": bool(
                        scan_batch and scan_batch.overlay_distributed),
                    "has_correction": _has_correction(a, scan_batch),
                    "error_message": a.error_message,
                    "created_at": str(a.created_at)})
    return out


@router.post("")
def create_assessment(body: AssessmentIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.type not in ("control", "training"):
        raise HTTPException(422, "type invalide")
    if not 1 <= body.pages <= 6:
        raise HTTPException(422, "pages entre 1 et 6")
    if body.note_base not in scoring.NOTE_BASES:
        raise HTTPException(422, "base de notation : 5, 10 ou 20")
    cls = db.get(SchoolClass, body.class_id)
    if not cls: raise HTTPException(404, "Classe inaccessible")
    teaching = db.query(ClassTeaching).filter_by(class_id=cls.id, subject_id=body.subject_id).first()
    if not teaching: raise HTTPException(422, "Aucun professeur affecté à cette matière")
    fw = framework_for(db, cls, body.subject_id, body.group_id)
    if not fw: raise HTTPException(422, "Référentiel non préparé pour cet enseignement")
    a = Assessment(class_id=body.class_id, subject_id=body.subject_id, group_id=body.group_id,
                   framework_id=fw.id, teacher_profile_id=teaching.teacher_id, created_by=user.id,
                   type=body.type, title=body.title.strip(),
                   pages_target=body.pages, duplex=body.pages >= 2,
                   note_base=body.note_base)
    assert_service_active(db, a)
    if not a.title: raise HTTPException(422, "Titre requis")
    db.add(a)
    db.commit()
    return {"id": a.id}


def validate_competencies(db, assessment, ids):
    cls = db.get(SchoolClass, assessment.class_id)
    framework = db.get(CompetencyFramework, assessment.framework_id) if assessment.framework_id else framework_for(db, cls, assessment.subject_id or "math", assessment.group_id)
    if framework is None:
        raise HTTPException(422, "Référentiel absent")
    found = db.query(Competency).filter(Competency.id.in_(set(ids)), Competency.framework_id == framework.id).count()
    if found != len(set(ids)):
        raise HTTPException(422, "Compétences incompatibles avec le programme, la matière ou la série")


@router.patch("/{assessment_id}")
def patch_assessment(assessment_id: str, body: AssessmentPatch, db: Session = Depends(get_db)):
    """Enregistre au fil de l'assistant : compétences cochées (étape
    Exercices) puis mode d'adaptation (étape Adaptation) — un sujet déjà mis
    en file/généré est immuable (§5.5)."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    if a.status != "draft":
        raise HTTPException(409, "Sujet déjà mis en file de génération")
    if body.title is not None:
        a.title = body.title
    if body.pages is not None:
        if not 1 <= body.pages <= 6:
            raise HTTPException(422, "pages entre 1 et 6")
        a.pages_target = body.pages
        a.duplex = body.pages >= 2
    if body.note_base is not None:
        if body.note_base not in scoring.NOTE_BASES:
            raise HTTPException(422, "base de notation : 5, 10 ou 20")
        a.note_base = body.note_base
    if body.personalization_mode is not None:
        a.personalization_mode = body.personalization_mode
    if body.competency_ids is not None:
        validate_competencies(db, a, body.competency_ids)
        a.blueprint_json = {**(a.blueprint_json or {}), "competency_ids": body.competency_ids}
    if body.exercise_source is not None:
        a.blueprint_json = {**(a.blueprint_json or {}), "exercise_source": body.exercise_source}
    if body.guides is not None:
        a.blueprint_json = {**(a.blueprint_json or {}), "guides": body.guides}
    db.commit()
    return {"ok": True}


@router.get("/exercises")
def list_exercises(grade_level: str | None = None, search: str | None = None,
                   provider: str | None = None, limit: int = 200,
                   db: Session = Depends(get_db)):
    q = db.query(ExerciseCatalog)
    if grade_level:
        q = q.filter_by(grade_level=grade_level)
    if provider:
        q = q.filter_by(provider=provider)
    if search:
        q = q.filter(ExerciseCatalog.title.ilike(f"%{search}%"))
    return [{"id": e.id, "title": e.title, "difficulty": e.difficulty,
             "response_type": e.response_type, "grade_level": e.grade_level,
             "provider": e.provider, "provider_ref": e.provider_ref,
             "automation_tier": e.automation_tier} for e in q.limit(limit).all()]


class PrepareAiIn(BaseModel):
    competency_id: str


@router.post("/exercises/ai-prepare")
def prepare_ai_exercise(body: PrepareAiIn, db: Session = Depends(get_db)):
    """Crée (ou retrouve) l'entrée catalogue « exercice IA » d'une compétence
    et amorce sa banque au niveau standard, pour préchauffage manuel — la
    génération réelle d'un sujet passe par le worker de fond (job_worker),
    pas par cet endpoint."""
    from ..services import exercise_gen

    comp = db.get(Competency, body.competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    row = exercise_gen.ensure_catalog_ref(db, comp)
    try:
        bank = exercise_gen.ensure_bank(db, comp, level=3)
    except Exception as e:
        db.commit()
        raise HTTPException(502, f"Banque IA indisponible : {e}")
    db.commit()
    return {"id": row.id, "title": row.title, "bank_level3": len(bank)}


@router.get("/exercises/ai-bank/{competency_id}")
def ai_bank_status(competency_id: str, db: Session = Depends(get_db)):
    from ..models import GeneratedExercise
    rows = db.query(GeneratedExercise).filter_by(
        competency_id=competency_id, status="active").all()
    by_level = {lv: 0 for lv in range(1, 6)}
    for r in rows:
        by_level[r.difficulty_level] = by_level.get(r.difficulty_level, 0) + 1
    return {"competency_id": competency_id, "by_level": by_level, "total": len(rows)}


@router.get("/manual/pool")
def manual_pool(competency_ids: str = "", pages: int = 1,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Exercices ET problèmes proposés au mode manuel de l'assistant « Créer un sujet », avec
    la hauteur réelle (points PDF) de chaque carte et la géométrie des colonnes
    — l'assistant dessine ses pages à l'échelle du rendu final."""
    ids = [c for c in competency_ids.split(",") if c]
    if not ids:
        raise HTTPException(422, "Aucune compétence sélectionnée")
    return manual_subject.pool(db, ids, pages=max(1, min(6, pages)), user_id=user.id)


@router.get("/auto-eligibility")
def auto_eligibility(class_id: str, db: Session = Depends(get_db), subject_id: str = "math", group_id: str | None = None):
    """La création automatique est-elle ouverte pour cette classe ? Il faut au
    moins `AUTO_MIN_CORRECTED` sujet corrigé ; sous `AUTO_RECOMMENDED_CORRECTED`,
    l'assistant prévient que le niveau des élèves est encore imprécis. Renvoie aussi la répartition des niveaux 1-10 de la classe : c'est
    elle que l'assistant montre face au dégradé des guides et aux problèmes."""
    cls = db.get(SchoolClass, class_id)
    if not cls:
        raise HTTPException(404, "Classe introuvable")
    framework_for(db, cls, subject_id, group_id)
    corrected = student_history.corrected_subjects(db, class_id, subject_id, group_id)
    levels = {lvl: 0 for lvl in range(1, 11)}
    students = assessment_students(db, Assessment(class_id=class_id, group_id=group_id))
    for st in students:
        levels[generation._student_level(db, st.id, subject_id)] += 1
    return {"corrected": corrected, "required": student_history.AUTO_MIN_CORRECTED,
            "recommended": student_history.AUTO_RECOMMENDED_CORRECTED,
            "eligible": corrected >= student_history.AUTO_MIN_CORRECTED,
            "students": len(students),
            "levels": [{"level": k, "count": v} for k, v in levels.items()]}


@router.get("/suggested-competencies")
def suggested_competencies(class_id: str, db: Session = Depends(get_db), subject_id: str = "math", group_id: str | None = None):
    """Proposition automatique de compétences à cocher : privilégie celles
    dues (courbe d'oubli) sur l'ensemble de la classe (§7.4). Déterministe ;
    aucun LLM n'intervient pour planifier."""
    cls = db.get(SchoolClass, class_id)
    if not cls: raise HTTPException(404)
    fw = framework_for(db, cls, subject_id, group_id)
    students = assessment_students(db, Assessment(class_id=class_id, group_id=group_id))
    allowed = {c.id for c in db.query(Competency).filter_by(framework_id=fw.id)} if fw else set()
    due_comp_ids: list[str] = []
    seen: set[str] = set()
    for s in students:
        for d in due_competencies(db, s.id):
            if d["competency_id"] in allowed and d["competency_id"] not in seen:
                seen.add(d["competency_id"])
                due_comp_ids.append(d["competency_id"])
    return {"competency_ids": due_comp_ids[:8],
            "reason": f"{len(due_comp_ids)} compétence(s) à revoir (courbe de l'oubli) "
                      "chez au moins un élève de la classe." if due_comp_ids else
                      "Aucune compétence à revoir pour l'instant."}


@router.post("/{assessment_id}/auto-plan")
def save_auto_plan(assessment_id: str, body: AutoPlanIn, db: Session = Depends(get_db)):
    """Enregistre une création AUTOMATIQUE : sujet individuel (une copie par
    élève, choisie sur son historique), exercices pris dans toute la banque,
    guides en dégradé selon le niveau, problèmes en option."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    if a.status != "draft":
        raise HTTPException(409, "Sujet déjà mis en file de génération")
    corrected = student_history.corrected_subjects(db, a.class_id, a.subject_id, a.group_id)
    if corrected < student_history.AUTO_MIN_CORRECTED:
        raise HTTPException(
            409, "Création automatique : il faut au moins un sujet corrigé "
                 "pour connaître le niveau des élèves.")
    ids = list(dict.fromkeys(body.competency_ids))
    validate_competencies(db, a, ids)
    if not ids:
        raise HTTPException(422, "Aucune compétence sélectionnée")
    if not 0 <= body.guides_reach_pct <= 100:
        raise HTTPException(422, "Réglage des guides : entre 0 et 100 %")
    a.blueprint_json = {**(a.blueprint_json or {}), "mode": "auto",
                        "competency_ids": ids,
                        "exercise_source": exercise_gen.BANK_SOURCE,
                        "guides": body.guides,
                        "guides_reach_pct": body.guides_reach_pct,
                        "problems": body.problems}
    a.personalization_mode = "individual"
    db.commit()
    return {"ok": True}


@router.post("/{assessment_id}/manual-plan")
def save_manual_plan(assessment_id: str, body: ManualPlanIn,
                     db: Session = Depends(get_db)):
    """Enregistre le plan composé à la main (§ assistant « Créer un sujet », mode manuel).
    Le sujet passe en mode "manual" : la génération ne choisit plus aucun
    exercice, elle pose ceux du plan là où ils ont été placés."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    if a.status != "draft":
        raise HTTPException(409, "Sujet déjà mis en file de génération")
    variants = [v for v in body.variants if v.items]
    if not variants:
        raise HTTPException(422, "Aucun exercice placé")
    if body.variant_kind == "level" and {v.key for v in variants} - set(manual_subject.LEVEL_KEYS):
        raise HTTPException(422, "Variantes par niveau : facile, moyen ou difficile")
    if len(variants) > manual_subject.MAX_VARIANTS:
        raise HTTPException(422, f"{manual_subject.MAX_VARIANTS} variantes au maximum")
    if any(it.page >= (a.pages_target or 1) for v in variants for it in v.items):
        raise HTTPException(422, "Un exercice est placé au-delà de la dernière page")

    # les compétences restent enregistrées telles quelles (bilan de compétences,
    # courbe d'oubli) ; à défaut, on les déduit des exercices placés.
    competency_ids = list(body.competency_ids)
    if not competency_ids:
        placed = {it.exercise_id for v in variants for it in v.items}
        competency_ids = list(dict.fromkeys(
            r.competency_id for r in db.query(GeneratedExercise)
            .filter(GeneratedExercise.id.in_(placed)).all()))

    validate_competencies(db, a, competency_ids)
    placed_ids = {it.exercise_id for v in variants for it in v.items}
    placed_rows = db.query(GeneratedExercise).filter(GeneratedExercise.id.in_(placed_ids),
        GeneratedExercise.status == "active").all()
    validate_competencies(db, a, [r.competency_id for r in placed_rows])
    if len(placed_rows) != len(placed_ids):
        raise HTTPException(422, "Un exercice est absent de la banque ou incompatible avec les compétences")
    if any(it.page < 0 or it.col not in (0, 1) or it.rank < 0 for v in variants for it in v.items):
        raise HTTPException(422, "Placement invalide")
    if not 0 <= body.guides_reach_pct <= 100:
        raise HTTPException(422, "Réglage des guides : entre 0 et 100 %")
    guides = (manual_subject.GUIDES_BY_LEVEL if body.guides == manual_subject.GUIDES_BY_LEVEL
              else manual_subject.normalize_guide_mode(body.guides))
    composition = {}
    if body.composition == manual_subject.COMPOSITION_ENGINE:
        for v in variants:
            try:
                manual_subject.assert_selection_fits(
                    db, [it.exercise_id for it in v.items], a.pages_target or 1, guides)
            except ValueError as e:
                raise HTTPException(422, str(e))
        composition = {"composition": manual_subject.COMPOSITION_ENGINE,
                       "guides_reach_pct": body.guides_reach_pct}
    a.blueprint_json = {**(a.blueprint_json or {}), "mode": "manual",
                        "competency_ids": competency_ids,
                        "guides": guides, **composition,
                        "variant_kind": body.variant_kind,
                        "variants": [v.model_dump() for v in variants]}
    # pas de sujet individuel dans cet assistant : commun, avec ou sans variantes
    a.personalization_mode = "common" if len(variants) == 1 else "common_variants"
    db.commit()
    return {"ok": True, "variants": len(variants),
            "items": sum(len(v.items) for v in variants)}


class DuplicateIn(BaseModel):
    # autre classe du même cycle ; absent = nouvelle passation dans la même classe
    class_id: str | None = None


def _duplication_target(db: Session, source: Assessment, class_id: str) -> tuple[SchoolClass, ClassTeaching]:
    """Une autre classe ne peut recevoir le sujet que si ses exercices y ont
    leur place : même établissement, même cycle, même référentiel."""
    origin = db.get(SchoolClass, source.class_id)
    target = db.get(SchoolClass, class_id)
    if not origin or not target:
        raise HTTPException(404, "Classe inaccessible")
    if target.grade_level != origin.grade_level or target.school_id != origin.school_id:
        raise HTTPException(422, "La classe doit être du même cycle et du même établissement")
    teaching = db.query(ClassTeaching).filter_by(
        class_id=target.id, subject_id=source.subject_id).first()
    if not teaching:
        raise HTTPException(422, "Aucun professeur affecté à cette matière dans cette classe")
    framework = framework_for(db, target, source.subject_id)
    if source.framework_id and (not framework or framework.id != source.framework_id):
        raise HTTPException(422, "Cette classe ne suit pas le même référentiel")
    if not db.query(Student).filter_by(class_id=target.id, active=True).first():
        raise HTTPException(409, "Cette classe n'a aucun élève actif")
    return target, teaching


@router.post("/{assessment_id}/duplicate", status_code=202)
def duplicate_assessment(assessment_id: str, body: DuplicateIn | None = None,
                         db: Session = Depends(get_db)):
    """Recrée un sujet manuel à l'identique pour une nouvelle passation.

    Le plan contient les exercices concrets, pas des critères de tirage. Dans
    la même classe, on fige en plus l'affectation élève → variante de la
    première passation : l'ordre de la classe peut donc changer sans modifier
    les feuilles reçues. Vers une autre classe (`class_id`), les variantes sont
    attribuées à ses propres élèves, comme pour un sujet neuf.
    """
    source = db.get(Assessment, assessment_id)
    if not source:
        raise HTTPException(404, "Sujet introuvable")
    blueprint = source.blueprint_json or {}
    if blueprint.get("mode") != "manual":
        raise HTTPException(
            409, "La duplication à l'identique est disponible pour les sujets sur mesure")
    if source.status not in ("ready", "printed", "scanning", "finalized"):
        raise HTTPException(409, "Le sujet doit être généré avant d'être dupliqué")

    root_id = blueprint.get("duplicate_source_id") or source.id
    source_version = int(blueprint.get("duplicate_version") or 1)
    base_title = source.title
    if source_version > 1:
        base_title = base_title.removesuffix(f" v{source_version}")
    target_id = body.class_id if body and body.class_id else source.class_id

    if target_id == source.class_id:
        source_copies = db.query(Copy).filter_by(assessment_id=source.id).all()
        if not source_copies:
            raise HTTPException(409, "Aucune affectation de variante à dupliquer")
        student_variants = {c.student_id: c.variant_key for c in source_copies}
        version = source_version + 1
        teacher_profile_id, group_id = source.teacher_profile_id, source.group_id
    else:
        target, teaching = _duplication_target(db, source, target_id)
        student_variants = {}
        # version propre à la classe : sa première passation ne porte pas de « v2 »
        version = 1 + max((
            int((a.blueprint_json or {}).get("duplicate_version") or 1)
            for a in db.query(Assessment).filter_by(class_id=target.id).all()
            if a.id == root_id or (a.blueprint_json or {}).get("duplicate_source_id") == root_id
        ), default=0)
        teacher_profile_id, group_id = teaching.teacher_id, None

    cloned_blueprint = copylib.deepcopy(blueprint)
    cloned_blueprint.update({
        "duplicate_version": version,
        "duplicate_source_id": root_id,
        "duplicate_student_variants": student_variants,
    })
    duplicate = Assessment(
        class_id=target_id, type=source.type, subject_id=source.subject_id,
        teacher_profile_id=teacher_profile_id, framework_id=source.framework_id,
        group_id=group_id, created_by=db.info.get("actor_id"),
        title=f"{base_title} v{version}" if version > 1 else base_title,
        pages_target=source.pages_target,
        duplex=source.duplex, personalization_mode=source.personalization_mode,
        blueprint_json=cloned_blueprint, note_base=source.note_base,
    )
    assert_service_active(db, duplicate)
    db.add(duplicate)
    db.flush()
    job = job_worker.enqueue_generation(db, duplicate, 10)
    return {"id": duplicate.id, "job_id": job.id, "status": "queued",
            "title": duplicate.title, "version": version}


@router.post("/{assessment_id}/mark-printed")
def mark_printed(assessment_id: str, db: Session = Depends(get_db)):
    """Sujet imprimé hors de la plateforme : même état qu'après une impression
    réussie par le connecteur."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404, "Sujet introuvable")
    if a.status != "ready":
        raise HTTPException(409, "Seul un sujet prêt peut être marqué comme imprimé")
    a.status = "printed"
    db.add(AuditLog(actor_id=db.info.get("actor_id"), action="assessment.mark_printed",
                    entity_type="assessment", entity_id=a.id))
    db.commit()
    return {"ok": True, "status": a.status}


@router.delete("/{assessment_id}")
def delete_assessment(assessment_id: str, db: Session = Depends(get_db)):
    """Suppression par le professeur, tant qu'aucune correction n'existe. La
    suppression sans condition reste réservée à Paramètres → Données."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404, "Sujet introuvable")
    if a.status in ("queued", "generating"):
        raise HTTPException(409, "Sujet en cours de génération : attendez la fin pour le supprimer")
    if _has_correction(a, db.query(ScanBatch).filter_by(assessment_id=a.id).first()):
        raise HTTPException(409, "Ce sujet a déjà une correction : il ne peut pas être supprimé")
    db.add(AuditLog(actor_id=db.info.get("actor_id"), action="assessment.delete",
                    entity_type="assessment", entity_id=a.id,
                    before_json={"title": a.title, "class_id": a.class_id, "status": a.status}))
    result = data_admin.delete_assessment(db, a)
    db.commit()
    return {"ok": True, **result}


@router.get("/competency-matrix")
def competency_matrix(grade_level: str, db: Session = Depends(get_db),
                      class_id: str | None = None, subject_id: str = "math",
                      group_id: str | None = None, framework_id: str | None = None):
    """Tableau complet des compétences du niveau (même hiérarchie/ordre que
    l'onglet Compétences), une colonne de maîtrise moyenne par classe du
    niveau — alimente l'étape Exercices de l'assistant sujet."""
    classes = (db.query(SchoolClass)
               .filter_by(grade_level=grade_level)
               .filter(SchoolClass.archived_at.is_(None)).all())
    cls = db.get(SchoolClass, class_id) if class_id else None
    if class_id and not cls: raise HTTPException(404, "Classe inaccessible")
    fw = (db.get(CompetencyFramework, framework_id) if framework_id else
          framework_for(db, cls, subject_id, group_id) if cls else
          db.query(CompetencyFramework).filter_by(grade_level=grade_level, subject_id=subject_id,
                                                  program_id="fr", path_id=None).first())
    if cls:
        classes = [cls]
    elif fw:
        classes = [c for c in classes if c.program_id == fw.program_id and c.series_id == fw.path_id]
    rows = (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.order_index).all() if fw else [])

    class_ids = [c.id for c in classes]
    student_class: dict[str, str] = {}
    if class_ids:
        for s in db.query(Student).filter(Student.class_id.in_(class_ids), Student.active.is_(True)).all():
            student_class[s.id] = s.class_id

    # une seule requête batch : évite un N+1 par cellule du tableau
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    if student_class:
        states = (db.query(StudentCompetencyState)
                  .filter(StudentCompetencyState.student_id.in_(student_class.keys())).all())
        for st in states:
            cid = student_class.get(st.student_id)
            if not cid:
                continue
            key = (cid, st.competency_id)
            sums[key] = sums.get(key, 0.0) + st.mastery
            counts[key] = counts.get(key, 0) + 1

    def mastery_by_class(competency: Competency) -> dict[str, float | None]:
        out = {}
        for c in classes:
            key = (c.id, competency.id)
            out[c.id] = round(sums[key] / counts[key], 3) if counts.get(key) else None
        return out

    # Ce que la banque contient pour chaque compétence (exercices) et chaque
    # chapitre (problèmes) : l'assistant grise ce qui ne produirait rien.
    from ..services.gemini_gen import FILLER_KIND
    ex_count: dict[str, int] = {}
    pb_count: dict[str, int] = {}
    comp_chapter = {c.id: c.chapter_code for c in rows}
    if rows:
        bank = (db.query(GeneratedExercise.competency_id, GeneratedExercise.kind)
                .filter(GeneratedExercise.competency_id.in_(comp_chapter.keys()),
                        GeneratedExercise.status == "active",
                        GeneratedExercise.kind != FILLER_KIND).all())
        for comp_id, kind in bank:
            if kind == "probleme":
                ch = comp_chapter.get(comp_id) or ""
                pb_count[ch] = pb_count.get(ch, 0) + 1
            else:
                ex_count[comp_id] = ex_count.get(comp_id, 0) + 1

    domains = build_competency_tree(
        rows, competency_extra=lambda c: {"mastery_by_class": mastery_by_class(c),
                                          "exercise_count": ex_count.get(c.id, 0)})
    for d in domains:
        for ch in d["chapters"]:
            ch["problem_count"] = pb_count.get(ch["code"] or "", 0)
    return {"classes": [{"id": c.id, "name": c.name} for c in classes], "domains": domains}


@router.get("/{assessment_id}/job")
def assessment_job(assessment_id: str, db: Session = Depends(get_db)):
    job = job_worker.latest_job(db, assessment_id)
    if not job:
        raise HTTPException(404, "Aucune génération en file pour ce sujet")
    return {"status": job.status, "progress": job.progress,
            "progress_message": job.progress_message, "error": job.error_code}


@router.get("/{assessment_id}/generation-log")
def generation_log(assessment_id: str, db: Session = Depends(get_db)):
    """Journal lisible du dernier job de génération (bouton « Voir log »)."""
    job = job_worker.latest_job(db, assessment_id)
    if not job:
        raise HTTPException(404, "Aucune génération en file pour ce sujet")
    return {"status": job.status, "progress": job.progress,
            "progress_message": job.progress_message, "error": job.error_code,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "log": job.log_text or ""}


@router.get("/jobs/active")
def active_generation_jobs(db: Session = Depends(get_db)):
    out = []
    for job in job_worker.active_jobs(db):
        a = db.get(Assessment, job.assessment_id)
        if not a:
            continue
        cls = db.get(SchoolClass, a.class_id)
        out.append({"assessment_id": a.id, "title": a.title,
                    "class_name": cls.name if cls else "?",
                    "status": job.status, "progress": job.progress})
    return out


@router.post("/{assessment_id}/generate", status_code=202)
def generate(assessment_id: str, body: GenerateIn, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    if a.status not in ("draft", "error"):
        raise HTTPException(409, "Sujet déjà en file ou généré (manifeste immuable, §5.5)")
    assert_service_active(db, a)
    blueprint = a.blueprint_json or {}
    actor = db.get(User, db.info.get("actor_id")) if db.info.get("actor_id") else None
    if actor and actor.role != "admin" and blueprint.get("mode") not in ("manual", "auto"):
        raise HTTPException(403, "Composez le sujet à partir des exercices de la banque")
    validate_competencies(db, a, blueprint.get("competency_ids", []))
    if blueprint.get("mode") == "manual":
        if not any(v.get("items") for v in blueprint.get("variants") or []):
            raise HTTPException(422, "Aucun exercice placé")
    elif not blueprint.get("competency_ids"):
        raise HTTPException(422, "Aucune compétence sélectionnée")
    a.status = "draft"  # autorise un nouvel essai depuis un état d'erreur
    job = job_worker.enqueue_generation(db, a, body.font_size)
    return {"job_id": job.id, "status": "queued"}


@router.get("/{assessment_id}/copies")
def list_copies(assessment_id: str, db: Session = Depends(get_db)):
    out = []
    for c in db.query(Copy).filter_by(assessment_id=assessment_id).all():
        s = db.get(Student, c.student_id)
        out.append({"id": c.id, "student": s.name, "student_order": s.order_index,
                    "status": c.status, "pages": c.total_pages, "seed": c.seed})
    out.sort(key=lambda copy: copy["student_order"])
    return out


@router.post("/{assessment_id}/copies/{copy_id}/absent")
def mark_absent(assessment_id: str, copy_id: str, db: Session = Depends(get_db)):
    c = db.get(Copy, copy_id)
    if not c or c.assessment_id != assessment_id:
        raise HTTPException(404)
    c.status = "absent"  # jamais pénalisé (infos complémentaires)
    db.commit()
    return {"ok": True}


@router.get("/{assessment_id}/preview")
def preview_info(assessment_id: str, db: Session = Depends(get_db)):
    """Copies triées par niveau élève pour l'aperçu : la plus facile, la
    médiane et la plus difficile sont repérées (§3.1 étape 5)."""
    import json as _json
    from ..models import StudentLevel

    assessment = db.get(Assessment, assessment_id)
    if not assessment: raise HTTPException(404)
    manifest_path = (settings.data_dir / "assessments" / assessment_id /
                     "generated" / "copy_manifest.json")
    if not manifest_path.exists():
        raise HTTPException(404, "Sujet non encore généré")
    manifest = _json.loads(manifest_path.read_text())

    entries = []
    page_offset = 0
    for m in manifest["copies"]:
        copy = db.get(Copy, m["copy_id"])
        if not copy:
            continue
        student = db.get(Student, copy.student_id)
        lvl_row = (db.query(StudentLevel).filter_by(student_id=student.id, subject_id=assessment.subject_id)
                   .order_by(StudentLevel.valid_from.desc()).first())
        entries.append({
            "copy_id": copy.id,
            "student": student.name,
            "level": lvl_row.level if lvl_row else 5,
            "pages": len(m["pages"]), "page_offset": page_offset,
        })
        page_offset += len(m["pages"])

    by_level = sorted(entries, key=lambda e: e["level"])
    roles = {}
    if by_level:
        roles[by_level[0]["copy_id"]] = "plus facile"
        roles[by_level[-1]["copy_id"]] = "plus difficile"
        roles[by_level[len(by_level) // 2]["copy_id"]] = "médiane"
    for e in entries:
        e["role"] = roles.get(e["copy_id"])
    return entries


@router.get("/{assessment_id}/copies/{copy_id}/pdf")
def copy_pdf(assessment_id: str, copy_id: str, db: Session = Depends(get_db)):
    """PDF d'une seule copie, extrait du batch d'après le manifeste."""
    import json as _json
    from pypdf import PdfReader, PdfWriter

    base = settings.data_dir / "assessments" / assessment_id
    manifest_path = base / "generated" / "copy_manifest.json"
    batch_path = base / "generated" / "subject_batch.pdf"
    if not manifest_path.exists() or not batch_path.exists():
        raise HTTPException(404, "Sujet non encore généré")

    cache = base / "generated" / "copies"
    cache.mkdir(exist_ok=True)
    out = cache / f"{copy_id}.pdf"
    if not out.exists():
        manifest = _json.loads(manifest_path.read_text())
        offset = 0
        found = None
        for m in manifest["copies"]:
            if m["copy_id"] == copy_id:
                found = (offset, len(m["pages"]))
                break
            offset += len(m["pages"])
        if not found:
            raise HTTPException(404, "Copie inconnue dans le manifeste")
        reader = PdfReader(str(batch_path))
        writer = PdfWriter()
        for i in range(found[0], min(found[0] + found[1], len(reader.pages))):
            writer.add_page(reader.pages[i])
        with open(out, "wb") as f:
            writer.write(f)
    return FileResponse(out, media_type="application/pdf", filename=f"copie-{copy_id[:8]}.pdf")


@router.get("/{assessment_id}/files/{name}")
def get_file(assessment_id: str, name: str, db: Session = Depends(get_db)):
    allowed = {"subject_batch.pdf": "generated", "copy_manifest.json": "generated",
               "generation_report.json": "generated", "correction_overlay.pdf": "overlays",
               "correction_review.pdf": "overlays"}
    if name not in allowed:
        raise HTTPException(404)
    path = settings.data_dir / "assessments" / assessment_id / allowed[name] / name
    if not Path(path).exists():
        raise HTTPException(404, "Fichier non encore généré")
    return FileResponse(path, filename=name)

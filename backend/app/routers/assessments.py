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
    StudentCompetencyState,
)
from ..services import job_worker, manual_subject, scoring
from ..services.forgetting import due_competencies
from .misc import build_competency_tree

router = APIRouter(prefix="/api/assessments", tags=["assessments"],
                   dependencies=[Depends(current_user)])


class AssessmentIn(BaseModel):
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


class GenerateIn(BaseModel):
    font_size: int = 10


# ------------------------------------------------- assistant « Créer mon sujet »

class PlacedItem(BaseModel):
    exercise_id: str
    page: int = 0            # index de page, 0 = 1re page
    col: int = 0             # 0 = colonne gauche, 1 = colonne droite
    rank: int = 0            # rang dans la colonne, de haut en bas


class VariantIn(BaseModel):
    key: str                 # "A"/"B"/… (anti-triche) ou facile|moyen|difficile
    label: str = ""
    items: list[PlacedItem] = []


class ManualPlanIn(BaseModel):
    """Plan complet composé à la main : ce que le professeur a posé sur ses
    pages, variante par variante. Remplace intégralement le plan précédent."""
    competency_ids: list[str] = []
    guides: Literal["overlay", "print_fragile", "none"] = "overlay"
    variant_kind: Literal["none", "anticheat", "level"] = "none"
    variants: list[VariantIn] = []


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
                    "class_id": a.class_id,
                    "grade_level": cls.grade_level if cls else "",
                    "duplex": bool(a.duplex),
                    "personalization_mode": a.personalization_mode,
                    # sujet composé à la main (assistant « Créer mon sujet »)
                    "manual": (a.blueprint_json or {}).get("mode") == "manual",
                    "variant_kind": (a.blueprint_json or {}).get("variant_kind") or "",
                    "duplicate_version": int((a.blueprint_json or {}).get(
                        "duplicate_version") or 1),
                    "note_base": scoring.assessment_note_base(a),
                    # Une distribution clôt visuellement la même carte dans
                    # Sujets et Corrections.
                    "overlay_distributed": bool(
                        scan_batch and scan_batch.overlay_distributed),
                    "error_message": a.error_message,
                    "created_at": str(a.created_at)})
    return out


@router.post("")
def create_assessment(body: AssessmentIn, db: Session = Depends(get_db)):
    if body.type not in ("control", "training"):
        raise HTTPException(422, "type invalide")
    if not 1 <= body.pages <= 6:
        raise HTTPException(422, "pages entre 1 et 6")
    if body.note_base not in scoring.NOTE_BASES:
        raise HTTPException(422, "base de notation : 5, 10 ou 20")
    a = Assessment(class_id=body.class_id, type=body.type, title=body.title,
                   pages_target=body.pages, duplex=body.pages >= 2,
                   note_base=body.note_base)
    db.add(a)
    db.commit()
    return {"id": a.id}


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
        a.blueprint_json = {**(a.blueprint_json or {}), "competency_ids": body.competency_ids}
    if body.exercise_source is not None:
        a.blueprint_json = {**(a.blueprint_json or {}), "exercise_source": body.exercise_source}
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
                db: Session = Depends(get_db)):
    """Exercices ET problèmes proposés à l'assistant « Créer mon sujet », avec
    la hauteur réelle (points PDF) de chaque carte et la géométrie des colonnes
    — l'assistant dessine ses pages à l'échelle du rendu final."""
    ids = [c for c in competency_ids.split(",") if c]
    if not ids:
        raise HTTPException(422, "Aucune compétence sélectionnée")
    return manual_subject.pool(db, ids, pages=max(1, min(6, pages)))


@router.post("/{assessment_id}/manual-plan")
def save_manual_plan(assessment_id: str, body: ManualPlanIn,
                     db: Session = Depends(get_db)):
    """Enregistre le plan composé à la main (§ assistant « Créer mon sujet »).
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

    a.blueprint_json = {**(a.blueprint_json or {}), "mode": "manual",
                        "competency_ids": competency_ids,
                        "guides": body.guides, "variant_kind": body.variant_kind,
                        "variants": [v.model_dump() for v in variants]}
    # pas de sujet individuel dans cet assistant : commun, avec ou sans variantes
    a.personalization_mode = "common" if len(variants) == 1 else "common_variants"
    db.commit()
    return {"ok": True, "variants": len(variants),
            "items": sum(len(v.items) for v in variants)}


@router.post("/{assessment_id}/duplicate", status_code=202)
def duplicate_assessment(assessment_id: str, db: Session = Depends(get_db)):
    """Recrée un sujet manuel à l'identique pour une nouvelle passation.

    Le plan contient les exercices concrets, pas des critères de tirage. On
    fige en plus l'affectation élève → variante de la première passation :
    l'ordre de la classe peut donc changer sans modifier les feuilles reçues.
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

    source_copies = db.query(Copy).filter_by(assessment_id=source.id).all()
    if not source_copies:
        raise HTTPException(409, "Aucune affectation de variante à dupliquer")

    version = int(blueprint.get("duplicate_version") or 1) + 1
    root_id = blueprint.get("duplicate_source_id") or source.id
    cloned_blueprint = copylib.deepcopy(blueprint)
    cloned_blueprint.update({
        "duplicate_version": version,
        "duplicate_source_id": root_id,
        "duplicate_student_variants": {
            c.student_id: c.variant_key for c in source_copies
        },
    })
    base_title = source.title
    previous_suffix = f" v{version - 1}"
    if version > 2 and base_title.endswith(previous_suffix):
        base_title = base_title[:-len(previous_suffix)]
    duplicate = Assessment(
        class_id=source.class_id, type=source.type,
        title=f"{base_title} v{version}", pages_target=source.pages_target,
        duplex=source.duplex, personalization_mode=source.personalization_mode,
        blueprint_json=cloned_blueprint, note_base=source.note_base,
    )
    db.add(duplicate)
    db.flush()
    job = job_worker.enqueue_generation(db, duplicate, 10)
    return {"id": duplicate.id, "job_id": job.id, "status": "queued",
            "title": duplicate.title, "version": version}


@router.get("/{assessment_id}/suggested-competencies")
def suggested_competencies(assessment_id: str, db: Session = Depends(get_db)):
    """Proposition automatique de compétences à cocher : privilégie celles
    dues (courbe d'oubli) sur l'ensemble de la classe (§7.4). Déterministe ;
    DeepSeek n'intervient jamais pour planifier."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    students = (db.query(Student).filter_by(class_id=a.class_id, active=True)
                .order_by(Student.order_index, Student.id).all())

    due_comp_ids: list[str] = []
    seen: set[str] = set()
    for s in students:
        for d in due_competencies(db, s.id):
            if d["competency_id"] not in seen:
                seen.add(d["competency_id"])
                due_comp_ids.append(d["competency_id"])

    return {"competency_ids": due_comp_ids[:8],
            "reason": f"{len(due_comp_ids)} compétence(s) due(s) (courbe de l'oubli) "
                      "sur au moins un élève de la classe." if due_comp_ids else
                      "Aucune compétence due pour l'instant : à choisir manuellement."}


@router.get("/competency-matrix")
def competency_matrix(grade_level: str, db: Session = Depends(get_db)):
    """Tableau complet des compétences du niveau (même hiérarchie/ordre que
    l'onglet Compétences), une colonne de maîtrise moyenne par classe du
    niveau — alimente l'étape Exercices de l'assistant sujet."""
    classes = (db.query(SchoolClass)
               .filter_by(grade_level=grade_level)
               .filter(SchoolClass.archived_at.is_(None)).all())
    fw = (db.query(CompetencyFramework).filter_by(grade_level=grade_level).first())
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

    domains = build_competency_tree(
        rows, competency_extra=lambda c: {"mastery_by_class": mastery_by_class(c)})
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
    blueprint = a.blueprint_json or {}
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
        lvl_row = (db.query(StudentLevel).filter_by(student_id=student.id)
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

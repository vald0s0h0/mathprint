"""Assistant sujets (§3.1) : création, proposition automatique, génération, fichiers."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import (
    Assessment, Copy, ExerciseCatalog, ExerciseCompetency, SchoolClass, Student,
)
from ..services.forgetting import due_competencies
from ..services.generation import generate_assessment

router = APIRouter(prefix="/api/assessments", tags=["assessments"],
                   dependencies=[Depends(current_user)])


class AssessmentIn(BaseModel):
    class_id: str
    type: str = "training"          # control | training
    title: str
    pages: int = 1                  # 1 = recto, 2 = recto/verso, etc.
    personalization_mode: str = "common"


class GenerateIn(BaseModel):
    exercise_ids: list[str]
    font_size: int = 10


@router.get("")
def list_assessments(db: Session = Depends(get_db)):
    out = []
    for a in db.query(Assessment).order_by(Assessment.created_at.desc()).all():
        cls = db.get(SchoolClass, a.class_id)
        out.append({"id": a.id, "title": a.title, "type": a.type, "status": a.status,
                    "class_name": cls.name if cls else "?",
                    "personalization_mode": a.personalization_mode,
                    "created_at": str(a.created_at)})
    return out


@router.post("")
def create_assessment(body: AssessmentIn, db: Session = Depends(get_db)):
    if body.type not in ("control", "training"):
        raise HTTPException(422, "type invalide")
    if not 1 <= body.pages <= 6:
        raise HTTPException(422, "pages entre 1 et 6")
    a = Assessment(class_id=body.class_id, type=body.type, title=body.title,
                   pages_target=body.pages, duplex=body.pages >= 2,
                   personalization_mode=body.personalization_mode)
    db.add(a)
    db.commit()
    return {"id": a.id}


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
    """Crée (ou retrouve) l'entrée catalogue « exercice IA » d'une compétence.
    Les exercices concrets sont générés par deepseek-v4-pro et stockés en
    banque par niveau 1-5 ; le niveau réel est choisi copie par copie."""
    from ..models import Competency, CompetencyFramework
    from ..services import exercise_gen

    comp = db.get(Competency, body.competency_id)
    if not comp:
        raise HTTPException(404, "Compétence inconnue")
    fw = db.get(CompetencyFramework, comp.framework_id)
    ref = f"deepseek:{comp.id}"
    row = db.query(ExerciseCatalog).filter_by(provider="deepseek", provider_ref=ref).first()
    if not row:
        row = ExerciseCatalog(
            provider="deepseek", provider_ref=ref,
            title=f"[IA] {comp.label}", grade_level=fw.grade_level if fw else "5e",
            difficulty=5, response_type="short_text", automation_tier="auto")
        db.add(row)
        db.flush()
        db.add(ExerciseCompetency(exercise_id=row.id, competency_id=comp.id,
                                  weight=1.0, evidence_strength=1.0))
    # amorcer la banque au niveau standard pour valider la faisabilité tout de suite
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


@router.post("/exercises/sync-mathalea")
def sync_mathalea(db: Session = Depends(get_db)):
    """Importe/actualise le catalogue MathALÉA depuis le service Node (§3.3).
    Les exercices avec réponse structurée (AMCNum/mathLive) sont automatisables ;
    les autres passent en validation obligatoire."""
    from ..services import mathalea_client
    from ..models import Competency, CompetencyFramework

    try:
        entries = mathalea_client.catalog()
    except mathalea_client.MathaleaUnavailable as e:
        raise HTTPException(503, str(e))

    # compétences par grade pour un rattachement heuristique par similarité de titre
    comps_by_grade: dict[str, list] = {}
    for fw in db.query(CompetencyFramework).all():
        comps_by_grade[fw.grade_level] = db.query(Competency).filter_by(
            framework_id=fw.id).all()

    def match_competency(grade: str, title: str):
        tokens = {w for w in title.lower().split() if len(w) > 4}
        best, best_score = None, 0.0
        for c in comps_by_grade.get(grade, []):
            ltokens = {w for w in c.label.lower().split() if len(w) > 4}
            if not tokens or not ltokens:
                continue
            score = len(tokens & ltokens) / len(tokens | ltokens)
            if score > best_score:
                best, best_score = c, score
        return best if best_score >= 0.2 else None

    existing = {e.provider_ref: e for e in db.query(ExerciseCatalog)
                .filter_by(provider="mathalea").all()}
    created = updated = mapped = 0
    for entry in entries:
        ref = f"mathalea:{entry['ref']}"
        auto = entry.get("amcType") == "AMCNum" or entry.get("interactifType") == "mathLive"
        row = existing.get(ref)
        if row:
            row.title = entry["title"]
            updated += 1
        else:
            row = ExerciseCatalog(
                provider="mathalea", provider_ref=ref, title=entry["title"],
                grade_level=entry["grade"], difficulty=5,
                response_type="short_text" if auto else "multiline_text",
                automation_tier="auto" if auto else "review_required")
            db.add(row)
            db.flush()
            comp = match_competency(entry["grade"], entry["title"])
            if comp:
                db.add(ExerciseCompetency(exercise_id=row.id, competency_id=comp.id,
                                          weight=1.0, evidence_strength=0.5))
                mapped += 1
            created += 1
    db.commit()
    return {"created": created, "updated": updated, "competency_mapped": mapped,
            "total": len(entries)}


@router.get("/{assessment_id}/suggestion")
def suggest_exercises(assessment_id: str, db: Session = Depends(get_db)):
    """Proposition automatique : privilégie les compétences dues (courbe d'oubli),
    répartition 60/30/10 en entraînement (§7.4). Déterministe ; DeepSeek n'est
    utilisé que pour affiner, jamais pour planifier."""
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    cls = db.get(SchoolClass, a.class_id)
    students = db.query(Student).filter_by(class_id=a.class_id, active=True).all()

    due_comp_ids: set[str] = set()
    for s in students:
        for d in due_competencies(db, s.id):
            due_comp_ids.add(d["competency_id"])

    exercises = db.query(ExerciseCatalog).filter_by(grade_level=cls.grade_level).all()
    if not exercises:
        exercises = db.query(ExerciseCatalog).all()

    def is_due(e):
        links = db.query(ExerciseCompetency).filter_by(exercise_id=e.id).all()
        return any(l.competency_id in due_comp_ids for l in links)

    due_ex = [e for e in exercises if is_due(e)]
    others = [e for e in exercises if not is_due(e)]
    easy = sorted(others, key=lambda e: e.difficulty)
    picked = (due_ex[:3] + easy[:3])[:6] or exercises[:6]
    return {"exercise_ids": [e.id for e in picked],
            "reason": f"{len(due_ex)} exercice(s) lié(s) à des compétences dues ; "
                      "complété par consolidation accessible (répartition 60/30/10)."}


@router.post("/{assessment_id}/generate")
def generate(assessment_id: str, body: GenerateIn, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404)
    if a.status not in ("draft",):
        raise HTTPException(409, "Sujet déjà généré (manifeste immuable après impression, §5.5)")
    try:
        report = generate_assessment(db, a, body.exercise_ids, body.font_size)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return report


@router.get("/{assessment_id}/copies")
def list_copies(assessment_id: str, db: Session = Depends(get_db)):
    out = []
    for c in db.query(Copy).filter_by(assessment_id=assessment_id).all():
        s = db.get(Student, c.student_id)
        out.append({"id": c.id, "student": f"{s.last_name} {s.first_name}",
                    "status": c.status, "pages": c.total_pages, "seed": c.seed})
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
            "student": f"{student.last_name} {student.first_name}",
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
               "generation_report.json": "generated", "correction_overlay.pdf": "overlays"}
    if name not in allowed:
        raise HTTPException(404)
    path = settings.data_dir / "assessments" / assessment_id / allowed[name] / name
    if not Path(path).exists():
        raise HTTPException(404, "Fichier non encore généré")
    return FileResponse(path, filename=name)

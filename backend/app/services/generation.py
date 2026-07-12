"""Orchestration de la génération d'un sujet (§3, §5.1).

Produit pour une évaluation : copies individuelles (avec seed), instantanés
d'exercices (RM-014), pages avec QR signés, zones de réponse, subject_batch.pdf,
copy_manifest.json et generation_report.json.

Sources d'exercices :
- provider "deepseek" : banque generated_exercises (compétence × niveau 1-5),
  générée par deepseek-v4-pro et réutilisée ; le niveau de l'exercice suit le
  niveau 1-10 de l'élève (sauf mode commun : niveau fixe) ;
- provider "builtin"/"mathalea" : générateurs seedés existants.

Rappels de leçon : pour un entraînement, les élèves fragiles (niveau ≤ 4)
reçoivent un rappel DeepSeek stocké (lesson_snippets) avant le premier
exercice de chaque compétence concernée (2 max par copie).
"""
import hashlib
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Assessment, Competency, Copy, CopyItem, DocumentPage, ExerciseCatalog,
    ExerciseCompetency, FileObject, ResponseZone, SchoolClass, Student,
    StudentLevel,
)
from . import exercise_gen
from . import exercises as exgen
from . import pdfgen
from .runtime_settings import doc_templates
from .security import sign_page


def assessment_dir(assessment_id: str) -> Path:
    d = settings.data_dir / "assessments" / assessment_id / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _student_level(db: Session, student_id: str) -> int:
    lvl = (db.query(StudentLevel).filter_by(student_id=student_id)
           .order_by(StudentLevel.valid_from.desc()).first())
    return lvl.level if lvl else 5


def _pick_difficulty(base: int, personalization: str, student_level: int) -> int:
    """Difficulté 1-10 de l'item : commune, ou adaptée (plage encadrée ±2)."""
    if personalization == "common":
        return base
    delta = max(-2, min(2, student_level - 5))
    return max(1, min(10, base + delta))


def _build_item(db: Session, ex: ExerciseCatalog, seed: int, difficulty: int,
                warnings: list[str]):
    """Instantané d'exercice selon le provider. Retourne (gen_dict, level5)."""
    level5 = exercise_gen.student_level_to_difficulty(difficulty)
    if ex.provider == "deepseek":
        comp = db.get(Competency, ex.provider_ref.split(":", 1)[1])
        row = exercise_gen.pick_exercise(db, comp, level5, seed)
        choices = row.grading_json.get("choices", [])
        return {"statement": row.statement, "correction": row.correction,
                "response_type": row.response_type, "expected": row.expected_json,
                "grading": row.grading_json, "choices": choices,
                "figure": row.figure_json,
                }, row.difficulty_level
    gen = exgen.generate(ex.provider_ref, seed, difficulty)
    return {"statement": gen.statement, "correction": gen.correction,
            "response_type": gen.response_type, "expected": gen.expected,
            "grading": {**gen.grading, "choices": gen.choices},
            "choices": gen.choices}, level5


def _exercise_competency(db: Session, ex: ExerciseCatalog) -> Competency | None:
    if ex.provider == "deepseek":
        return db.get(Competency, ex.provider_ref.split(":", 1)[1])
    link = db.query(ExerciseCompetency).filter_by(exercise_id=ex.id).first()
    return db.get(Competency, link.competency_id) if link else None


def generate_assessment(db: Session, assessment: Assessment,
                        exercise_ids: list[str], font_size: int = 9) -> dict:
    """Génère toutes les copies. Retourne le rapport de génération."""
    school_class = db.get(SchoolClass, assessment.class_id)
    students = [s for s in school_class.students if s.active]
    catalog = {e.id: e for e in db.query(ExerciseCatalog).filter(
        ExerciseCatalog.id.in_(exercise_ids)).all()}
    ordered = [catalog[i] for i in exercise_ids if i in catalog]
    if not ordered:
        raise ValueError("Aucun exercice sélectionné")

    out_dir = assessment_dir(assessment.id)
    tpl = doc_templates(db)
    pdf_path = out_dir / "subject_batch.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    manifest = {"assessment_id": assessment.id, "protocol": "MP1", "copies": []}
    warnings: list[str] = []

    base_seed = int(hashlib.sha256(assessment.id.encode()).hexdigest()[:8], 16)
    max_pages = max(1, min(6, assessment.pages_target or 1))
    assessment.duplex = max_pages >= 2
    ex_tpl_font_size = int(tpl["exercise"].get("font_size", font_size))
    math_fs = int(tpl["exercise"].get("math_size", 12))
    capacity = pdfgen.estimate_capacity(max_pages)
    MAX_FILL_ATTEMPTS = 10

    for s_idx, student in enumerate(students):
        seed = base_seed if assessment.personalization_mode == "common" else base_seed + s_idx + 1
        level = _student_level(db, student.id)
        copy = Copy(assessment_id=assessment.id, student_id=student.id, seed=seed)
        db.add(copy)
        db.flush()

        render_items: list[dict] = []
        lessons_added: set[str] = set()
        for seq, ex in enumerate(ordered):
            difficulty = _pick_difficulty(ex.difficulty, assessment.personalization_mode, level)
            try:
                gen, level5 = _build_item(db, ex, seed * 100 + seq, difficulty, warnings)
            except Exception as e:
                warnings.append(f"{ex.title} ({student.llm_pseudonym}) : {e}")
                continue

            # rappel de leçon pour élève fragile (entraînement uniquement, §7.1)
            comp = _exercise_competency(db, ex)
            if (assessment.type == "training" and level <= 4 and comp
                    and comp.id not in lessons_added and len(lessons_added) < 2):
                try:
                    snippet = exercise_gen.ensure_lesson(db, comp, level)
                    render_items.append({"kind": "lesson", "title": snippet.title,
                                         "blocks": snippet.blocks_json or None,
                                         "content": snippet.content_latex,
                                         "example": snippet.example_latex})
                    lessons_added.add(comp.id)
                except Exception as e:
                    warnings.append(f"Rappel {comp.code} indisponible : {e}")

            item = CopyItem(
                copy_id=copy.id, catalog_id=ex.id, sequence=seq,
                difficulty=level5 * 2, response_type=gen["response_type"],
                statement=gen["statement"], correction=gen["correction"],
                expected_json=gen["expected"], grading_json=gen["grading"])
            db.add(item)
            db.flush()
            render_items.append({"kind": "exercise", "item_id": item.id,
                                 "statement": gen["statement"],
                                 "response_type": gen["response_type"],
                                 "choices": gen["choices"], "level5": level5,
                                 "figure": gen.get("figure")})

        # remplissage automatique (§ remplissage) : tant qu'il reste de la
        # place sur les pages_target pages, on ajoute des variantes des
        # exercices déjà sélectionnés — pick_exercise/ensure_bank réutilisent
        # la banque existante et ne déclenchent une génération LLM que si elle
        # est épuisée pour cette compétence/ce niveau.
        running_h = sum(pdfgen.estimate_item_height(
            ri, ex_tpl_font_size, math_fs, tpl["exercise"], tpl["lesson"])
            for ri in render_items)
        fill_seq = len(ordered)
        fill_attempts = 0
        while running_h < capacity and fill_attempts < MAX_FILL_ATTEMPTS:
            ex = ordered[fill_seq % len(ordered)]
            difficulty = _pick_difficulty(ex.difficulty, assessment.personalization_mode, level)
            fill_attempts += 1
            try:
                gen, level5 = _build_item(db, ex, seed * 100 + fill_seq, difficulty, warnings)
            except Exception as e:
                warnings.append(f"{ex.title} ({student.llm_pseudonym}) remplissage : {e}")
                fill_seq += 1
                continue
            render_item = {"kind": "exercise", "statement": gen["statement"],
                          "response_type": gen["response_type"],
                          "choices": gen["choices"], "level5": level5,
                          "figure": gen.get("figure")}
            item_h = pdfgen.estimate_item_height(
                render_item, ex_tpl_font_size, math_fs, tpl["exercise"], tpl["lesson"])
            if running_h + item_h > capacity:
                fill_seq += 1
                continue  # cet item ne rentre pas : on tente une autre variante
            item = CopyItem(
                copy_id=copy.id, catalog_id=ex.id, sequence=fill_seq,
                difficulty=level5 * 2, response_type=gen["response_type"],
                statement=gen["statement"], correction=gen["correction"],
                expected_json=gen["expected"], grading_json=gen["grading"])
            db.add(item)
            db.flush()
            render_item["item_id"] = item.id
            render_items.append(render_item)
            running_h += item_h
            fill_seq += 1

        pages_meta, page_rows = [], []
        for p in range(max_pages):
            page = DocumentPage(copy_id=copy.id, page_no=p + 1,
                                side="recto" if p % 2 == 0 else "verso")
            db.add(page)
            db.flush()
            page.qr_payload = sign_page(page.id)
            pages_meta.append({"page_id": page.id, "payload": page.qr_payload})
            page_rows.append(page)

        zones = pdfgen.render_copy(
            c, student_name=f"{student.last_name} {student.first_name}",
            class_name=school_class.name, title=assessment.title,
            assessment_type=assessment.type, items=render_items,
            pages_meta=pages_meta, font_size=font_size, tpl=tpl)

        used_pages = max((z["page_index"] for z in zones), default=0) + 1
        if used_pages > max_pages:
            warnings.append(
                f"Débordement copie {student.llm_pseudonym} : {used_pages} pages "
                f"pour une cible de {max_pages}")
        copy.total_pages = used_pages
        for extra in page_rows[used_pages:]:
            db.delete(extra)

        zone_rows = []
        for z in zones:
            zr = ResponseZone(page_id=z["page_id"], item_id=z["item_id"], type=z["type"],
                              x_pt=z["x_pt"], y_pt=z["y_pt"], w_pt=z["w_pt"], h_pt=z["h_pt"],
                              meta_json=z["meta"])
            db.add(zr)
            db.flush()
            zone_rows.append((z, zr))

        manifest["copies"].append({
            "copy_id": copy.id, "student_pseudonym": student.llm_pseudonym,
            "seed": seed, "pages": [
                {"page_id": p["page_id"], "page_no": i + 1}
                for i, p in enumerate(pages_meta[:used_pages])],
            "zones": [{"zone_id": zr.id, **{k: z[k] for k in
                       ("item_id", "page_id", "type", "x_pt", "y_pt", "w_pt", "h_pt")},
                       "meta": z["meta"]} for z, zr in zone_rows],
        })

    c.save()
    pdfgen.write_manifest(str(out_dir / "copy_manifest.json"), manifest)
    report = {"copies": len(students), "exercises_per_copy": len(ordered),
              "pages_target": max_pages, "warnings": warnings,
              "estimated_mathpix_calls": sum(
                  1 for _ in students for e in ordered
                  if not e.response_type.startswith("qcm"))}
    pdfgen.write_manifest(str(out_dir / "generation_report.json"), report)

    db.add(FileObject(owner_type="assessment", owner_id=assessment.id,
                      storage_path=str(pdf_path), mime="application/pdf",
                      size=pdf_path.stat().st_size))
    assessment.status = "generated"
    db.commit()
    return report

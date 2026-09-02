"""Onglet Exercices (Indigo) — API admin-only.

Copie/adaptation d'exercices d'un manuel réel : sélection de pages →
OCR/segmentation/CV/Gemini (services.indigo) → brouillons éditables → validation.
Réservé au rôle admin (comme l'onglet Données) : absent des builds
utilisateur/correcteur. Les manuels PDF restent locaux à l'instance admin.
"""
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import (Competency, CompetencyFramework, GeneratedExercise,
                      IndigoExercise, IndigoExtraction, User)
from ..services import indigo, indigo_index, indigo_llm, indigo_manual

router = APIRouter(prefix="/api/indigo", tags=["indigo"],
                   dependencies=[Depends(require_role("admin"))])


# ------------------------------------------------------------------ manuels

@router.get("/manuals")
def manuals(grade_level: str = "3e", db: Session = Depends(get_db)):
    out = {}
    for which in ("eleve", "prof"):
        path = indigo_manual.manual_path(grade_level, which)
        out[which] = {"available": path is not None,
                      "pages": indigo_manual.page_count(grade_level, which) if path else 0}
    return {"grade_level": grade_level, "manuals": out}


@router.get("/manual/page.png")
def manual_page(which: str, index: int, grade_level: str = "3e"):
    if which not in ("eleve", "prof"):
        raise HTTPException(400, "which doit valoir 'eleve' ou 'prof'")
    doc = indigo_manual.open_doc(grade_level, which)
    if doc is None:
        raise HTTPException(404, f"Manuel {which} {grade_level} introuvable "
                                 "(reste local à l'instance admin)")
    if not (0 <= index < doc.page_count):
        raise HTTPException(404, "Page hors limites")
    png = indigo_manual.render_preview_png(doc, index)
    return StreamingResponse(io.BytesIO(png), media_type="image/png",
                             headers={"Cache-Control": "max-age=3600"})


# ------------------------------------------------------------- compétences 3e

@router.get("/competencies")
def competencies(grade_level: str = "3e", db: Session = Depends(get_db)):
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade_level).first()
    if fw is None:
        return {"grade_level": grade_level, "competencies": []}
    rows = (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.order_index).all())
    return {"grade_level": grade_level,
            "competencies": [{"id": c.id, "code": c.code, "short_id": c.short_id,
                              "label": c.label, "domain_code": c.domain_code,
                              "domain_name": c.domain_name, "chapter_code": c.chapter_code,
                              "chapter_name": c.chapter_name} for c in rows]}


# ------------------------------------------------------------ fournisseur LLM

class LlmProviderIn(BaseModel):
    provider: str


@router.get("/llm-provider")
def get_llm_provider(db: Session = Depends(get_db)):
    """Fournisseur LLM courant des 3 étapes Indigo (toggle de l'onglet)."""
    return {"provider": indigo_llm.get_provider(db),
            "options": list(indigo_llm.PROVIDERS),
            "offline": indigo_llm.offline(db)}


@router.post("/llm-provider")
def set_llm_provider(body: LlmProviderIn, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    """Bascule Anthropic ⇄ DeepSeek pour découpage + génération + vérification."""
    try:
        prov = indigo_llm.set_provider(db, body.provider, updated_by=user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"provider": prov, "offline": indigo_llm.offline(db)}


# ------------------------------------------------------------------ index manuel

@router.get("/index")
def get_index(grade_level: str = "3e", db: Session = Depends(get_db)):
    """État de l'index + couverture par compétence (pages et numéros repérés).

    C'est ce que l'assistant AUTOMATIQUE affiche avant de lancer : l'admin voit
    ce qui sera extrait, et surtout ce qui manque, au lieu de le découvrir dans
    le journal une fois l'extraction payée."""
    return indigo_index.coverage(db, grade_level)


@router.post("/index")
def build_index(grade_level: str = "3e", db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    """Lance (ou reprend) l'indexation du manuel dans la file de fond."""
    ext = indigo.create_index_run(db, grade_level, created_by=user.id)
    return indigo.extraction_out(ext)


# ------------------------------------------------------------------ extractions

class TargetIn(BaseModel):
    competency_id: str
    # nouveau format simplifié : plages saisies « 34-40 » (pages, 1-based PDF) et
    # « 34-67 » (numéros d'exercices, bornes incluses). Développées côté service.
    eleve_page_range: str = ""
    prof_page_range: str = ""
    number_range: str = ""
    # ancien format (listes d'index 0-based) — encore accepté pour rétrocompat
    eleve_pages: list[int] = []
    prof_pages: list[int] = []

    def has_eleve(self) -> bool:
        return bool(self.eleve_pages) or bool(self.eleve_page_range.strip())


class ExtractionIn(BaseModel):
    grade_level: str = "3e"
    targets: list[TargetIn]


@router.post("/extractions")
def create_extraction(body: ExtractionIn, db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    targets = [t.model_dump() for t in body.targets if t.has_eleve()]
    if not targets:
        raise HTTPException(422, "Renseigne au moins une compétence avec une plage de pages élève")
    ext = indigo.create_extraction(db, body.grade_level, targets, created_by=user.id)
    return indigo.extraction_out(ext)


class AutoExtractionIn(BaseModel):
    grade_level: str = "3e"
    competency_ids: list[str]


@router.post("/extractions/auto")
def create_auto_extraction(body: AutoExtractionIn, db: Session = Depends(get_db),
                           user: User = Depends(require_role("admin"))):
    """Extraction SANS saisie de plages : les pages et les numéros viennent de
    l'index du manuel. Une compétence que l'index ne couvre pas est refusée avec
    son nom — jamais lancée « à vide » (elle produirait zéro exercice sans dire
    pourquoi)."""
    targets, missing = indigo_index.resolve_targets(db, body.grade_level,
                                                    body.competency_ids)
    if not targets:
        raise HTTPException(422,
                            "Aucune compétence couverte par l'index"
                            + (f" ({', '.join(missing)})" if missing else "")
                            + ". Lance l'indexation du manuel, ou saisis les "
                              "plages à la main.")
    ext = indigo.create_extraction(db, body.grade_level, targets, created_by=user.id)
    out = indigo.extraction_out(ext)
    out["skipped"] = missing
    return out


@router.get("/extractions")
def list_extractions(db: Session = Depends(get_db)):
    rows = (db.query(IndigoExtraction).order_by(IndigoExtraction.created_at.desc())
            .limit(30).all())
    return [indigo.extraction_out(e) for e in rows]


@router.get("/extractions/{extraction_id}")
def get_extraction(extraction_id: str, db: Session = Depends(get_db)):
    ext = db.get(IndigoExtraction, extraction_id)
    if ext is None:
        raise HTTPException(404, "Extraction introuvable")
    return indigo.extraction_out(ext)


@router.post("/extractions/{extraction_id}/dismiss")
def dismiss_extraction(extraction_id: str, db: Session = Depends(get_db)):
    """Masque le bandeau d'une extraction terminée/en échec (croix de fermeture)."""
    ext = db.get(IndigoExtraction, extraction_id)
    if ext is None:
        raise HTTPException(404, "Extraction introuvable")
    indigo.dismiss_extraction(db, ext)
    return indigo.extraction_out(ext)


# ------------------------------------------------------------------ exercices

@router.get("/summary")
def summary(grade_level: str = "3e", db: Session = Depends(get_db)):
    """Arbre Domaine > Chapitre > Compétence avec compteurs séparés.

    Les exercices sont rattachés à une compétence. Les problèmes sont
    transverses et agrégés une seule fois au niveau du chapitre ; les mêmes
    compteurs de chapitre sont renvoyés sur chaque ligne afin que l'UI puisse
    les afficher dans des cellules fusionnées.
    """
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade_level).first()
    if fw is None:
        return []
    rows = (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.order_index).all())
    by_id = {c.id: c for c in rows}
    chapter_key = lambda c: f"{c.domain_code}\0{c.chapter_code}"
    work: dict[str, dict] = {}
    problem_work: dict[str, dict] = {}
    for ex in db.query(IndigoExercise).filter(IndigoExercise.competency_id.in_(list(by_id))).all():
        status = "validated" if ex.status == "validated" else "draft"
        comp = by_id[ex.competency_id]
        if ex.badge_type in ("probleme", "enigme"):
            problem_work.setdefault(chapter_key(comp), {"draft": 0, "validated": 0})[status] += 1
        else:
            work.setdefault(ex.competency_id, {"draft": 0, "validated": 0})[status] += 1
    pub: dict[str, int] = {}
    problem_pub: dict[str, int] = {}
    published = (db.query(GeneratedExercise)
                 .filter(GeneratedExercise.source == "indigo",
                         GeneratedExercise.competency_id.in_(list(by_id))).all())
    for ex in published:
        comp = by_id[ex.competency_id]
        if ex.kind == "probleme":
            key = chapter_key(comp)
            problem_pub[key] = problem_pub.get(key, 0) + 1
        else:
            pub[ex.competency_id] = pub.get(ex.competency_id, 0) + 1
    out = []
    for c in rows:
        w = work.get(c.id, {"draft": 0, "validated": 0})
        p = pub.get(c.id, 0)
        pw = problem_work.get(chapter_key(c), {"draft": 0, "validated": 0})
        pp = problem_pub.get(chapter_key(c), 0)
        total = w["draft"] + w["validated"]
        done = total > 0 and w["draft"] == 0 and p > 0 and p >= w["validated"]
        out.append({"competency_id": c.id, "short_id": c.short_id, "label": c.label,
                    "domain_code": c.domain_code, "domain_name": c.domain_name,
                    "chapter_code": c.chapter_code, "chapter_name": c.chapter_name,
                    "draft": w["draft"], "validated": w["validated"],
                    "published": p, "done": done,
                    "problem_draft": pw["draft"],
                    "problem_validated": pw["validated"],
                    "problem_published": pp})
    return out


@router.post("/publish")
def publish(force: bool = False, db: Session = Depends(get_db)):
    """Bake les exercices validés sur le volume persistant + rafraîchit la
    banque. Admin only. `force` autorise une publication vide (remise à zéro
    explicite) — refusée par défaut, elle effacerait le contenu déjà publié."""
    try:
        return indigo.publish(db, force=force)
    except indigo.PublishRefused as e:
        raise HTTPException(409, str(e))


@router.get("/published")
def published_count(db: Session = Depends(get_db)):
    """Exercices publiés + en banque, et surtout D'OÙ ils viennent : volume
    (publication de cette instance, persistante) ou image (contenu livré)."""
    status = indigo.published_status()
    seeded = db.query(GeneratedExercise).filter_by(source="indigo").count()
    return {"published": status["count"], "seeded": seeded,
            "source": status["source"], "on_volume": status["on_volume"],
            "generated_at": status["generated_at"]}


@router.get("/export")
def export_published():
    """Archive ZIP du contenu publié, à décompresser dans
    `backend/app/data/indigo/` du dépôt puis à commiter : c'est ce qui livre
    ces exercices à TOUS les déploiements au build suivant."""
    blob, filename = indigo.export_bundle()
    if not blob:
        raise HTTPException(404, "Aucun exercice publié à exporter")
    return StreamingResponse(
        io.BytesIO(blob), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/exercises")
def list_exercises(competency_id: str | None = None, extraction_id: str | None = None,
                   status: str | None = None, db: Session = Depends(get_db)):
    rows = indigo.list_exercises(db, competency_id, extraction_id, status)
    return [indigo.exercise_out(db, ex) for ex in rows]


@router.delete("/exercises")
def delete_exercises(competency_id: str, db: Session = Depends(get_db)):
    """Supprime TOUS les exercices d'une compétence (bouton « Tout supprimer » de
    l'onglet Exercices). `competency_id` est REQUIS (pas de suppression globale
    accidentelle). La confirmation est portée par l'UI."""
    n = indigo.delete_exercises_for_competency(db, competency_id)
    return {"deleted": n}


@router.get("/exercises/{exercise_id}")
def get_exercise(exercise_id: str, db: Session = Depends(get_db)):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    return indigo.exercise_out(db, ex)


class ExercisePatch(BaseModel):
    statement: str | None = None
    response_type: str | None = None
    expected: dict | None = None
    grading_json: dict | None = None
    correction_solution: str | None = None
    correction_guide: str | None = None
    badge_type: str | None = None
    difficulty: int | None = None
    calculator: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    bareme_points: float | None = None


@router.patch("/exercises/{exercise_id}")
def patch_exercise(exercise_id: str, body: ExercisePatch, db: Session = Depends(get_db)):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    indigo.update_exercise(db, ex, patch)
    return indigo.exercise_out(db, ex)


class CropNudge(BaseModel):
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0


class FigureBoxIn(BaseModel):
    x0: int
    y0: int
    x1: int
    y1: int


class FigureEditIn(BaseModel):
    """Géométrie exprimée dans le raster source à RASTER_DPI.

    Le navigateur travaille donc dans le même repère que le pipeline OCR : il
    peut agrandir un crop trop serré sans extrapoler depuis l'image déjà coupée.
    """
    crop: FigureBoxIn
    masks: list[FigureBoxIn] = Field(default_factory=list)


@router.post("/exercises/{exercise_id}/figure")
def nudge_figure(exercise_id: str, body: CropNudge, db: Session = Depends(get_db)):
    """Ajuste le cadrage de la FIGURE (schéma/dessin) de l'énoncé — seul crop
    éditable (l'extrait complet n'est qu'une référence)."""
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    indigo.nudge_figure(db, ex, body.model_dump())
    return indigo.exercise_out(db, ex)


@router.post("/exercises/{exercise_id}/figure/edit")
def edit_figure(exercise_id: str, body: FigureEditIn, db: Session = Depends(get_db)):
    """Remplace précisément le cadrage et la liste des caches blancs."""
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    try:
        indigo.edit_figure(db, ex, body.crop.model_dump(),
                           [m.model_dump() for m in body.masks])
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    return indigo.exercise_out(db, ex)


@router.post("/exercises/{exercise_id}/figure/add")
def add_figure(exercise_id: str, db: Session = Depends(get_db)):
    """Ajoute une image à l'énoncé depuis sa page PDF source, y compris quand
    le moteur n'avait détecté aucun besoin de figure."""
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    try:
        indigo.add_figure(db, ex)
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    return indigo.exercise_out(db, ex)


@router.delete("/exercises/{exercise_id}/figure")
def delete_figure(exercise_id: str, db: Session = Depends(get_db)):
    """Supprime l'image (figure) de l'énoncé."""
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    indigo.remove_figure(db, ex)
    return indigo.exercise_out(db, ex)


class RegenerateIn(BaseModel):
    ids: list[str]


@router.post("/exercises/regenerate")
def regenerate_exercises(body: RegenerateIn, db: Session = Depends(get_db),
                         user: User = Depends(require_role("admin"))):
    """Régénère les exercices sélectionnés depuis l'OCR stocké, avec le prompt et
    le fournisseur LLM ACTUELS (le prompt a pu changer). Lot borné pour éviter des
    requêtes interminables."""
    ids = [i for i in (body.ids or []) if i]
    if not ids:
        raise HTTPException(422, "Aucun exercice sélectionné")
    if len(ids) > 30:
        raise HTTPException(422, "Trop d'exercices d'un coup (max 30) — régénère par lots")
    return indigo.regenerate_exercises(db, ids)


@router.post("/exercises/{exercise_id}/validate")
def validate_exercise(exercise_id: str, db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    indigo.validate_exercise(db, ex, user.id)
    return indigo.exercise_out(db, ex)


@router.delete("/exercises/{exercise_id}")
def delete_exercise(exercise_id: str, db: Session = Depends(get_db)):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    indigo.delete_exercise(db, ex)
    return {"ok": True}


@router.get("/exercises/{exercise_id}/crop.png")
def exercise_crop(exercise_id: str, db: Session = Depends(get_db)):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None or not ex.crop_path:
        raise HTTPException(404, "Crop introuvable")
    path = indigo.crop_abs_path(ex.crop_path)
    if not path.exists():
        raise HTTPException(404, "Fichier de crop absent")
    return FileResponse(path, media_type="image/png")


@router.get("/exercises/{exercise_id}/figure.png")
def exercise_figure(exercise_id: str, db: Session = Depends(get_db)):
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None or not ex.figure_path:
        raise HTTPException(404, "Figure introuvable")
    path = indigo.crop_abs_path(ex.figure_path)
    if not path.exists():
        raise HTTPException(404, "Fichier de figure absent")
    return FileResponse(path, media_type="image/png")


@router.get("/exercises/{exercise_id}/figure/source.png")
def exercise_figure_source(exercise_id: str, db: Session = Depends(get_db)):
    """Page PDF élève originale, au DPI de travail, pour le recadrage visuel.

    Elle est volontairement distincte de ``figure.png`` : même après un crop
    Mistral trop agressif, l'éditeur dispose encore de tous les pixels source.
    """
    ex = db.get(IndigoExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercice introuvable")
    box = ex.figure_box_json or ex.crop_box_json or {}
    page_index = int(box.get("page_index", ex.source_page))
    doc = indigo_manual.open_doc(ex.grade_level, "eleve")
    if doc is None:
        raise HTTPException(404, "Manuel élève introuvable")
    if not (0 <= page_index < doc.page_count):
        raise HTTPException(404, "Page source hors limites")
    png = indigo_manual.encode_png(indigo_manual.raster_page(doc, page_index))
    return StreamingResponse(io.BytesIO(png), media_type="image/png",
                             headers={"Cache-Control": "private, max-age=3600"})

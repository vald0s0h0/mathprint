"""Assistant « Créer mon sujet » (§3.1 bis) : le professeur compose LUI-MÊME
son sujet, exercice par exercice, page par page, colonne par colonne.

Différences avec la pipeline automatique (services.generation) :

  • aucun choix d'exercice n'est fait par la plateforme — le plan (quelles
    cartes, dans quelle colonne de quelle page, dans quel ordre) est celui
    enregistré par l'assistant dans `assessment.blueprint_json["variants"]` ;
  • pas de sujet individuel : uniquement des sujets COMMUNS. Les variantes
    existent pour deux raisons bien distinctes, jamais mélangées —
      « anti-triche » : N sujets équivalents distribués en tourniquet aux
        voisins de table ;
      « par niveau » : exactement 3 variantes (facile/moyen/difficile),
        attribuées d'après le niveau de l'élève (StudentLevel, échelle 1-10) ;
  • les guides (rappels d'auto-correction attachés à chaque exercice) sont
    pilotés globalement par le sujet : à la correction seulement (overlay),
    imprimés dès le sujet pour les élèves de niveau 1 à 4, ou supprimés (cf.
    pdfgen.GUIDES_*).

Le plan étant figé, la mise en page l'est aussi : c'est `pdfgen.render_copy`
avec son argument `placement` qui pose chaque carte dans SA colonne, sans
réordonnancement ni remplissage automatique.
"""
import hashlib
import logging

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..models import (
    Assessment, Competency, Copy, DocumentPage, FileObject, GeneratedExercise,
    Job, ResponseZone, SchoolClass, StudentLevel,
)
from . import exercise_gen, generation, pdfgen, scoring
from .runtime_settings import doc_templates
from .security import sign_page

logger = logging.getLogger(__name__)

# Variantes par niveau : 3, toujours les mêmes clés, dans cet ordre. Le seuil
# Le seuil de niveau <= 4 sur 10 est le même que celui qui déclenche
# l'impression des guides — c'est la même population d'élèves.
LEVEL_KEYS = ("facile", "moyen", "difficile")
LEVEL_LABELS = {"facile": "Facile", "moyen": "Moyen", "difficile": "Difficile"}
GUIDES_PRINT_MAX_LEVEL = 4
MAX_VARIANTS = 6            # anti-triche : au-delà, plus personne ne s'y retrouve

# Modes de guide exposés par l'assistant, et leur traduction pdfgen. Le mode
# "print_fragile" ne se traduit pas en un mode unique : la GÉOMÉTRIE est celle
# du mode overlay pour tout le monde (une variante = une mise en page, quel que
# soit l'élève), seuls les élèves de niveau 1 à 4 reçoivent l'encre du guide.
GUIDE_MODES = ("overlay", "print_fragile", "none")


def guides_for_student(mode: str, student_level: int) -> str:
    """Mode pdfgen des cartes de la copie d'un élève (cf. pdfgen.GUIDES_*)."""
    if mode == "none":
        return pdfgen.GUIDES_NONE
    if mode == "print_fragile" and student_level <= GUIDES_PRINT_MAX_LEVEL:
        return pdfgen.GUIDES_PRINT
    return pdfgen.GUIDES_OVERLAY


def variant_for_student(blueprint: dict, student_level: int, student_index: int) -> int:
    """Index de la variante servie à un élève.

    • « par niveau » : la variante dont la clé correspond à son niveau
      (facile <= 4, moyen 5-7, difficile >= 8) ; repli sur la variante médiane
      si cette clé n'a pas été composée.
    • « anti-triche » : tourniquet sur le rang de l'élève dans la classe —
      deux voisins de liste (donc, en pratique, de table) n'ont jamais le même
      sujet tant qu'il y a plus d'une variante.
    """
    variants = blueprint.get("variants") or []
    if len(variants) <= 1:
        return 0
    if blueprint.get("variant_kind") == "level":
        want = ("facile" if student_level <= GUIDES_PRINT_MAX_LEVEL
                else "moyen" if student_level <= 7 else "difficile")
        for i, v in enumerate(variants):
            if v.get("key") == want:
                return i
        return len(variants) // 2
    return student_index % len(variants)


# ------------------------------------------------------------------ le pool

def _competency_label(comp: Competency) -> str:
    """Libellé complet : un libellé de compétence isolé (« Automatismes ») ne
    suffit jamais à savoir de quoi il s'agit (cf. modèle Competency)."""
    head = comp.short_id or comp.code
    return f"{head} · {comp.label}" if head else comp.label


def chapter_competency_ids(db: Session, competency_ids: list[str]) -> dict[str, list[str]]:
    """Compétences appartenant AU MÊME CHAPITRE que celles sélectionnées,
    groupées par code de chapitre.

    C'est la clé des problèmes : un exercice est rattaché à une compétence
    précise, un problème à un CHAPITRE entier. Une seule compétence cochée doit
    donc suffire à proposer tous les problèmes de son chapitre."""
    comps = db.query(Competency).filter(Competency.id.in_(competency_ids)).all()
    out: dict[str, list[str]] = {}
    for comp in comps:
        if not comp.chapter_code or comp.chapter_code in out:
            continue
        siblings = (db.query(Competency)
                    .filter_by(framework_id=comp.framework_id,
                               chapter_code=comp.chapter_code)
                    .order_by(Competency.order_index).all())
        out[comp.chapter_code] = [c.id for c in siblings]
    return out


def _card(db: Session, row: GeneratedExercise, comp: Competency, tpl: dict,
          font_size: int, math_fs: int) -> dict:
    """Une entrée du pool, telle que l'assistant l'affiche et la mesure."""
    indigo = (row.raw_extract_json or {}).get("indigo") or {}
    heights = {}
    for mode in (pdfgen.GUIDES_OVERLAY, pdfgen.GUIDES_NONE):
        shape = generation.render_shape(row, mode)
        heights[mode] = pdfgen.estimate_item_height(
            shape, font_size, math_fs, tpl["exercise"], tpl["lesson"])
    return {
        "id": row.id,
        "competency_id": row.competency_id,
        "competency_label": _competency_label(comp),
        "chapter_code": comp.chapter_code, "chapter_name": comp.chapter_name,
        "statement": row.statement,
        "response_type": row.response_type,
        "difficulty": row.difficulty_level,
        "kind": row.kind, "source": row.source,
        "badge_type": indigo.get("badge_type") or "",
        "title": indigo.get("title") or "",
        "calculator": indigo.get("calculator") or "autorisee",
        "source_number": indigo.get("source_number") or "",
        "has_figure": bool(row.figure_json),
        "bareme_points": scoring.item_bareme(row.grading_json, row.response_type),
        # hauteurs RÉELLES (points PDF) des deux mises en page possibles : c'est
        # la mesure de pdfgen, pas une estimation refaite côté navigateur.
        "height_pt": round(heights[pdfgen.GUIDES_OVERLAY], 1),
        "height_pt_no_guide": round(heights[pdfgen.GUIDES_NONE], 1),
    }


def pool(db: Session, competency_ids: list[str], pages: int = 1) -> dict:
    """Exercices et problèmes disponibles pour les compétences cochées.

    Deux listes SÉPARÉES, parce que ce ne sont pas les mêmes objets :
      • `exercises` : rattachés à une compétence précise, filtrés sur les
        compétences cochées ;
      • `problems` : rattachés à un chapitre entier — tous les problèmes et
        énigmes des chapitres touchés par les compétences cochées, même ceux
        d'une compétence voisine non cochée.
    """
    tpl = doc_templates(db)
    font_size = int(tpl["exercise"].get("font_size", 9))
    math_fs = int(tpl["exercise"].get("math_size", 12))

    by_chapter = chapter_competency_ids(db, competency_ids)
    all_ids = set(competency_ids) | {cid for ids in by_chapter.values() for cid in ids}
    comps = {c.id: c for c in db.query(Competency).filter(Competency.id.in_(all_ids)).all()}
    rows = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id.in_(all_ids),
                    GeneratedExercise.status == "active")
            .order_by(GeneratedExercise.difficulty_level).all())

    selected = set(competency_ids)
    exercises, problems = [], []
    for row in rows:
        comp = comps.get(row.competency_id)
        if comp is None:
            continue
        is_problem = row.kind == "probleme"
        if not is_problem and row.competency_id not in selected:
            continue        # exercice d'une compétence voisine non cochée
        (problems if is_problem else exercises).append(_card(db, row, comp, tpl,
                                                             font_size, math_fs))
    exercises.sort(key=lambda e: (e["competency_label"], e["difficulty"]))
    problems.sort(key=lambda e: (e["chapter_code"], e["difficulty"]))
    return {"exercises": exercises, "problems": problems,
            "chapters": [{"code": code, "name": comps[ids[0]].chapter_name}
                         for code, ids in by_chapter.items() if ids],
            "metrics": pdfgen.column_metrics(pages)}


# --------------------------------------------------------------- génération

def _student_level(db: Session, student_id: str) -> int:
    lvl = (db.query(StudentLevel).filter_by(student_id=student_id)
           .order_by(StudentLevel.valid_from.desc()).first())
    return lvl.level if lvl else 5


def _ordered_slots(variant: dict, max_pages: int = 6) -> list[dict]:
    """Cartes d'une variante, dans l'ordre où reportlab doit les dessiner :
    page, puis colonne, puis rang dans la colonne. Le canvas est séquentiel —
    une page close ne se rouvre pas.

    Les coordonnées sont bornées ici et pas seulement à l'enregistrement du
    plan : une page hors cible ferait improviser à pdfgen un page_id
    « overflow-N » sans ligne DocumentPage (violation de clé étrangère) ni QR
    signé (page illisible au scan)."""
    slots = []
    for rank, it in enumerate(variant.get("items") or []):
        slots.append({"exercise_id": it.get("exercise_id"),
                      "page": max(0, min(max_pages - 1, int(it.get("page", 0)))),
                      "col": 1 if int(it.get("col", 0)) else 0,
                      "rank": int(it.get("rank", rank))})
    slots.sort(key=lambda s: (s["page"], s["col"], s["rank"]))
    return slots


def generate_manual_job(db: Session, assessment: Assessment, job: Job | None = None,
                        font_size: int = 9) -> dict:
    """Génère les copies d'un sujet composé à la main. Même contrat de sortie
    que services.generation.generate_assessment_job (rapport + fichiers)."""
    blueprint = assessment.blueprint_json or {}
    variants = blueprint.get("variants") or []
    if not variants or not any(v.get("items") for v in variants):
        raise ValueError("Aucun exercice placé : composez au moins une page.")
    guide_mode = blueprint.get("guides", "overlay")

    school_class = db.get(SchoolClass, assessment.class_id)
    students = [s for s in school_class.students if s.active]
    if not students:
        raise ValueError("Aucun élève actif dans cette classe.")

    # une seule lecture de la banque pour tout le sujet
    wanted = {s["exercise_id"] for v in variants for s in _ordered_slots(v)}
    rows = {r.id: r for r in db.query(GeneratedExercise)
            .filter(GeneratedExercise.id.in_(wanted)).all()}
    missing = wanted - set(rows)
    if missing:
        raise ValueError(f"{len(missing)} exercice(s) du plan ont disparu de la "
                         "banque — rouvrez l'assistant et recomposez le sujet.")
    comps = {c.id: c for c in db.query(Competency).filter(
        Competency.id.in_({r.competency_id for r in rows.values()})).all()}
    catalog_refs = {cid: exercise_gen.ensure_catalog_ref(db, comp)
                    for cid, comp in comps.items()}

    out_dir = generation.assessment_dir(assessment.id)
    tpl = doc_templates(db)
    pdf_path = out_dir / "subject_batch.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    manifest = {"assessment_id": assessment.id, "protocol": "MP1", "copies": []}
    warnings: list[str] = []

    # cf. services.generation : les 8 hex du hash dépassent l'INTEGER Postgres
    # signé une fois sur deux, d'où le modulo.
    seed_source = str(blueprint.get("duplicate_source_id") or assessment.id)
    base_seed = int(hashlib.sha256(seed_source.encode()).hexdigest()[:8], 16) % 2_000_000_000
    max_pages = max(1, min(6, assessment.pages_target or 1))
    assessment.duplex = max_pages >= 2
    # réserve de pages signées : une colonne trop pleine fait glisser une carte
    # dans la suivante (jamais coupée), donc le plan peut déborder de la cible.
    PAGE_RESERVE = 4
    total_non_qcm = 0

    logger.info("Sujet manuel %s — %s variante(s) « %s », guides : %s, %s élève(s)",
                assessment.id, len(variants),
                blueprint.get("variant_kind", "none"), guide_mode, len(students))

    for s_idx, student in enumerate(students):
        generation._set_progress(
            db, job, round(5 + 90 * s_idx / max(1, len(students))),
            f"Copie {s_idx + 1}/{len(students)}")
        level = _student_level(db, student.id)
        fixed_key = (blueprint.get("duplicate_student_variants") or {}).get(student.id)
        v_idx = next((i for i, v in enumerate(variants)
                      if fixed_key and str(v.get("key")) == str(fixed_key)),
                     variant_for_student(blueprint, level, s_idx))
        variant = variants[v_idx]
        guides = guides_for_student(guide_mode, level)
        # seed = variante (et non élève) : deux élèves de la même variante ont
        # rigoureusement la même copie, c'est la définition d'un sujet commun.
        seed = base_seed + v_idx

        copy = Copy(assessment_id=assessment.id, student_id=student.id, seed=seed,
                    variant_key=str(variant.get("key") or v_idx))
        db.add(copy)
        db.flush()

        render_items, placement = [], []
        for seq, slot in enumerate(_ordered_slots(variant, max_pages), start=1):
            row = rows[slot["exercise_id"]]
            render = generation.build_render_item(
                db, row=row, copy_id=copy.id,
                catalog_id=catalog_refs[row.competency_id].id, seq=seq,
                guides=guides)
            if render is None:
                warnings.append(f"Exercice {row.id[:8]} ignoré (contenu incomplet)")
                continue
            render_items.append(render)
            placement.append((slot["page"], slot["col"]))
            if not row.response_type.startswith("qcm"):
                total_non_qcm += 1

        pages_meta, page_rows = [], []
        for p in range(max_pages + PAGE_RESERVE):
            page = DocumentPage(copy_id=copy.id, page_no=p + 1,
                                side="recto" if p % 2 == 0 else "verso")
            db.add(page)
            db.flush()
            page.qr_payload = sign_page(page.id)
            page.hmac_version = "2"
            pages_meta.append({"page_id": page.id, "payload": page.qr_payload})
            page_rows.append(page)

        zones = pdfgen.render_copy(
            c, student_name=f"{student.last_name} {student.first_name}",
            class_name=school_class.name, title=assessment.title,
            assessment_type=assessment.type, items=render_items,
            pages_meta=pages_meta, font_size=font_size, tpl=tpl,
            placement=placement, min_pages=max_pages)

        used_pages = max(max_pages, max((z["page_index"] for z in zones), default=0) + 1)
        if used_pages > max_pages + PAGE_RESERVE:
            raise ValueError(
                f"Copie {student.llm_pseudonym} : {used_pages} pages nécessaires — "
                f"une colonne du plan est trop chargée, allégez-la ou ajoutez une page.")
        if used_pages > max_pages:
            warnings.append(
                f"Débordement : {used_pages} pages au lieu de {max_pages} — une "
                f"colonne de la variante « {variant.get('label') or variant.get('key')} » "
                "contient plus de cartes qu'elle n'en peut tenir.")
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
            "seed": seed, "variant": copy.variant_key,
            "pages": [{"page_id": p["page_id"], "page_no": i + 1}
                      for i, p in enumerate(pages_meta[:used_pages])],
            "zones": [{"zone_id": zr.id, **{k: z[k] for k in
                       ("item_id", "page_id", "type", "x_pt", "y_pt", "w_pt", "h_pt")},
                       "meta": z["meta"]} for z, zr in zone_rows],
        })

    generation._set_progress(db, job, 96, "Assemblage du PDF…")
    c.save()
    pdfgen.write_manifest(str(out_dir / "copy_manifest.json"), manifest)
    report = {"copies": len(students), "mode": "manual",
              "variants": len(variants), "variant_kind": blueprint.get("variant_kind"),
              "guides": guide_mode, "pages_target": max_pages,
              "warnings": list(dict.fromkeys(warnings)),
              "estimated_mathpix_calls": total_non_qcm}
    pdfgen.write_manifest(str(out_dir / "generation_report.json"), report)

    db.add(FileObject(owner_type="assessment", owner_id=assessment.id,
                      storage_path=str(pdf_path), mime="application/pdf",
                      size=pdf_path.stat().st_size))
    generation._set_progress(db, job, 100, "Terminé")
    return report

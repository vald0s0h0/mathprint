"""Orchestration de la génération d'un sujet (§3, §5.1).

Produit pour une évaluation : copies individuelles (avec seed), instantanés
d'exercices (RM-014), pages avec QR signés, zones de réponse, subject_batch.pdf,
copy_manifest.json et generation_report.json.

Depuis la refonte de l'assistant sujet, l'étape Exercices ne fait plus
choisir des ExerciseCatalog un par un : le professeur coche des compétences
(assessment.blueprint_json["competency_ids"]). Pour chaque élève, cette
sélection est transformée en une liste d'exercices concrets par
services.distribution (priorité selon la courbe de l'oubli, difficulté selon
le mode d'adaptation, mix homogène des types de réponses), piochés dans la
banque generated_exercises (compétence × niveau 1-5, cf. exercise_gen —
générée à la demande seulement si la banque est insuffisante).

generate_assessment_job tourne dans le worker de fond (services.job_worker),
plus dans la requête HTTP : les appels DeepSeek/Claude déclenchés par une
banque manquante n'y bloquent donc plus la connexion du navigateur.

Rappels de leçon : pour un entraînement (jamais en contrôle), chaque élève
reçoit jusqu'à settings.max_lessons_per_copy rappels (lesson_snippets),
insérés avant le premier exercice de la compétence concernée. Les
compétences ciblées viennent de services.distribution.lesson_review_targets
— en priorité le plan post-correction personnalisé (lacunes identifiées par
le LLM à la correction précédente, cf. services.appreciation), à défaut un
repli déterministe sur la courbe de l'oubli (maîtrise faible), à défaut
l'ancien filet de sécurité "élève fragile" (niveau ≤ 4). Jamais deux fois la
même compétence dans une même copie ; un rappel peut en revanche réapparaître
d'un sujet à l'autre pour le même élève tant que la lacune persiste — c'est
voulu (accompagnement personnalisé, pas de nouveauté à tout prix). Chaque
insertion est tracée sur CopyItem.lesson_snippet_id (exercice qui suit
immédiatement le rappel), pour audit/traçabilité.
"""
import hashlib
import logging
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Assessment, Competency, Copy, CopyItem, DocumentPage, FileObject, Job,
    ResponseZone, SchoolClass, StudentLevel,
)
from . import distribution, exercise_gen, forgetting, scoring
from . import pdfgen
from .runtime_settings import doc_templates
from .security import sign_page

logger = logging.getLogger(__name__)


def assessment_dir(assessment_id: str) -> Path:
    d = settings.data_dir / "assessments" / assessment_id / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _student_level(db: Session, student_id: str) -> int:
    lvl = (db.query(StudentLevel).filter_by(student_id=student_id)
           .order_by(StudentLevel.valid_from.desc()).first())
    return lvl.level if lvl else 5


def _set_progress(db: Session, job: Job | None, progress: int, message: str) -> None:
    if job is None:
        return
    job.progress = progress
    job.progress_message = message
    db.commit()


def generate_assessment_job(db: Session, assessment: Assessment,
                            job: Job | None = None, font_size: int = 9) -> dict:
    """Génère toutes les copies à partir des compétences cochées. Retourne le
    rapport de génération. Appelé par le worker de fond (job_worker)."""
    school_class = db.get(SchoolClass, assessment.class_id)
    students = [s for s in school_class.students if s.active]
    # source des exercices choisie dans l'assistant (§ Sésamaths) : "auto"
    # préserve le comportement historique (MathALÉA + DeepSeek), inchangé
    # par défaut pour tout sujet existant sans ce champ
    exercise_source = (assessment.blueprint_json or {}).get("exercise_source", "auto")
    competency_ids = list(dict.fromkeys(
        (assessment.blueprint_json or {}).get("competency_ids") or []))
    competencies = {c.id: c for c in db.query(Competency).filter(
        Competency.id.in_(competency_ids)).all()}
    ordered_ids = [cid for cid in competency_ids if cid in competencies]
    if not ordered_ids:
        raise ValueError("Aucune compétence sélectionnée")
    catalog_refs = {cid: exercise_gen.ensure_catalog_ref(db, competencies[cid])
                    for cid in ordered_ids}
    logger.info("Génération sujet %s — source d'exercices : %s | %s élève(s), "
                "%s compétence(s) : %s", assessment.id, exercise_source,
                len(students), len(ordered_ids),
                ", ".join(competencies[cid].code for cid in ordered_ids))

    out_dir = assessment_dir(assessment.id)
    tpl = doc_templates(db)
    pdf_path = out_dir / "subject_batch.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    manifest = {"assessment_id": assessment.id, "protocol": "MP1", "copies": []}
    warnings: list[str] = []

    # modulo : les 8 hex (32 bits) du hash dépassent une fois sur deux
    # l'INTEGER Postgres signé (max 2^31-1) où Copy.seed est stocké — vu en
    # prod (psycopg2.errors.NumericValueOutOfRange). Marge sous 2^31-1 pour
    # absorber le + student_index de distribution.variant_seed.
    base_seed = int(hashlib.sha256(assessment.id.encode()).hexdigest()[:8], 16) % 2_000_000_000
    max_pages = max(1, min(6, assessment.pages_target or 1))
    assessment.duplex = max_pages >= 2
    ex_tpl_font_size = int(tpl["exercise"].get("font_size", font_size))
    math_fs = int(tpl["exercise"].get("math_size", 12))
    # marge de pages RÉELLES (DocumentPage signées) au-delà de la cible : les
    # exercices obligatoires (une compétence cochée = un exercice, boucle
    # ci-dessous) ne sont jamais soumis au contrôle de capacité qui ne
    # s'applique qu'au remplissage — un contenu obligatoire volumineux peut
    # donc dépasser max_pages. Sans cette réserve, pdfgen.render_copy improvise
    # un page_id "overflow-N" sans contrepartie en base (FK violation à
    # l'insertion des zones) ET sans QR signé (page illisible au scan même si
    # on rattrapait la ligne DocumentPage après coup).
    PAGE_RESERVE = 6
    # plafond d'essais de remplissage PAR PASSE (classiques puis courts) : assez
    # haut pour vraiment remplir des pages (10 était trop bas — un tour de
    # compétences suffisait à l'épuiser, d'où des bas de page vides), la vraie
    # borne étant la stagnation (plus rien ne tient dans la place restante).
    MAX_FILL_ATTEMPTS = 80
    total_non_qcm = 0

    for s_idx, student in enumerate(students):
        _set_progress(db, job, round(5 + 90 * s_idx / max(1, len(students))),
                      f"Copie {s_idx + 1}/{len(students)} — sélection des exercices "
                      f"(banque générée à la demande si besoin)")
        logger.info("Copie %s/%s (%s)", s_idx + 1, len(students), student.llm_pseudonym)
        seed = distribution.variant_seed(base_seed, assessment.personalization_mode, s_idx)
        level = _student_level(db, student.id)
        level5 = distribution.difficulty_level5(assessment.personalization_mode, level)
        target_mix = settings.exercise_kind_mix
        if assessment.personalization_mode == "individual":
            target_mix, level5 = distribution.apply_next_plan(student, target_mix, level5)
        priority = distribution.priority_competencies(db, student.id, ordered_ids)
        due = forgetting.due_competencies(db, student.id)
        lesson_targets = set(distribution.lesson_review_targets(
            priority, student, due, level, assessment.type))

        copy = Copy(assessment_id=assessment.id, student_id=student.id, seed=seed)
        db.add(copy)
        db.flush()

        render_items: list[dict] = []
        lessons_added: set[str] = set()
        kind_counts: dict[str, int] = {}
        # exercices déjà servis dans CETTE copie, par identité de CONTENU et non
        # par id de ligne : deux compétences voisines peuvent avoir en banque le
        # même exercice, l'élève ne doit pas le voir deux fois (cf.
        # distribution.exercise_identity)
        picked_keys: set[str] = set()

        def _add_item(seq: int, comp_id: str, item_seed: int,
                      filler: bool = False) -> bool:
            nonlocal total_non_qcm
            comp = competencies[comp_id]
            try:
                if filler:
                    # petites cartes (un calcul, un QCM court) pour combler les
                    # trous de bas de page : jamais répétées (None = épuisées)
                    rows = exercise_gen.filler_bank_rows(
                        db, comp, level5, source=exercise_source)
                    row = distribution.pick_unused_exercise(
                        rows, item_seed, exclude_keys=picked_keys)
                    if row is None:
                        return False
                else:
                    bank, _ = exercise_gen.bank_rows_near_level(
                        db, comp, level5, source=exercise_source)
                    row = distribution.pick_balanced_exercise(
                        bank, kind_counts, target_mix, item_seed, exclude_keys=picked_keys)
            except Exception as e:
                logger.warning("%s (%s) : %s", comp.code, student.llm_pseudonym, e)
                warnings.append(f"{comp.code} ({student.llm_pseudonym}) : {e}")
                return False

            # une carte de remplissage ne traîne jamais de rappel de leçon :
            # elle est là pour occuper un petit trou, pas pour enseigner
            lesson_snippet_id = None
            if (not filler and comp.id in lesson_targets and comp.id not in lessons_added
                    and len(lessons_added) < settings.max_lessons_per_copy):
                try:
                    snippet = exercise_gen.ensure_lesson(db, comp, level)
                    render_items.append({"kind": "lesson", "title": snippet.title,
                                         "blocks": snippet.blocks_json or None,
                                         "content": snippet.content_latex,
                                         "example": snippet.example_latex})
                    lessons_added.add(comp.id)
                    lesson_snippet_id = snippet.id
                except Exception as e:
                    warnings.append(f"Rappel {comp.code} ({student.llm_pseudonym}) indisponible : {e}")

            identity = distribution.exercise_identity(row)
            picked_keys.add(identity)
            choices = row.grading_json.get("choices", [])
            # barème RÉSOLU (repli compris) figé sur la copie : l'instantané
            # d'un CopyItem (RM-014) doit porter le barème avec lequel le sujet
            # a été composé, pas dépendre d'un repli recalculé à la correction —
            # le jour où la règle de repli change, les copies déjà imprimées ne
            # doivent pas se mettre à valoir autre chose (§ barème).
            grading_json = scoring.with_bareme(row.grading_json, row.response_type)
            item = CopyItem(
                copy_id=copy.id, catalog_id=catalog_refs[comp_id].id, sequence=seq,
                difficulty=row.difficulty_level * 2, response_type=row.response_type,
                statement=row.statement, correction=row.correction,
                expected_json=row.expected_json, grading_json=grading_json,
                lesson_snippet_id=lesson_snippet_id)
            db.add(item)
            db.flush()
            render_items.append({"kind": "exercise", "item_id": item.id,
                                 "statement": row.statement,
                                 "correction": row.correction,
                                 "response_type": row.response_type,
                                 "choices": choices, "level5": row.difficulty_level,
                                 "figure": row.figure_json,
                                 "grading": grading_json,
                                 "inline": bool((row.expected_json or {}).get("inline")),
                                 "_identity": identity,
                                 # les cartes de remplissage ne passent pas par le
                                 # tirage équilibré (kind_counts), donc pas de
                                 # bucket à décrémenter si elles sont retirées —
                                 # sinon on fausserait le mix des exercices classiques
                                 "_bucket": None if filler else distribution.exercise_bucket(row)})
            if not row.response_type.startswith("qcm"):
                total_non_qcm += 1
            return True

        for seq, comp_id in enumerate(priority):
            _add_item(seq, comp_id, seed * 100 + seq)

        # remplissage automatique (§ remplissage) : tant qu'il reste de la
        # place sur les pages_target pages, on repioche dans les compétences
        # cochées (priorité, en boucle) — bank_rows_near_level/ensure_bank ne
        # déclenchent une génération LLM que si la banque est épuisée pour
        # cette compétence/ce niveau.
        # Le critère est le nombre de pages RÉELLEMENT occupées (pdfgen.
        # pages_needed simule le placement en colonnes), pas une somme de
        # hauteurs comparée à une capacité théorique : une carte ne se coupe
        # pas, le bas de colonne perdu faisait déborder d'une page toute copie
        # remplie au plus près. Un item qui ne rentre pas n'arrête PAS la
        # boucle : un plus petit (autre compétence, autre format) peut encore
        # tenir dans la place restante.
        def _heights(items: list[dict]) -> list[float]:
            return [pdfgen.estimate_item_height(
                ri, ex_tpl_font_size, math_fs, tpl["exercise"], tpl["lesson"])
                for ri in items]

        def _pack(items: list[dict]) -> tuple[list[dict], list[float]]:
            """Réordonne les cartes pour un remplissage colonne par colonne
            efficace (First-Fit-Decreasing, cf. pdfgen.pack_reading_order) :
            les grandes cartes d'abord, les petites comblant les bas de colonne,
            au lieu du grand vide laissé par l'ordre de production du LLM. Chaque
            rappel de leçon reste collé à l'exercice qu'il précède (unité
            indissociable). Retourne (cartes réordonnées, leurs hauteurs)."""
            hs = _heights(items)
            units: list[tuple[list[dict], list[float]]] = []
            i = 0
            while i < len(items):
                if items[i].get("kind") == "lesson" and i + 1 < len(items):
                    units.append(([items[i], items[i + 1]], [hs[i], hs[i + 1]]))
                    i += 2
                else:
                    units.append(([items[i]], [hs[i]]))
                    i += 1
            order = pdfgen.pack_reading_order([sum(h) for _, h in units])
            packed_items: list[dict] = []
            packed_h: list[float] = []
            for k in order:
                packed_items.extend(units[k][0])
                packed_h.extend(units[k][1])
            return packed_items, packed_h

        def _rollback(before: int) -> None:
            nonlocal total_non_qcm
            for ri in render_items[before:]:
                if ri.get("item_id"):
                    db.query(CopyItem).filter_by(id=ri["item_id"]).delete()
                    if not ri["response_type"].startswith("qcm"):
                        total_non_qcm -= 1
                    if ri.get("_bucket"):
                        kind_counts[ri["_bucket"]] = max(0, kind_counts.get(ri["_bucket"], 0) - 1)
                    picked_keys.discard(ri.get("_identity"))
            db.flush()
            del render_items[before:]

        def _fill(start_seq: int, *, filler: bool) -> int:
            """Ajoute des exercices en boucle tant qu'ils tiennent dans
            max_pages. `filler`=False remplit au MAXIMUM avec des exercices
            classiques ; =True comble ensuite les trous restants avec des
            petites cartes. Un item qui déborde est retiré (une carte plus
            petite peut encore tenir) ; on s'arrête après quelques tours
            complets sans le moindre ajout (place résiduelle inexploitable)."""
            seq = start_seq
            attempts = stagnant = 0
            stop_stagnant = 2 * max(1, len(priority))
            while attempts < MAX_FILL_ATTEMPTS and stagnant < stop_stagnant:
                comp_id = priority[seq % len(priority)]
                attempts += 1
                before = len(render_items)
                if not _add_item(seq, comp_id, seed * 100 + seq, filler=filler):
                    seq += 1
                    stagnant += 1
                    continue
                # nombre de pages une fois les cartes réordonnées (FFD) : c'est
                # ce placement-là que render_copy réalise en bout de chaîne, donc
                # ce qui décide du débordement — pas l'ordre de production brut,
                # qui gaspille des bas de colonne et remplirait donc moins.
                if pdfgen.pages_needed(_pack(render_items)[1]) > max_pages:
                    _rollback(before)
                    stagnant += 1
                else:
                    stagnant = 0
                seq += 1
            return seq

        if priority:
            # 1) remplir au maximum avec les exercices classiques (grandes cartes) ;
            # 2) combler les trous de bas de page restants avec les cartes courtes.
            next_seq = _fill(len(priority), filler=False)
            _fill(next_seq, filler=True)

        # Ordre DÉFINITIF des cartes : le remplissage colonne par colonne (FFD)
        # est figé ici, une fois toutes les cartes choisies. On renumérote alors
        # les exercices dans l'ordre de LECTURE ainsi obtenu, pour que le badge
        # imprimé et le « Ex. N » de la correction manuelle (routers.scans)
        # restent alignés.
        render_items, _ = _pack(render_items)
        seq_no = 0
        for ri in render_items:
            if ri.get("kind") == "exercise" and ri.get("item_id"):
                seq_no += 1
                db.query(CopyItem).filter_by(id=ri["item_id"]).update({"sequence": seq_no})
        db.flush()

        _set_progress(db, job, round(5 + 90 * (s_idx + 1) / max(1, len(students))),
                     f"Copie {s_idx + 1}/{len(students)} ({student.llm_pseudonym})")

        # pages RÉELLES (signées) créées jusqu'à max_pages + PAGE_RESERVE : le
        # contenu obligatoire peut déborder de la cible, jamais du réservoir
        # (cf. commentaire PAGE_RESERVE ci-dessus) ; les pages en trop sont
        # supprimées ci-dessous une fois le nombre de pages réellement utilisé
        # connu.
        pages_meta, page_rows = [], []
        for p in range(max_pages + PAGE_RESERVE):
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
        if used_pages > max_pages + PAGE_RESERVE:
            # au-delà du réservoir : pdfgen a dû improviser un page_id
            # "overflow-N" sans QR signé (page illisible au scan) — on arrête
            # net plutôt que de produire une copie dont une partie ne sera
            # jamais corrigeable, ou de planter plus loin sur la contrainte FK.
            raise ValueError(
                f"Copie {student.llm_pseudonym} : {used_pages} page(s) nécessaire(s), "
                f"dépasse la réserve ({max_pages + PAGE_RESERVE}) — réduisez le nombre "
                f"de compétences cochées ou augmentez le nombre de pages cible.")
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

    _set_progress(db, job, 96, "Assemblage du PDF…")
    c.save()
    pdfgen.write_manifest(str(out_dir / "copy_manifest.json"), manifest)
    # dédup en préservant l'ordre : un manuel Sésamath manquant produit sinon le
    # même message pour chaque élève × compétence × tentative de remplissage.
    report = {"copies": len(students), "competencies": len(ordered_ids),
              "pages_target": max_pages, "warnings": list(dict.fromkeys(warnings)),
              "estimated_mathpix_calls": total_non_qcm}
    pdfgen.write_manifest(str(out_dir / "generation_report.json"), report)

    db.add(FileObject(owner_type="assessment", owner_id=assessment.id,
                      storage_path=str(pdf_path), mime="application/pdf",
                      size=pdf_path.stat().st_size))
    _set_progress(db, job, 100, "Terminé")
    return report

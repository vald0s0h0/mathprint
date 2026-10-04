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

Les guides d'auto-correction sont attachés directement aux exercices dans
GeneratedExercise.correction ; aucun contenu de leçon séparé n'est injecté.
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
from . import blocks, distribution, exercise_gen, scoring, student_history
from . import statement as statement_mod
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


def indigo_display(row) -> tuple[str, str, bool]:
    """(énoncé affiché, statut calculette, est-un-problème) d'une ligne de
    banque. Le titre concret des problèmes Indigo précède leur énoncé, EN GRAS
    (balisage `**` de services/blocks, lu à l'identique par le PDF et le web),
    sans rubrique éditoriale. Le statut calculette est dessiné en icône par
    pdfgen. Les types et tags du manuel restent dans les métadonnées."""
    meta = (row.raw_extract_json or {}).get("indigo") if row.source == "indigo" else None
    if not meta:
        return row.statement, "autorisee", False
    calc = meta.get("calculator") or "autorisee"
    if meta.get("badge_type") not in ("probleme", "enigme"):
        return row.statement, calc, False
    title = blocks.strip_bold(meta.get("title") or "").strip()
    return f"**{title}**\n{row.statement}" if title else row.statement, calc, True


def render_shape(row, guides: str = pdfgen.GUIDES_INCLUDE) -> dict:
    """Carte pdfgen d'une ligne de banque, SANS aucune écriture en base : tout
    ce dont dépendent la mise en page et la mesure de hauteur. `item_id` (et
    `part_item_ids` d'un composite) sont ajoutés par `build_render_item` quand
    les CopyItem existent ; le mode manuel de l'assistant « Créer un sujet », lui, s'en sert tel
    quel pour mesurer la hauteur des cartes proposées au professeur.

    Les parties d'un composite sont dans grading["parts"] : la carte reste
    unique à l'impression, les CopyItem sont créés une par partie."""
    # barème RÉSOLU (repli compris) : l'instantané d'un CopyItem (RM-014) doit
    # porter le barème avec lequel le sujet a été composé, pas dépendre d'un
    # repli recalculé à la correction.
    grading_json = scoring.with_bareme(row.grading_json, row.response_type)
    disp_statement, calc, is_probleme = indigo_display(row)
    common = {"kind": "exercise", "statement": disp_statement,
              "correction": row.correction, "level3": row.difficulty_level,
              "calc": calc, "is_probleme": is_probleme, "figure": row.figure_json,
              "guides": guides}
    if row.response_type == "composite":
        parts = (row.expected_json or {}).get("parts") \
            or (grading_json or {}).get("parts") or []
        return {**common, "response_type": "composite",
                "grading": {"parts": [
                    {"response_type": p.get("response_type", "short_text"),
                     "grading": scoring.with_bareme(
                         p.get("grading") or {}, p.get("response_type", "short_text")),
                     "statement": p.get("statement", ""),
                     "expected": p.get("expected") or {},
                     "figure": p.get("figure")} for p in parts]}}
    return {**common, "response_type": row.response_type,
            "choices": row.grading_json.get("choices", []),
            "grading": grading_json,
            # Le rendu dimensionne les champs sur la nature physique de la
            # réponse (une fraction manuscrite demande 3 mm de plus).
            "expected": row.expected_json or {},
            "inline": bool((row.expected_json or {}).get("inline"))}


def build_render_item(db: Session, *, row, copy_id: str, catalog_id: str, seq: int,
                      guides: str = pdfgen.GUIDES_INCLUDE) -> dict | None:
    """Crée la (ou les) CopyItem d'une ligne de banque et retourne la carte à
    rendre par pdfgen. UNE seule définition, partagée par la génération
    automatique (distribution par compétences) et par l'assistant « Créer mon
    sujet » (placement manuel) : deux constructions parallèles finiraient par
    diverger sur le barème figé, les composites ou l'étiquette Indigo.

    Retourne None si la ligne est inexploitable (composite sans partie)."""
    render = render_shape(row, guides)
    if row.response_type == "composite":
        # une CopyItem PAR PARTIE (chacune un type feuille normal, corrigée par
        # la pipeline existante) mais UNE seule carte unifiée à l'impression.
        part_ids = []
        for part in render["grading"]["parts"]:
            p_item = CopyItem(
                copy_id=copy_id, catalog_id=catalog_id, sequence=seq,
                generated_exercise_id=row.id,
                difficulty=row.difficulty_level * 3,
                response_type=part["response_type"],
                statement=part["statement"], correction=row.correction,
                expected_json=part["expected"], grading_json=part["grading"])
            db.add(p_item)
            db.flush()
            part_ids.append(p_item.id)
        if not part_ids:
            return None
        return {**render, "item_id": part_ids[0], "part_item_ids": part_ids}

    item = CopyItem(
        copy_id=copy_id, catalog_id=catalog_id, sequence=seq,
        generated_exercise_id=row.id,
        difficulty=row.difficulty_level * 3, response_type=row.response_type,
        statement=row.statement, correction=row.correction,
        expected_json=row.expected_json, grading_json=render["grading"])
    db.add(item)
    db.flush()
    return {**render, "item_id": item.id}


def generate_assessment_job(db: Session, assessment: Assessment,
                            job: Job | None = None, font_size: int = 9) -> dict:
    """Génère toutes les copies à partir des compétences cochées. Retourne le
    rapport de génération. Appelé par le worker de fond (job_worker)."""
    # assistant « Créer un sujet » (mode manuel) : le professeur a composé lui-même ses
    # pages (exercices choisis, placés colonne par colonne, variantes). Rien à
    # distribuer ni à remplir — pipeline dédiée, cf. services.manual_subject.
    if (assessment.blueprint_json or {}).get("mode") == "manual":
        from . import manual_subject
        return manual_subject.generate_manual_job(db, assessment, job, font_size)

    school_class = db.get(SchoolClass, assessment.class_id)
    students = sorted((s for s in school_class.students if s.active),
                      key=lambda s: (s.order_index, s.id))
    blueprint = assessment.blueprint_json or {}
    # Création AUTOMATIQUE de l'assistant « Créer un sujet » (mode "auto") :
    # sujet individuel, banque entière, guides en dégradé selon le niveau,
    # problèmes à part (option). Sans ce mode : sujets historiques, inchangés.
    auto_mode = blueprint.get("mode") == "auto"
    # source des exercices choisie dans l'assistant (§ Sésamaths) : "auto"
    # préserve le comportement historique (MathALÉA + DeepSeek), inchangé
    # par défaut pour tout sujet existant sans ce champ
    exercise_source = blueprint.get("exercise_source", "auto")
    # guides (encadrés « {{aide}} ») inclus ou retirés pour tout le sujet —
    # ou, en création automatique, décidés carte par carte (GuideQuota)
    guide_mode = (pdfgen.GUIDES_NONE if blueprint.get("guides") == pdfgen.GUIDES_NONE
                  else pdfgen.GUIDES_INCLUDE)
    guides_by_level = auto_mode and blueprint.get("guides") == "auto"
    guide_medium_share = max(0, min(100, int(blueprint.get(
        "guides_medium_pct", 100 * student_history.DEFAULT_GUIDE_MEDIUM_SHARE)))) / 100
    # Problèmes : en création automatique, JAMAIS piochés comme des exercices —
    # ils n'arrivent que par l'option « Problèmes », dosés selon le niveau.
    exclude_kinds = ("probleme",) if auto_mode else ()
    with_problems = auto_mode and bool(blueprint.get("problems"))
    competency_ids = list(dict.fromkeys(blueprint.get("competency_ids") or []))
    competencies = {c.id: c for c in db.query(Competency).filter(
        Competency.id.in_(competency_ids)).all()}
    ordered_ids = [cid for cid in competency_ids if cid in competencies]
    if not ordered_ids:
        raise ValueError("Aucune compétence sélectionnée")
    catalog_refs = {cid: exercise_gen.ensure_catalog_ref(db, competencies[cid])
                    for cid in ordered_ids}

    def catalog_ref(comp_id: str):
        """Entrée catalogue d'une compétence — y compris celle d'un problème,
        rattaché à une compétence voisine non cochée du même chapitre."""
        if comp_id not in catalog_refs:
            catalog_refs[comp_id] = exercise_gen.ensure_catalog_ref(
                db, db.get(Competency, comp_id))
        return catalog_refs[comp_id]

    # Problèmes proposables : ceux des CHAPITRES touchés par les compétences
    # cochées (un problème porte sur un chapitre, cf. manual_subject).
    problem_rows: list = []
    if with_problems:
        from . import manual_subject
        from ..models import GeneratedExercise
        chapter_ids = {cid for ids in manual_subject.chapter_competency_ids(
            db, ordered_ids).values() for cid in ids}
        problem_rows = (db.query(GeneratedExercise)
                        .filter(GeneratedExercise.competency_id.in_(chapter_ids or ordered_ids),
                                GeneratedExercise.status == "active",
                                GeneratedExercise.kind == "probleme")
                        .order_by(GeneratedExercise.id).all())
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

    # Cache des échecs DÉFINITIFS de banque (compétence sans AUCUN exercice, à
    # aucun des DIFFICULTY_LEVELS) : bank_rows_near_level les essaie tous avant
    # de lever, donc un échec ne dépend pas du niveau demandé. Sans ce cache,
    # une compétence à la banque vide (ou générable côté "gemini") était
    # requêtée/régénérée À CHAQUE tentative de remplissage — jusqu'à 80 fois
    # par élève × N élèves — un sujet sur banque vide donnait donc l'illusion
    # d'une boucle infinie au lieu d'échouer vite avec un message clair.
    _bank_exhausted: dict[str, Exception] = {}

    def bank_rows_near_level(comp: Competency, lvl: int):
        if comp.id in _bank_exhausted:
            raise _bank_exhausted[comp.id]
        try:
            return exercise_gen.bank_rows_near_level(db, comp, lvl, source=exercise_source,
                                                     exclude_kinds=exclude_kinds)
        except Exception as e:
            _bank_exhausted[comp.id] = e
            raise

    # modulo : les 8 hex (32 bits) du hash dépassent une fois sur deux
    # l'INTEGER Postgres signé (max 2^31-1) où Copy.seed est stocké — vu en
    # prod (psycopg2.errors.NumericValueOutOfRange). Marge sous 2^31-1 pour
    # absorber le + student_index de distribution.variant_seed.
    base_seed = int(hashlib.sha256(assessment.id.encode()).hexdigest()[:8], 16) % 2_000_000_000
    max_pages = max(1, min(6, assessment.pages_target or 1))
    assessment.duplex = max_pages >= 2
    ex_tpl_font_size = int(tpl["exercise"].get("font_size", font_size))
    math_fs = int(tpl["exercise"].get("math_size", 9))
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
    # Longueur de la trame demandée à student_history : assez de cases pour
    # remplir les pages les plus denses, la génération s'arrêtant de toute façon
    # quand la page est pleine (la hauteur des cartes n'est pas connue d'avance).
    MAX_FILL_ROUNDS = 6
    total_non_qcm = 0

    for s_idx, student in enumerate(students):
        _set_progress(db, job, round(5 + 90 * s_idx / max(1, len(students))),
                      f"Copie {s_idx + 1}/{len(students)} — sélection des exercices "
                      f"(banque générée à la demande si besoin)")
        logger.info("Copie %s/%s (%s)", s_idx + 1, len(students), student.llm_pseudonym)
        seed = distribution.variant_seed(base_seed, assessment.personalization_mode, s_idx)
        level = _student_level(db, student.id)
        level3 = distribution.difficulty_level3(assessment.personalization_mode, level)
        target_mix = settings.exercise_kind_mix
        individual = assessment.personalization_mode == "individual"
        # Sujet INDIVIDUEL : la trame vient de l'historique réel de l'élève
        # (exercices déjà faits, réussites, dates), calculée ici — au moment où
        # l'on connaît enfin la date du sujet, donc le délai écoulé depuis
        # chaque compétence. Un dérivé PAR COMPÉTENCE, pas un niveau unique
        # pour toute la copie.
        ex_log = student_history.exercise_log(db, student.id) if individual else {}
        if individual:
            slots, _stats = student_history.student_plan(
                db, student.id, ordered_ids, level,
                n_slots=len(ordered_ids) * MAX_FILL_ROUNDS)
            priority = [sl.competency_id for sl in slots]
            slot_levels = [sl.level3 for sl in slots]
        else:
            priority = distribution.priority_competencies(db, student.id, ordered_ids)
            slot_levels = [level3] * len(priority)
        copy = Copy(assessment_id=assessment.id, student_id=student.id, seed=seed)
        db.add(copy)
        db.flush()

        # Guides en dégradé (création automatique) : la part d'exercices guidés
        # glisse avec le niveau de l'élève (cf. student_history.guide_ratio).
        quota = (student_history.GuideQuota(
            student_history.guide_ratio(level, guide_medium_share))
            if guides_by_level else None)

        def _guide_for(row) -> tuple[str, bool, bool]:
            """(mode pdfgen, la carte a-t-elle un guide, est-il imprimé)."""
            if quota is None:
                return guide_mode, False, False
            has = statement_mod.has_guides(row.statement)
            on = quota.decide(has)
            return (pdfgen.GUIDES_INCLUDE if on else pdfgen.GUIDES_NONE), has, on

        render_items: list[dict] = []
        kind_counts: dict[str, int] = {}
        # exercices déjà servis dans CETTE copie, par identité de CONTENU et non
        # par id de ligne : deux compétences voisines peuvent avoir en banque le
        # même exercice, l'élève ne doit pas le voir deux fois (cf.
        # distribution.exercise_identity)
        picked_keys: set[str] = set()

        def _add_item(seq: int, comp_id: str, item_seed: int,
                      filler: bool = False, level: int | None = None,
                      allow_repeat: bool = False) -> bool:
            """`allow_repeat` : filet de sécurité de la passe OBLIGATOIRE, où
            mieux vaut répéter un exercice que laisser une compétence cochée
            sans rien. Au REMPLISSAGE il n'y a aucune obligation : servir deux
            fois le même exercice à un élève n'est plus un moindre mal, c'est du
            gaspillage — on préfère alors laisser la place à une autre carte."""
            comp = competencies[comp_id]
            want = level3 if level is None else level
            try:
                if filler:
                    # petites cartes (un calcul, un QCM court) pour combler les
                    # trous de bas de page : jamais répétées (None = épuisées)
                    rows = exercise_gen.filler_bank_rows(
                        db, comp, want, source=exercise_source)
                    row = distribution.pick_unused_exercise(
                        rows, item_seed, exclude_keys=picked_keys)
                    if row is None:
                        return False
                else:
                    bank, _ = bank_rows_near_level(comp, want)
                    # En individuel, on n'équilibre les types de réponse que
                    # DANS le meilleur rang de candidats (inédit > raté ancien >
                    # déjà réussi) : autrement un exercice déjà servi, mais du
                    # bon type, passerait devant un inédit.
                    if individual:
                        bank = student_history.preferred_rows(bank, ex_log) or bank
                    if not allow_repeat:
                        bank = [r for r in bank
                                if distribution.exercise_identity(r) not in picked_keys]
                        if not bank:
                            return False    # banque épuisée pour cette compétence
                    row = distribution.pick_balanced_exercise(
                        bank, kind_counts, target_mix, item_seed, exclude_keys=picked_keys)
            except Exception as e:
                logger.warning("%s (%s) : %s", comp.code, student.llm_pseudonym, e)
                warnings.append(f"{comp.code} ({student.llm_pseudonym}) : {e}")
                return False

            return _add_row(seq, comp_id, row, filler=filler)

        def _add_row(seq: int, comp_id: str, row, filler: bool = False) -> bool:
            """Pose en base une ligne de banque DÉJÀ choisie. Séparé de la
            sélection pour que la passe best-fit, qui choisit sur la hauteur
            mesurée, emprunte exactement le même chemin d'écriture."""
            nonlocal total_non_qcm
            identity = distribution.exercise_identity(row)
            picked_keys.add(identity)
            # les cartes de remplissage ne passent pas par le tirage équilibré
            # (kind_counts), donc pas de bucket à décrémenter si retirées.
            bucket = None if filler else distribution.exercise_bucket(row)
            mode, has_guide, guided = _guide_for(row)

            render = build_render_item(
                db, row=row, copy_id=copy.id, catalog_id=catalog_ref(comp_id).id,
                seq=seq, guides=mode)
            if render is None:
                return False
            if quota is not None:
                quota.take(has_guide, guided)
            # palier de lecture : du plus simple au plus difficile, problèmes
            # en dernier (cf. pdfgen.reading_tier)
            tier = pdfgen.reading_tier(
                row.difficulty_level, row.kind == "probleme" or render["is_probleme"])
            render_items.append({**render, "_identity": identity, "_bucket": bucket,
                                 "_tier": tier, "_guide": (has_guide, guided)})
            if not row.response_type.startswith("qcm"):
                total_non_qcm += 1
            return True

        # Passe OBLIGATOIRE : une fois chaque compétence cochée, quel que soit
        # le score de l'élève. Le périmètre choisi par le professeur est un
        # contrat, jamais rétréci par la personnalisation. Les premières cases
        # de la trame sont exactement ces compétences-là, une chacune, dans
        # l'ordre de priorité (cf. student_history.student_plan).
        n_required = len(ordered_ids)
        for seq in range(n_required):
            _add_item(seq, priority[seq], seed * 100 + seq,
                      level=slot_levels[seq] if seq < len(slot_levels) else None,
                      allow_repeat=True)

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
                ri, ex_tpl_font_size, math_fs, tpl["exercise"])
                for ri in items]

        def _pack(items: list[dict]) -> tuple[list[dict], list[tuple[int, int]], int]:
            """Réordonne les cartes pour un remplissage colonne par colonne
            efficace (First-Fit-Decreasing, cf. pdfgen.pack_columns) : les
            grandes cartes d'abord, les petites comblant les bas de colonne, au
            lieu du grand vide laissé par l'ordre de production du LLM — mais
            TOUJOURS lues du plus simple au plus difficile, problèmes en
            dernier (paliers `_tier`).
            Retourne (cartes réordonnées, leur (page, colonne), nb de pages) :
            le placement est passé tel quel à render_copy, la feuille imprimée
            est donc exactement celle qui a été simulée ici."""
            order, slots, n_pages = pdfgen.pack_placement(
                _heights(items), [ri["_tier"] for ri in items])
            return [items[i] for i in order], slots, n_pages

        def _pages(items: list[dict]) -> int:
            return _pack(items)[2]

        _measure_cache: dict[tuple[str, str], float] = {}

        def _h_for(row) -> float:
            """Hauteur de la carte telle qu'elle serait posée MAINTENANT —
            guide compris ou non selon la décision du quota à cet instant."""
            mode = _guide_for(row)[0]
            key = (row.id, mode)
            if key not in _measure_cache:
                _measure_cache[key] = pdfgen.estimate_item_height(
                    render_shape(row, mode), ex_tpl_font_size, math_fs, tpl["exercise"])
            return _measure_cache[key]

        def _rollback(before: int) -> None:
            nonlocal total_non_qcm
            for ri in render_items[before:]:
                # un composite porte PLUSIEURS CopyItem (une par partie) — toutes
                # à supprimer ; les autres exercices, une seule.
                ids = ri.get("part_item_ids") or ([ri["item_id"]] if ri.get("item_id") else [])
                if ids:
                    for iid in ids:
                        db.query(CopyItem).filter_by(id=iid).delete()
                    if not ri["response_type"].startswith("qcm"):
                        total_non_qcm -= 1
                    if ri.get("_bucket"):
                        kind_counts[ri["_bucket"]] = max(0, kind_counts.get(ri["_bucket"], 0) - 1)
                    picked_keys.discard(ri.get("_identity"))
                    if quota is not None:
                        quota.release(*ri["_guide"])
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
                # La trame individuelle est une SUITE de cases (compétence +
                # dérivé) déjà pondérée par la priorité : on la déroule, puis on
                # reboucle dessus. En commun/variantes, tour de rôle inchangé.
                comp_id = priority[seq % len(priority)]
                want = slot_levels[seq] if seq < len(slot_levels) else (
                    slot_levels[seq % len(slot_levels)] if slot_levels else None)
                attempts += 1
                before = len(render_items)
                if not _add_item(seq, comp_id, seed * 100 + seq, filler=filler,
                                 level=want):
                    seq += 1
                    stagnant += 1
                    continue
                # nombre de pages une fois les cartes réordonnées (FFD) : c'est
                # ce placement-là que render_copy réalise en bout de chaîne, donc
                # ce qui décide du débordement — pas l'ordre de production brut,
                # qui gaspille des bas de colonne et remplirait donc moins.
                if _pages(render_items) > max_pages:
                    _rollback(before)
                    stagnant += 1
                else:
                    stagnant = 0
                seq += 1
            return seq

        def _best_fit(start_seq: int) -> None:
            """Dernière passe : COMBLER les trous restants en choisissant une
            carte qui y tient, au lieu d'en essayer une au hasard.

            Les deux passes précédentes tirent l'exercice suivant sans jamais
            regarder sa HAUTEUR : quand il ne reste que deux centimètres, elles
            proposent des cartes trop grandes, les créent en base, mesurent,
            les suppriment, et abandonnent après quelques échecs — sans avoir
            essayé la petite carte qui tenait. D'où des bas de page vides.

            Ici on mesure AVANT de créer (`render_shape` ne touche pas la base),
            on ne retient que les cartes qui rentrent dans le plus grand trou, et
            on prend la plus grande d'entre elles (best-fit : le trou restant
            après coup est le plus petit possible).

            Cette passe ne dépend d'aucun pool de « cartes de remplissage » : elle
            marche donc aussi pour les sources à pool fini (Indigo, Sésamaths),
            où la passe filler ne fait rien du tout — c'est-à-dire justement là
            où les bas de page restaient blancs."""
            # Tous les dérivés que CETTE copie a le droit de servir : le mix
            # individuel en couvre plusieurs (cf. student_history.level_quota),
            # un sujet commun un seul. Se limiter à un niveau laisserait un trou
            # béant alors qu'un exercice parfaitement légitime le comblait.
            allowed_levels = sorted(set(slot_levels)) or [level3]
            # Banque mesurée UNE fois : la hauteur d'une carte ne dépend que de
            # sa ligne, jamais de ce qui est déjà placé. `rank` est la préférence
            # de l'élève (0 = inédit, 1 = raté ancien, 2 = déjà réussi) — on
            # garde TOUS les rangs, pour que l'épuisement du meilleur n'oblige
            # pas à laisser un trou ouvert.
            measured: list[tuple[int, float, str, object]] = []
            seen_rows: set[str] = set()
            for comp_id in dict.fromkeys(priority):
                comp = competencies[comp_id]
                rows = []
                for lvl in allowed_levels:
                    try:
                        lvl_rows, _ = bank_rows_near_level(comp, lvl)
                    except Exception:
                        continue        # source indisponible : déjà signalée
                    rows.extend(r for r in lvl_rows if r.id not in seen_rows)
                    seen_rows.update(r.id for r in lvl_rows)
                for row in rows:
                    rank = (student_history.candidate_rank(row, ex_log)[0]
                            if individual else 0)
                    measured.append((rank, _h_for(row), comp_id, row))
            # rang croissant d'abord (ce que l'élève n'a pas encore vu), puis
            # hauteur décroissante : à préférence égale, la plus grande carte qui
            # tient laisse le plus petit trou derrière elle.
            measured.sort(key=lambda m: (m[0], -m[1]))

            # Cartes qui, à l'essai, faisaient déborder malgré un trou annoncé
            # suffisant : le re-packing FFD peut redistribuer les colonnes et
            # réclamer une page de plus. On les écarte et on continue — une
            # carte PLUS PETITE peut encore tenir. Abandonner au premier échec
            # (ce que faisait le remplissage historique) laissait justement le
            # bas de page blanc.
            blocked: set[str] = set()
            seq = start_seq
            for _ in range(MAX_FILL_ATTEMPTS):
                holes = pdfgen.free_space(_heights(render_items), max_pages,
                                           [ri["_tier"] for ri in render_items])
                biggest = max(holes) if holes else 0.0
                if biggest <= 0 or not measured:
                    return
                # best-fit : la PLUS GRANDE carte qui tienne encore, pour que le
                # trou restant soit le plus petit possible (`measured` est trié
                # décroissant, donc la première qui rentre est la bonne).
                # hauteur relue à chaque tour : en guides par niveau, le quota
                # peut avoir changé d'avis (guide imprimé ou non) depuis le tri
                pick = next(
                    (m for m in measured
                     if _h_for(m[3]) <= biggest and m[3].id not in blocked
                     and distribution.exercise_identity(m[3]) not in picked_keys),
                    None)
                if pick is None:
                    return              # plus rien ne rentre : place inexploitable
                _rank, _h, comp_id, row = pick
                before = len(render_items)
                if not _add_row(seq, comp_id, row):
                    blocked.add(row.id)
                    continue
                # ceinture : la simulation de placement reste l'autorité finale
                if _pages(render_items) > max_pages:
                    _rollback(before)
                    blocked.add(row.id)
                    continue
                seq += 1

        def _add_problems(start_seq: int) -> int:
            """Problèmes (option de la création automatique) : combien, et de
            quelle difficulté, selon le niveau de l'élève
            (student_history.problem_plan). La part de copie qui leur revient
            est un BUDGET de hauteur : le premier problème passe toujours
            (s'il tient dans les pages), les suivants tant qu'ils tiennent dans
            le budget. À difficulté visée égale : inédit d'abord (même
            préférence que les exercices), puis un tirage propre à l'élève —
            deux élèves forts ne reçoivent pas forcément le même problème."""
            plan = student_history.problem_plan(level) if with_problems else None
            if not plan or not problem_rows:
                return start_seq
            share, mix = plan
            budget = share * sum(pdfgen.column_capacity(b) for b in range(2 * max_pages))

            def tiebreak(row) -> str:
                return hashlib.sha256(f"{seed}:{row.id}".encode()).hexdigest()

            # jamais une difficulté que le mix exclut (un élève de niveau 10 ne
            # reçoit pas de problème facile, même quand les difficiles sont
            # épuisés : la place revient alors aux exercices) — sauf si la
            # banque n'a RIEN d'autre à offrir
            allowed = [r for r in problem_rows
                       if mix[max(1, min(3, r.difficulty_level or 2)) - 1] > 0]
            candidates = [r for r in (allowed or problem_rows)
                          if distribution.exercise_identity(r) not in picked_keys]
            counts = {1: 0, 2: 0, 3: 0}
            spent, seq = 0.0, start_seq
            while candidates:
                want = student_history.next_problem_level(mix, counts)
                ordered = sorted(candidates, key=lambda r: (
                    abs(r.difficulty_level - want),
                    student_history.candidate_rank(r, ex_log), tiebreak(r)))
                added = False
                for row in ordered:
                    h = _h_for(row)
                    if sum(counts.values()) and spent + h > budget:
                        continue        # hors budget : un plus court peut tenir
                    candidates.remove(row)
                    before = len(render_items)
                    ok = _add_row(seq, row.competency_id, row)
                    if ok and _pages(render_items) > max_pages:
                        _rollback(before)
                        ok = False
                    if ok:
                        spent += h
                        lvl = max(1, min(3, row.difficulty_level or 2))
                        counts[lvl] += 1
                        seq += 1
                        added = True
                        candidates = [r for r in candidates
                                      if distribution.exercise_identity(r) not in picked_keys]
                        break
                if not added:
                    break
            return seq

        if priority:
            # 0) les problèmes (création automatique, option cochée), dosés
            #    selon le niveau AVANT le remplissage — sinon les exercices
            #    prendraient toute la place ;
            # 1) remplir au maximum avec les exercices classiques (grandes cartes) ;
            # 2) combler les trous de bas de page restants avec les cartes courtes ;
            # 3) finir au plus juste, en choisissant ce qui TIENT dans ce qui reste.
            # On reprend la trame là où la passe obligatoire l'a laissée : ses
            # cases suivantes sont déjà pondérées par la priorité.
            next_seq = _add_problems(n_required)
            next_seq = _fill(next_seq, filler=False)
            next_seq = _fill(next_seq, filler=True)
            _best_fit(next_seq)

        if not render_items:
            # banque totalement vide (ou source indisponible) pour TOUTES les
            # compétences cochées : mieux vaut échouer avec un message clair
            # que produire une copie blanche sans le dire (cf. `warnings`,
            # déjà rempli d'un message par compétence).
            raise ValueError(
                "Aucun exercice disponible dans la banque pour composer ce "
                f"sujet (compétence(s) : {', '.join(competencies[cid].code for cid in ordered_ids)}). "
                "Générez ou publiez d'abord des exercices pour ces compétences "
                "(onglet Exercices), ou changez la source d'exercices du sujet.")

        # Ordre DÉFINITIF des cartes : le remplissage colonne par colonne (FFD)
        # est figé ici, une fois toutes les cartes choisies — du plus simple au
        # plus difficile, problèmes en dernier. On renumérote alors les
        # exercices dans l'ordre de LECTURE ainsi obtenu, pour que le badge
        # imprimé et le « Ex. N » de la correction manuelle (routers.scans)
        # restent alignés.
        render_items, placement, _n = _pack(render_items)
        seq_no = 0
        for ri in render_items:
            if ri.get("kind") == "exercise" and (ri.get("part_item_ids") or ri.get("item_id")):
                seq_no += 1
                # un composite = UNE carte : ses parties partagent le même n° d'exercice
                for iid in (ri.get("part_item_ids") or [ri["item_id"]]):
                    db.query(CopyItem).filter_by(id=iid).update({"sequence": seq_no})
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
            page.hmac_version = "2"
            pages_meta.append({"page_id": page.id, "payload": page.qr_payload})
            page_rows.append(page)

        zones = pdfgen.render_copy(
            c, student_name=student.name,
            class_name=school_class.name, title=assessment.title,
            assessment_type=assessment.type, items=render_items,
            pages_meta=pages_meta, font_size=font_size, tpl=tpl,
            placement=placement, dyslexic=student.dyslexic)

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

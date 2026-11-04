"""Assistant « Créer un sujet », mode MANUEL (§3.1 bis) : le professeur compose
LUI-MÊME son sujet, exercice par exercice, page par page, colonne par colonne.

Différences avec la pipeline automatique (services.generation) :

  • aucun choix d'exercice n'est fait par la plateforme — le plan (quelles
    cartes, dans quelle colonne de quelle page, dans quel ordre) est celui
    enregistré par l'assistant dans `assessment.blueprint_json["variants"]` ;
  • pas de sujet individuel : uniquement des sujets COMMUNS. Les variantes
    existent pour deux raisons bien distinctes, jamais mélangées —
      « aléatoires » (clé « anticheat ») : N sujets équivalents répartis au
        hasard, en nombre égal ;
      « par niveau » : exactement 3 variantes (facile/moyen/difficile),
        attribuées d'après le niveau de l'élève (StudentLevel, échelle 1-10) ;
  • les guides (encadrés « {{aide}} » intégrés aux énoncés) sont pilotés
    globalement par le sujet : inclus ou retirés pour tous (cf.
    pdfgen.GUIDES_*).

Deux compositions coexistent dans `blueprint_json["composition"]` :

  • « engine » (assistant actuel) : le professeur choisit ses cartes, le moteur
    les place. Chaque élève reçoit les mêmes cartes, mais dans un ordre et avec
    des champs de réponse propres à sa copie (services.sheet_engine et
    answer_mix). Les guides peuvent suivre le niveau de l'élève.
  • sans cette clé (sujets composés avant) : plan figé, chaque carte posée dans la
    colonne enregistrée, copies identiques au sein d'une variante.
"""
import hashlib
import logging
import math
import random

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..models import (
    Assessment, Competency, Copy, DocumentPage, ExercisePreference, FileObject, GeneratedExercise,
    Job, ResponseZone, SchoolClass, StudentLevel,
)
from . import answer_mix, exercise_gen, generation, pdfgen, scoring, sheet_engine, student_history
from .runtime_settings import doc_templates
from .security import sign_page

logger = logging.getLogger(__name__)

# Variantes par niveau : 3, toujours les mêmes clés, dans cet ordre. Un élève
# de niveau <= 4 sur 10 reçoit la variante « facile ».
LEVEL_KEYS = ("facile", "moyen", "difficile")
LEVEL_LABELS = {"facile": "Facile", "moyen": "Moyen", "difficile": "Difficile"}
GUIDES_PRINT_MAX_LEVEL = 4
MAX_VARIANTS = 6            # anti-triche : au-delà, plus personne ne s'y retrouve

# Modes de guide exposés par les assistants : les encadrés guide des énoncés
# sont inclus, ou retirés, pour TOUS les élèves du sujet.
GUIDE_MODES = (pdfgen.GUIDES_INCLUDE, pdfgen.GUIDES_NONE)
GUIDES_BY_LEVEL = "auto"    # imprimés selon le niveau de l'élève, comme en automatique
COMPOSITION_ENGINE = "engine"

# Repli du temps estimé pour les exercices sans `estimated_seconds` : réponse par
# type, ajustée à la difficulté, plus la lecture de l'énoncé. Calé sur les étalons
# de la forge (QCM facile en une étape : 40 s).
_ANSWER_SECONDS = {"qcm_single": 35, "qcm_multiple": 55, "short_text": 60,
                   "manual_drawing": 150}
_ANSWER_SECONDS_PER_UNIT = {"checkbox_grid": 20, "matching": 20, "table_fill": 30,
                            "multi_blank": 30, "multiline_text": 25}
_DIFFICULTY_TIME = {1: 1.0, 2: 1.3, 3: 1.7}
_READING_CHARS_PER_SECOND = 14


def normalize_guide_mode(mode: str | None) -> str:
    """Mode pdfgen d'un sujet. Les anciennes valeurs (« overlay »,
    « print_fragile ») d'un sujet composé avant la refonte valent « include »."""
    return pdfgen.GUIDES_NONE if mode == pdfgen.GUIDES_NONE else pdfgen.GUIDES_INCLUDE


def variant_for_student(blueprint: dict, student_level: int, student_index: int) -> int:
    """Index de la variante servie à un élève.

    • « par niveau » : la variante dont la clé correspond à son niveau
      (facile <= 4, moyen 5-7, difficile >= 8) ; repli sur la variante médiane
      si cette clé n'a pas été composée.
    • « aléatoires » (clé historique « anticheat ») : tourniquet sur le rang
      de l'élève — rang TIRÉ AU SORT par l'appelant (`shuffled_ranks`), pour
      une répartition au hasard mais en nombre égal par variante.
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


def _answer_seconds(rtype: str, grading: dict) -> float:
    if rtype in _ANSWER_SECONDS:
        return _ANSWER_SECONDS[rtype]
    if rtype == "checkbox_grid":
        units = len(grading.get("rows") or [])
    elif rtype == "matching":
        units = len(grading.get("left") or [])
    elif rtype in ("table_fill", "multi_blank"):
        units = sum(1 for row in grading.get("cells") or [] for c in row if not c.get("given"))
    elif rtype == "multiline_text":
        units = int(grading.get("lines") or 5)
    else:
        return _ANSWER_SECONDS["short_text"]
    return _ANSWER_SECONDS_PER_UNIT[rtype] * max(1, units)


def estimated_seconds(row: GeneratedExercise) -> int:
    """Temps prévu pour un élève : celui de l'auteur s'il existe, sinon le repli."""
    if row.estimated_seconds:
        return int(row.estimated_seconds)
    parts = (row.expected_json or {}).get("parts") or (row.grading_json or {}).get("parts") or []
    if row.response_type == "composite":
        answer = sum(_answer_seconds(p.get("response_type") or "short_text", p.get("grading") or {})
                     for p in parts)
        reading = len(row.statement or "") + sum(len(p.get("statement") or "") for p in parts)
    else:
        answer = _answer_seconds(row.response_type, row.grading_json or {})
        reading = len(row.statement or "")
    factor = _DIFFICULTY_TIME.get(max(1, min(3, row.difficulty_level or 2)), 1.3)
    return 5 * math.ceil((answer * factor + reading / _READING_CHARS_PER_SECOND) / 5)


def _card(db: Session, row: GeneratedExercise, comp: Competency, tpl: dict,
          font_size: int, math_fs: int) -> dict:
    """Une entrée du pool, telle que l'assistant l'affiche et la mesure."""
    indigo = (row.raw_extract_json or {}).get("indigo") or {}
    heights = {}
    for mode in GUIDE_MODES:
        shape = generation.render_shape(row, mode)
        heights[mode] = pdfgen.estimate_item_height(
            shape, font_size, math_fs, tpl["exercise"])
    grading = row.grading_json or {}
    display_statement, is_problem = generation.indigo_display(row)
    return {
        "id": row.id,
        "competency_id": row.competency_id,
        "competency_label": _competency_label(comp),
        "chapter_code": comp.chapter_code, "chapter_name": comp.chapter_name,
        "statement": display_statement,
        "response_type": row.response_type,
        "expected": row.expected_json or {},
        "grading": grading,
        "choices": grading.get("choices") or [],
        "row_labels": grading.get("row_labels"),
        "col_labels": grading.get("col_labels"),
        "lines": grading.get("lines"),
        "difficulty": row.difficulty_level,
        "kind": row.kind, "source": row.source,
        "badge_type": indigo.get("badge_type") or "",
        "title": indigo.get("title") or "",
        "is_problem": is_problem,
        "source_number": indigo.get("source_number") or "",
        "has_figure": bool(row.figure_json),
        "figure": row.figure_json,
        "figure_url": (f"/api/content/exercises/{row.id}/figure.png"
                       if row.figure_json else None),
        "correction_guide": (row.correction or "")
                            if row.source in ("indigo", "gemini") else "",
        "correction_solution": (indigo.get("correction_solution") or "")
                               if row.source == "indigo"
                               else (row.correction or "") if row.source != "gemini"
                               else "",
        "bareme_points": scoring.item_bareme(row.grading_json, row.response_type),
        "estimated_seconds": estimated_seconds(row),
        "tier": pdfgen.reading_tier(row.difficulty_level, row.kind == "probleme" or is_problem),
        # hauteurs RÉELLES (points PDF) des deux mises en page possibles : c'est
        # la mesure de pdfgen, pas une estimation refaite côté navigateur.
        "height_pt": sheet_engine.measured(heights[pdfgen.GUIDES_INCLUDE]),
        "height_pt_no_guide": sheet_engine.measured(heights[pdfgen.GUIDES_NONE]),
    }


def pool(db: Session, competency_ids: list[str], pages: int = 1, user_id: str | None = None) -> dict:
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
    math_fs = int(tpl["exercise"].get("math_size", 9))

    by_chapter = chapter_competency_ids(db, competency_ids)
    all_ids = set(competency_ids) | {cid for ids in by_chapter.values() for cid in ids}
    comps = {c.id: c for c in db.query(Competency).filter(Competency.id.in_(all_ids)).all()}
    rows = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id.in_(all_ids),
                    GeneratedExercise.status == "active")
            .order_by(GeneratedExercise.difficulty_level).all())

    selected = set(competency_ids)
    exercises, problems = [], []
    # Cartes écartées par ce professeur : absentes de ses propositions manuelles.
    preferences = {p.exercise_id: p.value for p in
                   db.query(ExercisePreference).filter_by(user_id=user_id or "").all()}
    for row in rows:
        comp = comps.get(row.competency_id)
        if comp is None or preferences.get(row.id) == "dislike":
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

def _student_level(db: Session, student_id: str, subject_id: str = "math") -> int:
    lvl = (db.query(StudentLevel).filter_by(student_id=student_id, subject_id=subject_id)
           .order_by(StudentLevel.valid_from.desc()).first())
    return lvl.level if lvl else 5


def shuffled_ranks(n: int, seed: int) -> list[int]:
    """Rang tiré au sort de chacun des `n` élèves (index de classe → rang).
    Graine = sujet : régénérer le même sujet redonne la même répartition."""
    order = list(range(n))
    random.Random(seed).shuffle(order)
    ranks = [0] * n
    for rank, idx in enumerate(order):
        ranks[idx] = rank
    return ranks


def _card_tier(row: GeneratedExercise) -> int:
    return pdfgen.reading_tier(row.difficulty_level,
                               row.kind == "probleme" or generation.indigo_display(row)[1])


class _CardMeasures:
    """Mesures d'une carte, indépendantes de l'ordre de ses champs, partagées par
    toutes les copies du sujet : hauteur maximale et largeur de zone de réponse.
    La police adaptée change ces mesures : elles sont gardées séparément."""

    def __init__(self, tpl: dict, guide_mode: str):
        self.tpl, self.guide_mode = tpl, guide_mode
        self.font_size = int(tpl["exercise"].get("font_size", 9))
        self.math_fs = int(tpl["exercise"].get("math_size", 9))
        self._cache: dict[tuple[str, bool], tuple[float, float]] = {}

    def _measure(self, row: GeneratedExercise, dyslexic: bool) -> tuple[float, float]:
        key = (row.id, dyslexic)
        if key not in self._cache:
            shape = generation.render_shape(row, self.guide_mode)
            with pdfgen.dyslexic_measures(dyslexic):
                self._cache[key] = (
                    sheet_engine.measured(pdfgen.estimate_item_height(
                        shape, self.font_size, self.math_fs, self.tpl["exercise"])),
                    pdfgen.answer_zone_width(shape, self.font_size, self.math_fs))
        return self._cache[key]

    def height(self, row: GeneratedExercise, dyslexic: bool = False) -> float:
        return self._measure(row, dyslexic)[0]

    def zone_width(self, row: GeneratedExercise, dyslexic: bool = False) -> float:
        return self._measure(row, dyslexic)[1]


def assert_selection_fits(db: Session, exercise_ids: list[str], pages: int, guides: str) -> None:
    """Refuse une sélection que le moteur ne sait pas faire tenir. Dernier verrou :
    l'assistant grise déjà les cartes qui ne rentrent plus."""
    rows = {r.id: r for r in db.query(GeneratedExercise)
            .filter(GeneratedExercise.id.in_(exercise_ids)).all()}
    chosen = [rows[i] for i in exercise_ids if i in rows]
    worst = pdfgen.GUIDES_NONE if guides == pdfgen.GUIDES_NONE else pdfgen.GUIDES_INCLUDE
    measures = _CardMeasures(doc_templates(db), worst)
    heights = [measures.height(r) for r in chosen]
    if sheet_engine.pack_best(heights, [_card_tier(r) for r in chosen], pages) is None:
        raise ValueError("Ces exercices ne tiennent pas dans les pages prévues : "
                         "retirez-en un ou ajoutez une page.")


def _engine_copy(db: Session, *, chosen: list[GeneratedExercise], catalog_refs: dict,
                 copy_id: str, rng: random.Random, pages: int, guide_mode: str,
                 measures: _CardMeasures, quota: student_history.GuideQuota | None,
                 warnings: list[str], dyslexic: bool = False,
                 ) -> tuple[list[dict], list[tuple[int, int]]]:
    """Cartes et placement de la feuille d'un élève. Les cartes sont posées d'après
    leur hauteur maximale : une carte sans guide laisse un peu de blanc, mais la
    feuille tient quel que soit le niveau de l'élève. L'assistant raisonne en police
    standard : une copie en police adaptée peut demander une page de plus, signalée
    par l'appelant."""
    heights = [measures.height(r, dyslexic) for r in chosen]
    tiers = [_card_tier(r) for r in chosen]
    columns = sheet_engine.mix_columns(heights, tiers, pages, rng)
    if columns is None:
        columns = pdfgen.pack_columns(heights, tiers)
    render_items, placement = [], []
    seq = 0
    for b, column in enumerate(columns):
        for i in column:
            row = chosen[i]
            with pdfgen.dyslexic_measures(dyslexic):
                shape = answer_mix.mix_shape(generation.render_shape(row, guide_mode), rng,
                                             measures.zone_width(row, dyslexic), measures.font_size)
            mode = guide_mode
            if quota is not None:
                has_guide = generation.card_has_guides(row)
                printed = quota.decide(has_guide)
                quota.take(has_guide, printed)
                mode = pdfgen.GUIDES_INCLUDE if printed else pdfgen.GUIDES_NONE
            seq += 1
            render = generation.build_render_item(
                db, row=row, copy_id=copy_id, catalog_id=catalog_refs[row.competency_id].id,
                seq=seq, guides=mode, shape=shape)
            if render is None:
                warnings.append(f"Exercice {row.id[:8]} ignoré (contenu incomplet)")
                seq -= 1
                continue
            render_items.append(render)
            placement.append((b // 2, b % 2))
    return render_items, placement


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
    guide_mode = normalize_guide_mode(blueprint.get("guides"))
    guides_by_level = blueprint.get("guides") == GUIDES_BY_LEVEL
    guide_reach = blueprint.get("guides_reach_pct", student_history.DEFAULT_GUIDE_REACH)
    engine = blueprint.get("composition") == COMPOSITION_ENGINE

    school_class = db.get(SchoolClass, assessment.class_id)
    from .education import assessment_students
    students = assessment_students(db, assessment)
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
    generation.assert_competencies_in_framework(assessment, comps.values())
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
    measures = _CardMeasures(tpl, guide_mode)
    # réserve de pages signées : une colonne trop pleine fait glisser une carte
    # dans la suivante (jamais coupée), donc le plan peut déborder de la cible.
    PAGE_RESERVE = 4
    total_non_qcm = 0

    logger.info("Sujet manuel %s — %s variante(s) « %s », guides : %s, %s élève(s)",
                assessment.id, len(variants),
                blueprint.get("variant_kind", "none"), guide_mode, len(students))

    ranks = shuffled_ranks(len(students), base_seed)
    for s_idx, student in enumerate(students):
        generation._set_progress(
            db, job, round(5 + 90 * s_idx / max(1, len(students))),
            f"Copie {s_idx + 1}/{len(students)}")
        level = _student_level(db, student.id, assessment.subject_id or "math")
        fixed_key = (blueprint.get("duplicate_student_variants") or {}).get(student.id)
        v_idx = next((i for i, v in enumerate(variants)
                      if fixed_key and str(v.get("key")) == str(fixed_key)),
                     variant_for_student(blueprint, level, ranks[s_idx]))
        variant = variants[v_idx]
        guides = guide_mode
        # seed = variante : même cartes pour tous. En composition « engine »,
        # l'ordre propre à la copie vient de `mix_rng`, stable d'une génération à
        # l'autre ; sinon, deux élèves de la même variante ont la même copie.
        seed = base_seed + v_idx

        copy = Copy(assessment_id=assessment.id, student_id=student.id, seed=seed,
                    variant_key=str(variant.get("key") or v_idx))
        db.add(copy)
        db.flush()

        render_items, placement = [], []
        if engine:
            quota = (student_history.GuideQuota(student_history.guide_ratio(level, guide_reach))
                     if guides_by_level else None)
            render_items, placement = _engine_copy(
                db, chosen=[rows[s["exercise_id"]] for s in _ordered_slots(variant, max_pages)],
                catalog_refs=catalog_refs, copy_id=copy.id,
                rng=random.Random(f"{base_seed}:{student.id}:{variant.get('key')}"),
                pages=max_pages, guide_mode=guide_mode, measures=measures, quota=quota,
                warnings=warnings, dyslexic=bool(student.dyslexic))
            total_non_qcm += sum(1 for r in render_items if not r["response_type"].startswith("qcm"))
        else:
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
            c, student_name=student.name,
            class_name=school_class.name, title=assessment.title,
            assessment_type=assessment.type, items=render_items,
            pages_meta=pages_meta, font_size=font_size, tpl=tpl,
            placement=placement, min_pages=max_pages,
            dyslexic=student.dyslexic)

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

"""Pipeline Sésamaths (refonte VISION) : extraction d'exercices depuis les
manuels scolaires PDF (collection Sésamath), en complément de la moisson
MathALÉA/DeepSeek existante.

Pourquoi la vision. Les pages du manuel sont à deux colonnes denses (badges de
nombres, tableaux à compléter, figures géométriques). L'extraction TEXTE
(PyMuPDF) entrelace ces éléments et détruit la structure ; les figures sont par
nature illisibles en texte. On rend donc CHAQUE page en image et on l'extrait
avec un LLM multimodal, UN appel par page.

Architecture (mêmes barrières que exgen-3, réutilisées telles quelles) :
  1. CARTE (services.sesamaths_pdf) : la table des matières donne la page
     imprimée de chaque Série ; on en déduit la plage de pages fichier de
     chaque Série (hors « Culture »), sans jamais parcourir le manuel entier.
  2. EXTRACTION VISION (Claude Haiku 4.5, une fois par page, mise en cache par
     hash pdf+page+prompt+modèle+schéma) : lit l'IMAGE de la page et restitue
     TOUS ses exercices au contrat exgen-3 EXACT (statement/correction/
     response_type/answer/figure + difficulty), énoncés à l'identique, maths en
     LaTeX, tableaux reconstruits, badges de nombres inclus, questions
     reformulées vers un type de réponse de l'UI. Repli Opus 4.8 sur les pages
     denses qu'Haiku n'arrive pas à extraire.
  3. FIGURES : quand un exercice s'appuie sur une figure, le LLM renvoie sa
     zone (bbox relative) ; on la recadre du PDF en PNG (figure "image"), jamais
     devinée.
  4. VALIDATION DÉTERMINISTE : chaque candidat repasse par
     exercise_gen._validate_exercise (LaTeX, types de réponse, auto-vérif) —
     aucune duplication de logique.
  5. COMPLÉMENT si le chapitre ne fournit pas assez d'exercices : génération
     DeepSeek Pro inspirée des exercices Sésamaths réellement extraits du même
     chapitre, vérifiée par Claude — seul usage « génératif » de la pipeline.
  6. BANQUE : GeneratedExercise avec source="sesamaths" (extrait) ou
     "sesamaths_deepseek" (complément), pool STRICTEMENT séparé de la banque
     par défaut.

Reprise sur erreur : l'état d'extraction d'un chapitre est persistant
(SesamathsChapterExtraction), machine à états PAR PAGE — seules les pages en
échec sont retentées au prochain appel.
"""
import hashlib
import json
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CompetencyFramework, GeneratedExercise, SesamathsChapterExtraction, SesamathsLlmCache
from . import exercise_gen, mathrender, providers, sesamaths_pdf

logger = logging.getLogger(__name__)

PROMPT_VERSION = "sesamaths-2-vision"
SOURCE_POOL = ("sesamaths", "sesamaths_deepseek")


# ================================================================ cache LLM

def _cache_key(*parts) -> str:
    material = "|".join(
        p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)
        for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cached_vision(db: Session, cache_key: str, model: str, system: str,
                   user_text: str, image_png: bytes, correlation_id: str) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = providers.claude_vision_json(
        db, "sesamaths_vision_extract", system, user_text, image_png,
        max_tokens=6000, model=model, correlation_id=correlation_id)
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# ============================================================ prompt vision

_VISION_EXTRACT_INTRO = (
    "Tu es un professeur agrégé de mathématiques. On te fournit l'IMAGE d'une "
    "page d'un manuel de §GRADE§ (collection Sésamath), Série §SERIES_NUMBER§ "
    "« §SERIES_NAME§ » du chapitre « §CHAPTER_NAME§ ». Extrais CHAQUE exercice "
    "numéroté de cette page, SANS EN OUBLIER, SANS en inventer.\n\n"
    "RÈGLES D'EXTRACTION :\n"
    "- Restitue l'énoncé À L'IDENTIQUE (mêmes valeurs, même intention) ; corrige "
    "seulement les artefacts de mise en page.\n"
    "- Toute expression mathématique est balisée en LaTeX $...$ (cf. règles de "
    "format ci-dessous).\n"
    "- Une LISTE DE NOMBRES fournie dans des badges/encadrés fait partie de "
    "l'énoncé : recopie-la intégralement dans \"statement\".\n"
    "- Un TABLEAU à compléter doit être reconstruit en \"table_fill\" (l'élève y "
    "écrit ses réponses) : reprends libellés de lignes/colonnes et calcule les "
    "cellules attendues.\n"
    "- REFORMULE si besoin la consigne pour qu'elle rentre dans l'un des types de "
    "réponse ci-dessous (ex. « quels nombres sont divisibles par 2 ? » -> QCM à "
    "choix multiples listant les nombres de l'énoncé).\n"
    "- IGNORE : les rubriques « Culture », les rappels de leçon (« À RETENIR »), "
    "les QR codes, en-têtes et pieds de page.\n"
    "- Si un exercice s'appuie sur une FIGURE (géométrie, droite graduée, repère, "
    "schéma) présente sur la page, n'essaie pas de la décrire : ajoute "
    "\"figure_ref\": {\"bbox_pct\": [x0, y0, x1, y1]} où (x0,y0)=coin haut-gauche "
    "et (x1,y1)=coin bas-droit de la figure, en fractions 0-1 de la page "
    "(x=largeur, y=hauteur).\n"
    "- Ajoute à chaque exercice \"difficulty\": entier 1 (découverte) à 5 (défi), "
    "relatif au niveau §GRADE§.\n\n"
)


def _vision_system(grade: str, chapter_name: str, series_number, series_name: str,
                   is_geometry: bool) -> str:
    format_block = exercise_gen._RESPONSE_FORMAT_BLOCK.replace(
        "{geometry_rules}", exercise_gen._GEOMETRY_RULES if is_geometry else "")
    # .replace (et non .format) : le prompt contient des accolades JSON littérales
    intro = (_VISION_EXTRACT_INTRO
             .replace("§GRADE§", grade)
             .replace("§CHAPTER_NAME§", chapter_name)
             .replace("§SERIES_NUMBER§", str(series_number))
             .replace("§SERIES_NAME§", series_name))
    return intro + format_block


# ================================================================ candidats

def _to_candidate(raw: dict, doc, page_idx: int, competency, db: Session,
                  existing_norms: set[str], out_dir) -> dict | None:
    if not isinstance(raw, dict):
        return None
    raw = dict(raw)
    figure_ref = raw.pop("figure_ref", None)
    if figure_ref and not raw.get("figure"):
        bbox = figure_ref.get("bbox_pct") if isinstance(figure_ref, dict) else None
        if bbox:
            fname = hashlib.sha256(
                f"{page_idx}|{raw.get('statement', '')}".encode()).hexdigest()[:16]
            fig_path = out_dir / f"p{page_idx}_{fname}.png"
            if sesamaths_pdf.crop_bbox_png(doc, page_idx, bbox, fig_path):
                raw["figure"] = {"type": "image", "params": {"path": str(fig_path)}}
    try:
        difficulty = max(1, min(5, int(raw.pop("difficulty", 3))))
    except (TypeError, ValueError):
        difficulty = 3

    valid = exercise_gen._validate_exercise(raw, competency, db, existing_norms)
    if valid is None:
        return None
    valid["difficulty"] = difficulty
    return valid


def _extract_page(db: Session, doc, manual, chapter_code: str, page_meta: dict,
                  is_geometry: bool, competency, existing_norms: set[str],
                  out_dir) -> list[dict]:
    """Extrait les exercices d'UNE page via vision. Essaie Haiku puis, si aucun
    exercice valide, repli Opus 4.8 (page dense). Cache par (pdf, page, prompt,
    modèle, schéma) : une page extraite n'est jamais re-payée."""
    idx = page_meta["index"]
    grade = manual.grade_level
    system = _vision_system(grade, competency.chapter_name,
                            page_meta.get("series_number"), page_meta.get("series_name", ""),
                            is_geometry)
    user_text = ("Extrais TOUS les exercices de cette page au format JSON demandé. "
                 "N'oublie aucun exercice numéroté ; ignore les rubriques Culture "
                 "et les rappels de leçon.")
    png = sesamaths_pdf.render_page_png(doc, idx)

    for model in (settings.claude_vision_model, settings.claude_vision_fallback_model):
        cache_key = _cache_key(manual.sha256, "page", str(idx), PROMPT_VERSION,
                              model, settings.sesamaths_schema_version)
        try:
            data = _cached_vision(db, cache_key, model, system, user_text, png,
                                  correlation_id=f"sesa-vis-{chapter_code}-p{idx}")
        except Exception as e:
            logger.warning("Sésamaths : extraction vision page %s (%s) échouée : %s",
                           idx, model, e)
            continue
        cands: list[dict] = []
        for raw in (data.get("exercises") or []):
            c = _to_candidate(raw, doc, idx, competency, db, existing_norms, out_dir)
            if c is not None:
                cands.append(c)
        if cands:
            return cands
        if model == settings.claude_vision_fallback_model:
            return []
        logger.info("Sésamaths : page %s sans exercice valide en %s, repli %s",
                    idx, model, settings.claude_vision_fallback_model)
    return []


# ================================================================ chapitre

def _resolve_chapter(db: Session, competency):
    """(doc, manual, chapter_code) — chapter_code est None si indisponible
    (manuel absent/chapitre inconnu, déjà journalisé). Jamais d'exception."""
    fw = db.get(CompetencyFramework, competency.framework_id)
    grade_level = fw.grade_level if fw else None
    if not grade_level:
        return None, None, None
    doc, manual = sesamaths_pdf.open_manual(db, grade_level)
    if doc is None:
        return None, manual, None
    chapter_code = competency.chapter_code
    if not chapter_code or chapter_code not in (manual.toc_json or {}):
        logger.warning("Sésamaths : chapitre %s introuvable dans le manuel %s",
                       chapter_code, grade_level)
        return doc, manual, None
    return doc, manual, chapter_code


def ensure_chapter_pool(db: Session, doc, manual, chapter_code: str, competency
                       ) -> list[dict]:
    """État persistant par chapitre — machine à états PAR PAGE. Ne lève jamais :
    toute erreur est journalisée, le pool renvoyé peut être partiel (reprise
    ciblée : seules les pages en échec sont retentées au prochain appel)."""
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=chapter_code).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=chapter_code)
        db.add(row)
        db.flush()

    if row.step == "done":
        return row.validated_json or []

    row.attempts += 1
    # géométrie : chapitres du domaine B (5e) ou compétence en domaine EG/GM
    is_geometry = (competency.domain_code in exercise_gen.GEOMETRY_DOMAINS
                   or chapter_code[:1] == "B")
    out_dir = settings.data_dir / "sesamaths" / manual.grade_level / chapter_code

    try:
        if row.step == "pending":
            pages = sesamaths_pdf.chapter_exercise_pages(doc, manual.toc_json, chapter_code)
            row.page_range_json = {"pages": pages, "done_pages": []}
            row.step = "pages_located"
            db.commit()

        if row.step == "pages_located":
            pages = row.page_range_json.get("pages", [])
            done = set(row.page_range_json.get("done_pages", []))
            pool = list(row.validated_json or [])
            existing_norms = {exercise_gen._normalize_statement_for_dedup(c["statement"])
                              for c in pool}
            failed: list[int] = []
            for pg in pages:
                if pg["index"] in done:
                    continue
                try:
                    cands = _extract_page(db, doc, manual, chapter_code, pg,
                                          is_geometry, competency, existing_norms, out_dir)
                except Exception as e:
                    logger.warning("Sésamaths : page %s (%s) en échec : %s",
                                   pg["index"], chapter_code, e)
                    failed.append(pg["index"])
                    continue
                pool.extend(cands)
                done.add(pg["index"])
            row.validated_json = pool
            row.page_range_json = {"pages": pages, "done_pages": sorted(done)}
            row.failed_series_json = failed
            row.step = "done" if not failed else "pages_located"
            row.error_message = "" if pool else "Aucun exercice validé pour ce chapitre"
            db.commit()
    except Exception as e:
        row.error_message = str(e)[:2000]
        logger.error("Sésamaths : extraction %s en échec (step=%s) : %s",
                    chapter_code, row.step, e)
        db.commit()

    return row.validated_json or []


def chapter_pool(db: Session, competency) -> list[dict]:
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None or chapter_code is None:
        return []
    return ensure_chapter_pool(db, doc, manual, chapter_code, competency)


def harvest(db: Session, competency, level: int, need: int,
           existing_norms: set[str]) -> list[dict]:
    """Moisson des exercices Sésamaths déjà extraits du chapitre de
    `competency`, filtrés au niveau demandé — point d'entrée analogue à
    exercise_gen._harvest_mathalea."""
    if need <= 0:
        return []
    out = []
    for cand in chapter_pool(db, competency):
        if len(out) >= need:
            break
        if cand.get("difficulty") != level:
            continue
        normalized = exercise_gen._normalize_statement_for_dedup(cand["statement"])
        if normalized in existing_norms:
            continue
        existing_norms.add(normalized)
        c = dict(cand)
        c["_source"] = "sesamaths"
        out.append(c)
    return out


def top_up_with_deepseek_pro(db: Session, competency, level: int, need: int,
                             pool: list[dict], existing_norms: set[str]) -> list[dict]:
    """Complément DeepSeek Pro inspiré des exercices Sésamaths réellement
    extraits du même chapitre, vérifié par Claude Haiku (langage naturel) —
    seul usage « génératif » de cette pipeline, réservé au comblement."""
    if need <= 0:
        return []
    examples = [c["statement"] for c in pool[:3]]
    avoid = [mathrender.strip_math(s)[:120] for s in examples]
    system, payload = exercise_gen._generation_payload(db, competency, level, need, avoid)
    if examples:
        payload["inspiration_examples"] = examples
        system += (
            "\n\nINSPIRATION : les exercices ci-dessous (\"inspiration_examples\") sont "
            "des exercices RÉELS déjà validés du même chapitre — inspire-toi de leur "
            "style et de leur niveau pour créer des exercices DIFFÉRENTS (jamais une "
            "simple reformulation), qui évaluent la même compétence.")

    out: list[dict] = []
    for attempt in range(2):
        if len(out) >= need:
            break
        try:
            data = providers.deepseek_json(
                db, "exercise_generation", system, payload, max_tokens=6000,
                model=settings.deepseek_pro_model,
                correlation_id=f"sesamaths-topup-{competency.code}-L{level}-att{attempt}")
        except Exception as e:
            logger.warning("Sésamaths : top-up DeepSeek Pro indisponible pour %s "
                           "niveau %s : %s", competency.code, level, e)
            break
        for raw in (data.get("exercises") or [])[:need - len(out) + 2]:
            if len(out) >= need:
                break
            valid = exercise_gen._validate_exercise(raw, competency, db, existing_norms)
            if valid is None:
                continue
            is_good, verdict = exercise_gen._verify_with_claude(db, competency, level, valid)
            if not is_good:
                fixed_raw = exercise_gen._repair_exercise(db, competency, level, valid, verdict)
                if fixed_raw is None:
                    continue
                existing_norms.discard(
                    exercise_gen._normalize_statement_for_dedup(valid["statement"]))
                valid = exercise_gen._validate_exercise(fixed_raw, competency, db, existing_norms)
                if valid is None:
                    continue
                is_good, verdict = exercise_gen._verify_with_claude(db, competency, level, valid)
                if not is_good:
                    continue
                verdict = {**verdict, "repaired": True}
            valid["_verdict"] = verdict
            out.append(valid)
    return out


# ================================================================ banque

def ensure_bank(db: Session, competency, level: int,
                min_variants: int | None = None) -> list[GeneratedExercise]:
    """Équivalent de exercise_gen.ensure_bank pour la source Sésamaths : pool
    strictement séparé (source in SOURCE_POOL), jamais mélangé à la banque
    MathALÉA/DeepSeek par défaut."""
    level = max(1, min(5, level))
    min_variants = min_variants or settings.exercise_variants_per_level

    rows = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                   GeneratedExercise.difficulty_level == level,
                   GeneratedExercise.status == "active",
                   GeneratedExercise.source.in_(SOURCE_POOL))
            .all())
    missing = min_variants - len(rows)
    if missing <= 0:
        return rows
    logger.info("Sésamaths : banque %s niveau %s : %s variante(s) en stock, %s à créer",
               competency.code, level, len(rows), missing)

    existing_norms = {
        exercise_gen._normalize_statement_for_dedup(ex.statement)
        for ex in db.query(GeneratedExercise)
        .filter(GeneratedExercise.competency_id == competency.id,
               GeneratedExercise.status == "active",
               GeneratedExercise.source.in_(SOURCE_POOL)).all()}

    added: list[GeneratedExercise] = []
    next_variant = len(rows)

    def _store(candidate: dict, source: str, verdict: dict) -> None:
        nonlocal next_variant
        row = GeneratedExercise(
            competency_id=competency.id, difficulty_level=level, variant=next_variant,
            statement=candidate["statement"], correction=candidate["correction"],
            response_type=candidate["response_type"],
            expected_json=candidate["expected"], grading_json=candidate["grading"],
            model=(settings.claude_vision_model if source == "sesamaths"
                  else settings.deepseek_pro_model),
            prompt_version=PROMPT_VERSION, status="active",
            verifier_model=settings.claude_model if source == "sesamaths_deepseek" else "",
            verifier_verdict_json=verdict, quality_json=verdict.get("scores") or {},
            figure_json=candidate.get("figure_json"), source=source,
            kind=candidate.get("kind", "application"))
        db.add(row)
        added.append(row)
        next_variant += 1

    try:
        for cand in harvest(db, competency, level, missing, existing_norms):
            _store(cand, "sesamaths", cand.get("_verdict", {}))
    except Exception as e:
        logger.warning("Sésamaths : moisson %s niveau %s impossible : %s",
                       competency.code, level, e)
    missing = min_variants - len(rows) - len(added)

    if missing > 0:
        try:
            pool = chapter_pool(db, competency)
            for cand in top_up_with_deepseek_pro(db, competency, level, missing,
                                                 pool, existing_norms):
                _store(cand, "sesamaths_deepseek", cand.get("_verdict", {}))
        except Exception as e:
            logger.warning("Sésamaths : top-up DeepSeek Pro %s niveau %s impossible : %s",
                           competency.code, level, e)

    db.flush()
    if not rows and not added:
        raise ValueError(
            f"Aucun exercice Sésamaths n'a passé les contrôles qualité pour "
            f"{competency.code} niveau {level}")
    logger.info("Sésamaths : banque %s niveau %s prête : %s variante(s)",
               competency.code, level, len(rows) + len(added))
    return rows + added

"""Pipeline Sésamaths : extraction d'exercices depuis les manuels scolaires
PDF (collection Sésamath), en complément de la moisson MathALÉA existante.

Architecture (mêmes barrières que exgen-3, réutilisées telles quelles) :
  1. PDF (services.sesamaths_pdf, PyMuPDF) : repère le chapitre demandé dans
     le manuel du niveau de la compétence, extrait son texte (ordre de
     lecture) et ses figures — jamais le manuel entier.
  2. STRUCTURATION (DeepSeek flash, une fois par Série du chapitre, mise en
     cache par hash pdf+chapitre+modèle+prompt+schéma) : segmente le texte
     brut en exercices distincts, au contrat JSON exgen-3 EXACT
     (exercise_gen._RESPONSE_FORMAT_BLOCK), + une difficulté 1-5.
  3. VALIDATION DÉTERMINISTE : chaque candidat repasse par
     exercise_gen._validate_exercise (LaTeX, types de réponse, auto-vérif
     par grading.grade) — reformulation vers un type de réponse compatible
     comprise gratuitement, aucune duplication de logique.
  4. COMPLÉMENT si le chapitre ne fournit pas assez d'exercices au niveau
     demandé : génération DeepSeek Pro inspirée des exercices Sésamaths
     validés du même chapitre, vérifiée par Claude Haiku (langage naturel) —
     seul usage de Claude dans cette pipeline, réservé à cette voie générée.
  5. BANQUE : stockage dans GeneratedExercise avec source="sesamaths"
     (extrait) ou "sesamaths_deepseek" (complément), un pool STRICTEMENT
     séparé de la banque MathALÉA/DeepSeek par défaut (source in
     ("deepseek","mathalea")) — ne la remplace ni ne l'altère jamais.

Reprise sur erreur : l'état d'extraction d'un chapitre est une machine à
états persistante (SesamathsChapterExtraction.step), reprise au dernier step
réussi à chaque appel — pas de nouvelle file d'attente, la pipeline tourne en
synchrone à la demande (comme MathALÉA aujourd'hui).
"""
import hashlib
import json
import logging
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CompetencyFramework, GeneratedExercise, SesamathsChapterExtraction, SesamathsLlmCache
from . import exercise_gen, mathrender, providers, sesamaths_pdf

logger = logging.getLogger(__name__)

PROMPT_VERSION = "sesamaths-1"
SOURCE_POOL = ("sesamaths", "sesamaths_deepseek")

_SERIES_RE = re.compile(r"Série\s*(\d+)")
_MAX_SERIES_TEXT_CHARS = 8000


# ================================================================ cache LLM

def _cache_key(*parts) -> str:
    material = "|".join(
        p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)
        for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cached_deepseek_json(db: Session, cache_key: str, operation: str, system: str,
                          payload: dict, max_tokens: int) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = providers.deepseek_json(db, operation, system, payload, max_tokens=max_tokens,
                                   model=settings.deepseek_model,
                                   correlation_id=f"sesamaths-{cache_key[:12]}")
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# ============================================================ structuration

def _bundle_series(pages: list[dict]) -> dict[int, dict]:
    """Regroupe les pages d'un chapitre par numéro de Série — chaque page
    d'exercice porte son numéro en pied de page ; les pages de rappel de
    leçon n'en portent aucun et sont ignorées (extraction limitée aux
    exercices, cf. contraintes Sésamaths)."""
    bundles: dict[int, dict] = {}
    for p in pages:
        m = _SERIES_RE.search(p["text"])
        if not m:
            continue
        n = int(m.group(1))
        b = bundles.setdefault(n, {"text": "", "figures": []})
        b["text"] += ("\n\n" if b["text"] else "") + p["text"]
        b["figures"].extend(p["figures"])
    return bundles


def _structure_series(db: Session, manual_sha256: str, grade_level: str, chapter_code: str,
                      chapter_name: str, series_number: int, series_text: str,
                      n_figures: int, is_geometry: bool) -> list[dict]:
    """Un appel DeepSeek flash pour segmenter une Série en exercices, au
    contrat exgen-3 (statement/correction/response_type/answer/figure/kind +
    difficulty), mis en cache par (pdf, chapitre, série, modèle, prompt, schéma)."""
    format_block = exercise_gen._RESPONSE_FORMAT_BLOCK.replace(
        "{geometry_rules}", exercise_gen._GEOMETRY_RULES if is_geometry else "")
    system = (
        "Tu es un professeur agrégé de mathématiques. On te donne le texte BRUT "
        f"(extraction PDF, ordre de lecture approximatif) de la Série {series_number} "
        f"du chapitre « {chapter_name} » ({chapter_code}) d'un manuel de {grade_level}. "
        "Identifie CHAQUE exercice distinct de ce texte et restitue-le structuré, SANS "
        "en inventer, SANS changer les valeurs numériques ni l'intention pédagogique — "
        "corrige uniquement les défauts d'extraction (numérotation, texte tronqué ou "
        "mal ordonné, artefacts). La \"correction\" est un corrigé TRÈS SIMPLE, sans "
        "justification ni rédaction, réutilisable par une correction déterministe "
        "(juste le résultat attendu). Ajoute à chaque exercice \"difficulty\": entier "
        f"1 (découverte) à 5 (défi), relative au niveau {grade_level}.\n\n"
        + (f"{n_figures} figure(s) ont été extraites séparément de cette Série, "
           "identifiants \"fig:0\" à "
           f"\"fig:{max(0, n_figures - 1)}\" : si un exercice s'appuie sur l'une "
           "d'elles, référence-la par \"figure_ref\":\"fig:N\" au lieu de la décrire ; "
           "sinon omets ce champ.\n\n" if n_figures else "")
        + format_block
    )
    payload = {
        "grade": grade_level, "chapter_code": chapter_code, "chapter_name": chapter_name,
        "series_number": series_number,
        "texte_brut": series_text[:_MAX_SERIES_TEXT_CHARS],
    }
    cache_key = _cache_key(manual_sha256, chapter_code, str(series_number),
                          settings.deepseek_model, PROMPT_VERSION,
                          settings.sesamaths_schema_version, payload)
    data = _cached_deepseek_json(db, cache_key, "sesamaths_structure", system, payload,
                                 max_tokens=4000)
    return data.get("exercises") or []


def _to_candidate(raw: dict, figures: list[dict], competency, db: Session,
                  existing_norms: set[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    raw = dict(raw)
    figure_ref = raw.pop("figure_ref", None)
    if figure_ref and not raw.get("figure"):
        try:
            fi = int(str(figure_ref).split(":", 1)[1])
        except (ValueError, IndexError):
            fi = -1
        if 0 <= fi < len(figures):
            raw["figure"] = {"type": "image", "params": {"path": figures[fi]["png_path"]}}
    try:
        difficulty = max(1, min(5, int(raw.pop("difficulty", 3))))
    except (TypeError, ValueError):
        difficulty = 3

    valid = exercise_gen._validate_exercise(raw, competency, db, existing_norms)
    if valid is None:
        return None
    valid["difficulty"] = difficulty
    return valid


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
    """État persistant par chapitre — reprend au dernier step réussi. Ne
    lève jamais : toute erreur est journalisée, le pool renvoyé peut être
    partiel voire vide (reprise ciblée : seules les Séries en échec sont
    retentées au prochain appel, cf. failed_series_json)."""
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=chapter_code).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=chapter_code)
        db.add(row)
        db.flush()

    if row.step == "done":
        return row.validated_json or []

    row.attempts += 1
    is_geometry = chapter_code[:1] in exercise_gen.GEOMETRY_DOMAINS

    try:
        if row.step == "pending":
            start_idx, end_idx = sesamaths_pdf.chapter_page_range(
                doc, manual.toc_json, chapter_code)
            row.page_range_json = {"start_index": start_idx, "end_index": end_idx}
            row.step = "pages_located"
            db.commit()

        if row.step == "pages_located":
            start_idx = row.page_range_json["start_index"]
            end_idx = row.page_range_json["end_index"]
            row.raw_json = sesamaths_pdf.extract_chapter_raw(
                db, doc, manual, chapter_code, start_idx, end_idx)
            row.step = "raw_extracted"
            db.commit()

        if row.step == "raw_extracted":
            bundles = _bundle_series(row.raw_json.get("pages", []))
            validated = list(row.validated_json or [])
            failed = set(row.failed_series_json or [])
            existing_norms = {exercise_gen._normalize_statement_for_dedup(c["statement"])
                              for c in validated}
            for series_number, bundle in sorted(bundles.items()):
                if series_number in failed:
                    continue
                try:
                    raws = _structure_series(
                        db, manual.sha256, manual.grade_level, chapter_code,
                        competency.chapter_name, series_number, bundle["text"],
                        len(bundle["figures"]), is_geometry)
                except Exception as e:
                    logger.warning("Sésamaths : structuration Série %s (%s) échouée : %s",
                                   series_number, chapter_code, e)
                    failed.add(series_number)
                    continue
                for raw in raws:
                    candidate = _to_candidate(raw, bundle["figures"], competency, db,
                                              existing_norms)
                    if candidate is None:
                        continue
                    existing_norms.add(
                        exercise_gen._normalize_statement_for_dedup(candidate["statement"]))
                    validated.append(candidate)
            row.validated_json = validated
            row.failed_series_json = sorted(failed)
            row.step = "done"
            row.error_message = "" if validated else "Aucun exercice validé pour ce chapitre"
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
    """Moisson des exercices Sésamaths déjà validés du chapitre de
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
    """Complément DeepSeek Pro inspiré des exercices Sésamaths validés du
    même chapitre, vérifié par Claude Haiku (langage naturel) — seul usage
    de Claude dans cette pipeline, réservé à cette voie générée."""
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
            model=(settings.deepseek_model if source == "sesamaths"
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

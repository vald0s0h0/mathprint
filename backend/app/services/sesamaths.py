"""Pipeline Sésamaths (VISION) : extraction d'exercices depuis les manuels
scolaires PDF (collection Sésamath) — SEULE source d'exercices de l'app
depuis le 16/07 (la génération MathALÉA/DeepSeek a été retirée d'exercise_gen).

Pourquoi la vision. Les pages du manuel sont à deux colonnes denses (badges de
nombres, tableaux à compléter, figures géométriques). L'extraction TEXTE
(PyMuPDF) entrelace ces éléments et détruit la structure ; les figures sont par
nature illisibles en texte. On rend donc CHAQUE page en image et on l'extrait
avec un LLM multimodal, UN appel par page.

Architecture :
  1. CARTE (services.sesamaths_pdf) : la table des matières donne la page
     imprimée de chaque Série ; on en déduit la plage de pages fichier de
     chaque Série (hors « Culture »), sans jamais parcourir le manuel entier.
  2. EXTRACTION VISION (Claude Haiku 4.5, une fois par page, mise en cache par
     hash pdf+page+prompt+modèle+schéma) : lit l'IMAGE de la page et restitue
     TOUS ses exercices au contrat JSON de exercise_gen (statement/correction/
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
  5. Si le chapitre ne fournit pas assez d'exercices réels au niveau demandé,
     on ne comble PAS par de l'invention (le complément DeepSeek a été retiré) :
     la banque reste partielle, avec un avertissement explicite dans les logs.
  6. BANQUE : GeneratedExercise avec source="sesamaths", pool STRICTEMENT
     séparé de la banque par défaut.

Reprise sur erreur : l'état d'extraction d'un chapitre est persistant
(SesamathsChapterExtraction), machine à états PAR PAGE — seules les pages en
échec sont retentées au prochain appel.
"""
import hashlib
import json
import logging
import re
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CompetencyFramework, GeneratedExercise, SesamathsChapterExtraction, SesamathsLlmCache
from . import exercise_gen, providers, sesamaths_pdf

logger = logging.getLogger(__name__)

# Entre dans la clé du cache LLM : TOUJOURS le bumper en même temps qu'une
# modification de _VISION_EXTRACT_INTRO, sinon les réponses mises en cache par
# l'ANCIEN prompt sont resservies et le nouveau prompt reste sans effet.
PROMPT_VERSION = "sesamaths-8-expression-cells-fixes"
SOURCE_POOL = ("sesamaths", "sesamaths_deepseek")


class SesamathsExtractionError(RuntimeError):
    """L'extraction Sésamath n'a pas pu fournir d'exercices RÉELS du manuel
    (PDF introuvable, chapitre inconnu, extraction vision incomplète…). On la
    lève au lieu de retomber silencieusement sur une invention DeepSeek : le
    complément DeepSeek n'est autorisé QUE lorsque le chapitre a été
    entièrement extrait et qu'il faut ajuster le niveau (cf. ensure_bank)."""


# ================================================================ cache LLM

def _cache_key(*parts) -> str:
    material = "|".join(
        p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)
        for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_rate_limited(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) == 429


def _retry_after_s(exc: Exception, attempt: int) -> float:
    """Délai avant nouvel essai : en-tête `retry-after` si le serveur en donne
    un, sinon backoff exponentiel (2, 4, 8 s) — l'extraction enchaîne une page
    par seconde et sature sinon le quota Anthropic (cf. rafale de 429)."""
    resp = getattr(exc, "response", None)
    try:
        return max(1.0, float((resp.headers or {}).get("retry-after")))
    except (AttributeError, TypeError, ValueError):
        return float(2 ** (attempt + 1))


def _is_truncated(exc: Exception) -> bool:
    return "TRONQUÉE" in str(exc)


# Budgets de sortie essayés dans l'ordre : une page dense en table_fill
# multi-lignes (JSON verbeux, une cellule = un objet) peut dépasser 16000
# tokens. Sans ce palier, la page entière était perdue (exercices comptés en
# échec) au lieu d'être retentée avec plus de place (cf. incident série A1).
_VISION_TOKEN_BUDGETS = (16000, 32000, 48000)


def _cached_vision(db: Session, cache_key: str, model: str, system: str,
                   user_text: str, image_png: bytes, correlation_id: str) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = None
    for budget in _VISION_TOKEN_BUDGETS:
        for attempt in range(3):
            try:
                data = providers.claude_vision_json(
                    db, "sesamaths_vision_extract", system, user_text, image_png,
                    max_tokens=budget, model=model, correlation_id=correlation_id)
                break
            except Exception as e:
                if _is_rate_limited(e) and attempt < 2:
                    delay = _retry_after_s(e, attempt)
                    logger.info("Sésamaths : 429 sur %s, nouvel essai dans %.0f s "
                                "(tentative %s/3)", model, delay, attempt + 2)
                    time.sleep(delay)
                    continue
                if _is_truncated(e) and budget != _VISION_TOKEN_BUDGETS[-1]:
                    logger.info("Sésamaths : réponse tronquée sur %s à max_tokens=%s, "
                               "nouvel essai avec un budget plus élevé", model, budget)
                    break
                raise
        if data is not None:
            break
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# ============================================================ prompt vision

_VISION_EXTRACT_INTRO = (
    "Tu es un professeur agrégé de mathématiques. On te fournit l'IMAGE d'une "
    "page d'un manuel de §GRADE§ (collection Sésamath), Série §SERIES_NUMBER§ "
    "« §SERIES_NAME§ » du chapitre « §CHAPTER_NAME§ ». Extrais CHAQUE exercice "
    "numéroté de cette page, SANS EN OUBLIER, SANS en inventer.\n\n"
    "RÈGLE ABSOLUE, PLUS IMPORTANTE QUE TOUT LE RESTE : tu ne REFUSES ni "
    "n'OMETS JAMAIS un exercice sous prétexte que son format de réponse "
    "d'origine (Vrai/Faux, classement, opération posée, coloriage, tracé...) "
    "ne correspond à aucun type supporté tel quel. REFORMULE TOUJOURS la "
    "consigne pour qu'elle rentre dans un des formats de réponse listés plus "
    "bas ; si et SEULEMENT SI aucune reformulation n'est possible, utilise "
    "\"manual_drawing\" (dernier recours universel, valable pour N'IMPORTE "
    "QUEL exercice, pas seulement la géométrie). Il n'y a donc AUCUNE "
    "situation où un exercice doit être laissé de côté.\n\n"
    "CE QU'EST UN EXERCICE (règle la plus importante) :\n"
    "- UN exercice = UN badge numéroté (chiffre BLANC dans un petit carré de "
    "couleur), avec le titre qui le suit éventuellement. Le badge est le SEUL "
    "séparateur d'exercices.\n"
    "- Les sous-questions « a. », « b. », « c. »… d'un même badge NE SONT PAS des "
    "exercices : ce sont les CHAMPS DE RÉPONSE d'un seul et même exercice. Ne les "
    "sépare JAMAIS en plusieurs exercices, ne répète JAMAIS le même exercice.\n"
    "- Exemple : un badge « 4 Quotients et restes » avec « a. … » et « b. … » "
    "produit UN exercice, pas deux. Un badge « 12 Calcule chacun des produits » "
    "avec « a. » à « j. » produit UN exercice à 10 réponses, pas dix.\n"
    "- Le nombre d'exercices que tu renvoies doit être EXACTEMENT le nombre de "
    "badges numérotés de la page.\n\n"
    "STRUCTURE DES RÉPONSES (à respecter fidèlement) :\n"
    "- PLUSIEURS champs de réponse STRUCTURELLEMENT IDENTIQUES (a., b., c.… même "
    "forme de calcul, seuls les nombres changent, ou un vrai tableau imprimé) -> "
    "UN exercice \"table_fill\" avec UNE LIGNE PAR SOUS-QUESTION : \"row_labels\" = "
    "[\"a.\", \"b.\", …] (ou l'énoncé court de chaque sous-question), et une COLONNE "
    "PAR VALEUR attendue (ex. « quotient » et « reste » -> 2 colonnes ; un seul "
    "résultat -> col_labels = [\"Calcul\", \"Résultat\"]). Chaque ligne garde son "
    "énoncé propre dans row_labels ; \"statement\" ne porte que la consigne commune.\n"
    "- PLUSIEURS champs de réponse HÉTÉROGÈNES (phrases ou équations de formes "
    "différentes, pas une grille régulière) -> UN exercice \"multi_blank\" : place "
    "un marqueur {{blank}} à chaque endroit où l'élève écrit sa réponse, DANS "
    "\"statement\" qui contient alors le texte complet de toutes les sous-questions "
    "à la suite (garde leurs préfixes « a. », « b. »…), et fournis une valeur par "
    "{{blank}} dans answer.values, dans le même ordre.\n"
    "- UN SEUL champ de réponse -> \"short_text\".\n"
    "- Conserve l'ORDRE et le LIBELLÉ d'origine des sous-questions.\n\n"
    "RÈGLES D'EXTRACTION :\n"
    "- Restitue l'énoncé À L'IDENTIQUE (mêmes valeurs, même intention) ; corrige "
    "seulement les artefacts de mise en page.\n"
    "- Toute expression mathématique est balisée en LaTeX $...$ (cf. règles de "
    "format ci-dessous).\n"
    "- Une LISTE DE NOMBRES fournie dans des badges/encadrés fait partie de "
    "l'énoncé : recopie-la intégralement dans \"statement\".\n"
    "- Un TABLEAU à compléter doit être reconstruit en \"table_fill\" (l'élève y "
    "écrit ses réponses) : reprends libellés de lignes/colonnes et calcule les "
    "cellules attendues. Si une colonne du tableau imprimé donne déjà une valeur "
    "(ex. le calcul à effectuer) et qu'une seule colonne est à remplir par "
    "l'élève, marque les cellules déjà imprimées \"given\":true (elles seront "
    "recopiées telles quelles, pas transformées en case vide).\n"
    "- REFORMULE TOUJOURS la consigne pour qu'elle rentre dans l'un des types de "
    "réponse ci-dessous (ex. « quels nombres sont divisibles par 2 ? » -> QCM à "
    "choix multiples listant les nombres de l'énoncé ; « Vrai ou Faux ? » -> "
    "qcm_single à 2 choix ; « range dans l'ordre croissant » -> QCM listant des "
    "propositions d'ordre, ou short_text avec la séquence attendue en texte). "
    "N'utilise \"manual_drawing\" que si VRAIMENT aucune reformulation ne "
    "fonctionne — jamais pour éviter l'effort de reformuler.\n"
    "- IGNORE : les rubriques « Culture », les rappels de leçon (« À RETENIR »), "
    "les QR codes, en-têtes et pieds de page.\n"
    "- Si un exercice s'appuie sur une FIGURE (géométrie, droite graduée, repère, "
    "schéma) présente sur la page, n'essaie pas de la décrire : ajoute "
    "\"figure_ref\": {\"bbox_pct\": [x0, y0, x1, y1]} où (x0,y0)=coin haut-gauche "
    "et (x1,y1)=coin bas-droit de la figure, en fractions 0-1 de la page "
    "(x=largeur, y=hauteur).\n"
    "- Ajoute à chaque exercice \"difficulty\": entier 1 (découverte) à 5 (défi), "
    "relatif au niveau §GRADE§.\n"
    "- OBLIGATOIRE : \"correction\" ne doit JAMAIS être vide. Rédige la "
    "résolution complète (au moins une phrase, calcul détaillé puis résultat). "
    "Un exercice sans correction est REJETÉ.\n\n"
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
        # pourquoi, et pas seulement combien : sans ça un « 11 renvoyés, 0
        # validés » est indiagnosticable (cf. incident extraction A1).
        logger.warning("Sésamaths : exercice p%s REFUSÉ — %s | énoncé : %.90s",
                       page_idx, exercise_gen.diagnose_rejection(raw, competency),
                       str(raw.get("statement", "")).replace("\n", " "))
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

    errors: list[str] = []
    for model in (settings.claude_vision_model, settings.claude_vision_fallback_model):
        cache_key = _cache_key(manual.sha256, "page", str(idx), PROMPT_VERSION,
                              model, settings.sesamaths_schema_version)
        try:
            data = _cached_vision(db, cache_key, model, system, user_text, png,
                                  correlation_id=f"sesa-vis-{chapter_code}-p{idx}")
        except Exception as e:
            logger.warning("Sésamaths : extraction vision page %s (%s) échouée : %s",
                           idx, model, e)
            errors.append(f"{model}: {e}")
            continue
        raw_list = data.get("exercises") or []
        cands: list[dict] = []
        for raw in raw_list:
            c = _to_candidate(raw, doc, idx, competency, db, existing_norms, out_dir)
            if c is not None:
                cands.append(c)
        logger.info("Sésamaths : page %s (série %s) — modèle %s : %s exercice(s) "
                    "renvoyé(s), %s validé(s)", idx, page_meta.get("series_number"),
                    model, len(raw_list), len(cands))
        if cands:
            return cands
        if model == settings.claude_vision_fallback_model:
            return []
        logger.info("Sésamaths : page %s sans exercice valide en %s, repli %s",
                    idx, model, settings.claude_vision_fallback_model)
    # AUCUN modèle n'a répondu : c'est un ÉCHEC, pas une page vide. Sans cette
    # distinction la page serait marquée « done », le chapitre « complet », et
    # le complément DeepSeek autorisé à inventer à la place des vrais exercices.
    if errors:
        raise RuntimeError(f"aucun modèle vision n'a répondu ({' | '.join(errors)})")
    return []


# ================================================================ chapitre

def series_number_for(competency) -> int | None:
    """Numéro de Série du manuel correspondant à la compétence.

    Dans le manuel Sésamath une « Série » EST une compétence : le référentiel
    5e est aligné dessus (A1.1 « Automatismes » = Série 1 « Automatismes » du
    chapitre A1). Le suffixe du code compétence donne donc directement la
    Série, et l'extraction ne lit que SES pages — pas tout le chapitre."""
    code = (getattr(competency, "code", "") or "")
    m = re.search(r"\.(\d+)$", code.strip())
    return int(m.group(1)) if m else None


def _extraction_key(competency, chapter_code: str) -> str:
    """Clé de l'état d'extraction : le code COMPÉTENCE (= une Série du manuel),
    pas le chapitre. UNE SEULE définition : lecture et écriture doivent utiliser
    la même, sinon on relit la ligne d'un autre périmètre (une extraction
    partielle peut alors passer pour complète et rouvrir l'invention DeepSeek)."""
    return getattr(competency, "code", "") or chapter_code


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
    # L'état est persisté PAR COMPÉTENCE (= par Série du manuel), pas par
    # chapitre : on n'extrait que les pages de la Série demandée, donc une
    # poignée de pages au lieu des ~17 du chapitre.
    extraction_key = _extraction_key(competency, chapter_code)
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=extraction_key).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=extraction_key)
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
            series_no = series_number_for(competency)
            if series_no is not None:
                scoped = [p for p in pages if p.get("series_number") == series_no]
                if scoped:
                    pages = scoped
                else:
                    logger.warning("Sésamaths : aucune page pour la Série %s du "
                                   "chapitre %s — repli sur tout le chapitre",
                                   series_no, chapter_code)
            row.page_range_json = {"pages": pages, "done_pages": []}
            row.step = "pages_located"
            logger.info("Sésamaths : %s « %s » (chapitre %s, Série %s) — %s page(s) "
                        "d'exercices ciblée(s) : %s", extraction_key,
                        getattr(competency, "label", ""), chapter_code,
                        series_no if series_no is not None else "?",
                        len(pages), [p["index"] for p in pages])
            db.commit()

        if row.step == "pages_located":
            pages = row.page_range_json.get("pages", [])
            done = set(row.page_range_json.get("done_pages", []))
            pool = list(row.validated_json or [])
            existing_norms = {exercise_gen._normalize_statement_for_dedup(c["statement"])
                              for c in pool}
            todo = [p for p in pages if p["index"] not in done]
            logger.info("Sésamaths : %s — extraction vision de %s page(s) restante(s) "
                        "(%s déjà faite(s), %s exercice(s) en pool)",
                        extraction_key, len(todo), len(done), len(pool))
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
            logger.info("Sésamaths : %s — extraction %s : %s exercice(s) réel(s) "
                        "extrait(s), %s page(s) en échec %s",
                        extraction_key, "terminée" if not failed else "PARTIELLE",
                        len(pool), len(failed), failed or "")
            db.commit()
    except Exception as e:
        row.error_message = str(e)[:2000]
        logger.error("Sésamaths : extraction %s en échec (step=%s) : %s",
                    chapter_code, row.step, e)
        db.commit()

    return row.validated_json or []


def chapter_pool(db: Session, competency) -> list[dict]:
    """Pool d'exercices RÉELS extraits du chapitre (best-effort, jamais
    d'exception). Pour la génération de banque, préférer `_extracted_chapter`
    qui distingue « manuel introuvable » de « extraction complète »."""
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None or chapter_code is None:
        return []
    return ensure_chapter_pool(db, doc, manual, chapter_code, competency)


def _extracted_chapter(db: Session, competency) -> tuple[list[dict], bool]:
    """Extrait (ou récupère) le pool d'exercices RÉELS du chapitre et indique
    si l'extraction est COMPLÈTE (toutes les pages traitées, aucune en échec).

    Lève SesamathsExtractionError si le manuel est introuvable ou le chapitre
    absent : dans ce cas on NE retombe PAS sur une invention DeepSeek, on
    remonte un message clair à l'appelant."""
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None or chapter_code is None:
        detail = (manual.error_message if manual and manual.error_message
                  else f"chapitre {competency.chapter_code} absent du manuel")
        raise SesamathsExtractionError(
            f"Le PDF du manuel Sésamath est introuvable (ou le chapitre "
            f"{competency.chapter_code} en est absent) — les exercices n'ont "
            f"pas pu être extraits. Détail : {detail}")
    pool = ensure_chapter_pool(db, doc, manual, chapter_code, competency)
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id,
                      chapter_code=_extraction_key(competency, chapter_code)).first())
    fully_done = bool(row and row.step == "done" and not (row.failed_series_json or []))
    return pool, fully_done


def harvest(db: Session, competency, level: int, need: int,
           existing_norms: set[str], pool: list[dict]) -> list[dict]:
    """Moisson des exercices Sésamaths déjà extraits du chapitre de
    `competency`, filtrés au niveau demandé."""
    if need <= 0:
        return []
    out = []
    for cand in pool:
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

    # Extraction RÉELLE d'abord. Lève SesamathsExtractionError (message clair,
    # non bloquant en amont) si le manuel est introuvable — AUCUNE invention
    # DeepSeek à la place d'exercices qu'on n'a pas su extraire.
    pool, fully_done = _extracted_chapter(db, competency)
    logger.info("Sésamaths : banque %s niveau %s — %s variante(s) en stock, %s à "
                "produire ; %s exercice(s) réel(s) extrait(s) du chapitre "
                "(extraction %s)", competency.code, level, len(rows), missing,
                len(pool), "complète" if fully_done else "INCOMPLÈTE")

    existing_norms = {
        exercise_gen._normalize_statement_for_dedup(ex.statement)
        for ex in db.query(GeneratedExercise)
        .filter(GeneratedExercise.competency_id == competency.id,
               GeneratedExercise.status == "active",
               GeneratedExercise.source.in_(SOURCE_POOL)).all()}

    added: list[GeneratedExercise] = []
    next_variant = len(rows)

    def _store(candidate: dict, verdict: dict) -> None:
        nonlocal next_variant
        row = GeneratedExercise(
            competency_id=competency.id, difficulty_level=level, variant=next_variant,
            statement=candidate["statement"], correction=candidate["correction"],
            response_type=candidate["response_type"],
            expected_json=candidate["expected"], grading_json=candidate["grading"],
            model=settings.claude_vision_model,
            prompt_version=PROMPT_VERSION, status="active",
            verifier_model="", verifier_verdict_json=verdict,
            quality_json=verdict.get("scores") or {},
            figure_json=candidate.get("figure_json"), source="sesamaths",
            kind=candidate.get("kind", "application"))
        db.add(row)
        added.append(row)
        next_variant += 1

    for cand in harvest(db, competency, level, missing, existing_norms, pool):
        _store(cand, cand.get("_verdict", {}))
    missing = min_variants - len(rows) - len(added)

    # Aucun complément généré : depuis le 16/07, seule l'extraction vision
    # du manuel produit des exercices. Si le chapitre n'en fournit pas assez
    # au niveau demandé, on préfère un pool partiel mais RÉEL à une invention.
    if missing > 0:
        logger.warning("Sésamaths : %s exercice(s) réel(s) au niveau %s pour %s, "
                       "%s manquant(s) — pas de complément généré (extraction %s)",
                       len(added), level, competency.code, missing,
                       "complète" if fully_done else "INCOMPLÈTE")

    db.flush()
    if not rows and not added:
        if not fully_done:
            raise SesamathsExtractionError(
                f"Extraction Sésamath incomplète pour {competency.code} "
                f"(chapitre {competency.chapter_code}) : aucun exercice réel "
                f"disponible au niveau {level}. Les exercices n'ont pas pu être "
                f"extraits — réessayez, l'extraction reprendra les pages en échec.")
        raise ValueError(
            f"Aucun exercice Sésamaths n'a passé les contrôles qualité pour "
            f"{competency.code} niveau {level}")
    logger.info("Sésamaths : banque %s niveau %s prête : %s variante(s) réelle(s)",
                competency.code, level, len(rows) + len(added))
    return rows + added

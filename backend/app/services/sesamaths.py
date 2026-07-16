"""Pipeline Sésamaths : extraction d'exercices depuis les manuels scolaires PDF
(collection Sésamath) — SEULE source d'exercices de l'app depuis le 16/07.

Architecture à 2 appels LLM par page (16/07 soir) :
  1. EXTRACTEUR (vision, Claude Haiku 4.5, repli Opus 4.8 sur page dense) :
     lit l'IMAGE d'une page et transcrit fidèlement chaque exercice — texte
     avec marqueurs de zone de réponse ({{blank}}/{{lineN}}/{{check}}/{{dot}},
     cf. `_EXTRACT_INTRO`), tableaux et associations bruts, figures. N'invente
     RIEN, ne résout RIEN, ne choisit aucun format de réponse app. Résultat
     mis en cache dans `SesamathsChapterExtraction.raw_json` (par page).
  2. ADAPTATEUR (texte, mêmes modèles) : reçoit le JSON brut d'une page et le
     transforme en exercices au contrat app — choisit response_type/answer à
     partir des marqueurs, RÉSOUT l'exercice (rédige la correction, calcule
     les réponses — l'extracteur n'en fournit aucune), attribue la difficulté,
     reformule les consignes pour rester cohérentes avec le type choisi
     (« Entoure » -> « Coche » si converti en QCM).
  3. VALIDATION DÉTERMINISTE : chaque candidat adapté repasse par
     exercise_gen._validate_exercise (LaTeX, types de réponse, auto-vérif,
     garde-fou anti-marqueur-non-transformé) — aucune duplication de logique.
  4. FIGURES : recadrage PNG raster pur (jamais vectoriel) depuis le bbox
     renvoyé par l'extracteur, via sesamaths_pdf.crop_bbox_png.

Pourquoi 2 appels plutôt qu'1. Sur les sessions précédentes, chaque bug fix
touchait la partie « adaptation au format app » (bornes table_fill, QCM 2
choix, multi_blank, normalisation LaTeX…), jamais la fidélité de lecture. Un
seul appel obligeait à repayer la vision (coûteuse, image) à chaque itération
sur une règle de format. Avec 2 appels : bumper ADAPT_PROMPT_VERSION (ou
settings.sesamaths_schema_version) déclenche une RÉ-ADAPTATION depuis le JSON
brut déjà en cache, sans repayer la vision ; seul un bump de
EXTRACT_PROMPT_VERSION force une ré-extraction complète (cf.
ensure_chapter_pool, section « retraitement automatique »).

Reprise sur erreur : l'état d'extraction d'un chapitre est persistant
(SesamathsChapterExtraction), machine à états PAR PAGE ET PAR PHASE (extraite
puis adaptée) — seules les pages/phases en échec sont retentées au prochain
appel, sans jamais bloquer l'utilisation des pages déjà traitées avec succès.
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

# Entrent dans la clé du cache LLM : TOUJOURS bumper la version concernée en
# même temps qu'une modification du prompt correspondant, sinon les réponses
# mises en cache par l'ANCIEN prompt sont resservies et le nouveau reste sans
# effet. EXTRACT_PROMPT_VERSION ne concerne QUE `_EXTRACT_INTRO` (fidélité de
# lecture) ; ADAPT_PROMPT_VERSION ne concerne QUE `_ADAPT_INTRO` +
# exercise_gen._RESPONSE_FORMAT_BLOCK (choix de format/résolution) — les
# bumper séparément est tout l'intérêt de la séparation en 2 appels.
EXTRACT_PROMPT_VERSION = "sesamaths-extract-1"
ADAPT_PROMPT_VERSION = "sesamaths-adapt-1"
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


# Budgets de sortie essayés dans l'ordre, partagés par les 2 appels (vision et
# texte) : une page dense en table_fill multi-lignes (JSON verbeux, une
# cellule = un objet) peut dépasser 16000 tokens. Sans ce palier, l'appel
# entier était perdu (page comptée en échec) au lieu d'être retenté avec plus
# de place (cf. incident série A1).
_TOKEN_BUDGETS = (16000, 32000, 48000)


def _cached_vision(db: Session, cache_key: str, model: str, system: str,
                   user_text: str, image_png: bytes, correlation_id: str) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = None
    for budget in _TOKEN_BUDGETS:
        for attempt in range(3):
            try:
                data = providers.claude_vision_json(
                    db, "sesamaths_extract", system, user_text, image_png,
                    max_tokens=budget, model=model, correlation_id=correlation_id)
                break
            except Exception as e:
                if _is_rate_limited(e) and attempt < 2:
                    delay = _retry_after_s(e, attempt)
                    logger.info("Sésamaths : 429 sur %s, nouvel essai dans %.0f s "
                                "(tentative %s/3)", model, delay, attempt + 2)
                    time.sleep(delay)
                    continue
                if _is_truncated(e) and budget != _TOKEN_BUDGETS[-1]:
                    logger.info("Sésamaths : réponse tronquée sur %s à max_tokens=%s, "
                               "nouvel essai avec un budget plus élevé", model, budget)
                    break
                raise
        if data is not None:
            break
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


def _cached_adapt(db: Session, cache_key: str, model: str, system: str,
                  payload: dict, correlation_id: str) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = None
    for budget in _TOKEN_BUDGETS:
        for attempt in range(3):
            try:
                data = providers.claude_json(
                    db, "sesamaths_adapt", system, payload,
                    max_tokens=budget, model=model, correlation_id=correlation_id)
                break
            except Exception as e:
                if _is_rate_limited(e) and attempt < 2:
                    delay = _retry_after_s(e, attempt)
                    logger.info("Sésamaths : 429 sur %s, nouvel essai dans %.0f s "
                                "(tentative %s/3)", model, delay, attempt + 2)
                    time.sleep(delay)
                    continue
                if _is_truncated(e) and budget != _TOKEN_BUDGETS[-1]:
                    logger.info("Sésamaths : réponse tronquée sur %s à max_tokens=%s, "
                               "nouvel essai avec un budget plus élevé", model, budget)
                    break
                raise
        if data is not None:
            break
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# ===================================================== prompt 1 : extracteur

_EXTRACT_INTRO = (
    "Tu es un moteur d'extraction multimodale spécialisé dans les pages de "
    "manuels scolaires de mathématiques de §GRADE§ (collection Sésamath).\n\n"
    "Ta mission est de transformer une page PNG en données structurées "
    "fidèles, destinées à une banque d'exercices : les exercices présents, "
    "leurs numéros, énoncés et sous-questions, les données mathématiques, "
    "les tableaux, les listes de choix, les figures, et les zones où l'élève "
    "doit répondre.\n\n"
    "Tu effectues UNIQUEMENT une extraction fidèle. Tu ne résous JAMAIS les "
    "exercices, tu n'inventes AUCUNE réponse, AUCUNE correction, AUCUNE "
    "difficulté : ces informations ne font pas partie de ta tâche (un autre "
    "passage s'en charge).\n\n"
    "# Règles générales\n"
    "1. Extrais tous les exercices numérotés visibles, dans leur ordre de "
    "lecture (colonnes, sous-questions, séparateurs visuels).\n"
    "2. UN exercice = UN badge numéroté (chiffre dans un petit carré de "
    "couleur), avec son titre éventuel. Les sous-questions « a. », « b. », "
    "« c. »… d'un même badge NE SONT PAS des exercices séparés : elles font "
    "partie du texte du même exercice. Le nombre d'exercices renvoyés doit "
    "être EXACTEMENT le nombre de badges numérotés de la page.\n"
    "3. Ne reformule pas les énoncés. Corrige seulement les erreurs "
    "manifestes d'OCR (mise en page, caractères mal reconnus).\n"
    "4. Conserve la ponctuation, les unités, les nombres décimaux (virgule "
    "française) et la structure des sous-questions.\n"
    "5. Toute expression mathématique est balisée en LaTeX entre $...$ "
    "(ex. $17,65 - 4,20$, $2,4\\ \\text{m}^3$, $[AC]$). Ne place jamais une "
    "phrase entière dans une formule LaTeX.\n"
    "6. N'invente ni ne déduis aucune information absente ou illisible ; si "
    "un contenu est incertain, conserve la meilleure lecture possible.\n"
    "7. Ignore : rubriques « Culture », rappels de leçon (« À RETENIR »), QR "
    "codes, en-têtes et pieds de page.\n"
    "8. N'utilise jamais de Markdown autour du JSON, réponds uniquement par "
    "l'objet JSON demandé.\n\n"
    "# Syntaxe des marqueurs de réponse\n"
    "Chaque endroit où l'élève doit écrire, cocher ou relier devient un "
    "marqueur inséré DIRECTEMENT dans le texte, à la position exacte où il "
    "apparaît sur la page (au plus proche possible de la mise en page "
    "d'origine) :\n"
    "- `{{blank}}` : une case ou un pointillé COURT, pour une réponse tenant "
    "en quelques caractères (un nombre, un mot, un résultat de calcul), au "
    "milieu ou en fin de phrase. Ex. : « a. $7 \\times 8 =$ {{blank}} ».\n"
    "- `{{lineN}}` (N = nombre entier, ex. `{{line2}}`, `{{line4}}`) : un "
    "espace ou des lignes réglées destinées à un raisonnement rédigé sur "
    "plusieurs lignes ; N = le nombre de lignes visibles ou l'espace laissé "
    "(estime-le si besoin, entre 1 et 12).\n"
    "- `{{check}}` : une case à cocher, un cercle ou un carré à cocher/"
    "entourer, placé JUSTE À CÔTÉ du texte du choix concerné. Ex. : « Vrai "
    "{{check}}  Faux {{check}} ».\n"
    "- `{{dot}}` : un point, une puce ou une extrémité de trait à relier "
    "(exercice d'association), placé JUSTE À CÔTÉ du texte de l'item "
    "concerné, du côté où il apparaît. Ex. : « $2 \\times 4$ {{dot}} » à "
    "gauche et « {{dot}} $8$ » à droite.\n"
    "N'insère JAMAIS ces marqueurs à l'intérieur d'un bloc LaTeX $...$. Ne "
    "décris jamais en mots ce qu'un marqueur représente déjà — le marqueur "
    "suffit.\n\n"
    "# Tableaux et associations\n"
    "Si la page imprime un VRAI tableau à compléter (grille de lignes/"
    "colonnes), reconstruis-le dans le champ \"table\" plutôt que d'y mettre "
    "des marqueurs {{blank}} dispersés dans le texte : reprends les libellés "
    "de lignes/colonnes, une cellule = {\"value\": contenu déjà imprimé ou "
    "null, \"given\": true si cette cellule est déjà imprimée (non à "
    "compléter par l'élève) sinon false}. N'utilise jamais \"...\" comme "
    "valeur de cellule à la place de null.\n"
    "Si la page présente une association de deux colonnes (points/traits à "
    "relier), reconstruis-la dans le champ \"matching\" (left_items, "
    "right_items, mode) si les colonnes sont longues ou complexes à "
    "linéariser ; pour une association simple de 2-3 paires apparaissant en "
    "ligne, les marqueurs {{dot}} inline suffisent.\n\n"
    "# Figures\n"
    "Si un exercice s'appuie sur une figure (géométrie, droite graduée, "
    "repère, schéma, diagramme), n'essaie pas de la décrire mathématiquement : "
    "ajoute un objet dans \"figures\" avec \"bbox\": {\"x\":, \"y\":, "
    "\"width\":, \"height\":} (fractions 0-1 de la page, x/y = coin "
    "haut-gauche, width/height = dimensions) et une courte \"description\".\n\n"
    "Réponds UNIQUEMENT en JSON strictement valide :\n"
    '{"exercises":[{"number":str,"title":str|null,"text":str,'
    '"table":{"rows":int,"cols":int,"col_labels":[str]?,"row_labels":[str]?,'
    '"cells":[[{"value":"str|null","given":bool}]]}?,'
    '"matching":{"left_items":[str],"right_items":[str],'
    '"mode":"one_to_one"|"many_to_one"|"one_to_many"|"unknown"}?,'
    '"figures":[{"bbox":{"x":float,"y":float,"width":float,"height":float},'
    '"description":str}]?}]}'
)


def _extract_system(grade: str) -> str:
    return _EXTRACT_INTRO.replace("§GRADE§", grade)


# ===================================================== prompt 2 : adaptateur

_ADAPT_INTRO = (
    "Tu es un professeur agrégé de mathématiques. On te fournit l'extraction "
    "BRUTE (JSON) d'une page d'un manuel de §GRADE§ (collection Sésamath), "
    "Série §SERIES_NUMBER§ « §SERIES_NAME§ » du chapitre « §CHAPTER_NAME§ ». "
    "Cette extraction vient d'un premier passage fidèle qui n'a RIEN résolu "
    "et RIEN inventé : chaque exercice contient un texte brut avec des "
    "marqueurs de zone de réponse ({{blank}}, {{lineN}}, {{check}}, {{dot}}) "
    "et, le cas échéant, un tableau ou une association bruts.\n\n"
    "Ta mission : transformer CHAQUE exercice brut en exercice complet et "
    "noté, prêt à être imprimé et corrigé automatiquement. Pour chacun, tu "
    "dois RÉSOUDRE l'exercice (rédiger \"correction\" — la résolution "
    "complète, étape par étape, jamais vide), choisir le type de réponse de "
    "la plateforme et construire \"answer\" en conséquence, et attribuer "
    "\"difficulty\" (entier 1 à 5, relatif au niveau §GRADE§).\n\n"
    "RÈGLE ABSOLUE, PLUS IMPORTANTE QUE TOUT LE RESTE : tu ne REFUSES ni "
    "n'OMETS JAMAIS un exercice sous prétexte que son format de réponse "
    "d'origine ne correspond à aucun type supporté tel quel. REFORMULE "
    "TOUJOURS la consigne pour qu'elle rentre dans un des formats listés "
    "plus bas ; si et SEULEMENT SI aucune reformulation n'est possible, "
    "utilise \"manual_drawing\" (dernier recours universel, valable pour "
    "N'IMPORTE QUEL exercice, pas seulement la géométrie). Il n'y a donc "
    "AUCUNE situation où un exercice doit être omis.\n\n"
    "CONTRAINTE DE CORRESPONDANCE : renvoie EXACTEMENT un exercice adapté "
    "par exercice brut reçu, dans le MÊME ORDRE — ne fusionne jamais deux "
    "exercices bruts, ne scinde jamais un exercice brut en plusieurs (la "
    "frontière des exercices a déjà été fixée par l'extraction).\n\n"
    "# Interprétation des marqueurs du texte brut\n"
    "- `{{blank}}` : case de réponse courte. Conserve-la telle quelle si "
    "l'exercice n'a qu'une ou deux cases (\"short_text\"/\"multi_blank\") ; "
    "si l'exercice compte PLUS de 4-5 cases {{blank}} (beaucoup de "
    "sous-questions structurellement identiques), NE LES GARDE PAS "
    "dispersées dans le texte : regroupe-les dans un \"table_fill\" (une "
    "ligne par sous-question) — un tableau donne à l'élève des limites "
    "visuelles claires (cadre), indispensables pour un recadrage OCR fiable, "
    "alors que des cases éparpillées dans un paragraphe n'en donnent aucune.\n"
    "- `{{lineN}}` : zone de rédaction de N lignes. Si l'exercice n'a QUE "
    "cette zone et que la réponse attendue est un résultat unique (pas un "
    "raisonnement à étapes), remplace-la simplement par une réponse "
    "\"short_text\" (answer.type=\"text\", la zone par défaut suffit). Sinon "
    "(raisonnement identifiable en plusieurs étapes, ou N ≥ 3), utilise "
    "\"multiline_text\" avec answer.type=\"rubric\" (2 à 6 étapes), "
    "\"lines\" ≈ N (somme si plusieurs {{lineN}} dans le même exercice). "
    "RETIRE le marqueur {{lineN}} du \"statement\" final : la zone de "
    "rédaction est ajoutée automatiquement par le rendu, elle ne doit plus "
    "apparaître dans le texte.\n"
    "- `{{check}}` : case à cocher inline. Le texte immédiatement adjacent à "
    "chaque {{check}} est le libellé d'un choix. Construis \"choices\" dans "
    "leur ordre d'apparition, \"response_type\"=\"qcm_single\" (une seule "
    "coche attendue, ex. Vrai/Faux) ou \"qcm_multiple\" (plusieurs coches "
    "possibles). RETIRE tous les {{check}} et le texte des choix déjà "
    "recopié dans \"choices\" du \"statement\" final : les cases sont "
    "dessinées automatiquement sous l'énoncé, à partir de \"choices\".\n"
    "- `{{dot}}` : point à relier inline. Le texte adjacent à chaque {{dot}} "
    "(à gauche ou à droite selon sa position) devient un élément de "
    "\"left\"/\"right\". \"response_type\"=\"matching\". RETIRE tous les "
    "{{dot}} du \"statement\" final : les points sont dessinés "
    "automatiquement, à partir de \"left\"/\"right\".\n"
    "Le \"statement\" final ne doit donc plus JAMAIS contenir {{lineN}}, "
    "{{check}} ou {{dot}} — seul {{blank}} peut y subsister.\n\n"
    "# Reformulation cohérente avec le type de réponse choisi\n"
    "Si tu changes de type de réponse par rapport à l'original, reformule "
    "AUSSI le verbe d'instruction pour rester cohérent avec ce que l'élève "
    "fait réellement sur sa copie : « Entoure »/« Souligne »/« Barre » "
    "deviennent « Coche » si tu choisis un QCM ; « Relie » reste correct "
    "pour un matching ; un « Vrai ou Faux ? » devient un qcm_single à 2 "
    "choix (choices=[\"Vrai\",\"Faux\"]).\n\n"
    "# Tableaux et associations bruts\n"
    "Si l'exercice brut contient un champ \"table\", transforme-le en "
    "réponse \"table_fill\" (mêmes lignes/colonnes, \"given\":true recopié à "
    "l'identique). Si l'exercice brut contient un champ \"matching\", "
    "transforme-le en réponse \"matching\" (mêmes left/right).\n\n"
)


def _adapt_system(grade: str, chapter_name: str, series_number, series_name: str,
                  is_geometry: bool) -> str:
    format_block = exercise_gen._RESPONSE_FORMAT_BLOCK.replace(
        "{geometry_rules}", exercise_gen._GEOMETRY_RULES if is_geometry else "")
    # .replace (et non .format) : le prompt contient des accolades JSON littérales
    intro = (_ADAPT_INTRO
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


def _extract_page_raw(db: Session, doc, manual, page_meta: dict, grade: str,
                      chapter_code: str) -> dict:
    """Appel 1 (vision) : extraction fidèle d'UNE page — aucune résolution,
    aucun choix de format app. Essaie Haiku puis, si aucun exercice renvoyé,
    repli Opus 4.8 (page dense). Cache par (pdf, page, prompt, modèle)."""
    idx = page_meta["index"]
    system = _extract_system(grade)
    user_text = ("Extrais fidèlement CHAQUE exercice numéroté de cette page au "
                 "format JSON demandé. N'en oublie aucun, n'en invente aucun, "
                 "ne résous rien.")
    png = sesamaths_pdf.render_page_png(doc, idx)

    errors: list[str] = []
    for model in (settings.claude_vision_model, settings.claude_vision_fallback_model):
        cache_key = _cache_key(manual.sha256, "extract", str(idx), EXTRACT_PROMPT_VERSION, model)
        try:
            data = _cached_vision(db, cache_key, model, system, user_text, png,
                                  correlation_id=f"sesa-ext-{chapter_code}-p{idx}")
        except Exception as e:
            logger.warning("Sésamaths : extraction brute page %s (%s) échouée : %s",
                           idx, model, e)
            errors.append(f"{model}: {e}")
            continue
        n = len(data.get("exercises") or [])
        logger.info("Sésamaths : extraction brute page %s (série %s) — modèle %s : "
                    "%s exercice(s)", idx, page_meta.get("series_number"), model, n)
        if n or model == settings.claude_vision_fallback_model:
            return data
        logger.info("Sésamaths : page %s sans exercice en %s, repli %s",
                    idx, model, settings.claude_vision_fallback_model)
    raise RuntimeError(f"aucun modèle vision n'a répondu ({' | '.join(errors)})")


def _adapt_page(db: Session, raw: dict, page_meta: dict, chapter_code: str,
                competency, is_geometry: bool, grade: str,
                existing_norms: set[str], doc, out_dir) -> list[dict]:
    """Appel 2 (texte) : adapte l'extraction brute d'UNE page au contrat app
    (format de réponse, résolution, difficulté). Essaie Haiku puis, si aucun
    candidat validé, repli Opus 4.8 texte — sans repayer la vision, le JSON
    brut est déjà en main."""
    idx = page_meta["index"]
    raw_exercises = raw.get("exercises") or []
    if not raw_exercises:
        return []
    payload = {"exercises": raw_exercises}

    errors: list[str] = []
    for model in (settings.claude_adapt_model, settings.claude_adapt_fallback_model):
        system = _adapt_system(grade, competency.chapter_name,
                               page_meta.get("series_number"),
                               page_meta.get("series_name", ""), is_geometry)
        cache_key = _cache_key("adapt", ADAPT_PROMPT_VERSION, model,
                              settings.sesamaths_schema_version, chapter_code,
                              page_meta.get("series_number"), raw_exercises)
        try:
            data = _cached_adapt(db, cache_key, model, system, payload,
                                 correlation_id=f"sesa-adp-{chapter_code}-p{idx}")
        except Exception as e:
            logger.warning("Sésamaths : adaptation page %s (%s) échouée : %s",
                           idx, model, e)
            errors.append(f"{model}: {e}")
            continue
        adapted = data.get("exercises") or []
        if len(adapted) != len(raw_exercises):
            logger.warning("Sésamaths : adaptation page %s (%s) — %s exercice(s) "
                           "brut(s), %s adapté(s) (correspondance 1:1 attendue) : "
                           "candidats ignorés", idx, model, len(raw_exercises), len(adapted))
            errors.append(f"{model}: correspondance 1:1 rompue")
            continue
        cands: list[dict] = []
        for raw_item, item in zip(raw_exercises, adapted):
            c = _to_candidate(item, doc, idx, competency, db, existing_norms, out_dir)
            if c is not None:
                c["raw_extract_json"] = raw_item
                cands.append(c)
        logger.info("Sésamaths : adaptation page %s — modèle %s : %s/%s validé(s)",
                    idx, model, len(cands), len(adapted))
        if cands:
            return cands
        if model == settings.claude_adapt_fallback_model:
            return []
        logger.info("Sésamaths : page %s sans candidat validé en %s, repli %s",
                    idx, model, settings.claude_adapt_fallback_model)
    if errors:
        raise RuntimeError(f"aucun modèle d'adaptation n'a répondu ({' | '.join(errors)})")
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
    """État persistant par chapitre — machine à états PAR PAGE ET PAR PHASE
    (extraction vision puis adaptation texte). Ne lève jamais : toute erreur
    est journalisée, le pool renvoyé peut être partiel (reprise ciblée : seule
    la phase/page en échec est retentée au prochain appel, sans jamais
    bloquer l'usage des pages déjà traitées avec succès)."""
    extraction_key = _extraction_key(competency, chapter_code)
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=extraction_key).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=extraction_key)
        db.add(row)
        db.flush()

    current_versions = {"extract": EXTRACT_PROMPT_VERSION, "adapt": ADAPT_PROMPT_VERSION,
                        "schema": settings.sesamaths_schema_version}
    if row.step == "done":
        stored = (row.page_range_json or {}).get("versions", {})
        if stored.get("extract") != current_versions["extract"]:
            logger.info("Sésamaths : %s — version d'extraction changée (%s -> %s), "
                        "ré-extraction complète", extraction_key,
                        stored.get("extract"), current_versions["extract"])
            row.step = "pending"
            row.raw_json = {}
            row.validated_json = []
            row.page_range_json = {}
            db.commit()
        elif (stored.get("adapt") != current_versions["adapt"]
              or stored.get("schema") != current_versions["schema"]):
            logger.info("Sésamaths : %s — version d'adaptation changée (%s -> %s), "
                        "ré-adaptation depuis le JSON brut déjà en cache (aucun "
                        "nouvel appel vision)", extraction_key,
                        stored.get("adapt"), current_versions["adapt"])
            row.validated_json = []
            pr = dict(row.page_range_json or {})
            pr["adapted_pages"] = []
            row.page_range_json = pr
            db.commit()
        else:
            return row.validated_json or []

    row.attempts += 1
    # géométrie : chapitres du domaine B (5e) ou compétence en domaine EG/GM
    is_geometry = (competency.domain_code in exercise_gen.GEOMETRY_DOMAINS
                   or chapter_code[:1] == "B")
    out_dir = settings.data_dir / "sesamaths" / manual.grade_level / chapter_code
    grade = manual.grade_level

    try:
        if not (row.page_range_json or {}).get("pages"):
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
            row.page_range_json = {"pages": pages, "extracted_pages": [], "adapted_pages": []}
            row.step = "pages_located"
            logger.info("Sésamaths : %s « %s » (chapitre %s, Série %s) — %s page(s) "
                        "d'exercices ciblée(s) : %s", extraction_key,
                        getattr(competency, "label", ""), chapter_code,
                        series_no if series_no is not None else "?",
                        len(pages), [p["index"] for p in pages])
            db.commit()

        pages = row.page_range_json.get("pages", [])
        extracted = set(row.page_range_json.get("extracted_pages", []))
        adapted = set(row.page_range_json.get("adapted_pages", []))
        raw_json = dict(row.raw_json or {})
        pool = list(row.validated_json or [])

        # --------- phase 1 : extraction vision des pages manquantes ---------
        extract_todo = [p for p in pages if p["index"] not in extracted]
        logger.info("Sésamaths : %s — extraction vision de %s page(s) restante(s) "
                    "(%s déjà faite(s))", extraction_key, len(extract_todo), len(extracted))
        extract_failed: list[int] = []
        for pg in extract_todo:
            try:
                raw = _extract_page_raw(db, doc, manual, pg, grade, chapter_code)
            except Exception as e:
                logger.warning("Sésamaths : extraction page %s (%s) en échec : %s",
                               pg["index"], chapter_code, e)
                extract_failed.append(pg["index"])
                continue
            raw_json[str(pg["index"])] = raw
            extracted.add(pg["index"])
        row.raw_json = raw_json
        pr = dict(row.page_range_json)
        pr["extracted_pages"] = sorted(extracted)
        row.page_range_json = pr
        if extracted:
            row.step = "raw_extracted"
        db.commit()

        # --------- phase 2 : adaptation texte des pages extraites -----------
        existing_norms = {exercise_gen._normalize_statement_for_dedup(c["statement"])
                          for c in pool}
        adapt_todo = [p for p in pages if p["index"] in extracted and p["index"] not in adapted]
        logger.info("Sésamaths : %s — adaptation de %s page(s) restante(s) "
                    "(%s déjà faite(s), %s exercice(s) en pool)",
                    extraction_key, len(adapt_todo), len(adapted), len(pool))
        adapt_failed: list[int] = []
        for pg in adapt_todo:
            raw = raw_json.get(str(pg["index"]))
            try:
                cands = _adapt_page(db, raw, pg, chapter_code, competency,
                                    is_geometry, grade, existing_norms, doc, out_dir)
            except Exception as e:
                logger.warning("Sésamaths : adaptation page %s (%s) en échec : %s",
                               pg["index"], chapter_code, e)
                adapt_failed.append(pg["index"])
                continue
            pool.extend(cands)
            adapted.add(pg["index"])
        row.validated_json = pool
        pr = dict(row.page_range_json)
        pr["adapted_pages"] = sorted(adapted)
        row.page_range_json = pr

        still_pending = sorted({p["index"] for p in pages if p["index"] not in adapted})
        row.failed_series_json = still_pending
        if not still_pending:
            row.step = "done"
            row.page_range_json = {**row.page_range_json, "versions": current_versions}
            row.error_message = "" if pool else "Aucun exercice validé pour ce chapitre"
        else:
            row.step = "raw_extracted" if extracted else "pages_located"
            row.error_message = (f"incomplet : extraction en échec {extract_failed or '[]'}, "
                                 f"adaptation en échec {adapt_failed or '[]'}")
        logger.info("Sésamaths : %s — %s : %s exercice(s) réel(s) au total, "
                    "page(s) en attente %s", extraction_key,
                    "terminé" if row.step == "done" else "PARTIEL", len(pool), still_pending)
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
            model=settings.claude_adapt_model,
            prompt_version=f"{EXTRACT_PROMPT_VERSION}+{ADAPT_PROMPT_VERSION}", status="active",
            verifier_model="", verifier_verdict_json=verdict,
            quality_json=verdict.get("scores") or {},
            figure_json=candidate.get("figure_json"), source="sesamaths",
            kind=candidate.get("kind", "application"),
            raw_extract_json=candidate.get("raw_extract_json"))
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

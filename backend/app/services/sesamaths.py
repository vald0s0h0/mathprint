"""Pipeline Sésamaths : extraction d'exercices depuis les manuels scolaires PDF
(collection Sésamath) — SEULE source d'exercices de l'app depuis le 16/07.

Architecture à 2 appels LLM par SÉRIE (16/07 soir : 2 appels par page avec
Claude vision des deux côtés ; 17/07 : extracteur remplacé par Mistral OCR,
granularité passée de la page à la Série) :
  1. EXTRACTEUR (Mistral OCR 4, moteur de reconnaissance de document PUR — pas
     un modèle de chat, aucune instruction/prompt) : lit le PDF de la Série
     (toutes ses pages en UN appel, `include_blocks=True`) et retourne des
     blocs typés NATIFS (title/text/table/image/equation/list/caption/...),
     chacun avec son contenu et son bbox pixel — le vocabulaire de Mistral
     lui-même, aucun schéma maison ne lui est imposé. Remplace l'ancien
     extracteur Claude vision : bien meilleure fidélité de lecture (moteur
     dédié plutôt qu'un modèle de chat), et le traitement multi-page en un
     seul appel donne à l'étape suivante toute la visibilité nécessaire pour
     ne jamais couper un exercice à cheval sur un saut de page.
  2. ADAPTATEUR (Claude Sonnet, texte pur, UN SEUL modèle — cf. 17/07 :
     Haiku produisait trop peu d'exercices distincts par Série, un 2e modèle
     de repli "correcteur" ajoutait de la complexité sans fiabiliser, retiré)
     : reçoit la liste APLATIE des blocs de toute la Série (ordre de
     lecture, page taguée par bloc) et les regroupe en exercices — un bloc
     "title" numéroté ("12 Calcule...", correspondant au badge coloré du
     manuel) démarre un nouvel exercice, tout ce qui suit (y compris les
     sous-parties a./b./c. et A./B./C.) lui appartient jusqu'au PROCHAIN
     titre numéroté, même après un changement de page. Fait aussi le tri
     entre un vrai tableau et une liste à puces sur 2 colonnes mal étiquetée
     "table" par l'OCR, et reconnaît les "..." comme des champs réponse
     vides (pas une ellipse). RÉSOUT l'exercice (l'OCR n'en fournit aucune
     réponse) avec un corrigé succinct, choisit le format de réponse app,
     reformule les consignes pour rester cohérentes avec le format choisi.
     N'évalue PLUS la difficulté (mise de côté le 17/07, cf. _to_candidate) :
     toujours 3/5 par défaut.
  3. VALIDATION DÉTERMINISTE : chaque candidat adapté repasse par
     exercise_gen._validate_exercise — aucune duplication de logique.
  4. FIGURES : un bloc "image" référencé dans les "source_blocks" d'un
     exercice est recadré en PNG raster (jamais vectoriel) depuis son bbox —
     déterministe (fourni par l'OCR), plus deviné par un LLM.

Reprise sur erreur : l'état d'extraction d'une Série est persistant
(SesamathsChapterExtraction, keyée par compétence = Série), machine à états
à 2 phases (extrait -> adapté) — une Série tient en 1-3 pages en général, la
reprise se fait donc au niveau de la Série entière (pas page par page).
`extraction_state()` expose cet état en LECTURE SEULE (jamais d'appel LLM),
pour l'onglet diagnostic « Sésamaths » de la banque (vérifier ce que l'OCR a
vraiment lu, avant de regarder ce que l'adaptateur en a fait).
`ensure_series_ocr()` expose la phase 1 SEULE (blocs OCR, sans adaptation) à
la pipeline Gemini, qui lit le manuel comme CONTEXTE de programme sans avoir
besoin des exercices adaptés — même extraction, même cache, une seule facture
OCR pour les deux pipelines.

Retraitement automatique : bumper ADAPT_PROMPT_VERSION (ou
settings.sesamaths_schema_version) sur une Série déjà "done" déclenche une
RÉ-ADAPTATION depuis le JSON brut Mistral déjà en cache, SANS repayer l'OCR ;
bumper EXTRACT_PROMPT_VERSION force une ré-extraction complète.
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
# même temps qu'une modification du prompt/schéma correspondant, sinon les
# réponses mises en cache par l'ANCIEN prompt sont resservies. Séparées pour
# que changer l'un ne force pas un nouvel appel de l'autre (tout l'intérêt de
# la séparation extracteur/adaptateur : l'adaptateur, cheap et itéré souvent,
# ne doit jamais forcer une ré-extraction Mistral, coûteuse).
EXTRACT_PROMPT_VERSION = "sesamaths-extract-2-mistral"
ADAPT_PROMPT_VERSION = "sesamaths-adapt-3-sonnet"
SOURCE_POOL = ("sesamaths", "sesamaths_deepseek")


class SesamathsExtractionError(RuntimeError):
    """L'extraction Sésamath n'a pas pu fournir d'exercices RÉELS du manuel
    (PDF introuvable, chapitre inconnu, extraction incomplète…). On la lève
    au lieu de retomber silencieusement sur une invention DeepSeek : le
    complément DeepSeek n'est autorisé QUE lorsque la Série a été entièrement
    extraite et qu'il faut ajuster le niveau (cf. ensure_bank)."""


# ================================================================ cache LLM

def _cache_key(*parts) -> str:
    material = "|".join(
        p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)
        for p in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cached_extract(db: Session, cache_key: str, pdf_bytes: bytes, n_pages: int,
                    correlation_id: str) -> dict:
    """Appel Mistral OCR mis en cache par (manuel, Série, prompt/schéma). Pas
    de notion de budget de tokens à escalader (l'OCR n'est pas une completion
    à max_tokens) — juste un retry basique sur 429."""
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = None
    for attempt in range(3):
        try:
            data = providers.mistral_ocr(db, "sesamaths_extract", pdf_bytes, n_pages,
                                         correlation_id=correlation_id)
            break
        except Exception as e:
            if providers.is_rate_limited(e) and attempt < 2:
                delay = providers.retry_after_s(e, attempt)
                logger.info("Sésamaths : 429 Mistral, nouvel essai dans %.0f s "
                           "(tentative %s/3)", delay, attempt + 2)
                time.sleep(delay)
                continue
            raise
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# Budgets de sortie essayés dans l'ordre par l'ADAPTATEUR (Claude, sujet à
# troncature contrairement à l'OCR Mistral) : une Série dense en table_fill
# multi-lignes peut dépasser 16000 tokens.
_ADAPT_TOKEN_BUDGETS = (16000, 32000, 48000)


def _cached_adapt(db: Session, cache_key: str, model: str, system: str,
                  payload: dict, correlation_id: str) -> dict:
    cached = db.query(SesamathsLlmCache).filter_by(cache_key=cache_key).first()
    if cached:
        return cached.response_json
    data = None
    for budget in _ADAPT_TOKEN_BUDGETS:
        for attempt in range(3):
            try:
                data = providers.claude_json(
                    db, "sesamaths_adapt", system, payload,
                    max_tokens=budget, model=model, correlation_id=correlation_id)
                break
            except Exception as e:
                if providers.is_rate_limited(e) and attempt < 2:
                    delay = providers.retry_after_s(e, attempt)
                    logger.info("Sésamaths : 429 sur %s, nouvel essai dans %.0f s "
                                "(tentative %s/3)", model, delay, attempt + 2)
                    time.sleep(delay)
                    continue
                if providers.is_truncated(e) and budget != _ADAPT_TOKEN_BUDGETS[-1]:
                    logger.info("Sésamaths : réponse tronquée sur %s à max_tokens=%s, "
                               "nouvel essai avec un budget plus élevé", model, budget)
                    break
                raise
        if data is not None:
            break
    db.add(SesamathsLlmCache(cache_key=cache_key, response_json=data))
    db.commit()
    return data


# ===================================================== prompt de l'adaptateur

_ADAPT_INTRO = (
    "Tu es un professeur agrégé de mathématiques. On te fournit les blocs "
    "extraits par OCR (Mistral) d'une Série d'un manuel de §GRADE§ (collection "
    "Sésamath), Série §SERIES_NUMBER§ « §SERIES_NAME§ » du chapitre "
    "« §CHAPTER_NAME§ ». L'OCR n'a RIEN résolu et RIEN inventé : chaque bloc "
    "est un fragment brut typé, dans l'ordre de lecture (colonnes puis pages "
    "de la Série, champ \"page\" = numéro de page fichier) :\n"
    "- \"title\" : un titre ou un numéro d'exercice (souvent « 12 Calcule "
    "chacun des produits ») ;\n"
    "- \"text\" : un paragraphe de consigne ou de sous-question ;\n"
    "- \"list\" : une liste à puces ou des items a./b./c. ;\n"
    "- \"table\" : un tableau imprimé (markdown), à compléter ou déjà "
    "rempli ;\n"
    "- \"equation\" : une formule ou un calcul isolé ;\n"
    "- \"image\" : une figure (géométrie, droite graduée, repère, schéma) ;\n"
    "- \"caption\" : la légende d'une figure ou d'un tableau ;\n"
    "- autres (\"code\"/\"references\"/\"aside_text\"/\"header\"/\"footer\"/"
    "\"signature\") : ignore-les, ce ne sont jamais des exercices, ni les "
    "rubriques « Culture » ou les rappels de leçon (« À RETENIR »).\n\n"

    "# Nettoyage préalable — l'OCR décrit la MISE EN PAGE, pas la structure : "
    "utilise le bon sens avant de faire confiance à un type de bloc\n"
    "- Une liste à puces imprimée sur 2 colonnes (mise en page fréquente dans "
    "ce manuel) est parfois étiquetée \"table\" par l'OCR alors que ce n'est "
    "PAS un vrai tableau : un vrai tableau a un EN-TÊTE de colonne cohérent "
    "(ex. « Quotient »/« Reste ») et une relation logique entre les cellules "
    "d'une même ligne. Si ce n'est pas le cas, traite chaque ligne/puce "
    "comme un ITEM INDÉPENDANT (souvent une sous-question a./b./c.), jamais "
    "comme une grille table_fill.\n"
    "- Toute séquence de points de suspension (« … » ou « ... », 3 points ou "
    "plus) dans un bloc de texte ou une cellule de tableau est un CHAMP "
    "RÉPONSE VIDE laissé pour l'élève, JAMAIS une ellipse de ponctuation à "
    "recopier telle quelle — transforme-la systématiquement en trou à "
    "compléter (marqueur {{blank}}, cellule de tableau non \"given\", ou "
    "case de multi_blank selon le format choisi).\n\n"

    "# Découpage en exercices — RÈGLE ABSOLUE\n"
    "Un bloc \"title\" dont le contenu commence par un numéro (ex. « 12 "
    "Calcule... », « 4. Quotients... ») marque le DÉBUT d'un nouvel exercice "
    "(ce numéro correspond au badge coloré imprimé dans le manuel). TOUS les "
    "blocs qui suivent (text/list/table/equation/image/caption), y compris "
    "sur la page SUIVANTE (le champ \"page\" change en cours de lecture), "
    "appartiennent à ce MÊME exercice jusqu'au PROCHAIN \"title\" numéroté — "
    "ne le sépare JAMAIS à cause d'un changement de page, fusionne-le.\n"
    "À l'intérieur d'un exercice, deux niveaux de sous-parties existent, NI "
    "L'UN NI L'AUTRE n'est un exercice séparé :\n"
    "- « a. », « b. », « c. »… (minuscules) : des CALCULS ou sous-questions "
    "associés au MÊME exercice — ce sont ses champs de réponse (typiquement "
    "une ligne de table_fill, ou plusieurs {{blank}}), jamais des exercices "
    "à part.\n"
    "- « A. », « B. », « C. »… (majuscules, ex. « Partie A »/« Partie B ») : "
    "de GRANDES parties du même exercice (souvent un contexte commun décliné "
    "en plusieurs volets) — regroupe-les aussi dans le MÊME exercice que le "
    "numéro qui les précède, jamais un exercice par partie.\n"
    "Ne scinde JAMAIS un exercice en plusieurs, ne répète JAMAIS le même "
    "exercice deux fois. Le nombre d'exercices que tu renvoies doit être "
    "EXACTEMENT le nombre de \"title\" numérotés distincts (après fusion "
    "inter-page) — recompte-les avant de répondre.\n\n"

    "Ta mission pour CHAQUE exercice ainsi délimité :\n"
    "- RÉSOUS-le et rédige \"correction\" : un corrigé TRÈS SUCCINCT — le "
    "résultat clairement énoncé + une explication courte (1 à 2 phrases "
    "maximum, JAMAIS une résolution pas-à-pas façon copie double), balisé "
    "$...$ en LaTeX si pertinent. Les blocs OCR ne fournissent aucune "
    "réponse : calcule-la toi-même.\n"
    "- Choisis le type de réponse de la plateforme et construis \"answer\" en "
    "conséquence (menu ci-dessous) — jamais le format d'origine du manuel.\n"
    "- Renvoie \"source_blocks\": [les indices \"i\" de TOUS les blocs "
    "utilisés pour cet exercice] — sert à retrouver le texte original.\n"
    "- N'ÉVALUE PAS de niveau de difficulté : ce champ n'est pas demandé ici, "
    "ignore toute idée de noter l'exercice.\n\n"

    "RÈGLE ABSOLUE : tu ne REFUSES ni n'OMETS JAMAIS un exercice sous "
    "prétexte que son format d'origine ne correspond à aucun type supporté "
    "tel quel. REFORMULE TOUJOURS la consigne pour qu'elle rentre dans l'un "
    "des formats ci-dessous ; si et SEULEMENT SI aucune reformulation n'est "
    "possible, utilise \"manual_drawing\" (dernier recours universel, tous "
    "domaines confondus). Quand tu changes de type de réponse, reformule "
    "AUSSI le verbe d'instruction pour rester cohérent avec ce que l'élève "
    "fait réellement sur sa copie (« Entoure »/« Souligne »/« Barre » "
    "deviennent « Coche » si tu choisis un QCM ; « Relie » reste correct "
    "pour un matching ; un bloc \"list\" Vrai/Faux devient un qcm_single à 2 "
    "choix, choices=[\"Vrai\",\"Faux\"]). Un bloc \"table\" imprimé (un VRAI "
    "tableau, cf. nettoyage préalable ci-dessus) devient directement un "
    "\"table_fill\" (mêmes lignes/colonnes, cellules déjà remplies marquées "
    "\"given\":true). Au-delà de 4-5 réponses courtes dans un même exercice, "
    "regroupe-les TOUJOURS en \"table_fill\" plutôt que de multiplier les "
    "{{blank}} dispersés dans le texte — un tableau donne à l'élève des "
    "limites visuelles (cadre), indispensables pour un recadrage OCR fiable "
    "de sa copie une fois complétée.\n\n"

    "LATEX ET ESPACES INSÉCABLES : tout nombre collé à une unité ou un "
    "symbole (€, cm, kg, %, ...) est balisé en LaTeX avec un espace "
    "insécable entre la valeur et l'unité — ex. « 13 € » devient "
    "$13\\ \\text{€}$, « 7,5 cm » devient $7{,}5\\ \\text{cm}$ — jamais de "
    "texte brut « 13 € » où une coupure de ligne séparerait le nombre de son "
    "unité à l'impression.\n\n"
)


def _adapt_system(grade: str, chapter_name: str, series_number, series_name: str,
                  is_geometry: bool) -> str:
    format_block = exercise_gen.format_contract(
        exercise_gen._ADAPT_FORMAT_INTRO,
        geometry_rules=exercise_gen._GEOMETRY_RULES if is_geometry else "")
    # .replace (et non .format) : le prompt contient des accolades JSON littérales
    intro = (_ADAPT_INTRO
             .replace("§GRADE§", grade)
             .replace("§CHAPTER_NAME§", chapter_name)
             .replace("§SERIES_NUMBER§", str(series_number))
             .replace("§SERIES_NAME§", series_name))
    return intro + format_block


# ================================================================ blocs OCR

def _flatten_blocks(raw: dict, start_index: int) -> list[dict]:
    """Aplatit les blocs typés Mistral (pages[].blocks[]) en UNE liste
    ordonnée (ordre des pages puis des blocs), chaque bloc taggé de son index
    global "i" (référencé par l'adaptateur dans "source_blocks") et de son
    index de PAGE DANS LE MANUEL "page" — pas l'index relatif au mini-PDF
    envoyé à Mistral, qui recommence à 0 (cf. _extract_series)."""
    flat: list[dict] = []
    for page in sorted(raw.get("pages") or [], key=lambda p: p.get("index", 0)):
        manual_page = start_index + int(page.get("index") or 0)
        dims = page.get("dimensions") or {}
        w, h = dims.get("width") or 1, dims.get("height") or 1
        for b in page.get("blocks") or []:
            try:
                bbox_pct = [b.get("top_left_x", 0) / w, b.get("top_left_y", 0) / h,
                           b.get("bottom_right_x", 0) / w, b.get("bottom_right_y", 0) / h]
            except (TypeError, ZeroDivisionError):
                bbox_pct = [0.0, 0.0, 0.0, 0.0]
            flat.append({"i": len(flat), "page": manual_page, "type": b.get("type"),
                        "content": b.get("content", ""), "bbox_pct": bbox_pct})
    return flat


# ================================================================ candidats

def _to_candidate(item: dict, doc, blocks_by_index: dict[int, dict], competency,
                  db: Session, existing_norms: set[str], out_dir) -> dict | None:
    if not isinstance(item, dict):
        return None
    item = dict(item)
    source_idx = item.pop("source_blocks", None) or []
    item.pop("difficulty", None)  # niveau non évalué par le LLM (cf. 17/07) : toujours 3

    # figure : si l'adaptateur n'a pas décrit une figure paramétrique
    # (rectangle/triangle/...), et qu'un bloc "image" fait partie de ses
    # source_blocks, recadre-le en PNG raster (bbox déterministe, fourni par
    # l'OCR — jamais deviné).
    if not item.get("figure"):
        image_block = next(
            (blocks_by_index[i] for i in source_idx
             if i in blocks_by_index and blocks_by_index[i].get("type") == "image"), None)
        if image_block is not None:
            page_idx = image_block["page"]
            fname = hashlib.sha256(
                f"{page_idx}|{item.get('statement', '')}".encode()).hexdigest()[:16]
            fig_path = out_dir / f"p{page_idx}_{fname}.png"
            if sesamaths_pdf.crop_bbox_png(doc, page_idx, image_block["bbox_pct"], fig_path):
                item["figure"] = {"type": "image", "params": {"path": str(fig_path)}}

    valid = exercise_gen._validate_exercise(item, competency, db, existing_norms)
    if valid is None:
        # pourquoi, et pas seulement combien : sans ça un « 11 renvoyés, 0
        # validés » est indiagnosticable (cf. incident extraction A1).
        logger.warning("Sésamaths : exercice REFUSÉ — %s | énoncé : %.90s",
                       exercise_gen.diagnose_rejection(item, competency),
                       str(item.get("statement", "")).replace("\n", " "))
        return None
    valid["difficulty"] = 3
    valid["raw_extract_json"] = {
        "blocks": [blocks_by_index[i] for i in source_idx if i in blocks_by_index]}
    return valid


def _extract_series(db: Session, doc, series_range: dict, chapter_code: str) -> dict:
    """Appel 1 (Mistral OCR, pur — aucune instruction) : construit un mini-PDF
    contenant UNIQUEMENT les pages de la Série et l'envoie à Mistral OCR
    (include_blocks=True). Retourne la réponse brute (pages[].blocks[] typés
    natifs). Mis en cache par (manuel, Série, prompt/modèle)."""
    start, end = series_range["start_index"], series_range["end_index"]
    n_pages = end - start + 1
    pdf_bytes = sesamaths_pdf.extract_page_range_pdf(doc, start, end)
    cache_key = _cache_key(chapter_code, "extract", EXTRACT_PROMPT_VERSION,
                          settings.mistral_ocr_model, start, end)
    data = _cached_extract(db, cache_key, pdf_bytes, n_pages,
                           correlation_id=f"sesa-ext-{chapter_code}-s{series_range.get('number')}")
    n_blocks = sum(len(p.get("blocks") or []) for p in data.get("pages") or [])
    logger.info("Sésamaths : extraction Mistral %s Série %s (pages fichier %s-%s) — "
               "%s bloc(s) sur %s page(s)", chapter_code, series_range.get("number"),
               start, end, n_blocks, n_pages)
    return data


def _adapt_series(db: Session, blocks: list[dict], series_range: dict, chapter_code: str,
                  competency, is_geometry: bool, grade: str, existing_norms: set[str],
                  doc, out_dir) -> list[dict]:
    """Appel 2 (texte) : regroupe les blocs typés d'UNE Série (toutes pages
    confondues) en exercices, résout, choisit le format. Un seul modèle
    (settings.claude_adapt_model) — un 2e modèle de repli ("qui corrige")
    ajoutait de la complexité sans fiabiliser (cf. 17/07) ; en mode mock,
    _cached_adapt réessaie déjà sur 429/troncature avec un budget de tokens
    croissant, ce qui reste (résilience réseau, pas une 2e opinion)."""
    if not blocks:
        return []
    blocks_by_index = {b["i"]: b for b in blocks}
    payload = {"blocks": [{"i": b["i"], "page": b["page"], "type": b["type"],
                          "content": b["content"]} for b in blocks]}

    model = settings.claude_adapt_model
    system = _adapt_system(grade, competency.chapter_name, series_range.get("number"),
                           series_range.get("name", ""), is_geometry)
    cache_key = _cache_key("adapt", ADAPT_PROMPT_VERSION, model,
                          settings.sesamaths_schema_version, chapter_code,
                          series_range.get("number"), payload["blocks"])
    try:
        data = _cached_adapt(db, cache_key, model, system, payload,
                             correlation_id=f"sesa-adp-{chapter_code}-s{series_range.get('number')}")
    except Exception as e:
        logger.warning("Sésamaths : adaptation Série %s (%s) échouée : %s",
                       series_range.get("number"), model, e)
        raise RuntimeError(f"adaptation Série {series_range.get('number')} ({model}) : {e}") from e

    cands: list[dict] = []
    for item in data.get("exercises") or []:
        c = _to_candidate(item, doc, blocks_by_index, competency, db, existing_norms, out_dir)
        if c is not None:
            cands.append(c)
    logger.info("Sésamaths : adaptation Série %s — modèle %s : %s exercice(s) "
                "validé(s) sur %s renvoyé(s)", series_range.get("number"), model,
                len(cands), len(data.get("exercises") or []))
    return cands


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


def _resolve_series_range(doc, manual, chapter_code: str, competency) -> dict:
    """Plage de pages fichier (0-indexées, incluses) de la Série de
    `competency`. Repli sur tout le chapitre si la Série ne peut pas être
    résolue (numéro absent du code compétence, ou introuvable dans la ToC)."""
    series_no = series_number_for(competency)
    if series_no is not None:
        for r in sesamaths_pdf.series_page_ranges(doc, manual.toc_json, chapter_code):
            if r["number"] == series_no:
                return r
        logger.warning("Sésamaths : aucune page pour la Série %s du chapitre %s — "
                       "repli sur tout le chapitre", series_no, chapter_code)
    start, end = sesamaths_pdf.chapter_page_range(doc, manual.toc_json, chapter_code)
    return {"number": series_no, "name": "", "start_index": start, "end_index": end}


def ensure_chapter_pool(db: Session, doc, manual, chapter_code: str, competency
                       ) -> list[dict]:
    """État persistant par Série — machine à états à 2 phases (extraite ->
    adaptée). Ne lève jamais : toute erreur est journalisée, le pool renvoyé
    peut être vide (reprise au prochain appel, depuis la phase en échec)."""
    extraction_key = _extraction_key(competency, chapter_code)
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=extraction_key).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=extraction_key)
        db.add(row)
        db.flush()

    current_versions = {"extract": EXTRACT_PROMPT_VERSION, "adapt": ADAPT_PROMPT_VERSION,
                        "schema": settings.sesamaths_schema_version}
    stored = (row.page_range_json or {}).get("versions", {})
    # La péremption de l'EXTRACTION vaut aussi pour une Série restée en
    # "extracted" — état devenu courant : la pipeline Gemini extrait sans
    # jamais adapter (ensure_series_ocr). Ne la contrôler que sur "done"
    # laisserait ces Séries-là se faire adapter depuis un raw_json périmé,
    # puis estampiller de la version courante : le bump d'EXTRACT_PROMPT_VERSION
    # ne garantirait plus la ré-extraction qu'il promet.
    if (row.step in ("done", "extracted")
            and stored.get("extract") != current_versions["extract"]):
        logger.info("Sésamaths : %s — version d'extraction changée (%s -> %s), "
                    "ré-extraction complète", extraction_key,
                    stored.get("extract"), current_versions["extract"])
        row.step = "pending"
        row.raw_json = {}
        row.validated_json = []
        db.commit()
    elif row.step == "done":
        if (stored.get("adapt") != current_versions["adapt"]
                or stored.get("schema") != current_versions["schema"]):
            logger.info("Sésamaths : %s — version d'adaptation changée (%s -> %s), "
                        "ré-adaptation depuis le JSON brut Mistral déjà en cache "
                        "(aucun nouvel appel OCR)", extraction_key,
                        stored.get("adapt"), current_versions["adapt"])
            row.step = "extracted"
            row.validated_json = []
            db.commit()
        else:
            return row.validated_json or []
    # "extracted" ne retourne JAMAIS ici : la Série a du raw_json mais pas
    # d'exercices — elle tombe dans la phase d'adaptation ci-dessous.

    row.attempts += 1
    is_geometry = (competency.domain_code in exercise_gen.GEOMETRY_DOMAINS
                   or chapter_code[:1] == "B")
    out_dir = settings.data_dir / "sesamaths" / manual.grade_level / chapter_code
    grade = manual.grade_level

    try:
        series_range = _resolve_series_range(doc, manual, chapter_code, competency)
        row.page_range_json = {**(row.page_range_json or {}),
                               "start_index": series_range["start_index"],
                               "end_index": series_range["end_index"],
                               "series_number": series_range["number"],
                               "series_name": series_range.get("name", "")}

        if row.step in ("pending", ""):
            row.raw_json = _extract_series(db, doc, series_range, chapter_code)
            row.step = "extracted"
            db.commit()

        if row.step == "extracted":
            blocks = _flatten_blocks(row.raw_json or {}, series_range["start_index"])
            existing_norms = {
                exercise_gen._dedup_key(c["statement"], c.get("expected"),
                                        (c.get("grading") or {}).get("choices"))
                for c in (row.validated_json or [])}
            cands = _adapt_series(db, blocks, series_range, chapter_code, competency,
                                  is_geometry, grade, existing_norms, doc, out_dir)
            row.validated_json = cands
            row.step = "done"
            row.page_range_json = {**row.page_range_json, "versions": current_versions}
            row.error_message = "" if cands else "Aucun exercice validé pour cette Série"
            db.commit()
    except Exception as e:
        row.error_message = str(e)[:2000]
        logger.error("Sésamaths : extraction %s en échec (step=%s) : %s",
                    extraction_key, row.step, e)
        db.commit()

    return row.validated_json or []


def chapter_pool(db: Session, competency) -> list[dict]:
    """Pool d'exercices RÉELS extraits de la Série (best-effort, jamais
    d'exception). Pour la génération de banque, préférer `_extracted_chapter`
    qui distingue « manuel introuvable » de « extraction complète »."""
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None or chapter_code is None:
        return []
    return ensure_chapter_pool(db, doc, manual, chapter_code, competency)


def _extracted_chapter(db: Session, competency) -> tuple[list[dict], bool]:
    """Extrait (ou récupère) le pool d'exercices RÉELS de la Série et indique
    si l'extraction est COMPLÈTE.

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
    fully_done = bool(row and row.step == "done")
    return pool, fully_done


def ensure_series_ocr(db: Session, competency) -> list[dict]:
    """Blocs OCR Mistral de la Série de `competency` (phase 1 SEULE), pour une
    pipeline TIERCE qui veut lire le manuel sans en adapter les exercices —
    aujourd'hui services.gemini_gen, qui s'en sert de contexte de programme.

    Ne lance JAMAIS l'adaptateur (phase 2, Claude Sonnet) : l'appelant veut le
    texte du manuel, pas des exercices au contrat app — les payer serait
    absurde. Partage en revanche l'état et le cache d'extraction de la pipeline
    Sésamaths : si la Série a déjà été extraite, aucun appel OCR n'est fait, et
    si c'est nous qui l'extrayons, la Série reste en `step="extracted"`, prête
    à être adaptée gratuitement le jour où la pipeline Sésamaths passe dessus.

    N'invalide RIEN sur changement de version de prompt : gérer ici la machine
    à états de `ensure_chapter_pool` la dupliquerait à moitié. Un raw_json déjà
    présent est du texte de manuel, toujours bon comme contexte, quelle que
    soit la version du prompt qui l'a demandé.

    Lève SesamathsExtractionError si le manuel ou le chapitre sont introuvables
    (l'appelant décide quoi en faire — jamais de repli silencieux)."""
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None or chapter_code is None:
        detail = (manual.error_message if manual and manual.error_message
                  else f"chapitre {competency.chapter_code} absent du manuel")
        raise SesamathsExtractionError(
            f"Le PDF du manuel Sésamath est introuvable (ou le chapitre "
            f"{competency.chapter_code} en est absent). Détail : {detail}")

    extraction_key = _extraction_key(competency, chapter_code)
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code=extraction_key).first())
    if row is None:
        row = SesamathsChapterExtraction(manual_id=manual.id, chapter_code=extraction_key)
        db.add(row)
        db.flush()

    series_range = _resolve_series_range(doc, manual, chapter_code, competency)
    if not row.raw_json:
        row.attempts += 1
        try:
            row.raw_json = _extract_series(db, doc, series_range, chapter_code)
        except Exception as e:
            row.error_message = str(e)[:2000]
            db.commit()
            raise SesamathsExtractionError(
                f"L'OCR de la Série de {competency.code} a échoué : {e}") from e
        row.page_range_json = {**(row.page_range_json or {}),
                               "start_index": series_range["start_index"],
                               "end_index": series_range["end_index"],
                               "series_number": series_range["number"],
                               "series_name": series_range.get("name", ""),
                               # estampiller la version d'extraction est
                               # OBLIGATOIRE, pas cosmétique : c'est elle que
                               # ensure_chapter_pool relit pour savoir si ce
                               # raw_json est périmé (cf. le commentaire là-bas).
                               "versions": {**(row.page_range_json or {}).get("versions", {}),
                                            "extract": EXTRACT_PROMPT_VERSION}}
        row.step = "extracted"
        db.commit()
    return _flatten_blocks(row.raw_json or {}, series_range["start_index"])


def raw_pages(row: SesamathsChapterExtraction) -> list[dict]:
    """Pages OCR brutes (Mistral) d'une Série, taguées par page MANUEL (pas
    l'index relatif au mini-PDF envoyé à Mistral) — pour l'affichage
    diagnostic (onglet « Sésamaths » de la banque). Jamais consommé par
    l'adaptateur, qui lit row.raw_json directement via _flatten_blocks."""
    start = (row.page_range_json or {}).get("start_index", 0)
    out = []
    for page in sorted((row.raw_json or {}).get("pages") or [], key=lambda p: p.get("index", 0)):
        out.append({
            "page": start + int(page.get("index") or 0),
            "markdown": page.get("markdown") or "",
            "blocks": [{"type": b.get("type"), "content": b.get("content", "")}
                      for b in page.get("blocks") or []],
        })
    return out


def extraction_state(db: Session, competency) -> dict:
    """État d'extraction en LECTURE SEULE (aucun appel LLM, jamais d'écriture)
    — pour l'onglet diagnostic « Sésamaths » de la banque : vérifier que
    l'OCR a bien lu la Série AVANT de regarder ce que l'adaptateur en a fait.
    N'appelle jamais ensure_chapter_pool : si rien n'a encore été extrait,
    renvoie juste "not_extracted" (l'utilisateur lance l'extraction via
    « Compléter la banque », qui existe déjà)."""
    doc, manual, chapter_code = _resolve_chapter(db, competency)
    if doc is None:
        return {"status": "manual_missing",
                "detail": manual.error_message if manual else "", "pages": []}
    if chapter_code is None:
        return {"status": "chapter_missing",
                "detail": f"chapitre {competency.chapter_code} absent du manuel", "pages": []}
    row = (db.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id,
                      chapter_code=_extraction_key(competency, chapter_code)).first())
    if row is None or row.step in ("pending", "") or not row.raw_json:
        return {"status": "not_extracted", "detail": "", "pages": [],
                "series_number": series_number_for(competency)}
    return {
        "status": row.step, "detail": row.error_message, "attempts": row.attempts,
        "series_number": (row.page_range_json or {}).get("series_number"),
        "series_name": (row.page_range_json or {}).get("series_name"),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "n_exercises_validated": len(row.validated_json or []),
        "pages": raw_pages(row),
    }


def harvest(db: Session, competency, level: int, need: int,
           existing_norms: set[str], pool: list[dict]) -> list[dict]:
    """Moisson des exercices Sésamaths déjà extraits de la Série de
    `competency`, filtrés au niveau demandé."""
    if need <= 0:
        return []
    out = []
    for cand in pool:
        if len(out) >= need:
            break
        if cand.get("difficulty") != level:
            continue
        key = exercise_gen._dedup_key(cand["statement"], cand.get("expected"),
                                      (cand.get("grading") or {}).get("choices"))
        if key in existing_norms:
            continue
        existing_norms.add(key)
        c = dict(cand)
        c["_source"] = "sesamaths"
        out.append(c)
    return out


# ================================================================ banque

def ensure_bank(db: Session, competency, level: int) -> list[GeneratedExercise]:
    """Équivalent de exercise_gen.ensure_bank pour la source Sésamaths : pool
    strictement séparé (source in SOURCE_POOL), jamais mélangé à la banque
    MathALÉA/DeepSeek par défaut.

    Aucune cible de variantes : la Série du manuel contient ce qu'elle contient,
    on stocke TOUT ce qu'on a su en extraire au niveau demandé. Le plafond
    historique (settings.exercise_variants_per_level, 3) est tombé le 17/07 : il
    ne « limitait » rien d'utile, il jetait le reste d'une extraction déjà payée
    et bornait à 3 le choix disponible pour remplir une page — d'où des copies
    qui répétaient les mêmes exercices."""
    level = max(1, min(5, level))
    rows = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                   GeneratedExercise.difficulty_level == level,
                   GeneratedExercise.status == "active",
                   GeneratedExercise.source.in_(SOURCE_POOL))
            .all())

    # Extraction RÉELLE d'abord. Lève SesamathsExtractionError (message clair,
    # non bloquant en amont) si le manuel est introuvable — AUCUNE invention
    # DeepSeek à la place d'exercices qu'on n'a pas su extraire.
    pool, fully_done = _extracted_chapter(db, competency)
    logger.info("Sésamaths : banque %s niveau %s — %s variante(s) en stock ; "
                "%s exercice(s) réel(s) extrait(s) de la Série (extraction %s)",
                competency.code, level, len(rows), len(pool),
                "complète" if fully_done else "INCOMPLÈTE")

    # Pas de filtre status="active" ICI : un exercice RETIRÉ doit rester
    # définitivement "vu" (sinon il redevient piochable dès le pool cache
    # suivant — "Retirer" ne retirait rien durablement, cf. incident doublons).
    existing_norms = {
        exercise_gen._dedup_key(ex.statement, ex.expected_json,
                                (ex.grading_json or {}).get("choices"))
        for ex in db.query(GeneratedExercise)
        .filter(GeneratedExercise.competency_id == competency.id,
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

    # `need=len(pool)` : tout ce que la Série offre à ce niveau et qui n'est pas
    # déjà en banque (harvest dédoublonne et filtre le niveau) — plus aucun
    # plafond arbitraire.
    for cand in harvest(db, competency, level, len(pool), existing_norms, pool):
        _store(cand, cand.get("_verdict", {}))

    db.flush()
    if not rows and not added:
        if not fully_done:
            raise SesamathsExtractionError(
                f"Extraction Sésamath incomplète pour {competency.code} "
                f"(chapitre {competency.chapter_code}) : aucun exercice réel "
                f"disponible au niveau {level}. Les exercices n'ont pas pu être "
                f"extraits — réessayez, l'extraction reprendra depuis la phase en échec.")
        raise ValueError(
            f"Aucun exercice Sésamaths n'a passé les contrôles qualité pour "
            f"{competency.code} niveau {level}")
    logger.info("Sésamaths : banque %s niveau %s prête : %s variante(s) réelle(s)",
                competency.code, level, len(rows) + len(added))
    return rows + added

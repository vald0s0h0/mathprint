"""Indigo — « mise au propre » d'exercices de manuel par DeepSeek pro (par LOTS).

Duplique la pipeline de création Gemini (services.gemini_gen) mais avec une
mission différente : on ne CRÉE pas, on REPREND des exercices précis du manuel.
Règles fixées avec l'utilisateur :
- 1 exercice du manuel → 1 exercice en sortie (jamais 0, jamais 2) ;
- garder l'OBJECTIF (même tâche, même notion, même niveau), mais réécrire
  librement la tournure de l'énoncé ET choisir le type de réponse pour tomber
  dans un format auto-corrigeable — priorité QCM, puis case simple ;
- recopier la VRAIE correction du manuel prof dans `correction_solution`,
  et en dériver le court guide d'auto-correction élève (`correction`) ;
- si l'exercice s'appuie sur une figure, elle est fournie comme IMAGE à côté
  de l'énoncé : le modèle n'y touche pas, écrit « la figure ci-contre » et choisit
  une réponse lisible (QCM/nombre) — d'où allow_geometry_text côté validateur.

Traitement par LOTS de `BATCH_SIZE` : un appel DeepSeek adapte plusieurs exercices
d'un coup (moins d'appels, réponses plus courtes donc plus fiables), la sortie
est réappariée à l'entrée PAR NUMÉRO d'exercice (source_number).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models import Competency
from . import exercise_gen, indigo_llm, prompts
from . import statement as statement_mod
from .gemini_gen import _competency_name, _competency_tree

logger = logging.getLogger("app.indigo")

PROMPT_VERSION = "indigo-adapt-5-matching"
FORBIDDEN_RESPONSE_TYPES = {"manual_drawing"}
BATCH_SIZE = 5
# fenêtre de taille de lot pour l'adaptation : « par lot de 5 à 7 selon le
# nombre » (cf. demande utilisateur). On choisit dans cette fenêtre la taille
# dont le DERNIER lot est le plus rempli (évite un lot final d'un seul exercice).
BATCH_MIN, BATCH_MAX = 5, 7


def choose_batch_size(n: int) -> int:
    """Taille de lot dans [BATCH_MIN, BATCH_MAX] qui remplit au mieux le dernier
    lot pour `n` exercices (un lot terminal d'un seul exercice est du gâchis).
    `n <= BATCH_MAX` → un seul lot."""
    if n <= BATCH_MAX:
        return max(1, n)
    best_s, best_rem = BATCH_MAX, -1
    for s in range(BATCH_MAX, BATCH_MIN - 1, -1):
        rem = n % s or s          # taille du dernier lot (s s'il divise pile)
        if rem > best_rem:
            best_rem, best_s = rem, s
    return best_s


def _intro() -> str:
    """Bloc d'instructions de l'adaptateur — ÉDITABLE dans
    prompts/indigo/generation.txt (le schéma JSON strict est ajouté par le
    code, cf. exercise_gen.format_contract). Chargé paresseusement."""
    return prompts.load("indigo", "generation")


def _system_prompt(db: Session, competency: Competency, grade: str) -> str:
    intro = (_intro()
             .replace("§GRADE§", grade)
             .replace("§COMPETENCY§", _competency_name(competency))
             .replace("§CHAPTER§", f"{competency.chapter_code} {competency.chapter_name}".strip())
             .replace("§DOMAIN§", f"{competency.domain_code} {competency.domain_name}".strip())
             .replace("§COMPETENCY_TREE§", _competency_tree(db, competency)))
    contract = exercise_gen.format_contract(
        intro, geometry_rules=exercise_gen._GEOMETRY_RULES)
    contract += (
        "\n\nAJOUT AU SCHÉMA : chaque objet exercice porte EN PLUS "
        "\"source_number\":str (le numéro du manuel), \"correction_solution\":str "
        "(cf. « La correction : DEUX champs ») et \"needs_figure\":bool (cf. "
        "« Champ needs_figure »). N'utilise ni \"figure\" ni \"source_blocks\".")
    return contract


def _finalize(raw: dict, competency: Competency, db: Session, manual: dict) -> dict | None:
    """Valide UN exercice adapté (contrat interne) et l'enrichit du corrigé
    recopié + du numéro source. None si non conforme."""
    valid = exercise_gen._validate_exercise(raw, competency, db, set(),
                                            allow_geometry_text=True)
    if valid is None:
        reason = exercise_gen.diagnose_rejection(raw, competency)
        logger.warning("Indigo/DeepSeek : exercice n°%s REFUSÉ — %s | %.80s",
                       manual.get("number"), reason,
                       str(raw.get("statement", "")).replace("\n", " "))
        return None
    if valid["response_type"] in FORBIDDEN_RESPONSE_TYPES:
        logger.warning("Indigo/DeepSeek : format interdit %s (n°%s)",
                       valid["response_type"], manual.get("number"))
        return None
    valid["correction_solution"] = statement_mod.repair_latex_control_chars(
        str(raw.get("correction_solution") or "").strip())
    valid["source_number"] = str(raw.get("source_number") or manual.get("number") or "").strip()
    # cf. « Champ needs_figure » — repli sur has_figure (déjà vrai) si le modèle a
    # omis le champ, pour ne jamais perdre le signal le plus sûr des deux.
    valid["needs_figure"] = bool(raw.get("needs_figure")) or bool(manual.get("has_figure"))
    # ceinture + bretelles : si le modèle a malgré tout remis le numéro du manuel en
    # tête d'énoncé, on le retire (l'énoncé ne commence pas par un chiffre).
    valid["statement"] = statement_mod.strip_leading_number(
        valid.get("statement", ""), valid["source_number"])
    # on conserve le CONTRAT brut (schéma answer/choices) qui vient de passer le
    # validateur : la relecture finale (services.indigo_verify) le renvoie tel
    # quel au modèle et re-valide sa sortie par le MÊME chemin, sans avoir à
    # reconstruire `answer` depuis la forme interne `expected`. Non persisté
    # (retiré avant l'enregistrement, cf. indigo._persist_exercise).
    valid["_raw"] = {"kind": valid["kind"], "effort_points": raw.get("effort_points"),
                     "choices": raw.get("choices"), "answer": raw.get("answer")}
    return valid


def _call(db: Session, system: str, payload: dict, correlation_id: str) -> dict | None:
    """Adaptation par le fournisseur choisi dans l'onglet (Anthropic Sonnet ou
    DeepSeek pro, cf. services.indigo_llm). Une sortie tronquée/en échec retombe en
    JSON invalide → l'appelant (adapt_batch) rend {} et CHAQUE exercice du lot est
    rejoué en solo (adapt_one, sortie minuscule), puis en repli OCR brut."""
    return indigo_llm.call(db, "adapt", system, payload, correlation_id)


def adapt_batch(db: Session, competency: Competency, grade: str,
                manuals: list[dict]) -> dict:
    """Adapte un LOT d'exercices (<= BATCH_SIZE) en UN appel DeepSeek. `manuals` =
    [{number, statement, correction, has_figure}, ...]. Retourne un dict
    {source_number -> contrat interne enrichi} (réappariement par numéro ;
    replis best-effort : un exercice manquant/refusé est simplement absent)."""
    system = _system_prompt(db, competency, grade)
    payload = {
        "grade_level": grade,
        "competency_code": competency.code,
        "competency_label": _competency_name(competency),
        "exercises_to_adapt": [
            {"number": str(m.get("number", "")), "statement": m.get("statement", ""),
             "correction": m.get("correction", ""), "has_figure": bool(m.get("has_figure"))}
            for m in manuals],
    }
    nums = ",".join(str(m.get("number", "?")) for m in manuals)
    data = _call(db, system, payload, f"indigo-{competency.code}-{nums}")
    items = (data or {}).get("exercises") or []

    by_number = {}
    for raw in items:
        if isinstance(raw, dict):
            by_number.setdefault(str(raw.get("source_number") or "").strip(), raw)

    out: dict[str, dict] = {}
    for m in manuals:
        num = str(m.get("number", "")).strip()
        raw = by_number.get(num)
        if raw is None:
            # repli : réappariement positionnel si le modèle n'a pas renvoyé le numéro
            idx = manuals.index(m)
            raw = items[idx] if idx < len(items) and isinstance(items[idx], dict) else None
        if raw is None:
            logger.warning("Indigo/DeepSeek : pas de sortie pour l'exercice n°%s (%s)",
                           num, competency.code)
            continue
        valid = _finalize(raw, competency, db, m)
        if valid is not None:
            out[num] = valid
    logger.info("Indigo/DeepSeek : lot [%s] pour %s — %s/%s exercice(s) mis au propre",
                nums, competency.code, len(out), len(manuals))
    return out


def adapt_one(db: Session, competency: Competency, grade: str,
              manual: dict) -> dict | None:
    """Adapte UN exercice en SOLO — 2e chance après un échec/refus en lot. Un
    lot chargé (5 à 7) dégrade parfois un exercice (sortie tronquée, format
    ignoré) ; le rejouer seul (contexte minimal) le récupère souvent. Retourne
    le contrat interne enrichi, ou None (l'appelant retombe alors sur l'OCR
    brut)."""
    try:
        out = adapt_batch(db, competency, grade, [manual])
    except Exception:
        logger.exception("Indigo/DeepSeek : retry solo échoué (n°%s, %s)",
                         manual.get("number"), competency.code)
        return None
    return out.get(str(manual.get("number", "")).strip())

"""Indigo — VÉRIFICATION FINALE des exercices mis au propre (DeepSeek pro).

Dernière couche de la pipeline de création Indigo. Une fois qu'un exercice du
manuel a été « mis au propre » par l'adaptateur (services.indigo_gemini) et a
passé le validateur partagé (services.exercise_gen), DeepSeek pro le REVÉRIFIE une
dernière fois — par LOTS courts en UN seul appel. Différence clé avec un simple
relecteur : le modèle reçoit AUSSI LA SOURCE (OCR brut de l'énoncé + corrigé du
manuel prof) et RÉSOUT l'exercice indépendamment, au lieu de faire confiance au
candidat de l'adaptateur. Il attrape ainsi :
  • une réponse fausse (answer / cellule / QCM), un corrigé incohérent ;
  • une lecture OCR INFIDÈLE (candidat cohérent avec lui-même mais ≠ manuel :
    « $3^4$ » lu « $3 \\times 4$ ») — invisible sans la source ;
  • un nombre du manuel changé sans justification ;
  • une parenthèse en trop, un « $ » orphelin, une accolade LaTeX parasite ;
  • une formulation peu claire, une faute de frappe/accord, une mise en page.

DEUX régimes à ne jamais confondre : STRICT sur les maths et la fidélité à la
source (le modèle recalcule) ; CONSERVATEUR sur la rédaction (dans le doute, ne
rien changer). Le format d'entrée et de sortie reste le MÊME contrat strict que
l'adaptateur (exercise_gen.format_contract) : la sortie est re-validée par le MÊME
chemin (indigo_gemini._finalize). Une vérification qui échoue, refuse ou casse un
exercice ne DÉGRADE JAMAIS l'existant : on garde alors la version adaptée
d'origine (jamais 0 exercice, jamais un exercice cassé par la relecture).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Competency
from . import exercise_gen, indigo_gemini, indigo_llm, prompts
from .gemini_gen import _competency_name, _competency_tree

logger = logging.getLogger("app.indigo")

PROMPT_VERSION = "indigo-review-1"

# Taille de lot de VÉRIFICATION : lots de 6 à 8. Contrainte DURE = le plafond de
# sortie DeepSeek (deepseek_max_output_tokens ≈ 8k) : le modèle réémet TOUS les
# champs de chaque exercice (near-copie), donc une dizaine d'exercices suffit à
# tronquer la sortie. Un lot tronqué n'est PAS re-vérifié (la version adaptée est
# gardée) : on préfère des lots courts qui passent à coup sûr. La vérif reçoit en
# plus la source (OCR + corrigé), ce qui alourdit aussi l'entrée par exercice.
REVIEW_MIN, REVIEW_MAX = 6, 8


def choose_review_batch_size(n: int) -> int:
    """Taille de lot dans [REVIEW_MIN, REVIEW_MAX] qui remplit au mieux le
    dernier lot pour `n` exercices (un lot terminal riquiqui est du gâchis).
    `n <= REVIEW_MAX` → un seul lot (on vérifie tout ce qu'on a, même < 6)."""
    if n <= REVIEW_MAX:
        return max(1, n)
    best_s, best_rem = REVIEW_MAX, -1
    for s in range(REVIEW_MAX, REVIEW_MIN - 1, -1):
        rem = n % s or s          # taille du dernier lot (s s'il divise pile)
        if rem > best_rem:
            best_rem, best_s = rem, s
    return best_s


def _review_intro() -> str:
    """Bloc d'instructions de la relecture — ÉDITABLE dans
    prompts/indigo/verification.txt (le schéma JSON strict est ajouté par le
    code, cf. exercise_gen.format_contract). Chargé paresseusement."""
    return prompts.load("indigo", "verification")


def _system_prompt(db: Session, competency: Competency, grade: str) -> str:
    """Prompt de relecture. Il RÉUTILISE le contrat partagé
    (exercise_gen.format_contract) : le relecteur doit produire EXACTEMENT le
    même schéma que l'adaptateur, sinon sa sortie serait rejetée en silence par
    le validateur. Mêmes ajouts Indigo (source_number / correction_solution /
    needs_figure) que services.indigo_gemini._system_prompt."""
    intro = (_review_intro()
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
        "(cf. « La correction : DEUX champs ») et \"needs_figure\":bool. "
        "N'utilise ni \"figure\" ni \"source_blocks\".")
    return contract


def _review_item(number: str, manual: dict, valid: dict) -> dict | None:
    """Objet à soumettre à la vérification. Il porte DEUX faces :
      • la SOURCE (autorité) — `source_statement` = OCR brut de l'énoncé,
        `source_correction` = corrigé du manuel prof, `has_figure` = image
        réellement disponible. C'est contre ELLE que le modèle recalcule (sans elle,
        on ne peut détecter qu'une incohérence interne, pas une lecture infidèle) ;
      • le CANDIDAT à contrôler, DANS LE FORMAT DE SORTIE (celui que le modèle doit
        reproduire) : énoncé/corrigés pris sur la version validée (normalisée,
        telle qu'imprimée), schéma structuré de la réponse (answer/choices/kind/
        effort_points) pris sur le contrat brut archivé (`_raw`).
    None si le contrat brut manque : on ne vérifie pas à l'aveugle un exercice
    dont on ne peut pas montrer la réponse attendue."""
    raw = valid.get("_raw")
    if not raw or raw.get("answer") is None:
        return None
    item = {
        "source_number": number,
        # --- SOURCE (vérité terrain, ne pas la reproduire en sortie) ---
        "source_statement": manual.get("statement", ""),
        "source_correction": manual.get("correction", ""),
        "has_figure": bool(manual.get("has_figure")),
        # --- CANDIDAT à contrôler (format de sortie attendu) ---
        "statement": valid.get("statement", ""),
        "response_type": valid.get("response_type", "short_text"),
        "correction": valid.get("correction", ""),
        "correction_solution": valid.get("correction_solution", ""),
        "kind": raw.get("kind", "application"),
        "effort_points": raw.get("effort_points"),
        "answer": raw.get("answer"),
        "needs_figure": bool(valid.get("needs_figure")),
    }
    if raw.get("choices"):
        item["choices"] = raw["choices"]
    return item


def _call(db: Session, system: str, payload: dict, correlation_id: str) -> dict | None:
    """Vérification par le fournisseur choisi dans l'onglet (Anthropic Opus ou
    DeepSeek pro, cf. services.indigo_llm). Les lots courts (REVIEW_MAX) tiennent
    sous le plafond de sortie le plus bas. Une sortie tronquée/en échec retombe en
    JSON invalide → l'appelant garde la version adaptée (jamais de dégradation)."""
    return indigo_llm.call(db, "review", system, payload, correlation_id)


def _review_batch(db: Session, competency: Competency, grade: str,
                  batch: list[tuple[str, dict, dict]]) -> dict[str, dict]:
    """Relit UN lot (<= REVIEW_MAX). `batch` = [(number, manual, valid), ...].
    Retourne {number -> valid RELU} — uniquement les exercices dont la version
    relue re-passe le validateur. Un exercice absent/refusé/inchangeable n'y
    figure pas : l'appelant garde alors la version adaptée d'origine (jamais de
    dégradation)."""
    items: list[dict] = []
    order: list[tuple[str, dict]] = []      # (number, manual) dans l'ordre soumis
    for number, manual, valid in batch:
        it = _review_item(number, manual, valid)
        if it is None:
            continue                        # sans contrat brut : gardé tel quel
        items.append(it)
        order.append((number, manual))
    if not items:
        return {}

    system = _system_prompt(db, competency, grade)
    payload = {
        "grade_level": grade,
        "competency_code": competency.code,
        "competency_label": _competency_name(competency),
        "exercises_to_review": items,
    }
    nums = ",".join(n for n, _m in order)
    data = _call(db, system, payload, f"indigo-review-{competency.code}-{nums[:60]}")
    reviewed = (data or {}).get("exercises") or []

    by_number: dict[str, dict] = {}
    for raw in reviewed:
        if isinstance(raw, dict):
            by_number.setdefault(str(raw.get("source_number") or "").strip(), raw)

    out: dict[str, dict] = {}
    for idx, (number, manual) in enumerate(order):
        raw = by_number.get(number)
        if raw is None:                     # repli positionnel si le numéro manque
            raw = reviewed[idx] if idx < len(reviewed) and isinstance(reviewed[idx], dict) else None
        if raw is None:
            continue                        # rien de relu : on garde l'original
        valid = indigo_gemini._finalize(raw, competency, db, manual)
        if valid is not None:               # la sortie relue re-passe le validateur
            out[number] = valid
    logger.info("Indigo/relecture : lot [%s] pour %s — %s/%s exercice(s) corrigé(s)/confirmé(s)",
                nums[:60], competency.code, len(out), len(items))
    return out


def review(db: Session, competency: Competency, grade: str,
           triples: list[tuple], progress_cb=None) -> dict[str, dict]:
    """Relecture finale de TOUS les exercices adaptés d'une cible, par lots de
    20 à 40. `triples` = [(row, manual, valid|None), ...] tel que produit par
    l'adaptation. Retourne {number -> valid} pour CHAQUE exercice adapté
    (valid != None) : la version RELUE si elle re-passe le validateur, sinon la
    version adaptée d'origine — le contrat brut `_raw` est retiré au passage
    (jamais persisté). Les exercices non adaptés (valid None) sont ignorés."""
    ready = [(str(m.get("number", "")).strip(), m, v)
             for (_row, m, v) in triples if v is not None]
    # base : chaque exercice pointe d'abord sur SA version adaptée (repli garanti)
    result: dict[str, dict] = {num: v for num, m, v in ready}

    if not settings.indigo_review_enabled or not ready:
        return {n: _strip_raw(v) for n, v in result.items()}

    size = choose_review_batch_size(len(ready))
    for i in range(0, len(ready), size):
        batch = ready[i:i + size]
        if progress_cb:
            progress_cb(f"{competency.short_id or competency.code} : relecture "
                        f"{min(i + len(batch), len(ready))}/{len(ready)}…")
        try:
            corrected = _review_batch(db, competency, grade, batch)
        except Exception:
            logger.exception("Indigo/relecture : lot échoué (%s) — versions adaptées conservées",
                             competency.code)
            corrected = {}
        for number, valid in corrected.items():
            result[number] = valid          # la version relue remplace l'adaptée

    return {n: _strip_raw(v) for n, v in result.items()}


def _strip_raw(valid: dict) -> dict:
    """Retire le contrat brut archivé (`_raw`) avant persistance : il n'a servi
    qu'à la relecture, il n'a rien à faire en base."""
    valid.pop("_raw", None)
    return valid

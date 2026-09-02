"""Indigo — mode « QCM only » : génération DÉTERMINISTE et corrigeable par CV.

Deuxième pipeline de génération de l'onglet Exercices, à côté de l'adaptation
libre (services.indigo_gemini + services.indigo_verify). Elle ne remplace pas
l'autre : le sélecteur de l'onglet choisit laquelle tourne.

Quatre partis pris, tous pris CONTRE l'expérience de la pipeline classique :

  1. TROIS formats de réponse, pas dix. QCM à réponse unique, QCM à choix
     multiples, grille à cocher. Tous les trois se corrigent par vision par
     ordinateur, en local et gratuitement : aucune copie ne part chez Mathpix,
     aucune note ne dépend d'un LLM.
  2. Un prompt COURT (prompts/indigo/qcm.txt, ~95 lignes contre ~1 330 pour le
     contrat partagé). Le menu des formats, les règles de barème, de mise en
     lignes et de phrases à trous n'ont plus lieu d'être : il ne reste qu'une
     mission, trois formats, trois exemples.
  3. Le barème est CODÉ (services.scoring.qcm_bareme), jamais demandé au modèle.
  4. La vérification est DÉTERMINISTE (services.indigo_check) : sympy recalcule
     ce que le modèle a déclaré. Une variante non vérifiable est ABANDONNÉE.

Et chaque exercice du manuel donne un TRIO : la version de base, un dérivé
FACILE (élève en difficulté) et un dérivé DIFFICILE (élève à l'aise) — les trois
niveaux de difficulté de la plateforme (§ exercise_gen.DIFFICULTY_LEVELS).

DEUX CHOSES QUE LE MODÈLE N'ÉCRIT PAS, pour ne pas payer des tokens de sortie
inutiles (un exercice en produit déjà trois) :

  • le CORRIGÉ DU PROFESSEUR. Il est déjà lu — gratuitement — dans le manuel du
    professeur (services.indigo_index lit sa couche texte) et
    `indigo._persist_exercise` le reprend tel quel. Le faire recopier par le
    modèle, c'était payer une paraphrase de ce qu'on avait déjà.
  • DEUX guides sur trois. Le guide d'auto-correction est écrit UNE fois par
    exercice source et sert aux trois variantes : elles portent la même notion,
    la même règle et le même piège — seuls les nombres changent. Il s'adresse à
    un élève EN DIFFICULTÉ, d'où les contraintes de langage posées dans le
    prompt (phrases courtes, tutoiement, jamais la solution).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Competency
from . import exercise_gen, indigo_check, indigo_llm, prompts, providers, scoring
from . import statement as statement_mod
from .gemini_gen import _competency_name

logger = logging.getLogger("app.indigo")

PROMPT_VERSION = "indigo-qcm-1"

# Les trois variantes d'un exercice, et le niveau de difficulté de chacune.
# L'ORDRE compte : la base est validée en premier, de sorte qu'en cas de doublon
# ce soit un dérivé qui saute, jamais elle.
VARIANTS = ("base", "facile", "difficile")
VARIANT_LEVEL = {"facile": 1, "base": 2, "difficile": 3}


def choose_batch_size(n: int) -> int:
    """Nombre d'exercices SOURCE par appel. Chacun rend trois variantes, d'où un
    lot volontairement petit (§ settings.indigo_qcm_batch_size) : c'est le volume
    de SORTIE qui casse un lot, pas le nombre d'entrées."""
    return max(1, min(int(settings.indigo_qcm_batch_size), max(1, n)))


def _system_prompt(competency: Competency, grade: str) -> str:
    """Prompt du mode QCM — ÉDITABLE dans prompts/indigo/qcm.txt.

    Il ne passe PAS par exercise_gen.format_contract : ce contrat décrit les dix
    formats de réponse, le barème à estimer et les règles de champs à trous,
    dont rien ne s'applique ici. Le schéma de sortie est décrit par le fichier
    lui-même, et c'est `_to_raw` qui le remet dans la forme attendue par le
    validateur partagé."""
    return (prompts.load("indigo", "qcm")
            .replace("§GRADE§", grade)
            .replace("§COMPETENCY§", _competency_name(competency))
            .replace("§CHAPTER§", f"{competency.chapter_code} {competency.chapter_name}".strip())
            .replace("§DOMAIN§", f"{competency.domain_code} {competency.domain_name}".strip()))


# ------------------------------------------------------- conversion + validation

def _to_raw(variant: dict, guide: str) -> dict:
    """Forme compacte du modèle -> dict attendu par
    `exercise_gen._validate_exercise` (schéma `answer`/`choices` partagé).

    On garde le validateur ÉPROUVÉ — bornes des propositions, distracteurs
    dupliqués, auto-vérification de la grille par le moteur de correction — sans
    garder son prompt. `bareme_points` est volontairement absent (barème codé),
    et le `guide` vient de l'exercice, pas de la variante : il est COMMUN aux
    trois."""
    rtype = variant.get("response_type")
    raw = {"response_type": rtype, "kind": "application",
           "statement": variant.get("statement") or "",
           "correction": guide}
    if rtype == "checkbox_grid":
        raw["answer"] = {"type": "grid", "cols": variant.get("cols") or [],
                         "rows": variant.get("rows") or []}
    else:
        raw["choices"] = variant.get("choices") or []
        raw["answer"] = {"type": "choice", "correct": variant.get("correct") or []}
    return raw


def _finalize_variant(variant: dict, guide: str, competency: Competency, db: Session,
                      existing_norms: set[str], *, has_figure: bool) -> tuple[dict | None, list[str]]:
    """Valide UNE variante. Retourne (contrat interne, problèmes).

    Trois portes, dans l'ordre du moins cher au plus cher :
      1. vérification déterministe (lint + sympy) — c'est elle qui rend des
         raisons LISIBLES, réutilisables telles quelles pour la réparation ;
      2. validateur partagé — dernier rempart de format ;
      3. barème codé — un format hors bornes lève plutôt que d'être écrêté.
    """
    problems = indigo_check.verify(variant, has_figure=has_figure)
    if problems:
        return None, problems

    raw = _to_raw(variant, guide)
    valid = exercise_gen._validate_exercise(
        raw, competency, db, existing_norms, allow_geometry_text=True)
    if valid is None:
        reason = exercise_gen.diagnose_rejection(raw, competency)
        return None, [f"refusé par le validateur partagé : {reason}"]
    if valid["response_type"] not in indigo_check.QCM_TYPES:
        return None, [f"format « {valid['response_type']} » interdit en mode QCM"]

    try:
        valid["grading"] = scoring.with_qcm_bareme(valid["grading"], valid["response_type"])
    except ValueError as e:
        return None, [str(e)]
    return valid, []


# --------------------------------------------------------------------- appel

# Bornes du guide d'auto-correction. Le plancher est celui du validateur partagé
# (exercise_gen._validate_exercise exige 5 caractères) ; le plafond tient au
# public visé : au-delà de trois phrases, un élève en difficulté ne lit plus.
GUIDE_MIN, GUIDE_MAX = 20, 400


def _guide(item: dict) -> tuple[str, str | None]:
    """(guide utilisable, problème éventuel) pour un exercice source.

    Un guide manquant ou hors bornes ne fait PAS tomber les trois variantes : on
    retient le problème (il part dans l'aller-retour de réparation) et, en
    dernier ressort, on pose le même placeholder que la pipeline classique —
    l'admin voit alors exactement ce qu'il lui reste à écrire, au lieu de perdre
    trois QCM par ailleurs corrects."""
    from .indigo import _GUIDE_TODO          # import tardif : cycle indigo <-> qcm
    guide = statement_mod.repair_latex_control_chars(
        str(item.get("guide") or "").strip())
    if len(guide) < GUIDE_MIN:
        return _GUIDE_TODO, (f"guide trop court ({len(guide)} caractères, minimum "
                             f"{GUIDE_MIN}) : une règle et un piège, en phrases "
                             f"courtes, pour un élève en difficulté")
    if len(guide) > GUIDE_MAX:
        return guide[:GUIDE_MAX], (f"guide trop long ({len(guide)} caractères, maximum "
                                   f"{GUIDE_MAX}) : 1 à 3 phrases courtes suffisent")
    return guide, None


def _call(db: Session, system: str, payload: dict, correlation_id: str) -> dict | None:
    return indigo_llm.call(db, "qcm", system, payload, correlation_id)


def _payload(grade: str, competency: Competency, manuals: list[dict]) -> dict:
    return {
        "grade_level": grade,
        "competency_code": competency.code,
        "competency_label": _competency_name(competency),
        "exercises_to_convert": [
            {"number": str(m.get("number", "")), "statement": m.get("statement", ""),
             "correction": m.get("correction", ""), "has_figure": bool(m.get("has_figure"))}
            for m in manuals],
    }


def generate_batch(db: Session, competency: Competency, grade: str,
                   manuals: list[dict], existing_norms: set[str],
                   errors: list[str] | None = None) -> dict[str, dict]:
    """Convertit un LOT d'exercices du manuel en trios de QCM vérifiés.

    Retourne {source_number -> {"guide": str,
                                "variants": [(kind, contrat interne), ...]}}.
    Le `guide` est COMMUN aux trois variantes (§ en-tête du module) ; le corrigé
    du professeur, lui, n'est pas demandé au modèle — il vient du manuel.
    Un exercice absent de la sortie n'a produit AUCUNE variante exploitable :
    l'appelant retombe alors sur le repli OCR brut, comme en mode classique.

    Un ÉCHEC D'APPEL coupe le lot en deux plutôt que de repartir en appels
    unitaires — c'est la correction de l'incident A1.3 du 02/08, où un lot en
    échec multipliait les appels et épuisait le plafond de dépense au milieu de
    l'extraction. Seul `BudgetExceeded` remonte : découper n'y changerait rien.
    """
    try:
        return _generate_call(db, competency, grade, manuals, existing_norms)
    except providers.BudgetExceeded:
        raise
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        if len(manuals) <= 1:
            logger.warning("Indigo/QCM : conversion de l'exercice n°%s échouée (%s) — %s",
                           manuals[0].get("number") if manuals else "?",
                           competency.code, reason)
            if errors is not None:
                errors.append(reason)
            return {}
        mid = len(manuals) // 2
        logger.warning("Indigo/QCM : lot de %s exercices en échec (%s) — %s ; "
                       "coupé en deux (%s + %s)", len(manuals), competency.code,
                       reason, mid, len(manuals) - mid)
        out: dict[str, dict] = {}
        for half in (manuals[:mid], manuals[mid:]):
            out.update(generate_batch(db, competency, grade, half, existing_norms, errors))
        return out


def _generate_call(db: Session, competency: Competency, grade: str,
                   manuals: list[dict], existing_norms: set[str]) -> dict[str, dict]:
    """UN appel de conversion (+ au plus UNE réparation), sans repli de découpe."""
    system = _system_prompt(competency, grade)
    nums = ",".join(str(m.get("number", "?")) for m in manuals)
    data = _call(db, system, _payload(grade, competency, manuals),
                 f"indigo-qcm-{competency.code}-{nums}")

    by_number = {}
    for item in (data or {}).get("exercises") or []:
        if isinstance(item, dict):
            by_number.setdefault(str(item.get("source_number") or "").strip(), item)

    out: dict[str, dict] = {}
    rejected: list[dict] = []
    # guide de CHAQUE exercice, y compris ceux dont aucune variante n'est passée :
    # la réparation en a besoin, et il ne se redemande pas au modèle.
    guides: dict[str, str] = {}
    for pos, m in enumerate(manuals):
        num = str(m.get("number", "")).strip()
        item = by_number.get(num)
        if item is None:
            # repli positionnel : le modèle a parfois oublié de recopier le numéro
            items = [i for i in ((data or {}).get("exercises") or []) if isinstance(i, dict)]
            item = items[pos] if pos < len(items) else None
        if item is None:
            logger.warning("Indigo/QCM : pas de sortie pour l'exercice n°%s (%s)",
                           num, competency.code)
            continue
        kept, failed, guide = _accept_variants(item, m, competency, db, existing_norms)
        guides[num] = guide
        if kept:
            out[num] = {"guide": guide, "variants": kept}
        rejected += [{"source_number": num, **f} for f in failed]

    if rejected:
        _repair(db, system, competency, grade, manuals, rejected, out, guides,
                existing_norms)

    logger.info("Indigo/QCM : lot [%s] pour %s — %s/%s exercice(s), %s variante(s)",
                nums, competency.code, len(out), len(manuals),
                sum(len(v["variants"]) for v in out.values()))
    return out


def _accept_variants(item: dict, manual: dict, competency: Competency, db: Session,
                     existing_norms: set[str]) -> tuple[list[tuple[str, dict]], list[dict], str]:
    """Valide les trois variantes d'un exercice. Retourne (gardées, rejetées, guide).

    La BASE d'abord : si deux variantes se ressemblent trop (le modèle a rendu
    un « facile » identique à la base), c'est le dérivé que le dé-doublonnage du
    validateur écarte, jamais l'exercice principal."""
    has_figure = bool(manual.get("has_figure"))
    guide, guide_problem = _guide(item)
    kept: list[tuple[str, dict]] = []
    failed: list[dict] = []
    for kind in VARIANTS:
        variant = item.get(kind)
        if not isinstance(variant, dict):
            failed.append({"variant": kind, "problems": ["variante absente de la sortie"]})
            continue
        valid, problems = _finalize_variant(variant, guide, competency, db,
                                            existing_norms, has_figure=has_figure)
        if valid is None:
            logger.info("Indigo/QCM : n°%s / %s refusée — %s",
                        manual.get("number"), kind, " ; ".join(problems))
            failed.append({"variant": kind, "problems": problems, "sent": variant})
            continue
        kept.append((kind, valid))
    if guide_problem:
        logger.info("Indigo/QCM : n°%s — %s", manual.get("number"), guide_problem)
    return kept, failed, guide


def _repair(db: Session, system: str, competency: Competency, grade: str,
            manuals: list[dict], rejected: list[dict], out: dict[str, dict],
            guides: dict[str, str], existing_norms: set[str]) -> None:
    """UNE seule tentative de réparation, avec les raisons EXACTES trouvées par
    Python. Au-delà, la variante est abandonnée : un QCM dont on n'a pas pu
    vérifier les mathématiques ne doit jamais atteindre une copie, et enchaîner
    les allers-retours sur un modèle qui se trompe deux fois de suite consomme
    le plafond de dépense sans rien produire (§ incident A1.3).

    Écrit directement dans `out` — les variantes réparées rejoignent celles qui
    étaient déjà passées."""
    by_number = {str(m.get("number", "")).strip(): m for m in manuals}
    payload = {
        "grade_level": grade,
        "competency_label": _competency_name(competency),
        "instruction": ("Ces variantes ont été REFUSÉES par la vérification "
                        "Python. Corrige-les en respectant les mêmes règles et "
                        "le même schéma, et renvoie UNIQUEMENT les variantes "
                        "corrigées."),
        "rejected": [{"source_number": r["source_number"], "variant": r["variant"],
                      "problems": r["problems"], "previous": r.get("sent")}
                     for r in rejected],
        "format": {"exercises": [{"source_number": "str", "variant": "base|facile|difficile",
                                  "fixed": "<variante>"}]},
    }
    nums = ",".join(sorted({r["source_number"] for r in rejected}))
    try:
        data = _call(db, system, payload, f"indigo-qcm-fix-{competency.code}-{nums}")
    except providers.BudgetExceeded:
        raise
    except Exception:
        logger.warning("Indigo/QCM : réparation du lot [%s] impossible (%s) — "
                       "variantes abandonnées", nums, competency.code)
        return

    for fix in (data or {}).get("exercises") or []:
        if not isinstance(fix, dict):
            continue
        num = str(fix.get("source_number") or "").strip()
        kind = str(fix.get("variant") or "").strip()
        variant = fix.get("fixed")
        manual = by_number.get(num)
        if kind not in VARIANTS or manual is None or not isinstance(variant, dict):
            continue
        # le guide reste celui de l'exercice : la réparation porte sur UNE
        # variante, pas sur le texte commun aux trois
        entry = out.setdefault(num, {"guide": guides.get(num, ""), "variants": []})
        valid, problems = _finalize_variant(
            variant, entry["guide"], competency, db, existing_norms,
            has_figure=bool(manual.get("has_figure")))
        if valid is None:
            logger.info("Indigo/QCM : n°%s / %s abandonnée après réparation — %s",
                        num, kind, " ; ".join(problems))
            continue
        entry["variants"].append((kind, valid))

    for entry in out.values():                # remet la base en tête (§ VARIANTS)
        entry["variants"].sort(key=lambda kv: VARIANTS.index(kv[0]))

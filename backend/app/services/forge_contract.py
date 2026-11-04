"""Contrat des exercices forge, partagé par la pipeline et l'application.

Ce que la forge ajoute à un exercice tient dans `grading_json`, pour traverser
sans migration l'instantané d'une copie et les parties d'un composite :

- `feedback` : micro-corrections pré-rédigées, retrouvées par identifiant stable
  (les positions changent à l'impression) ;
- `route` : ordre de traitement à la correction ;
- `llm_brief` : dossier réservé au correcteur LLM, jamais imprimé ;
- `answer_form` : forme exigée d'une réponse manuscrite.

Ce module ne dessine rien et n'appelle aucun modèle.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROUTE_CV = "cv"
ROUTE_OCR_LLM = "ocr_llm"
ROUTE_LLM = "llm"
ROUTE_GEO = "geo"
ROUTES = (ROUTE_CV, ROUTE_OCR_LLM, ROUTE_LLM, ROUTE_GEO)

_ROUTE_BY_TYPE = {
    "qcm_single": ROUTE_CV, "qcm_multiple": ROUTE_CV,
    "checkbox_grid": ROUTE_CV, "matching": ROUTE_CV,
    "short_text": ROUTE_OCR_LLM, "multi_blank": ROUTE_OCR_LLM, "table_fill": ROUTE_OCR_LLM,
    "multiline_text": ROUTE_LLM,
    "geo_construction": ROUTE_GEO,
}
# Une route peut seulement monter vers le LLM : on ne retire jamais le correcteur
# à une rédaction, et une coche n'a pas de texte à lui soumettre.
_ALLOWED_OVERRIDE = {"short_text": ROUTE_LLM, "multi_blank": ROUTE_LLM, "table_fill": ROUTE_LLM}

# Lignes réservées sous la carte : 2 pour une réponse déterministe, 4 pour un
# raisonnement jugé par le LLM.
FEEDBACK_LINES = {ROUTE_CV: 2, ROUTE_OCR_LLM: 2, ROUTE_LLM: 4, ROUTE_GEO: 2}

ANSWER_FORMS = ("any", "irreducible", "decimal", "factored", "expanded", "scientific", "exact_decimal")
# Mot de l'énoncé -> forme qu'il impose. La consigne et le champ doivent concorder.
_FORM_WORDS = (
    (re.compile(r"\barrondi(?:e|es|s|r|t)?\b|\bencadr(?:e|es|er|ez|ent|ements?)\b", re.I), "exact_decimal"),
    (re.compile(r"irr[ée]ductible", re.I), "irreducible"),
    (re.compile(r"factoris", re.I), "factored"),
    (re.compile(r"d[ée]velopp", re.I), "expanded"),
    (re.compile(r"[ée]criture scientifique|notation scientifique", re.I), "scientific"),
    (re.compile(r"(?:nombre|[ée]criture|forme|valeur) d[ée]cimale?", re.I), "decimal"),
)

# Le placement des réponses changera d'une copie à l'autre (anti-triche) : un
# message ne peut désigner ni une lettre ni une position.
_POSITION_RE = re.compile(
    r"\b(?:case|r[ée]ponse|proposition|choix|option|ligne|colonne|point)\s+(?:n°\s*)?[a-h1-9]\b"
    r"|\b(?:premi[èe]re?|deuxi[èe]me|seconde?|troisi[èe]me|quatri[èe]me|derni[èe]re?)\s+"
    r"(?:case|r[ée]ponse|proposition|ligne|colonne)\b",
    re.I)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "misconceptions"
OTHER_ERROR = "autre"


def default_route(response_type: str) -> str:
    return _ROUTE_BY_TYPE[response_type]


def resolve_route(response_type: str, requested: str | None) -> tuple[str, str | None]:
    """(route retenue, problème éventuel). Le script fixe la route d'après le
    format ; seule une montée vers le LLM peut être demandée, et justifiée."""
    route = default_route(response_type)
    if not requested or requested == route:
        return route, None
    if _ALLOWED_OVERRIDE.get(response_type) == requested:
        return requested, None
    return route, f"route « {requested} » refusée pour {response_type} (attendue : {route})"


@lru_cache(maxsize=8)
def _taxonomy_file(subject: str) -> dict:
    path = _DATA_DIR / f"{subject}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"errors": []}


def error_codes(subject: str = "math") -> frozenset[str]:
    """Codes d'erreur valides : taxonomie fermée, par matière."""
    return frozenset(e["code"] for e in _taxonomy_file(subject)["errors"]) | {OTHER_ERROR}


def form_problem(statement: str, answer_form: str | None) -> str | None:
    """Refuse une consigne et un champ `answer_form` qui se contredisent."""
    form = answer_form or "any"
    if form not in ANSWER_FORMS:
        return f"answer_form {form!r} ∉ {ANSWER_FORMS}"
    demanded = [name for pattern, name in _FORM_WORDS if pattern.search(statement or "")]
    # exact_decimal passe avant « valeur décimale » : sans lui, un arrondi faux aurait des points.
    if demanded and (form not in demanded or (demanded[0] == "exact_decimal" and form != demanded[0])):
        return (f"l'énoncé demande la forme « {demanded[0]} » mais answer_form vaut « {form} » : "
                "sinon une réponse dans la mauvaise forme serait comptée juste")
    if form != "any" and not demanded:
        return f"answer_form vaut « {form} » mais l'énoncé ne demande pas cette forme à l'élève"
    return None


def message_problems(text: str, lines: int) -> list[str]:
    """Un message tient-il dans sa bande, une fois typographié, dans les deux
    polices ? Il ne désigne jamais une position."""
    from . import pdfgen, typography
    final = typography.french(text or "")
    problems = []
    if not final.strip():
        return ["message vide"]
    if _POSITION_RE.search(final):
        problems.append(f"message citant une position ou une lettre : « {final[:60]} »")
    for dyslexic, label in ((False, "Helvetica"), (True, "OpenDyslexic")):
        if not pdfgen.feedback_fits(final, lines, dyslexic=dyslexic):
            problems.append(f"message trop long pour {lines} ligne(s) en {label} : « {final[:60]} »")
    return problems


def feedback_problems(feedback, *, option_ids: list[str], level: int,
                      subject: str = "math") -> list[str]:
    """Contrôle les micro-corrections d'une question. [] = valide."""
    if not isinstance(feedback, dict):
        return ["feedback : objet attendu"]
    lines = feedback.get("lines")
    if lines not in (2, 4):
        return [f"feedback.lines vaut {lines!r}, attendu 2 ou 4"]
    codes = error_codes(subject)
    options = feedback.get("options") or {}
    problems: list[str] = []
    for needed in ("blank", "fallback"):
        node = feedback.get(needed)
        if not isinstance(node, dict) or not node.get("text"):
            problems.append(f"feedback.{needed} : message obligatoire")
    if level >= 2 and not (feedback.get("correct") or {}).get("text"):
        problems.append("feedback.correct : obligatoire sur un moyen ou un difficile")
    missing = [oid for oid in option_ids if oid not in options]
    if missing:
        problems.append(f"feedback.options : message manquant pour {', '.join(missing)}")
    unknown = sorted(set(options) - set(option_ids))
    if unknown:
        problems.append(f"feedback.options : identifiant inconnu {', '.join(unknown)}")
    wrong = feedback.get("wrong_answers") or []
    for key, node in list(options.items()) + [(f"réponse fausse {i}", w) for i, w in enumerate(wrong)]:
        if not isinstance(node, dict) or not node.get("text"):
            problems.append(f"feedback {key} : message obligatoire")
            continue
        if node.get("error") not in codes:
            problems.append(f"feedback {key} : code d'erreur inconnu {node.get('error')!r} "
                            "(propose-le dans proposed_errors si aucun code ne convient)")
    nodes = [feedback.get("correct"), feedback.get("blank"), feedback.get("fallback"),
             *options.values(), *wrong]
    for node in nodes:
        if isinstance(node, dict) and node.get("text"):
            problems += message_problems(node["text"], lines)
    return list(dict.fromkeys(problems))


def brief_problems(brief) -> list[str]:
    """Le dossier du correcteur LLM : objectif, étapes, erreurs attendues."""
    if not isinstance(brief, dict):
        return ["llm_brief : obligatoire pour une route passant par le LLM"]
    problems = []
    if not str(brief.get("objective") or "").strip():
        problems.append("llm_brief.objective : obligatoire")
    if not isinstance(brief.get("expected_steps"), list) or not brief["expected_steps"]:
        problems.append("llm_brief.expected_steps : au moins une étape")
    if not isinstance(brief.get("expected_errors"), list):
        problems.append("llm_brief.expected_errors : liste attendue (identifiant du message, piège)")
    return problems


def attach(grading: dict, *, feedback: dict | None, route: str, llm_brief: dict | None = None,
           answer_form: str | None = None, option_ids: list[str] | None = None,
           right_ids: list[str] | None = None, figure_layout: str | None = None,
           competency_code: str | None = None) -> dict:
    """Ajoute le contrat forge à un `grading` validé. À appeler APRÈS
    `_validate_exercise`, qui reconstruit la politique de notation."""
    out = dict(grading or {})
    out["route"] = route
    if feedback:
        out["feedback"] = feedback
    if llm_brief:
        out["llm_brief"] = llm_brief
    if answer_form and answer_form != "any":
        out["answer_form"] = answer_form
    if option_ids:
        out["option_ids"] = list(option_ids)
    if right_ids:
        out["right_ids"] = list(right_ids)
        # une décision par point de droite : un piège laissé libre est juste
        out["scoring"] = "per_point"
    if figure_layout:
        out["figure_layout"] = figure_layout
    if competency_code:
        out["competency_code"] = competency_code
    return out

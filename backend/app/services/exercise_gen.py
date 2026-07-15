"""Pipeline exgen-3 : création d'exercices et de rappels de leçon de haute qualité.

Architecture (chaque contenu passe TOUTES les barrières avant stockage) :

  1. SOURCES — mix de fournisseurs :
     - MathALÉA (déterministe, mathématiquement sûr) quand un générateur est
       rattaché à la compétence : moissonné dans la banque avec LaTeX balisé ;
     - DeepSeek (deepseek-v4-pro) avec un prompt calibré par niveau 1-5,
       imposant variété des tâches et problèmes contextualisés.
  2. VALIDATION DÉTERMINISTE (_validate_exercise) : LaTeX $...$ entièrement
     validé (liste blanche + rendu d'essai), figures rendues à blanc, QCM
     cohérents (distracteurs uniques, bonne réponse présente), géométrie sans
     verbe de construction (l'élève ne trace jamais : QCM ou texte), réponse
     de référence acceptée par le moteur de correction déterministe,
     anti-doublon par compétence.
  3. VÉRIFICATION CROISÉE (Claude) : verdict structuré avec scores
     (justesse, adéquation compétence, adéquation niveau 1-5, clarté) et
     liste de problèmes ; seuils stricts d'acceptation.
  4. RÉPARATION : un exercice refusé mais réparable repasse une fois par
     DeepSeek avec la critique, puis re-validation + re-vérification.
  5. BANQUE INCRÉMENTALE : stockage par (compétence × niveau 1-5), généré à
     la demande — la base grandit avec les classes réellement utilisées.

Formats de réponse, choisis dans cet ordre de préférence (le plus automatisable
d'abord — priorité au QCM, intervention humaine minimisée) :
  - qcm_single / qcm_multiple : reconnaissance, propriétés, géométrie ;
  - short_text : résultat numérique, fraction ou expression (OCR Mathpix),
    éventuellement inséré en ligne dans l'énoncé (marqueur {{blank}}) ;
  - table_fill : série de valeurs organisées en tableau (une cellule = un
    short_text) ;
  - multiline_text + rubric : raisonnement rédigé multi-étapes, problèmes,
    nombre de lignes proportionné au nombre d'étapes ;
  - matching : association deux colonnes (dernier recours avant le tracé —
    correction par détection de trait manuscrit, jamais garantie 100% auto) ;
  - manual_drawing : tracé/construction/schéma sur figure — SEUL format où
    l'élève dessine ; toujours envoyé en correction manuelle, jamais noté par
    la pipeline automatique.
"""

import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Competency, ExerciseCatalog, ExerciseCompetency, GeneratedExercise,
    LessonSnippet,
)
from . import figures, grading, mathrender, providers

logger = logging.getLogger(__name__)

PROMPT_VERSION = "exgen-3"

LEVEL_SPECS = {
    1: ("découverte", "application directe d'UNE seule notion, nombres entiers petits "
        "(≤ 20), aucune étape intermédiaire, vocabulaire minimal ; l'élève le plus "
        "fragile de la classe doit pouvoir réussir s'il connaît la définition"),
    2: ("consolidation", "application directe, nombres un peu plus grands, relatifs ou "
        "décimaux simples (1 chiffre après la virgule), une seule étape"),
    3: ("standard", "niveau attendu du programme officiel : une à deux étapes, nombres "
        "du quotidien, éventuellement un petit contexte concret"),
    4: ("approfondissement", "deux à trois étapes, pièges classiques (priorités, signes, "
        "unités) qu'une bonne maîtrise évite, données moins directes"),
    5: ("défi", "transfert : problème contextualisé riche ou raisonnement en plusieurs "
        "étapes, l'élève doit choisir lui-même la méthode ; réponse rédigée attendue"),
}

# Domaines de géométrie/mesure où tracer est interdit
GEOMETRY_DOMAINS = {"EG", "GM"}

FORBIDDEN_GEOMETRY_VERBS = {
    "trace", "tracer", "tracez", "tracée", "tracés", "tracées",
    "construis", "construire", "construisez", "construit", "construite",
    "construits", "construites",
    "dessine", "dessiner", "dessinez", "dessiné", "dessinée", "dessinés", "dessinées",
    "reporte", "reporter", "reportez",
    "draw", "drawn", "build", "sketch",
}
# « place/placer » n'est interdit que hors droite graduée fournie en figure —
# trop de faux positifs sinon ; on l'interdit aussi, l'élève écrit l'abscisse.
FORBIDDEN_GEOMETRY_VERBS |= {"place", "placer", "placez", "placé", "placée"}

VALID_RESPONSE_TYPES = {"qcm_single", "qcm_multiple", "short_text", "multiline_text",
                        "table_fill", "matching", "manual_drawing"}

_PROGRAM_CACHE: dict | None = None


def _program_data() -> dict:
    global _PROGRAM_CACHE
    if _PROGRAM_CACHE is None:
        path = Path(__file__).resolve().parents[1] / "data" / "competencies_fr.json"
        _PROGRAM_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _PROGRAM_CACHE


def _chapter_objectives(grade: str, chapter_name: str) -> list[str]:
    for fw in _program_data()["frameworks"]:
        if fw["grade_level"] != grade:
            continue
        for dom in fw["domains"]:
            for chap in dom["chapters"]:
                if chap["name"] == chapter_name:
                    return [c["label"] for c in chap["competencies"]]
    return []


def _grade_of(db: Session, competency: Competency) -> str:
    from ..models import CompetencyFramework
    fw = db.get(CompetencyFramework, competency.framework_id)
    return fw.grade_level if fw else "5e"


def student_level_to_difficulty(level_1_10: int) -> int:
    """Niveau élève 1-10 -> niveau d'exercice 1-5."""
    return max(1, min(5, (max(1, min(10, level_1_10)) + 1) // 2))


# ================================================================ validation

def _has_raw_latex_outside_math(text: str) -> bool:
    """Une commande LaTeX hors des bornes $...$ = balisage cassé -> refus."""
    return any(not is_math and "\\" in content
               for content, is_math in mathrender.split_math_spans(text))


def _is_geometry_verb(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(rf"\b{verb}\b", text_lower)
               for verb in FORBIDDEN_GEOMETRY_VERBS)


def _normalize_statement_for_dedup(s: str) -> str:
    s = mathrender.strip_math(s).lower()
    s = re.sub(r"\d+([.,]\d+)?", "#", s)
    return re.sub(r"\s+", " ", s).strip()


def _check_text(text: str, min_len: int = 1, max_len: int = 1200) -> bool:
    """Texte balisé valide : longueur, LaTeX des spans, pas de LaTeX hors spans."""
    if not min_len <= len(text) <= max_len:
        return False
    if _has_raw_latex_outside_math(text):
        return False
    return mathrender.has_valid_math(text)


def _validate_exercise(raw: dict, competency: Competency, db: Session,
                       existing_norms: set[str]) -> dict | None:
    """Valide un exercice candidat. Retourne le contrat interne ou None."""
    if not isinstance(raw, dict):
        return None
    statement = str(raw.get("statement", "")).strip()
    correction = str(raw.get("correction", "")).strip()
    if not _check_text(statement, 15, 1200) or not _check_text(correction, 5, 1500):
        return None

    rtype = raw.get("response_type", "short_text")
    if rtype not in VALID_RESPONSE_TYPES:
        return None

    is_geometry = competency.domain_code in GEOMETRY_DOMAINS
    # seul manual_drawing autorise les verbes de construction (l'élève y
    # dessine réellement) ; tout autre format géométrique doit s'en passer
    if is_geometry and rtype != "manual_drawing" and _is_geometry_verb(statement):
        return None

    # anti-doublon (le set est maintenu par l'appelant pour couvrir le lot en cours)
    normalized = _normalize_statement_for_dedup(statement)
    if normalized in existing_norms:
        return None

    kind = raw.get("kind") if raw.get("kind") in ("application", "probleme") else "application"

    # figure optionnelle : validée par rendu à blanc, sinon abandonnée
    figure_json = figures.validate_figure(raw.get("figure"))

    answer = raw.get("answer") or {}
    atype = answer.get("type")

    def _contract(expected, gpolicy, rtype):
        existing_norms.add(normalized)
        return {"statement": statement, "correction": correction,
                "response_type": rtype, "expected": expected, "grading": gpolicy,
                "figure_json": figure_json, "kind": kind}

    # ---------------- QCM ----------------
    if rtype in ("qcm_single", "qcm_multiple"):
        choices = [str(c).strip() for c in (raw.get("choices") or [])]
        correct = answer.get("correct", [])
        if not (3 <= len(choices) <= 8):
            return None
        if len({mathrender.strip_math(c).lower() for c in choices}) != len(choices):
            return None  # distracteurs dupliqués
        if not all(_check_text(c, 1, 120) for c in choices):
            return None
        if not (isinstance(correct, list) and correct
                and all(isinstance(i, int) and 0 <= i < len(choices) for i in correct)):
            return None
        correct = sorted(set(correct))
        if rtype == "qcm_single" and len(correct) != 1:
            return None
        if len(correct) >= len(choices):
            return None  # « tout est juste » n'évalue rien
        expected = {"type": "choice", "correct": correct}
        gpolicy = {"max_score": 1, "comparator": "qcm", "negative": 0, "choices": choices}
        return _contract(expected, gpolicy, rtype)

    # ---------------- tableau à remplir ----------------
    if rtype == "table_fill":
        if atype != "table":
            return None
        try:
            rows, cols = int(answer.get("rows")), int(answer.get("cols"))
        except (TypeError, ValueError):
            return None
        if not (2 <= rows <= 6 and 2 <= cols <= 6):
            return None
        cells = answer.get("cells")
        if not (isinstance(cells, list) and len(cells) == rows
                and all(isinstance(r, list) and len(r) == cols for r in cells)):
            return None
        col_labels = answer.get("col_labels")
        row_labels = answer.get("row_labels")
        if col_labels is not None and (not isinstance(col_labels, list) or len(col_labels) != cols):
            return None
        if row_labels is not None and (not isinstance(row_labels, list) or len(row_labels) != rows):
            return None
        validated_cells = []
        for row in cells:
            vrow = []
            for cell in row:
                if not isinstance(cell, dict):
                    return None
                cval = _validate_cell(cell)
                if cval is None:
                    return None
                vrow.append(cval)
            validated_cells.append(vrow)
        expected = {"type": "table", "rows": rows, "cols": cols, "cells": validated_cells}
        gpolicy = {"max_score": rows * cols, "comparator": "table_cells",
                  "cells": validated_cells,
                  "col_labels": [str(c) for c in col_labels] if col_labels else None,
                  "row_labels": [str(r) for r in row_labels] if row_labels else None}
        # grade(table_cells) attend un cell_texts À PLAT (une entrée par cellule,
        # dans l'ordre ligne par ligne) — cf. grading._grade table_cells
        reference = [_cell_reference_text(c) for r in validated_cells for c in r]
        verdict = grading.grade(expected, gpolicy, "", 0.99, cell_texts=reference)
        if verdict["score"] < gpolicy["max_score"]:
            return None
        return _contract(expected, gpolicy, rtype)

    # ---------------- points à relier ----------------
    if rtype == "matching":
        if atype != "matching":
            return None
        left = [str(c).strip() for c in (answer.get("left") or [])]
        right = [str(c).strip() for c in (answer.get("right") or [])]
        pairs = answer.get("pairs")
        if not (3 <= len(left) <= 6 and 3 <= len(right) <= 6):
            return None
        if not all(_check_text(c, 1, 80) for c in left + right):
            return None
        if not (isinstance(pairs, list) and pairs
                and all(isinstance(p, (list, tuple)) and len(p) == 2
                       and isinstance(p[0], int) and isinstance(p[1], int)
                       and 0 <= p[0] < len(left) and 0 <= p[1] < len(right)
                       for p in pairs)):
            return None
        pairs = [[int(p[0]), int(p[1])] for p in pairs]
        if len({p[0] for p in pairs}) != len(pairs) or len({p[1] for p in pairs}) != len(pairs):
            return None  # chaque item utilisé une seule fois
        expected = {"type": "matching", "left": left, "right": right, "pairs": pairs}
        gpolicy = {"max_score": len(pairs), "comparator": "matching",
                  "left": left, "right": right, "pairs": pairs}
        verdict = grading.grade(expected, gpolicy, "", 0.99, selected_pairs=pairs)
        if verdict["score"] < gpolicy["max_score"]:
            return None
        return _contract(expected, gpolicy, rtype)

    # ---------------- tracé / dessin (toujours correction manuelle) ----------------
    if rtype == "manual_drawing":
        expected = {"type": "manual"}
        gpolicy = {"max_score": 1, "comparator": "manual"}
        return _contract(expected, gpolicy, rtype)

    # ---------------- réponses construites ----------------
    if rtype == "short_text" and statement.count("{{blank}}") > 1:
        return None  # au plus un trou en ligne par énoncé
    if atype == "integer":
        try:
            expected = {"type": "integer", "value": int(answer["value"])}
        except (KeyError, TypeError, ValueError):
            return None
        gpolicy = {"max_score": 1, "comparator": "numeric", "tolerance": 0}
    elif atype in ("decimal", "number"):
        try:
            expected = {"type": "decimal",
                        "value": float(str(answer["value"]).replace(",", "."))}
        except (KeyError, TypeError, ValueError):
            return None
        gpolicy = {"max_score": 1, "comparator": "numeric", "tolerance": 0}
    elif atype == "rational":
        v = answer.get("value")
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            return None
        try:
            expected = {"type": "rational", "value": [int(v[0]), int(v[1])]}
        except (TypeError, ValueError):
            return None
        if expected["value"][1] == 0:
            return None
        gpolicy = {"max_score": 2, "comparator": "rational_equiv"}
    elif atype == "expression":
        val = str(answer.get("value", "")).strip()
        if not val or len(val) > 120:
            return None
        expected = {"type": "expression", "value": val,
                    "variable": answer.get("variable", "x")}
        gpolicy = {"max_score": 2, "comparator": "symbolic_equiv"}
    elif atype == "text":
        val = str(answer.get("value", "")).strip()
        if not val or len(val) > 80:
            return None
        expected = {"type": "text", "value": val}
        gpolicy = {"max_score": 1, "comparator": "text_equal"}
    elif atype == "rubric":
        steps = answer.get("steps", [])
        if not isinstance(steps, list) or not (2 <= len(steps) <= 6):
            return None
        validated_steps = []
        for step in steps:
            if not isinstance(step, dict):
                return None
            desc = str(step.get("description", "")).strip()
            expected_text = str(step.get("expected_text", "")).strip()
            try:
                points = max(1, min(3, int(step.get("points", 1))))
            except (TypeError, ValueError):
                return None
            if not desc or not expected_text:
                return None
            if not _check_text(expected_text, 1, 400):
                return None
            validated_steps.append({"description": desc,
                                    "expected_text": expected_text, "points": points})
        total = sum(s["points"] for s in validated_steps)
        try:
            lines = int(answer.get("lines", 0))
        except (TypeError, ValueError):
            lines = 0
        lines = max(3, min(12, lines or len(validated_steps) * 2))
        expected = {"type": "rubric", "steps": validated_steps}
        gpolicy = {"max_score": total, "comparator": "rubric", "steps": validated_steps,
                   "rubric": validated_steps, "lines": lines}
        return _contract(expected, gpolicy, "multiline_text")
    else:
        return None

    if rtype == "short_text" and "{{blank}}" in statement:
        expected["inline"] = True

    # la réponse de référence doit passer le moteur de correction déterministe
    verdict = grading.grade(expected, gpolicy, _reference_text(expected), 0.99)
    if verdict["score"] < gpolicy["max_score"]:
        return None

    return _contract(expected, gpolicy, rtype)


def _validate_cell(cell: dict) -> dict | None:
    """Valide une cellule de table_fill : {"type": "integer"|"decimal"|"text", "value": ...}."""
    ctype = cell.get("type")
    if ctype == "integer":
        try:
            return {"type": "integer", "value": int(cell["value"])}
        except (KeyError, TypeError, ValueError):
            return None
    if ctype in ("decimal", "number"):
        try:
            return {"type": "decimal",
                    "value": float(str(cell["value"]).replace(",", "."))}
        except (KeyError, TypeError, ValueError):
            return None
    if ctype == "text":
        val = str(cell.get("value", "")).strip()
        if not val or len(val) > 40:
            return None
        return {"type": "text", "value": val}
    return None


def _cell_reference_text(cell: dict) -> str:
    return str(cell["value"])


def _reference_text(expected: dict) -> str:
    t = expected["type"]
    if t == "rational":
        return f"{expected['value'][0]}/{expected['value'][1]}"
    if t in ("expression", "text"):
        return str(expected["value"])
    return str(expected["value"])


# ================================================================ vérification croisée

# Seuils d'acceptation du verdict structuré (sur 5)
_VERIFY_THRESHOLDS = {"justesse": 4, "adequation_competence": 3,
                      "adequation_niveau": 3, "clarte": 3}

_VERIFY_SYSTEM = (
    "Tu es un professeur agrégé de mathématiques, relecteur intransigeant de sujets "
    "de collège français. On te soumet UN exercice avec sa correction et sa réponse "
    "attendue. Vérifie en REFAISANT les calculs toi-même :\n"
    "1. justesse : la correction et la réponse attendue sont-elles mathématiquement "
    "exactes et cohérentes avec l'énoncé ? (refais chaque calcul)\n"
    "2. adequation_competence : l'exercice évalue-t-il bien la compétence visée "
    "(et pas une autre) ?\n"
    "3. adequation_niveau : la difficulté correspond-elle au niveau annoncé "
    "(échelle 1-5 croissante) ?\n"
    "4. clarte : l'énoncé est-il autoporteur, sans ambiguïté, avec toutes les "
    "données nécessaires, dans un français adapté à l'âge ?\n"
    "5. Pour un QCM : la bonne réponse est-elle indiscutable et unique parmi les "
    "choix ? Les distracteurs sont-ils plausibles mais faux ?\n"
    "Réponds en JSON strict : {\"valide\": bool, \"scores\": {\"justesse\": 0-5, "
    "\"adequation_competence\": 0-5, \"adequation_niveau\": 0-5, \"clarte\": 0-5}, "
    "\"problemes\": [str], \"reparable\": bool}. "
    "valide=true SEULEMENT si tu certifierais cet exercice pour impression sans "
    "relecture humaine."
)


def _verdict_passes(result: dict) -> bool:
    if not result.get("valide", False):
        return False
    scores = result.get("scores") or {}
    for key, minimum in _VERIFY_THRESHOLDS.items():
        try:
            if float(scores.get(key, 5)) < minimum:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _verify_with_claude(db: Session, competency: Competency, level: int,
                        candidate: dict) -> tuple[bool, dict]:
    """Vérification croisée structurée. Retourne (accepté, verdict_json)."""
    payload = {
        "competency_code": competency.code,
        "competency_label": competency.label,
        "chapter": competency.chapter_name,
        "domain": competency.domain_name,
        "level": level,
        "level_description": LEVEL_SPECS[level][1],
        "kind": candidate["kind"],
        "statement": candidate["statement"],
        "choices": candidate["grading"].get("choices"),
        "correction": candidate["correction"],
        "response_type": candidate["response_type"],
        "expected": candidate["expected"],
        "figure": candidate.get("figure_json"),
    }
    try:
        result = providers.claude_json(
            db, "exercise_verification", _VERIFY_SYSTEM, payload,
            max_tokens=700, correlation_id=f"exverif-{competency.code}-L{level}")
    except Exception as e:
        return False, {"valide": False, "problemes": [f"Vérification indisponible : {e}"],
                       "reparable": False}
    return _verdict_passes(result), result


def _repair_exercise(db: Session, competency: Competency, level: int,
                     candidate: dict, verdict: dict) -> dict | None:
    """Une seule passe de réparation DeepSeek guidée par la critique du vérificateur."""
    problems = verdict.get("problemes") or []
    if not verdict.get("reparable", False) or not problems:
        return None
    system = (
        "Tu corriges UN exercice de mathématiques refusé par un relecteur. "
        "Conserve l'intention et le niveau, corrige UNIQUEMENT les problèmes listés. "
        + _GEN_FORMAT_RULES
        + " Réponds en JSON strict : {\"exercises\": [<l'exercice corrigé, même schéma>]}."
    )
    payload = {"exercise": {k: candidate[k] for k in
                            ("statement", "correction", "response_type", "kind")},
               "expected": candidate["expected"],
               "choices": candidate["grading"].get("choices"),
               "figure": candidate.get("figure_json"),
               "problemes": problems[:6],
               "level": level, "level_description": LEVEL_SPECS[level][1],
               "competency_label": competency.label}
    try:
        data = providers.deepseek_json(
            db, "exercise_repair", system, payload,
            max_tokens=1800, model=settings.deepseek_pro_model,
            correlation_id=f"exrepair-{competency.code}-L{level}")
    except Exception:
        return None
    fixed = (data.get("exercises") or [None])[0]
    return fixed if isinstance(fixed, dict) else None


# ================================================================ prompt de génération

_GEN_FORMAT_RULES = (
    "RÈGLES DE FORMAT (obligatoires) : tout objet mathématique (nombre en écriture "
    "fractionnaire, expression, égalité, unité collée à une valeur) est balisé $...$ "
    "en LaTeX. Commandes autorisées UNIQUEMENT : \\dfrac \\frac \\sqrt \\times \\div "
    "\\cdot \\pm \\leq \\geq \\neq \\approx \\pi \\text{...} \\% ^ _ ( ) [ ] { }. "
    "Notation française : virgule décimale ($3{,}5$), unités en \\text ($7{,}5\\ \\text{cm}$). "
    "JAMAIS de LaTeX hors des bornes $...$, jamais de \\\\ ni d'environnements. "
    "Les nombres simples isolés dans une phrase (« 3 crayons ») restent en texte."
)

# Bloc partagé (choix du format de réponse + figures + contrat JSON) —
# réutilisé tel quel par la génération DeepSeek (_GEN_SYSTEM_TEMPLATE) ET par
# la structuration Sésamaths (services.sesamaths), pour que les deux
# pipelines produisent EXACTEMENT le même contrat, consommé par
# _validate_exercise sans distinction de provenance.
_RESPONSE_FORMAT_BLOCK = (
    "CHOIX DU FORMAT DE RÉPONSE — priorité au format le PLUS automatisable, "
    "l'intervention humaine à la correction doit rester exceptionnelle. Reformule "
    "la tâche si besoin pour qu'elle rentre dans l'un de ces formats, dans cet ordre "
    "de préférence :\n"
    "1. \"qcm_single\"/\"qcm_multiple\" : reconnaissance, propriété, lecture de figure. "
    "3 à 8 choix, distracteurs = erreurs TYPIQUES d'élèves (erreur de signe, de "
    "priorité, confusion périmètre/aire...), une seule formulation possible de la "
    "bonne réponse ; PRÉFÈRE ce format à chaque fois qu'une tâche de "
    "reconnaissance/classement le permet, quitte à transformer une question ouverte "
    "en QCM à choix nombreux.\n"
    "2. \"short_text\" : un résultat unique — answer.type parmi \"integer\", "
    "\"decimal\", \"rational\" (valeur [num, den]), \"expression\" (réduite, variable "
    "précisée), \"text\" (mot exact attendu, ex. « isocèle »). Si la réponse s'insère "
    "naturellement au milieu de la phrase ou de l'équation (texte à trous), place le "
    "marqueur littéral {{blank}} à cet endroit précis dans \"statement\" (au plus un "
    "par exercice) ; sinon la case de réponse est ajoutée après l'énoncé.\n"
    "3. \"table_fill\" : quand plusieurs résultats du même type forment naturellement "
    "une grille (ex. compléter une table de valeurs, un tableau de proportionnalité) — "
    "answer = {\"type\":\"table\",\"rows\":int (2-6),\"cols\":int (2-6),"
    "\"col_labels\":[str]?,\"row_labels\":[str]?,\"cells\":[[{\"type\":\"integer\"|"
    "\"decimal\"|\"text\",\"value\":...}]]} (une ligne = une liste de cellules).\n"
    "4. \"multiline_text\" + answer.type=\"rubric\" : raisonnement rédigé (obligatoire "
    "pour les problèmes et le niveau 5) — 2 à 5 étapes {description, expected_text, "
    "points 1-3}, expected_text = ce qu'on doit lire sur la copie, balisé $...$ ; "
    "ajoute \"lines\": nombre de lignes de rédaction à prévoir (3-12, proportionné à "
    "la longueur attendue de la réponse).\n"
    "5. \"matching\" (DERNIER RECOURS avant le tracé, à n'utiliser que si aucun des "
    "formats ci-dessus ne convient à une tâche d'association) : deux listes à relier — "
    "answer = {\"type\":\"matching\",\"left\":[str] (3-6),\"right\":[str] (3-6),"
    "\"pairs\":[[i,j]]} (indices 0-based, chaque élément utilisé une seule fois).\n"
    "6. \"manual_drawing\" (INTERDIT sauf construction géométrique réellement "
    "impossible à reformuler en QCM/texte — l'élève trace alors sur la copie et la "
    "correction est TOUJOURS manuelle, jamais automatique) : aucune réponse "
    "structurée requise, \"answer\" peut être omis.\n\n"
    "{geometry_rules}"
    "FIGURES : si une figure aide (géométrie, droite graduée, repère), ajoute "
    "\"figure\": {\"type\": \"rectangle\"|\"triangle\"|\"circle\"|\"angle\"|"
    "\"number_line\"|\"coordinate_plane\", \"params\": {...}} avec les MÊMES valeurs "
    "que l'énoncé. Types de params : rectangle{length,width,unit,show_diagonal} ; "
    "triangle{base,height,unit,right_angle_at} ; circle{radius,unit,show_diameter} ; "
    "angle{degrees,label} ; number_line{min,max,points:[{value,label}]} ; "
    "coordinate_plane{points:[{x,y,label}],grid}.\n\n"
    + _GEN_FORMAT_RULES + "\n\n"
    "Réponds UNIQUEMENT en JSON strictement valide :\n"
    '{"exercises":[{"kind":"application"|"probleme","statement":str,"correction":str '
    "(rédigée comme au tableau, chaque étape justifiée),"
    '"response_type":"short_text"|"qcm_single"|"qcm_multiple"|"multiline_text"|'
    '"table_fill"|"matching"|"manual_drawing",'
    '"choices":[str]?,"answer":{"type":"integer"|"decimal"|"rational"|"expression"|'
    '"text"|"choice"|"rubric"|"table"|"matching","value":...,"variable":str?,'
    '"correct":[int]?,"steps":[{"description":str,"expected_text":str,"points":int}]?,'
    '"lines":int?,"rows":int?,"cols":int?,"col_labels":[str]?,"row_labels":[str]?,'
    '"cells":[[{"type":str,"value":...}]]?,"left":[str]?,"right":[str]?,'
    '"pairs":[[int,int]]?},'
    '"figure":{...}?}]}'
)

_GEN_SYSTEM_TEMPLATE = (
    "Tu es un professeur agrégé de mathématiques, auteur reconnu de manuels de "
    "collège français. Tu crées des exercices IMPRIMÉS d'excellente qualité, "
    "utilisables sans aucune relecture : données cohérentes, calculs faisables à la "
    "main, résultats « propres » sauf si la compétence vise le contraire, énoncés "
    "autoportants et sans ambiguïté, vocabulaire exact du programme.\n\n"
    "VARIÉTÉ : les exercices demandés doivent porter sur des tâches DIFFÉRENTES de la "
    "même compétence (pas la même consigne avec d'autres nombres). Si count ≥ 2 et "
    "niveau ≥ 2, AU MOINS UN exercice est un problème contextualisé réaliste "
    "(kind=\"probleme\") : situation concrète crédible (cuisine, sport, bricolage, "
    "argent de poche...), données réalistes, question qui oblige à mobiliser la "
    "compétence.\n\n"
    + _RESPONSE_FORMAT_BLOCK
)

_GEOMETRY_RULES = (
    "GÉOMÉTRIE (impératif absolu, sauf format \"manual_drawing\") : l'élève ne trace, "
    "ne construit, ne dessine et ne place JAMAIS rien — sa copie est scannée, seuls du "
    "texte et des cases cochées sont lus. Les tâches possibles : lire/exploiter une "
    "figure fournie, calculer (longueur, aire, angle, périmètre), justifier une "
    "propriété en une ou deux phrases, reconnaître (QCM). Toute donnée géométrique "
    "utile doit figurer dans l'énoncé ET, si pertinent, sur la figure. N'utilise "
    "\"manual_drawing\" qu'en dernier recours, pour une construction qu'aucune "
    "reformulation en QCM/texte ne peut évaluer.\n\n"
)


def _generation_payload(db: Session, competency: Competency, level: int,
                        count: int, avoid: list[str]) -> tuple[str, dict]:
    fw_grade = _grade_of(db, competency)
    is_geometry = competency.domain_code in GEOMETRY_DOMAINS
    system = _GEN_SYSTEM_TEMPLATE.replace(
        "{geometry_rules}", _GEOMETRY_RULES if is_geometry else "")
    payload = {
        "grade": fw_grade,
        "competency_code": competency.code,
        "competency_label": competency.label,
        "chapter": competency.chapter_name,
        "domain": competency.domain_name,
        "program_objectives": _chapter_objectives(fw_grade, competency.chapter_name)[:12],
        "difficulty_level": level,
        "difficulty_name": LEVEL_SPECS[level][0],
        "difficulty_description": LEVEL_SPECS[level][1],
        "count": count,
        "avoid_similar_to": avoid[:8],
    }
    return system, payload


# ================================================================ moisson MathALÉA

def _mathalea_refs_for(db: Session, competency: Competency) -> list[str]:
    rows = (db.query(ExerciseCatalog)
            .join(ExerciseCompetency, ExerciseCompetency.exercise_id == ExerciseCatalog.id)
            .filter(ExerciseCompetency.competency_id == competency.id,
                    ExerciseCatalog.provider == "mathalea")
            .all())
    return [r.provider_ref.split(":", 1)[1] for r in rows]


def _harvest_mathalea(db: Session, competency: Competency, level: int,
                      need: int, existing_norms: set[str]) -> list[dict]:
    """Instancie des exercices MathALÉA rattachés à la compétence et les fait
    passer par les mêmes barrières (validation + vérification croisée).
    MathALÉA n'étant pas calibré 1-5, on ne moissonne que pour les niveaux 2-4."""
    if level in (1, 5) or need <= 0:
        return []
    from . import mathalea_client
    out = []
    refs = _mathalea_refs_for(db, competency)[:4]
    for i, ref in enumerate(refs):
        if len(out) >= need:
            break
        try:
            gen = mathalea_client.generate(ref, seed=1000 * level + i, db=db)
        except mathalea_client.MathaleaUnavailable:
            continue
        if gen["grading"].get("comparator") == "manual":
            continue  # sans réponse structurée : inutilisable en auto
        raw = {"statement": gen["statement"], "correction": gen["correction"],
               "response_type": gen["response_type"], "kind": "application"}
        expected, gpolicy = gen["expected"], gen["grading"]
        if not _check_text(raw["statement"], 15, 1200):
            continue
        normalized = _normalize_statement_for_dedup(raw["statement"])
        if normalized in existing_norms:
            continue
        candidate = {"statement": raw["statement"], "correction": raw["correction"],
                     "response_type": gen["response_type"], "expected": expected,
                     "grading": gpolicy, "figure_json": None, "kind": "application",
                     "_source": f"mathalea:{ref}",
                     "_provider_version": gen.get("provider_version", "?")}
        ok, verdict = _verify_with_claude(db, competency, level, candidate)
        if not ok:
            continue
        candidate["_verdict"] = verdict
        existing_norms.add(normalized)
        out.append(candidate)
    return out


# ================================================================ banque

def ensure_bank(db: Session, competency: Competency, level: int,
                min_variants: int | None = None,
                source: str = "auto") -> list[GeneratedExercise]:
    """Garantit min_variants exercices actifs pour (compétence, niveau).
    Mix : moisson MathALÉA puis génération DeepSeek, chaque exercice validé
    déterministiquement puis vérifié par Claude (avec une passe de réparation).

    `source="sesamaths"` délègue entièrement à services.sesamaths.ensure_bank
    (pool séparé, extraction du manuel scolaire du niveau de la compétence) —
    le reste de cette fonction (MathALÉA/DeepSeek, `source` "auto"/"mathalea")
    n'est pas affecté."""
    if source == "sesamaths":
        from . import sesamaths
        return sesamaths.ensure_bank(db, competency, level, min_variants)

    level = max(1, min(5, level))
    min_variants = min_variants or settings.exercise_variants_per_level

    rows = (db.query(GeneratedExercise)
            .filter_by(competency_id=competency.id, difficulty_level=level, status="active")
            .all())
    missing = min_variants - len(rows)
    if missing <= 0:
        return rows
    logger.info("Banque %s niveau %s : %s variante(s) en stock, %s à créer",
                competency.code, level, len(rows), missing)

    # normalisations existantes (toutes variantes/niveaux de la compétence)
    existing_norms = {
        _normalize_statement_for_dedup(ex.statement)
        for ex in db.query(GeneratedExercise)
        .filter_by(competency_id=competency.id, status="active").all()}

    added: list[GeneratedExercise] = []
    next_variant = len(rows)

    def _store(candidate: dict, source: str, verdict: dict) -> None:
        nonlocal next_variant
        row = GeneratedExercise(
            competency_id=competency.id, difficulty_level=level,
            variant=next_variant, statement=candidate["statement"],
            correction=candidate["correction"],
            response_type=candidate["response_type"],
            expected_json=candidate["expected"], grading_json=candidate["grading"],
            model=(source if source.startswith("mathalea")
                   else settings.deepseek_pro_model),
            prompt_version=PROMPT_VERSION, status="active",
            verifier_model=settings.claude_model, verifier_verdict_json=verdict,
            quality_json=verdict.get("scores") or {},
            figure_json=candidate.get("figure_json"),
            source="mathalea" if source.startswith("mathalea") else "deepseek",
            kind=candidate.get("kind", "application"))
        db.add(row)
        added.append(row)
        next_variant += 1

    # 1) moisson MathALÉA (déterministe, sûre) — au plus la moitié de la banque
    try:
        for cand in _harvest_mathalea(db, competency, level,
                                      min(missing, min_variants // 2), existing_norms):
            _store(cand, cand["_source"], cand.get("_verdict", {}))
    except Exception as e:
        logger.warning("Moisson MathALÉA %s niveau %s impossible : %s",
                       competency.code, level, e)
    missing = min_variants - len(rows) - len(added)
    if added:
        logger.info("MathALÉA : %s exercice(s) moissonné(s) pour %s niveau %s",
                    len(added), competency.code, level)

    # 2) génération DeepSeek (jusqu'à 3 lots)
    avoid = [mathrender.strip_math(ex.statement)[:120]
             for ex in rows + added][-8:]
    for gen_attempt in range(3):
        if missing <= 0:
            break
        system, payload = _generation_payload(db, competency, level,
                                              missing, avoid)
        logger.info("DeepSeek : génération de %s exercice(s) pour %s niveau %s "
                    "(lot %s/3)…", missing, competency.code, level, gen_attempt + 1)
        try:
            data = providers.deepseek_json(
                db, "exercise_generation", system, payload,
                max_tokens=6000, model=settings.deepseek_pro_model,
                correlation_id=f"exgen-{competency.code}-L{level}-att{gen_attempt}")
        except Exception as e:
            logger.warning("DeepSeek indisponible pour %s niveau %s : %s",
                           competency.code, level, e)
            if len(rows) + len(added) >= 1:
                break
            raise

        for raw in (data.get("exercises") or [])[:missing + 2]:
            if missing <= 0:
                break
            valid = _validate_exercise(raw, competency, db, existing_norms)
            if valid is None:
                logger.info("Exercice rejeté (validation déterministe) — %s niveau %s",
                            competency.code, level)
                continue
            is_good, verdict = _verify_with_claude(db, competency, level, valid)
            if not is_good:
                # une passe de réparation guidée par la critique
                logger.info("Vérification Claude négative — tentative de réparation "
                            "(%s niveau %s)", competency.code, level)
                fixed_raw = _repair_exercise(db, competency, level, valid, verdict)
                if fixed_raw is None:
                    continue
                existing_norms.discard(_normalize_statement_for_dedup(valid["statement"]))
                valid = _validate_exercise(fixed_raw, competency, db, existing_norms)
                if valid is None:
                    continue
                is_good, verdict = _verify_with_claude(db, competency, level, valid)
                if not is_good:
                    continue
                verdict = {**verdict, "repaired": True}
            _store(valid, "deepseek", verdict)
            missing -= 1
            avoid.append(mathrender.strip_math(valid["statement"])[:120])

    db.flush()
    if not rows and not added:
        raise ValueError(
            f"Aucun exercice n'a passé les contrôles qualité pour "
            f"{competency.code} niveau {level}")
    logger.info("Banque %s niveau %s prête : %s variante(s)",
                competency.code, level, len(rows) + len(added))
    return rows + added


def bank_rows_near_level(db: Session, competency: Competency, level: int,
                         source: str = "auto") -> tuple[list[GeneratedExercise], int]:
    """Comme pick_exercise, mais retourne toute la banque du niveau le plus
    proche disponible (pour une sélection en aval équilibrée par type de
    réponse, cf. services.distribution). `source` : voir ensure_bank."""
    for candidate in sorted(range(1, 6), key=lambda l: abs(l - level)):
        try:
            rows = ensure_bank(db, competency, candidate, source=source)
        except Exception as e:
            # Manuel Sésamath introuvable / extraction impossible : message clair
            # et actionnable — inutile d'essayer les autres niveaux (le PDF manque
            # quel que soit le niveau) et surtout PAS de repli silencieux sur une
            # banque inventée. On remonte l'erreur telle quelle.
            if source == "sesamaths":
                from .sesamaths import SesamathsExtractionError
                if isinstance(e, SesamathsExtractionError):
                    raise
            q = db.query(GeneratedExercise).filter_by(
                competency_id=competency.id, difficulty_level=candidate, status="active")
            if source == "sesamaths":
                from .sesamaths import SOURCE_POOL
                q = q.filter(GeneratedExercise.source.in_(SOURCE_POOL))
            rows = q.all()
        if rows:
            return rows, candidate
    raise ValueError(f"Aucun exercice disponible pour {competency.code}")


def pick_exercise(db: Session, competency: Competency, level: int,
                  seed: int) -> GeneratedExercise:
    """Choisit un exercice de banque, en générant si nécessaire."""
    rows, _ = bank_rows_near_level(db, competency, level)
    return rows[seed % len(rows)]


def ensure_catalog_ref(db: Session, competency: Competency) -> ExerciseCatalog:
    """Get-or-create l'entrée catalogue « exercice IA » d'une compétence —
    seul lien encore nécessaire vers exercise_catalog/copy_items, les
    exercices concrets restant en banque generated_exercises (compétence ×
    niveau)."""
    ref = f"deepseek:{competency.id}"
    row = db.query(ExerciseCatalog).filter_by(provider="deepseek", provider_ref=ref).first()
    if row:
        return row
    from ..models import CompetencyFramework
    fw = db.get(CompetencyFramework, competency.framework_id)
    row = ExerciseCatalog(
        provider="deepseek", provider_ref=ref,
        title=f"[IA] {competency.label}", grade_level=fw.grade_level if fw else "5e",
        difficulty=5, response_type="short_text", automation_tier="auto")
    db.add(row)
    db.flush()
    db.add(ExerciseCompetency(exercise_id=row.id, competency_id=competency.id,
                              weight=1.0, evidence_strength=1.0))
    return row


# ================================================================ rappels de leçon

_LESSON_SYSTEM = (
    "Tu écris un rappel de leçon de mathématiques pour un élève de collège FRAGILE, "
    "qui a besoin qu'on lui redonne confiance. Exigences :\n"
    "- français très simple : phrases courtes (max 15 mots), vocabulaire concret, "
    "tutoiement, aucun jargon non défini ;\n"
    "- structure stricte : l'essentiel (1-2 phrases : la définition ou la règle), "
    "la méthode (2 à 4 étapes numérotées, chacune une action concrète), un exemple "
    "entièrement résolu étape par étape avec des nombres très simples ;\n"
    "- encarts (0 à 3, chacun typé) : type \"conseil\" pour un moyen "
    "mnémotechnique ou une vérification rapide ; type \"attention\" pour un "
    "piège fréquent ou une erreur classique à ne pas commettre. Chaque encart "
    "est court (une phrase, deux maximum) et autonome ;\n"
    "- si une figure aide à comprendre (géométrie, droite graduée), fournis-la ;\n"
    + _GEN_FORMAT_RULES + "\n"
    "Réponds en JSON strict : {\"title\": str (court), \"essentiel\": str, "
    "\"methode\": [str], \"exemple\": {\"enonce\": str, \"etapes\": [str], "
    "\"resultat\": str}, "
    "\"encarts\": [{\"type\": \"conseil\"|\"attention\", \"texte\": str}], "
    "\"figure\": {\"type\": ..., \"params\": {...}}?}"
)

_LESSON_VERIFY_SYSTEM = (
    "Tu vérifies un rappel de leçon destiné à un élève fragile de collège. Juge : "
    "(a) justesse mathématique de la règle ET de l'exemple (refais les calculs), "
    "(b) simplicité réelle (phrases courtes, pas de jargon), "
    "(c) la méthode est-elle applicable telle quelle par l'élève, "
    "(d) l'exemple illustre-t-il exactement la compétence, "
    "(e) les encarts \"attention\" pointent-ils un piège réel et pertinent. "
    "JSON strict : {\"valide\": bool, \"scores\": {\"justesse\": 0-5, "
    "\"simplicite\": 0-5, \"utilite\": 0-5}, \"problemes\": [str], \"reparable\": bool}."
)

_ENCART_TYPES = ("conseil", "attention")


def _validate_lesson_blocks(data: dict) -> dict | None:
    """Contrôle déterministe de la structure et du LaTeX d'un rappel."""
    if not isinstance(data, dict):
        return None
    title = str(data.get("title", "")).strip()[:120]
    essentiel = str(data.get("essentiel", "")).strip()
    methode = [str(s).strip() for s in (data.get("methode") or []) if str(s).strip()]
    exemple = data.get("exemple") or {}
    enonce = str(exemple.get("enonce", "")).strip()
    etapes = [str(s).strip() for s in (exemple.get("etapes") or []) if str(s).strip()]
    resultat = str(exemple.get("resultat", "")).strip()

    if not title or not _check_text(essentiel, 10, 500):
        return None
    if not (2 <= len(methode) <= 4) or not all(_check_text(s, 5, 300) for s in methode):
        return None
    if not _check_text(enonce, 5, 400) or not _check_text(resultat, 1, 300):
        return None
    if not (1 <= len(etapes) <= 5) or not all(_check_text(s, 3, 300) for s in etapes):
        return None

    # encarts typés (conseil / attention) — repli sur l'ancien champ "astuce"
    raw_encarts = data.get("encarts")
    if not raw_encarts and data.get("astuce"):
        raw_encarts = [{"type": "conseil", "texte": data["astuce"]}]
    encarts = []
    for enc in (raw_encarts or [])[:3]:
        if not isinstance(enc, dict):
            continue
        etype = enc.get("type") if enc.get("type") in _ENCART_TYPES else "conseil"
        texte = str(enc.get("texte", "")).strip()
        if _check_text(texte, 5, 300):
            encarts.append({"type": etype, "texte": texte})

    figure = figures.validate_figure(data.get("figure"))
    return {"title": title, "essentiel": essentiel, "methode": methode,
            "exemple": {"enonce": enonce, "etapes": etapes, "resultat": resultat},
            "encarts": encarts, "figure": figure}


def ensure_lesson(db: Session, competency: Competency, level: int) -> LessonSnippet:
    """Rappel de leçon structuré pour élève fragile : génération DeepSeek,
    validation déterministe, vérification Claude, une passe de réparation.
    Un rappel par compétence × tranche de niveau (1-3 / 4-5)."""
    lo, hi = (1, 3) if level <= 3 else (4, 5)
    row = (db.query(LessonSnippet)
           .filter_by(competency_id=competency.id, level_min=lo, level_max=hi,
                      status="active")
           .first())
    if row:
        return row

    fw_grade = _grade_of(db, competency)
    payload = {
        "competency_code": competency.code,
        "competency_label": competency.label,
        "chapter": competency.chapter_name,
        "grade": fw_grade,
        "level_range": f"{lo}-{hi}",
        "is_geometry": competency.domain_code in GEOMETRY_DOMAINS,
    }

    blocks = None
    verdict: dict = {}
    last_error = "génération impossible"
    for attempt in range(2):  # 1 génération + 1 réparation éventuelle
        op = "lesson_snippet" if attempt == 0 else "lesson_repair"
        try:
            data = providers.deepseek_json(
                db, op, _LESSON_SYSTEM, payload,
                max_tokens=1800, model=settings.deepseek_pro_model,
                correlation_id=f"lesson-{competency.code}-a{attempt}")
        except Exception as e:
            raise ValueError(f"Échec génération rappel de leçon : {e}")

        candidate = _validate_lesson_blocks(data)
        if candidate is None:
            last_error = "structure ou LaTeX invalide"
            continue

        try:
            verdict = providers.claude_json(
                db, "lesson_verification", _LESSON_VERIFY_SYSTEM,
                {"competency": competency.label, "grade": fw_grade, **candidate},
                max_tokens=500, correlation_id=f"lessonverif-{competency.code}")
        except Exception as e:
            verdict = {"valide": False, "problemes": [f"vérification indisponible : {e}"],
                       "reparable": False}

        if verdict.get("valide", False):
            blocks = candidate
            break
        last_error = "; ".join(verdict.get("problemes") or ["refus du vérificateur"])
        if not verdict.get("reparable", True):
            break
        # nourrir la passe suivante avec la critique
        payload = {**payload, "previous_attempt": candidate,
                   "problemes_a_corriger": (verdict.get("problemes") or [])[:6]}

    if blocks is None:
        raise ValueError(f"Rappel {competency.code} refusé : {last_error}")

    # champs plats conservés pour compatibilité (anciens PDF/écrans)
    content = blocks["essentiel"] + " " + " ".join(
        f"{i + 1}. {s}" for i, s in enumerate(blocks["methode"]))
    example = (blocks["exemple"]["enonce"] + " " +
               " ".join(blocks["exemple"]["etapes"]) + " " +
               blocks["exemple"]["resultat"]).strip()

    row = LessonSnippet(
        competency_id=competency.id, level_min=lo, level_max=hi,
        title=blocks["title"], content_latex=content, example_latex=example,
        blocks_json=blocks, figure_json=blocks.get("figure"),
        version=PROMPT_VERSION, validated=True,
        verifier_model=settings.claude_model, verifier_verdict_json=verdict,
        status="active")
    db.add(row)
    db.flush()
    return row


__all__ = ["ensure_bank", "pick_exercise", "ensure_lesson",
           "student_level_to_difficulty", "PROMPT_VERSION"]

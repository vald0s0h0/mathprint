"""Validation déterministe des exercices + banque, partagées par les DEUX
pipelines qui produisent des exercices :
  - services.sesamaths : adaptation des blocs OCR Mistral d'un manuel Sésamath
    (`source="sesamaths"`) ;
  - services.gemini_gen : création d'exercices par Gemini, en s'appuyant sur
    ces mêmes blocs OCR comme référence de programme/niveau (`source="gemini"`).
Toute autre source lève une erreur claire — pas de repli silencieux sur du
contenu inventé hors pipeline assumée (la génération DeepSeek/MathALÉA et
celle des rappels de leçon ont été retirées le 16/07).

Ce qui vit ici, et que les deux pipelines partagent SANS jamais le réécrire :
  - VALIDATION DÉTERMINISTE (_validate_exercise/_validate_cell) : LaTeX $...$
    entièrement validé (liste blanche + rendu d'essai), figures rendues à
    blanc, QCM cohérents (distracteurs uniques, bonne réponse présente),
    géométrie sans verbe de construction, réponse de référence acceptée par
    le moteur de correction déterministe, anti-doublon par compétence
    (_dedup_key, pas le seul énoncé — cf. commentaire dédié).
  - CONTRAT DE FORMAT (`format_contract`, assemblé depuis _FORMAT_MENU/
    _FIGURE_RULES/_GEN_FORMAT_RULES/_JSON_CONTRACT) : le bloc de prompt qui
    décrit le JSON attendu. Seule l'INTRO diffère d'une pipeline à l'autre
    (_ADAPT_FORMAT_INTRO : adapter un exercice de manuel sans jamais en
    omettre ; _GEMINI_FORMAT_INTRO : inventer, sans droit aux formats non
    corrigeables). Le reste est commun DÉLIBÉRÉMENT : un prompt qui décrirait
    le contrat autrement que _validate_exercise produit des rejets muets.

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

import hashlib
import json
import logging
import re

from sqlalchemy.orm import Session

from ..models import (
    Competency, ExerciseCatalog, ExerciseCompetency, GeneratedExercise,
    LessonSnippet,
)
from . import figures, grading, mathrender

logger = logging.getLogger(__name__)

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

VALID_RESPONSE_TYPES = {"qcm_single", "qcm_multiple", "short_text", "multi_blank",
                        "multiline_text", "table_fill", "matching", "manual_drawing"}

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


def _dedup_key(statement: str, expected: dict | None, choices: list | None = None) -> str:
    """Identité anti-doublon d'un exercice. Le SEUL énoncé normalisé ne
    suffit pas pour table_fill/multi_blank : "statement" n'y porte que la
    consigne commune, souvent générique ("Calcule."), IDENTIQUE pour des
    exercices dont les cellules/nombres sont totalement différents — deux
    exercices réels distincts finissaient donc pris pour des doublons, et le
    second silencieusement rejeté (pool réel bien plus petit que ce qui a été
    extrait). On ajoute donc le contenu qui distingue réellement deux
    exercices (cellules, choix, paires, valeur) au hash d'identité.

    Un QCM est distingué par ses CHOIX (texte), pas seulement l'INDICE de la
    bonne réponse (expected["correct"]) : deux QCM différents ont ~1 chance
    sur 2-4 de partager le même indice correct, ce qui les ferait passer à
    tort pour des doublons.

    "manual_drawing" (et tout type sans réponse structurée) n'a AUCUN contenu
    distinctif dans `expected` : replier sur le SEUL énoncé normalisé (base,
    chiffres effacés) confondait alors deux exercices bien réels dont
    l'énoncé a juste la même trame de phrase (ex. « Trace le triangle ABC de
    côté 5 cm » / « … de côté 9 cm ») — cause identifiée d'un pool réduit à
    un seul exercice survivant. Le repli utilise donc l'énoncé COMPLET (pas
    la base sans chiffres), jamais juste la trame."""
    base = _normalize_statement_for_dedup(statement)
    if not expected:
        return f"{base}|{hashlib.sha256(statement.encode('utf-8')).hexdigest()[:16]}"
    etype = expected.get("type")
    if etype == "table":  # table_fill ET multi_blank (même forme interne)
        extra = expected.get("cells")
    elif etype == "choice":
        extra = (choices, expected.get("correct"))
    elif etype == "matching":
        extra = expected.get("pairs")
    elif etype == "rubric":
        extra = [s.get("expected_text") for s in (expected.get("steps") or [])]
    else:
        extra = expected.get("value")
    if extra is None:
        return f"{base}|{hashlib.sha256(statement.encode('utf-8')).hexdigest()[:16]}"
    digest = hashlib.sha256(
        json.dumps(extra, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return f"{base}|{digest}"


_LEAKED_MARKER_RE = re.compile(r"\{\{(?:line\d+|check|dot)\}\}")


def _leaked_marker(text: str) -> str | None:
    """Marqueur d'extraction Sésamaths (cf. services.sesamaths) resté non
    transformé par l'adaptateur : {{lineN}}/{{check}}/{{dot}} doivent
    toujours être convertis en response_type/answer app (multiline_text,
    qcm, matching) et retirés du texte final — seul {{blank}} peut y
    subsister. Le rendu PDF ne connaît que {{blank}} ; un marqueur oublié
    s'afficherait comme texte parasite sur la copie imprimée."""
    m = _LEAKED_MARKER_RE.search(text)
    return m.group(0) if m else None


def _check_text(text: str, min_len: int = 1, max_len: int = 1200) -> bool:
    """Texte balisé valide : longueur, LaTeX des spans, pas de LaTeX hors spans."""
    if not min_len <= len(text) <= max_len:
        return False
    if _has_raw_latex_outside_math(text):
        return False
    if _leaked_marker(text):
        return False
    return mathrender.has_valid_math(text)


def _text_reject_reason(text: str, min_len: int, max_len: int) -> str | None:
    """Pourquoi _check_text refuse ce texte (None = accepté). Diagnostic
    uniquement : _check_text reste la source de vérité."""
    if not min_len <= len(text) <= max_len:
        return f"longueur {len(text)} hors [{min_len}, {max_len}]"
    bad = [c for c, is_math in mathrender.split_math_spans(text)
           if not is_math and "\\" in c]
    if bad:
        return f"commande LaTeX hors $...$ : {bad[0][:60]!r}"
    if (leaked := _leaked_marker(text)):
        return f"marqueur d'extraction non transformé par l'adaptateur : {leaked!r}"
    for content, is_math in mathrender.split_math_spans(text):
        if is_math and mathrender.sanitize_latex(content) is None:
            return f"span LaTeX refusé : ${content[:60]}$"
    return None


def diagnose_rejection(raw: dict, competency: Competency) -> str:
    """Explique en clair pourquoi _validate_exercise a refusé `raw`.

    Rejoue les mêmes contrôles dans le même ordre et renvoie le PREMIER qui
    échoue. Appelé uniquement sur le chemin d'échec (jamais en régime normal),
    pour que les logs disent « pourquoi » au lieu d'un simple compte de rejets.
    Ne remplace pas la validation : si tout passe ici, le refus vient d'un
    contrôle spécifique au type de réponse (auto-vérification, doublon…)."""
    if not isinstance(raw, dict):
        return f"pas un objet JSON ({type(raw).__name__})"
    rtype = raw.get("response_type", "short_text")
    statement = str(raw.get("statement", "")).strip()
    correction = str(raw.get("correction", "")).strip()
    # table_fill : le détail vit dans row_labels/col_labels, "statement" ne
    # porte que la consigne commune (souvent très courte, ex. "Calcule.").
    statement_min = 3 if rtype == "table_fill" else 15
    if (r := _text_reject_reason(statement, statement_min, 1200)):
        return f"énoncé invalide : {r}"
    if (r := _text_reject_reason(correction, 5, 1500)):
        return f"correction invalide : {r}"
    if rtype not in VALID_RESPONSE_TYPES:
        return f"response_type inconnu : {rtype!r}"
    if (competency.domain_code in GEOMETRY_DOMAINS and rtype != "manual_drawing"
            and _is_geometry_verb(statement)):
        return "verbe de construction géométrique hors manual_drawing"
    if raw.get("figure") is not None and figures.validate_figure(raw.get("figure")) is None:
        return f"figure invalide : {str(raw.get('figure'))[:80]}"
    answer = raw.get("answer") or {}
    if not answer:
        return "champ 'answer' absent ou vide"
    if not answer.get("type"):
        return f"answer.type absent : {str(answer)[:80]}"
    if rtype == "table_fill":
        return _diagnose_table_fill(answer)
    if rtype == "multi_blank":
        return _diagnose_multi_blank(statement, answer)
    if rtype in ("qcm_single", "qcm_multiple"):
        return _diagnose_qcm(raw, answer, rtype)
    if rtype == "matching":
        return _diagnose_matching(answer)
    if rtype in ("short_text", "multiline_text"):
        return _diagnose_short_text(answer)
    return (f"contrôle spécifique à '{rtype}' (answer.type={answer.get('type')!r}) — "
            f"auto-vérification, doublon ou incohérence réponse/énoncé")


def _diagnose_short_text(answer: dict) -> str:
    atype = answer.get("type")
    if atype == "integer":
        try:
            int(answer["value"])
        except (KeyError, TypeError, ValueError):
            return "short_text : answer.value absent ou non entier"
    elif atype in ("decimal", "number"):
        try:
            float(str(answer["value"]).replace(",", "."))
        except (KeyError, TypeError, ValueError):
            return "short_text : answer.value absent ou non décimal"
    elif atype == "rational":
        v = answer.get("value")
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            return "short_text : answer.value doit être [num, den]"
        try:
            num, den = int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return "short_text : num/den non entiers"
        if den == 0:
            return "short_text : dénominateur nul"
    elif atype == "expression":
        val = str(answer.get("value", "")).strip()
        if not val or len(val) > 120:
            return f"short_text : expression vide ou trop longue ({len(val)} car., max 120)"
    elif atype == "text":
        val = str(answer.get("value", "")).strip()
        if not val or len(val) > 80:
            return f"short_text : texte vide ou trop long ({len(val)} car., max 80)"
    elif atype == "rubric":
        steps = answer.get("steps", [])
        if not isinstance(steps, list) or not (2 <= len(steps) <= 6):
            got = len(steps) if isinstance(steps, list) else 0
            return f"rubric : {got} étape(s) hors bornes [2,6]"
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return f"rubric : étape #{i} n'est pas un objet"
            desc = str(step.get("description", "")).strip()
            expected_text = str(step.get("expected_text", "")).strip()
            if not desc or not expected_text:
                return f"rubric : étape #{i} description/expected_text manquant"
            if (r := _text_reject_reason(expected_text, 1, 400)):
                return f"rubric : étape #{i} expected_text invalide : {r}"
    else:
        return f"short_text : answer.type inconnu {atype!r}"
    return ("short_text : auto-vérification échouée (incohérence réponse/énoncé — "
            "vérifier la notation LaTeX de answer.value, ex. \\dfrac ou {,} décimal)")


def _diagnose_qcm(raw: dict, answer: dict, rtype: str) -> str:
    choices = [str(c).strip() for c in (raw.get("choices") or [])]
    if not (2 <= len(choices) <= 8):
        return f"qcm : {len(choices)} choix hors bornes [2,8]"
    if len({mathrender.strip_math(c).lower() for c in choices}) != len(choices):
        return "qcm : distracteurs dupliqués (identiques une fois le LaTeX normalisé)"
    for c in choices:
        if not _check_text(c, 1, 120):
            return f"qcm : choix invalide : {c[:60]!r}"
    correct = answer.get("correct", [])
    if not (isinstance(correct, list) and correct
            and all(isinstance(i, int) and 0 <= i < len(choices) for i in correct)):
        return f"qcm : answer.correct invalide (attendu une liste d'indices 0-{len(choices) - 1})"
    correct = sorted(set(correct))
    if rtype == "qcm_single" and len(correct) != 1:
        return "qcm_single : answer.correct doit contenir exactement un indice"
    if len(correct) >= len(choices):
        return "qcm : tous les choix sont corrects, n'évalue rien"
    return "qcm : auto-vérification échouée (incohérence réponse attendue)"


def _diagnose_matching(answer: dict) -> str:
    left = [str(c).strip() for c in (answer.get("left") or [])]
    right = [str(c).strip() for c in (answer.get("right") or [])]
    if not (2 <= len(left) <= 6 and 2 <= len(right) <= 6):
        return f"matching : left={len(left)}/right={len(right)} hors bornes [2,6]"
    for c in left + right:
        if not _check_text(c, 1, 80):
            return f"matching : élément invalide : {c[:60]!r}"
    pairs = answer.get("pairs")
    if not (isinstance(pairs, list) and pairs
            and all(isinstance(p, (list, tuple)) and len(p) == 2
                   and isinstance(p[0], int) and isinstance(p[1], int)
                   and 0 <= p[0] < len(left) and 0 <= p[1] < len(right)
                   for p in pairs)):
        return "matching : answer.pairs invalide (indices hors bornes ou mal formés)"
    if (len({int(p[0]) for p in pairs}) != len(pairs)
            or len({int(p[1]) for p in pairs}) != len(pairs)):
        return "matching : un élément est utilisé dans plusieurs paires"
    return "matching : auto-vérification échouée (incohérence réponse attendue)"


def _diagnose_table_fill(answer: dict) -> str:
    """Diagnostic détaillé pour table_fill : rejoue chaque sous-contrôle de
    _validate_exercise pour dire PRÉCISÉMENT lequel échoue."""
    atype = answer.get("type")
    if atype != "table":
        return f"table_fill : answer.type={atype!r} (attendu 'table')"
    try:
        rows, cols = int(answer.get("rows")), int(answer.get("cols"))
    except (TypeError, ValueError):
        return "table_fill : rows/cols manquant ou non entier"
    if not (2 <= rows <= 12 and 1 <= cols <= 6):
        return f"table_fill : rows={rows}/cols={cols} hors bornes [2,12]x[1,6]"
    cells = answer.get("cells")
    if not (isinstance(cells, list) and len(cells) == rows
            and all(isinstance(r, list) and len(r) == cols for r in cells)):
        got_rows = len(cells) if isinstance(cells, list) else None
        return f"table_fill : cells mal formé (attendu {rows}x{cols}, reçu {got_rows} ligne(s))"
    col_labels = answer.get("col_labels")
    row_labels = answer.get("row_labels")
    if col_labels is not None and (not isinstance(col_labels, list) or len(col_labels) != cols):
        return f"table_fill : col_labels de longueur incohérente (attendu {cols})"
    if row_labels is not None and (not isinstance(row_labels, list) or len(row_labels) != rows):
        return f"table_fill : row_labels de longueur incohérente (attendu {rows})"
    for ri, row in enumerate(cells):
        for ci, cell in enumerate(row):
            if not isinstance(cell, dict) or _validate_cell(cell) is None:
                return f"table_fill : cellule [{ri}][{ci}] invalide : {str(cell)[:60]}"
    fillable = [c for r in cells for c in r if not c.get("given")]
    if not fillable:
        return "table_fill : tableau entièrement 'given', rien à noter"
    return "table_fill : auto-vérification échouée (incohérence réponse attendue)"


def _diagnose_multi_blank(statement: str, answer: dict) -> str:
    atype = answer.get("type")
    if atype != "blanks":
        return f"multi_blank : answer.type={atype!r} (attendu 'blanks')"
    values = answer.get("values")
    n_blanks = statement.count("{{blank}}")
    if not isinstance(values, list):
        return "multi_blank : answer.values doit être une liste"
    if len(values) != n_blanks:
        return (f"multi_blank : {len(values)} valeur(s) pour {n_blanks} "
                "occurrence(s) de {{blank}} dans le statement")
    if len(values) < 2:
        return "multi_blank : au moins 2 cases attendues (sinon utiliser short_text)"
    for i, v in enumerate(values):
        if not isinstance(v, dict) or _validate_cell(v) is None:
            return f"multi_blank : case #{i} invalide : {str(v)[:60]}"
    return "multi_blank : auto-vérification échouée (incohérence réponse attendue)"


def _validate_exercise(raw: dict, competency: Competency, db: Session,
                       existing_norms: set[str]) -> dict | None:
    """Valide un exercice candidat. Retourne le contrat interne ou None."""
    if not isinstance(raw, dict):
        return None
    rtype = raw.get("response_type", "short_text")
    if rtype not in VALID_RESPONSE_TYPES:
        return None
    statement = str(raw.get("statement", "")).strip()
    correction = str(raw.get("correction", "")).strip()
    # table_fill : le détail vit dans row_labels/col_labels, "statement" ne
    # porte que la consigne commune (souvent très courte, ex. "Calcule.").
    statement_min = 3 if rtype == "table_fill" else 15
    if not _check_text(statement, statement_min, 1200) or not _check_text(correction, 5, 1500):
        return None

    is_geometry = competency.domain_code in GEOMETRY_DOMAINS
    # seul manual_drawing autorise les verbes de construction (l'élève y
    # dessine réellement) ; tout autre format géométrique doit s'en passer
    if is_geometry and rtype != "manual_drawing" and _is_geometry_verb(statement):
        return None

    kind = raw.get("kind") if raw.get("kind") in ("application", "probleme") else "application"

    # figure optionnelle : validée par rendu à blanc, sinon abandonnée
    figure_json = figures.validate_figure(raw.get("figure"))

    answer = raw.get("answer") or {}
    atype = answer.get("type")

    def _contract(expected, gpolicy, rtype, choices=None):
        # anti-doublon (le set est maintenu par l'appelant pour couvrir le lot
        # en cours) : calculé ICI, une fois `expected` connu, pas sur le seul
        # statement — cf. _dedup_key.
        key = _dedup_key(statement, expected, choices)
        if key in existing_norms:
            return None
        existing_norms.add(key)
        return {"statement": statement, "correction": correction,
                "response_type": rtype, "expected": expected, "grading": gpolicy,
                "figure_json": figure_json, "kind": kind}

    # ---------------- QCM ----------------
    if rtype in ("qcm_single", "qcm_multiple"):
        choices = [str(c).strip() for c in (raw.get("choices") or [])]
        correct = answer.get("correct", [])
        # 2 choix minimum : couvre les questions Vrai/Faux, très fréquentes
        # dans les manuels, auparavant rejetées d'office (seuil historique 3).
        if not (2 <= len(choices) <= 8):
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
        return _contract(expected, gpolicy, rtype, choices=choices)

    # ---------------- tableau à remplir ----------------
    if rtype == "table_fill":
        if atype != "table":
            return None
        try:
            rows, cols = int(answer.get("rows")), int(answer.get("cols"))
        except (TypeError, ValueError):
            return None
        # jusqu'à 12 lignes : un exercice de manuel a couramment 10 sous-questions
        # (a. à j.) qui doivent rester UN seul exercice, une ligne par question.
        # cols=1 est autorisé : une phrase à trou par ligne (row_label = la
        # phrase, cellule = le trou), pour les séries de sous-questions a./b./c.
        if not (2 <= rows <= 12 and 1 <= cols <= 6):
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
        # "given" : cellule déjà imprimée dans le manuel (calcul donné), pas à
        # noter — seules les cellules à compléter par l'élève comptent.
        fillable = [c for r in validated_cells for c in r if not c.get("given")]
        if not fillable:
            return None  # un tableau entièrement "given" n'a rien à faire remplir
        expected = {"type": "table", "rows": rows, "cols": cols, "cells": validated_cells}
        gpolicy = {"max_score": len(fillable), "comparator": "table_cells",
                  "cells": validated_cells,
                  "col_labels": [str(c) for c in col_labels] if col_labels else None,
                  "row_labels": [str(r) for r in row_labels] if row_labels else None}
        # grade(table_cells) attend un cell_texts À PLAT, une entrée par cellule
        # NON "given", dans l'ordre ligne par ligne — cf. grading._grade table_cells
        reference = [_cell_reference_text(c) for r in validated_cells for c in r
                     if not c.get("given")]
        verdict = grading.grade(expected, gpolicy, "", 0.99, cell_texts=reference)
        if verdict["score"] < gpolicy["max_score"]:
            return None
        return _contract(expected, gpolicy, rtype)

    # ---------------- plusieurs cases indépendantes hors tableau ----------------
    if rtype == "multi_blank":
        if atype != "blanks":
            return None
        values = answer.get("values")
        n_blanks = statement.count("{{blank}}")
        # au moins 2 cases (sinon c'est un short_text) ; une valeur par
        # occurrence de {{blank}}, dans l'ordre d'apparition dans le statement
        if not (isinstance(values, list) and 2 <= len(values) == n_blanks):
            return None
        validated_values = []
        for v in values:
            if not isinstance(v, dict):
                return None
            vval = _validate_cell(v)
            if vval is None:
                return None
            vval.pop("given", None)  # non pertinent : une case inline est toujours à remplir
            validated_values.append(vval)
        expected = {"type": "table", "rows": 1, "cols": len(validated_values),
                   "cells": [validated_values]}
        gpolicy = {"max_score": len(validated_values), "comparator": "table_cells",
                  "cells": [validated_values], "col_labels": None, "row_labels": None}
        reference = [_cell_reference_text(c) for c in validated_values]
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
        # 2 paires minimum (seuil historique 3, rejetait les petites séries
        # d'association à 2 éléments)
        if not (2 <= len(left) <= 6 and 2 <= len(right) <= 6):
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
    """Valide une cellule de table_fill/multi_blank : {"type": "integer"|
    "decimal"|"rational"|"expression"|"text", "value": ..., "given": bool?}.
    "given"=true : valeur déjà imprimée dans le manuel (non éditable, non
    notée), toute autre cellule est à remplir."""
    ctype = cell.get("type")
    if ctype == "integer":
        try:
            v = {"type": "integer", "value": int(cell["value"])}
        except (KeyError, TypeError, ValueError):
            return None
    elif ctype in ("decimal", "number"):
        try:
            v = {"type": "decimal",
                 "value": float(str(cell["value"]).replace(",", "."))}
        except (KeyError, TypeError, ValueError):
            return None
    elif ctype == "rational":
        val = cell.get("value")
        if not (isinstance(val, (list, tuple)) and len(val) == 2):
            return None
        try:
            num, den = int(val[0]), int(val[1])
        except (TypeError, ValueError):
            return None
        if den == 0:
            return None
        v = {"type": "rational", "value": [num, den]}
    elif ctype == "expression":
        val = str(cell.get("value", "")).strip()
        if not val or len(val) > 120:
            return None
        v = {"type": "expression", "value": val}
    elif ctype == "text":
        val = str(cell.get("value", "")).strip()
        if not val or len(val) > 40:
            return None
        v = {"type": "text", "value": val}
    else:
        return None
    if cell.get("given"):
        v["given"] = True
    return v


def _cell_reference_text(cell: dict) -> str:
    if cell["type"] == "rational":
        return f"{cell['value'][0]}/{cell['value'][1]}"
    return str(cell["value"])


def _reference_text(expected: dict) -> str:
    t = expected["type"]
    if t == "rational":
        return f"{expected['value'][0]}/{expected['value'][1]}"
    if t in ("expression", "text"):
        return str(expected["value"])
    return str(expected["value"])


# ================================================================ prompt de génération

_GEN_FORMAT_RULES = (
    "RÈGLES DE FORMAT (obligatoires) : tout objet mathématique (nombre en écriture "
    "fractionnaire, expression, égalité, unité collée à une valeur) est balisé $...$ "
    "en LaTeX. Commandes autorisées UNIQUEMENT : \\dfrac \\frac \\sqrt \\times \\div "
    "\\cdot \\pm \\leq \\geq \\neq \\approx \\pi \\text{...} \\% ^ _ ( ) [ ] { } "
    "\\rightarrow \\leftrightarrow (association/correspondance). "
    "Notation française : virgule décimale ($3{,}5$), unités en \\text ($7{,}5\\ \\text{cm}$). "
    "JAMAIS de LaTeX hors des bornes $...$, jamais de \\\\ ni d'environnements. "
    "Les nombres simples isolés dans une phrase (« 3 crayons ») restent en texte."
)

# Contrat JSON (menu des formats de réponse + figures + schéma de sortie),
# consommé par _validate_exercise et partagé par TOUTES les pipelines qui
# produisent des exercices : l'adaptateur Sésamaths (services.sesamaths, blocs
# OCR -> contrat app) et la création Gemini (services.gemini_gen, invention
# pure). Une seule définition, sinon un prompt dérive du validateur et les
# exercices sont rejetés sans raison visible (cf. incident table_fill 16/07).
# Seule l'INTRO change d'une pipeline à l'autre (adapter vs inventer) : elle
# est passée en paramètre à `format_contract`.
_ADAPT_FORMAT_INTRO = (
    "CHOIX DU FORMAT DE RÉPONSE — RÈGLE ABSOLUE : N'OMETS JAMAIS UN EXERCICE. "
    "Chaque exercice DOIT être renvoyé, quel que soit son énoncé d'origine — la "
    "plateforme n'imprime QUE l'un des 8 formats ci-dessous, jamais la mise en "
    "page du manuel/PDF d'origine. Priorité au format le PLUS automatisable, "
    "l'intervention humaine à la correction doit rester exceptionnelle — "
    "REFORMULE TOUJOURS la tâche pour qu'elle rentre dans l'un des formats 1 à "
    "7 ci-dessous, dans cet ordre de préférence. C'est SEULEMENT si AUCUNE "
    "reformulation n'est possible dans AUCUN de ces formats que tu utilises le "
    "format 8 (\"manual_drawing\") — qui accepte n'importe quel exercice sans "
    "exception, quel que soit son domaine (pas seulement la géométrie) "
    "puisqu'il ne demande aucune réponse structurée. Il n'existe donc JAMAIS "
    "de raison légitime d'omettre un exercice : au pire, utilise "
    "\"manual_drawing\".\n"
)

# Intro de la pipeline Gemini : on n'adapte pas un exercice existant, on
# l'invente — le modèle choisit donc son format AVANT de rédiger, et n'a
# jamais l'excuse du « format d'origine » pour tomber sur un dernier recours
# non corrigeable automatiquement (formats 7 et 8, refusés par
# gemini_gen._reject_reason).
_GEMINI_FORMAT_INTRO = (
    "CHOIX DU FORMAT DE RÉPONSE : tu INVENTES chaque exercice — choisis donc "
    "TOUJOURS d'abord ce que l'élève devra écrire ou cocher, puis rédige "
    "l'énoncé autour. Les 8 formats ci-dessous sont les SEULS que la "
    "plateforme sait imprimer et corriger, mais dans cette pipeline les "
    "formats 7 (\"matching\") et 8 (\"manual_drawing\") sont INTERDITS : leur "
    "correction n'est pas automatisable, et comme c'est toi qui inventes "
    "l'exercice, tu peux toujours en concevoir un qui rentre dans les formats "
    "1 à 6. Un exercice renvoyé dans un format interdit est rejeté. "
    "N'invente jamais un exercice qui aurait besoin d'une figure (n'utilise "
    "pas le champ \"figure\") et ignore le champ \"source_blocks\", réservé à "
    "une autre pipeline.\n"
)

_FORMAT_MENU = (
    "1. QCM UNIQUE / QCM MULTIPLE (\"qcm_single\"/\"qcm_multiple\") : "
    "reconnaissance, propriété, lecture de figure. 2 à 8 choix (2 pour un "
    "Vrai/Faux : choices=[\"Vrai\",\"Faux\"]), distracteurs = erreurs "
    "TYPIQUES d'élèves (erreur de signe, de priorité, confusion "
    "périmètre/aire...), une seule formulation possible de la bonne réponse ; "
    "PRÉFÈRE ce format à chaque fois qu'une tâche de reconnaissance/classement "
    "le permet, quitte à transformer une question ouverte en QCM à choix "
    "nombreux — un exercice « Vrai ou Faux ? » du manuel devient un "
    "qcm_single à 2 choix.\n"
    "2. CASE SIMPLE AVEC RÉPONSE COURTE (\"short_text\", EN LIGNE) : la "
    "réponse s'insère naturellement au milieu de la phrase ou de l'équation "
    "(texte à trous) — place le marqueur littéral {{blank}} à cet endroit "
    "précis dans \"statement\" (UN SEUL {{blank}} ; pour plusieurs cases "
    "indépendantes dans le même exercice, voir « case à trous » ci-dessous). "
    "answer.type parmi \"integer\", \"decimal\", \"rational\" (valeur "
    "[num, den]), \"expression\" (réduite, variable précisée), \"text\" (mot "
    "exact attendu, ex. « isocèle »).\n"
    "3. CASE TOUTE LA LARGEUR POUR RÉPONSE MOYENNE (\"short_text\", EN BLOC) : "
    "même answer.type qu'au format 2, mais la réponse ne s'insère PAS "
    "naturellement dans une phrase (ex. « Calcule $12+8$. », résultat "
    "attendu seul) — n'utilise PAS {{blank}}, la case de réponse est ajoutée "
    "automatiquement après l'énoncé, sur toute la largeur de la colonne.\n"
    "4. CASE À TROUS (\"multi_blank\") : PLUSIEURS cases de réponse courte "
    "indépendantes dans le MÊME exercice, quand les sous-questions (a., b., "
    "c...) sont des phrases ou équations hétérogènes qui NE forment PAS une "
    "grille régulière (sinon préfère « tableau à remplir »). Place un "
    "marqueur {{blank}} à CHAQUE endroit où l'élève doit écrire (2 minimum — "
    "pour une seule case, utilise le format 2 ou 3), dans l'ordre naturel de "
    "lecture. answer = {\"type\":\"blanks\",\"values\":[{\"type\":\"integer\"|"
    "\"decimal\"|\"rational\"|\"expression\"|\"text\",\"value\":...}, ...]} "
    "avec EXACTEMENT une entrée par occurrence de {{blank}}, dans le MÊME "
    "ordre que leur apparition dans \"statement\".\n"
    "5. TABLEAU À REMPLIR (\"table_fill\") : quand plusieurs résultats du "
    "même type forment naturellement une grille (ex. compléter une table de "
    "valeurs, un tableau de proportionnalité), OU quand un badge contient "
    "plusieurs sous-questions a./b./c. STRUCTURELLEMENT IDENTIQUES (même "
    "forme de phrase, seuls les nombres changent) : une ligne par "
    "sous-question, row_labels[i] = la phrase complète de la sous-question "
    "(avec le trou à la place où il apparaît, ex. « a. 7 × 8 = »), cols=1, "
    "cells[i][0] = la réponse attendue pour cette ligne. \"statement\" ne "
    "porte alors que la consigne commune (peut être très courte, ex. "
    "« Calcule. »), le détail complet est dans row_labels. Si un tableau du "
    "manuel imprime déjà certaines valeurs (colonne de calcul donnée, seule "
    "la colonne résultat est à remplir), marque ces cellules \"given\":true "
    "— elles seront imprimées telles quelles, non éditables, non notées (au "
    "moins une cellule de la grille doit rester non \"given\"). "
    "answer = {\"type\":\"table\",\"rows\":int (2-12),\"cols\":int (1-6),"
    "\"col_labels\":[str]?,\"row_labels\":[str]?,\"cells\":[[{\"type\":\"integer\"|"
    "\"decimal\"|\"rational\"|\"expression\"|\"text\",\"value\":...,\"given\":bool?}]]} "
    "(une ligne = une liste de cellules, \"rational\" a value=[num,den]).\n"
    "6. MULTI-LIGNE POUR RÉPONSE RAISONNÉE (\"multiline_text\", "
    "answer.type=\"rubric\") : raisonnement rédigé (obligatoire pour les "
    "problèmes) — 2 à 5 étapes {description, expected_text, points 1-3}, "
    "expected_text = ce qu'on doit lire sur la copie, balisé $...$ ; ajoute "
    "\"lines\" : nombre de lignes de rédaction à prévoir (3-12), PROPORTIONNÉ "
    "à la longueur attendue de la réponse (pas un nombre fixe) — une "
    "justification en une phrase mérite 3-4 lignes, un problème à plusieurs "
    "étapes 8-12.\n"
    "7. POINTS À RELIER (\"matching\", DERNIER RECOURS avant le tracé, à "
    "n'utiliser que si aucun des formats ci-dessus ne convient à une tâche "
    "d'association) : deux listes à relier — answer = {\"type\":\"matching\","
    "\"left\":[str] (2-6),\"right\":[str] (2-6),\"pairs\":[[i,j]]} (indices "
    "0-based, chaque élément utilisé une seule fois).\n"
    "8. FIGURE GÉOMÉTRIQUE, COLORIAGE, DESSIN (\"manual_drawing\", DERNIER "
    "RECOURS ABSOLU, tous domaines confondus — pas seulement la géométrie : "
    "construction géométrique, tâche de repérage/coloriage sur une figure "
    "non géométrique, opération posée en colonnes, ou toute tâche qui ne "
    "rentre vraiment dans AUCUN des formats 1 à 7 malgré la reformulation) : "
    "l'élève écrit/trace librement sur la copie, la correction est TOUJOURS "
    "manuelle, JAMAIS automatique (aucune correction possible par la "
    "pipeline) ; aucune réponse structurée requise, \"answer\" peut être "
    "omis. Utilise CE format plutôt que d'omettre l'exercice.\n\n"
)

_FIGURE_RULES = (
    "FIGURES : si une figure aide (géométrie, droite graduée, repère), ajoute "
    "\"figure\": {\"type\": \"rectangle\"|\"triangle\"|\"circle\"|\"angle\"|"
    "\"number_line\"|\"coordinate_plane\", \"params\": {...}} avec les MÊMES valeurs "
    "que l'énoncé. Types de params : rectangle{length,width,unit,show_diagonal} ; "
    "triangle{base,height,unit,right_angle_at} ; circle{radius,unit,show_diameter} ; "
    "angle{degrees,label} ; number_line{min,max,points:[{value,label}]} ; "
    "coordinate_plane{points:[{x,y,label}],grid}.\n\n"
)

_JSON_CONTRACT = (
    "Réponds UNIQUEMENT en JSON strictement valide :\n"
    '{"exercises":[{"kind":"application"|"probleme","statement":str,"correction":str '
    "(TRÈS SUCCINCTE : le résultat + 1-2 phrases d'explication au maximum, "
    "jamais une résolution pas-à-pas),"
    '"response_type":"short_text"|"qcm_single"|"qcm_multiple"|"multi_blank"|'
    '"multiline_text"|"table_fill"|"matching"|"manual_drawing",'
    '"choices":[str]?,"answer":{"type":"integer"|"decimal"|"rational"|"expression"|'
    '"text"|"choice"|"rubric"|"table"|"blanks"|"matching","value":...,"variable":str?,'
    '"correct":[int]?,"steps":[{"description":str,"expected_text":str,"points":int}]?,'
    '"lines":int?,"rows":int?,"cols":int?,"col_labels":[str]?,"row_labels":[str]?,'
    '"cells":[[{"type":str,"value":...}]]?,"values":[{"type":str,"value":...}]?,'
    '"left":[str]?,"right":[str]?,"pairs":[[int,int]]?},'
    '"figure":{...}?,"source_blocks":[int]?}]}'
)


def format_contract(intro: str, *, geometry_rules: str = "") -> str:
    """Bloc de prompt décrivant le contrat de sortie attendu par
    `_validate_exercise` : menu des 8 formats de réponse, figures, règles
    LaTeX, schéma JSON. `intro` cadre la MISSION de la pipeline appelante
    (adapter un exercice existant vs en inventer un) ; tout le reste est
    commun, et doit le rester — un prompt qui décrirait le contrat autrement
    que le validateur produit des rejets silencieux."""
    return (intro + _FORMAT_MENU + geometry_rules + _FIGURE_RULES
            + _GEN_FORMAT_RULES + "\n\n" + _JSON_CONTRACT)


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


# ================================================================ banque

def ensure_bank(db: Session, competency: Competency, level: int,
                source: str = "sesamaths") -> list[GeneratedExercise]:
    """Garantit une banque d'exercices actifs pour (compétence, niveau).

    Deux sources actives, chacune avec son pool séparé et sa propre notion de
    « banque suffisante » — c'est pourquoi il n'y a pas de cible commune ici :
    - `source="sesamaths"` (services.sesamaths) : extraction du manuel scolaire.
      Le pool est celui de la Série, fini : on prend tout, il n'y a rien à
      viser ;
    - `source="gemini"` (services.gemini_gen) : création par LLM. Le pool est
      infini : on appelle par lots jusqu'à `settings.gemini_bank_target`.

    La génération MathALÉA/DeepSeek (`source` "auto"/"mathalea") a été retirée
    (16/07) : plus aucun repli silencieux sur du contenu inventé sans pipeline
    assumée."""
    if source == "sesamaths":
        from . import sesamaths
        return sesamaths.ensure_bank(db, competency, level)
    if source == "gemini":
        from . import gemini_gen
        return gemini_gen.ensure_bank(db, competency, level)
    raise NotImplementedError(
        f"Génération d'exercices source={source!r} désactivée : seules "
        "l'extraction Sésamaths (source=\"sesamaths\") et la création Gemini "
        "(source=\"gemini\") sont actives.")


def _source_pool(source: str) -> tuple[str, ...] | None:
    """Valeurs de GeneratedExercise.source appartenant à `source` — None si la
    source n'a pas de pool dédié (ne jamais filtrer au hasard). Les pools ne se
    mélangent pas : un sujet « Gemini » ne doit jamais servir un exercice tiré
    du manuel, et réciproquement."""
    if source == "sesamaths":
        from .sesamaths import SOURCE_POOL
        return SOURCE_POOL
    if source == "gemini":
        from .gemini_gen import SOURCE
        return (SOURCE,)
    return None


def bank_rows_near_level(db: Session, competency: Competency, level: int,
                         source: str = "sesamaths") -> tuple[list[GeneratedExercise], int]:
    """Comme pick_exercise, mais retourne toute la banque du niveau le plus
    proche disponible (pour une sélection en aval équilibrée par type de
    réponse, cf. services.distribution). `source` : voir ensure_bank."""
    pool = _source_pool(source)
    for candidate in sorted(range(1, 6), key=lambda l: abs(l - level)):
        try:
            rows = ensure_bank(db, competency, candidate, source=source)
        except Exception as e:
            # Échec PROPRE à la source (manuel Sésamath introuvable, compétence
            # de géométrie refusée par Gemini…) : message clair et actionnable,
            # remonté tel quel. Inutile d'essayer les autres niveaux (la cause
            # ne dépend pas du niveau) et surtout PAS de repli silencieux sur
            # une banque d'une autre provenance.
            from .gemini_gen import GeminiGenerationError
            from .sesamaths import SesamathsExtractionError
            if isinstance(e, (SesamathsExtractionError, GeminiGenerationError)):
                raise
            q = db.query(GeneratedExercise).filter_by(
                competency_id=competency.id, difficulty_level=candidate, status="active")
            if pool is not None:
                q = q.filter(GeneratedExercise.source.in_(pool))
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

def ensure_lesson(db: Session, competency: Competency, level: int) -> LessonSnippet:
    """Rappel de leçon pour élève fragile, par compétence × tranche de niveau
    (1-3 / 4-5) — sert d'abord ce qui est déjà en banque (actif).

    La génération DeepSeek d'un rappel a été retirée (16/07) en même temps que
    la génération d'exercices : une pipeline de rappels basée sur la même
    extraction Sésamaths (pages « À RETENIR » du manuel) est prévue pour la
    remplacer, pas encore implémentée. En attendant, seuls les rappels déjà
    en banque sont servis."""
    lo, hi = (1, 3) if level <= 3 else (4, 5)
    row = (db.query(LessonSnippet)
           .filter_by(competency_id=competency.id, level_min=lo, level_max=hi,
                      status="active")
           .first())
    if row:
        return row
    raise NotImplementedError(
        f"Aucun rappel de leçon en banque pour {competency.code} (niveau {lo}-{hi}) : "
        "la génération DeepSeek a été retirée, la pipeline de remplacement "
        "(extraction Sésamaths) n'est pas encore implémentée.")


__all__ = ["ensure_bank", "pick_exercise", "ensure_lesson",
           "student_level_to_difficulty"]

"""Indigo — moteur d'ADAPTATION des champs de réponse élève.

Après que Gemini a « mis au propre » un exercice du manuel (services.
indigo_gemini) et qu'il a passé le validateur partagé (services.exercise_gen),
on ajuste ICI, de façon DÉTERMINISTE, la forme des cases que l'élève remplira —
en fonction de la SEULE réponse attendue, jamais du type d'exercice (« seuls les
tags/metadata changent »). Trois décisions, robustes et idempotentes :

1. TAILLE DE CHAQUE CASE (cf. statement.MINI_TOKEN / WIDE_TOKEN) :
   • réponse = petit entier (0-99) ET la case est COLLÉE à une formule
     (équation à trous, ex. « $60 = 2 \\times${{blank}} ») → mini-case {{mini}}
     (2 chiffres). Un petit entier isolé dans une phrase ordinaire (« Le
     résultat est {{blank}}. ») N'EST PAS une mini-case : une équation à trous
     est le SEUL contexte qui la justifie (cf. _adjacent_to_formula) ;
   • réponse plus longue (expression, fraction, décimal, texte) placée en fin de
     ligne → case pleine largeur {{blank_right}} pour maximiser l'espace ;
   • sinon → case standard {{blank}}. Le corps de texte de la ligne SUIT
     toujours sa case, mini comprise (cf. statement.has_answer_field) : jamais
     deux tailles de police différentes selon la case portée par une phrase.

2. UNE CASE ENTRE CHAQUE SOUS-QUESTION a. b. c. : quand un exercice a plusieurs
   sous-questions ET autant de réponses, on garantit une case en FIN de CHAQUE
   sous-question (jamais toutes regroupées à la fin, où l'élève se perd).

3. NOMBRE DE LIGNES d'un champ « raisonnement rédigé » (multiline_text) :
   estimé sur la longueur réelle du corrigé attendu, pas un forfait.

Le moteur préserve TOUJOURS le nombre et l'ordre des cases : la correction
apparie les cases à `expected` par position (cf. services.grading table_cells),
ré-typer une case ne doit jamais en ajouter ni en retirer une.
"""
from __future__ import annotations

import math
import re

from . import mathrender
from . import statement as statement_mod

BLANK = statement_mod.BLANK_TOKEN
MINI = statement_mod.MINI_TOKEN
WIDE = statement_mod.WIDE_TOKEN

# Estimation du nombre de lignes d'un raisonnement rédigé : largeur utile d'une
# carte à ~48 caractères imprimés par ligne, majorée d'un facteur « écriture
# manuscrite » (l'élève écrit plus gros et laisse des blancs / ratures).
_CHARS_PER_LINE = 48
_HAND_FACTOR = 1.5
_MIN_LINES, _MAX_LINES = 3, 12


# --------------------------------------------------------------------------- taille des cases

def _num_value(v: dict):
    """Valeur numérique d'une case/réponse, ou None si non numérique/absente."""
    if not isinstance(v, dict):
        return None
    if v.get("type") == "integer":
        try:
            return int(v.get("value"))
        except (TypeError, ValueError):
            return None
    return None


def _is_short_numeric(v: dict) -> bool:
    """Petit entier 0-99 : tient dans une mini-case de deux chiffres."""
    n = _num_value(v)
    return n is not None and 0 <= n <= 99


def _is_wide(v: dict) -> bool:
    """Réponse « longue » qui gagne à s'étaler jusqu'au bord de la colonne :
    expression, fraction, décimal, ou texte de plus de deux caractères."""
    if not isinstance(v, dict):
        return False
    t = v.get("type")
    if t in ("expression", "rational", "decimal", "number"):
        return True
    if t == "text":
        return len(str(v.get("value", "")).strip()) > 2
    return False


def _adjacent_to_formula(before: str, after: str) -> bool:
    """Le {{blank}} est-il COLLÉ (sans espace) à une formule $...$ juste avant ou
    juste après ? C'est la signature d'une ÉQUATION À TROUS — le manuel écrit
    « ... » au milieu d'un calcul, et le prompt Gemini impose de fermer la
    formule puis coller la case (« $60 = 2 \\times${{blank}} »). C'est le SEUL
    cas où la mini-case a du sens (cf. demande utilisateur) : une réponse courte
    isolée dans une phrase ordinaire (« Le résultat est {{blank}}. ») n'y est
    PAS collée et doit rester une case standard, même pour un petit entier."""
    return before.endswith("$") or after.startswith("$")


def _choose_token(value: dict | None, at_eol: bool, formula_adjacent: bool) -> str:
    """Marque de case à poser pour une réponse donnée, selon sa taille, sa
    position (une case pleine largeur n'a de sens qu'en FIN de ligne) et son
    adossement à une formule (seul cas où la mini-case s'applique)."""
    if formula_adjacent and _is_short_numeric(value):
        return MINI
    if at_eol and _is_wide(value):
        return WIDE
    return BLANK


def _reset(statement: str) -> str:
    """Ramène toute variante de case à la case standard {{blank}} — rend le
    moteur idempotent (ré-exécuter reproduit le même résultat) et absorbe une
    variante qu'un LLM aurait écrite directement."""
    return statement.replace(WIDE, BLANK).replace(MINI, BLANK)


def _ordered_values(response_type: str, expected: dict) -> list[dict] | None:
    """Réponses attendues dans l'ordre de lecture des cases en ligne, ou None si
    ce format n'a pas de case dans le fil du texte (QCM, tableau, appariement…)."""
    expected = expected or {}
    if response_type == "short_text" and expected.get("inline"):
        return [expected]
    if response_type == "multi_blank":
        cells = expected.get("cells") or []
        return list(cells[0]) if cells else None
    return None


def _type_blanks(statement: str, values: list[dict]) -> str:
    """Remplace chaque {{blank}}, dans l'ordre, par la variante adaptée à la
    réponse correspondante. La position (fin de ligne ou non) décide de
    l'éligibilité à la case pleine largeur ; l'adossement à une formule décide
    de l'éligibilité à la mini-case (cf. _adjacent_to_formula)."""
    parts = statement.split(BLANK)
    if len(parts) == 1:
        return statement
    out = [parts[0]]
    for i in range(1, len(parts)):
        value = values[i - 1] if i - 1 < len(values) else None
        before = parts[i - 1]
        tail = parts[i]
        nl = tail.find("\n")
        seg = tail if nl < 0 else tail[:nl]
        # fin de ligne = rien de significatif ne suit la case (ponctuation tolérée)
        at_eol = seg.strip(" .:;)]}·•") == ""
        out.append(_choose_token(value, at_eol, _adjacent_to_formula(before, tail)))
        out.append(tail)
    return "".join(out)


# --------------------------------------------------------------------- une case par sous-question

def _blank_block_bounds(lines: list[str], labels: list[int]) -> list[tuple[int, int]]:
    """Pour chaque sous-question, (début, fin_incluse) de son bloc de lignes."""
    return [(start, (labels[j + 1] if j + 1 < len(labels) else len(lines)) - 1)
            for j, start in enumerate(labels)]


# Amorce vide (« Réponses : ») laissée par un modèle qui regroupe les cases à la
# fin : quand on répartit les cases sur les sous-questions, cette ligne devient
# une coquille et on la retire.
_BARE_LEADIN = re.compile(r"(?i)^r[ée]ponses?\s*[:.]?\s*$")


def _redistribute_subquestions(statement: str, n_values: int) -> str:
    """Garantit une case en FIN de CHAQUE sous-question a./b./c. quand leur
    nombre égale celui des réponses attendues ET qu'une sous-question se
    retrouve SANS case (cases regroupées à la fin, où l'élève se perd). Si
    chaque sous-question porte déjà sa case, on ne touche à RIEN : une case bien
    placée au milieu d'une égalité (« $3 \\times${{blank}}$ = 12$ ») doit y rester.

    L'ordre des sous-questions = l'ordre des réponses (le prompt l'impose), donc
    répartir les cases ne casse pas l'appariement de la correction."""
    lines = statement.split("\n")
    labels = [i for i, ln in enumerate(lines) if statement_mod.subquestion_label(ln)]
    if len(labels) < 2 or len(labels) != n_values:
        return statement
    bounds = _blank_block_bounds(lines, labels)
    per_block = [sum(lines[i].count(BLANK) for i in range(a, b + 1)) for a, b in bounds]
    if all(c >= 1 for c in per_block):
        return statement          # déjà réparti : on respecte le placement en ligne
    stripped = [ln.replace(BLANK, "").rstrip() for ln in lines]
    label_set = set(labels)
    for i in labels:              # une case en fin de la LIGNE d'étiquette
        stripped[i] = (stripped[i] + " " + BLANK).strip()
    # on retire les coquilles (« Réponses : » vidée de ses cases), pas les vraies
    # lignes de sous-question
    return "\n".join(ln for k, ln in enumerate(stripped)
                     if ln and (k in label_set or not _BARE_LEADIN.match(ln)))


# ------------------------------------------------------------------- raisonnement : nombre de lignes

def _steps(expected: dict, grading: dict) -> list[dict]:
    for src in ((expected or {}).get("steps"), (grading or {}).get("rubric"),
                (grading or {}).get("steps")):
        if isinstance(src, list) and src:
            return src
    return []


def estimate_reasoning_lines(expected: dict, grading: dict) -> int:
    """Nombre de lignes d'écriture à réserver pour un raisonnement rédigé,
    estimé sur la LONGUEUR du corrigé attendu (une étape courte ≈ 1 ligne, une
    étape longue en occupe plusieurs), majoré pour l'écriture manuscrite. À
    défaut d'étapes exploitables, on garde la valeur déjà posée (ou le minimum)."""
    steps = _steps(expected, grading)
    if not steps:
        try:
            return max(_MIN_LINES, min(_MAX_LINES, int((grading or {}).get("lines", _MIN_LINES))))
        except (TypeError, ValueError):
            return _MIN_LINES
    raw = 0
    for s in steps:
        txt = mathrender.strip_math(str((s or {}).get("expected_text", "")))
        hard = txt.count("\n")
        raw += max(1, math.ceil(len(txt) / _CHARS_PER_LINE) + hard)
    lines = math.ceil(raw * _HAND_FACTOR)
    return max(_MIN_LINES, min(_MAX_LINES, lines))


# ----------------------------------------------------------------------------------- point d'entrée

def adapt_fields(statement: str, response_type: str,
                 expected: dict | None, grading: dict | None) -> tuple[str, dict]:
    """Adapte les champs de réponse d'un exercice Indigo. Retourne
    (statement, grading) — jamais d'exception : en cas d'imprévu on rend l'entrée
    inchangée (le rendu sait déjà traiter {{blank}} et un multiline_text)."""
    grading = dict(grading or {})
    try:
        if response_type == "multiline_text":
            grading["lines"] = estimate_reasoning_lines(expected or {}, grading)
            return statement, grading

        values = _ordered_values(response_type, expected or {})
        if not values:
            return statement, grading

        statement = _reset(statement or "")
        statement = _redistribute_subquestions(statement, len(values))
        statement = _type_blanks(statement, values)
        return statement_mod.normalize(statement), grading
    except Exception:  # robustesse : un exercice ne doit jamais casser sur ce point
        return statement, grading

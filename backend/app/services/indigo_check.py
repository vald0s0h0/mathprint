"""Indigo — vérification DÉTERMINISTE des QCM produits par le mode « QCM only ».

Aucun LLM ici : c'est précisément le point. La pipeline QCM demande au modèle de
produire un exercice ET, à côté, de quoi le VÉRIFIER en Python (champ `check`).
Python recalcule, compare, et REFUSE ce qui ne tombe pas juste. Un QCM dont un
distracteur vaut la bonne réponse, ou dont la bonne réponse est fausse, n'est pas
« un peu moins bon » : il est incorrigeable et il fausse la note de tout élève
qui le rencontre.

Deux couches, dans cet ordre :

  1. `lint` — contrôles STRUCTURELS et de CLARTÉ, toujours appliqués. Ils ne
     demandent rien au modèle : bornes du format, propositions réellement
     distinctes, marqueurs de champ de réponse qui n'ont rien à faire dans un
     QCM, énoncé qui renvoie hors de la feuille (« voir page 42 »). Une figure
     mentionnée sans image disponible n'en fait PAS partie : c'est une note de
     relecture (`figure_note`), le professeur ajoute l'image au brouillon.
  2. `check_math` — contrôle MATHÉMATIQUE via sympy, à partir du champ `check`.
     Le modèle déclare ce qui doit être vrai (« la bonne réponse vaut
     pgcd(1925, 4125) ») ; on l'évalue et on exige que la proposition cochée
     corresponde ET que les autres en diffèrent. Une question non calculatoire
     (reconnaissance, vocabulaire) déclare `{"kind": "none"}` et ne subit que la
     couche 1 — on ne fabrique pas une vérification là où il n'y a rien à
     calculer.

`verify` enchaîne les deux et renvoie la liste des problèmes trouvés (vide =
exercice accepté). L'appelant (services.indigo_qcm) tente UNE réparation avec ces
raisons, puis abandonne la variante — jamais de publication d'un QCM non vérifié.
"""
from __future__ import annotations

import logging
import re

import sympy
from sympy.parsing.sympy_parser import parse_expr

from . import mathrender, scoring
from . import statement as statement_mod
from .grading import TRANSFORMS, normalize, parse_number, strip_answer_noise

logger = logging.getLogger("app.indigo")

QCM_TYPES = ("qcm_single", "qcm_multiple", "checkbox_grid")

# Marqueurs de champ de réponse (case à écrire, lignes de raisonnement). Dans un
# QCM l'élève coche : un marqueur resté dans l'énoncé s'imprimerait en case vide
# à côté des propositions, et l'élève ne saurait plus où répondre.
_ANSWER_MARKER_RE = re.compile(r"\{\{\s*(blank|blank_right|mini|line\d+|check|dot)\s*\}\}")

# Renvois HORS de la feuille. L'élève n'a que sa copie sous les yeux : un énoncé
# qui s'appuie sur le manuel ou sur l'exercice voisin est insoluble une fois
# imprimé seul sur une carte.
_EXTERNAL_REF_RE = re.compile(
    r"(?i)\b(voir\s+(la\s+)?page|à\s+la\s+page|page\s+\d+|"
    r"exercice\s+(n[°o]\s*)?\d+|comme\s+dans\s+l['’]exercice)\b")

# Renvois à ce qui PRÉCÈDE. Refusés dans un exercice autonome — rien ne le
# précède sur la carte. Tolérés dans une SOUS-QUESTION (`part=True`) : les
# sous-questions d'un même exercice s'impriment ensemble, « la question
# précédente » y est bien sous les yeux de l'élève.
_SIBLING_REF_RE = re.compile(
    r"(?i)\b(question\s+précédente|ci[- ]dessus|précédemment)\b")

# Mention d'un visuel. UNE seule définition pour tout Indigo : services.indigo
# l'utilise via `mentions_figure` ci-dessous (elle y était recopiée, et deux
# copies d'une même règle finissent toujours par diverger).
_FIGURE_REF_RE = re.compile(
    r"(?i)\b(figure|sch[ée]ma|dessin|graphique|diagramme|courbe|tableau|"
    r"histogramme|image|photo|illustration|plan|carte|ci[- ]contre|"
    r"ci[- ]dessous|donn[ée]es\s+(?:repr[ée]sent[ée]es|ci[- ]dessous)|"
    r"r[ée]partition\s+(?:repr[ée]sent[ée]e|ci[- ]dessous))\b")


def mentions_figure(text: str) -> bool:
    """L'énoncé s'appuie-t-il sur un visuel ? Si oui, il en faut un.

    Le marqueur de PLACEMENT « {{figure}} » est retiré avant la recherche : il
    contient le mot « figure » et répondrait donc oui à tous les coups, ce qui
    viderait le contrôle de son sens."""
    return bool(_FIGURE_REF_RE.search(
        statement_mod.strip_figure_marker(str(text or ""))))

# Longueur d'énoncé. Une GRILLE porte l'essentiel dans les libellés de ses
# lignes, sa consigne commune est légitimement courte (« Vrai ou faux ? ») — même
# distinction que le validateur partagé (exercise_gen._validate_exercise).
STATEMENT_MIN = 20
STATEMENT_MIN_GRID = 8
# Une SOUS-QUESTION porte le contexte de son exercice, pas le sien : « Quelle est
# la plus grande dalle possible ? » se suffit à elle-même sous un énoncé commun.
STATEMENT_MIN_PART = 5
STATEMENT_MAX = 1200


def _flat(text: str) -> str:
    """Texte comparable : LaTeX aplati puis normalisé comme une réponse d'élève.

    Passe par les MÊMES fonctions que la correction (mathrender.strip_math +
    grading.normalize) pour qu'un distracteur jugé « distinct » ici le reste
    au moment de la notation."""
    return normalize(mathrender.strip_math(str(text or ""))).strip().lower()


# ------------------------------------------------------------------ couche 1

def lint_statement(statement: str, *, has_figure: bool,
                   min_len: int = STATEMENT_MIN, part: bool = False) -> list[str]:
    """Problèmes d'un ÉNONCÉ seul, indépendamment du format de réponse.

    Extrait de `lint` parce qu'un énoncé n'a pas toujours de propositions sous
    lui : le CONTEXTE commun d'un exercice à sous-questions n'en a aucune (§ le
    composite du mode « QCM multipass »), et il doit pourtant être aussi
    autonome et aussi propre que n'importe quel autre énoncé."""
    statement = str(statement or "").strip()
    problems: list[str] = []
    if len(statement) < min_len:
        problems.append(f"énoncé trop court ({len(statement)} caractères, "
                        f"minimum {min_len}) : la consigne doit se suffire à elle-même")
    if len(statement) > STATEMENT_MAX:
        problems.append(f"énoncé trop long ({len(statement)} caractères, "
                        f"maximum {STATEMENT_MAX})")
    marker = _ANSWER_MARKER_RE.search(statement)
    if marker:
        problems.append(f"marqueur de champ de réponse « {marker.group(0)} » dans "
                        "l'énoncé : dans un QCM l'élève coche, il n'écrit pas")
    ref = _EXTERNAL_REF_RE.search(statement) or (
        None if part else _SIBLING_REF_RE.search(statement))
    if ref:
        problems.append(f"renvoi hors de la feuille (« {ref.group(0)} ») : l'élève "
                        "n'a que sa copie, l'énoncé doit être autonome")
    return problems


def figure_note(statement: str, *, has_figure: bool) -> str:
    """Note de relecture — JAMAIS un refus — quand l'énoncé s'appuie sur un
    visuel qu'aucune image n'accompagne.

    Ce contrôle était un rejet, et c'était une erreur de conception. D'abord
    parce qu'il est structurellement bruyant : `_FIGURE_REF_RE` reconnaît
    « tableau », « graphique », « diagramme »… c'est-à-dire le vocabulaire
    ORDINAIRE du chapitre statistiques, où l'énoncé cite un tableau qu'il porte
    lui-même en toutes lettres. Ensuite parce qu'il n'avait aucune issue : on
    demandait au modèle de « reformuler sans visuel » un exercice dont les
    données SONT un tableau, et il repartait pour quatre tentatives identiques
    avant que la source ne soit jetée (extraction du 03/09 : 9 exercices sur 33).

    Une image manquante ne rend pas un brouillon inutilisable : le professeur
    l'ajoute à la relecture (onglet Exercices → « Ajouter une image »). On note
    donc le besoin au lieu de détruire le travail des cinq passes."""
    if has_figure or not mentions_figure(statement):
        return ""
    return ("l'énoncé s'appuie sur un visuel qu'aucune image n'accompagne : "
            "ajoute la figure à la relecture, ou vérifie que les données "
            "suffisent telles qu'elles sont écrites")


def lint(variant: dict, *, has_figure: bool, part: bool = False) -> list[str]:
    """Problèmes structurels et de clarté d'une variante QCM. [] = rien à dire.

    `part` : la variante est une SOUS-QUESTION d'un exercice à sous-questions.
    Deux assouplissements, et deux seulement : l'énoncé peut être court (le
    contexte est au-dessus) et il peut renvoyer à la question précédente (elle
    est sur la même carte). Tout le reste est vérifié à l'identique."""
    rtype = variant.get("response_type")
    if rtype not in QCM_TYPES:
        return [f"format « {rtype} » interdit en mode QCM (attendu : "
                f"{', '.join(QCM_TYPES)})"]

    if part:
        # Une grille en sous-question ne porte rien d'autre que ses lignes et ses
        # colonnes : elles SONT la question, et lui réclamer une phrase revient à
        # réclamer le « Vrai ou faux ? » que la mise en page supprime.
        min_len = 0 if rtype == "checkbox_grid" else STATEMENT_MIN_PART
    else:
        min_len = STATEMENT_MIN_GRID if rtype == "checkbox_grid" else STATEMENT_MIN
    problems = lint_statement(variant.get("statement"), has_figure=has_figure,
                              min_len=min_len, part=part)

    if rtype == "checkbox_grid":
        problems += _lint_grid(variant)
    else:
        problems += _lint_choices(variant, exclusive=rtype == "qcm_single")
    return problems


def _lint_choices(variant: dict, *, exclusive: bool) -> list[str]:
    problems: list[str] = []
    choices = [str(c).strip() for c in (variant.get("choices") or [])]
    n = len(choices)
    if not 2 <= n <= scoring.QCM_MAX_CHOICES:
        return [f"{n} proposition(s), attendu 2 à {scoring.QCM_MAX_CHOICES}"]
    if any(not c for c in choices):
        problems.append("une proposition est vide")
    flat = [_flat(c) for c in choices]
    if len(set(flat)) != n:
        problems.append("deux propositions sont identiques une fois le LaTeX aplati "
                        "— l'élève ne peut pas les départager")

    correct = variant.get("correct")
    if not (isinstance(correct, list) and correct
            and all(isinstance(i, int) and 0 <= i < n for i in correct)):
        return problems + [f"« correct » doit lister des indices de 0 à {n - 1}"]
    correct = sorted(set(correct))
    if exclusive and len(correct) != 1:
        problems.append(f"QCM à réponse unique : {len(correct)} bonnes réponses "
                        "déclarées, il en faut exactement une")
    if len(correct) >= n:
        problems.append("toutes les propositions sont justes : l'exercice n'évalue rien")
    return problems


def _lint_grid(variant: dict) -> list[str]:
    problems: list[str] = []
    cols = [str(c).strip() for c in (variant.get("cols") or [])]
    rows = variant.get("rows")
    if not 2 <= len(cols) <= 4:
        problems.append(f"{len(cols)} colonne(s), attendu 2 à 4")
    elif len({c.lower() for c in cols}) != len(cols):
        problems.append("deux colonnes portent le même libellé")
    if not isinstance(rows, list) or not 2 <= len(rows) <= scoring.QCM_MAX_GRID_ROWS:
        n = len(rows) if isinstance(rows, list) else 0
        return problems + [f"{n} ligne(s), attendu 2 à {scoring.QCM_MAX_GRID_ROWS}"]
    labels = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            problems.append(f"ligne {i + 1} mal formée")
            continue
        label = str(r.get("label") or "").strip()
        if not label:
            problems.append(f"ligne {i + 1} sans libellé")
        labels.append(_flat(label))
        try:
            col = int(r.get("correct"))
        except (TypeError, ValueError):
            problems.append(f"ligne {i + 1} : colonne correcte absente ou illisible")
            continue
        if cols and not 0 <= col < len(cols):
            problems.append(f"ligne {i + 1} : colonne correcte {col} hors des "
                            f"{len(cols)} colonnes")
    if labels and len(set(labels)) != len(labels):
        problems.append("deux lignes de la grille portent la même affirmation")
    return problems


# ------------------------------------------------------------------ couche 2

def _value_of(text: str):
    """Valeur sympy d'une proposition, ou None si elle n'en porte pas.

    Deux lectures successives, de la plus stricte à la plus tolérante : un
    nombre écrit à la française (virgule décimale, espace milliers) via le
    parseur de la correction, puis une expression symbolique. Une proposition
    purement textuelle (« le triangle est rectangle ») ne rend rien : c'est
    légitime, elle sera simplement hors du contrôle numérique."""
    flat = mathrender.strip_math(str(text or ""))
    norm = normalize(flat)
    if not norm.strip():
        return None
    number = parse_number(norm)
    if number is not None:
        return sympy.Rational(number.numerator, number.denominator)
    # Une proposition comme « 168 km/h » porte bien la valeur 168. Le
    # correcteur des copies sait déjà retirer une liste EXPLICITE d'unités ;
    # le QCM doit employer exactement la même règle au lieu de transformer
    # l'unité en variable symbolique et de refuser une réponse juste.
    numeric = strip_answer_noise(norm)
    if numeric != norm:
        number = parse_number(numeric)
        if number is not None:
            return sympy.Rational(number.numerator, number.denominator)
    try:
        return sympy.simplify(parse_expr(norm, transformations=TRANSFORMS))
    except Exception:
        return None


def _stat_values(values) -> list:
    if isinstance(values, (list, tuple, sympy.Tuple)):
        seq = list(values)
    else:
        seq = [values]
    if not seq:
        raise ValueError("série vide")
    return [sympy.nsimplify(v) for v in seq]


def _median(values):
    seq = sorted(_stat_values(values), key=sympy.default_sort_key)
    middle = len(seq) // 2
    return seq[middle] if len(seq) % 2 else sympy.simplify(
        (seq[middle - 1] + seq[middle]) / 2)


def _mean(values):
    seq = _stat_values(values)
    return sympy.simplify(sum(seq) / len(seq))


_EVAL_LOCALS = {"median": _median, "Median": _median,
                "mean": _mean, "Mean": _mean, "average": _mean}


def _eval(expr: str):
    """Valeur sympy d'une expression déclarée par le modèle. Lève ValueError si
    elle est illisible — c'est un refus, pas un silence.

    Volontairement SANS `normalize` : cette fonction-là sert aux réponses
    d'élèves écrites à la française et remplace la virgule décimale par un
    point, ce qui détruit le séparateur d'arguments — « gcd(1925,4125) »
    devenait « gcd(1925.4125) ». Le prompt impose donc au modèle d'écrire
    `check.expr` en syntaxe sympy ASCII (point décimal, virgule = séparateur),
    jamais en LaTeX ni en notation française."""
    try:
        source = re.sub(r"\bstatistics\.(median|mean)\b", r"\1", str(expr))
        return sympy.simplify(parse_expr(
            source, local_dict=_EVAL_LOCALS, transformations=TRANSFORMS))
    except Exception as e:
        raise ValueError(f"expression « {expr} » illisible ({type(e).__name__}) — "
                         "attendu une expression sympy ASCII, ex. « gcd(1925, 4125) » "
                         "ou « 3*x + 2 »") from e


# Bruit de calcul flottant. « 8.4 - 3.1 » vaut 5.300000000000001 en binaire :
# comparé à 5,3 par une égalité exacte, il déclare fausse une réponse juste.
_FLOAT_NOISE = sympy.Rational(1, 10) ** 9


# Décimales ÉCRITES dans une proposition (« 294 183,33 € » → 2). C'est le texte
# qui fait foi, pas la valeur : « 3,00 € » est écrit à deux décimales même si sa
# valeur est l'entier 3. Lire la précision sur le rationnel aurait fait arrondir
# la bonne réponse 3,15 à l'unité, donc déclaré le distracteur « 3,00 € » juste.
_WRITTEN_DECIMALS_RE = re.compile(r"\d[.,](\d+)")


def _written_decimals(text: str) -> int | None:
    """Nombre de décimales écrites dans `text`, ou None s'il n'en porte pas."""
    matches = _WRITTEN_DECIMALS_RE.findall(mathrender.strip_math(str(text or "")))
    return max((len(m) for m in matches), default=None)


def _is_rounding_of(text: str, want) -> bool:
    """« text » est-il l'ARRONDI CORRECT de la valeur exacte `want` ?

    Une bonne réponse écrite « 294 183,33 € » pour 1765100/6 est ce qu'un élève
    écrit et ce qu'un professeur attend. On l'accepte à la seule condition
    qu'elle tombe juste à la décimale près de ce qui est ÉCRIT : « 15,9 » pour
    172/11 (= 15,63…) reste refusé, il s'arrondit à 15,6."""
    decimals = _written_decimals(text)
    got = _value_of(text)
    if decimals is None or got is None:
        return False
    try:
        exact = sympy.Rational(sympy.nsimplify(sympy.N(want, 30)))
        rounded = sympy.Rational(round(float(exact) * 10 ** decimals), 10 ** decimals)
        return sympy.Rational(got) == rounded
    except (TypeError, ValueError, AttributeError):
        return False


def _equal(a, b) -> bool:
    """La proposition `a` correspond-elle à la valeur exacte `b` ?

    Trois lectures, de la plus stricte à la plus tolérante. Les deux dernières
    ne sont pas des complaisances : sans elles, la vérification refusait des
    exercices JUSTES (7 sur 33 le 03/09), ce qui est exactement le défaut
    qu'elle est censée empêcher, à l'envers.

      1. égalité symbolique — le cas normal ;
      2. égalité numérique au bruit flottant près — « 12*1.2 » ne vaut pas
         14,4 en binaire, et cela ne dit rien des mathématiques de l'exercice.

    L'ARRONDI, lui, n'est PAS traité ici : il se juge sur le texte écrit
    (§ `_is_rounding_of`), et seulement pour la bonne réponse. L'appliquer aussi
    aux distracteurs déclarerait « 3,00 € » égal à 3,15."""
    if a is None or b is None:
        return False
    try:
        if sympy.simplify(a - b) == 0:
            return True
    except Exception:
        return False
    try:
        gap = sympy.nsimplify(sympy.Abs(sympy.N(a - b)))
        if not gap.is_number or gap.is_extended_real is False:
            return False
        scale = max(1, abs(float(sympy.N(b))))
        return float(gap) <= float(_FLOAT_NOISE) * scale
    except (TypeError, ValueError, AttributeError):
        return False


def check_math(variant: dict) -> list[str]:
    """Contrôle mathématique d'une variante à partir de son champ `check`.

    Absent ou `{"kind": "none"}` : rien à recalculer (question non calculatoire),
    on rend []. Sinon toute incohérence est rendue en clair, pour être renvoyée
    telle quelle au modèle lors de l'unique tentative de réparation."""
    check = variant.get("check")
    if not isinstance(check, dict):
        return []
    kind = str(check.get("kind") or "none")
    if kind == "none":
        return []
    try:
        if kind == "value":
            return _check_value(variant, check)
        if kind == "set":
            return _check_set(variant, check)
        if kind == "rows":
            return _check_rows(variant, check)
    except ValueError as e:
        return [f"vérification impossible : {e}"]
    return [f"type de vérification inconnu : « {kind} »"]


def _check_value(variant: dict, check: dict) -> list[str]:
    """La proposition désignée vaut `expr`, et aucune autre ne la vaut."""
    choices = [str(c) for c in (variant.get("choices") or [])]
    try:
        idx = int(check.get("choice"))
    except (TypeError, ValueError):
        return ["« check.choice » doit être l'indice de la bonne proposition"]
    if not 0 <= idx < len(choices):
        return [f"« check.choice » = {idx} hors des {len(choices)} propositions"]
    correct = sorted(set(variant.get("correct") or []))
    if correct != [idx]:
        return [f"la vérification désigne la proposition {idx}, mais « correct » "
                f"annonce {correct}"]
    want = _eval(check.get("expr"))
    got = _value_of(choices[idx])
    problems = []
    if got is None:
        problems.append(f"la proposition « {choices[idx]} » ne porte aucune valeur "
                        "calculable : la vérification ne peut pas la confronter à "
                        f"« {check.get('expr')} »")
    elif not _equal(got, want) and not _is_rounding_of(choices[idx], want):
        # Cas piégeux et fréquent : « $3$ m » ne vaut pas 3 pour sympy — l'unité
        # devient un symbole libre et la comparaison échoue alors que la réponse
        # est juste. Sans cette phrase, le modèle relit une réponse correcte,
        # n'y voit rien à corriger, et brûle ses tentatives.
        hint = ""
        if getattr(got, "free_symbols", None) and not getattr(want, "free_symbols", None):
            hint = (" — la proposition mêle un nombre et une unité, alors qu'une "
                    "vérification « value » compare des nombres : mets l'unité dans "
                    "la question, ou déclare {\"kind\": \"none\"}")
        problems.append(f"la bonne réponse annoncée « {choices[idx]} » ne vaut pas "
                        f"« {check.get('expr')} » (= {want}){hint}")
    for i, c in enumerate(choices):
        if i == idx:
            continue
        if _equal(_value_of(c), want):
            problems.append(f"le distracteur « {c} » vaut la bonne réponse : "
                            "deux cases seraient justes")
    return problems


def _check_set(variant: dict, check: dict) -> list[str]:
    """QCM multiple : `exprs` donne une valeur de vérité par proposition."""
    choices = variant.get("choices") or []
    exprs = check.get("exprs")
    if not isinstance(exprs, list) or len(exprs) != len(choices):
        return [f"« check.exprs » doit compter {len(choices)} entrées "
                f"(une par proposition), reçu "
                f"{len(exprs) if isinstance(exprs, list) else 'autre chose'}"]
    correct = set(variant.get("correct") or [])
    problems = []
    for i, expr in enumerate(exprs):
        truth = _truth(expr)
        if truth is None:
            problems.append(f"proposition {i} : « {expr} » ne s'évalue pas en "
                            "vrai/faux")
            continue
        if truth != (i in correct):
            problems.append(
                f"proposition {i} (« {choices[i]} ») : la vérification la dit "
                f"{'VRAIE' if truth else 'FAUSSE'}, elle devrait donc "
                f"{'figurer dans' if truth else 'être absente de'} « correct »")
    return problems


def _check_rows(variant: dict, check: dict) -> list[str]:
    """Grille : `exprs` donne une valeur de vérité par ligne. N'a de sens que
    sur une grille à DEUX colonnes (Vrai/Faux, Oui/Non) : au-delà, la colonne
    juste n'est plus une valeur de vérité et seul le lint s'applique."""
    rows = variant.get("rows") or []
    cols = variant.get("cols") or []
    exprs = check.get("exprs")
    if len(cols) != 2:
        return []
    if not isinstance(exprs, list) or len(exprs) != len(rows):
        return [f"« check.exprs » doit compter {len(rows)} entrées (une par ligne)"]
    # colonne « vraie » = celle dont le libellé dit oui (Vrai / Oui / V)
    true_col = 0 if _flat(cols[0]) in ("vrai", "oui", "v", "true") else 1
    problems = []
    for i, (row, expr) in enumerate(zip(rows, exprs)):
        truth = _truth(expr)
        if truth is None:
            problems.append(f"ligne {i + 1} : « {expr} » ne s'évalue pas en vrai/faux")
            continue
        want_col = true_col if truth else 1 - true_col
        if int(row.get("correct", -1)) != want_col:
            problems.append(
                f"ligne {i + 1} (« {row.get('label')} ») : la vérification la dit "
                f"{'VRAIE' if truth else 'FAUSSE'}, la colonne correcte devrait "
                f"être {want_col}")
    return problems


def _truth(expr) -> bool | None:
    """Valeur de vérité d'une déclaration du modèle.

    Accepte un booléen JSON, ou une expression sympy comparable
    (« 17 % 2 == 1 » écrit « Eq(17, 17) », « 45 > 30 »…). None si indécidable —
    et un « indécidable » est un REFUS, pas un laissez-passer."""
    if isinstance(expr, bool):
        return expr
    try:
        value = _eval(str(expr))             # mêmes fonctions autorisées que value
    except (TypeError, ValueError):
        return None
    truth = value.simplify() if hasattr(value, "simplify") else value
    if truth in (sympy.true, True):
        return True
    if truth in (sympy.false, False):
        return False
    return None


# --------------------------------------------------------------------- API

def verify(variant: dict, *, has_figure: bool, part: bool = False) -> list[str]:
    """Tous les problèmes d'une variante QCM, lint PUIS mathématique. [] = OK.

    Le lint passe en premier et court-circuite : inutile de recalculer les
    valeurs d'un QCM dont on sait déjà que le format est hors bornes, et les
    raisons rendues au modèle restent lisibles (trois lignes, pas trente).

    `part` : cf. `lint` — une sous-question subit exactement le même contrôle
    mathématique, seule l'exigence d'autonomie de l'énoncé est assouplie."""
    problems = lint(variant, has_figure=has_figure, part=part)
    if problems:
        return problems
    return check_math(variant)

"""Moteur de correction déterministe (§6.4, §6.5).

Priorité au déterminisme : les égalités numériques et symboliques sont
traitées sans LLM. La chaîne OCR originale est conservée telle quelle ;
la normalisation travaille sur une copie. Jamais de comparaison de LaTeX brut.

Retourne (tier, score, confidence, reason_code) :
  A/B -> validation automatique ; C -> LLM ; D -> file professeur ; E -> blocage.
"""
import re
from fractions import Fraction

import sympy
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application,)


def normalize(raw: str) -> str:
    """Normalisation FR -> canonique : virgule décimale, ×, −, espaces, LaTeX simple."""
    s = raw.strip()
    s = s.replace("\\times", "*").replace("×", "*").replace("÷", "/")
    s = s.replace("−", "-").replace("–", "-")
    # virgule décimale française balisée en LaTeX ($3{,}5$, cf. _GEN_FORMAT_RULES)
    # AVANT le dépliage des fractions : sinon les accolades de {,} sont prises
    # pour la fermeture du groupe \frac{...} et tronquent le numérateur.
    s = re.sub(r"(?<=\d)\{,\}(?=\d)", ".", s)
    # \frac ET \dfrac (le \d optionnel absorbe le préfixe "d" de \dfrac,
    # sinon jamais reconnu alors que c'est la commande préférée du prompt)
    s = re.sub(r"\\d?frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", s)
    # délimiteurs maths de Mathpix : « \( 8 \) », « \[ ... \] » (et $...$) — à
    # retirer sinon `\(` reste collé au nombre et fait échouer le parse (incident
    # « Réponse attendue 8 / OCR a lu \(8\) / Motif parse_error » : réponse juste
    # rejetée). Même normalisation partagée par cell_credit, donc corrige aussi les cases.
    s = (s.replace("\\left", "").replace("\\right", "")
         .replace("\\(", "").replace("\\)", "")
         .replace("\\[", "").replace("\\]", "").replace("$", ""))
    s = re.sub(r"\\text\{[^}]*\}", "", s)
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)   # virgule décimale non balisée (repli)
    s = re.sub(r"\s+", "", s)
    # réponse du type "x=5" -> garder le membre droit pour une solution demandée
    return s


def parse_number(s: str) -> Fraction | None:
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            return Fraction(int(num.strip("()")), int(den.strip("()")))
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


# ------------------------------------------------------- tolérance d'écriture
# L'élève écrit souvent PLUS que la réponse attendue : l'unité (« 12 cm »), le
# membre de gauche (« x = 12 », « PGCD = 12 »), un point final. Ces réponses
# sont JUSTES ; sans ce nettoyage elles ne se parsent pas et partaient en revue
# professeur — et, dans un tableau, une seule d'entre elles envoyait TOUT
# l'exercice en revue avec 0 point.
#
# Liste d'unités EXPLICITE, jamais « toutes les lettres finales » : sur une
# réponse de type expression, « 2m » doit rester « 2m » (m est une variable).
# C'est pourquoi le dépouillement n'est appliqué qu'aux types NUMÉRIQUES.
_UNITS = ("cm2", "cm3", "m2", "m3", "km2", "mm2", "dm3",
          "mm", "cm", "dm", "km", "kg", "mg", "hg", "dag", "mL", "cL", "dL",
          "min", "ms", "m", "g", "t", "L", "l", "h", "s",
          "unités", "unite", "unités.", "unité", "unites",
          "€", "$", "%", "°", "°C", "²", "³")
_UNIT_RE = re.compile(
    r"(?i)(?:" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")\.?$")
# « … = 42 » : tout ce qui précède le DERNIER « = » est un rappel de l'énoncé,
# la réponse est à droite. On ne coupe que si la droite est non vide.
_LEFT_MEMBER_RE = re.compile(r"^[^=]*=(?=.)")


def strip_answer_noise(norm: str) -> str:
    """Retire d'une réponse NUMÉRIQUE déjà normalisée ce que l'élève a ajouté
    autour du nombre : membre de gauche (« x= »), unité finale, point final.
    Ne touche ni au type `text` (où « cm » peut ÊTRE la réponse) ni aux
    expressions (où une lettre finale est une variable)."""
    s = _LEFT_MEMBER_RE.sub("", norm or "", count=1)
    s = _UNIT_RE.sub("", s)
    return s.rstrip(".") if s.rstrip(".") else s


# ------------------------------------------------------- crédit partiel
# Un arrondi correct n'est pas une faute de calcul : il vaut la MOITIÉ des
# points, pas zéro (décision utilisateur du 02/08). Une troncature non plus.
PARTIAL_CREDIT = 0.5
_DECIMALS_RE = re.compile(r"^-?\d+\.(\d+)$")


def _decimals(s: str) -> int | None:
    m = _DECIMALS_RE.match(s or "")
    return len(m.group(1)) if m else None


def _round_half_up(value: Fraction, k: int) -> Fraction:
    """Arrondi SCOLAIRE à k décimales (0,5 monte), pas l'arrondi bancaire de
    round() qui donnerait 0,2 pour 0,25 à une décimale."""
    q = Fraction(10) ** k
    scaled = value * q
    n = int(scaled + Fraction(1, 2)) if scaled >= 0 else -int(-scaled + Fraction(1, 2))
    return Fraction(n, 1) / q


def _truncate(value: Fraction, k: int) -> Fraction:
    q = Fraction(10) ** k
    return Fraction(int(value * q), 1) / q


def numeric_credit(got_norm: str, got: Fraction, want: Fraction,
                   want_text: str = "") -> float:
    """Crédit d'une réponse numérique : 1 (juste), PARTIAL_CREDIT (arrondi ou
    troncature corrects de la valeur exacte), 0 (faux).

    Deux sens de lecture, tous deux légitimes :
      • l'élève a écrit MOINS de décimales que la référence et a bien arrondi
        (« 0,7 » ou « 0,66 » pour 0,6667) -> crédit partiel ;
      • l'élève a écrit PLUS précis que la référence, ou la valeur exacte
        (« 0,6666 », « 2/3 » pour 0,67) -> crédit PLEIN : c'est la référence qui
        est arrondie, pas la copie."""
    if got == want:
        return 1.0
    k_want = _decimals(want_text)
    if k_want is not None and _round_half_up(got, k_want) == want:
        return 1.0                      # copie plus précise que la référence
    k_got = _decimals(got_norm)
    if k_got is not None and got in (_round_half_up(want, k_got), _truncate(want, k_got)):
        return PARTIAL_CREDIT
    return 0.0


def qcm_credit(correct: set[int], chosen: set[int], n_choices: int = 0,
               exclusive: bool = False) -> float:
    """Part des points d'un QCM.

    QCM à CHOIX MULTIPLES : chaque case est une décision de l'élève, et chaque
    décision juste rapporte sa part — cocher une case qu'il fallait cocher,
    mais AUSSI laisser vide une case qu'il ne fallait pas cocher : décider
    qu'une proposition est fausse est une réponse juste, elle vaut ses points
    (§ barème, décision utilisateur du 03/08). Un QCM de 4 cases dont 3 sont
    bien tranchées vaut donc 3/4 de son barème. C'est aussi ce qui empêche
    « je coche tout » de payer : les cases à laisser vides deviennent alors
    autant de décisions fausses.

    QCM à réponse UNIQUE (`exclusive`) : l'élève ne prend qu'UNE décision — quelle
    case cocher — donc tout ou rien. Le compter case par case offrirait la moitié
    des points à qui coche n'importe quoi (les cases qu'il fallait laisser vides
    le sont toutes, sauf une), ce qui n'a aucun sens sur un choix exclusif.

    `n_choices` inconnu (contrat ancien sans `choices`) : repli sur le comptage
    des seules bonnes réponses, une case cochée à tort en annulant une — on ne
    peut pas créditer des cases vides qu'on ne sait pas compter."""
    if not correct:
        return 0.0
    if exclusive:
        return float(chosen == correct)
    if n_choices <= 0:
        hits, wrong = len(chosen & correct), len(chosen - correct)
        return max(0, hits - wrong) / len(correct)
    right = sum(1 for i in range(n_choices) if (i in chosen) == (i in correct))
    return right / n_choices


def _extract_answer_side(s: str, variable: str | None) -> str:
    """Pour 'x=5' ne garder que '5' quand on attend la solution d'une équation."""
    if variable and "=" in s:
        left, right = s.split("=", 1)
        if left.replace(" ", "") == variable:
            return right
    return s


def cell_credit(exp_cell: dict, raw_cell: str) -> float | None:
    """Crédit d'une cellule de tableau à remplir : 1 (juste), PARTIAL_CREDIT
    (arrondi correct), 0 (fausse), None si illisible (parse impossible).
    Source unique de la comparaison par cellule, partagée par la NOTATION
    (grade, comparator table_cells) et le MARQUAGE de l'overlay (cell_marks) :
    deux règles dériveraient."""
    norm = normalize(raw_cell or "")
    # case VIDE (aucune encre → jamais envoyée à Mathpix, § pipeline) = réponse
    # non donnée = FAUSSE, jamais « illisible ». Un blanc ne doit pas envoyer
    # toute la réponse en revue ni occuper le professeur (§ modale = support pour
    # OCR défaillant, pas pour une case laissée vide).
    if not norm:
        return 0.0
    ctype = exp_cell["type"]
    if ctype == "text":
        return float(_text_equal(norm, str(exp_cell["value"])))
    if ctype == "expression":
        try:
            got_e = parse_expr(norm, transformations=TRANSFORMS)
            want_e = parse_expr(normalize(str(exp_cell["value"])), transformations=TRANSFORMS)
            return float(sympy.simplify(got_e - want_e) == 0)
        except Exception:
            return None
    norm = strip_answer_noise(norm)     # « 12 cm », « = 12 » : réponses justes
    got = parse_number(norm)
    if got is None:
        return None
    if ctype == "rational":
        num, den = exp_cell["value"]
        return numeric_credit(norm, got, Fraction(int(num), int(den)))
    want_text = str(exp_cell["value"])
    return numeric_credit(norm, got, Fraction(want_text), want_text)


def _fold(s: str) -> str:
    """Comparaison de texte tolérante : casse, accents et ponctuation finale
    ignorés — « Isocèle. » et « isocele » sont la même réponse."""
    import unicodedata
    stripped = unicodedata.normalize("NFD", s or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return stripped.casefold().strip(" .!;:")


# Séparateurs d'une réponse-LISTE écrite dans UNE case (« 1, 2, 3, 4 »). Le
# prompt l'interdit désormais (§ une case = une réponse : une liste se demande
# dans un tableau `unordered`), mais les exercices déjà en banque — et les
# élèves — en produisent : comparer ces listes à l'ordre près évite de refuser
# une réponse juste écrite dans le désordre.
_LIST_SEP_RE = re.compile(r"[;,]")


def _list_items(folded: str) -> list[str] | None:
    parts = [p.strip() for p in _LIST_SEP_RE.split(folded)]
    parts = [p for p in parts if p]
    return sorted(parts) if len(parts) > 1 else None


def _text_equal(norm_got: str, want_raw: str) -> bool:
    got, want = _fold(norm_got), _fold(normalize(want_raw))
    if got == want:
        return True
    # LISTE des deux côtés : même contenu dans un autre ordre = même réponse.
    # Jamais quand un seul côté est une liste (répondre « 2 » à « 2 ; 3 » reste faux).
    got_items, want_items = _list_items(got), _list_items(want)
    return got_items is not None and got_items == want_items


def cell_reference_text(cell: dict) -> str:
    """Texte canonique d'une cellule JUSTE — celui que `cell_credit` reconnaît à
    coup sûr. Sert à réécrire une cellule que le professeur valide « juste » en
    correction manuelle, pour que la marque d'overlay (`cell_marks`, dérivée du
    texte de cellule) reste cohérente avec la note attribuée. Miroir de
    services.exercise_gen._cell_reference_text (côté génération), gardé ici pour
    ne pas créer d'import circulaire (exercise_gen dépend déjà de grading)."""
    if cell["type"] == "rational":
        return f"{cell['value'][0]}/{cell['value'][1]}"
    return str(cell["value"])


def fillable_cells(grading: dict) -> list[dict]:
    """Cellules NOTÉES d'un tableau/multi_blank (ordre row-major) : toutes sauf
    les « given », déjà imprimées par le manuel."""
    return [c for row in (grading.get("cells") or []) for c in row
            if not c.get("given")]


def table_credits(grading: dict, cell_texts: list[str] | None) -> list[float | None]:
    """Crédit de CHAQUE case écrite par l'élève (1 juste, 0,5 arrondi correct,
    0 faux, None illisible), dans l'ordre des cases de la copie.

    Source UNIQUE de la comparaison d'un tableau, partagée par la note
    (`grade`), les marques d'overlay (`cell_marks`) et la modale de correction
    (routers.scans._cell_units) : trois lectures divergeraient.

    `grading["unordered"]` — le tableau attend une LISTE de résultats dont
    l'ordre n'a aucun sens (les diviseurs de 24, les solutions d'une équation) :
    l'élève remplit alors les cases dans l'ordre qu'il veut, et on APPARIE
    chaque case écrite à une réponse attendue encore libre. Sans ça, une liste
    juste mais dans le désordre valait zéro."""
    flat = fillable_cells(grading)
    texts = list(cell_texts or [])
    texts += [""] * max(0, len(flat) - len(texts))
    if not grading.get("unordered"):
        return [cell_credit(exp, raw) for exp, raw in zip(flat, texts)]

    free = list(range(len(flat)))
    credits: list[float | None] = []
    for raw in texts[:len(flat)]:
        if not normalize(raw or ""):
            credits.append(0.0)         # case laissée vide = réponse non donnée
            continue
        best_i, best_c, readable = None, 0.0, False
        for i in free:
            c = cell_credit(flat[i], raw)
            readable = readable or c is not None
            if c is not None and c > best_c:
                best_i, best_c = i, c
                if c >= 1.0:
                    break
        if best_i is None:
            # aucune réponse attendue ne colle : faux si on a su lire la case,
            # illisible si AUCUNE comparaison n'a pu aboutir (OCR défaillant).
            credits.append(0.0 if readable else None)
        else:
            free.remove(best_i)
            credits.append(best_c)
    return credits


def cell_marks(grading: dict, cell_texts: list[str] | None,
               credits: list[float] | None = None) -> list[float]:
    """Crédit par cellule NON-donnée (ordre row-major), pour marquer chaque
    champ d'un tableau/multi_blank en overlay (coche / demi / croix). Une
    cellule illisible OU absente de l'OCR est comptée fausse : « tous les champs
    de réponse doivent être marqués », jamais laissés sans signe.

    `credits` : verdicts EXPLICITES du professeur (correction manuelle case par
    case). Ils font foi — ils portent le demi-point, que le texte de cellule
    réécrit ne saurait pas exprimer."""
    flat = fillable_cells(grading)
    if credits is not None:
        # `None` (case illisible) vaut 0 ICI seulement : un champ de réponse doit
        # TOUJOURS porter une marque sur la copie corrigée. Le None est conservé
        # tel quel en base, où il veut dire « à trancher » (modale de correction).
        marks = [max(0.0, min(1.0, float(v or 0.0))) for v in credits[:len(flat)]]
    else:
        marks = [c or 0.0 for c in table_credits(grading, cell_texts)]
    marks += [0.0] * (len(flat) - len(marks))
    return marks


def grid_rows_ok(grading: dict, selected: list[int] | None) -> list[bool]:
    """Justesse par LIGNE d'une grille cochée (checkbox_grid), pour marquer chaque
    ligne en overlay. `selected[i]` = colonne cochée à la ligne i (-1 = rien).
    Une ligne sans lecture (crédit plein reconstruit ailleurs) est comptée fausse
    faute de sélection — jamais laissée sans signe."""
    rows = grading.get("rows") or []
    sel = selected or []
    return [(i < len(sel) and sel[i] == r.get("correct")) for i, r in enumerate(rows)]


def grade(expected: dict, grading: dict, ocr_text: str, ocr_confidence: float,
          selected_choices: list[int] | None = None,
          cell_texts: list[str] | None = None,
          selected_pairs: list[list[int]] | None = None) -> dict:
    """Décision déterministe. Ne choisit jamais en cas d'ambiguïté (RM-005)."""
    max_score = float(grading.get("max_score", 1))
    comparator = grading.get("comparator", "numeric")
    result = {"max_score": max_score, "score": 0.0, "tier": "D",
              "confidence": ocr_confidence, "reason_code": "unresolved"}

    # --- sans réponse structurée (manual_drawing, tracé/dessin) : toujours
    # revue professeur, même vide — jamais de score deviné sur une planche
    # blanche (§ tracés géométriques, correction humaine obligatoire) ---
    if comparator == "manual":
        result.update(tier="D", reason_code="no_structured_answer")
        return result

    # --- points à relier : détection CV du trait, jamais de choix deviné ---
    if comparator == "matching":
        expected_pairs = {tuple(p) for p in expected.get("pairs", [])}
        if selected_pairs is None:
            result.update(tier="D", reason_code="matching_unreadable")
            return result
        got_pairs = [tuple(p) for p in selected_pairs]
        if len(set(got_pairs)) != len(got_pairs):
            result.update(tier="D", reason_code="matching_ambiguous")
            return result
        got_set = set(got_pairs)
        score = float(len(got_set & expected_pairs))
        ok = got_set == expected_pairs
        result.update(tier="B", score=score, confidence=1.0,
                      reason_code="matching_match" if ok else "matching_partial")
        return result

    # --- tableau à remplir : une comparaison numérique/texte par cellule
    # (les cellules "given" sont déjà imprimées dans le manuel, non éditables,
    # exclues de la notation) ---
    if comparator == "table_cells":
        flat_expected = fillable_cells(grading)
        if cell_texts is None or len(cell_texts) != len(flat_expected):
            result.update(tier="D", reason_code="table_unreadable")
            return result
        # Le score est TOUJOURS calculé sur l'ensemble des cellules, y compris
        # quand l'une d'elles est illisible : sortir au premier `None` renvoyait
        # tout l'exercice en revue AVEC 0 point, en jetant les cellules justes
        # déjà lues (une seule case « 7 cm » coûtait un tableau entier).
        score, unreadable = 0.0, 0
        partial = False
        for credit in table_credits(grading, cell_texts):
            if credit is None:
                unreadable += 1
                continue
            partial = partial or 0.0 < credit < 1.0
            score += credit
        if unreadable:
            # revue professeur (l'OCR n'a pas su lire), mais le score partiel
            # accompagne la décision : la modale arrive pré-remplie.
            result.update(tier="D", score=score, reason_code="table_cell_unreadable")
            return result
        full = score == len(flat_expected)
        result.update(tier="A" if full else "B", score=score, confidence=1.0,
                      reason_code="table_match" if full
                      else "table_partial" if partial else "table_mismatch")
        return result

    # --- QCM : purement déterministe (CV local, pas de LLM) ---
    if comparator == "qcm":
        correct = set(expected.get("correct", []))
        # choix EXCLUSIF (qcm_single) : posé par le contrat depuis le comptage
        # par case ; les contrats antérieurs se lisent sur le nombre de bonnes
        # réponses, comme le fait déjà la règle de la double coche juste après.
        exclusive = grading.get("exclusive")
        if exclusive is None:
            exclusive = len(correct) == 1
        if selected_choices is None:
            result.update(tier="D", reason_code="qcm_unreadable")
            return result
        chosen = set(selected_choices)
        if len(chosen) == 0:
            # copie BLANCHE sur ce QCM : zéro, et surtout pas le crédit des
            # cases « bien laissées vides » — ne rien faire n'est pas répondre.
            result.update(tier="A", score=0.0, confidence=1.0, reason_code="qcm_blank")
        elif expected.get("type") == "choice" and len(chosen) > 1 and exclusive:
            # double coche sur un choix EXCLUSIF -> exception, jamais un choix
            # arbitraire (§4.3). Sur un QCM multiple, cocher deux cases est une
            # réponse ordinaire (partiellement fausse), pas une ambiguïté —
            # même quand une seule des propositions est vraie.
            result.update(tier="D", reason_code="qcm_double_check")
        else:
            # crédit PARTIEL, case par case : une seule case ratée ne fait plus
            # tomber tout le QCM à zéro. Le tier reste A : le comptage des
            # coches est déterministe, un crédit partiel n'a pas plus besoin de
            # revue qu'une réponse juste.
            credit = qcm_credit(correct, chosen,
                                len(grading.get("choices") or []),
                                exclusive=bool(exclusive))
            result.update(tier="A", score=max_score * credit, confidence=1.0,
                          reason_code="qcm_match" if credit == 1.0
                          else "qcm_partial" if credit else "qcm_wrong")
        return result

    # --- grille cochée (checkbox_grid) : une case cochée par ligne, lue par CV
    # (comme le QCM). `selected_choices` porte ici, par POSITION, l'indice de la
    # colonne cochée à chaque ligne (-1 = rien coché). Score = nombre de lignes
    # dont la colonne cochée est la bonne. Une lecture ambiguë (None) part en revue. ---
    if comparator == "grid":
        rows = grading.get("rows") or []
        if selected_choices is None:
            result.update(tier="D", reason_code="grid_unreadable")
            return result
        score = 0.0
        for i, r in enumerate(rows):
            sel = selected_choices[i] if i < len(selected_choices) else -1
            if sel == r.get("correct"):
                score += 1.0
        full = score >= max_score
        result.update(tier="A", score=score, confidence=1.0,
                      reason_code="grid_match" if full else "grid_mismatch")
        return result

    if not ocr_text.strip():
        result.update(tier="A", score=0.0, confidence=1.0, reason_code="blank")
        return result

    if ocr_confidence < 0.55:
        result.update(tier="D", reason_code="ocr_low_confidence")
        return result

    norm = normalize(ocr_text)
    norm = _extract_answer_side(norm, expected.get("variable"))
    etype = expected.get("type")

    if comparator == "text_equal" or etype == "text":
        ok = _text_equal(norm, str(expected.get("value") or ""))
        result.update(tier="B" if ok else "C",
                      score=max_score if ok else 0.0,
                      reason_code="text_match" if ok else "text_mismatch")
        # texte différent : ambiguïté possible (accents, notation) -> tier C/D
        if not ok:
            result.update(tier="D", reason_code="text_mismatch")
        return result

    try:
        if etype in ("integer", "decimal") or comparator in ("numeric", "equation_solution"):
            # l'unité et le membre de gauche écrits en plus par l'élève ne rendent
            # pas la réponse fausse. Dépouillement réservé aux types NUMÉRIQUES :
            # sur une expression, « 2m » est un produit, pas « 2 mètres ».
            norm = strip_answer_noise(norm)
            got = parse_number(norm)
            want_text = str(expected["value"])
            want = (Fraction(*expected["value"]) if etype == "rational"
                    else Fraction(want_text))
            if got is None:
                # tenter une évaluation symbolique du texte (ex: "2+3")
                got_expr = parse_expr(norm, transformations=TRANSFORMS)
                if got_expr.is_number:
                    got = Fraction(str(sympy.nsimplify(got_expr)))
            if got is None:
                result.update(tier="C" if comparator == "equation_solution" else "D",
                              reason_code="parse_failed")
                return result
            credit = numeric_credit(norm, got, want, want_text)
            # le tier reste piloté par la CONFIANCE OCR, pas par la justesse :
            # A et B sont tous deux automatiques, un crédit partiel n'a pas à
            # déclencher de revue (décision utilisateur : demi-point auto).
            tier = "A" if ocr_confidence >= 0.85 else "B"
            result.update(tier=tier, score=max_score * credit,
                          reason_code="numeric_match" if credit == 1.0
                          else "numeric_rounded" if credit else "numeric_mismatch")
            return result

        if etype == "rational" or comparator == "rational_equiv":
            norm = strip_answer_noise(norm)
            got = parse_number(norm)
            want = Fraction(*expected["value"])
            if got is None:
                result.update(tier="D", reason_code="parse_failed")
                return result
            credit = numeric_credit(norm, got, want)
            result.update(tier="B", score=max_score * credit,
                          reason_code="rational_equiv" if credit == 1.0
                          else "rational_rounded" if credit else "rational_mismatch")
            return result

        if etype == "expression" or comparator == "symbolic_equiv":
            var = sympy.Symbol(expected.get("variable", "x"))
            got_e = parse_expr(norm, transformations=TRANSFORMS, local_dict={str(var): var})
            # expected["value"] est balisé comme le reste (LaTeX $...$ : \dfrac,
            # \times, {,} décimale française...) — il doit passer par la MÊME
            # normalisation que le texte OCR, sinon parse_expr échoue (exception
            # silencieusement rattrapée plus bas) dès que la réponse de référence
            # contient la moindre notation LaTeX (cf. incident auto-vérification
            # 'expression' toujours refusée).
            want_e = parse_expr(normalize(str(expected["value"])), transformations=TRANSFORMS,
                                local_dict={str(var): var})
            ok = sympy.simplify(got_e - want_e) == 0
            result.update(tier="B", score=max_score if ok else 0.0,
                          reason_code="symbolic_equiv" if ok else "symbolic_mismatch")
            return result

        # type non couvert par le parseur -> refuser la comparaison (§6.5)
        result.update(tier="C", reason_code="type_not_covered")
        return result

    except Exception:
        # Réponse multiligne / ambiguë -> rubrique LLM ou revue
        tier = "C" if grading.get("rubric") else "D"
        result.update(tier=tier, reason_code="parse_error")
        return result

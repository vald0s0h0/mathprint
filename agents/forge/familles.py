"""Familles : une carte écrite une fois, plusieurs jumelles imprimées.

L'auteur écrit `familles/<emplacement>.json` :

    {"slot": "s10",
     "jeux": [{"a": 3, "b": 4, "P": "A"}, {"a": 5, "b": 2, "P": "M"}],
     "carte": { …une carte au contrat habituel, avec des valeurs à remplir… }}

- `jeux` : 2 ou 3 jeux de valeurs. Mêmes noms dans chaque jeu ; un nombre ou un texte.
- `carte` : le contrat de `contexte/formats/`, où `<<a>>` reprend une valeur et `<<a*b>>` un
  calcul (SymPy). Le script calcule : une réponse ou un distracteur ne se recopie pas.
- `dispositions`, sur une question à réponses cochées, reliées ou en tableau : deux
  ordres des réponses, **écrits par l'auteur**. Aucun script ne mélange une réponse.

Le script en tire les jumelles : chaque jeu, dans chaque disposition. La première
(jeu 1, disposition 1) devient `slots/<emplacement>.json`, les autres
`jumelles/<emplacement>.jXdY.json`. Toutes passent ensuite les mêmes contrôles
qu'une carte écrite à la main.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

JEUX_MIN, JEUX_MAX = 2, 3
DISPOSITIONS = 2
# Formats dont les réponses se déplacent : l'auteur écrit deux ordres, ou dit pourquoi
# l'ordre est imposé (suite logique, chronologie, valeurs croissantes, protocole).
MOVABLE = ("qcm_single", "qcm_multiple", "checkbox_grid", "matching")
_PLACEHOLDER = re.compile(r"<<((?:(?!<<|>>).)+)>>", re.DOTALL)
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+|/\d+)?")
_FORMATS = ("txt", "tex", "frac", "calc", "pt", "num", "rat")
_DEC = re.compile(r"dec(\d)")
# Clés dont le texte est un calcul SymPy ou du TikZ : les nombres s'y écrivent en ASCII.
_ASCII_KEYS = {"calc": "calc", "answer_expr": "calc", "tikz": "pt"}


class FamilyError(ValueError):
    pass


def twin_key(slot: str, jeu: int, disposition: int) -> str:
    """Clé d'une jumelle. La première porte le nom de l'emplacement."""
    return slot if (jeu, disposition) == (1, 1) else f"{slot}.j{jeu}d{disposition}"


def group_of(key: str) -> str:
    """Emplacement d'une clé de jumelle (`s10.j2d1` → `s10`)."""
    return key.split(".", 1)[0]


# ------------------------------------------------------------------ valeurs
def _number(value):
    """Nombre exact d'un jeu, ou None si la valeur est un texte (nom, prénom, objet)."""
    import sympy
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return sympy.Integer(value)
    if isinstance(value, float):
        return sympy.Rational(str(value))
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        return sympy.Rational(value.strip().replace(",", "."))
    return None


def _finite_decimal(value) -> bool:
    q = int(value.q)
    for prime in (2, 5):
        while q % prime == 0:
            q //= prime
    return q == 1


def _decimal_text(value, places: int | None = None) -> str:
    import sympy
    if places is None:
        return _plain_decimal(value)
    rounded = sympy.Rational(round(value * 10 ** places), 10 ** places)
    return f"{float(rounded):.{places}f}"


def _plain_decimal(value) -> str:
    """Écriture décimale exacte d'un rationnel à développement fini, sans exposant."""
    sign = "-" if value < 0 else ""
    p, q = abs(int(value.p)), int(value.q)
    whole, rest = divmod(p, q)
    digits = []
    while rest and len(digits) < 12:
        rest *= 10
        digit, rest = divmod(rest, q)
        digits.append(str(digit))
    return sign + str(whole) + ("." + "".join(digits) if digits else "")


def _format(value, fmt: str, where: str):
    """Valeur calculée → ce qui s'écrit dans la carte."""
    import sympy
    if not getattr(value, "is_Rational", False):
        if fmt in ("num", "rat"):
            raise FamilyError(f"{where} : résultat non rationnel ({value}), impossible en `{fmt}`")
        return sympy.latex(value) if fmt in ("tex", "frac") else str(value)
    integer = value.q == 1
    places = _DEC.fullmatch(fmt)
    if places:
        return _decimal_text(value, int(places.group(1))).replace(".", ",")
    if fmt == "rat":
        return [int(value.p), int(value.q)]
    if fmt == "num":
        if integer:
            return int(value)
        if not _finite_decimal(value):
            raise FamilyError(f"{where} : {value} n'a pas d'écriture décimale exacte ; "
                              "utilise `|rat` pour une fraction")
        return float(_plain_decimal(value))
    if fmt == "calc":
        return str(int(value)) if integer else f"{int(value.p)}/{int(value.q)}"
    if fmt == "pt":
        return str(int(value)) if integer else f"{float(value):.4f}".rstrip("0").rstrip(".")
    if integer:
        return str(int(value))
    if fmt == "frac" or not _finite_decimal(value):
        if fmt == "txt":
            return f"{int(value.p)}/{int(value.q)}"
        sign = "-" if value < 0 else ""
        return f"{sign}\\dfrac{{{abs(int(value.p))}}}{{{int(value.q)}}}"
    text = _plain_decimal(value)
    return text.replace(".", "{,}") if fmt == "tex" else text.replace(".", ",")


def _evaluate(expression: str, jeu: dict, where: str):
    """(valeur, est un texte). Un nom seul rend la valeur du jeu ; sinon un calcul."""
    import sympy
    name = expression.strip()
    if name in jeu and _number(jeu[name]) is None:
        return str(jeu[name]), True
    numbers = {k: _number(v) for k, v in jeu.items() if _number(v) is not None}
    try:
        value = sympy.sympify(name, locals=numbers, rational=True)
    except (sympy.SympifyError, SyntaxError, TypeError, ZeroDivisionError) as exc:
        raise FamilyError(f"{where} : calcul « {name} » illisible ({exc})") from exc
    if getattr(value, "free_symbols", None):
        unknown = ", ".join(sorted(str(s) for s in value.free_symbols))
        raise FamilyError(f"{where} : « {unknown} » n'est pas une valeur du jeu")
    if value in (sympy.zoo, sympy.nan, sympy.oo, -sympy.oo):
        raise FamilyError(f"{where} : « {name} » n'a pas de valeur (division par zéro ?)")
    return sympy.nsimplify(value) if getattr(value, "is_Rational", False) else value, False


def _in_math(text: str, position: int) -> bool:
    """Le remplacement tombe-t-il dans une formule `$…$` ?"""
    return len(re.findall(r"(?<!\\)\$", text[:position])) % 2 == 1


def _fill_text(text: str, jeu: dict, key: str | None, where: str):
    whole = _PLACEHOLDER.fullmatch(text.strip())
    if whole:
        expression, fmt = _split(whole.group(1), where)
        value, is_text = _evaluate(expression, jeu, where)
        if is_text:
            return value
        return _format(value, fmt or _ASCII_KEYS.get(key or "", "num"), where)

    def replace(match):
        expression, fmt = _split(match.group(1), where)
        value, is_text = _evaluate(expression, jeu, where)
        if is_text:
            return value
        default = _ASCII_KEYS.get(key or "") or ("tex" if _in_math(text, match.start()) else "txt")
        out = _format(value, fmt or default, where)
        if not isinstance(out, str):
            raise FamilyError(f"{where} : `|{fmt}` rend une valeur, pas un texte ; "
                              "il s'emploie seul dans le champ")
        return out

    return _PLACEHOLDER.sub(replace, text)


def _split(inner: str, where: str) -> tuple[str, str | None]:
    expression, sep, fmt = inner.rpartition("|")
    if not sep:
        return inner, None
    fmt = fmt.strip()
    if fmt not in _FORMATS and not _DEC.fullmatch(fmt):
        raise FamilyError(f"{where} : format `|{fmt}` inconnu ({', '.join(_FORMATS)}, decN)")
    return expression, fmt


def fill(node, jeu: dict, key: str | None = None, where: str = "carte"):
    """La carte, ses valeurs remplies pour un jeu."""
    if isinstance(node, str):
        return _fill_text(node, jeu, key, where) if "<<" in node else node
    if isinstance(node, list):
        return [fill(item, jeu, key, f"{where}[{i}]") for i, item in enumerate(node)]
    if isinstance(node, dict):
        return {k: fill(v, jeu, k, f"{where}.{k}") for k, v in node.items()}
    return node


# ------------------------------------------------------------------ dispositions
def _leaves(card: dict) -> list[dict]:
    return list(card.get("parts") or []) if card.get("response_type") == "composite" else [card]


def _movable_ids(leaf: dict) -> list:
    """Ce qui peut changer de place dans cette question, par identifiant."""
    rtype = leaf.get("response_type")
    if rtype in ("qcm_single", "qcm_multiple"):
        return [c.get("id") for c in leaf.get("choices") or [] if isinstance(c, dict)]
    if rtype == "checkbox_grid":
        return [r.get("id") for r in leaf.get("rows") or [] if isinstance(r, dict)]
    if rtype == "matching":
        return [r.get("id") for r in leaf.get("right") or [] if isinstance(r, dict)]
    if rtype == "table_fill":
        return list(range(len((leaf.get("cells") or [[]])[0])))
    return []


def disposition_problems(leaf: dict) -> list[str]:
    """Deux ordres écrits par l'auteur, ou la raison d'un ordre imposé."""
    rtype = leaf.get("response_type")
    orders = leaf.get("dispositions")
    reason = str(leaf.get("ordre_impose") or "").strip()
    if orders is None:
        if rtype in MOVABLE and not reason:
            return ["écris `dispositions` (deux ordres des réponses), ou `ordre_impose` avec la "
                    "raison (suite logique, chronologie, valeurs croissantes, protocole)"]
        return []
    if reason:
        return ["`dispositions` et `ordre_impose` ne vont pas ensemble"]
    ids = _movable_ids(leaf)
    if not ids:
        return [f"`dispositions` : rien ne se déplace dans un {rtype}"]
    if not isinstance(orders, list) or len(orders) != DISPOSITIONS:
        return [f"`dispositions` : {DISPOSITIONS} ordres attendus"]
    problems = []
    for rank, order in enumerate(orders, 1):
        if not isinstance(order, list) or sorted(map(str, order)) != sorted(map(str, ids)):
            problems.append(f"disposition {rank} : elle doit contenir exactement "
                            f"{', '.join(map(str, ids))}, une fois chacun")
    if problems:
        return problems
    if list(map(str, orders[0])) == list(map(str, orders[1])):
        problems.append("les deux dispositions sont identiques")
    if rtype == "qcm_single":
        right = [str(c.get("id")) for c in leaf.get("choices") or [] if isinstance(c, dict) and c.get("correct")]
        if len(right) == 1 and (list(map(str, orders[0])).index(right[0])
                                == list(map(str, orders[1])).index(right[0])):
            problems.append("la bonne réponse est à la même place dans les deux dispositions")
    return problems


def arrange(leaf: dict, rank: int) -> dict:
    """La question dans sa disposition `rank` (1 ou 2). Seul l'ordre change : chaque
    réponse garde son identifiant, donc sa correction et son message."""
    out = {k: v for k, v in leaf.items() if k not in ("dispositions", "ordre_impose")}
    orders = leaf.get("dispositions")
    if not orders:
        return out
    order = orders[rank - 1]
    rtype = leaf.get("response_type")
    field = {"qcm_single": "choices", "qcm_multiple": "choices", "checkbox_grid": "rows",
             "matching": "right"}.get(rtype)
    if field:
        by_id = {str(item.get("id")): item for item in leaf.get(field) or []}
        out[field] = [by_id[str(i)] for i in order]
        return out
    # tableau : ce sont les colonnes qui bougent, jamais les lignes
    position = {int(old): new for new, old in enumerate(order)}
    out["cells"] = [[row[int(i)] for i in order] for row in leaf.get("cells") or []]
    if leaf.get("col_labels"):
        out["col_labels"] = [leaf["col_labels"][int(i)] for i in order]
    wrong = []
    for item in leaf.get("wrong_answers") or []:
        cell = item.get("cell") if isinstance(item, dict) else None
        wrong.append({**item, "cell": [cell[0], position[int(cell[1])]]} if cell else item)
    if wrong:
        out["wrong_answers"] = wrong
    out["mix"] = "none"
    return out


# ------------------------------------------------------------------ équité
def _shape(value) -> tuple:
    """Classe d'un nombre : sa nature, son signe, son ordre de grandeur."""
    number = _number(value)
    if number is None:
        return ("texte",)
    kind = "entier" if number.q == 1 else ("décimal" if _finite_decimal(number) else "fraction")
    sign = "négatif" if number < 0 else ("nul" if number == 0 else "positif")
    return (kind, sign, len(str(abs(int(number)))))


def _shape_text(shape: tuple) -> str:
    return shape[0] if shape[0] == "texte" else f"{shape[0]} {shape[1]}"


def fairness_problems(jeux: list[dict]) -> list[str]:
    """Des jeux de même difficulté : chaque valeur garde sa nature et son signe, et
    son ordre de grandeur ne change pas de plus d'un chiffre."""
    problems = []
    for name in jeux[0]:
        shapes = [_shape(jeu[name]) for jeu in jeux]
        if len({s[:2] for s in shapes}) > 1:
            problems.append(f"jeux de difficulté inégale : « {name} » est "
                            + ", puis ".join(_shape_text(s) for s in shapes))
        elif shapes[0][0] != "texte" and max(s[2] for s in shapes) - min(s[2] for s in shapes) > 1:
            problems.append(f"jeux de difficulté inégale : « {name} » change trop de taille "
                            f"({', '.join(str(jeu[name]) for jeu in jeux)})")
    return problems


def _answer_signature(card: dict) -> str:
    """Ce qu'un voisin pourrait souffler : les réponses de la carte."""
    import forge
    parts = []
    for leaf in _leaves(card):
        answers = forge._answer_texts(leaf)
        if not answers:
            answers = [{k: v for k, v in leaf.items() if not k.endswith("feedback")
                        and k not in ("guides", "dispositions", "llm_brief", "expected_errors")}]
        parts.append(answers)
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


# ------------------------------------------------------------------ jumelles
def twins(family: dict) -> tuple[dict[str, dict], list[str]]:
    """({clé: carte}, problèmes) d'une famille. Aucune jumelle si la famille est fautive."""
    slot = str(family.get("slot") or "")
    jeux = family.get("jeux")
    card = family.get("carte")
    if not isinstance(card, dict) or not isinstance(jeux, list):
        return {}, ["famille : `jeux` (liste) et `carte` (objet) attendus"]
    if not JEUX_MIN <= len(jeux) <= JEUX_MAX or not all(isinstance(j, dict) and j for j in jeux):
        return {}, [f"famille : {JEUX_MIN} ou {JEUX_MAX} jeux de valeurs attendus"]
    names = set(jeux[0])
    if any(set(jeu) != names for jeu in jeux):
        return {}, ["famille : tous les jeux portent les mêmes noms de valeurs"]
    problems = fairness_problems(jeux)
    if len({json.dumps(j, sort_keys=True, ensure_ascii=False) for j in jeux}) < len(jeux):
        problems.append("famille : deux jeux de valeurs sont identiques")
    for i, leaf in enumerate(_leaves(card)):
        where = f"partie {chr(97 + i)} : " if card.get("response_type") == "composite" else ""
        problems += [where + p for p in disposition_problems(leaf)]
    if problems:
        return {}, problems
    two = any(leaf.get("dispositions") for leaf in _leaves(card))
    out: dict[str, dict] = {}
    signatures = []
    for j, jeu in enumerate(jeux, 1):
        try:
            filled = fill(card, jeu)
        except FamilyError as exc:
            return {}, [f"jeu {j} : {exc}"]
        leftover = _PLACEHOLDER.search(json.dumps(filled, ensure_ascii=False))
        if leftover:
            return {}, [f"jeu {j} : « {leftover.group(0)} » n'a pas été rempli"]
        signatures.append(_answer_signature(filled))
        for d in range(1, (DISPOSITIONS if two else 1) + 1):
            twin = copy.deepcopy(filled)
            if twin.get("response_type") == "composite":
                twin["parts"] = [arrange(part, d) for part in twin.get("parts") or []]
            else:
                twin = arrange(twin, d)
            twin["slot"] = slot
            twin["twin"] = {"group": slot, "jeu": j, "disposition": d,
                            "jeux": len(jeux), "dispositions": DISPOSITIONS if two else 1}
            out[twin_key(slot, j, d)] = twin
    if len(set(signatures)) < len(signatures):
        return {}, ["famille : deux jeux donnent la même réponse ; un voisin pourrait la souffler"]
    return out, []


def develop(run: Path) -> dict[str, list[str]]:
    """Écrit les jumelles de chaque famille du run. {emplacement: problèmes} pour les
    familles fautives, dont les anciennes jumelles sont retirées."""
    folder = run / "familles"
    errors: dict[str, list[str]] = {}
    if not folder.is_dir():
        return errors
    twins_dir = run / "jumelles"
    for path in sorted(folder.glob("*.json")):
        slot = path.stem
        for old in twins_dir.glob(f"{slot}.j*.json") if twins_dir.is_dir() else ():
            old.unlink()
        try:
            family = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors[slot] = [f"famille : JSON invalide : {exc}"]
            (run / "slots" / f"{slot}.json").unlink(missing_ok=True)
            continue
        family.setdefault("slot", slot)
        cards, problems = twins(family)
        if problems:
            errors[slot] = problems
            (run / "slots" / f"{slot}.json").unlink(missing_ok=True)
            continue
        for key, card in cards.items():
            target = (run / "slots" / f"{slot}.json") if key == slot else (twins_dir / f"{key}.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return errors

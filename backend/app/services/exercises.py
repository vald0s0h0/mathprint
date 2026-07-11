"""Générateur d'exercices déterministe (adaptateur type MathALÉA, §3.3).

Chaque générateur reçoit une seed et une difficulté 1-10, et retourne un
instantané complet : énoncé, correction, réponses attendues et barème.
En production, le provider "mathalea" appelle le conteneur Node.js épinglé ;
le provider "builtin" fournit un catalogue local autonome du même contrat.
"""
import random
from dataclasses import dataclass, field
from fractions import Fraction


@dataclass
class GeneratedItem:
    statement: str
    correction: str
    response_type: str
    expected: dict
    grading: dict
    choices: list[str] = field(default_factory=list)


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


# ------------------------------------------------------------------ générateurs

def gen_addition_relatifs(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    span = 5 + difficulty * 3
    a, b = r.randint(-span, span), r.randint(-span, span)
    ans = a + b
    return GeneratedItem(
        statement=f"Calculer : ({a}) + ({b}) = ?",
        correction=f"({a}) + ({b}) = {ans}",
        response_type="short_text",
        expected={"type": "integer", "value": ans},
        grading={"max_score": 1, "comparator": "numeric", "tolerance": 0},
    )


def gen_multiplication_relatifs(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    span = 3 + difficulty
    a, b = r.randint(-span, span), r.randint(-span, span)
    ans = a * b
    return GeneratedItem(
        statement=f"Calculer : ({a}) × ({b}) = ?",
        correction=f"({a}) × ({b}) = {ans}",
        response_type="short_text",
        expected={"type": "integer", "value": ans},
        grading={"max_score": 1, "comparator": "numeric", "tolerance": 0},
    )


def gen_fraction_somme(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    d1 = r.choice([2, 3, 4, 5, 6][: 2 + difficulty // 3])
    d2 = r.choice([2, 3, 4, 5, 6, 8][: 2 + difficulty // 2])
    n1, n2 = r.randint(1, d1), r.randint(1, d2)
    ans = Fraction(n1, d1) + Fraction(n2, d2)
    return GeneratedItem(
        statement=f"Calculer et simplifier : {n1}/{d1} + {n2}/{d2} = ?",
        correction=f"{n1}/{d1} + {n2}/{d2} = {ans.numerator}/{ans.denominator}"
        if ans.denominator != 1 else f"{n1}/{d1} + {n2}/{d2} = {ans.numerator}",
        response_type="short_text",
        expected={"type": "rational", "value": [ans.numerator, ans.denominator]},
        grading={"max_score": 2, "comparator": "rational_equiv", "simplified_bonus": False},
    )


def gen_equation_1er_degre(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    a = r.randint(2, 3 + difficulty // 2)
    x = r.randint(-5 - difficulty, 5 + difficulty)
    b = r.randint(-10, 10)
    c = a * x + b
    return GeneratedItem(
        statement=f"Résoudre l'équation : {a}x {'+' if b >= 0 else '−'} {abs(b)} = {c}",
        correction=f"{a}x = {c - b} donc x = {x}",
        response_type="multiline_text",
        expected={"type": "integer", "value": x, "variable": "x"},
        grading={
            "max_score": 3,
            "comparator": "equation_solution",
            "rubric": [
                {"step": "isoler le terme en x", "points": 1},
                {"step": "diviser par le coefficient", "points": 1},
                {"step": "valeur finale correcte", "points": 1},
            ],
        },
    )


def gen_qcm_priorites(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    a, b, c = r.randint(2, 5 + difficulty), r.randint(2, 6), r.randint(2, 4)
    good = a + b * c
    distractors = {(a + b) * c, a * b + c, a + b + c} - {good}
    choices = [str(good)] + [str(d) for d in list(distractors)[:3]]
    r.shuffle(choices)
    correct_idx = choices.index(str(good))
    return GeneratedItem(
        statement=f"Que vaut {a} + {b} × {c} ?",
        correction=f"Priorité à la multiplication : {a} + {b*c} = {good}",
        response_type="qcm_single",
        expected={"type": "choice", "correct": [correct_idx]},
        grading={"max_score": 1, "comparator": "qcm", "negative": 0},
        choices=choices,
    )


def gen_qcm_proportionnalite(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    k = r.randint(2, 3 + difficulty // 2)
    a = r.randint(2, 9)
    good = a * k
    choices = [str(good), str(good + k), str(good - a), str(a + k)]
    r.shuffle(choices)
    return GeneratedItem(
        statement=f"Un tableau de proportionnalité fait correspondre {a} → ? avec un coefficient {k}.",
        correction=f"{a} × {k} = {good}",
        response_type="qcm_single",
        expected={"type": "choice", "correct": [choices.index(str(good))]},
        grading={"max_score": 1, "comparator": "qcm", "negative": 0},
        choices=choices,
    )


def gen_developpement(seed: int, difficulty: int) -> GeneratedItem:
    r = _rng(seed)
    a = r.randint(2, 2 + difficulty // 2)
    b = r.randint(1, 9)
    return GeneratedItem(
        statement=f"Développer et réduire : {a}(x + {b})",
        correction=f"{a}(x + {b}) = {a}x + {a*b}",
        response_type="short_text",
        expected={"type": "expression", "value": f"{a}*x + {a*b}", "variable": "x"},
        grading={"max_score": 2, "comparator": "symbolic_equiv"},
    )


GENERATORS = {
    "builtin:add_relatifs": ("Addition de nombres relatifs", gen_addition_relatifs, "short_text", "N1"),
    "builtin:mult_relatifs": ("Multiplication de nombres relatifs", gen_multiplication_relatifs, "short_text", "N2"),
    "builtin:frac_somme": ("Somme de fractions", gen_fraction_somme, "short_text", "N3"),
    "builtin:eq_1d": ("Équation du premier degré", gen_equation_1er_degre, "multiline_text", "A2"),
    "builtin:qcm_priorites": ("Priorités opératoires (QCM)", gen_qcm_priorites, "qcm_single", "N4"),
    "builtin:qcm_proportion": ("Proportionnalité (QCM)", gen_qcm_proportionnalite, "qcm_single", "P1"),
    "builtin:developpement": ("Développement", gen_developpement, "short_text", "A1"),
}


def generate(provider_ref: str, seed: int, difficulty: int) -> GeneratedItem:
    if provider_ref.startswith("mathalea:"):
        from . import mathalea_client
        data = mathalea_client.generate(provider_ref.split(":", 1)[1], seed)
        return GeneratedItem(
            statement=data["statement"], correction=data["correction"],
            response_type=data["response_type"], expected=data["expected"],
            grading=data["grading"])
    if provider_ref not in GENERATORS:
        raise ValueError(f"Exercice inconnu : {provider_ref}")
    _, fn, _, _ = GENERATORS[provider_ref]
    return fn(seed, max(1, min(10, difficulty)))

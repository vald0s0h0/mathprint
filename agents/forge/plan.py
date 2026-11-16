"""Plan d'un run : combien de familles, lesquelles, avec quelle intention.

Le script décide ce que le modèle n'a pas à deviner :

  - le **volume**, d'après le verbe de la compétence et sa durée (un savoir à
    restituer ne demande pas autant d'exercices qu'un savoir-faire) ;
  - le **format** et le **niveau** de chaque emplacement ;
  - l'**archétype** de chaque emplacement (application, représentation, ordre de
    grandeur, document, décision…), pour que la banque ne soit pas une suite de
    « Calcule ». Les élèves fictifs (une copie à corriger, deux élèves en désaccord)
    se répartissent sur la LEÇON, 2 ou 3 en tout (`lesson_debates`) : trop nombreux,
    ils fatiguent et embrouillent ;
  - l'**idée** donnée à certains emplacements, quand la leçon en a (blocs `yaml idees`).

Un emplacement est une famille : il s'imprime en 4 ou 6 jumelles.
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

LEVEL_NAMES = {1: "facile", 2: "moyen", 3: "difficile"}
# Élèves fictifs par leçon : 2, et 3 à partir de ce nombre de compétences qui en accueillent.
DEBATES = (2, 3)
DEBATES_THIRD_FROM = 6
# Au plus cette part des emplacements reçoit une idée tirée d'une source : au-delà,
# un manuel dicterait toute la banque.
IDEA_SHARE = 0.4
PROBLEMS_PER_LESSON = 4

# Nature d'une compétence d'après le verbe de son libellé.
VERBS = {
    "savoir": ("connaitre", "identifier", "reconnaitre", "noter", "nommer", "citer", "definir",
               "distinguer", "enoncer"),
    "raisonnement": ("justifier", "demontrer", "interpreter", "prouver", "argumenter", "expliquer",
                     "analyser", "critiquer"),
    "situation": ("traiter",),
}
# (niveau, format, taille, archétype), dans l'ordre où l'on prend les emplacements.
PLANS = {
    "savoir": ((1, "qcm_single", "XS", "CALC"), (1, "matching", "S", "REPR"),
               (2, "checkbox_grid", "M", "CONTRE")),
    "savoir_faire": ((1, "qcm_single", "XS", "CALC"), (2, "short_text", "S", "CALC"),
                     (2, "table_fill", "L", "REPR"), (2, "qcm_single", "M", "ESTIM"),
                     (3, "multiline_text", "L", "INVERSE"), (2, "short_text", "M", "DOC"),
                     (3, "multiline_text", "L", "DECIDE")),
    "raisonnement": ((2, "checkbox_grid", "M", "CONTRE"), (2, "qcm_single", "M", "REPR"),
                     (3, "multiline_text", "L", "CALC"), (3, "multiline_text", "L", "INVERSE")),
}
ARCHETYPES = {
    "CALC": "application directe du savoir-faire",
    "REPR": "passer d'une représentation à une autre (tableau, graphique, formule, figure, schéma)",
    "ERR": "une copie d'élève contient une erreur : la trouver, la corriger",
    "QUI": "deux élèves ne sont pas d'accord : qui a raison, et pourquoi",
    "CONTRE": "vrai ou faux, avec un contre-exemple quand c'est faux",
    "INVERSE": "le résultat est donné, il faut retrouver la donnée",
    "ESTIM": "ordre de grandeur : ce résultat est-il possible ?",
    "DOC": "lire un document : ticket, facture, horaires, étiquette, relevé",
    "DECIDE": "prendre une décision que le bon sens seul ne permet pas",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def nature(label: str) -> str:
    """savoir, savoir_faire, raisonnement ou situation, d'après le premier mot du libellé."""
    words = re.findall(r"[a-zà-ÿ]+", _fold(label or ""))
    verb = words[0] if words else ""
    for name, verbs in VERBS.items():
        if verb in verbs:
            return name
    return "savoir_faire"


def family_count(label: str, minutes: int | None = None) -> int:
    """Socle d'une compétence, en familles. La durée module un savoir-faire : sans
    durée, la valeur médiane."""
    kind = nature(label)
    if kind == "savoir":
        return 2
    if kind == "raisonnement":
        return 4
    if kind == "situation":
        return 0                      # une situation se traite en problèmes, par leçon
    if minutes is None:
        return 6
    return 5 if minutes <= 15 else (6 if minutes <= 30 else 7)


def lesson_debates(comps: list[tuple[str, str, int | None]], lesson: str) -> dict[tuple[str, int], str]:
    """{(compétence, rang de l'emplacement): "ERR" ou "QUI"} pour toute la leçon.
    `comps` : (code, libellé, minutes). Seuls un savoir-faire ou un raisonnement en
    accueillent, sur un emplacement moyen ou difficile, jamais le premier ; une
    compétence au plus chacune. Déterministe : la même leçon donne le même choix,
    qu'on la prépare entière ou compétence par compétence."""
    hosts = []
    for code, label, minutes in comps:
        if nature(label) not in ("savoir_faire", "raisonnement"):
            continue
        plan = PLANS[nature(label)][:family_count(label, minutes)]
        ranks = [i for i, (level, _rtype, _size, _arch) in enumerate(plan) if i > 0 and level >= 2]
        if ranks:
            hosts.append((code, ranks))
    if not hosts:
        return {}
    rng = random.Random(f"{lesson}/debats")
    wanted = DEBATES[1] if len(hosts) >= DEBATES_THIRD_FROM else DEBATES[0]
    picked = rng.sample(hosts, min(wanted, len(hosts)))
    picked.sort(key=lambda h: [c for c, _l, _m in comps].index(h[0]))
    return {(code, rng.choice(ranks)): ("ERR" if i % 2 == 0 else "QUI")
            for i, (code, ranks) in enumerate(picked)}


def debate_slot(slot: dict, archetype: str) -> dict:
    """Un emplacement devenu « élève fictif » : un format où l'erreur se lit d'un coup
    d'œil (QCM simple ou réponse courte), jamais un QCM multiple ni un tableau."""
    rtype = "multiline_text" if slot["level"] == 3 else "qcm_single"
    return {**slot, "archetype": archetype, "response_type": rtype,
            "size": "L" if rtype == "multiline_text" else "M"}


def family_grid(code: str, label: str, minutes: int | None, *, figures: float = 0.2,
                traces: str = "aucun", count: int | None = None, skip: int = 0,
                debates: dict[tuple[str, int], str] | None = None) -> list[dict]:
    """Emplacements d'une compétence. `count`, `skip` : une partie seulement de la
    grille. `debates` : les élèves fictifs de la leçon (`lesson_debates`).
    Déterministe : même appel, même grille."""
    kind = nature(label)
    plan = list(PLANS.get(kind, PLANS["savoir_faire"]))
    total = family_count(label, minutes)
    rng = random.Random(f"{code}/plan")
    if kind == "savoir":
        plan = plan[rng.randrange(len(plan)):] + plan      # un savoir : format varié d'un contenu à l'autre
    plan = plan[:total]
    if traces == "central":
        plan = [(1, "geo_construction", "M", "CALC"), *plan[1:-1], (2, "geo_construction", "L", "CALC")][:max(total, 2)]
    elif traces == "partiel" and plan:
        plan = [*plan[:-1], (2, "geo_construction", "L", "CALC")]
    chosen = plan[skip:skip + (count if count is not None else len(plan))]
    with_figure = {i for i in range(len(chosen)) if rng.random() < figures}
    grid = []
    for i, (level, rtype, size, archetype) in enumerate(chosen):
        slot = {"slot": f"s{skip + i + 1:02d}", "kind": "exercise", "competency": code, "level": level,
                "difficulty": LEVEL_NAMES[level], "response_type": rtype, "size": size,
                "figure": i in with_figure and rtype != "geo_construction", "archetype": archetype}
        if rtype == "geo_construction":
            slot.update(trace_mode="quadrillage" if level == 1 else "libre", figure=False)
        elif (code, skip + i) in (debates or {}):
            slot = debate_slot(slot, debates[(code, skip + i)])
        grid.append(slot)
    return grid


def problem_slots(codes: list[str], count: int = PROBLEMS_PER_LESSON) -> list[dict]:
    """Problèmes d'une leçon : une situation, 2 à 4 parties, au moins 2 compétences."""
    levels = ([1, 2, 2, 3] * count)[:count]
    parts = {1: 2, 2: 3, 3: 4}
    out = []
    for i, level in enumerate(levels):
        wanted = max(2, min(len(codes), parts[level])) if len(codes) >= 2 else 1
        out.append({"slot": f"p{i + 1:02d}", "kind": "problem",
                    "competencies": [codes[(i + k) % len(codes)] for k in range(wanted)],
                    "level": level, "difficulty": LEVEL_NAMES[level], "response_type": "composite",
                    "parts": parts[level], "size": "L", "figure": i % 2 == 0,
                    "archetype": "DECIDE" if i % 2 else "DOC"})
    return out


# ------------------------------------------------------------------ idées
def load_ideas(lesson: Path, codes: list[str]) -> list[dict]:
    """Idées d'une leçon : les blocs ```yaml idees``` de ses sections de compétence,
    écrits par `forge lecon`."""
    if not lesson.is_file():
        return []
    import lecon
    return [i for i in lecon.ideas(lesson.read_text(encoding="utf-8"), codes)
            if i.get("id") and i.get("mecanisme")]


def assign_ideas(grid: list[dict], ideas: list[dict], code: str) -> list[dict]:
    """Donne une idée à une partie des emplacements : celles de la compétence, jamais
    deux fois la même, jamais sur une application directe (elle n'en a pas besoin)."""
    mine = [i for i in ideas if code in (i.get("competences") or [])]
    if not mine:
        return grid
    limit = math.ceil(IDEA_SHARE * len(grid))
    open_slots = [s for s in grid if s.get("archetype") != "CALC"] or grid
    rng = random.Random(f"{code}/idees")
    rng.shuffle(mine)
    for slot, idea in zip(open_slots[:limit], mine):
        slot["idee"] = {"id": idea["id"], "mecanisme": idea["mecanisme"],
                        "ne_pas_reprendre": idea.get("ne_pas_reprendre") or ""}
    return grid


# ------------------------------------------------------------------ calendrier
def school_week(today: date, start: date) -> int:
    """Semaine de l'année scolaire (1 = semaine de la rentrée)."""
    return (today - start).days // 7 + 1


def default_start(today: date) -> date:
    """Rentrée par défaut : le troisième lundi de septembre de l'année scolaire en cours."""
    year = today.year if today.month >= 8 else today.year - 1
    first = date(year, 9, 1)
    monday = first + timedelta(days=(7 - first.weekday()) % 7)
    return monday + timedelta(days=14)


def upcoming(lessons: list[dict], week: int, ahead: int = 3) -> list[dict]:
    """Leçons qui commencent dans les `ahead` semaines à venir (ou viennent de commencer)."""
    return sorted((l for l in lessons if l.get("week") and week - 1 <= l["week"] <= week + ahead),
                  key=lambda l: (l["week"], l.get("level", ""), l.get("code", "")))

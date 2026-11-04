"""Composition automatique d'une feuille à partir des cartes choisies.

`pack_best` répond à la question posée à chaque clic de l'assistant : ces cartes
tiennent-elles dans ces pages ? `frontend/src/pages/subjects/packEngine.ts` en est
le miroir exact, pour griser les cartes sans aller-retour réseau ; le serveur
refait le calcul à l'enregistrement. test_sheet_engine vérifie que les deux
donnent le même résultat.

`mix_columns` compose ensuite la feuille de CHAQUE élève : cartes dans un ordre
propre à sa copie, du plus simple au plus difficile, avec une tolérance d'une
carte entre deux niveaux, et les problèmes en dernier.

Les hauteurs passées à ces fonctions sont les plus grandes qu'une carte peut
atteindre (guide imprimé), arrondies par `measured`. Une feuille qui tient avec
ces hauteurs tient donc pour tous les élèves.
"""
import math
import random

from .pdfgen import column_capacity, pack_columns

SEARCH_BUDGET = 4000        # nœuds par recherche de sous-ensemble
FULL_ENOUGH_PT = 0.5        # moins d'un demi-point libre : colonne jugée pleine
MIX_ATTEMPTS = 40
PROBLEM_TIER = 10           # cf. pdfgen.reading_tier


def measured(height: float) -> float:
    """Hauteur arrondie au dixième supérieur. Le navigateur et le serveur calculent
    sur la même valeur, et la carte réelle n'est jamais plus haute."""
    return math.ceil(height * 10 - 1e-9) / 10


def _crosses_problems(lower: int, upper: int) -> bool:
    return lower < PROBLEM_TIER <= upper


def _best_subset(items: list[int], heights: list[float], room: float) -> list[int]:
    """Sous-ensemble de `items` (hauteurs décroissantes) qui remplit le mieux `room`."""
    suffix = [0.0] * (len(items) + 1)
    for i in range(len(items) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + heights[items[i]]
    best: list[int] = []
    best_sum = 0.0
    nodes = 0
    picked: list[int] = []

    def go(i: int, total: float) -> None:
        nonlocal best, best_sum, nodes
        if total > best_sum:
            best_sum, best = total, picked[:]
        if (i >= len(items) or nodes >= SEARCH_BUDGET
                or best_sum >= room - FULL_ENOUGH_PT or total + suffix[i] <= best_sum):
            return
        nodes += 1
        height = heights[items[i]]
        if total + height <= room:
            picked.append(items[i])
            go(i + 1, total + height)
            picked.pop()
        go(i + 1, total)

    go(0, 0.0)
    return best


def _pack_by_slack(heights: list[float], tiers: list[int],
                   n_columns: int) -> list[list[int]] | None:
    """Remplit chaque colonne au plus juste, palier après palier, quand le rangement
    First-Fit-Decreasing de pdfgen n'y arrive pas. Si le palier courant n'a plus
    qu'une carte et qu'elle ne tient pas, une carte du palier suivant peut combler
    le trou : c'est la tolérance d'une carte, jamais entre exercices et problèmes."""
    levels = sorted(set(tiers))
    groups = [sorted((i for i in range(len(heights)) if tiers[i] == t),
                     key=lambda i: (-heights[i], i)) for t in levels]
    g = 0
    columns: list[list[int]] = []
    for b in range(n_columns):
        room = column_capacity(b)
        column: list[int] = []
        while g < len(groups):
            group = groups[g]
            chosen = _best_subset(group, heights, room)
            column.extend(chosen)
            room -= sum(heights[i] for i in chosen)
            groups[g] = [i for i in group if i not in chosen]
            if groups[g]:
                break
            g += 1
        if (g + 1 < len(groups) and len(groups[g]) == 1
                and not _crosses_problems(levels[g], levels[g + 1])):
            filler = next((i for i in groups[g + 1] if heights[i] <= room), None)
            if filler is not None:
                column.append(filler)
                groups[g + 1] = [i for i in groups[g + 1] if i != filler]
        columns.append(column)
        if not any(groups):
            return columns
    return None


def pack_best(heights: list[float], tiers: list[int], pages: int) -> list[list[int]] | None:
    """Colonnes (index des cartes, ordre de lecture) qui font tenir ces cartes dans
    `pages` pages, ou None. Ne contient pas de colonnes au-delà de la dernière occupée."""
    if not heights:
        return []
    n_columns = 2 * max(1, pages)
    if max(heights) > column_capacity(2):
        return None
    columns = pack_columns(heights, tiers)
    if len(columns) <= n_columns:
        return columns
    packed = _pack_by_slack(heights, tiers, n_columns)
    if packed is None:
        return None
    return [sorted(col, key=lambda i: tiers[i]) for col in packed]


def _mixed_reading_order(tiers: list[int], rng: random.Random) -> list[int]:
    """Ordre tiré au hasard dans chaque palier. Une fois sur deux, les deux cartes
    voisines de part et d'autre d'une frontière de paliers s'échangent."""
    order: list[int] = []
    ends: list[int] = []
    levels = sorted(set(tiers))
    for tier in levels:
        members = [i for i, t in enumerate(tiers) if t == tier]
        rng.shuffle(members)
        order.extend(members)
        ends.append(len(order) - 1)
    last_swap = -2
    for k, end in enumerate(ends[:-1]):
        # un palier d'une seule carte déjà déplacée ne bouge pas une seconde fois
        if (end > last_swap + 1 and not _crosses_problems(levels[k], levels[k + 1])
                and rng.random() < 0.5):
            order[end], order[end + 1] = order[end + 1], order[end]
            last_swap = end
    return order


def _fill_in_order(order: list[int], heights: list[float], tiers: list[int],
                   n_columns: int) -> list[list[int]] | None:
    """Pose les cartes dans cet ordre, colonne après colonne. Si la suivante ne
    tient pas, une carte du même palier qui la suit immédiatement peut passer
    devant pour éviter un trou en bas de colonne."""
    queue = list(order)
    columns: list[list[int]] = [[]]
    room = column_capacity(0)
    while queue:
        taken = None
        for k, card in enumerate(queue):
            if tiers[card] != tiers[queue[0]]:
                break
            if heights[card] <= room:
                taken = k
                break
        if taken is None:
            if len(columns) == n_columns:
                return None
            columns.append([])
            room = column_capacity(len(columns) - 1)
            continue
        card = queue.pop(taken)
        columns[-1].append(card)
        room -= heights[card]
    return columns


def _shuffle_inside_columns(columns: list[list[int]], tiers: list[int],
                            rng: random.Random) -> list[list[int]]:
    """Dernier recours sur une feuille très remplie : les cartes restent dans leur
    colonne et changent d'ordre, ce qui ne modifie aucune hauteur."""
    mixed = []
    for column in columns:
        cards: list[int] = []
        k = 0
        while k < len(column):
            end = k
            while end < len(column) and tiers[column[end]] == tiers[column[k]]:
                end += 1
            run = column[k:end]
            rng.shuffle(run)
            cards.extend(run)
            k = end
        mixed.append(cards)
    return mixed


def mix_columns(heights: list[float], tiers: list[int], pages: int,
                rng: random.Random) -> list[list[int]] | None:
    """Colonnes de la feuille d'UN élève, ou None si les cartes ne tiennent pas."""
    if not heights:
        return []
    n_columns = 2 * max(1, pages)
    for _ in range(MIX_ATTEMPTS):
        columns = _fill_in_order(_mixed_reading_order(tiers, rng), heights, tiers, n_columns)
        if columns is not None:
            return columns
    reference = pack_best(heights, tiers, pages)
    return None if reference is None else _shuffle_inside_columns(reference, tiers, rng)

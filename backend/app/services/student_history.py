"""Choix des exercices d'un sujet INDIVIDUEL, sur l'historique réel de l'élève.

Déterministe, sans le moindre appel LLM (RM-009). Tout ce qui est nécessaire est
déjà en base après chaque correction :

    copy_item_results   quel exercice, quel dérivé, quelle réponse, quel jour
    copy_items          ce qui a été SERVI (même si la copie n'est jamais revenue)
    student_competency_state  maîtrise et stabilité (services.forgetting)

Ce module remplace la « trame » que le LLM écrivait à la fin de chaque
correction. Une trame écrite à ce moment-là pariait sur une date de sujet
suivant que personne ne connaissait, alors que c'est exactement le délai écoulé
qui décide de ce qu'il faut retravailler : on la calcule donc ICI, au moment de
composer le sujet, avec la vraie date sous les yeux.

Trois décisions, dans cet ordre :

  1. COMBIEN de chaque dérivé — `level_quota`, d'après le niveau 1-10 de
     l'élève. Un mix, jamais un palier unique : trois dérivés ne se déduisent
     pas d'une échelle sur dix.
  2. QUELLE compétence pour chaque case — couverture uniforme d'abord (une par
     compétence cochée), puis surpondération des plus prioritaires.
  3. QUEL exercice concret — `rank_candidates` : inédit d'abord, puis un raté
     assez ancien pour ne pas être récité de mémoire, le déjà-réussi en dernier.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Copy, CopyItem, CopyItemResult, GeneratedExercise
from . import distribution, forgetting

# Mix de dérivés (facile, base, difficile) par niveau d'élève 1-10. Chaque ligne
# somme à 1.
#
# Le niveau d'un élève est noté sur 10, les dérivés d'un exercice sont trois :
# la traduction ne peut pas être un palier unique, sinon un élève de niveau 4 ne
# verrait QUE du facile et n'aurait jamais l'occasion de progresser, tandis qu'un
# élève de niveau 9 ne verrait QUE du difficile et n'aurait plus une seule
# réussite facile pour s'installer. Tout le monde reçoit donc les trois, dans des
# proportions qui glissent : c'est le mix qui porte le niveau, pas la carte.
LEVEL_MIX: dict[int, tuple[float, float, float]] = {
    1:  (0.75, 0.25, 0.00),
    2:  (0.70, 0.30, 0.00),
    3:  (0.60, 0.35, 0.05),
    4:  (0.50, 0.40, 0.10),
    5:  (0.35, 0.50, 0.15),
    6:  (0.25, 0.55, 0.20),
    7:  (0.15, 0.55, 0.30),
    8:  (0.10, 0.50, 0.40),
    9:  (0.05, 0.45, 0.50),
    10: (0.00, 0.40, 0.60),
}

# Bornes d'appariement compétence ↔ dérivé (cf. _assign_levels). Une lacune ne
# se teste pas en « difficile », une compétence solide ne se retravaille pas en
# « facile » : dans les deux cas la carte est du papier perdu.
SOLID_STRENGTH = 0.75
FRAGILE_STRENGTH = 0.40


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------- l'historique

@dataclass
class Seen:
    """Ce que l'élève a déjà eu sous les yeux pour UN exercice de banque."""
    last_at: datetime | None = None
    attempts: int = 0
    best_ratio: float = 0.0
    graded: bool = False        # False = servi mais jamais corrigé (copie absente)


def exercise_log(db: Session, student_id: str) -> dict[str, Seen]:
    """Tout ce qui a été servi à l'élève, par exercice de banque.

    La source est `copy_items` et non `copy_item_results` : un exercice imprimé
    sur une copie jamais rendue a quand même été vu, et le resservir tel quel
    au sujet suivant serait une répétition visible. Les résultats viennent
    ensuite l'enrichir quand ils existent.

    Les lignes antérieures à `CopyItem.generated_exercise_id` n'ont pas
    d'exercice identifiable (l'information n'a jamais été écrite) : elles sont
    simplement absentes du journal, donc sans effet sur l'anti-répétition."""
    log: dict[str, Seen] = {}
    rows = (db.query(CopyItem.generated_exercise_id, Copy.generated_at)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .filter(Copy.student_id == student_id,
                    CopyItem.generated_exercise_id.isnot(None),
                    # une colonne ajoutée par ALTER TABLE peut porter la chaîne
                    # vide : « pas d'exercice », pas un exercice nommé ""
                    CopyItem.generated_exercise_id != "").all())
    for ex_id, generated_at in rows:
        seen = log.setdefault(ex_id, Seen())
        seen.attempts += 1
        at = _utc(generated_at)
        if at and (seen.last_at is None or at > seen.last_at):
            seen.last_at = at

    results = (db.query(CopyItemResult)
               .filter(CopyItemResult.student_id == student_id,
                       CopyItemResult.generated_exercise_id.isnot(None),
                       CopyItemResult.generated_exercise_id != "").all())
    for r in results:
        seen = log.setdefault(r.generated_exercise_id, Seen())
        seen.graded = True
        seen.best_ratio = max(seen.best_ratio, r.success_ratio or 0.0)
        at = _utc(r.occurred_at)
        if at and (seen.last_at is None or at > seen.last_at):
            seen.last_at = at
    return log


@dataclass
class Stats:
    """Ce qu'on sait d'un élève sur UNE compétence, au moment de composer."""
    competency_id: str
    priority: float = 1.0           # 1 = à traiter d'urgence (jamais évaluée)
    strength: float = 0.0           # maîtrise fraîche
    mastery: float = 0.0
    last_level: int = 0             # dernier dérivé tenté (0 = aucun)
    last_ratio: float = 0.0


def competency_stats(db: Session, student_id: str,
                     competency_ids: list[str],
                     at: datetime | None = None) -> dict[str, Stats]:
    """Profil de l'élève sur les compétences cochées : état de mémorisation
    (services.forgetting) + ce que dit l'historique d'exercices."""
    at = at or datetime.now(timezone.utc)
    states = distribution.competency_states(db, student_id, competency_ids)
    out = {cid: Stats(competency_id=cid) for cid in competency_ids}
    for cid, stats in out.items():
        state = states.get(cid)
        stats.priority = forgetting.priority(state, at)
        if state is not None:
            stats.strength = forgetting.strength(state, at)
            stats.mastery = state.mastery
    if not competency_ids:
        return out

    rows = (db.query(CopyItemResult)
            .filter(CopyItemResult.student_id == student_id,
                    CopyItemResult.competency_id.in_(competency_ids))
            .order_by(CopyItemResult.occurred_at).all())
    for r in rows:
        stats = out.get(r.competency_id)
        if stats is None:
            continue
        if r.difficulty_level:
            stats.last_level = r.difficulty_level
            stats.last_ratio = r.success_ratio or 0.0
    return out


# ----------------------------------------------------- combien de chaque dérivé

def level_quota(student_level_1_10: int, n_slots: int) -> dict[int, int]:
    """Répartit `n_slots` cartes entre les trois dérivés, selon LEVEL_MIX.

    Méthode du plus fort reste : les entiers somment TOUJOURS exactement à
    `n_slots` (un arrondi naïf en perd ou en invente un, et la copie finit avec
    une carte de trop ou de moins)."""
    if n_slots <= 0:
        return {1: 0, 2: 0, 3: 0}
    mix = LEVEL_MIX[max(1, min(10, int(student_level_1_10 or 5)))]
    exact = [m * n_slots for m in mix]
    quota = {lvl: int(math.floor(exact[lvl - 1])) for lvl in (1, 2, 3)}
    remainder = n_slots - sum(quota.values())
    # les plus fortes décimales d'abord ; à égalité, le dérivé le plus bas
    order = sorted((1, 2, 3), key=lambda lvl: (-(exact[lvl - 1] % 1), lvl))
    for lvl in order[:remainder]:
        quota[lvl] += 1
    return quota


# ------------------------------------------------ quelle compétence, quel dérivé

def _weighted_order(stats: dict[str, Stats], competency_ids: list[str],
                    n_slots: int) -> list[str]:
    """Répartit `n_slots` cases entre les compétences PROPORTIONNELLEMENT à leur
    priorité (méthode du diviseur, déterministe).

    Le remplissage tournait auparavant en simple tour de rôle : une compétence
    acquise recevait exactement autant d'exercices qu'une lacune. Ici une
    compétence à priorité 0,9 en reçoit environ trois fois plus qu'une à 0,3 —
    sans jamais en affamer aucune, puisque le diviseur croît avec le nombre de
    cases déjà attribuées."""
    if not competency_ids or n_slots <= 0:
        return []
    counts = {cid: 0 for cid in competency_ids}
    order: list[str] = []
    for _ in range(n_slots):
        # +0.05 : une compétence de priorité nulle doit rester atteignable,
        # sinon une classe entièrement à jour ne se verrait plus rien attribuer.
        cid = max(competency_ids,
                  key=lambda c: ((stats[c].priority + 0.05) / (2 * counts[c] + 1),
                                 -competency_ids.index(c)))
        counts[cid] += 1
        order.append(cid)
    return order


def _level_for(stats: Stats, wanted: int) -> int:
    """Dérivé effectivement servi à une compétence pour une case de niveau
    `wanted`, après garde-fous.

    Le quota dit COMBIEN de chaque dérivé ; ces bornes disent à QUI. On ne teste
    pas une lacune en « difficile » (échec garanti, aucune information) et on ne
    retravaille pas une compétence solide en « facile » (réussite garantie,
    aucune information). On ne saute pas non plus deux crans d'un coup."""
    level = wanted
    if stats.strength < FRAGILE_STRENGTH:
        level = min(level, 2)
    if stats.strength > SOLID_STRENGTH:
        level = max(level, 2)
    if stats.last_level:
        level = min(level, stats.last_level + 1)
        # dernier essai raté à ce niveau : on redescend d'un cran
        if stats.last_ratio < settings.history_success_threshold:
            level = min(level, max(1, stats.last_level - 1))
    return max(1, min(3, level))


@dataclass
class Slot:
    """Une case de la copie : quelle compétence, quel dérivé."""
    competency_id: str
    level3: int


def _assign_levels(stats: dict[str, Stats], order: list[str],
                   quota: dict[int, int]) -> list[Slot]:
    """Marie les cases (dans l'ordre des compétences) aux dérivés du quota.

    Les compétences les plus PRIORITAIRES prennent les dérivés les plus faciles,
    les mieux tenues les plus difficiles : c'est le sens pédagogique de la
    manœuvre, et ça tombe juste, l'ordre étant déjà celui de la priorité."""
    pool: list[int] = []
    for lvl in (1, 2, 3):
        pool.extend([lvl] * quota.get(lvl, 0))
    # ordre = priorité décroissante ; pool = du plus facile au plus difficile
    slots = [Slot(competency_id=cid, level3=lvl) for cid, lvl in zip(order, pool)]
    # order plus long que pool (arrondis) : les cases restantes prennent le
    # dérivé de base, jamais rien d'extrême.
    slots.extend(Slot(competency_id=cid, level3=2) for cid in order[len(pool):])
    for slot in slots:
        slot.level3 = _level_for(stats[slot.competency_id], slot.level3)
    return slots


def student_plan(db: Session, student_id: str, competency_ids: list[str],
                 student_level_1_10: int, n_slots: int,
                 at: datetime | None = None) -> tuple[list[Slot], dict[str, Stats]]:
    """Trame de la copie : la liste ordonnée des cases à remplir.

    Les `len(competency_ids)` premières cases couvrent UNE FOIS CHACUNE toutes
    les compétences cochées — le périmètre choisi par le professeur est un
    contrat, jamais rétréci par la personnalisation, même pour une compétence
    parfaitement acquise. Les cases suivantes vont aux plus prioritaires.

    `n_slots` n'est qu'une estimation (le remplissage réel dépend de la hauteur
    des cartes) : la liste est volontairement plus longue que nécessaire, la
    génération s'arrête quand la page est pleine."""
    ordered = [cid for cid in competency_ids]
    stats = competency_stats(db, student_id, ordered, at)
    by_priority = sorted(ordered, key=lambda cid: (-stats[cid].priority,
                                                   ordered.index(cid)))
    n_slots = max(n_slots, len(ordered))
    quota = level_quota(student_level_1_10, n_slots)
    order = by_priority + _weighted_order(stats, by_priority, n_slots - len(ordered))
    return _assign_levels(stats, order, quota), stats


# ------------------------------------------------------- quel exercice concret

def candidate_rank(row: GeneratedExercise, log: dict[str, Seen],
                   at: datetime | None = None) -> tuple[int, float]:
    """Rang d'un exercice pour cet élève — plus petit = meilleur candidat.

      0. jamais servi ;
      1. servi et RATÉ il y a assez longtemps : c'est la lacune à reprendre, et
         le délai garantit qu'il sera refait, pas récité ;
      2. tout le reste (déjà réussi, ou raté trop récemment), du plus ancien au
         plus récent.

    Le second membre départage à l'intérieur d'un rang : ancienneté en jours,
    en négatif, pour que le plus ancien passe devant."""
    seen = log.get(row.id)
    if seen is None:
        return (0, 0.0)
    at = at or datetime.now(timezone.utc)
    days = ((at - seen.last_at).total_seconds() / 86400) if seen.last_at else 1e6
    failed = seen.graded and seen.best_ratio < settings.history_success_threshold
    if failed and days >= settings.history_replay_min_days:
        return (1, -days)
    return (2, -days)


def rank_candidates(rows: list[GeneratedExercise], log: dict[str, Seen],
                    at: datetime | None = None) -> list[GeneratedExercise]:
    """Banque d'une compétence triée du meilleur candidat au moins bon pour cet
    élève. Ne filtre rien : c'est un ORDRE de préférence, la sélection finale
    (mix des types de réponse, hauteur de carte) reste à l'appelant."""
    at = at or datetime.now(timezone.utc)
    return sorted(rows, key=lambda r: candidate_rank(r, log, at))


def preferred_rows(rows: list[GeneratedExercise], log: dict[str, Seen],
                   at: datetime | None = None) -> list[GeneratedExercise]:
    """Sous-ensemble des MEILLEURS candidats (le premier rang non vide).

    Sert à laisser `distribution.pick_balanced_exercise` équilibrer les types de
    réponse à l'intérieur du meilleur rang, plutôt que de lui donner la banque
    entière — sinon un exercice déjà servi, mais du bon type, passerait devant
    un inédit."""
    if not rows:
        return []
    at = at or datetime.now(timezone.utc)
    ranked = rank_candidates(rows, log, at)
    best = candidate_rank(ranked[0], log, at)[0]
    return [r for r in ranked if candidate_rank(r, log, at)[0] == best]

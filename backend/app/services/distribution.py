"""Répartition automatique des exercices par élève (étape Exercices de
l'assistant sujet) : priorité selon la courbe de l'oubli, difficulté selon
le mode d'adaptation, mix homogène des types de réponses au sein d'une
copie. Remplace les anciennes heuristiques fixes (suggestion 60/30/10,
remplissage par répétition sans diversité) — la sélection concrète des
exercices en banque reste celle de exercise_gen (ensure_bank/bank_rows_near_level),
jamais réinventée ici.
"""
from sqlalchemy.orm import Session

from ..models import GeneratedExercise, StudentCompetencyState
from . import exercise_gen, forgetting


def competency_states(db: Session, student_id: str,
                      competency_ids: list[str]) -> dict[str, StudentCompetencyState]:
    """États de maîtrise de l'élève pour les compétences cochées, indexés par id."""
    if not competency_ids:
        return {}
    return {
        s.competency_id: s
        for s in db.query(StudentCompetencyState).filter(
            StudentCompetencyState.student_id == student_id,
            StudentCompetencyState.competency_id.in_(competency_ids)).all()
    }


def priority_competencies(db: Session, student_id: str, competency_ids: list[str]) -> list[str]:
    """Trie les compétences cochées par priorité décroissante (cf.
    forgetting.priority) ; une compétence jamais évaluée revient en tête.

    La priorité combine la LACUNE et l'OUBLI (1 - maîtrise × fraîcheur). Le tri
    ne regardait auparavant que la fraîcheur : une compétence revue hier et
    ratée passait donc derrière une compétence acquise depuis longtemps, ce qui
    est exactement l'inverse de ce qu'il faut retravailler."""
    if not competency_ids:
        return []
    states = competency_states(db, student_id, competency_ids)
    return sorted(competency_ids,
                  key=lambda cid: -forgetting.priority(states.get(cid)))


def difficulty_level3(personalization_mode: str, student_level_1_10: int) -> int:
    """Niveau de banque (1-3) : neutre pour commun/variantes communes,
    adapté au niveau élève (±2 autour d'une base neutre) en individuel.

    Le ±2 porte sur l'échelle ÉLÈVE (1-10), pas sur celle des exercices : c'est la
    conversion finale qui a été ramenée de 5 à 3 niveaux (cf.
    exercise_gen.student_level_to_difficulty)."""
    base = 5
    if personalization_mode == "individual":
        delta = max(-2, min(2, student_level_1_10 - 5))
        base = max(1, min(10, base + delta))
    return exercise_gen.student_level_to_difficulty(base)


def variant_seed(base_seed: int, personalization_mode: str, student_index: int) -> int:
    """Seed de copie : commune à toute la classe, plafonnée à 3 variantes
    (anti-copie) pour "commun avec variantes", unique par élève sinon."""
    if personalization_mode == "common":
        return base_seed
    if personalization_mode == "common_variants":
        return base_seed + (student_index % 3) + 1
    return base_seed + student_index + 1


# ---------------------------------------------------------------- jumelles
TWIN_STATUS = "twin"


def twin_choice(position: int, jeux: int, dispositions: int) -> tuple[int, int]:
    """(jeu, disposition) de l'élève à cette place dans la liste de la classe, qui
    est l'ordre de distribution : deux voisins n'ont jamais le même jeu de valeurs.

    - 3 jeux, 2 dispositions : deux copies identiques sont à 6 places au moins ;
    - 2 jeux, 2 dispositions : le jeu alterne, la disposition change toutes les
      deux places ; deux copies identiques sont à 4 places ;
    - 1 disposition : les jeux se suivent."""
    jeux, dispositions = max(1, jeux), max(1, dispositions)
    jeu = position % jeux
    if dispositions == 1:
        return jeu + 1, 1
    if jeux % 2:
        return jeu + 1, position % 2 + 1
    return jeu + 1, (position // jeux) % 2 + 1


def twin_for(db: Session, row: GeneratedExercise, position: int,
             cache: dict | None = None) -> GeneratedExercise:
    """La jumelle de `row` pour l'élève à cette place. `row` est la carte choisie
    (la seule active de sa famille) ; sans jumelles, elle est rendue telle quelle."""
    if not row.twin_group:
        return row
    cache = cache if cache is not None else {}
    siblings = cache.get(row.twin_group)
    if siblings is None:
        siblings = {(r.twin_jeu, r.twin_disposition): r
                    for r in db.query(GeneratedExercise).filter(
                        GeneratedExercise.twin_group == row.twin_group,
                        GeneratedExercise.status.in_(("active", TWIN_STATUS))).all()}
        cache[row.twin_group] = siblings
    jeux = max((j for j, _d in siblings), default=1)
    dispositions = max((d for _j, d in siblings), default=1)
    return siblings.get(twin_choice(position, jeux, dispositions), row)


def family_heads(db: Session, exercise_ids) -> dict[str, str]:
    """{jumelle servie → carte active de sa famille}. Pour l'historique d'un élève :
    avoir vu une jumelle, c'est avoir vu la carte."""
    ids = [i for i in exercise_ids if i]
    if not ids:
        return {}
    twins = (db.query(GeneratedExercise.id, GeneratedExercise.twin_group)
             .filter(GeneratedExercise.id.in_(ids), GeneratedExercise.status == TWIN_STATUS).all())
    groups = {group for _id, group in twins if group}
    if not groups:
        return {}
    heads = dict(db.query(GeneratedExercise.twin_group, GeneratedExercise.id)
                 .filter(GeneratedExercise.twin_group.in_(groups),
                         GeneratedExercise.status == "active").all())
    return {twin_id: heads[group] for twin_id, group in twins if group in heads}


def reorder_class_from_scan(db: Session, batch_id: str) -> int:
    """La liste d'une classe suit l'ordre de son dernier lot scanné : les copies sont
    ramassées de proche en proche, donc deux élèves qui se suivent dans le lot sont
    voisins, et c'est sur cet ordre que les jumelles sont réparties au sujet suivant.

    Rien ne bouge si le lot mêle plusieurs classes ou couvre moins de la moitié de la
    classe (un rattrapage ne dit rien de la salle). Les élèves absents du lot gardent
    leur ordre, à la suite. Renvoie le nombre d'élèves replacés."""
    from ..models import Copy, DocumentPage, ScannedPage, Student
    rows = (db.query(Copy.student_id, ScannedPage.wave, ScannedPage.source_index)
            .join(DocumentPage, DocumentPage.copy_id == Copy.id)
            .join(ScannedPage, ScannedPage.page_id == DocumentPage.id)
            .filter(ScannedPage.batch_id == batch_id, ScannedPage.dismissed.is_(False))
            .order_by(ScannedPage.wave, ScannedPage.source_index).all())
    scanned: list[str] = []
    for student_id, _wave, _index in rows:
        if student_id not in scanned:
            scanned.append(student_id)
    if not scanned:
        return 0
    students = {s.id: s for s in db.query(Student).filter(Student.id.in_(scanned)).all()}
    classes = {s.class_id for s in students.values()}
    if len(classes) != 1 or None in classes:
        return 0
    roster = (db.query(Student).filter_by(class_id=classes.pop(), active=True)
              .order_by(Student.order_index, Student.id).all())
    if len(scanned) * 2 < len(roster):
        return 0
    present = [students[i] for i in scanned if i in students]
    absent = [s for s in roster if s.id not in students]
    moved = 0
    for position, student in enumerate(present + absent):
        if student.order_index != position:
            student.order_index = position
            moved += 1
    return moved


def exercise_bucket(row: GeneratedExercise) -> str:
    if row.response_type.startswith("qcm"):
        return "qcm"
    return row.kind or "application"


def exercise_identity(row: GeneratedExercise) -> str:
    """Identité d'un exercice pour l'anti-répétition DANS une copie.

    L'id de ligne ne suffit pas : la banque est constituée par compétence, et
    deux compétences voisines peuvent parfaitement avoir produit le MÊME
    exercice chacune de son côté (le dédoublonnage d'exercise_gen est, lui,
    par compétence — deux lignes distinctes, légitimes). Pour l'élève, c'est
    pourtant deux fois le même exercice sur sa copie. On compare donc le
    contenu, avec la même clé que la banque (_dedup_key)."""
    return exercise_gen._dedup_key(row.statement, row.expected_json,
                                   (row.grading_json or {}).get("choices"))


def pick_balanced_exercise(rows: list[GeneratedExercise], counts: dict[str, int],
                           target_mix: dict[str, float], seed: int,
                           exclude_keys: set[str] | None = None) -> GeneratedExercise:
    """Choisit, dans une banque déjà chargée pour une compétence × niveau,
    l'exercice du type le moins représenté par rapport au mix cible de la
    copie en cours ; sélection déterministe (seed) à l'intérieur du type
    retenu. Le compteur `counts` est mis à jour — à décrémenter par
    l'appelant (via exercise_bucket) si l'item est finalement retiré (ex.
    dépassement de la capacité de page).

    `exclude_keys` : identités (cf. exercise_identity) déjà servies dans la
    copie en cours — jamais re-piochées tant qu'il reste des exercices non
    utilisés (pas deux fois le même exercice dans un même sujet d'un élève).
    Repli sur l'ensemble complet si tous les exercices disponibles sont déjà
    exclus."""
    if not rows:
        raise ValueError("banque vide")
    if exclude_keys:
        available = [r for r in rows if exercise_identity(r) not in exclude_keys]
        rows = available or rows
    by_bucket: dict[str, list[GeneratedExercise]] = {}
    for r in rows:
        by_bucket.setdefault(exercise_bucket(r), []).append(r)
    total = sum(counts.values()) + 1

    def deficit(bucket: str) -> float:
        return target_mix.get(bucket, 0.0) - counts.get(bucket, 0) / total

    bucket = max(by_bucket, key=deficit)
    candidates = by_bucket[bucket]
    row = candidates[seed % len(candidates)]
    counts[bucket] = counts.get(bucket, 0) + 1
    return row


def pick_unused_exercise(rows: list[GeneratedExercise], seed: int,
                         exclude_keys: set[str] | None = None) -> GeneratedExercise | None:
    """Un exercice NON encore servi dans la copie (identité hors exclude_keys),
    déterministe par seed ; None si tous sont déjà servis. Contrairement à
    pick_balanced_exercise, ne se replie PAS sur l'ensemble complet : l'appelant
    (remplissage de bas de page) préfère s'arrêter plutôt que de répéter une
    même petite carte sur la copie d'un élève."""
    if not rows:
        return None
    available = ([r for r in rows if exercise_identity(r) not in exclude_keys]
                 if exclude_keys else list(rows))
    if not available:
        return None
    return available[seed % len(available)]

"""Courbe d'oubli déterministe et explicable (§7.5) — calculée sans LLM (RM-009).

Modèle simplifié inspiré de FSRS. Pour chaque couple élève-compétence on
PERSISTE trois choses, mises à jour à chaque correction :

    mastery       qualité du dernier rappel consolidé (0-1)
    stability     combien de jours la trace tient
    last_seen_at  JOUR DU DEVOIR (pas de la correction)

et on en DÉRIVE, à chaque lecture et avec la date du jour :

    freshness = exp(-Δjours / stability)      1 le jour même, → 0 avec le temps
    strength  = mastery × freshness           la maîtrise FRAÎCHE
    priority  = 1 - strength                  ce qu'il faut retravailler

C'est la séparation essentielle. Une valeur figée en base ne peut pas exprimer
la fraîcheur, qui dépend d'aujourd'hui : une compétence à 0,9 il y a trois mois
ne vaut plus 0,9 aujourd'hui, et c'est tout l'intérêt d'une courbe d'oubli. On
persiste donc les INGRÉDIENTS et on recalcule le score à la lecture.

Une seule formule couvre tous les cas, réussites comprises : un exercice réussi
avec une forte stabilité décroît lentement (priorité basse pendant des mois),
une compétence fragile décroît vite et remonte d'elle-même en tête de liste.
C'est `stability_ceiling` qui fait la différence entre les deux.
"""
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    CompetencyEvidence,
    CompetencyStateHistory,
    Student,
    StudentCompetencyState,
    StudentLevel,
)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def days_since(state: StudentCompetencyState, at: datetime | None = None) -> float | None:
    """Jours écoulés depuis le dernier devoir portant cette compétence."""
    last = _utc(state.last_seen_at)
    if last is None:
        return None
    at = at or datetime.now(timezone.utc)
    return max(0.0, (at - last).total_seconds() / 86400)


def freshness(state: StudentCompetencyState, at: datetime | None = None) -> float:
    """Fraîcheur de la trace : R(t) = exp(-Δjours / S), 1 le jour du devoir.

    Jamais persistée — elle dépend d'aujourd'hui, la figer en base reviendrait à
    supprimer la courbe d'oubli."""
    days = days_since(state, at)
    if days is None:
        return 0.0
    return math.exp(-days / max(0.1, state.stability))


# `recall_probability` est l'ancien nom de `freshness`, gardé parce qu'il est
# affiché tel quel dans l'écran Élève et le tableau de bord.
recall_probability = freshness


def strength(state: StudentCompetencyState, at: datetime | None = None) -> float:
    """Maîtrise FRAÎCHE : ce que l'élève saurait encore faire aujourd'hui.

    `mastery` seul ne vieillit jamais — une compétence à 0,9 il y a trois mois se
    relit 0,9 aujourd'hui. C'est cette valeur-ci, et non `mastery`, qui dit l'état
    réel."""
    return _clamp01(state.mastery) * freshness(state, at)


def priority(state: StudentCompetencyState | None, at: datetime | None = None) -> float:
    """Priorité de travail 0-1 (1 = à traiter d'urgence). Une compétence jamais
    évaluée vaut 1 : on ne peut rien affirmer, donc on la voit en premier.

    Score unique et continu, valable pour TOUT — y compris les réussites, qui
    remontent d'elles-mêmes quand leur fraîcheur baisse. Il remplace le verdict
    binaire « due / pas due », dont la falaise à 0,80 rendait deux compétences
    très inégales indiscernables."""
    if state is None:
        return 1.0
    return 1.0 - strength(state, at)


# --- bornes de la stabilité -------------------------------------------------
# La stabilité était plafonnée à 3650 jours pour tout le monde. C'était un
# garde-fou contre l'OverflowError (elle était multipliée à chaque succès et
# croissait sans borne : la date due finissait au-delà de l'an 9999, d'où le 500
# « Valider la correction » vu en production), pas une règle pédagogique : une
# compétence à moitié acquise obtenait la même fenêtre de rappel qu'une
# compétence sue par cœur.
#
# Le plafond dépend désormais de la MAÎTRISE. C'est lui qui réalise « les
# acquises reviennent dans longtemps, les fragiles dans une fenêtre courte » :
#   m=0,3 → 11 j · 0,5 → 46 j · 0,7 → 126 j · 0,9 → 266 j · 1,0 → 365 j
# Le cube creuse volontairement le bas de l'échelle : une compétence à moitié
# maîtrisée doit revenir dans le mois qui suit, pas dans un trimestre.
S_MIN = 1.0
S_CEIL = 365.0
MAX_DUE_DAYS = 3650.0   # ceinture de sécurité sur l'arithmétique de date
# Gain maximal de stabilité pour un rappel réussi (cf. apply_evidence).
GAIN_MAX = 2.5
# Au-dessus, le rappel compte comme réussi.
SUCCESS_Q = 0.6


def stability_ceiling(mastery: float) -> float:
    """Fenêtre de rappel maximale (jours) autorisée par la maîtrise. Une
    compétence à moitié maîtrisée ne peut PAS obtenir un an de stabilité, même
    après dix succès d'affilée."""
    return S_MIN + (S_CEIL - S_MIN) * _clamp01(mastery) ** 3


def recall_quality(score_ratio: float, level3: int) -> float:
    """Qualité de rappel 0-1 : réussir « difficile » vaut mieux que réussir
    « facile ».

    Calibrée sur l'échelle RÉELLE des dérivés (1-3), pas sur l'échelle 1-10 dont
    le point neutre (5) n'existe jamais dans les données : `CopyItem.difficulty`
    ne vaut que 3, 6 ou 9, si bien que l'ancienne formule pénalisait « facile »
    au lieu de le laisser neutre.

    Il n'y a plus de bonus de délai : il pouvait porter q AU-DESSUS du score
    réellement obtenu, autrement dit inventer une réussite. Le mérite d'un rappel
    tardif est reconnu là où il a un sens — dans le gain de stabilité."""
    return _clamp01(score_ratio * (1 + (max(1, min(3, level3)) - 2) * 0.15))


def level3_of(difficulty: int) -> int:
    """Dérivé 1-3 depuis l'échelle 3/6/9 portée par CopyItem/CompetencyEvidence
    (l'historique antérieur porte encore 5, 12 ou 15 : on ramène par tranches)."""
    if difficulty <= 4:
        return 1
    return 2 if difficulty <= 7 else 3


def apply_evidence(db: Session, ev: CompetencyEvidence) -> StudentCompetencyState:
    """Met à jour l'état après une preuve finalisée (RM-008). Append-only côté historique."""
    state = db.get(StudentCompetencyState, (ev.student_id, ev.competency_id))
    if state is None:
        state = StudentCompetencyState(
            student_id=ev.student_id, competency_id=ev.competency_id,
            mastery=0.0, confidence=0.0, stability=1.0, memory_difficulty=5.0)
        db.add(state)

    before = {"mastery": state.mastery, "stability": state.stability,
              "confidence": state.confidence, "due_at": str(state.due_at)}

    # Date du DEVOIR, pas de la correction (cf. scoring.assessment_date) : c'est
    # elle qui date la trace mémoire. Un lot corrigé dix jours plus tard offrirait
    # sinon dix jours de fraîcheur en cadeau.
    now = _utc(ev.observed_at) or datetime.now(timezone.utc)
    # Fraîcheur AU MOMENT DU RAPPEL : elle sert deux fois ci-dessous — pour partir
    # de la maîtrise réelle et pour mesurer l'effort qu'a demandé ce rappel.
    fresh_before = freshness(state, now)
    strength_before = _clamp01(state.mastery) * fresh_before
    q = recall_quality(ev.score_ratio, level3_of(ev.difficulty))
    mode_weight = 1.0 if ev.mode == "control" else 0.6  # preuve formative pondérée plus faiblement (§7.1)
    w = ev.weight * mode_weight

    # Maîtrise : moyenne mobile pondérée. Le point de départ dépend du résultat,
    # et c'est tout l'objet du correctif — un rappel RÉUSSI revalide la maîtrise
    # acquise (on repart d'elle), un rappel RATÉ après un long silence prouve
    # qu'elle n'était plus qu'un souvenir de fiche (on repart de la maîtrise
    # fraîche, quasi nulle). Auparavant on repartait toujours de la valeur figée :
    # une compétence « maîtrisée » il y a un an puis ratée aujourd'hui ne baissait
    # presque pas. Repartir TOUJOURS de la valeur fraîche serait l'excès inverse :
    # l'oubli normal entre deux devoirs plafonnerait à ~0,6 la maîtrise d'un élève
    # qui réussit tout, et avec elle son niveau (cf. compute_student_level).
    alpha = min(0.5, 0.15 + 0.1 * w)
    base_mastery = state.mastery if q >= SUCCESS_Q else strength_before
    state.mastery = round((1 - alpha) * base_mastery + alpha * q, 4)
    state.confidence = round(min(1.0, state.confidence + 0.1 * w), 4)

    # Stabilité. Le gain d'un rappel réussi est d'autant plus grand que la trace
    # était FAIBLE au moment du rappel : réviser ce qu'on vient de voir n'apprend
    # rien, retrouver ce qu'on avait presque oublié consolide beaucoup (effet
    # d'espacement). L'ancienne règle (×1,5 à ×2 quoi qu'il arrive) ignorait le
    # délai et récompensait donc autant un bachotage de la veille.
    if q >= SUCCESS_Q:
        gain = 1.0 + (GAIN_MAX - 1.0) * q * (1.0 - fresh_before)
        state.stability = round(state.stability * max(1.15, gain), 2)
    else:
        # Échec : retour près du plancher, proportionnellement à la gravité.
        state.stability = round(max(S_MIN, state.stability * (0.3 + 0.4 * q)), 2)
    # Plafond indexé sur la maîtrise (cf. stability_ceiling) : il s'applique aussi
    # à une valeur déjà énorme lue en base, donc il répare les états hérités.
    state.stability = round(min(state.stability, stability_ceiling(state.mastery)), 2)
    state.memory_difficulty = round(
        max(1.0, min(10.0, state.memory_difficulty + (0.5 - q) * 2)), 2)

    state.last_seen_at = now
    # date due : R(t) = seuil  =>  t = -S * ln(seuil), bornée pour ne jamais
    # dépasser datetime.max (OverflowError sinon, cf. MAX_DUE_DAYS)
    t_due = -state.stability * math.log(settings.forgetting_threshold)
    state.due_at = now + timedelta(days=min(MAX_DUE_DAYS, max(0.5, t_due)))

    db.add(CompetencyStateHistory(
        student_id=ev.student_id, competency_id=ev.competency_id,
        before_json=before,
        after_json={"mastery": state.mastery, "stability": state.stability,
                    "confidence": state.confidence, "due_at": str(state.due_at)},
        evidence_id=ev.id,
    ))
    return state


def state_snapshot(state: StudentCompetencyState, at: datetime | None = None) -> dict:
    """Lecture complète d'un état, telle que l'affichent l'écran Élève et le
    moteur de sujets. Une seule définition : la priorité montrée au professeur
    doit être EXACTEMENT celle qui choisit les exercices."""
    at = at or datetime.now(timezone.utc)
    fresh = freshness(state, at)
    days = days_since(state, at)
    return {
        "competency_id": state.competency_id,
        "mastery": state.mastery,
        "recall_probability": round(fresh, 3),
        "freshness": round(fresh, 3),
        "strength": round(_clamp01(state.mastery) * fresh, 3),
        "priority": round(1.0 - _clamp01(state.mastery) * fresh, 3),
        "stability_days": state.stability,
        "days_since": None if days is None else round(days, 1),
        "due_at": str(state.due_at),
    }


def due_competencies(db: Session, student_id: str) -> list[dict]:
    """Compétences à retravailler, motif explicable, TRIÉES PAR PRIORITÉ (§7.5).

    Le seuil `forgetting_threshold` ne décide plus que de l'appartenance à la
    liste (« à revoir ou pas », ce que le professeur veut voir) ; le classement,
    lui, suit le score continu. Une falaise à 0,80 rendait indiscernables une
    compétence à peine tiède et une compétence complètement oubliée."""
    now = datetime.now(timezone.utc)
    out = []
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    for s in states:
        snap = state_snapshot(s, now)
        due_at = _utc(s.due_at)
        if snap["freshness"] < settings.forgetting_threshold or (due_at and due_at <= now):
            if s.last_seen_at is None:
                reason = "absence de preuve"
            elif s.mastery < 0.4:
                reason = "échec récent ou maîtrise à consolider"
            elif snap["strength"] < 0.4:
                reason = "acquis mais ancien"
            else:
                reason = "oubli probable"
            out.append({**snap, "reason": reason})
    return sorted(out, key=lambda x: -x["priority"])


def compute_student_level(db: Session, student_id: str, grade_level: str = "5e") -> tuple[int, str]:
    """Niveau global 1-10 : calcul initial déterministe (§7.3).

    Volontairement fondé sur `mastery` et NON sur `strength` : le niveau dit ce
    dont l'élève est capable, pas ce dont il se souvient aujourd'hui. Le faire
    décroître avec la fraîcheur ferait baisser le niveau de toute la classe
    pendant les vacances, et avec lui le mix de dérivés servi à la rentrée. La
    fraîcheur pilote CE QU'ON RETRAVAILLE (priority), pas ce dont l'élève est
    capable."""
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    if not states:
        return 5, "aucune preuve : niveau médian par défaut"
    weighted = sum(s.mastery * max(0.1, s.confidence) for s in states)
    total = sum(max(0.1, s.confidence) for s in states)
    mastery_avg = weighted / total
    level = max(1, min(10, round(1 + mastery_avg * 9)))
    return level, f"maîtrise moyenne pondérée {mastery_avg:.2f} sur {len(states)} compétences"


def update_level_after_assessment(
    db: Session, student: Student, assessment_id: str,
) -> StudentLevel | None:
    """Enregistre au plus un palier automatique causé par une correction.

    Le niveau automatique ne varie que d'un palier par correction. Le premier
    calcul initialise le niveau sans constituer une hausse ou une baisse ; le
    carnet peut ainsi distinguer un vrai changement d'une valeur initiale.
    """
    existing = (db.query(StudentLevel)
                .filter_by(student_id=student.id, assessment_id=assessment_id)
                .first())
    if existing or student.level_locked:
        return existing

    current = (db.query(StudentLevel).filter_by(student_id=student.id)
               .order_by(StudentLevel.valid_from.desc(), StudentLevel.id.desc()).first())
    level, reason = compute_student_level(db, student.id)
    if current:
        level = max(current.level - 1, min(current.level + 1, level))
        if level == current.level:
            return None

    row = StudentLevel(
        student_id=student.id,
        assessment_id=assessment_id,
        level=level,
        source="deterministic",
        reason=reason,
    )
    db.add(row)
    db.flush()
    return row

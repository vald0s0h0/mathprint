"""Courbe d'oubli déterministe et explicable (§7.5) — calculée sans LLM (RM-009).

Modèle simplifié inspiré de FSRS : pour chaque couple élève-compétence on
maintient (mastery, stability, memory_difficulty). La probabilité de rappel
suit R(t) = exp(-t / S). Une compétence est "due" quand R < seuil (déf. 0,80).
"""
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    CompetencyEvidence,
    CompetencyStateHistory,
    StudentCompetencyState,
)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def recall_probability(state: StudentCompetencyState, at: datetime | None = None) -> float:
    at = at or datetime.now(timezone.utc)
    last = _utc(state.last_seen_at)
    if last is None:
        return 0.0
    days = max(0.0, (at - last).total_seconds() / 86400)
    return math.exp(-days / max(0.1, state.stability))


def recall_quality(score_ratio: float, difficulty: int, days_elapsed: float) -> float:
    """Qualité de rappel 0-1 : exactitude pondérée par difficulté et délai."""
    diff_bonus = 1 + (difficulty - 5) * 0.05
    delay_bonus = min(1.3, 1 + days_elapsed / 30)
    return max(0.0, min(1.0, score_ratio * diff_bonus * delay_bonus))


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

    now = datetime.now(timezone.utc)
    last = _utc(state.last_seen_at)
    days = (now - last).total_seconds() / 86400 if last else 0.0
    q = recall_quality(ev.score_ratio, ev.difficulty, days)
    mode_weight = 1.0 if ev.mode == "control" else 0.6  # preuve formative pondérée plus faiblement (§7.1)
    w = ev.weight * mode_weight

    # maîtrise : moyenne mobile pondérée
    alpha = min(0.5, 0.15 + 0.1 * w)
    state.mastery = round((1 - alpha) * state.mastery + alpha * q, 4)
    state.confidence = round(min(1.0, state.confidence + 0.1 * w), 4)

    # stabilité : augmente après rappel réussi, diminue après échec
    if q >= 0.6:
        state.stability = round(state.stability * (1.5 + 0.5 * q * w), 2)
    else:
        state.stability = round(max(0.5, state.stability * 0.5), 2)
    state.memory_difficulty = round(
        max(1.0, min(10.0, state.memory_difficulty + (0.5 - q) * 2)), 2)

    state.last_seen_at = now
    # date due : R(t) = seuil  =>  t = -S * ln(seuil)
    t_due = -state.stability * math.log(settings.forgetting_threshold)
    state.due_at = now + timedelta(days=max(0.5, t_due))

    db.add(CompetencyStateHistory(
        student_id=ev.student_id, competency_id=ev.competency_id,
        before_json=before,
        after_json={"mastery": state.mastery, "stability": state.stability,
                    "confidence": state.confidence, "due_at": str(state.due_at)},
        evidence_id=ev.id,
    ))
    return state


def due_competencies(db: Session, student_id: str) -> list[dict]:
    """Compétences dues avec motif explicable (§7.5)."""
    now = datetime.now(timezone.utc)
    out = []
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    for s in states:
        p = recall_probability(s, now)
        due_at = _utc(s.due_at)
        if p < settings.forgetting_threshold or (due_at and due_at <= now):
            if s.mastery < 0.4:
                reason = "échec récent ou maîtrise fragile"
            elif s.last_seen_at is None:
                reason = "absence de preuve"
            else:
                reason = "oubli probable"
            out.append({"competency_id": s.competency_id, "recall_probability": round(p, 3),
                        "mastery": s.mastery, "reason": reason, "due_at": str(s.due_at)})
    return sorted(out, key=lambda x: x["recall_probability"])


def compute_student_level(db: Session, student_id: str, grade_level: str = "5e") -> tuple[int, str]:
    """Niveau global 1-10 : calcul initial déterministe (§7.3)."""
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    if not states:
        return 5, "aucune preuve : niveau médian par défaut"
    weighted = sum(s.mastery * max(0.1, s.confidence) for s in states)
    total = sum(max(0.1, s.confidence) for s in states)
    mastery_avg = weighted / total
    level = max(1, min(10, round(1 + mastery_avg * 9)))
    return level, f"maîtrise moyenne pondérée {mastery_avg:.2f} sur {len(states)} compétences"

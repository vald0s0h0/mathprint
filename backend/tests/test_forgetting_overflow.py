"""Régression du 500 « Valider la correction » : OverflowError « date value out
of range ». La stabilité d'une compétence, multipliée à chaque rappel réussi,
croissait sans borne ; au bout d'une vingtaine de rappels la date due calculée
(`now + timedelta(days=t_due)`) dépassait datetime.max et levait OverflowError
NON gérée dans la boucle de preuves de finalize_batch (avant le try overlay).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import CompetencyEvidence, StudentCompetencyState
from app.services import forgetting


@pytest.fixture
def db():
    s = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(s.bind)
    yield s
    s.close()


def _ev(mode="control", ratio=1.0):
    return CompetencyEvidence(student_id="s1", competency_id="c1", item_id="i1",
                              mode=mode, score_ratio=ratio, difficulty=3, weight=1.0)


def test_huge_stored_stability_does_not_overflow(db):
    """État déjà corrompu en base (stabilité énorme) : appliquer une preuve ne
    doit plus lever OverflowError, et la stabilité est ramenée sous le plafond."""
    db.add(StudentCompetencyState(
        student_id="s1", competency_id="c1", mastery=0.9, confidence=0.9,
        stability=5e8, memory_difficulty=3.0,   # ~500M jours : déborde sans plafond
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=10)))
    db.flush()
    ev = _ev(); db.add(ev); db.flush()

    state = forgetting.apply_evidence(db, ev)   # ne doit PAS lever
    assert state.stability <= forgetting.S_CEIL
    # le plafond dépend désormais de la maîtrise : il ne suffit pas de ne pas
    # déborder, la fenêtre doit correspondre à ce que l'élève maîtrise vraiment
    assert state.stability <= forgetting.stability_ceiling(state.mastery)
    assert state.due_at is not None and state.due_at.year < 9999


def test_repeated_success_stays_bounded(db):
    """Vingt rappels réussis d'affilée : la stabilité plafonne, aucune date ne
    déborde (avant le fix : OverflowError bien avant la 20e)."""
    db.add(StudentCompetencyState(
        student_id="s1", competency_id="c1", mastery=0.0, confidence=0.0,
        stability=1.0, memory_difficulty=5.0))
    db.flush()
    for _ in range(20):
        ev = _ev(); db.add(ev); db.flush()
        state = forgetting.apply_evidence(db, ev)
    assert state.stability <= forgetting.S_CEIL
    assert state.stability <= forgetting.stability_ceiling(state.mastery)
    assert state.due_at.year < 9999

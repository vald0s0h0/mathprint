"""Courbe d'oubli : la fraîcheur s'applique à TOUT, réussites comprises.

Le défaut que ces tests verrouillent : `mastery` seul ne vieillissait jamais. Une
compétence à 0,9 il y a trois mois se relisait 0,9 aujourd'hui, si bien que le
moteur de sujets la considérait acquise pour toujours. La priorité est désormais
`1 - mastery × fraîcheur`, et la fenêtre de rappel est bornée par la maîtrise.
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


def _state(mastery=0.9, stability=30.0, days_ago=0.0):
    return StudentCompetencyState(
        student_id="s1", competency_id="c1", mastery=mastery, confidence=0.9,
        stability=stability, memory_difficulty=5.0,
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=days_ago))


def _ev(mode="control", ratio=1.0, difficulty=6, days_ago=0.0):
    return CompetencyEvidence(
        student_id="s1", competency_id="c1", item_id="i1", mode=mode,
        score_ratio=ratio, difficulty=difficulty, weight=1.0,
        observed_at=datetime.now(timezone.utc) - timedelta(days=days_ago))


# --------------------------------------------------------------- la fraîcheur

def test_a_competency_mastered_three_months_ago_is_no_longer_fresh():
    """Le cœur du sujet : maîtrisée il y a 3 mois ≠ maîtrisée aujourd'hui."""
    old = _state(mastery=0.95, stability=30.0, days_ago=90)
    assert forgetting.priority(old) > 0.9
    assert forgetting.strength(old) < 0.1
    # …alors que la maîtrise brute, elle, n'a pas bougé d'un pouce
    assert old.mastery == 0.95


def test_the_same_competency_seen_yesterday_is_not_urgent():
    fresh = _state(mastery=0.95, stability=30.0, days_ago=1)
    assert forgetting.priority(fresh) < 0.1


def test_a_never_assessed_competency_is_maximally_urgent():
    assert forgetting.priority(None) == 1.0
    never = _state()
    never.last_seen_at = None
    assert forgetting.priority(never) == 1.0


def test_a_solid_competency_decays_slower_than_a_fragile_one():
    """Même délai, même maîtrise affichée : c'est la stabilité qui fait la
    différence entre « on peut attendre » et « à revoir maintenant »."""
    solid = _state(mastery=0.9, stability=200.0, days_ago=60)
    fragile = _state(mastery=0.9, stability=10.0, days_ago=60)
    assert forgetting.priority(solid) < 0.4
    assert forgetting.priority(fragile) > 0.85


# ------------------------------------------------- le plafond par la maîtrise

def test_stability_ceiling_grows_with_mastery():
    ceil = forgetting.stability_ceiling
    assert ceil(0.0) == pytest.approx(1.0)
    assert ceil(1.0) == pytest.approx(forgetting.S_CEIL)
    assert ceil(0.3) < ceil(0.5) < ceil(0.7) < ceil(0.9)
    # une compétence à moitié maîtrisée revient dans le mois ou deux, pas dans un an
    assert 20 < ceil(0.5) < 60


def test_a_half_mastered_competency_never_earns_a_long_window(db):
    """Cinq succès d'affilée sur une compétence qui reste moyenne ne doivent pas
    ouvrir une fenêtre de rappel de plusieurs mois."""
    db.add(_state(mastery=0.5, stability=10.0, days_ago=5))
    db.flush()
    for _ in range(5):
        ev = _ev(ratio=0.6)     # réussite tout juste : la maîtrise reste moyenne
        db.add(ev)
        db.flush()
        state = forgetting.apply_evidence(db, ev)
    assert state.stability <= forgetting.stability_ceiling(state.mastery)
    assert state.stability < 120


def test_full_mastery_earns_the_long_window(db):
    db.add(_state(mastery=0.95, stability=100.0, days_ago=60))
    db.flush()
    for _ in range(4):
        ev = _ev(ratio=1.0, difficulty=9)
        db.add(ev)
        db.flush()
        state = forgetting.apply_evidence(db, ev)
    assert state.stability > 150      # « on peut la revoir dans longtemps »


# --------------------------------------------------------- l'effet d'espacement

def test_a_late_success_consolidates_more_than_a_same_day_one(db):
    """Réviser ce qu'on vient de voir n'apprend rien ; retrouver ce qu'on avait
    presque oublié consolide beaucoup."""
    def stability_after(days_ago: float) -> float:
        s = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
        Base.metadata.create_all(s.bind)
        s.add(_state(mastery=0.8, stability=20.0, days_ago=days_ago))
        s.flush()
        ev = _ev(ratio=1.0)
        s.add(ev)
        s.flush()
        state = forgetting.apply_evidence(s, ev)
        s.close()
        return state.stability

    assert stability_after(40) > stability_after(0.5)


# ------------------------------------------------------------ maîtrise honnête

def test_failing_after_a_long_silence_really_drops_mastery(db):
    """Avant : la moyenne mobile repartait d'un 0,9 périmé, la chute était
    dérisoire. Maintenant elle repart de la maîtrise FRAÎCHE, quasi nulle."""
    db.add(_state(mastery=0.9, stability=20.0, days_ago=180))
    db.flush()
    ev = _ev(ratio=0.0)
    db.add(ev)
    db.flush()
    state = forgetting.apply_evidence(db, ev)
    assert state.mastery < 0.1


def test_succeeding_does_not_punish_the_normal_spacing(db):
    """Le miroir du test précédent : un élève qui réussit systématiquement doit
    voir sa maîtrise MONTER vers 1, même avec de l'oubli entre deux devoirs.
    Repartir toujours de la valeur fraîche la plafonnerait autour de 0,6 — et
    avec elle le niveau de l'élève (compute_student_level)."""
    db.add(_state(mastery=0.5, stability=20.0, days_ago=15))
    db.flush()
    for _ in range(8):
        ev = _ev(ratio=1.0, difficulty=6, days_ago=0)
        db.add(ev)
        db.flush()
        state = forgetting.apply_evidence(db, ev)
        state.last_seen_at = datetime.now(timezone.utc) - timedelta(days=15)
    assert state.mastery > 0.85


def test_recall_quality_never_invents_a_success():
    """L'ancien bonus de délai (×1,3) pouvait porter q au-dessus du score
    réellement obtenu. Un demi-exercice juste ne vaut jamais un exercice juste."""
    assert forgetting.recall_quality(0.5, 3) <= 0.5 * 1.15
    assert forgetting.recall_quality(0.0, 3) == 0.0
    # réussir « difficile » vaut mieux que réussir « facile »
    assert (forgetting.recall_quality(0.8, 1)
            < forgetting.recall_quality(0.8, 2)
            < forgetting.recall_quality(0.8, 3))
    # …mais on ne dépasse jamais le sans-faute : un sujet difficile réussi à 100 %
    # sature à 1, il ne crée pas de la maîtrise au-delà de ce qui a été observé.
    assert forgetting.recall_quality(1.0, 3) == 1.0
    assert forgetting.recall_quality(1.0, 1) < 1.0


def test_level3_of_maps_the_legacy_scales():
    # 3/6/9 depuis le passage à 3 niveaux, 5 (défaut) et 12/15 pour l'historique
    assert forgetting.level3_of(3) == 1
    assert forgetting.level3_of(5) == 2
    assert forgetting.level3_of(6) == 2
    assert forgetting.level3_of(9) == 3
    assert forgetting.level3_of(15) == 3


# ------------------------------------------------------------------- l'écriture

def test_evidence_is_dated_from_the_assessment_not_the_correction(db):
    """Une correction saisie dix jours après le devoir ne doit pas offrir dix
    jours de fraîcheur en cadeau."""
    ev = _ev(ratio=1.0, days_ago=10)
    db.add(ev)
    db.flush()
    state = forgetting.apply_evidence(db, ev)
    days = forgetting.days_since(state)
    assert days is not None and 9.5 < days < 10.5


def test_due_competencies_are_ranked_by_priority(db):
    db.add(StudentCompetencyState(
        student_id="s1", competency_id="tiede", mastery=0.7, confidence=0.9,
        stability=30.0, last_seen_at=datetime.now(timezone.utc) - timedelta(days=12)))
    db.add(StudentCompetencyState(
        student_id="s1", competency_id="oubliee", mastery=0.7, confidence=0.9,
        stability=5.0, last_seen_at=datetime.now(timezone.utc) - timedelta(days=90)))
    db.flush()
    due = forgetting.due_competencies(db, "s1")
    assert [d["competency_id"] for d in due] == ["oubliee", "tiede"]
    assert due[0]["priority"] > due[1]["priority"]


def test_a_never_seen_state_is_not_reported_as_a_recent_failure(db):
    db.add(StudentCompetencyState(student_id="s1", competency_id="c1",
                                  mastery=0.0, confidence=0.0, stability=1.0))
    db.flush()
    assert forgetting.due_competencies(db, "s1")[0]["reason"] == "absence de preuve"

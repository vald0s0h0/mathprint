"""Choix des exercices d'un sujet individuel, sur l'historique réel de l'élève.

Ce que ces tests verrouillent, et qui n'existait pas avant : le niveau d'élève
(1-10) se traduit en MIX de dérivés et non en palier unique, la couverture des
compétences cochées reste uniforme, les plus prioritaires reçoivent davantage de
cartes, et un exercice déjà servi ne revient pas tant qu'il reste des inédits.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (
    Copy, CopyItem, CopyItemResult, CopyResult, GeneratedExercise,
    StudentCompetencyState,
)
from app.services import student_history

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db():
    s = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(s.bind)
    yield s
    s.close()


def _state(db, comp_id, mastery, stability=30.0, days_ago=1.0):
    db.add(StudentCompetencyState(
        student_id="s1", competency_id=comp_id, mastery=mastery, confidence=0.8,
        stability=stability, memory_difficulty=5.0,
        last_seen_at=NOW - timedelta(days=days_ago)))
    db.flush()


def _served(db, exercise_id, days_ago=30.0, ratio=None, competency_id="c1", level=2):
    """Un exercice servi à l'élève, éventuellement corrigé."""
    copy = Copy(assessment_id="a1", student_id="s1",
                generated_at=NOW - timedelta(days=days_ago))
    db.add(copy)
    db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat1", sequence=1,
                    generated_exercise_id=exercise_id, difficulty=level * 3,
                    response_type="short_text", statement="x", correction="")
    db.add(item)
    db.flush()
    if ratio is not None:
        result = CopyResult(copy_id=copy.id, assessment_id="a1", student_id="s1")
        db.add(result)
        db.flush()
        db.add(CopyItemResult(
            copy_result_id=result.id, copy_item_id=item.id,
            competency_id=competency_id, student_id="s1",
            generated_exercise_id=exercise_id, difficulty_level=level,
            success_ratio=ratio, occurred_at=NOW - timedelta(days=days_ago),
            score=ratio, max_score=1.0, bareme_points=1.0, points_earned=ratio))
    db.flush()
    return item


def _row(ex_id, kind="application", response_type="short_text", level=2):
    return GeneratedExercise(
        id=ex_id, competency_id="c1", difficulty_level=level, statement=f"E{ex_id}",
        kind=kind, response_type=response_type, expected_json={}, grading_json={})


# ---------------------------------------------------------- mix de dérivés

def test_level_quota_always_sums_to_the_number_of_slots():
    for level in range(1, 11):
        for n in range(0, 25):
            quota = student_history.level_quota(level, n)
            assert sum(quota.values()) == n, (level, n)


def test_level_quota_is_a_mix_not_a_single_tier():
    """Le cœur du correctif : un élève n'a jamais QUE du facile ou QUE du
    difficile — sinon le faible ne progresse jamais et le fort n'a plus une
    seule réussite pour s'installer."""
    weak = student_history.level_quota(2, 10)
    strong = student_history.level_quota(9, 10)
    assert weak[1] > 0 and weak[2] > 0        # le faible voit aussi du « base »
    assert strong[2] > 0 and strong[3] > 0    # le fort garde du « base »
    assert weak[1] > strong[1]                # …mais nettement plus de facile
    assert strong[3] > weak[3]


def test_level_quota_slides_monotonically_with_the_student_level():
    faciles = [student_history.level_quota(lvl, 20)[1] for lvl in range(1, 11)]
    difficiles = [student_history.level_quota(lvl, 20)[3] for lvl in range(1, 11)]
    assert faciles == sorted(faciles, reverse=True)
    assert difficiles == sorted(difficiles)


def test_level_3_is_reachable():
    """Régression : l'ancien difficulty_level3 ne pouvait JAMAIS produire 3, le
    ±2 autour de 5 étant écrasé par la conversion en trois paliers."""
    assert student_history.level_quota(10, 10)[3] > 0
    assert student_history.level_quota(5, 10)[3] > 0


# ------------------------------------------------ couverture et pondération

def test_every_checked_competency_gets_at_least_one_exercise(db):
    """Le périmètre coché par le professeur est un contrat : même une compétence
    parfaitement acquise garde sa case."""
    _state(db, "acquise", mastery=0.98, stability=300.0, days_ago=1)
    _state(db, "lacune", mastery=0.1, stability=2.0, days_ago=40)
    slots, _ = student_history.student_plan(
        db, "s1", ["acquise", "lacune"], student_level_1_10=5, n_slots=12)
    assert {s.competency_id for s in slots[:2]} == {"acquise", "lacune"}


def test_the_most_urgent_competency_gets_more_slots(db):
    _state(db, "acquise", mastery=0.95, stability=300.0, days_ago=1)
    _state(db, "lacune", mastery=0.1, stability=2.0, days_ago=60)
    slots, _ = student_history.student_plan(
        db, "s1", ["acquise", "lacune"], student_level_1_10=5, n_slots=12)
    counts = {"acquise": 0, "lacune": 0}
    for s in slots:
        counts[s.competency_id] += 1
    assert counts["lacune"] > counts["acquise"]
    # …sans jamais affamer l'autre : le tour de rôle pondéré n'exclut personne
    assert counts["acquise"] >= 2


def test_the_priority_order_leads(db):
    _state(db, "ok", mastery=0.9, stability=200.0, days_ago=2)
    _state(db, "oubliee", mastery=0.9, stability=5.0, days_ago=90)
    slots, _ = student_history.student_plan(
        db, "s1", ["ok", "oubliee"], student_level_1_10=5, n_slots=6)
    assert slots[0].competency_id == "oubliee"


# ------------------------------------------------- appariement dérivé/élève

def test_a_fragile_competency_is_never_served_at_the_hardest_level(db):
    _state(db, "lacune", mastery=0.05, stability=2.0, days_ago=40)
    slots, _ = student_history.student_plan(
        db, "s1", ["lacune"], student_level_1_10=10, n_slots=8)
    assert all(s.level3 <= 2 for s in slots)


def test_a_solid_competency_is_never_served_at_the_easiest_level(db):
    _state(db, "solide", mastery=0.98, stability=400.0, days_ago=1)
    slots, _ = student_history.student_plan(
        db, "s1", ["solide"], student_level_1_10=2, n_slots=8)
    assert all(s.level3 >= 2 for s in slots)


def test_a_failed_level_is_not_repeated_at_the_same_height(db):
    """Raté en « difficile » : on redescend, on ne réessaie pas le même mur."""
    _state(db, "c1", mastery=0.5, stability=30.0, days_ago=10)
    _served(db, "ex-hard", days_ago=10, ratio=0.1, competency_id="c1", level=3)
    slots, _ = student_history.student_plan(
        db, "s1", ["c1"], student_level_1_10=9, n_slots=6)
    assert all(s.level3 <= 2 for s in slots)


# ------------------------------------------------------- anti-répétition

def test_an_unseen_exercise_always_wins(db):
    log = student_history.exercise_log(db, "s1")
    rows = [_row("vu"), _row("inedit")]
    _served(db, "vu", days_ago=5, ratio=1.0)
    log = student_history.exercise_log(db, "s1")
    assert student_history.rank_candidates(rows, log)[0].id == "inedit"
    assert [r.id for r in student_history.preferred_rows(rows, log)] == ["inedit"]


def test_a_long_ago_failure_comes_back_before_a_success(db):
    """Le renforcement de lacune : un exercice raté il y a longtemps est un
    meilleur candidat qu'un exercice déjà réussi."""
    _served(db, "rate", days_ago=settings.history_replay_min_days + 10, ratio=0.0)
    _served(db, "reussi", days_ago=200, ratio=1.0)
    log = student_history.exercise_log(db, "s1")
    ranked = student_history.rank_candidates([_row("reussi"), _row("rate")], log)
    assert [r.id for r in ranked] == ["rate", "reussi"]


def test_a_recent_failure_is_not_served_again_immediately(db):
    """Trop tôt : l'élève le referait de mémoire, pas de tête."""
    _served(db, "rate", days_ago=2, ratio=0.0)
    _served(db, "vieux", days_ago=300, ratio=1.0)
    log = student_history.exercise_log(db, "s1")
    ranked = student_history.rank_candidates([_row("rate"), _row("vieux")], log)
    assert [r.id for r in ranked] == ["vieux", "rate"]


def test_an_exercise_printed_on_an_unreturned_copy_still_counts_as_seen(db):
    """Servi mais jamais corrigé (copie absente) : le resservir tel quel au sujet
    suivant serait une répétition visible."""
    _served(db, "imprime", days_ago=5, ratio=None)
    log = student_history.exercise_log(db, "s1")
    assert "imprime" in log and log["imprime"].graded is False
    ranked = student_history.rank_candidates([_row("imprime"), _row("neuf")], log)
    assert ranked[0].id == "neuf"


def test_history_without_an_identified_exercise_is_simply_ignored(db):
    """Les corrections antérieures à generated_exercise_id n'ont pas d'exercice
    identifiable : elles ne doivent pas planter ni bloquer l'anti-répétition."""
    copy = Copy(assessment_id="a1", student_id="s1", generated_at=NOW)
    db.add(copy)
    db.flush()
    db.add(CopyItem(copy_id=copy.id, catalog_id="cat1", sequence=1,
                    generated_exercise_id=None, difficulty=6,
                    response_type="short_text", statement="x", correction=""))
    db.flush()
    assert student_history.exercise_log(db, "s1") == {}


# ------------------------------------------------------------ déterminisme

def test_the_plan_is_deterministic(db):
    _state(db, "a", mastery=0.4, stability=20.0, days_ago=10)
    _state(db, "b", mastery=0.8, stability=50.0, days_ago=5)
    at = NOW
    first, _ = student_history.student_plan(db, "s1", ["a", "b"], 5, 10, at=at)
    second, _ = student_history.student_plan(db, "s1", ["a", "b"], 5, 10, at=at)
    assert [(s.competency_id, s.level3) for s in first] == \
           [(s.competency_id, s.level3) for s in second]


def test_a_student_never_assessed_still_gets_a_full_plan(db):
    slots, stats = student_history.student_plan(db, "s1", ["a", "b", "c"], 5, 9)
    assert len(slots) == 9
    assert {s.competency_id for s in slots[:3]} == {"a", "b", "c"}
    assert all(st.priority == 1.0 for st in stats.values())

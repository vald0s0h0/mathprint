"""Consensus déterministe : votes par famille, paires corrélées, scores, garde-fou, Mathpix."""
import copy
import itertools
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ocr_fusion as F
from app.services.ocr_surface import to_surface
from app.services.runtime_settings import DEFAULT_ENGINE_PLAN as PLAN, DEFAULT_ENGINE_THRESHOLDS as TH

FAM = PLAN["families"]


def R(engine, raw, role, *, cls="num_line", lp=-0.05, conf=None, illegible=False, plan=PLAN):
    fam = plan["families"][engine]
    if engine == "apple_vision" and conf is None:
        conf = 0.9
        lp = None
    return F.ReadIn(engine=engine, family=fam, raw=raw, surface=to_surface(raw, cls), illegible=illegible,
                    confidence_raw=conf, logprob_mean=lp, logprob_min=lp, role=role)


def ctx(cls="num_line", n=4, per_char=0.25, h=80):
    return F.CropCtx(zone_class=cls, ink_width_px=int(h * per_char * n), height_px=h)


def plan_with(cls="num_line", stage1=None, stage2=None, observe=None, families=None, pairs=None, scorers=None):
    p = copy.deepcopy(PLAN)
    p["classes"][cls] = {"stage1": stage1 or [], "stage2": stage2 or [], "observe": observe or []}
    if families:
        p["families"].update(families)
    if pairs is not None:
        p["correlated_pairs"] = pairs
    if scorers is not None:
        p["scorers"] = scorers
    return p


# ------------------------------------------------------------ étape 1

def test_two_families_agree_in_stage1_is_accepted():
    out = F.decide_local(ctx(n=2), [R("apple_vision", "24", "stage1"), R("unimumer", "2 4", "stage1")], PLAN, TH)
    assert (out.action, out.reason, out.surface) == ("accept", "local_consensus", "24")
    assert out.supporters == ["apple_vision", "unimumer"]


def test_same_family_agreement_does_not_count_twice():
    p = plan_with(stage1=["unimumer", "unimumer2"], families={"unimumer2": "qwen35"}, pairs=[])
    reads = [R("unimumer", "24", "stage1", plan=p), R("unimumer2", "24", "stage1", plan=p)]
    out = F.decide_local(ctx(n=2), reads, p, TH)
    assert out.action == "mathpix" and out.reason == "no_majority"


def test_stage1_disagreement_goes_to_stage2():
    out = F.decide_local(ctx(n=2), [R("apple_vision", "21", "stage1"), R("unimumer", "24", "stage1")], PLAN, TH)
    assert out.action == "need_stage2"


def test_below_threshold_is_not_a_vote():
    low = R("unimumer", "24", "stage1", lp=-0.9)            # seuil par défaut -0.5
    out = F.decide_local(ctx(n=2), [R("apple_vision", "24", "stage1"), low], PLAN, TH)
    assert out.action == "need_stage2"
    weak_apple = R("apple_vision", "24", "stage1", conf=0.3)
    out = F.decide_local(ctx(n=2), [weak_apple, R("unimumer", "24", "stage1")], PLAN, TH)
    assert out.action == "need_stage2"


def test_missing_measure_when_a_threshold_exists_is_refused():
    r = F.ReadIn("unimumer", "qwen35", "24", "24", role="stage1")     # pas de log-prob
    assert not F.passes_threshold(r, TH)
    assert F.passes_threshold(r, {})


def test_observers_never_vote():
    reads = [R("unimumer", "24", "stage1"), R("trocr", "24", "observe")]
    out = F.decide_local(ctx(n=2), reads, PLAN, TH, phase=1)
    assert out.action == "need_stage2"                                # trocr ne compte pas
    assert F.tally(reads, PLAN, TH, "num_line")[0]["voices"] == 1


def test_observer_dissent_never_blocks_a_consensus():
    reads = [R("apple_vision", "24", "stage1"), R("unimumer", "24", "stage1"), R("trocr", "21", "observe")]
    assert F.decide_local(ctx(n=2), reads, PLAN, TH).action == "accept"


# ------------------------------------------------------------ paires corrélées

def test_correlated_pair_alone_is_not_enough_in_stage1():
    reads = [R("unimumer", "x^2", "stage1", cls="math_line"), R("lighton", "x²", "stage1", cls="math_line")]
    out = F.decide_local(ctx("math_line", n=3), reads, PLAN, TH)
    assert out.action == "need_stage2" and out.reason == "stage1_not_conclusive"


def test_correlated_pair_counts_twice_when_the_other_family_does_not_contradict():
    c = ctx("math_line", n=3)
    base = [R("unimumer", "x^2", "stage1", cls="math_line"), R("lighton", "x²", "stage1", cls="math_line")]
    empty = R("apple_vision", "", "stage2", cls="math_line", illegible=True)
    assert F.decide_local(c, base + [empty], PLAN, TH, phase=2).action == "accept"
    agree = R("apple_vision", "x2", "stage2", cls="math_line")        # lit une AUTRE surface (x2)
    out = F.decide_local(c, base + [agree], PLAN, TH, phase=2)
    assert out.action == "mathpix"                                    # conflit -> Mathpix
    same = R("apple_vision", "x^2", "stage2", cls="math_line")
    out = F.decide_local(c, base + [same], PLAN, TH, phase=2)
    assert out.action == "accept" and F.tally(base + [same], PLAN, TH, "math_line")[0]["voices"] == 3


# ------------------------------------------------------------ étape 2

P3 = plan_with(stage1=["apple_vision", "unimumer"], stage2=["trocr"], observe=["lighton"], pairs=[],
               scorers=["unimumer", "trocr"])


def _two_vs_one(**kw):
    return [R("apple_vision", "24", "stage1", plan=P3), R("unimumer", "21", "stage1", plan=P3),
            R("trocr", "24", "stage2", plan=P3, **kw)]


def test_two_against_one_asks_for_scores_then_accepts_when_nobody_objects():
    reads = _two_vs_one()
    out = F.decide_local(ctx(n=2), reads, P3, TH, phase=2)
    assert out.action == "need_score" and out.surface == "24" and out.score_surfaces == ["21", "24"]
    ok = F.decide_local(ctx(n=2), reads, P3, TH, phase=2,
                        scores={"unimumer": {"24": -3.0, "21": -3.5}, "trocr": {"24": -1.0, "21": -6.0}})
    assert (ok.action, ok.surface) == ("accept", "24")


def test_a_score_can_remove_the_agreement():
    reads = _two_vs_one()
    out = F.decide_local(ctx(n=2), reads, P3, TH, phase=2,
                         scores={"unimumer": {"24": -9.0, "21": -1.0}})      # écart 8 nats > 2
    assert out.action == "mathpix" and out.reason == "score_veto:unimumer"
    within = F.decide_local(ctx(n=2), reads, P3, TH, phase=2,
                            scores={"unimumer": {"24": -3.0, "21": -1.5}})   # écart 1,5 < 2
    assert within.action == "accept"


def test_score_margin_is_adjustable():
    reads = _two_vs_one()
    sc = {"unimumer": {"24": -3.0, "21": -1.5}}
    assert F.decide_local(ctx(n=2), reads, P3, TH, phase=2, scores=sc, margin=1.0).action == "mathpix"


def test_a_score_can_never_introduce_a_candidate():
    """Une surface qu'aucun moteur n'a lue n'entre jamais en jeu, même avec un score parfait."""
    reads = _two_vs_one()
    out = F.decide_local(ctx(n=2), reads, P3, TH, phase=2,
                         scores={"unimumer": {"24": -3.0, "21": -3.5, "99": 0.0}})
    assert (out.action, out.surface) == ("accept", "24")          # le « 99 » parfait est ignoré
    assert F.decide_local(ctx(n=2), reads, P3, TH, phase=2,
                          scores={"unimumer": {"24": -9.0, "99": 0.0}}).action == "accept"
    assert F.score_candidates_for("unimumer", reads, ["21", "24"]) == ["21", "24"]


def test_score_candidates_use_the_scorers_own_raw_form():
    reads = [R("unimumer", "2 4", "stage1"), R("apple_vision", "24", "stage1")]
    assert F.score_candidates_for("unimumer", reads, ["24"]) == ["2 4"]
    assert F.score_candidates_for("trocr", reads, ["24"]) == ["2 4"]      # pas lu par trocr : brut d'un autre (tri)


def test_one_one_one_goes_to_mathpix():
    reads = [R("apple_vision", "24", "stage1", plan=P3), R("unimumer", "21", "stage1", plan=P3),
             R("trocr", "27", "stage2", plan=P3)]
    out = F.decide_local(ctx(n=2), reads, P3, TH, phase=2)
    assert out.action == "mathpix" and out.reason == "no_majority"
    assert [c["voices"] for c in out.candidates] == [1, 1, 1]


def test_two_surfaces_with_two_voices_is_a_conflict():
    p = plan_with(stage1=["apple_vision", "unimumer"], stage2=["trocr", "lighton"], pairs=[],
                  families={"lighton": "pixtral_qwen3"})
    reads = [R("apple_vision", "24", "stage1", plan=p), R("unimumer", "21", "stage1", plan=p),
             R("trocr", "24", "stage2", plan=p), R("lighton", "21", "stage2", plan=p)]
    out = F.decide_local(ctx(n=2), reads, p, TH, phase=2)
    assert (out.action, out.reason) == ("mathpix", "conflicting_majorities")


def test_no_scoring_needed_when_a_single_surface_was_read():
    reads = [R("apple_vision", "24", "stage1"), R("unimumer", "24", "stage1"), R("lighton", "24", "stage2")]
    assert F.decide_local(ctx(n=2), reads, PLAN, TH, phase=2).action == "accept"


# ------------------------------------------------------------ illisible

def test_two_families_illegible_goes_to_manual_never_blank():
    reads = [R("apple_vision", "", "stage1", illegible=True), R("unimumer", "", "stage1")]
    out = F.decide_local(ctx(n=1), reads, PLAN, TH)
    assert (out.action, out.reason) == ("manual", "illegible_consensus")
    suspect = [R("unimumer", "2 \\square 3", "stage1", illegible=True), R("apple_vision", "", "stage1")]
    assert F.decide_local(ctx(n=1), suspect, PLAN, TH).action == "manual"


def test_one_family_illegible_is_not_manual_by_itself():
    reads = [R("apple_vision", "", "stage1", illegible=True), R("unimumer", "24", "stage1")]
    assert F.decide_local(ctx(n=2), reads, PLAN, TH).action == "need_stage2"


# ------------------------------------------------------------ garde-fou

@pytest.mark.parametrize("n,per_char,ok", [(4, 0.25, True), (4, 0.05, False), (4, 1.2, False),
                                           (1, 3.0, False), (10, 0.25, True)])
def test_ink_guard_bounds(n, per_char, ok):
    surface = "1" * n
    assert F.ink_guard_ok(surface, ctx(n=n, per_char=per_char), TH) is ok


def test_ink_guard_refuses_when_geometry_unknown():
    assert not F.ink_guard_ok("24", F.CropCtx("num_line", 0, 0), TH)


def test_length_guard_blocks_a_consensus_on_implausible_length():
    """Deux moteurs d'accord sur `1234567`, mais l'encre ne tient que sur 2 caractères."""
    reads = [R("apple_vision", "1234567", "stage1"), R("unimumer", "1234567", "stage1")]
    assert F.decide_local(ctx(n=2), reads, PLAN, TH).action == "need_stage2"
    out = F.decide_local(ctx(n=2), reads + [R("lighton", "1234567", "stage2")], PLAN, TH, phase=2)
    assert (out.action, out.reason) == ("mathpix", "ink_guard")


def test_ink_guard_ignores_spaces():
    assert F.ink_guard_ok("1 000", ctx(n=4), TH) == F.ink_guard_ok("1000", ctx(n=4), TH)


# ------------------------------------------------------------ Mathpix

def _locals():
    return [R("apple_vision", "24", "stage1"), R("unimumer", "21", "stage1"), R("lighton", "26", "stage2")]


def test_mathpix_confirms_a_surface_read_locally():
    out = F.decide_mathpix(ctx(n=2), _locals(), "\\( 24 \\)", 0.97, PLAN, TH)
    assert (out.action, out.reason, out.surface) == ("accept", "mathpix_confirmed", "24")


def test_mathpix_cannot_introduce_a_value():
    out = F.decide_mathpix(ctx(n=2), _locals(), "25", 0.99, PLAN, TH)
    assert (out.action, out.reason) == ("manual", "mathpix_not_read_locally")


def test_mathpix_low_confidence_or_empty_goes_manual():
    assert F.decide_mathpix(ctx(n=2), _locals(), "24", 0.5, PLAN, TH).reason == "mathpix_low_confidence"
    assert F.decide_mathpix(ctx(n=2), _locals(), "  ", 0.99, PLAN, TH).reason == "mathpix_empty"


def test_mathpix_blocked_when_another_surface_has_two_voices():
    reads = [R("apple_vision", "21", "stage1"), R("unimumer", "21", "stage1"), R("lighton", "24", "stage2")]
    out = F.decide_mathpix(ctx(n=2), reads, "24", 0.99, PLAN, TH)
    assert (out.action, out.reason) == ("manual", "other_surface_has_two_voices")


def test_mathpix_ignores_observer_reads():
    reads = [R("apple_vision", "21", "stage1"), R("trocr", "24", "observe")]
    assert F.decide_mathpix(ctx(n=2), reads, "24", 0.99, PLAN, TH).reason == "mathpix_not_read_locally"


def test_mathpix_surface_goes_through_to_surface():
    reads = [R("unimumer", "1,79", "stage1"), R("apple_vision", "1.8", "stage1")]
    out = F.decide_mathpix(ctx(n=3), reads, "\\( 1.79 \\)", 0.99, PLAN, TH)
    assert out.action == "accept" and out.surface == "1,79"
    wrong = F.decide_mathpix(ctx(n=3), reads, "\\( 1.8 \\)", 0.99, PLAN, TH)
    assert wrong.surface == "1,8"            # 1,8 a été lu par apple : confirmé comme lu, pas arrondi


# ------------------------------------------------------------ full sample

def test_full_decision_counts_observers_as_extra_readers():
    reads = [R("apple_vision", "24", "stage1"), R("unimumer", "21", "stage1"), R("trocr", "24", "observe")]
    fd = F.full_decision(ctx(n=2), reads, PLAN, TH)
    assert fd["surface"] == "24" and fd["action"] in ("accept", "need_score")
    assert F.decide_local(ctx(n=2), reads, PLAN, TH, phase=1).action == "need_stage2"   # la vraie décision


# ------------------------------------------------------------ propriétés

def test_an_accepted_surface_was_always_read_by_a_voting_engine():
    rnd = random.Random(7)
    pool = ["24", "21", "1,79", "1,8", "", "3/6", "1/2", "x^2", "x2"]
    engines = [("apple_vision", "stage1"), ("unimumer", "stage1"), ("lighton", "stage2"), ("trocr", "observe")]
    for _ in range(3000):
        reads = []
        for eng, role in engines:
            raw = rnd.choice(pool)
            reads.append(R(eng, raw, role, illegible=(raw == "" or rnd.random() < .1), lp=rnd.choice([-0.05, -0.9])))
        c = ctx(n=rnd.choice([1, 2, 3, 4, 8]), per_char=rnd.choice([0.05, 0.25, 0.25, 1.0]))
        for phase in (1, 2):
            out = F.decide_local(c, reads, PLAN, TH, phase=phase)
            if out.action == "accept":
                readers = {r.surface for r in reads if r.role in ("stage1", "stage2") and F.is_valid(r, TH)}
                assert out.surface in readers
                assert F.ink_guard_ok(out.surface, c, TH)
                assert len({r.family for r in reads if r.surface == out.surface and r.role != "observe"
                            and F.is_valid(r, TH)}) >= 2

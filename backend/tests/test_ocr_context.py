"""Passage avec contexte (énoncé + réponse attendue) : deux passages par moteur, garde-fou
contre ce que le contexte souffle, inscription des tâches et données envoyées au worker."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import OcrCrop, OcrDecision, OcrJob, SystemSetting
from app.services import ocr_cascade, ocr_context, pipeline
from app.services import ocr_fusion as F
from app.services import runtime_settings as rs
from app.services.ocr_surface import to_surface
from app.services.runtime_settings import DEFAULT_ENGINE_PLAN as PLAN, DEFAULT_ENGINE_THRESHOLDS as TH

from tests.test_ocr_pipeline_modes import (  # noqa: F401 (fixtures)
    TOKEN, ClientTransport, Config, FakeEngine, Server, Worker, crops_by_key, mathpix, set_mode, world)


def R(engine, raw, role, *, context=False, cls="num_line", lp=-0.05, illegible=False):
    conf = 0.9 if engine == "apple_vision" else None
    lp = None if engine == "apple_vision" else lp
    return F.ReadIn(engine=engine, family=PLAN["families"][engine], raw=raw, surface=to_surface(raw, cls),
                    illegible=illegible or not raw, confidence_raw=conf, logprob_mean=lp, logprob_min=lp,
                    role=role, context=context)


def C(expected="24", statement="Calcule 4 × 6.", cls="num_line", n=2, h=80):
    echo, text = ocr_context.echo_of({"statement": statement, "expected": expected}, cls)
    return F.CropCtx(cls, ink_width_px=int(h * 0.25 * n), height_px=h, echo=echo, echo_text=text)


# ------------------------------------------------------------------ contexte

def test_echo_contains_the_expected_answer_and_every_number_of_the_statement():
    echo, text = ocr_context.echo_of({"statement": "Un carré a pour côté 3,5 cm et 2 angles.",
                                      "expected": "12,25"}, "num_line")
    assert {"12,25", "3,5", "2"} <= echo and text == ""
    _, text = ocr_context.echo_of({"statement": "Calcule BC.", "expected": "BC = 10"}, "multiline_line")
    assert "BC=10" in text.replace(" ", "")
    assert ocr_context.echo_of(None, "num_line") == (frozenset(), "")


def test_context_of_a_cell_is_the_value_of_that_cell_only():
    class Item:
        statement = "Complète le tableau de prix."
        expected_json = {"cells": [[{"value": 5}, {"value": 1.79}], [{"value": [3, 4]}]]}
    assert ocr_context.for_cell(Item, 0, 1) == {"statement": "Complète le tableau de prix.", "expected": "1.79"}
    assert ocr_context.for_cell(Item, 1, 0)["expected"] == "3/4"
    assert ocr_context.for_cell(Item, 5, 5)["expected"] == ""


def test_no_context_when_there_is_nothing_to_give():
    assert ocr_context.build("", "") is None
    assert ocr_context.build("  $x$ ", "")["statement"] == "x"


# ------------------------------------------------------------------ fusion

def test_both_passes_agreeing_is_one_voice_per_engine_and_stage1_concludes():
    reads = [R("apple_vision", "24", "stage1"), R("apple_vision", "24", "stage1", context=True),
             R("unimumer", "2 4", "stage1"), R("unimumer", "24", "stage1", context=True)]
    out = F.decide_local(C(), reads, PLAN, TH)
    assert (out.action, out.surface) == ("accept", "24")
    assert out.candidates[0]["voices"] == 2 and out.candidates[0]["echo"]


def test_expected_answer_read_only_with_context_never_votes():
    """L'élève a écrit 21, les moteurs avec contexte « voient » 24 : jamais accepté."""
    reads = [R("apple_vision", "", "stage1"), R("apple_vision", "24", "stage1", context=True),
             R("unimumer", "", "stage1"), R("unimumer", "24", "stage1", context=True),
             R("lighton", "", "stage2"), R("lighton", "24", "stage2", context=True)]
    out = F.decide_local(C(), reads, PLAN, TH, phase=2)
    assert out.action == "mathpix" and out.reason == "no_majority"
    cand = next(c for c in out.candidates if c["surface"] == "24")
    assert cand["voices"] == 0 and cand["echo"]


def test_mathpix_can_confirm_what_only_the_context_pass_read():
    """Mathpix ne reçoit jamais de contexte : c'est la lecture indépendante qui manquait."""
    reads = [R("apple_vision", "", "stage1"), R("apple_vision", "24", "stage1", context=True),
             R("unimumer", "", "stage1"), R("unimumer", "24", "stage1", context=True)]
    out = F.decide_mathpix(C(), reads, "24", 0.97, PLAN, TH)
    assert (out.action, out.reason, out.surface) == ("accept", "mathpix_confirmed", "24")


def test_expected_answer_with_one_free_reader_is_accepted_at_stage2():
    reads = [R("apple_vision", "", "stage1"), R("apple_vision", "24", "stage1", context=True),
             R("unimumer", "24", "stage1"), R("unimumer", "24", "stage1", context=True)]
    assert F.decide_local(C(), reads, PLAN, TH, phase=1).action == "need_stage2"   # étape 1 : lectures libres seules
    reads += [R("lighton", "", "stage2"), R("lighton", "24", "stage2", context=True)]
    out = F.decide_local(C(), reads, PLAN, TH, phase=2)
    assert (out.action, out.surface) == ("accept", "24")


def test_an_engine_whose_two_passes_disagree_abstains():
    reads = [R("apple_vision", "21", "stage1"), R("apple_vision", "24", "stage1", context=True),
             R("unimumer", "21", "stage1"), R("unimumer", "21", "stage1", context=True)]
    out = F.decide_local(C(), reads, PLAN, TH, phase=1)
    assert out.action == "need_stage2"
    top = next(c for c in out.candidates if c["surface"] == "21")
    assert top["voices"] == 1 and top["families"] == ["qwen35"]


def test_a_reading_the_context_did_not_suggest_votes_even_from_the_context_pass():
    """Lire 17 alors qu'on a soufflé 24 : le biais joue contre, la voix compte (à l'étape 2)."""
    reads = [R("apple_vision", "", "stage1"), R("apple_vision", "17", "stage1", context=True),
             R("unimumer", "", "stage1"), R("unimumer", "17", "stage1", context=True),
             R("lighton", "", "stage2"), R("lighton", "17", "stage2", context=True)]
    assert F.decide_local(C(), reads, PLAN, TH, phase=1).action == "need_stage2"
    out = F.decide_local(C(), reads, PLAN, TH, phase=2)
    assert (out.action, out.surface) == ("accept", "17")


def test_a_number_of_the_statement_is_treated_like_the_expected_answer():
    reads = [R("apple_vision", "", "stage1"), R("apple_vision", "6", "stage1", context=True),
             R("unimumer", "", "stage1"), R("unimumer", "6", "stage1", context=True),
             R("lighton", "", "stage2"), R("lighton", "6", "stage2", context=True)]
    out = F.decide_local(C(n=1), reads, PLAN, TH, phase=2)
    assert out.action == "mathpix"                     # « 6 » figure dans l'énoncé : soufflé


def test_a_piece_of_the_expected_text_read_only_with_context_does_not_vote():
    cls = "text_line"
    c = C(expected="On applique le théorème de Pythagore", statement="Calcule BC.", cls=cls, n=10)
    reads = [R("lighton", "", "stage1", cls=cls), R("lighton", "le théorème", "stage1", context=True, cls=cls),
             R("apple_vision", "", "stage1", cls=cls),
             R("apple_vision", "le théorème", "stage1", context=True, cls=cls)]
    out = F.decide_local(c, reads, PLAN, TH, phase=1)
    assert out.action != "accept"


def test_failing_context_pass_changes_nothing_compared_to_before():
    reads = [R("apple_vision", "24", "stage1"), R("unimumer", "24", "stage1")]
    assert F.decide_local(C(), reads, PLAN, TH).action == "accept"


def test_context_echo_is_never_accepted_without_a_free_reading():
    """Propriété : une surface soufflée acceptée a toujours été lue SANS contexte par un moteur qui vote ;
    toute surface acceptée a au moins deux familles qui la soutiennent."""
    rnd = random.Random(11)
    pool = ["24", "21", "17", "6", "", "2,4"]
    engines = [("apple_vision", "stage1"), ("unimumer", "stage1"), ("lighton", "stage2"), ("trocr", "observe")]
    ctx = C(n=2)
    for _ in range(4000):
        reads = []
        for eng, role in engines:
            for with_ctx in (False, True):
                if with_ctx and eng == "trocr":
                    continue
                raw = rnd.choice(pool)
                reads.append(R(eng, raw, role, context=with_ctx, lp=rnd.choice([-0.05, -0.9])))
        for phase in (1, 2):
            out = F.decide_local(ctx, reads, PLAN, TH, phase=phase)
            if out.action != "accept":
                continue
            ops = [o for o in F.opinions(F.voting(reads), TH) if o.surface == out.surface]
            assert len({o.family for o in ops}) >= 2
            if F.is_echo(out.surface, ctx):
                assert any(not r.context and r.role != "observe" and r.surface == out.surface
                           and F.has_text(r) for r in reads)


# ------------------------------------------------------------------ inscription et worker

def test_context_jobs_are_enqueued_with_the_free_ones_and_only_for_capable_engines(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    z1 = k[(1, None, None)]
    assert z1.context_json == {"statement": "s", "expected": "24"}
    assert k[(2, 1, None)].context_json["expected"] == "1.79"            # la case, pas tout le tableau
    jobs = {(j.engine, j.kind) for j in db.query(OcrJob).filter_by(crop_id=z1.id)}
    assert jobs == {("apple_vision", "read"), ("apple_vision", "read_ctx"), ("unimumer", "read"),
                    ("unimumer", "read_ctx"), ("trocr", "read")}         # TrOCR ne prend pas de texte

    h = {"Authorization": f"Bearer {TOKEN}"}
    got = world.client.post("/api/ocr-worker/claim", json={"engine": "unimumer", "limit": 64}, headers=h).json()
    by_kind = {}
    for j in got["jobs"]:
        by_kind.setdefault(j["kind"], []).append(j)
    assert all(j["context"] is None for j in by_kind["read"])            # passage libre : rien d'autre que l'image
    assert {j["context"]["expected"] for j in by_kind["read_ctx"] if j["crop_id"] == z1.id} == {"24"}


def test_context_disabled_enqueues_free_reads_only(world, mathpix):
    db = world.db
    set_mode(db, "cascade", ocr_context_enabled=False)
    pipeline.process_batch(db, world.make_batch())
    assert db.query(OcrJob).filter_by(kind="read_ctx").count() == 0


def _run(world, free: dict, ctx: dict):
    fam = {"apple_vision": "apple", "unimumer": "qwen35", "lighton": "pixtral_qwen3", "trocr": "encdec"}

    def table(reads):
        return {cid: ({"raw_text": raw, "logprob_mean": -0.05, "confidence_raw": 0.9} if raw
                      else {"raw_text": "", "illegible": True}) for cid, raw in reads.items()}
    fakes = {n: FakeEngine(n, fam[n], {"reads": {"*": {"raw_text": "", "illegible": True}, **table(free.get(n, {}))},
                                       "reads_ctx": table(ctx.get(n, {})) or {"*": {"raw_text": "", "illegible": True}}})
             for n in fam}
    w = Worker(Config(servers=[Server("local", "http://testserver", TOKEN)]),
               transport=ClientTransport(world.client), engines=fakes)
    for _ in range(6):
        w.run_once()
        world.db.expire_all()
        ocr_cascade.tick(world.db)
        world.db.expire_all()


def test_messy_correct_answer_is_read_thanks_to_the_context(world, mathpix):
    """Libre : Apple ne lit rien, Uni-MuMER lit 24, LightOn rien (sans contexte : illisible,
    donc manuel). Avec contexte, les trois lisent 24 : accepté, sans Mathpix."""
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    z1 = crops_by_key(db, world)[(1, None, None)].id
    _run(world, free={"unimumer": {z1: "24"}},
         ctx={"apple_vision": {z1: "24"}, "unimumer": {z1: "24"}, "lighton": {z1: "24"}})
    d = db.query(OcrDecision).filter_by(crop_id=z1).one()
    assert (d.decision, d.accepted_surface) == ("local_consensus", "24")
    assert not any(c["corr"].startswith("ocr-" + z1[:8]) for c in mathpix.calls)


def test_wrong_answer_is_not_turned_into_the_expected_one(world, mathpix):
    """L'élève a écrit 21. Libre : Apple et LightOn lisent 21 ; avec contexte, tous « voient » 24.
    Jamais 24 accepté : Mathpix (sans contexte) lit 21, qui est confirmé."""
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    crop = crops_by_key(db, world)[(1, None, None)]
    mathpix.by_sha = {crop.image_sha256: ("21", 0.97)}
    _run(world, free={"apple_vision": {crop.id: "21"}, "lighton": {crop.id: "21"}},
         ctx={"apple_vision": {crop.id: "24"}, "unimumer": {crop.id: "24"}, "lighton": {crop.id: "24"}})
    d = db.query(OcrDecision).filter_by(crop_id=crop.id).one()
    assert d.accepted_surface != "24"
    assert (d.decision, d.accepted_surface) == ("mathpix_confirmed", "21")
    assert all(c["hint"] is None for c in mathpix.calls)                  # Mathpix : jamais de contexte


def test_saved_plan_without_context_engines_keeps_the_default_list(world):
    db = world.db
    plan = {k: v for k, v in PLAN.items() if k != "context_engines"}
    db.merge(SystemSetting(key="ocr_engine_plan", value_json={"value": plan}))
    db.commit()
    assert rs.ocr_context_engines(db) == PLAN["context_engines"]

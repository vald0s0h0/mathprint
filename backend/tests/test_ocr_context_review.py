"""Passages avec contexte vus par le professeur (OCRiser) et mesurés par le rapport ; état du Mac
(machine à états du bouton du lot) en cascade et en ombre."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import OcrDecision, ScanBatch, StudentResponse
from app.routers import scans as scans_router
from app.services import ocr_analysis, ocr_cascade, pipeline
from app.services import runtime_settings as rs

from tests.test_ocr_context import _run
from tests.test_ocr_pipeline_modes import crops_by_key, mathpix, set_mode, world  # noqa: F401 (fixtures)


def _wrong_answer_batch(world, mathpix, **settings):
    """L'élève a écrit 21 (attendu 24). Libre : Apple et LightOn lisent 21 ; avec contexte,
    Apple et Uni-MuMER « voient » 24. Tout part chez le professeur (audit à 100 %)."""
    db = world.db
    set_mode(db, "cascade", ocr_audit_share=1.0, **settings)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    crop = crops_by_key(db, world)[(1, None, None)]
    mathpix.by_sha = {crop.image_sha256: ("21", 0.97)}
    _run(world, free={"apple_vision": {crop.id: "21"}, "lighton": {crop.id: "21"}},
         ctx={"apple_vision": {crop.id: "24"}, "unimumer": {crop.id: "24"}})
    return db, crop, db.query(ScanBatch).one()


def test_ocriser_proposes_the_context_readings_labelled_apart(world, mathpix):
    db, crop, b = _wrong_answer_batch(world, mathpix)
    items = {i["sequence"]: i for i in scans_router.list_ocr_items(b.id, db)}
    by = {o["surface"]: o for o in items[1]["ocr_options"]}
    # la lecture soufflée est proposée, étiquetée « +ctx », jamais confondue avec le passage libre
    assert set(by["24"]["engines"]) == {"apple_vision+ctx", "unimumer+ctx"}
    assert {"apple_vision", "lighton", "mathpix"} <= set(by["21"]["engines"])
    # la lecture partagée par le plus de sources d'abord
    assert items[1]["ocr_options"][0]["surface"] == "21"


def test_teacher_choice_measures_the_context_pass_and_its_copy_of_the_expected_answer(world, mathpix):
    db, crop, b = _wrong_answer_batch(world, mathpix)
    resp = db.query(StudentResponse).filter_by(copy_item_id=world.items[1].id).one()
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="21", latex="21"), db, None)
    db.commit()
    assert db.query(OcrDecision).filter_by(crop_id=crop.id).one().truth_surface == "21"
    rep = ocr_analysis.build_report(db)
    g = rep["context_gain"]["engines"]["apple_vision"]
    assert (g["crops"], g["free_ok"], g["ctx_ok"], g["broken"], g["fixed"]) == (1, 1, 0, 1, 0)
    assert (g["copied_expected"], g["wrong_answers"]) == (1, 1)
    u = rep["context_gain"]["engines"]["unimumer"]                 # libre illisible, contexte : l'attendu
    assert (u["free_ok"], u["ctx_ok"], u["copied_expected"]) == (0, 0, 1)
    eng = rep["engines"]["num_line"]
    assert eng["apple_vision"]["accuracy"]["k"] == 1 and eng["apple_vision+ctx"]["accuracy"]["k"] == 0
    pairs = {(p["a"], p["b"]) for p in rep["pairs"]}
    assert ("apple_vision", "apple_vision+ctx") in pairs           # un moteur face à son propre passage avec contexte
    assert "stacks" in rep and "marginal" in rep


def _crop(reads, truth):
    fam = {"apple_vision": "apple", "unimumer": "qwen35", "lighton": "pixtral_qwen3"}
    return {"zone_class": "num_line", "ink": 40, "h": 80, "truth": truth, "scores": {},
            "context": {"statement": "Calcule 4 × 6.", "expected": "24"},
            "reads": [(e, fam[e], raw, raw, False, 0.9 if e == "apple_vision" else None,
                       None if e == "apple_vision" else -0.05, None if e == "apple_vision" else -0.05, wc)
                      for e, raw, wc in reads]}


def test_stack_replay_keeps_or_drops_single_passes(world):
    plan, th = rs.ocr_engine_plan(world.db), rs.ocr_engine_thresholds(world.db)
    c = _crop([("apple_vision", "24", False), ("apple_vision", "24", True), ("unimumer", "24", False),
               ("unimumer", "24", True), ("lighton", "24", False)], truth="24")
    assert ocr_analysis.decide_full(c, plan, th, 1.0)[0] == "accept"
    # sans passage libre, l'écho du contexte ne vote jamais : rien d'automatique
    only_ctx = frozenset({"unimumer+ctx", "apple_vision+ctx"})
    assert ocr_analysis.decide_full(c, plan, th, 1.0, keep=only_ctx)[0] != "accept"
    # une lecture libre d'un autre moteur suffit à la laisser voter
    assert ocr_analysis.decide_full(c, plan, th, 1.0, keep=only_ctx | {"lighton"})[0] == "accept"
    # retirer un moteur entier (ancien usage) ou ses deux passages : même décision
    assert ocr_analysis.decide_full(c, plan, th, 1.0, exclude="unimumer") == \
        ocr_analysis.decide_full(c, plan, th, 1.0, exclude={"unimumer", "unimumer+ctx"})


# ------------------------------------------------------------------ état du Mac

def test_shadow_ocriser_waits_for_the_mac_then_turns_orange(world, mathpix):
    db = world.db
    set_mode(db, "shadow")
    mathpix.by_order = [("2 4", .5), ("5", .97), ("1,79", .4), ("On calcule\nBC = 10", .5)]
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert b.status == "ocr_review_pending"
    prog = ocr_cascade.local_progress(db, b)
    assert prog["mode"] == "shadow" and prog["state"] == "mac_offline" and prog["decided"] < prog["total"]
    view = scans_router._batch_view(db, b)
    ocrize = next(s for s in view["segments"] if s["phase"] == "ocrize")
    assert view["ocr_local"] == prog and ocrize["state"] == "blue"     # pas encore « à vous »

    k = crops_by_key(db, world)
    _run(world, free={"apple_vision": {c.id: "24" for c in k.values()}, "unimumer": {c.id: "24" for c in k.values()}},
         ctx={})
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert ocr_cascade.local_progress(db, b) is None                   # le Mac a fini
    view = scans_router._batch_view(db, b)
    assert view["ocr_local"] is None
    assert next(s for s in view["segments"] if s["phase"] == "ocrize")["state"] == "orange"


def test_overdue_mac_hands_the_decision_to_the_teacher(world, mathpix):
    from datetime import datetime, timedelta, timezone
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert ocr_cascade.local_progress(db, b)["state"] == "mac_offline"
    p = dict(b.progress_json)
    p["ocr_local_pending"] = {**p["ocr_local_pending"],
                              "since": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()}
    b.progress_json = p
    db.commit()
    view = scans_router._batch_view(db, db.query(ScanBatch).one())
    assert view["ocr_local"]["state"] == "mac_overdue"
    assert next(s for s in view["segments"] if s["phase"] == "ocrize")["state"] == "orange"


def test_shadow_ocriser_also_waits_for_every_engine_of_the_full_sample(world, mathpix):
    db = world.db
    set_mode(db, "shadow", ocr_full_sample_share=1.0)
    mathpix.by_order = [("2 4", .5), ("5", .97), ("1,79", .4), ("On calcule\nBC = 10", .5)]
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    assert all(c.full_sample for c in k.values())                     # 100 % : toutes les découpes
    every = {c.id: "24" for c in k.values()}
    _run(world, free={"apple_vision": every, "unimumer": every, "lighton": every, "trocr": every}, ctx={})
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert ocr_cascade.local_progress(db, b) is None
    # une découpe décidée dont le passage complet n'est pas fini : OCRiser attend encore le Mac
    d = db.query(OcrDecision).filter_by(crop_id=k[(1, None, None)].id).one()
    d.full_decision = {"state": "reading"}
    db.commit()
    prog = ocr_cascade.local_progress(db, b)
    assert prog["state"] == "mac_reading" and prog["decided"] == prog["total"] - 1   # le worker vient de sonder

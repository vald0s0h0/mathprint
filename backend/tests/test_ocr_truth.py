"""Vérité terrain, suggestions OCRiser et « C'est juste » de la lecture multicouche."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import OcrAttempt, OcrCrop, OcrDecision, ScanBatch, StudentResponse
from app.routers import scans as scans_router
from app.services import ocr_cascade, ocr_truth, pipeline, refine
from tests.test_ocr_pipeline_modes import (  # noqa: F401  (fixtures + helpers)
    _cascade_reads, crops_by_key, mathpix, set_mode, settle, world,
)


def _run_cascade(world, mathpix, *, reads_fn=_cascade_reads, mathpix_cell="1,79", **settings):
    db = world.db
    set_mode(db, "cascade", **settings)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    mathpix.by_sha = {c.image_sha256: (mathpix_cell, .97) for c in k.values() if c.cell_index == 1}
    b = settle(world, reads_fn(k))
    return db, k, b


def _resp(db, world, seq):
    return db.query(StudentResponse).filter_by(copy_item_id=world.items[seq].id).one()


def _decision(db, crop):
    db.expire_all()
    return db.query(OcrDecision).filter_by(crop_id=crop.id).one()


# ------------------------------------------------------------ vérité terrain

def test_teacher_correction_of_an_automatic_reading_is_counted_as_an_auto_error(world, mathpix):
    db, k, _b = _run_cascade(world, mathpix, ocr_audit_share=1.0)      # tout part chez le professeur
    resp = _resp(db, world, 1)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="29", latex="29"), db, None)
    d = _decision(db, k[(1, None, None)])
    assert (d.decision, d.accepted_surface) == ("local_consensus", "24")
    assert (d.truth_surface, d.truth_source, d.auto_error) == ("29", "audit", True)
    assert d.truth_at is not None
    z = db.query(OcrDecision).filter_by(zone_id=world.zones[1].id, crop_id=None).one()
    assert (z.truth_surface, z.auto_error) == ("29", True)


def test_a_confirmed_automatic_reading_is_not_an_error(world, mathpix):
    db, k, _b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    resp = _resp(db, world, 1)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="2 4"), db, None)
    d = _decision(db, k[(1, None, None)])
    assert (d.truth_surface, d.auto_error) == ("24", False)             # même surface : "2 4" == "24"


def test_only_cells_the_teacher_saw_get_a_truth(world, mathpix):
    db, k, _b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    resp = _resp(db, world, 2)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="1,8", cell_index=1), db, None)
    c0, c1 = _decision(db, k[(2, 0, None)]), _decision(db, k[(2, 1, None)])
    assert c0.truth_surface is None and c0.auto_error is None           # jamais relue : non contrôlée
    assert (c1.truth_surface, c1.auto_error) == ("1,8", True)           # 1,79 accepté, 1,8 écrit
    z = db.query(OcrDecision).filter_by(zone_id=world.zones[2].id, crop_id=None).one()
    assert z.truth_surface is None                                       # la zone n'est pas encore entièrement vue
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="5", cell_index=0), db, None)
    z = db.query(OcrDecision).filter_by(zone_id=world.zones[2].id, crop_id=None).one()
    assert (z.truth_surface, z.auto_error) == ("5|1,8", True)


def test_manual_decisions_are_never_auto_errors(world, mathpix):
    def conflict(k):
        r = _cascade_reads(k)
        c1 = k[(2, 1, None)].id
        r["apple_vision"][c1], r["unimumer"][c1], r["lighton"][c1] = "1,2", "1,79", "1,3"
        return r
    db, k, _b = _run_cascade(world, mathpix, reads_fn=conflict, mathpix_cell="9,99")   # personne ne l'a lu
    resp = _resp(db, world, 2)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="1,79", cell_index=1), db, None)
    d = _decision(db, k[(2, 1, None)])
    assert d.decision == "manual" and d.auto_error is False and d.truth_surface == "1,79"
    assert d.truth_source == "teacher_ocr"                              # pas tirée pour l'audit


def test_multiline_truth_aligns_lines_only_when_the_count_matches(world, mathpix):
    db, k, _b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    resp = _resp(db, world, 3)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="On calcule\nBC = 12"), db, None)
    l0, l1 = _decision(db, k[(3, None, 0)]), _decision(db, k[(3, None, 1)])
    assert (l0.truth_surface, l0.auto_error) == ("On calcule", False)
    assert (l1.truth_surface, l1.auto_error) == ("BC = 12", True)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="une seule ligne"), db, None)
    l0 = _decision(db, k[(3, None, 0)])
    assert l0.truth_surface == "On calcule"                              # nombre de lignes différent : rien de réécrit


def test_no_multilayer_decision_means_no_truth(world, mathpix):
    db = world.db
    set_mode(db, "mathpix")
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("a\nb", .95)]
    pipeline.process_batch(db, world.make_batch())
    resp = _resp(db, world, 1)
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text="24"), db, None)
    assert db.query(OcrDecision).count() == 0


def test_refine_answer_writes_the_truth_too(world, mathpix):
    db, k, _b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    resp, item, zone = _resp(db, world, 2), world.items[2], world.zones[2]
    refine.apply_answer(db, resp, item, zone, {"cells": ["5", "1,8"]})
    db.commit()
    assert _decision(db, k[(2, 0, None)]).auto_error is False
    assert _decision(db, k[(2, 1, None)]).auto_error is True


# ------------------------------------------------------------ suggestions + Contrôle

def test_ocr_items_expose_candidates_most_supported_first_and_the_audit_flag(world, mathpix):
    def conflict(k):
        r = _cascade_reads(k)
        c1 = k[(2, 1, None)].id
        r["apple_vision"][c1], r["unimumer"][c1], r["lighton"][c1] = "1,2", "1,79", "1,3"
        return r
    db, k, b = _run_cascade(world, mathpix, reads_fn=conflict, mathpix_cell="9,99", ocr_audit_share=1.0)
    items = scans_router.list_ocr_items(b.id, db)
    by_seq = {i["sequence"]: i for i in items}
    assert all(i["ocr_audit"] is (i["sequence"] != 2) or i["sequence"] == 2 for i in items)
    assert by_seq[1]["ocr_audit"] is True and by_seq[3]["ocr_audit"] is True      # lecture sûre tirée pour le contrôle
    assert by_seq[1]["ocr_candidates"][0]["surface"] == "24"
    assert by_seq[1]["ocr_candidates"][0]["voices"] == 2
    cells = by_seq[2]["cells"]
    assert cells[1]["ocr_candidates"]                                      # case en conflit : candidats lus
    surfaces = [c["surface"] for c in cells[1]["ocr_candidates"]]
    assert set(surfaces) >= {"1,2", "1,79", "1,3"}
    assert all("engines" in c and "voices" in c for c in cells[1]["ocr_candidates"])
    # jamais de valeur attendue dans les suggestions
    assert "expected" not in str(cells[1]["ocr_candidates"]).lower()


def test_confirm_creates_teacher_ocr_with_the_accepted_reading_as_audit_truth(world, mathpix):
    db, k, b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    assert len(pipeline.pending_ocr_responses(db, world.assessment.id)) == 3
    resp = _resp(db, world, 2)
    out = scans_router.confirm_ocr_reading(resp.id, db, None)
    assert out["remaining"] == 2
    last = pipeline.effective_reading(db, world.zones[2].id)
    assert last.provider == "teacher_ocr" and last.raw_json["cells"] == ["5", "1,79"]
    assert last.raw_json["cell_confidences"] == [1.0, 1.0] and last.raw_json["confirmed_as_correct"] is True
    for key in ((2, 0, None), (2, 1, None)):
        d = _decision(db, k[key])
        assert (d.truth_source, d.auto_error) == ("audit", False) and d.truth_surface == d.accepted_surface
    z = db.query(OcrDecision).filter_by(zone_id=world.zones[2].id, crop_id=None).one()
    assert (z.truth_surface, z.auto_error, z.truth_source) == ("5|1,79", False, "audit")
    # idempotent : un 2e clic ne crée pas une 2e lecture
    n = db.query(OcrAttempt).filter_by(provider="teacher_ocr", zone_id=world.zones[2].id).count()
    scans_router.confirm_ocr_reading(resp.id, db, None)
    assert db.query(OcrAttempt).filter_by(provider="teacher_ocr", zone_id=world.zones[2].id).count() == n


def test_confirm_text_zone_and_unknown_response(world, mathpix):
    from fastapi import HTTPException
    db, k, b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    scans_router.confirm_ocr_reading(_resp(db, world, 1).id, db, None)
    d = _decision(db, k[(1, None, None)])
    assert (d.truth_surface, d.auto_error, d.truth_source) == ("24", False, "audit")
    with pytest.raises(HTTPException) as e:
        scans_router.confirm_ocr_reading("inconnue", db, None)
    assert e.value.status_code == 404


# ------------------------------------------------------------ options de l'assistant OCRiser

def test_review_options_list_every_engine_and_mathpix_grouped_by_surface(world, mathpix):
    def conflict(k):
        r = _cascade_reads(k)
        c1 = k[(2, 1, None)].id
        r["apple_vision"][c1], r["unimumer"][c1], r["lighton"][c1] = "1,2", "1,79", "1,79"
        return r
    db, k, b = _run_cascade(world, mathpix, reads_fn=conflict, mathpix_cell="1,2", ocr_audit_share=1.0)
    items = {i["sequence"]: i for i in scans_router.list_ocr_items(b.id, db)}
    cell = items[2]["cells"][1]["ocr_options"]
    by = {o["surface"]: o for o in cell}
    # Mathpix figure parmi les lectures, même quand la case part en manuel
    assert "mathpix" in by["1,2"]["engines"] and "apple_vision" in by["1,2"]["engines"]
    assert set(by["1,79"]["engines"]) >= {"unimumer", "lighton"}
    # la plus partagée d'abord ; le texte rendu vient d'un moteur LaTeX quand il y en a un
    assert len(cell[0]["engines"]) >= len(cell[-1]["engines"])
    assert by["1,2"]["text"].strip().startswith("\\(")                      # forme brute de Mathpix
    # zone simple : un seul groupe pour "24" et "2 4" (même surface)
    zone = items[1]["ocr_options"]
    assert zone[0]["surface"] == "24" and {"apple_vision", "unimumer"} <= set(zone[0]["engines"])
    # rédaction : un moteur propose la zone entière s'il a lu toutes les lignes
    multi = items[3]["ocr_options"]
    assert any(o["surface"] == "On calcule\nBC = 10" for o in multi)
    assert "expected" not in str(items[2]["cells"][1]["ocr_options"]).lower()


def test_review_options_exist_in_mathpix_mode_too(world, mathpix):
    db = world.db
    set_mode(db, "mathpix")
    mathpix.by_order = [("2 4", .5), ("5", .97), ("1,79", .4), ("On calcule\nBC = 10", .5)]
    b = world.make_batch()
    pipeline.process_batch(db, b)
    items = {i["sequence"]: i for i in scans_router.list_ocr_items(b.id, db)}
    assert items[1]["ocr_options"] == [{"surface": "24", "text": "\\( 2 4 \\)", "engines": ["mathpix"]}]
    assert items[2]["cells"][1]["ocr_options"][0]["engines"] == ["mathpix"]
    assert items[3]["ocr_options"][0]["engines"] == ["mathpix"]


def test_choosing_a_shared_reading_credits_every_engine_that_read_it(world, mathpix):
    db, k, b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    resp = _resp(db, world, 1)
    opt = next(o for o in scans_router.list_ocr_items(b.id, db) if o["sequence"] == 1)["ocr_options"][0]
    scans_router.update_ocr_reading(resp.id, scans_router.OcrReadingIn(text=opt["text"], latex=opt["text"]), db, None)
    d = _decision(db, k[(1, None, None)])
    assert d.truth_surface == "24"
    from app.models import OcrEngineRead
    right = {r.engine for r in db.query(OcrEngineRead).filter_by(crop_id=k[(1, None, None)].id, kind="read")
             if r.surface == d.truth_surface}
    assert {"apple_vision", "unimumer"} <= right                          # pas seulement celui dont on a pris le texte


# ------------------------------------------------------------ reprise de l'assistant

def test_ocr_items_keep_what_the_teacher_already_read_so_he_can_come_back(world, mathpix):
    db, k, b = _run_cascade(world, mathpix, ocr_audit_share=1.0)
    units = pipeline.pending_ocr_units(db, world.assessment.id)
    # compteur en lectures (cases), comme l'assistant : un tableau compte ses cases douteuses
    assert units[world.items[1].id] == 1 and units[world.items[2].id] >= 1
    scans_router.update_ocr_reading(_resp(db, world, 1).id, scans_router.OcrReadingIn(text="24"), db, None)
    scans_router.update_ocr_reading(_resp(db, world, 2).id,
                                    scans_router.OcrReadingIn(text="1,8", cell_index=1), db, None)
    items = {i["sequence"]: i for i in scans_router.list_ocr_items(b.id, db)}
    assert set(items) == {1, 2, 3}                                     # rien ne disparaît une fois lu
    assert items[1]["ocr_pending"] is False and items[1]["ocr_touched"] == ["zone"]
    assert items[1]["ocr_text"] == "24"
    assert items[2]["ocr_touched"] == [1] and items[2]["cells"][1]["ocr_text"] == "1,8"
    assert items[3]["ocr_pending"] is True and items[3]["ocr_touched"] == []
    assert world.items[1].id not in pipeline.pending_ocr_units(db, world.assessment.id)

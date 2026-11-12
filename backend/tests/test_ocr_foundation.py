"""Lot 1 de la lecture OCR multicouche : tables, réglages, et correction du
fournisseur `cv_ocr` (tableau avec case à choix) qui finissait toujours en OCRiser."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models
from app.db import Base
from app.models import (
    Assessment, Copy, CopyItem, DocumentPage, OcrAttempt, OcrCrop, OcrDecision, OcrEngineRead,
    OcrJob, OcrMontage, ResponseZone, ScanBatch, SchoolClass, Student, StudentResponse,
)
from app.routers import misc, scans as scans_router
from app.services import pipeline, runtime_settings as rs


@pytest.fixture
def db():
    from tests.pgsupport import make_engine
    session = sessionmaker(bind=make_engine())()
    yield session
    session.close()


def _seed_table(db, response_type="table_fill"):
    cls = SchoolClass(name="4C", grade_level="4e"); db.add(cls); db.flush()
    a = Assessment(class_id=cls.id, title="T", type="control", note_base=20)
    db.add(a); db.flush()
    stu = Student(class_id=cls.id, name="A B", llm_pseudonym="x"); db.add(stu); db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed"); db.add(copy); db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1); db.add(page); db.flush()
    cells = [[{"type": "integer", "value": 5}, {"type": "single_choice", "value": "A"}]]
    from tests.pgsupport import ensure_catalog
    ensure_catalog(db)
    item = CopyItem(copy_id=copy.id, catalog_id="c", sequence=1, difficulty=3,
                    response_type=response_type, statement="s", correction="c",
                    expected_json={"type": "table", "cells": cells},
                    grading_json={"comparator": "table_cells", "max_score": 2,
                                  "bareme_points": 1.5, "cells": cells})
    db.add(item); db.flush()
    zone = ResponseZone(page_id=page.id, item_id=item.id, type=response_type, x_pt=1, y_pt=1,
                        w_pt=10, h_pt=10, meta_json={"cells": [[{}, {}]]})
    db.add(zone); db.flush()
    resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id); db.add(resp); db.flush()
    return a, item, zone, resp


# ------------------------------------------------------------- bug cv_ocr

def test_cv_ocr_table_is_a_reading_and_not_pending_when_confident(db):
    a, _item, zone, _resp = _seed_table(db)
    db.add(OcrAttempt(zone_id=zone.id, provider="cv_ocr", confidence=0.97,
                      raw_json={"cells": ["5", "A"], "cell_latex": ["5", ""],
                                "cell_confidences": [0.97, 0.99]}))
    db.flush()
    assert pipeline.effective_reading(db, zone.id).provider == "cv_ocr"
    assert pipeline.pending_ocr_responses(db, a.id) == []


def test_cv_ocr_table_accepts_the_teacher_rewrite(db):
    """Avant le correctif, update_ocr_reading ne retrouvait pas la lecture cv_ocr
    (cells vide) et répondait 422 « case OCR inconnue »."""
    a, _item, zone, resp = _seed_table(db)
    db.add(OcrAttempt(zone_id=zone.id, provider="cv_ocr", confidence=0.4,
                      raw_json={"cells": ["S", "A"], "cell_latex": ["S", ""],
                                "cell_confidences": [0.4, 0.99]}))
    db.commit()
    assert len(pipeline.pending_ocr_responses(db, a.id)) == 1
    out = scans_router.update_ocr_reading(
        resp.id, scans_router.OcrReadingIn(text="5", latex="5", cell_index=0), db, None)
    assert out["remaining"] == 0
    last = pipeline.effective_reading(db, zone.id)
    assert last.provider == "teacher_ocr" and last.raw_json["cells"] == ["5", "A"]


def test_ocr_fusion_is_a_reading_provider(db):
    _a, _item, zone, _resp = _seed_table(db)
    db.add(OcrAttempt(zone_id=zone.id, provider="ocr_fusion", confidence=0.95,
                      raw_json={"cells": ["5", "A"]}))
    db.flush()
    assert pipeline.effective_reading(db, zone.id).provider == "ocr_fusion"


# ----------------------------------------------------------------- tables

def test_new_tables_create_and_read_back(db):
    a, item, zone, _resp = _seed_table(db)
    batch = ScanBatch(assessment_id=a.id); db.add(batch); db.flush()
    crop = OcrCrop(batch_id=batch.id, zone_id=zone.id, cell_index=0, image_path="x.png",
                   image_sha256="ab" * 32, zone_class="num_line", charset="0123456789,.-")
    db.add(crop); db.flush()
    job = OcrJob(crop_id=crop.id, engine="unimumer"); db.add(job)
    db.add(OcrEngineRead(crop_id=crop.id, job_id="j1", engine="unimumer", raw_text="1,79",
                         surface="1,79", logprob_mean=-0.02, alternatives_json=[{"t": "1,79"}]))
    db.add(OcrDecision(crop_id=crop.id, zone_id=zone.id, decision="local_consensus",
                       candidates_json=[{"surface": "1,79", "votes": 2}], accepted_surface="1,79"))
    db.add(OcrMontage(image_path="m.png", crops_json=[{"crop_id": crop.id, "box": [0, 0, 5, 5]}]))
    db.commit()

    got = db.query(OcrCrop).one()
    assert (got.status, got.full_sample, got.line_index) == ("pending", False, None)
    j = db.query(OcrJob).one()
    assert (j.status, j.kind, j.attempts, j.candidates_json) == ("pending", "read", 0, [])
    r = db.query(OcrEngineRead).one()
    assert r.raw_text == "1,79" and r.illegible is False and r.scores_json == {}
    d = db.query(OcrDecision).one()
    assert d.auto_error is None and d.truth_surface is None and d.audit is False and d.mode == "shadow"
    assert db.query(OcrMontage).one().cost_eur == 0


def test_engine_read_is_idempotent_by_job_id(db):
    a, _item, zone, _resp = _seed_table(db)
    batch = ScanBatch(assessment_id=a.id); db.add(batch); db.flush()
    crop = OcrCrop(batch_id=batch.id, zone_id=zone.id); db.add(crop); db.flush()
    db.add(OcrEngineRead(crop_id=crop.id, job_id="j1", engine="e")); db.commit()
    db.add(OcrEngineRead(crop_id=crop.id, job_id="j1", engine="e"))
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("cls", [OcrCrop, OcrJob, OcrEngineRead, OcrDecision, OcrMontage])
def test_new_tables_compile_on_postgres(cls):
    ddl = str(CreateTable(cls.__table__).compile(dialect=postgresql.dialect()))
    assert cls.__tablename__ in ddl


# --------------------------------------------------------------- réglages

def _set(db, key, value, role="admin"):
    return misc.set_system_setting(misc.SettingIn(key=key, value=value), db,
                                   SimpleNamespace(id="u1", role=role))


def test_defaults_are_safe(db):
    assert rs.ocr_mode(db) == "mathpix"
    assert rs.ocr_setting(db, "ocr_audit_share") == pytest.approx(0.02)
    assert rs.ocr_setting(db, "ocr_full_sample_share") == pytest.approx(0.05)
    assert rs.ocr_setting(db, "ocr_accept_confidence") == pytest.approx(0.95)
    assert rs.ocr_setting(db, "ocr_montage_enabled") is False
    assert (rs.ocr_setting(db, "ocr_montage_max_crops"), rs.ocr_setting(db, "ocr_montage_flush_minutes"),
            rs.ocr_setting(db, "ocr_lease_minutes")) == (12, 10, 15)
    plan = rs.ocr_engine_plan(db)
    assert plan["classes"]["num_line"]["stage1"] == ["apple_vision", "unimumer"]
    assert plan["correlated_pairs"] == [["qwen35", "pixtral_qwen3"]]
    rs.validate_ocr_plan(plan)


def test_default_plan_never_mixes_roles_and_every_engine_has_a_family():
    for cls, roles in rs.DEFAULT_ENGINE_PLAN["classes"].items():
        flat = [e for r in roles.values() for e in r]
        assert len(flat) == len(set(flat)), cls
        assert set(flat) <= set(rs.DEFAULT_ENGINE_PLAN["families"])


def test_settings_roundtrip_and_validation(db):
    _set(db, "ocr_mode", {"value": "shadow"})
    assert rs.ocr_mode(db) == "shadow"
    _set(db, "ocr_audit_share", {"value": 0.1})
    assert rs.ocr_setting(db, "ocr_audit_share") == pytest.approx(0.1)
    for key, bad in [("ocr_mode", "auto"), ("ocr_audit_share", 1.5), ("ocr_montage_max_crops", 0),
                     ("ocr_montage_enabled", "oui"), ("ocr_score_margin", -1),
                     ("ocr_lease_minutes", True)]:
        with pytest.raises(HTTPException) as e:
            _set(db, key, {"value": bad})
        assert e.value.status_code == 422, key
    assert rs.ocr_mode(db) == "shadow"          # rien n'a été écrasé par un refus


def test_invalid_stored_value_falls_back_to_default(db):
    from app.models import SystemSetting
    db.add(SystemSetting(key="ocr_mode", value_json={"value": "n'importe quoi"}))
    db.add(SystemSetting(key="ocr_engine_plan", value_json={"value": {"classes": 3}}))
    db.commit()
    assert rs.ocr_mode(db) == "mathpix"
    assert rs.ocr_engine_plan(db) is rs.DEFAULT_ENGINE_PLAN


def test_plan_validation_rejects_incoherent_plans(db):
    good = rs.DEFAULT_ENGINE_PLAN
    import copy as cp
    unknown = cp.deepcopy(good); unknown["classes"]["num_line"]["stage2"] = ["ghost"]
    twice = cp.deepcopy(good); twice["classes"]["num_line"]["observe"] = ["unimumer"]
    single = cp.deepcopy(good); single["classes"]["text_line"]["stage1"] = ["lighton"]
    bad_class = cp.deepcopy(good); bad_class["classes"]["other"] = {"stage1": ["trocr", "lighton"]}
    for plan in (unknown, twice, single, bad_class, "x"):
        with pytest.raises(HTTPException):
            _set(db, "ocr_engine_plan", {"value": plan})
    _set(db, "ocr_engine_plan", {"value": good})


def test_thresholds_merge_on_defaults(db):
    _set(db, "ocr_engine_thresholds", {"value": {"unimumer": {"min_logprob_mean": -0.2},
                                                  "newengine": {"min_confidence": 0.7}}})
    t = rs.ocr_engine_thresholds(db)
    assert t["unimumer"]["min_logprob_mean"] == -0.2
    assert t["apple_vision"]["min_confidence"] == 0.5 and t["newengine"]["min_confidence"] == 0.7
    assert t["ink_guard"]["max_per_char"] == 0.45


def test_get_system_settings_exposes_defaults_and_hides_the_token(db):
    _set(db, "ocr_worker_token", {"value": "t" * 32})
    out = misc.get_system_settings(db)
    assert "ocr_worker_token" not in out
    assert out["ocr_mode"] == {"value": "mathpix"}
    assert "stage1" in out["ocr_engine_plan"]["value"]["classes"]["num_line"]


def test_settings_say_whether_a_token_exists_without_revealing_it(db, monkeypatch):
    assert misc.get_system_settings(db)["ocr_worker_token_set"] == {"value": False, "source": "setting"}
    _set(db, "ocr_worker_token", {"value": "z" * 40})
    out = misc.get_system_settings(db)
    assert out["ocr_worker_token_set"] == {"value": True, "source": "setting"} and "z" * 40 not in str(out)
    monkeypatch.setattr(rs.settings, "ocr_worker_token", "e" * 40)
    assert misc.get_system_settings(db)["ocr_worker_token_set"]["source"] == "env"


def test_worker_token_env_wins_and_short_token_refused(db, monkeypatch):
    with pytest.raises(HTTPException):
        _set(db, "ocr_worker_token", {"value": "court"})
    assert rs.ocr_worker_token(db) == ""
    _set(db, "ocr_worker_token", {"value": "d" * 30})
    assert rs.ocr_worker_token(db) == "d" * 30
    monkeypatch.setattr(rs.settings, "ocr_worker_token", "e" * 30)
    assert rs.ocr_worker_token(db) == "e" * 30

"""Lot 5 : les trois modes de lecture (`mathpix`, `shadow`, `cascade`) de bout en bout.

Le scan est simulé en remplaçant la détection de page (`worker_cv.analyze_page`) par une
page recalée synthétique : les découpes, la détection de vide, l'inscription des tâches,
l'API du worker (moteurs `fake`), la fusion, Mathpix et la reprise de la pipeline sont
RÉELS. Mathpix est un faux qui enregistre ce qu'on lui envoie."""
import hashlib
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocr_worker.config import Config, Server
from ocr_worker.engines.fake import FakeEngine
from ocr_worker.waves import Worker

from app.config import settings as cfg
from app.db import Base, get_db
from app.models import (
    Assessment, Copy, CopyItem, DocumentPage, FileObject, GradingDecision, OcrAttempt, OcrCrop, OcrDecision,
    OcrEngineRead, ResponseZone, ScanBatch, SchoolClass, Student, StudentResponse, SystemSetting,
)
from app.routers import ocr_worker as ocr_worker_router
from app.services import ocr_cascade, ocr_queue, pipeline, providers, worker_cv
from tests.test_ocr_worker_loop import ClientTransport

TOKEN = "k" * 40
A4_H = 842


class World:
    def __init__(self, db, tmp):
        self.db, self.tmp = db, tmp
        cls = SchoolClass(name="3A", grade_level="3e"); db.add(cls); db.flush()
        self.assessment = Assessment(class_id=cls.id, title="Ctrl", type="control", note_base=20)
        db.add(self.assessment); db.flush()
        stu = Student(class_id=cls.id, name="E X", llm_pseudonym="p"); db.add(stu); db.flush()
        self.copy = Copy(assessment_id=self.assessment.id, student_id=stu.id, status="printed")
        db.add(self.copy); db.flush()
        self.page = DocumentPage(copy_id=self.copy.id, page_no=1); db.add(self.page); db.flush()
        self.zones: dict[int, ResponseZone] = {}
        self.items: dict[int, CopyItem] = {}
        # 1 : entier sur une ligne
        self._add(1, "short_text", {"type": "integer", "value": 24},
                  {"comparator": "numeric", "max_score": 1, "bareme_points": 1}, (60, 700, 120, 32), {})
        # 2 : tableau, 2 cases écrites (entier, décimal)
        cells = [[{"type": "integer", "value": 5}, {"type": "decimal", "value": 1.79}]]
        meta = {"cells": [[{"x_pt": 60, "y_pt": 600, "w_pt": 100, "h_pt": 26},
                           {"x_pt": 170, "y_pt": 600, "w_pt": 100, "h_pt": 26}]]}
        self._add(2, "table_fill", {"type": "table", "cells": cells},
                  {"comparator": "table_cells", "max_score": 2, "bareme_points": 1, "cells": cells}, (60, 590, 220, 50), meta)
        # 3 : rédaction de 3 lignes (la 3e reste vide)
        self._add(3, "multiline_text", {"type": "rubric", "steps": [{"expected_text": "BC = 10"}]},
                  {"comparator": "rubric", "max_score": 2, "bareme_points": 1, "lines": 3},
                  (60, 440, 400, 3 * 25.5 + 11.3), {})
        self.canvas = np.full((int(A4_H * worker_cv.SCALE), int(595 * worker_cv.SCALE), 3), 255, np.uint8)
        self.db.commit()

    def _add(self, seq, rtype, expected, grading, geo, meta):
        from tests.pgsupport import ensure_catalog
        ensure_catalog(self.db)
        item = CopyItem(copy_id=self.copy.id, catalog_id="c", sequence=seq, difficulty=3, response_type=rtype,
                        statement="s", correction="c", expected_json=expected, grading_json=grading)
        self.db.add(item); self.db.flush()
        z = ResponseZone(page_id=self.page.id, item_id=item.id, type=rtype, x_pt=geo[0], y_pt=geo[1],
                         w_pt=geo[2], h_pt=geo[3], meta_json=meta, padding_pt=2.0)
        self.db.add(z); self.db.flush()
        self.items[seq], self.zones[seq] = item, z

    def ink(self, x_pt, y_pt, h_pt, text, scale=1.1):
        px, py = worker_cv.pt_to_px(x_pt + 4, y_pt + h_pt * 0.25)
        cv2.putText(self.canvas, text, (int(px), int(py)), cv2.FONT_HERSHEY_SIMPLEX, scale, (40, 30, 20), 3, cv2.LINE_AA)

    def draw_all(self):
        z = self.zones
        self.ink(z[1].x_pt, z[1].y_pt, 32, "24", 1.4)
        for i, t in enumerate(("5", "1,79")):
            c = z[2].meta_json["cells"][0][i]
            self.ink(c["x_pt"], c["y_pt"], 26, t, 1.1)
        for k, t in enumerate(("On calcule", "BC = 10")):          # 2 lignes écrites, la 3e vide
            b, h = ocr_cascade.line_bands(z[3].y_pt, z[3].h_pt, 3)[k]
            self.ink(z[3].x_pt, b, h, t, 1.0)

    def make_batch(self):
        scan = self.tmp / "scan.png"
        scan.write_bytes(b"x")
        fo = FileObject(owner_type="scan_batch", owner_id="x", storage_path=str(scan))
        self.db.add(fo); self.db.flush()
        batch = ScanBatch(assessment_id=self.assessment.id, source_file_id=fo.id, status="uploaded")
        self.db.add(batch); self.db.commit()
        return batch


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    monkeypatch.setattr(cfg, "ocr_worker_token", TOKEN)
    from tests.pgsupport import make_engine
    engine = make_engine()
    Session = sessionmaker(bind=engine)
    db = Session()
    w = World(db, tmp_path)
    w.draw_all()
    monkeypatch.setattr(worker_cv, "count_pages", lambda p: 1)
    monkeypatch.setattr(worker_cv, "iter_raster_any", lambda p: iter([w.canvas]))
    monkeypatch.setattr(worker_cv, "analyze_page", lambda img, forced_page_id=None: worker_cv.PageAnalysis(
        page_id=w.page.id, status="registered", marker_count=4, reprojection_error_px=1.0, blur=200.0,
        warped=w.canvas.copy()))
    app = FastAPI()
    app.include_router(ocr_worker_router.router)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    w.client = TestClient(app)
    w.Session = Session
    yield w
    db.close()


def set_mode(db, mode, **extra):
    for k, v in {"ocr_mode": mode, "ocr_audit_share": 0.0, "ocr_shadow_audit_share": 0.0,
                 "ocr_full_sample_share": 0.0, **extra}.items():
        db.merge(SystemSetting(key=k, value_json={"value": v}))
    db.commit()


class FakeMathpix:
    """Remplace providers.mathpix_ocr : réponses par image (sha256), et trace des indices reçus."""
    def __init__(self):
        self.by_sha: dict[str, tuple[str, float]] = {}
        self.by_order: list[tuple[str, float]] = []
        self.calls: list[dict] = []
        self.default = ("?", 0.4)

    def __call__(self, db, image_bytes, correlation_id, expected_hint=None):
        sha = hashlib.sha256(image_bytes).hexdigest()
        self.calls.append({"sha": sha, "hint": expected_hint, "corr": correlation_id})
        if sha in self.by_sha:
            text, conf = self.by_sha[sha]
        elif self.by_order:
            text, conf = self.by_order.pop(0)
        else:
            text, conf = self.default
        return {"latex": f"\\( {text} \\)", "text": text, "confidence": conf, "raw": {"fake": True}}


@pytest.fixture
def mathpix(monkeypatch):
    fake = FakeMathpix()
    monkeypatch.setattr(providers, "mathpix_ocr", fake)
    monkeypatch.setattr(providers, "offline", lambda db, name: False)
    return fake


def run_worker(world, engines: dict[str, dict]) -> dict:
    """engines : {nom: {crop_id: lecture brute}} ; une clé absente = illisible."""
    fakes = {}
    fam = {"apple_vision": "apple", "unimumer": "qwen35", "lighton": "pixtral_qwen3", "trocr": "encdec"}
    for name, reads in engines.items():
        table = {"reads": {cid: ({"raw_text": raw, "logprob_mean": -0.05, "confidence_raw": 0.9}
                                 if raw is not None else {"raw_text": "", "illegible": True})
                           for cid, raw in reads.items()},
                 "scores": {}}
        fakes[name] = FakeEngine(name, fam[name], table)
    w = Worker(Config(servers=[Server("local", "http://testserver", TOKEN)]),
               transport=ClientTransport(world.client), engines=fakes)
    return w.run_once()


def crops_by_key(db, world):
    """{(séquence, cell_index, line_index): crop}"""
    seq_of = {z.id: s for s, z in world.zones.items()}
    return {(seq_of[c.zone_id], c.cell_index, c.line_index): c for c in db.query(OcrCrop).all()}


def settle(world, engines, rounds=12):
    """Alterne worker et orchestrateur jusqu'à ce que le lot sorte de l'attente locale."""
    db = world.db
    for _ in range(rounds):
        run_worker(world, engines)
        db.expire_all()
        ocr_cascade.tick(db)
        db.expire_all()
        batch = db.query(ScanBatch).one()
        if batch.status != "ocr_local_pending":
            return batch
    return db.query(ScanBatch).one()


# ============================================================ mode mathpix

def test_mathpix_mode_changes_nothing(world, mathpix):
    db = world.db
    batch = world.make_batch()
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(db, batch)
    assert db.query(OcrCrop).count() == 0 and db.query(OcrDecision).count() == 0
    assert db.query(OcrAttempt).filter_by(provider="mathpix").count() == 3
    assert db.query(StudentResponse).count() == 3
    assert db.query(ScanBatch).one().status != "ocr_local_pending"
    assert all(c["hint"] is None for c in mathpix.calls)


# ============================================================ mode shadow

def _run_mathpix_mode(world, mathpix):
    batch = world.make_batch()
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(world.db, batch)
    return {r.copy_item_id: (r.final_text) for r in world.db.query(StudentResponse).all()}, \
        sorted((d.status, d.score) for d in world.db.query(GradingDecision).all())


def test_shadow_keeps_mathpix_as_the_decision_and_records_local_reads(world, mathpix):
    db = world.db
    set_mode(db, "shadow")
    texts, grades = _run_mathpix_mode(world, mathpix)
    assert db.query(ScanBatch).one().status != "ocr_local_pending"          # le lot n'attend pas le Mac
    k = crops_by_key(db, world)
    assert set(k) == {(1, None, None), (2, 0, None), (2, 1, None), (3, None, 0), (3, None, 1)}  # ligne vide ignorée
    assert {c.mode for c in k.values()} == {"shadow"}
    assert [m for m in mathpix.calls if m["hint"] is not None] == []

    # le Mac lit : apple + unimumer d'accord partout, sauf la case 1,79 (apple lit 1,8)
    reads = {"apple_vision": {k[(1, None, None)].id: "24", k[(2, 0, None)].id: "5", k[(2, 1, None)].id: "1,8",
                              k[(3, None, 0)].id: "On calcule", k[(3, None, 1)].id: "BC = 10"},
             "unimumer": {k[(1, None, None)].id: "2 4", k[(2, 0, None)].id: "5", k[(2, 1, None)].id: "1,79"},
             "lighton": {k[(2, 1, None)].id: "1,79", k[(3, None, 0)].id: "On calcule", k[(3, None, 1)].id: "BC = 10"},
             "trocr": {c.id: "x" for c in k.values()}}
    for _ in range(4):
        run_worker(world, reads); db.expire_all(); ocr_cascade.tick(db)
    db.expire_all()
    dec = {key: db.query(OcrDecision).filter_by(crop_id=c.id).one() for key, c in k.items()}
    assert dec[(1, None, None)].decision == "local_consensus" and dec[(1, None, None)].accepted_surface == "24"
    assert dec[(2, 0, None)].decision == "local_consensus"
    # 1,79 : apple 1,8 contre unimumer/lighton 1,79 (paire corrélée contredite) -> Mathpix, déjà lu en ligne
    assert dec[(2, 1, None)].decision == "mathpix_confirmed" and dec[(2, 1, None)].accepted_surface == "1,79"
    assert all(d.mode == "shadow" and d.audit is False for d in dec.values())
    # lignes : Mathpix n'a lu que la zone entière -> pas de Mathpix par ligne en mode ombre
    assert dec[(3, None, 0)].decision == "local_consensus"
    # comparaison de zone, jamais de note
    zrows = db.query(OcrDecision).filter(OcrDecision.crop_id.is_(None)).all()
    assert len(zrows) == 3 and all(z.mode == "shadow" for z in zrows)
    table_row = next(z for z in zrows if z.zone_id == world.zones[2].id)
    assert table_row.full_decision["local"] == ["5", "1,79"] and table_row.full_decision["agree"] == [True, True]
    # la note vient de Mathpix : identique au mode mathpix
    assert db.query(OcrAttempt).filter_by(provider="ocr_fusion").count() == 0
    assert db.query(StudentResponse).count() == 3


def test_shadow_grades_are_identical_to_mathpix_mode(tmp_path, monkeypatch, mathpix):
    out = []
    for mode in ("mathpix", "shadow"):
        d = tmp_path / mode; d.mkdir()
        monkeypatch.setattr(cfg, "data_dir", d)
        from tests.pgsupport import make_engine
        db = sessionmaker(bind=make_engine())()
        w = World(db, d); w.draw_all()
        monkeypatch.setattr(worker_cv, "count_pages", lambda p: 1)
        monkeypatch.setattr(worker_cv, "iter_raster_any", lambda p, w=w: iter([w.canvas]))
        monkeypatch.setattr(worker_cv, "analyze_page", lambda img, forced_page_id=None, w=w: worker_cv.PageAnalysis(
            page_id=w.page.id, status="registered", marker_count=4, reprojection_error_px=1.0, blur=200.0, warped=w.canvas.copy()))
        set_mode(db, mode)
        mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
        pipeline.process_batch(db, w.make_batch())
        out.append((sorted((d_.status, d_.score) for d_ in db.query(GradingDecision).all()),
                    sorted(r.final_text for r in db.query(StudentResponse).all()),
                    db.query(ScanBatch).one().status))
        db.close()
    assert out[0] == out[1]


# ============================================================ mode cascade

def _cascade_reads(k, *, conflict_cell=False):
    z1, c0, c1 = k[(1, None, None)].id, k[(2, 0, None)].id, k[(2, 1, None)].id
    l0, l1 = k[(3, None, 0)].id, k[(3, None, 1)].id
    apple = {z1: "24", c0: "5", c1: "1,8", l0: "On calcule", l1: "BC = 10"}
    unimumer = {z1: "2 4", c0: "5", c1: "1,79", l0: "On calcule", l1: "BC = 10"}
    lighton = {z1: "24", c0: "5", c1: "1,79", l0: "On calcule", l1: "BC = 10"}
    if conflict_cell:
        apple[c1], unimumer[c1], lighton[c1] = "1,2", "1,79", "1,3"
    return {"apple_vision": apple, "unimumer": unimumer, "lighton": lighton,
            "trocr": {c: "x" for c in (z1, c0, c1, l0, l1)}}


def test_cascade_waits_for_the_mac_then_grades_from_the_fusion(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    batch = world.make_batch()
    pipeline.process_batch(db, batch)
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert b.status == "ocr_local_pending" and db.query(StudentResponse).count() == 0
    assert mathpix.calls == []                                   # aucun appel Mathpix tant que le local n'a pas lu
    k = crops_by_key(db, world)
    assert {c.mode for c in k.values()} == {"cascade"} and all(c.status == "pending" for c in k.values())
    prog = ocr_cascade.local_progress(db, b)
    assert (prog["decided"], prog["total"], prog["worker_online"]) == (0, 5, False)
    from app.routers import scans as scans_router
    view = scans_router._batch_view(db, b)
    assert view["status"] == "ocr_local_pending" and view["ocr_local"] == prog   # affiché par ScanLotRow

    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    b = settle(world, _cascade_reads(k))
    assert b.status in ("graded", "review_pending", "finalized", "overlay_ready", "ocr_confirmed")
    dec = {key: db.query(OcrDecision).filter_by(crop_id=c.id).one() for key, c in k.items()}
    assert dec[(1, None, None)].decision == "local_consensus" and dec[(1, None, None)].reason == "local_consensus"
    assert dec[(2, 1, None)].decision == "mathpix_confirmed"
    assert all(c["hint"] is None for c in mathpix.calls) and len(mathpix.calls) == 1   # une seule case chez Mathpix

    fusion = {a.zone_id: a for a in db.query(OcrAttempt).filter_by(provider="ocr_fusion").all()}
    assert len(fusion) == 3
    short = fusion[world.zones[1].id]
    assert (short.text, short.confidence) == ("24", 0.95)       # ocr_accept_confidence
    table = fusion[world.zones[2].id]
    assert table.raw_json["cells"] == ["5", "1,79"] and table.raw_json["cell_confidences"] == [0.95, 0.95]
    essay = fusion[world.zones[3].id]
    assert essay.text == "On calcule\nBC = 10" and essay.confidence == 0.95
    assert pipeline.effective_reading(db, world.zones[1].id).provider == "ocr_fusion"
    # les réponses sont écrites une seule fois, la notation a eu lieu
    assert db.query(StudentResponse).count() == 3 and db.query(GradingDecision).count() >= 3
    zrows = db.query(OcrDecision).filter(OcrDecision.crop_id.is_(None)).all()
    assert len(zrows) == 3 and all(z.mode == "cascade" for z in zrows)
    assert {a.provider for a in db.query(OcrAttempt).filter(OcrAttempt.provider.like("ocr_pending%")).all()} == {"ocr_pending_done"}


def test_cascade_conflict_reaches_ocriser_with_its_candidates_and_nothing_invented(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    mathpix.by_sha = {k[(2, 1, None)].image_sha256: ("9,99", .99)}      # Mathpix propose une valeur que personne n'a lue
    b = settle(world, _cascade_reads(k, conflict_cell=True))
    assert b.status == "ocr_review_pending"
    d = db.query(OcrDecision).filter_by(crop_id=k[(2, 1, None)].id).one()
    assert d.decision == "manual" and d.reason.endswith("mathpix_not_read_locally")
    assert d.accepted_surface is None                                     # Mathpix n'a rien introduit
    assert {c["surface"] for c in d.candidates_json} >= {"1,2", "1,79", "1,3"}
    att = db.query(OcrAttempt).filter_by(provider="ocr_fusion", zone_id=world.zones[2].id).one()
    assert att.raw_json["cells"] == ["5", ""] and att.raw_json["cell_confidences"] == [0.95, 0.0]
    assert att.confidence == 0.0
    pending = pipeline.pending_ocr_responses(db, world.assessment.id)
    assert [r.zone_id for r in pending] == [world.zones[2].id]
    # les autres zones n'attendent pas le professeur
    assert db.query(GradingDecision).count() == 0                         # rien n'est noté avant OCRiser


def test_cascade_without_worker_stays_pending_and_resumes_on_return(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    for _ in range(3):
        db.expire_all(); ocr_cascade.tick(db)                              # le Mac est éteint
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert b.status == "ocr_local_pending"
    assert ocr_cascade.local_progress(db, b)["worker_online"] is False
    k = crops_by_key(db, world)
    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    b = settle(world, _cascade_reads(k))                                    # il revient
    assert b.status != "ocr_local_pending"
    assert db.query(StudentResponse).count() == 3


def test_cascade_illegible_everywhere_goes_to_manual_without_calling_mathpix(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    reads = {name: {c.id: None for c in k.values()} for name in ("apple_vision", "unimumer", "lighton", "trocr")}
    b = settle(world, reads)
    assert b.status == "ocr_review_pending"
    assert mathpix.calls == []
    assert {d.reason for d in db.query(OcrDecision).filter(OcrDecision.crop_id.isnot(None)).all()} == {"illegible_consensus"}


def test_cascade_mathpix_unavailable_goes_to_manual_not_stuck(world, mathpix, monkeypatch):
    db = world.db
    set_mode(db, "cascade")
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)

    def refuse(*a, **kw):
        raise providers.BudgetExceeded("quota")
    monkeypatch.setattr(providers, "mathpix_ocr", refuse)
    b = settle(world, _cascade_reads(k))
    d = db.query(OcrDecision).filter_by(crop_id=k[(2, 1, None)].id).one()
    assert d.decision == "manual" and "mathpix_unavailable" in d.reason
    assert b.status == "ocr_review_pending"


def test_audit_share_one_sends_every_automatic_zone_to_ocriser_as_control(world, mathpix):
    db = world.db
    set_mode(db, "cascade", ocr_audit_share=1.0)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    b = settle(world, _cascade_reads(k))
    assert b.status == "ocr_review_pending"
    zrows = db.query(OcrDecision).filter(OcrDecision.crop_id.is_(None)).all()
    assert all(z.audit for z in zrows)
    short = db.query(OcrAttempt).filter_by(provider="ocr_fusion", zone_id=world.zones[1].id).one()
    assert short.text == "24" and short.raw_json["audit"] is True and short.confidence < pipeline.ocr_confidence_threshold(db)
    assert len(pipeline.pending_ocr_responses(db, world.assessment.id)) == 3


def test_full_sample_runs_every_engine_without_changing_the_real_decision(world, mathpix):
    db = world.db
    set_mode(db, "cascade", ocr_full_sample_share=1.0)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    assert all(c.full_sample for c in k.values())
    z1 = k[(1, None, None)]
    assert {j.engine for j in db.query(ocr_queue.OcrJob).filter_by(crop_id=z1.id)} == {"apple_vision", "unimumer", "trocr"}
    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    reads = _cascade_reads(k)
    settle(world, reads)
    for _ in range(6):                      # l'échantillon complet se termine APRÈS la décision réelle
        run_worker(world, reads); db.expire_all(); ocr_cascade.tick(db)
    db.expire_all()
    d = db.query(OcrDecision).filter_by(crop_id=z1.id).one()
    assert d.decision == "local_consensus" and d.reason == "local_consensus"       # la décision réelle : étape 1
    assert {j.engine for j in db.query(ocr_queue.OcrJob).filter_by(crop_id=z1.id, kind="read")} == {
        "apple_vision", "unimumer", "lighton", "trocr"}                          # tous les moteurs ont lu
    assert "action" in d.full_decision and d.full_decision["surface"] == "24"


def test_a_second_pipeline_run_does_not_duplicate_crops_or_skeletons(world, mathpix):
    db = world.db
    set_mode(db, "cascade")
    batch = world.make_batch()
    pipeline.process_batch(db, batch)
    n_crops, n_jobs = db.query(OcrCrop).count(), db.query(ocr_queue.OcrJob).count()
    pipeline.process_batch(db, db.get(ScanBatch, batch.id))
    assert db.query(OcrCrop).count() == n_crops and db.query(ocr_queue.OcrJob).count() == n_jobs
    assert db.query(OcrAttempt).filter_by(provider="ocr_pending").count() == 3   # une par zone


# ============================================================ audit en mode ombre

def test_shadow_audit_sends_mathpix_accepted_zones_to_ocriser_and_truth_is_backfilled(world, mathpix):
    from app.routers import scans as scans_router
    db = world.db
    set_mode(db, "shadow", ocr_shadow_audit_share=1.0)
    batch = world.make_batch()
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(db, batch)
    db.expire_all()
    b = db.query(ScanBatch).one()
    assert b.status == "ocr_review_pending"                           # tout est « Contrôle » : rien n'est douteux pour Mathpix
    assert len(pipeline.pending_ocr_responses(db, world.assessment.id)) == 3
    items = scans_router.list_ocr_items(b.id, db)
    assert [i["ocr_audit"] for i in items] == [True, True, True]      # étiquette dès l'ouverture, avant la lecture locale
    att = db.query(OcrAttempt).filter_by(provider="mathpix", zone_id=world.zones[1].id).one()
    assert att.raw_json["audit"] is True and att.confidence < pipeline.ocr_confidence_threshold(db)
    tbl = db.query(OcrAttempt).filter_by(provider="mathpix", zone_id=world.zones[2].id).one()
    assert tbl.raw_json["audit"] is True and all(c < pipeline.ocr_confidence_threshold(db) for c in tbl.raw_json["cell_confidences"])

    # le professeur confirme AVANT que le Mac ait lu : aucune décision locale n'existe encore
    resp = db.query(StudentResponse).filter_by(copy_item_id=world.items[1].id).one()
    scans_router.confirm_ocr_reading(resp.id, db, None)
    assert db.query(OcrDecision).count() == 0
    # puis le Mac lit : la vérité est rattrapée à la fin de la zone, source « audit »
    k = crops_by_key(db, world)
    reads = {"apple_vision": {k[(1, None, None)].id: "24"}, "unimumer": {k[(1, None, None)].id: "2 4"},
             "lighton": {}, "trocr": {}}
    for _ in range(4):
        run_worker(world, reads); db.expire_all(); ocr_cascade.tick(db)
    db.expire_all()
    z = db.query(OcrDecision).filter_by(zone_id=world.zones[1].id, crop_id=None).one()
    assert (z.audit, z.truth_source, z.truth_surface, z.auto_error) == (True, "audit", "24", False)
    d = db.query(OcrDecision).filter_by(crop_id=k[(1, None, None)].id).one()
    assert (d.decision, d.truth_surface, d.auto_error) == ("local_consensus", "24", False)


def test_shadow_audit_share_zero_changes_nothing(world, mathpix):
    db = world.db
    set_mode(db, "shadow", ocr_shadow_audit_share=0.0)
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(db, world.make_batch())
    assert not any((a.raw_json or {}).get("audit") for a in db.query(OcrAttempt).all())
    assert db.query(ScanBatch).one().status != "ocr_review_pending"


def test_shadow_audit_never_applies_in_mathpix_or_cascade_modes(world, mathpix):
    db = world.db
    set_mode(db, "mathpix", ocr_shadow_audit_share=1.0)
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(db, world.make_batch())
    assert not any((a.raw_json or {}).get("audit") for a in db.query(OcrAttempt).all())


# ============================================================ Mac absent trop longtemps

def test_overdue_batch_can_fall_back_to_mathpix(world, mathpix):
    from datetime import datetime, timedelta, timezone
    from fastapi import BackgroundTasks, HTTPException
    from app.routers import scans as scans_router
    from app.models import OcrJob
    db = world.db
    set_mode(db, "cascade")
    batch = world.make_batch()
    pipeline.process_batch(db, batch)
    db.expire_all()
    b = db.query(ScanBatch).one()
    prog = ocr_cascade.local_progress(db, b)
    assert prog["overdue"] is False and prog["timeout_hours"] == 6 and prog["waiting_hours"] < 1
    # le lot attend depuis 10 h
    p = dict(b.progress_json); p["ocr_local_pending"] = {**p["ocr_local_pending"],
        "since": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()}
    b.progress_json = p; db.commit()
    prog = ocr_cascade.local_progress(db, db.query(ScanBatch).one())
    assert prog["overdue"] is True and prog["waiting_hours"] >= 10

    tasks = BackgroundTasks()
    out = scans_router.fallback_mathpix(b.id, tasks, db)
    assert out["ok"] and out["cancelled_crops"] == 5 and len(tasks.tasks) == 1
    db.expire_all()
    assert {j.status for j in db.query(OcrJob).all()} == {"cancelled"}
    reasons = {d.reason for d in db.query(OcrDecision).filter(OcrDecision.crop_id.isnot(None)).all()}
    assert reasons == {"mac_fallback_mathpix"}
    # la tâche de fond relit avec Mathpix, comme avant la lecture multicouche
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("On calcule\nBC = 10", .95)]
    pipeline.process_batch(db, db.get(ScanBatch, b.id), ocr_mode_override="mathpix")
    db.expire_all()
    assert db.query(StudentResponse).count() == 3
    assert db.query(OcrAttempt).filter_by(provider="mathpix").count() == 3
    assert db.query(ScanBatch).one().status != "ocr_local_pending"
    # un lot qui n'attend plus le Mac refuse le repli
    with pytest.raises(HTTPException) as e:
        scans_router.fallback_mathpix(b.id, BackgroundTasks(), db)
    assert e.value.status_code == 409
    # un résultat tardif du worker pour une tâche annulée est ignoré
    job = db.query(OcrJob).first()
    out = ocr_queue.post_results(db, [{"job_id": job.id, "engine": job.engine, "raw_text": "9"}])
    assert out["ignored"] == 1 and db.query(OcrEngineRead).count() == 0


def test_ocr_report_endpoint_is_admin_only_json(world):
    from app.routers import misc
    rep = misc.ocr_report(None, None, None, world.db, None)
    assert {"auto_errors", "go_no_go", "engines", "pairs", "mathpix", "waiting"} <= set(rep)
    import json; json.dumps(rep, default=str)


def test_finished_full_samples_do_not_crowd_out_unfinished_ones(world, mathpix):
    """Les échantillons complets déjà conclus ne sont plus repris par le tour de fond : seuls
    ceux sans `action` le sont, même s'ils sont plus anciens que 500 échantillons conclus."""
    db = world.db
    set_mode(db, "cascade", ocr_full_sample_share=1.0)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    reads = _cascade_reads(k)
    settle(world, reads)
    calls = []
    real = ocr_cascade.finish_full_sample
    ocr_cascade.finish_full_sample = lambda db_, crop: (calls.append(crop.id), real(db_, crop))[1]
    try:
        for _ in range(6):
            run_worker(world, reads); db.expire_all(); ocr_cascade.tick(db)
        db.expire_all()
        calls.clear()
        ocr_cascade.tick(db)
    finally:
        ocr_cascade.finish_full_sample = real
    done = [c.id for c in k.values()
            if "action" in (db.query(OcrDecision).filter_by(crop_id=c.id).one().full_decision or {})]
    assert done and not set(calls) & set(done)


def test_shadow_zone_pass_skips_zones_already_finished(world, mathpix):
    db = world.db
    set_mode(db, "shadow")
    pipeline.process_batch(db, world.make_batch())
    reads = _cascade_reads(crops_by_key(db, world))
    settle(world, reads)
    for _ in range(4):
        run_worker(world, reads); db.expire_all(); ocr_cascade.tick(db)
    db.expire_all()
    calls = []
    real = ocr_cascade.finish_zone
    ocr_cascade.finish_zone = lambda *a, **kw: (calls.append(1), real(*a, **kw))[1]
    try:
        ocr_cascade.finish_shadow_zones(db)
    finally:
        ocr_cascade.finish_zone = real
    assert db.query(OcrDecision).filter(OcrDecision.crop_id.is_(None)).count() > 0
    assert calls == []

"""Planches Mathpix : assemblage, réattribution par boîte (réponse `line_data` synthétique),
déclenchement, repli unitaire."""
import io
import sys
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import OcrDecision, OcrMontage
from app.services import ocr_cascade, ocr_montage as M, providers
from tests.test_ocr_pipeline_modes import (  # noqa: F401  (fixtures + helpers)
    mathpix, set_mode, settle, crops_by_key, world,
)


def png(w, h, shade=200):
    ok, buf = cv2.imencode(".png", np.full((h, w, 3), shade, np.uint8))
    return buf.tobytes()


def line(y0, y1, text="1,79", conf=0.97, x0=30, x1=200):
    return {"text": text, "latex": text, "confidence": conf,
            "cnt": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


# ------------------------------------------------------------ assemblage

def test_montage_geometry_gap_width_and_boxes_inside():
    m = M.build_montage([png(300, 60), png(2400, 100), png(500, 80)])
    img = cv2.imdecode(np.frombuffer(m.png, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] <= 1600 and img.shape[1] == m.width and img.shape[0] == m.height
    for (x0, y0, x1, y1), nxt in zip(m.boxes, m.boxes[1:] + [None]):
        assert 0 <= x0 < x1 <= m.width and 0 <= y0 < y1 <= m.height
        if nxt:
            assert nxt[1] - y1 >= 80                                   # au moins 80 px d'écart
    assert m.scales[1] < 1.0 and m.scales[0] == 1.0                     # la découpe trop large est réduite
    assert (m.boxes[1][2] - m.boxes[1][0]) <= 1600 - 40
    # fond blanc entre deux découpes, encre des découpes à leur place
    gap_row = img[m.boxes[0][3] + 40]
    assert (gap_row == 255).all()
    x0, y0, x1, y1 = m.boxes[0]
    assert (img[y0 + 5, x0 + 5] == 200).all()


def test_montage_keeps_small_crops_unscaled_and_rejects_garbage():
    m = M.build_montage([png(120, 40)])
    assert m.scales == [1.0] and m.boxes[0][2] - m.boxes[0][0] == 120
    with pytest.raises(ValueError):
        M.build_montage([b"pas une image"])


# ------------------------------------------------------------ réattribution

BOXES = [(20, 20, 220, 80), (20, 160, 220, 220), (20, 300, 220, 360)]


def test_each_line_goes_back_to_its_crop_by_its_box():
    a = M.assign_lines(BOXES, [line(30, 70, "a"), line(170, 210, "b"), line(310, 350, "c")])
    assert [x.status for x in a] == ["ok", "ok", "ok"]
    assert [x.lines[0]["text"] for x in a] == ["a", "b", "c"]


def test_a_line_straddling_two_bands_invalidates_both():
    a = M.assign_lines(BOXES, [line(60, 175, "pont"), line(310, 350, "c")])
    assert [x.status for x in a] == ["straddle", "straddle", "ok"]


def test_a_line_overflowing_its_band_into_the_gap_invalidates_that_crop():
    a = M.assign_lines(BOXES, [line(30, 100, "déborde"), line(170, 210, "b"), line(310, 350, "c")])
    assert [x.status for x in a] == ["straddle", "ok", "ok"]


def test_no_line_and_two_lines_are_not_assigned():
    a = M.assign_lines(BOXES, [line(30, 45, "x"), line(50, 70, "y"), line(310, 350, "c")])
    assert [x.status for x in a] == ["ambiguous", "no_line", "ok"]


def test_a_line_in_the_gap_has_no_owner_and_breaks_nothing():
    a = M.assign_lines(BOXES, [line(30, 70, "a"), line(100, 130, "perdu"), line(170, 210, "b")])
    assert [x.status for x in a] == ["ok", "ok", "no_line"]


def test_a_line_without_usable_contour_invalidates_everything():
    a = M.assign_lines(BOXES, [line(30, 70, "a"), {"text": "?", "cnt": []}])
    assert [x.status for x in a] == ["straddle"] * 3


# ------------------------------------------------------------ déclenchement

@pytest.mark.parametrize("pending,wait,only,sent", [
    (12, timedelta(0), False, True),          # assez de découpes
    (11, timedelta(minutes=3), False, False),
    (3, timedelta(minutes=10), False, True),  # la plus ancienne attend depuis 10 min
    (2, timedelta(minutes=1), True, True),    # un lot n'attend plus que Mathpix
    (0, timedelta(minutes=99), True, False),
])
def test_should_send(pending, wait, only, sent):
    assert M.should_send(pending=pending, oldest_wait=wait, batch_only_mathpix=only,
                         max_crops=12, flush_minutes=10) is sent


# ------------------------------------------------------------ bout en bout

class FakeMontageApi:
    def __init__(self, monkeypatch):
        self.calls = 0
        self.boxes = None
        self.texts: list[str] = []
        self.skip: set[int] = set()
        real = M.build_montage

        def spy(images, **kw):
            m = real(images, **kw)
            self.boxes = m.boxes
            return m
        monkeypatch.setattr(M, "build_montage", spy)
        monkeypatch.setattr(providers, "mathpix_montage_ocr", self)

    def __call__(self, db, png_bytes, correlation_id):
        self.calls += 1
        lines = [line(b[1] + 5, b[3] - 5, t) for i, (b, t) in enumerate(zip(self.boxes, self.texts))
                 if i not in self.skip]
        return {"lines": lines, "raw": {"fake": True}, "cost": 0.004}


def _all_disagree(k, truth):
    """Trois familles, trois surfaces -> aucune majorité -> Mathpix. Le moteur qui lit la vraie
    valeur est un moteur qui VOTE pour la classe (unimumer en nombres, lighton en lignes)."""
    unimumer, lighton = {}, {}
    for key, c in k.items():
        t = truth.get(key, "7")
        if c.zone_class == "multiline_line":
            lighton[c.id], unimumer[c.id] = t, "8"
        else:
            unimumer[c.id], lighton[c.id] = t, "8"
    return {"apple_vision": {c.id: "9" for c in k.values()}, "unimumer": unimumer, "lighton": lighton,
            "trocr": {c.id: "x" for c in k.values()}}


def test_one_montage_call_replaces_several_unit_calls_and_falls_back_for_the_rest(world, mathpix, monkeypatch):
    from app.services import pipeline
    from app.models import OcrCrop
    db = world.db
    set_mode(db, "cascade", ocr_montage_enabled=True)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    truth = {(1, None, None): "24", (2, 0, None): "5", (2, 1, None): "1,79", (3, None, 0): "On calcule", (3, None, 1): "BC = 10"}
    api = FakeMontageApi(monkeypatch)
    api.texts = None
    reads = _all_disagree(k, truth)
    # laisse le local lire, sans toucher à Mathpix : on prépare la planche à la main
    from tests.test_ocr_pipeline_modes import run_worker
    for _ in range(4):
        run_worker(world, reads); db.expire_all()
        for c in db.query(OcrCrop).filter(OcrCrop.status.in_(("pending", "local_done"))).all():
            ocr_cascade.advance_crop(db, c)
        db.commit()
    db.expire_all()
    pending = [c for c, _t in db.query(OcrCrop, OcrDecision.created_at)       # même ordre que la planche
               .join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
               .filter(OcrCrop.status == "mathpix_pending")
               .order_by(OcrDecision.created_at, OcrCrop.id).all()]
    assert len(pending) == 5
    id_to_key = {c.id: key for key, c in k.items()}
    api.texts = [truth[id_to_key[c.id]] for c in pending]
    api.skip = {2}                                                    # la 3e découpe n'a aucune ligne
    unit_text = truth[id_to_key[pending[2].id]]
    mathpix.by_sha = {pending[2].image_sha256: (unit_text, .98)}

    assert ocr_cascade.run_mathpix_step(db) == 5
    db.expire_all()
    assert api.calls == 1 and len(mathpix.calls) == 1                 # 1 planche + 1 relecture seule
    assert mathpix.calls[0]["sha"] == pending[2].image_sha256 and mathpix.calls[0]["hint"] is None
    decs = {c.id: db.query(OcrDecision).filter_by(crop_id=c.id).one() for c in pending}
    for c in pending:
        d = decs[c.id]
        assert d.decision == "mathpix_confirmed", (id_to_key[c.id], d.reason, d.candidates_json)
        assert d.accepted_surface == ocr_cascade.to_surface(
            truth[id_to_key[c.id]], c.zone_class)
    via_montage = [d for c, d in decs.items() if d.montage_id]
    assert len(via_montage) == 4 and all(abs(d.cost_eur - 0.004 / 5) < 1e-9 for d in via_montage)
    assert decs[pending[2].id].montage_id is None and decs[pending[2].id].cost_eur == pytest.approx(0.004)
    m = db.query(OcrMontage).one()
    assert len(m.crops_json) == 5 and m.cost_eur == 0.004 and (world.tmp / m.image_path).exists()


def test_montage_failure_falls_back_to_unit_reads(world, mathpix, monkeypatch):
    from app.services import pipeline
    from app.models import OcrCrop
    from tests.test_ocr_pipeline_modes import run_worker
    db = world.db
    set_mode(db, "cascade", ocr_montage_enabled=True)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    truth = {key: "7" for key in k}
    reads = _all_disagree(k, truth)
    for _ in range(4):
        run_worker(world, reads); db.expire_all()
        for c in db.query(OcrCrop).filter(OcrCrop.status.in_(("pending", "local_done"))).all():
            ocr_cascade.advance_crop(db, c)
        db.commit()

    def boom(*a, **kw):
        raise providers.BudgetExceeded("quota")
    monkeypatch.setattr(providers, "mathpix_montage_ocr", boom)
    mathpix.default = ("7", .99)
    ocr_cascade.run_mathpix_step(db)
    db.expire_all()
    assert len(mathpix.calls) == 5                                    # chaque découpe relue seule
    assert db.query(OcrMontage).count() == 0
    assert db.query(OcrCrop).filter_by(status="mathpix_pending").count() == 0   # aucune découpe n'est restée coincée


def test_montage_disabled_by_default_uses_unit_calls_only(world, mathpix, monkeypatch):
    from app.services import pipeline, runtime_settings as rs
    db = world.db
    set_mode(db, "cascade")
    assert rs.ocr_setting(db, "ocr_montage_enabled") is False
    api = FakeMontageApi(monkeypatch)
    pipeline.process_batch(db, world.make_batch())
    db.expire_all()
    k = crops_by_key(db, world)
    mathpix.by_sha = {c.image_sha256: ("1,79", .97) for c in k.values() if c.cell_index == 1}
    from tests.test_ocr_pipeline_modes import _cascade_reads
    settle(world, _cascade_reads(k))
    assert api.calls == 0 and len(mathpix.calls) == 1


def test_default_gap_is_the_measured_250_px_and_below_80_is_refused():
    """Mesuré sur l'API réelle : à 80 px Mathpix fusionne la colonne de courts nombres en UN tableau."""
    m = M.build_montage([png(200, 60), png(200, 60), png(200, 60)])
    assert all(b[1] - a[3] == M.DEFAULT_GAP_PX == 250 for a, b in zip(m.boxes, m.boxes[1:]))
    with pytest.raises(ValueError):
        M.build_montage([png(200, 60), png(200, 60)], gap=79)
    assert M.build_montage([png(200, 60), png(200, 60)], gap=80).boxes[1][1] - 20 - 60 == 80


def test_a_merged_array_response_falls_back_entirely():
    """Réponse réelle observée à 80 px : un seul bloc `array` couvrant toute la planche -> aucune
    découpe attribuée (repli sur la lecture seule), jamais d'attribution au hasard."""
    boxes = [(20, 20, 328, 103), (20, 183, 328, 265), (20, 345, 328, 428)]
    merged = [{"text": "\\( \\begin{array}{l}2 \\\\ 2 \\\\ 4\\end{array} \\)", "confidence": 0.99,
               "cnt": [[111, 30], [398, 30], [398, 420], [111, 420]]}]
    assert [a.status for a in M.assign_lines(boxes, merged)] == ["straddle"] * 3

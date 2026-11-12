"""Revue manuelle avant la correction : squelette, lecture corrigée, journal des revues."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Copy, OcrAttempt, ReviewEvent, StudentResponse
from app.routers import scans as scans_router
from app.services import pipeline, review_log, skeleton
from tests.test_ocr_pipeline_modes import mathpix, set_mode, world  # noqa: F401  (fixtures)


def _read_batch(world, mathpix):
    db = world.db
    set_mode(db, "mathpix")
    mathpix.by_order = [("24", .97), ("5", .97), ("1,79", .97), ("a\nb", .95)]
    b = world.make_batch()
    pipeline.process_batch(db, b)
    db.expire_all()
    return db, b


def _resp(db, world, seq):
    return db.query(StudentResponse).filter_by(copy_item_id=world.items[seq].id).one()


def test_skeleton_page_carries_what_goes_to_grading(world, mathpix):
    db, b = _read_batch(world, mathpix)
    copy = db.get(Copy, world.items[1].copy_id)
    view = scans_router.review_copy(copy.id, db)
    assert view["pages"], "au moins une page lue"
    page = view["pages"][0]
    sk = page["skeleton"]
    assert sk["paths"] and sk["exact"] is False                 # pas de PDF imprimé : repli sur les zones
    by_seq = {z["sequence"]: z for z in sk["zones"]}
    assert by_seq[1]["text"] == "24"
    assert [c["text"] for c in by_seq[2]["cells"]] == ["5", "1,79"]
    assert all(0 <= z["rect"]["x"] <= 1 for z in page["zones"])


def test_correcting_a_cell_before_grading_writes_a_reading_and_a_review_dossier(world, mathpix):
    db, b = _read_batch(world, mathpix)
    resp = _resp(db, world, 2)
    out = scans_router.review_answer(resp.id, scans_router.RefineAnswerIn(cells=["5", "1,8"]), db, None)
    last = pipeline.effective_reading(db, resp.zone_id)
    assert last.provider == "teacher_ocr" and last.raw_json["cells"] == ["5", "1,8"]
    assert db.query(OcrAttempt).filter_by(zone_id=resp.zone_id, provider="teacher_ocr").count() == 1
    ev = db.query(ReviewEvent).one()
    assert (ev.stage, ev.category, ev.status, ev.sequence) == ("pre_correction", "ocr", "open", 2)
    assert [c["text"] for c in ev.before_json["synthesis"]["cells"]] == ["5", "1,79"]
    assert [c["text"] for c in ev.after_json["synthesis"]["cells"]] == ["5", "1,8"]
    assert ev.context_json["attempts"]
    from app.config import settings
    files = {f.name for f in (settings.data_dir / ev.folder).iterdir()}
    assert {"event.json", "squelette-avant.svg", "squelette-apres.svg"} <= files
    assert out["copy"]["pages"]


def test_unchanged_answer_is_not_logged(world, mathpix):
    db, b = _read_batch(world, mathpix)
    resp = _resp(db, world, 1)
    scans_router.review_answer(resp.id, scans_router.RefineAnswerIn(text="24"), db, None)
    assert db.query(ReviewEvent).count() == 0


def test_points_only_answers_wait_for_the_correction(world, mathpix):
    from fastapi import HTTPException
    db, b = _read_batch(world, mathpix)
    resp = _resp(db, world, 3)                                 # rédaction notée en points
    with pytest.raises(HTTPException) as e:
        scans_router.review_answer(resp.id, scans_router.RefineAnswerIn(text="x"), db, None)
    assert e.value.status_code == 422


def test_review_log_script_lists_exports_and_marks(world, mathpix, capsys):
    import io
    import tarfile
    db, b = _read_batch(world, mathpix)
    resp = _resp(db, world, 2)
    scans_router.review_answer(resp.id, scans_router.RefineAnswerIn(cells=["6", "1,79"]), db, None)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import review_log as script
    script.main(["list"], db=db)
    assert "pre_correction" in capsys.readouterr().out
    buf = io.BytesIO()

    class Out:
        buffer = buf
    real = sys.stdout
    sys.stdout = Out()
    try:
        script.main(["export"], db=db)
    finally:
        sys.stdout = real
    names = tarfile.open(fileobj=io.BytesIO(buf.getvalue())).getnames()
    assert "RESUME.md" in names and "index.json" in names
    assert any(n.endswith("event.json") for n in names)
    ev = db.query(ReviewEvent).one()
    script.main(["mark", ev.id[:8], "--note", "test"], db=db)
    db.expire_all()
    assert db.get(ReviewEvent, ev.id).status == "treated"

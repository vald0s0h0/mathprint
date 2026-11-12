"""Lot 2 : file de tâches OCR et API du worker (jeton, réservation, baux, résultats)."""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base, get_db
from app.models import (
    Assessment, Copy, CopyItem, DocumentPage, OcrCrop, OcrDecision, OcrEngineRead, OcrJob,
    ResponseZone, ScanBatch, SchoolClass, Student, SystemSetting,
)
from app.routers import ocr_worker
from app.services import ocr_queue, runtime_settings as rs

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-OCR-Worker": "mac-test"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_worker_token", TOKEN)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from tests.pgsupport import make_engine
    engine = make_engine()
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(ocr_worker.router)
    app.dependency_overrides[get_db] = override
    return TestClient(app), Session, tmp_path


def _seed(db, tmp_path, n=3, zone_class="num_line", engines=("unimumer", "trocr")):
    cls = SchoolClass(name="4C", grade_level="4e"); db.add(cls); db.flush()
    a = Assessment(class_id=cls.id, title="T", type="control", note_base=20); db.add(a); db.flush()
    stu = Student(class_id=cls.id, name="A B", llm_pseudonym="x"); db.add(stu); db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed"); db.add(copy); db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1); db.add(page); db.flush()
    from tests.pgsupport import ensure_catalog
    ensure_catalog(db)
    item = CopyItem(copy_id=copy.id, catalog_id="c", sequence=1, difficulty=3,
                    response_type="short_text", statement="s", correction="c",
                    expected_json={}, grading_json={})
    db.add(item); db.flush()
    zone = ResponseZone(page_id=page.id, item_id=item.id, type="short_text", x_pt=1, y_pt=1,
                        w_pt=10, h_pt=10, meta_json={})
    db.add(zone); db.flush()
    batch = ScanBatch(assessment_id=a.id); db.add(batch); db.flush()
    crops = []
    for i in range(n):
        (tmp_path / f"c{i}.png").write_bytes(b"\x89PNG fake")
        crop = OcrCrop(batch_id=batch.id, zone_id=zone.id, cell_index=i, image_path=f"c{i}.png",
                       image_sha256=f"sha{i}", zone_class=zone_class, charset="0123456789,")
        db.add(crop); db.flush()
        ocr_queue.enqueue_jobs(db, crop, list(engines))
        crops.append(crop)
    db.commit()
    return crops


def _res(job_id, engine="unimumer", raw="1,79", **extra):
    return {"job_id": job_id, "engine": engine, "raw_text": raw, "logprob_mean": -0.02,
            "family": "qwen35", "backend": "mps", "latency_ms": 900, **extra}


# ----------------------------------------------------------------- jeton

def test_wrong_missing_and_closed_token(env, monkeypatch):
    client, _S, _p = env
    assert client.get("/api/ocr-worker/pending").status_code == 401
    assert client.get("/api/ocr-worker/pending", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/ocr-worker/pending", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    # une longueur différente ne casse pas la comparaison
    assert client.get("/api/ocr-worker/pending", headers={"Authorization": "Bearer " + TOKEN + "x"}).status_code == 401
    assert client.get("/api/ocr-worker/pending", headers=AUTH).status_code == 200
    monkeypatch.setattr(settings, "ocr_worker_token", "")
    assert client.get("/api/ocr-worker/pending", headers=AUTH).status_code == 503   # API fermée


def test_user_login_token_is_not_accepted(env):
    """Le jeton du worker est distinct de l'authentification des utilisateurs."""
    client, _S, _p = env
    assert client.post("/api/ocr-worker/claim", json={"engine": "unimumer"},
                       headers={"Authorization": "Bearer un-jwt-utilisateur"}).status_code == 401


def test_token_never_leaks_in_errors(env):
    client, _S, _p = env
    r = client.get("/api/ocr-worker/pending", headers={"Authorization": "Bearer mauvais"})
    assert TOKEN not in r.text


# ------------------------------------------------------- pending / claim

def test_pending_counts_and_claim_flow(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=3)
    assert client.get("/api/ocr-worker/pending", params={"engine": "unimumer"}, headers=AUTH).json() == {
        "engine": "unimumer", "pending": 3}
    assert client.get("/api/ocr-worker/pending", headers=AUTH).json() == {
        "pending": {"unimumer": 3, "trocr": 3}}
    r = client.post("/api/ocr-worker/claim", json={"engine": "unimumer", "limit": 2}, headers=AUTH)
    jobs = r.json()["jobs"]
    assert len(jobs) == 2 and all(j["engine"] == "unimumer" and j["attempt"] == 1 for j in jobs)
    assert jobs[0]["zone_class"] == "num_line" and jobs[0]["charset"] == "0123456789,"
    assert "expected" not in str(jobs).lower() and "value" not in jobs[0]    # jamais de valeur attendue
    assert client.get("/api/ocr-worker/pending", params={"engine": "unimumer"}, headers=AUTH).json()["pending"] == 1
    # le reliquat part, puis plus rien
    assert len(client.post("/api/ocr-worker/claim", json={"engine": "unimumer", "limit": 32}, headers=AUTH).json()["jobs"]) == 1
    assert client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"] == []


def test_claim_limit_bounds(env):
    client, _S, _p = env
    assert client.post("/api/ocr-worker/claim", json={"engine": "x", "limit": 0}, headers=AUTH).status_code == 422
    assert client.post("/api/ocr-worker/claim", json={"engine": "x", "limit": 65}, headers=AUTH).status_code == 422


def test_no_double_claim_between_concurrent_workers(tmp_path):
    """Vrai fichier SQLite, une connexion par thread (StaticPool partagerait une
    seule connexion entre les threads et ne prouverait rien)."""
    from tests.pgsupport import make_file_engine
    engine = make_file_engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        _seed(db, tmp_path, n=40, engines=("unimumer",))
    taken: list[list[str]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def work(name):
        try:
            with Session() as db:
                got = ocr_queue.claim_jobs(db, engine="unimumer", limit=40, worker=name)
                ids = [j.id for j in got]
            with lock:
                taken.append(ids)
        except Exception as e:                       # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors, errors
    ids = [i for chunk in taken for i in chunk]
    assert len(ids) == len(set(ids)) == 40           # chaque tâche à un seul worker, aucune perdue
    with Session() as db:
        assert db.query(OcrJob).filter_by(status="claimed").count() == 40


def test_claim_conditional_update_refuses_an_already_claimed_job(env):
    """Même si deux sélections voient la même tâche, la mise à jour conditionnelle départage."""
    _client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
        first = ocr_queue.claim_jobs(db, engine="unimumer", limit=5, worker="a")
        second = ocr_queue.claim_jobs(db, engine="unimumer", limit=5, worker="b")
    assert len(first) == 1 and second == []
    with Session() as db:
        assert db.query(OcrJob).one().claimed_by == "a"


def test_lease_uses_the_setting(env):
    _client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
        db.add(SystemSetting(key="ocr_lease_minutes", value_json={"value": 30})); db.commit()
        job = ocr_queue.claim_jobs(db, engine="unimumer", limit=1, worker="a")[0]
        delta = ocr_queue._naive(job.lease_until) - ocr_queue._naive(datetime.now(timezone.utc))
        assert timedelta(minutes=29) < delta <= timedelta(minutes=30)


# ------------------------------------------------------------ baux

def _expire(Session):
    with Session() as db:
        for j in db.query(OcrJob).filter_by(status="claimed"):
            j.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()


def test_expired_lease_requeues_then_errors_and_sends_to_manual(env):
    _client, Session, tmp = env
    with Session() as db:
        crop, = _seed(db, tmp, n=1, engines=("unimumer", "trocr"))
        crop_id = crop.id
    for attempt in (1, 2, 3):
        with Session() as db:
            got = ocr_queue.claim_jobs(db, engine="unimumer", limit=5, worker="mac")
            assert len(got) == 1 and got[0].attempts == attempt
        _expire(Session)
        with Session() as db:
            out = ocr_queue.release_expired(db)
        assert out == ({"requeued": 1, "error": 0} if attempt < 3 else {"requeued": 0, "error": 1})
    with Session() as db:
        job = db.query(OcrJob).filter_by(engine="unimumer").one()
        assert job.status == "error" and job.attempts == 3
        d = db.query(OcrDecision).one()           # unimumer vote en num_line : manuel
        assert (d.decision, d.stage_reached, d.crop_id) == ("manual", "manual", crop_id)
        assert d.reason.startswith("engine_error:unimumer")
        assert db.get(OcrCrop, crop_id).status == "decided"
        assert ocr_queue.claim_jobs(db, engine="unimumer", limit=5, worker="mac") == []


def test_observer_failure_never_forces_manual(env):
    """trocr observe seulement : ses échecs n'empêchent pas la lecture automatique."""
    _client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("trocr",))
    for _ in range(3):
        with Session() as db:
            ocr_queue.claim_jobs(db, engine="trocr", limit=1, worker="mac")
        _expire(Session)
        with Session() as db:
            ocr_queue.release_expired(db)
    with Session() as db:
        assert db.query(OcrJob).one().status == "error"
        assert db.query(OcrDecision).count() == 0


def test_unexpired_lease_is_left_alone(env):
    _client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
        ocr_queue.claim_jobs(db, engine="unimumer", limit=1, worker="mac")
        assert ocr_queue.release_expired(db) == {"requeued": 0, "error": 0}
        assert db.query(OcrJob).one().status == "claimed"


# --------------------------------------------------------- résultats

def test_results_are_stored_and_idempotent(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    body = {"results": [_res(job["job_id"], raw="1,79", alternatives=[{"text": "1,79"}],
                              weights="phxember/Uni-MuMER-Qwen3.5-2B@40a62882")]}
    first = client.post("/api/ocr-worker/results", json=body, headers=AUTH).json()
    again = client.post("/api/ocr-worker/results", json=body, headers=AUTH).json()
    assert first == {"accepted": 1, "duplicates": 0, "ignored": 0, "requeued": 0}
    assert again == {"accepted": 0, "duplicates": 1, "ignored": 0, "requeued": 0}
    with Session() as db:
        r = db.query(OcrEngineRead).one()
        assert (r.raw_text, r.engine, r.family, r.backend) == ("1,79", "unimumer", "qwen35", "mps")
        assert r.weights.endswith("@40a62882") and r.alternatives_json == [{"text": "1,79"}]
        assert r.surface == "1,79"                   # forme canonique calculée à la réception (ocr_surface)
        assert db.query(OcrJob).one().status == "done"
        assert db.query(OcrCrop).one().status == "local_done"


def test_duplicate_inside_one_batch_is_stored_once(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    r = client.post("/api/ocr-worker/results", json={"results": [_res(job["job_id"]), _res(job["job_id"], raw="autre")]},
                    headers=AUTH).json()
    assert r["accepted"] == 1 and r["duplicates"] == 1
    with Session() as db:
        assert db.query(OcrEngineRead).one().raw_text == "1,79"


def test_crop_becomes_local_done_only_when_all_jobs_are_done(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer", "trocr"))
    j1 = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    client.post("/api/ocr-worker/results", json={"results": [_res(j1["job_id"])]}, headers=AUTH)
    with Session() as db:
        assert db.query(OcrCrop).one().status == "pending"
    j2 = client.post("/api/ocr-worker/claim", json={"engine": "trocr"}, headers=AUTH).json()["jobs"][0]
    client.post("/api/ocr-worker/results", json={"results": [_res(j2["job_id"], engine="trocr")]}, headers=AUTH)
    with Session() as db:
        assert db.query(OcrCrop).one().status == "local_done"


def test_unknown_job_and_engine_mismatch_are_ignored(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    r = client.post("/api/ocr-worker/results", json={"results": [
        _res("inconnu"), _res(job["job_id"], engine="trocr")]}, headers=AUTH).json()
    assert r["ignored"] == 2 and r["accepted"] == 0
    with Session() as db:
        assert db.query(OcrEngineRead).count() == 0 and db.query(OcrJob).one().status == "claimed"


def test_late_result_after_lease_expiry_is_still_accepted(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    _expire(Session)
    with Session() as db:
        ocr_queue.release_expired(db)
        assert db.query(OcrJob).one().status == "pending"
    assert client.post("/api/ocr-worker/results", json={"results": [_res(job["job_id"])]},
                       headers=AUTH).json()["accepted"] == 1
    with Session() as db:
        assert db.query(OcrJob).one().status == "done"
        assert ocr_queue.claim_jobs(db, engine="unimumer", limit=5, worker="x") == []


def test_engine_error_result_requeues_then_errors(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    outs = []
    for _ in range(3):
        job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
        outs.append(client.post("/api/ocr-worker/results", json={"results": [
            {"job_id": job["job_id"], "engine": "unimumer", "error": "OOM"}]}, headers=AUTH).json())
    assert [o["requeued"] for o in outs] == [1, 1, 0]
    with Session() as db:
        assert db.query(OcrJob).one().status == "error"
        assert db.query(OcrDecision).one().decision == "manual"
        assert db.query(OcrEngineRead).filter(OcrEngineRead.error != "").count() == 3


def test_score_result_keeps_candidate_scores(env):
    client, Session, tmp = env
    with Session() as db:
        crop, = _seed(db, tmp, n=1, engines=())
        ocr_queue.enqueue_jobs(db, crop, ["unimumer"], kind="score", candidates=["1,79", "1,8"])
        db.commit()
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    assert job["kind"] == "score" and job["candidates"] == ["1,79", "1,8"]
    client.post("/api/ocr-worker/results", json={"results": [
        _res(job["job_id"], scores={"1,79": -0.4, "1,8": -3.1})]}, headers=AUTH)
    with Session() as db:
        r = db.query(OcrEngineRead).one()
        assert r.kind == "score" and r.scores_json == {"1,79": -0.4, "1,8": -3.1}


def test_raw_text_is_stored_verbatim_with_accents_and_capped(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    job = client.post("/api/ocr-worker/claim", json={"engine": "unimumer"}, headers=AUTH).json()["jobs"][0]
    client.post("/api/ocr-worker/results", json={"results": [_res(job["job_id"], raw="œuf  é\n1,79" + "x" * 5000)]},
                headers=AUTH)
    with Session() as db:
        raw = db.query(OcrEngineRead).one().raw_text
        assert raw.startswith("œuf  é\n1,79") and len(raw) == ocr_queue.MAX_RAW_CHARS


# ------------------------------------------------------------- image

def test_crop_image_download_and_path_safety(env):
    client, Session, tmp = env
    with Session() as db:
        crops = _seed(db, tmp, n=2, engines=("unimumer",))
        ids = [c.id for c in crops]
        (tmp.parent / "secret.txt").write_text("secret")
        db.get(OcrCrop, ids[1]).image_path = "../secret.txt"; db.commit()
    ok = client.get(f"/api/ocr-worker/crops/{ids[0]}/image", headers=AUTH)
    assert ok.status_code == 200 and ok.content.startswith(b"\x89PNG") and ok.headers["X-Image-Sha256"] == "sha0"
    assert client.get(f"/api/ocr-worker/crops/{ids[1]}/image", headers=AUTH).status_code == 404
    assert client.get("/api/ocr-worker/crops/nope/image", headers=AUTH).status_code == 404
    assert client.get(f"/api/ocr-worker/crops/{ids[0]}/image").status_code == 401


# --------------------------------------------------------- présence

def test_last_worker_seen_and_online_window(env):
    client, Session, _tmp = env
    with Session() as db:
        assert ocr_queue.last_worker_seen_at(db) is None and not ocr_queue.worker_online(db)
    client.get("/api/ocr-worker/pending", headers=AUTH)
    with Session() as db:
        assert ocr_queue.worker_online(db)
        assert db.get(SystemSetting, "ocr_worker_last_seen").value_json["worker"] == "mac-test"
        row = db.get(SystemSetting, "ocr_worker_last_seen")
        row.value_json = {"at": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(), "worker": "mac-test"}
        db.commit()
        assert not ocr_queue.worker_online(db)
    client.get("/api/ocr-worker/pending", headers=AUTH)       # il revient
    with Session() as db:
        assert ocr_queue.worker_online(db)


def test_a_refused_call_does_not_count_as_a_worker_seen(env):
    client, Session, _tmp = env
    client.get("/api/ocr-worker/pending", headers={"Authorization": "Bearer mauvais"})
    with Session() as db:
        assert ocr_queue.last_worker_seen_at(db) is None


def test_enqueue_does_not_duplicate_read_jobs(env):
    _client, Session, tmp = env
    with Session() as db:
        crop, = _seed(db, tmp, n=1, engines=("unimumer",))
        assert ocr_queue.enqueue_jobs(db, crop, ["unimumer", "lighton"]) != []
        db.commit()
        assert db.query(OcrJob).count() == 2

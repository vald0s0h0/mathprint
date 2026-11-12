"""Le worker (moteurs `fake`) contre le VRAI backend : même chemin que la production,
transport HTTP remplacé par un TestClient."""
import hashlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocr_worker.config import Config, Server
from ocr_worker.engines.fake import FakeEngine
from ocr_worker.waves import Worker

from app.models import OcrCrop, OcrEngineRead, OcrJob
from app.services import ocr_queue
from tests.test_ocr_worker_api import AUTH, TOKEN, env, _seed  # noqa: F401  (fixture + helper)


class ClientTransport:
    def __init__(self, client):
        self.c = client

    def request(self, method, url, *, headers, params=None, json_body=None, timeout=60):
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        r = self.c.request(method, path, headers=headers, params=params, json=json_body)
        return r.status_code, dict(r.headers), r.content


def _png(tmp, name, shade):
    buf = io.BytesIO(); Image.new("L", (80, 24), shade).save(buf, format="PNG")
    (tmp / name).write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


def test_worker_round_trip_against_the_backend(env):
    client, Session, tmp = env
    with Session() as db:
        crops = _seed(db, tmp, n=3, engines=("unimumer", "trocr"))
        for i, c in enumerate(crops):
            db.get(OcrCrop, c.id).image_sha256 = _png(tmp, c.image_path, 200 + i)
        db.commit()
    cfg = Config(servers=[Server("local", "http://testserver", TOKEN)], claim_batch=2)
    engines = {
        "unimumer": FakeEngine("unimumer", "qwen35", {"reads": {"*": {"raw_text": "1,79", "logprob_mean": -0.03}}}),
        "trocr": FakeEngine("trocr", "encdec", {"reads": {"*": {"raw_text": "179"}}}),
    }
    w = Worker(cfg, transport=ClientTransport(client), engines=engines)
    stats = w.run_once()
    assert stats["jobs"] == 6 and stats["errors"] == 0
    with Session() as db:
        assert {j.status for j in db.query(OcrJob)} == {"done"}
        assert {c.status for c in db.query(OcrCrop)} == {"local_done"}
        reads = db.query(OcrEngineRead).all()
        assert len(reads) == 6
        by = {(r.engine): r for r in reads}
        assert by["unimumer"].raw_text == "1,79" and by["unimumer"].family == "qwen35"
        assert by["trocr"].raw_text == "179"             # texte brut intact : la virgule perdue reste visible
        assert ocr_queue.worker_online(db)               # le worker est « vu » par le serveur


def test_worker_with_the_wrong_token_leaves_the_queue_untouched(env):
    client, Session, tmp = env
    with Session() as db:
        _seed(db, tmp, n=1, engines=("unimumer",))
    cfg = Config(servers=[Server("local", "http://testserver", "faux-jeton")])
    w = Worker(cfg, transport=ClientTransport(client),
               engines={"unimumer": FakeEngine("unimumer", "qwen35", {})})
    assert w.run_once()["jobs"] == 0
    with Session() as db:
        assert db.query(OcrJob).one().status == "pending"

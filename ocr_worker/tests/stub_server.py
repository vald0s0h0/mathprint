"""Faux serveur MathPrint en mémoire (mêmes routes que backend/app/routers/ocr_worker.py)."""
import hashlib
import io
import json

from PIL import Image


def png(w=60, h=20, shade=255) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (w, h), shade).save(buf, format="PNG")
    return buf.getvalue()


class StubServer:
    def __init__(self, token="secret", fail_posts=0):
        self.token = token
        self.jobs: list[dict] = []          # {job_id, engine, kind, candidates, crop_id, zone_class, image, status}
        self.results: list[dict] = []
        self.fail_posts = fail_posts
        self.requests: list[tuple[str, str]] = []
        self.down = False

    def add(self, job_id, engine, crop_id, *, kind="read", candidates=(), zone_class="num_line",
            image=None, sha=True):
        image = image or png()
        self.jobs.append(dict(job_id=job_id, engine=engine, kind=kind, candidates=list(candidates),
                              crop_id=crop_id, zone_class=zone_class, image=image, status="pending",
                              sha=hashlib.sha256(image).hexdigest() if sha is True else sha))

    # transport.request(...)
    def request(self, method, url, *, headers, params=None, json_body=None, timeout=60):
        if self.down:
            raise ConnectionError("down")
        path = url.split("//", 1)[1].split("/", 1)[1]
        path = "/" + path
        self.requests.append((method, path))
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, {}, b"{}"
        if path == "/api/ocr-worker/pending":
            counts: dict[str, int] = {}
            for j in self.jobs:
                if j["status"] == "pending":
                    counts[j["engine"]] = counts.get(j["engine"], 0) + 1
            return 200, {}, json.dumps({"pending": counts}).encode()
        if path == "/api/ocr-worker/claim":
            got = [j for j in self.jobs if j["engine"] == json_body["engine"] and j["status"] == "pending"]
            got = got[: json_body["limit"]]
            for j in got:
                j["status"] = "claimed"
            out = [{"job_id": j["job_id"], "crop_id": j["crop_id"], "engine": j["engine"], "kind": j["kind"],
                    "candidates": j["candidates"], "zone_class": j["zone_class"], "charset": "0123456789,",
                    "image_sha256": j["sha"], "image_url": f"/api/ocr-worker/crops/{j['crop_id']}/image"}
                   for j in got]
            return 200, {}, json.dumps({"jobs": out}).encode()
        if path.startswith("/api/ocr-worker/crops/"):
            cid = path.split("/")[4]
            j = next(j for j in self.jobs if j["crop_id"] == cid)
            return 200, {}, j["image"]
        if path == "/api/ocr-worker/results":
            if self.fail_posts > 0:
                self.fail_posts -= 1
                return 500, {}, b"{}"
            for r in json_body["results"]:
                self.results.append(r)
                for j in self.jobs:
                    if j["job_id"] == r["job_id"]:
                        j["status"] = "done"
            return 200, {}, b"{}"
        return 404, {}, b"{}"

"""Client HTTP du worker pour UN serveur MathPrint (stdlib seulement).

Le transport est injectable : les tests branchent un TestClient FastAPI, la
production utilise urllib. Aucun jeton dans les messages d'erreur.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from .config import Server


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict, params: dict | None = None,
                json_body: dict | None = None, timeout: float = 60) -> tuple[int, dict, bytes]: ...


class UrllibTransport:
    def request(self, method, url, *, headers, params=None, json_body=None, timeout=60):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = dict(headers)
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


class ServerError(RuntimeError):
    pass


class ServerClient:
    def __init__(self, server: Server, worker_name: str, transport: Transport | None = None):
        self.server = server
        self.name = server.name
        self._headers = {"Authorization": f"Bearer {server.token}", "X-OCR-Worker": worker_name}
        self._t = transport or UrllibTransport()

    def _call(self, method, path, *, params=None, json_body=None, raw=False):
        try:
            status, headers, body = self._t.request(
                method, self.server.url + path, headers=self._headers, params=params,
                json_body=json_body)
        except Exception as e:                     # réseau, DNS, Tailscale coupé...
            raise ServerError(f"{self.name}: injoignable ({type(e).__name__})") from None
        if status != 200:
            raise ServerError(f"{self.name}: HTTP {status} sur {path}")
        return (body, headers) if raw else json.loads(body)

    def pending(self) -> dict[str, int]:
        return self._call("GET", "/api/ocr-worker/pending")["pending"]

    def claim(self, engine: str, limit: int) -> list[dict]:
        return self._call("POST", "/api/ocr-worker/claim",
                          json_body={"engine": engine, "limit": limit})["jobs"]

    def image(self, job: dict) -> bytes:
        body, _h = self._call("GET", job["image_url"], raw=True)
        return body

    def post_results(self, results: list[dict]) -> dict:
        return self._call("POST", "/api/ocr-worker/results", json_body={"results": results})

"""ETag / Cache-Control de la route figure.png (revalidation 304)."""
from types import SimpleNamespace

from app.routers import content
from app.services import figures

FIG = {"type": "rectangle", "params": {"length": 5, "width": 3}}


def test_etag_stable_et_sensible_aux_params():
    assert figures.figure_etag(FIG) == figures.figure_etag(dict(FIG))
    other = {"type": "rectangle", "params": {"length": 6, "width": 3}}
    assert figures.figure_etag(FIG) != figures.figure_etag(other)
    assert figures.figure_etag({}) is None


class _Db:
    def get(self, model, ident):
        return SimpleNamespace(figure_json=FIG)


def _req(inm=None):
    return SimpleNamespace(headers={"if-none-match": inm} if inm else {})


def test_route_200_puis_304(tmp_path, monkeypatch):
    monkeypatch.setattr(figures.settings, "data_dir", tmp_path)
    r = content.exercise_figure("x", _req(), _Db())
    assert r.status_code == 200 and r.body[:4] == b"\x89PNG"
    assert r.headers["cache-control"] == "private, no-cache"
    etag = r.headers["etag"]
    r2 = content.exercise_figure("x", _req(etag), _Db())
    assert r2.status_code == 304 and not r2.body
    r3 = content.exercise_figure("x", _req('"autre"'), _Db())
    assert r3.status_code == 200

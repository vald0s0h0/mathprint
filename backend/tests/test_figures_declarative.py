"""Figures DÉCLARATIVES (geo, chart) : toute spec valide se rend, toute spec
fautive est refusée avec un message qui nomme la clé — c'est ce message que lit
l'auteur de la spec pour la corriger."""
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import figures


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


VALID = {
    "triangle": {"type": "geo", "params": {
        "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]}, "polygons": [["A", "B", "C"]],
        "angles": [{"at": "A", "from": "B", "to": "C", "right": True},
                   {"at": "B", "from": "C", "to": "A", "label": "$37^\\circ$"}],
        "lengths": [{"seg": ["A", "B"], "label": "4 cm"}], "width_mm": 50}},
    "thales": {"type": "geo", "params": {
        "points": {"A": [0, 0], "B": [6, 0], "C": [2, 4], "M": [1, 2], "N": [4, 2]},
        "segments": [["A", "B"], ["A", "C"], ["C", "B"], ["M", "N"]],
        "parallel_marks": [{"segs": [["M", "N"], ["A", "B"]]}],
        "equal_marks": [{"segs": [["A", "M"], ["M", "C"]], "ticks": 2}],
        "circles": [{"center": "C", "through": "M"}]}},
    "geo_axes": {"type": "geo", "params": {
        "points": {"A": [1, 2], "B": [-2, -1]},
        "axes": {"x": {"min": -4, "max": 4}, "y": {"min": -3, "max": 4}, "origin": "O"},
        "lines": [{"through": ["A", "B"], "label": "$(d)$"}]}},
    "function_reading": {"type": "chart", "params": {
        "axes": {"x": {"min": -1, "max": 6, "step": 1}, "y": {"min": -1, "max": 5, "step": 1}},
        "functions": [{"expr": "0.5x+1", "domain": [-1, 6], "label": "$f$"},
                      {"pieces": [{"expr": "x", "domain": [0, 2]}, {"expr": "2", "domain": [2, 5]}]}],
        "points": [{"xy": [2, 2], "label": "A", "reading": True}]}},
    "curve": {"type": "chart", "params": {
        "axes": {"x": {"min": 0, "max": 24, "step": 4, "minor": 1}, "y": {"min": 0, "max": 30, "step": 5},
                 "grid": "both"},
        "curves": [{"points": [[0, 12], [4, 10], [8, 15], [12, 24], [24, 13]], "markers": True}]}},
    "bars": {"type": "chart", "params": {
        "axes": {"y": {"min": 0, "max": 12, "step": 2}},
        "bars": {"categories": ["Lun", "Mar"], "series": [{"values": [4, 7], "label": "A"},
                                                          {"values": [5, 6], "label": "B"}]},
        "legend": True}},
    "histogram": {"type": "chart", "params": {
        "axes": {"x": {"min": 0, "max": 30, "step": 10}, "y": {"min": 0, "max": 8, "step": 2}},
        "histogram": {"edges": [0, 10, 20, 30], "values": [3, 7, 2]}}},
    "pie": {"type": "chart", "params": {"pie": {"values": [120, 90, 150], "labels": ["Bus", "Vélo", "À pied"],
                                                "unit": "angle", "show": "percent"}}},
    "tree": {"type": "chart", "params": {"tree": {"outcomes": True, "children": [
        {"label": "R", "prob": "$\\frac{2}{5}$", "children": [{"label": "R", "prob": "$\\frac{1}{4}$"}]},
        {"label": "B", "prob": "$\\frac{3}{5}$"}]}}},
}


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_specs_render_at_their_physical_size(name):
    fig = VALID[name]
    assert figures.figure_error(fig) is None
    assert figures.validate_figure(fig) is not None
    png = figures.render_figure(fig)
    with Image.open(io.BytesIO(png)) as im:
        assert round(im.info["dpi"][0]) == 300      # taille imprimée lue par pdfgen
        assert im.width > 100 and im.height > 50


INVALID = [
    ({"type": "geo", "params": {"points": {"A": [0, 0]}, "segments": [["A", "E"]]}}, "point inconnu 'E'"),
    ({"type": "geo", "params": {"points": {"A": [0, 0], "B": [1, 1]}, "colour": 1}}, "clé(s) inconnue(s)"),
    ({"type": "geo", "params": {"points": {"A": [0, 0], "B": [1, 1]},
                                "texts": [{"at": [0, 1], "text": "\\alpha"}]}}, "hors $...$"),
    ({"type": "chart", "params": {"pie": {"values": [100, 100], "labels": ["a", "b"], "unit": "angle"}}},
     "attendu 360°"),
    ({"type": "chart", "params": {"axes": {}, "functions": [{"expr": "__import__('os')"}]}},
     "caractère non autorisé"),
    ({"type": "chart", "params": {"axes": {}, "functions": [{"expr": "y+1"}]}}, "nom inconnu"),
    ({"type": "chart", "params": {"axes": {"y": {"max": 5}},
                                  "bars": {"categories": ["a", "b"], "series": [{"values": [1]}]}}},
     "bars.series[0].values"),
    ({"type": "chart", "params": {"axes": {}, "histogram": {"edges": [0, 20, 10], "values": [1, 2]}}},
     "strictement croissantes"),
    ({"type": "chart", "params": {"axes": {}, "pie": {"values": [1, 2], "labels": ["a", "b"]}}},
     "UN repère"),
    ({"type": "chart", "params": {"curves": [{"points": [[0, 1], [1, 2]]}]}}, "axes"),
]


@pytest.mark.parametrize("fig,needle", INVALID)
def test_invalid_specs_are_refused_with_a_readable_reason(fig, needle):
    err = figures.figure_error(fig)
    assert err and needle in err, err
    assert figures.validate_figure(fig) is None


def test_the_cache_key_follows_the_engine_version(monkeypatch):
    from app.services import figkit
    fig = VALID["triangle"]
    first = figures.render_figure(fig)
    monkeypatch.setattr(figkit, "ENGINE_VERSION", "test-bump")
    cache = Path(settings.data_dir) / "figcache"
    before = set(cache.iterdir())
    figures.render_figure(fig)
    assert len(set(cache.iterdir()) - before) == 1 and first

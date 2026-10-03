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


def test_close_labels_avoid_each_other_and_geometry():
    import matplotlib.pyplot as plt
    from app.services import figkit as fk
    fig = fk.new_figure(85, 55)
    ax = fig.add_axes([0.08, 0.08, 0.84, 0.84])
    ax.set_xlim(-1, 5); ax.set_ylim(-2, 2); ax.axis("off")
    ax.plot([0, 4], [0, 0], color="black")
    ax.plot([2, 2], [-1, 1], color="black")
    layout = fk.LabelLayout(ax)
    for lab, xy in (("A", [2, 0]), ("B", [2.1, 0]), ("12 cm", [2, 0.05])):
        ax.plot(*xy, "o", markersize=2)
        layout.add(lab, xy)
    layout.place()
    renderer = fig.canvas.get_renderer()
    boxes = [a.get_window_extent(renderer).padded(1) for a, *_ in layout.items]
    assert all(not a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i+1:])
    for line in ax.lines[:2]:
        path = line.get_transform().transform_path(line.get_path())
        assert all(not path.intersects_bbox(box, filled=False) for box in boxes)
    assert [a.get_text() for a, *_ in layout.items] == ["A", "B", "12 cm"]
    plt.close(fig)


def test_dense_axes_keep_the_grid_and_space_readable_values():
    import matplotlib.pyplot as plt
    from app.services import figkit as fk
    fig = fk.new_figure(93, 78)
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
    fk.draw_axes(ax, {"x": {"min": 0, "max": 60}, "y": {"min": 0, "max": 60}},
                 "axes", equal=False)
    before = ax.get_xticks().copy()
    fk.space_tick_labels(ax)
    assert list(ax.get_xticks()) == list(before)  # aucun changement de graduation
    renderer = fig.canvas.get_renderer()
    for axis in (ax.xaxis, ax.yaxis):
        boxes = [t.get_window_extent(renderer) for t in axis.get_ticklabels() if t.get_text()]
        assert len(boxes) >= 3
        assert all(not a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i+1:])
        assert all(t.get_fontsize() >= 9 for t in axis.get_ticklabels())
    plt.close(fig)


def test_a_tall_geometry_keeps_its_physical_size_in_print():
    from app.services import pdfgen
    fig = {"type": "geo", "params": {"points": {"A": [0, 0], "B": [3, 5], "C": [6, 0]},
                                     "polygons": [["A", "B", "C"]], "width_mm": 85}}
    png = figures.render_figure(fig)
    with Image.open(io.BytesIO(png)) as im:
        natural_h = im.height * 72 / im.info["dpi"][0]
    layout = pdfgen._statement_layout("Observe.\n{{figure}}", 93 * pdfgen.mm, 9, 9, fig)
    assert natural_h > 63 * pdfgen.mm
    assert layout["figure"][2] == pytest.approx(natural_h, abs=0.1)


def test_legacy_coordinate_plane_also_uses_readable_physical_rendering():
    png = figures.render_figure({"type": "coordinate_plane", "params": {
        "points": [{"x": 1, "y": 1, "label": "A"}, {"x": 1.1, "y": 1, "label": "B"}]}})
    with Image.open(io.BytesIO(png)) as im:
        assert round(im.info["dpi"][0]) == 300
        assert im.width / im.info["dpi"][0] * 25.4 <= 95


@pytest.mark.parametrize("kind,params", [
    ("rectangle", {"length": 5, "width": 3, "show_diagonal": True}),
    ("triangle", {"base": 4, "height": 3}),
    ("circle", {"radius": 2, "show_diameter": True}),
    ("angle", {"degrees": 45}),
    ("number_line", {"min": 0, "max": 10, "points": [{"value": 2, "label": "A"},
                                                        {"value": 2.1, "label": "B"}]}),
])
def test_legacy_shapes_keep_readable_letters_at_print_size(kind, params):
    from app.services import pdfgen
    figure = {"type": kind, "params": params}
    png = figures.render_figure(figure)
    with Image.open(io.BytesIO(png)) as im:
        assert round(im.info["dpi"][0]) == 300
        natural_w = im.width * 72 / im.info["dpi"][0]
        natural_h = im.height * 72 / im.info["dpi"][0]
    _, width, height = pdfgen._figure_image(figure, 88 * pdfgen.mm, 90 * pdfgen.mm)
    assert width / natural_w >= 0.85
    assert height / natural_h >= 0.85

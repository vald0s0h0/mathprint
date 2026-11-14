"""Figures TikZ : la charte ne se contourne pas, le NAS ne compile jamais, et toute
figure livrée a son image dans la réserve pré-rendue."""
import io
import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import figures, tikzfig
from app.services.figkit import FigureSpecError

ROOT = Path(__file__).resolve().parents[2]
HAS_TEX = tikzfig.tectonic_binary() is not None
needs_tex = pytest.mark.skipif(not HAS_TEX, reason="Tectonic absent (seul le Mac compile)")

SIMPLE = r"""\begin{tikzpicture}
\coordinate (A) at (0,0); \coordinate (B) at (3,0); \coordinate (C) at (0,2);
\filldraw[trait,aplat] (A)--(B)--(C)--cycle;
\AngDroit{B}{A}{C}
\Nom{(A)}{A}{below left} \Cote{(A)}{(B)}{3 cm}{-4mm}
\end{tikzpicture}"""


def _pic(inner: str) -> dict:
    return {"tikz": "\\begin{tikzpicture}\n" + inner + "\n\\end{tikzpicture}"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(tikzfig, "STORE_DIR", tmp_path / "tikz")
    return tmp_path / "tikz"


def test_schema_examples_pass_the_lint():
    md = (ROOT / "agents" / "forge" / "schema.md").read_text(encoding="utf-8")
    bodies = re.findall(r"```latex\n(.*?)\n```", md, re.S)
    assert len(bodies) >= 4
    for body in bodies:
        assert tikzfig.lint({"tikz": body}) == body.strip()


@pytest.mark.parametrize("inner, needle", [
    (r"\draw[line width=2pt] (0,0)--(1,0);", "line width"),
    (r"\draw[very thick] (0,0)--(1,0);", "thick"),
    (r"\draw[thin] (0,0)--(1,0);", "thin"),
    (r"\begin{scope}[scale=2]\draw (0,0)--(1,0);\end{scope}", "scale"),
    (r"\node[font=\bfseries] at (0,0) {A};", "font"),
    (r"\node at (0,0) {\fontsize{20}{20}A};", "fontsize"),
    (r"\input{/etc/passwd}", "input"),
    (r"\def\x{1}", "def"),
    (r"\tikzset{trait/.style={red}}", "tikzset"),
    (r"\draw[red] (0,0)--(1,0);", "red"),
    (r"\draw[draw=blue] (0,0)--(1,0);", "blue"),
    (r"\fill[fill=repA!30!green] (0,0) circle (1);", "green"),
    (r"\definecolor{x}{HTML}{FF0000}", "definecolor"),
    (r"\node at (0,0) {\textcolor{red}{A}};", "red"),
])
def test_lint_refuses_what_bypasses_the_charte(inner, needle):
    with pytest.raises(FigureSpecError) as err:
        tikzfig.lint(_pic(inner))
    assert needle in str(err.value)


def test_lint_accepts_palette_and_mixes():
    tikzfig.lint(_pic(r"\filldraw[figA] (0,0) rectangle (1,1); \draw[draw=repB!60!ink] (0,0)--(1,1);"
                      r"\node[etiq,text=repC] at (0,0) {\textcolor{cotec}{3}}; \fill[fill=white] (0,0) circle (1mm);"))


@pytest.mark.parametrize("params, needle", [
    ({"tikz": SIMPLE, "width_mm": 50}, "width_mm"),
    ({"tikz": r"\draw (0,0)--(1,0);"}, "tikzpicture"),
    ({"tikz": SIMPLE + "\n" + SIMPLE}, "tikzpicture"),
    ({"tikz": ""}, "texte"),
    ("pas un objet", "objet"),
])
def test_lint_refuses_malformed_specs(params, needle):
    with pytest.raises(FigureSpecError) as err:
        tikzfig.lint(params)
    assert needle in str(err.value)


def test_nas_reads_the_store_and_never_compiles(store, monkeypatch):
    monkeypatch.setattr(tikzfig, "tectonic_binary", lambda: None)
    with pytest.raises(FigureSpecError, match="Mac"):
        tikzfig.render({"tikz": SIMPLE})
    store.mkdir(parents=True)
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG", dpi=(300, 300))
    tikzfig.store_path(SIMPLE).write_bytes(buf.getvalue())
    assert tikzfig.render({"tikz": SIMPLE}) == buf.getvalue()
    assert figures.figure_error({"type": "tikz", "params": {"tikz": SIMPLE}}) is None


def test_charte_is_part_of_the_cache_key(monkeypatch):
    fig = {"type": "tikz", "params": {"tikz": SIMPLE}}
    before, store_before = figures.figure_cache_key(fig), tikzfig.store_path(SIMPLE)
    monkeypatch.setattr(tikzfig, "CHARTE_VERSION", "autre-charte")
    assert figures.figure_cache_key(fig) != before
    assert tikzfig.store_path(SIMPLE) != store_before


def test_iter_bodies_finds_part_figures():
    data = {"exercises": [{"figure_json": {"type": "tikz", "params": {"tikz": " A "}}},
                          {"expected": {"parts": [{"figure": {"type": "tikz", "params": {"tikz": "B"}}}]}},
                          {"figure": {"kind": "tikz", "spec": {"tikz": "C"}}},
                          {"figure": {"kind": "geo", "spec": {}}}]}
    assert list(tikzfig.iter_bodies(data)) == ["A", "B", "C"]


def test_every_shipped_tikz_figure_is_prerendered():
    """Le NAS ne compile pas : une figure TikZ publiée ou d'étalon sans image dans
    app/data/tikz casserait l'impression. Lancer backend/scripts/tikz_prerender.py."""
    sources = [tikzfig._APP_DIR / "data" / "indigo" / "exercises.json",
               *sorted((ROOT / "agents" / "forge" / "matieres").glob("*/etalons.json"))]
    missing = []
    for path in sources:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            missing += [f"{path.name} : {b[:60]!r}" for b in tikzfig.iter_bodies(data)
                        if not tikzfig.store_path(b).exists()]
    assert not missing, "figures TikZ non pré-rendues :\n" + "\n".join(missing)


@needs_tex
def test_compiles_at_physical_size_and_fills_the_store(store):
    png = tikzfig.render({"tikz": SIMPLE})
    im = Image.open(io.BytesIO(png))
    assert round(im.info["dpi"][0]) == 300
    width_mm = im.width / 300 * 25.4
    assert 30 < width_mm < 45        # 3 cm de dessin, plus noms et bordure
    assert tikzfig.store_path(SIMPLE).read_bytes() == png


@needs_tex
def test_compile_error_names_the_line(store):
    with pytest.raises(FigureSpecError, match=r"ligne 2 : .*Undefined control sequence"):
        tikzfig.render(_pic(r"\Ptt{(0,0)}{A}{above}"))


@needs_tex
def test_oversized_figure_is_refused(store):
    with pytest.raises(FigureSpecError, match="maximum 93"):
        tikzfig.render(_pic(r"\draw[trait] (0,0)--(12,0);"))


@needs_tex
def test_balance_macro_compiles(store):
    png = tikzfig.render(_pic(r"\Balance[gauche]{$3(2x+1)$}{$2x+10$}"))
    assert Image.open(io.BytesIO(png)).width / 300 * 25.4 < 80

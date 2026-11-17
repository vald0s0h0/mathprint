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
    md = "\n".join(p.read_text(encoding="utf-8")
                   for p in sorted((ROOT / "agents" / "forge" / "contexte").rglob("*.md")))
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
               *sorted((ROOT / "agents" / "forge" / "contexte" / "exemples").glob("*.json"))]
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


# ------------------------------------------------------- contrôle géométrique
@needs_tex
def test_a_name_on_a_line_and_overlapping_names_are_reported(store):
    problems = tikzfig.inspect(_pic(
        r"\coordinate (A) at (2.7,4.2); \coordinate (B) at (0,0); \coordinate (C) at (7,0);"
        r"\filldraw[trait,aplat] (A)--(B)--(C)--cycle;"
        r"\Nom{(A)}{A}{above} \Nom{(3.5,0)}{M}{} \Nom{(6,3)}{K}{} \Nom{(6.08,3)}{L}{}"))
    assert any("« M » est posé sur un trait" in p for p in problems)
    assert any("se recouvrent" in p for p in problems)
    assert not any("« A »" in p for p in problems)


@needs_tex
def test_a_clean_figure_and_its_dimension_line_raise_nothing(store):
    # La cote est posée sur sa propre ligne de cote, très claire : ce n'est pas un défaut.
    assert tikzfig.inspect({"tikz": SIMPLE}) == []


@needs_tex
def test_schema_examples_have_no_geometric_defect(store):
    md = "\n".join(p.read_text(encoding="utf-8")
                   for p in sorted((ROOT / "agents" / "forge" / "contexte").rglob("*.md")))
    for body in re.findall(r"```latex\n(.*?)\n```", md, re.S):
        assert tikzfig.inspect({"tikz": body}) == [], body[:60]


@needs_tex
def test_the_inspection_is_kept_per_figure(store, monkeypatch):
    first = tikzfig.inspect({"tikz": SIMPLE})
    monkeypatch.setattr(tikzfig, "tectonic_binary", lambda: None)
    assert tikzfig.inspect({"tikz": SIMPLE}) == first


def _card_pdf(draw) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 200))
    c.setFont("Helvetica", 10)
    draw(c)
    c.save()
    return buf.getvalue()


def test_card_inspection_reports_text_on_a_rule_overlap_and_overflow():
    from app.services import figlint

    def clean(c):
        c.rect(20, 100, 120, 30)
        c.drawString(30, 111, "Dans sa case")
        c.drawString(30, 60, "Une ligne")
        c.drawString(30, 48, "puis la suivante, serrée")

    def broken(c):
        c.rect(20, 100, 60, 30)
        c.drawString(30, 111, "Texte trop long pour la case")
        c.drawString(30, 60, "Premier")
        c.drawString(32, 59, "Second")
        c.drawString(270, 20, "Dehors")

    assert figlint.inspect_card(_card_pdf(clean), 200, (2, 298)) == []
    problems = figlint.inspect_card(_card_pdf(broken), 200, (2, 298))
    assert any("touche un trait" in p for p in problems)
    assert any("se recouvrent" in p for p in problems)
    assert any("sort du cadre" in p for p in problems)


# ------------------------------------------------------- kits par matière
KITS = {
    "kn-supports.tex": [
        r"\Calculatrice{2,9999999}{1.5E-4}",
        r"\Ticket{Marché}{Riz 5 kg/3 500,Huile 1 L/1 200}{4 700 F}",
        r"\Copie[Awa]{3x + 2 = 11\\3x = 9}",
        r"\Copie[Mariam]{NP² = 13² − 5² = 144\\$\dfrac{3}{4}$ à $45^\circ$}",
        r"\Bulle{Je pense que $x=4$.}",
        r"\BulleAuDessus{Ce résultat est exact.}{\draw[trait] (0,0)--(4,0)--(0,2)--cycle;}",
        r"\Fiche{FICHE}{\FicheLigne{Hauteur}{1,4~m}\FicheLigne{Angle}{$30^\circ$}}",
    ],
    "kn-pc.tex": [
        r"\Becher{(0,0)}{0.6}{$100$ mL} \Erlenmeyer{(2.6,0)}{0.5}{} \Eprouvette{(4.5,0)}{0.7}{}",
        r"\TubeEssai{(0,0)}{0.5}{} \Ballon{(2,0)}{0.4}{} \Burette{(4,0)}{0.8}{} \Support{(5.5,0)}{4}",
        r"\PlanIncline{30}{4} \Solide{(3,0.3)}{$m$} \Force{(1,1)}{60}{1.2}{$\vec{F}$}",
        r"\draw (0,0) to[R,l=$R$] (2,0) to[lamp] (2,-2) -- (0,-2) to[battery1] (0,0);",
        r"\node {\ce{H2O} \chemfig{H-O-H}};",
    ],
    "kn-svt.tex": [
        r"\BrinADN{ATGCCGTA}",
        r"\BrinADN[simple]{ATGC}",
        r"\Echiquier{AB,ab}{Ab,aB}",
        r"\draw[trait] (0,0) circle (0.8); \Legende{(0.3,0.2)}{(1.6,1)}{1}",
    ],
    "kn-hg.tex": [
        "\\Frise{1900}{2000}{25}\n\\Evenement{1914}{Guerre}\n\\Periode{1939}{1945}{Guerre}",
    ],
}


def _tikz(inner: str) -> dict:
    return _pic(inner)


@pytest.mark.parametrize("kit, inner", [(k, i) for k, items in KITS.items() for i in items])
def test_each_kit_macro_loads_its_kit(kit, inner):
    assert tikzfig.extensions_for(_tikz(inner)["tikz"]) == [kit]
    tikzfig.lint(_tikz(inner))


@needs_tex
@pytest.mark.parametrize("kit, inner", [(k, i) for k, items in KITS.items() for i in items])
def test_each_kit_macro_compiles_without_defect(store, kit, inner):
    params = _tikz(inner)
    png = tikzfig.render(params)
    assert Image.open(io.BytesIO(png)).width / 300 * 25.4 < 95
    assert tikzfig.inspect(params) == []


def test_a_figure_without_kit_macro_keeps_its_key():
    import hashlib
    assert tikzfig.extensions_for(SIMPLE) == []
    assert tikzfig.figure_key(SIMPLE) == hashlib.sha256(
        f"{tikzfig.CHARTE_VERSION}\n{SIMPLE}".encode()).hexdigest()
    assert tikzfig.figure_key(_tikz(KITS["kn-hg.tex"][0])["tikz"]) != hashlib.sha256(
        f"{tikzfig.CHARTE_VERSION}\n{_tikz(KITS['kn-hg.tex'][0])['tikz']}".encode()).hexdigest()


def test_kit_patterns_do_not_catch_neighbouring_names():
    assert tikzfig.extensions_for(r"\Nom{(0,0)}{A}{} \Cote{(A)}{(B)}{3}{4mm} \cell \centering") == []
    assert tikzfig.extensions_for(r"\draw (A) to[bend left] (B);") == ["kn-pc.tex"]


def test_mascotte_is_refused_without_a_sheet_and_accepted_with_one(tmp_path, monkeypatch):
    params = _tikz(r"\Mascotte{kiro}{doute}")
    monkeypatch.setattr(tikzfig, "MASCOTTE_DIR", tmp_path)
    with pytest.raises(FigureSpecError, match="planche"):
        tikzfig.lint(params)
    (tmp_path / "planche.json").write_text(json.dumps({"personnages": {"kiro": ["idee"]}}), encoding="utf-8")
    with pytest.raises(FigureSpecError, match="kiro.*doute|doute"):
        tikzfig.lint(params)
    with pytest.raises(FigureSpecError, match="nina"):
        tikzfig.lint(_tikz(r"\Mascotte[1,0]{nina}{idee}"))
    (tmp_path / "planche.json").write_text(json.dumps({"personnages": {"kiro": ["doute", "idee"]}}),
                                           encoding="utf-8")
    assert tikzfig.lint(params) == params["tikz"].strip()


@needs_tex
def test_mascotte_with_a_dummy_sheet_compiles(tmp_path, monkeypatch, store):
    from reportlab.pdfgen import canvas
    monkeypatch.setattr(tikzfig, "MASCOTTE_DIR", tmp_path)
    c = canvas.Canvas(str(tmp_path / "kiro-doute.pdf"), pagesize=(40, 56))
    c.circle(20, 28, 18, fill=1)
    c.save()
    (tmp_path / "planche.json").write_text(json.dumps({"personnages": {"kiro": ["doute"]}}), encoding="utf-8")
    png = tikzfig.render(_tikz(r"\Mascotte{kiro}{doute}"))
    im = Image.open(io.BytesIO(png))
    assert abs(im.height / 300 * 25.4 - 16) < 1.0     # 14 mm de haut + bordure


@needs_tex
def test_complementary_strand_is_right_and_simple_leaves_empty_boxes():
    import fitz
    pdf = tikzfig.compile_pdf(_tikz(r"\BrinADN{ATGCCGTA}")["tikz"], tikzfig.tectonic_binary())
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        words = [(w[1], w[4]) for w in doc[0].get_text("words")]
    rows = {}
    for y, ch in words:
        rows.setdefault(round(y), []).append(ch)
    top, bottom = sorted(rows)
    assert "".join(rows[top]) == "ATGCCGTA" and "".join(rows[bottom]) == "TACGGCAT"
    pdf = tikzfig.compile_pdf(_tikz(r"\BrinADN[simple]{ATGC}")["tikz"], tikzfig.tectonic_binary())
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert doc[0].get_text().split() == list("ATGC")


@needs_tex
def test_punnett_square_fills_its_cells():
    import fitz
    pdf = tikzfig.compile_pdf(_tikz(r"\Echiquier{A,a}{A,a}")["tikz"], tikzfig.tectonic_binary())
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert sorted(doc[0].get_text().split()) == sorted(["A", "a", "A", "a", "AA", "Aa", "Aa", "aa"])


@needs_tex
def test_error_line_stays_the_authors_with_kits():
    body = _tikz("\\Calculatrice{1}{2}\n\\draw (0,0) -- (1,0);\n\\Ptt{(0,0)}{A}{above}")["tikz"]
    assert tikzfig.extensions_for(body) == ["kn-supports.tex"]
    with pytest.raises(FigureSpecError, match=r"ligne 4 : .*Undefined control sequence"):
        tikzfig.compile_pdf(body, tikzfig.tectonic_binary())
    body = _tikz("\\Becher{(0,0)}{0.5}{}\n\\Pt{(0,0)}{A}{above}\n\\Ptt{(0,0)}{A}{above}")["tikz"]
    assert len(tikzfig.extensions_for(body)) == 1
    with pytest.raises(FigureSpecError, match=r"ligne 4 : .*Undefined control sequence"):
        tikzfig.compile_pdf(body, tikzfig.tectonic_binary())


@needs_tex
def test_copie_switches_whole_text_to_classic_when_it_contains_latex():
    import fitz

    def text_font(content):
        body = r"\begin{tikzpicture}\Copie[Awa]{" + content + r"}\end{tikzpicture}"
        data = tikzfig.compile_pdf(body, tikzfig.tectonic_binary())
        with fitz.open(stream=data, filetype="pdf") as doc:
            spans = [span for block in doc[0].get_text("dict")["blocks"]
                     for line in block.get("lines", []) for span in line["spans"]]
        return next(span["font"] for span in spans if "Bonjour" in span["text"])

    assert "PatrickHand" in text_font("Bonjour élève")
    assert "FiraSans" in text_font(r"Bonjour élève\\$\dfrac{3}{4}$")

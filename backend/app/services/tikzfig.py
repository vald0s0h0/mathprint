"""Figures TikZ : `{"type": "tikz", "params": {"tikz": "\\begin{tikzpicture}...\\end{tikzpicture}"}}`.

Le modèle n'écrit que le dessin. Tout ce qui rend les figures homogènes d'un
exercice à l'autre (épaisseurs, couleurs, police, codages, cotes, balance) vient
de la charte `assets/tikz/kn-figures.tex` (style « D5 »). Le contrôle ci-dessous
refuse donc ce qui la contournerait : épaisseur, police ou échelle imposées,
couleur hors palette, et toute commande qui lit, écrit ou redéfinit.

Où l'on compile. Les exercices ne se génèrent que sur le Mac, qui a Tectonic.
Chaque figure compilée y est rangée dans `app/data/tikz/<clé>.png`, dossier
livré avec le code : le NAS, sans TeX, lit cette réserve et ne compile jamais.
La clé dépend du dessin ET de la charte (`CHARTE_VERSION`) : changer la charte
oblige à recompiler sur le Mac (`scripts/tikz_prerender.py`), et un test refuse
de livrer un exercice dont la figure manque dans la réserve.

Toute erreur lève figkit.FigureSpecError avec un message lisible par le modèle
(« tikz : ligne 4 : Undefined control sequence \\Ptt »).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .figkit import FigureSpecError, fail

_APP_DIR = Path(__file__).resolve().parents[1]
CHARTE_PATH = _APP_DIR / "assets" / "tikz" / "kn-figures.tex"
FONTS_DIR = _APP_DIR / "assets" / "fonts"
STORE_DIR = _APP_DIR / "data" / "tikz"
# version du MOTEUR (gabarit du document, résolution) ; la charte a sa propre empreinte
ENGINE = "tikz-1"
RENDER_DPI = 300
MAX_LEN = 12_000
MAX_WIDTH_MM, MAX_HEIGHT_MM = 93.0, 90.0
COMPILE_TIMEOUT_S = 180       # le premier usage de Tectonic télécharge ses paquets


def _charte_version() -> str:
    data = CHARTE_PATH.read_bytes() if CHARTE_PATH.exists() else b""
    return hashlib.sha256(ENGINE.encode() + data).hexdigest()[:16]


CHARTE_VERSION = _charte_version()

# Palette de la charte : seules couleurs nommables dans une figure.
PALETTE = {"ink", "nomc", "cotec", "accent", "fillA", "fillB", "angfill", "gridc",
           "repA", "repB", "repC", "repD", "repE", "white", "none"}

# Commandes refusées : lecture/écriture de fichiers, redéfinitions, réglages que
# seule la charte fixe (police, taille, couleurs, styles globaux).
_FORBIDDEN_CS = re.compile(
    r"\\(input|include|InputIfFileExists|openin|openout|read|write|immediate|newwrite|"
    r"newread|catcode|csname|endcsname|usepackage|RequirePackage|documentclass|directlua|"
    r"special|jobname|def|edef|gdef|xdef|let|futurelet|expandafter|newcommand|renewcommand|"
    r"providecommand|DeclareRobustCommand|setmainfont|setsansfont|setmathfont|fontspec|"
    r"fontsize|selectfont|tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|"
    r"huge|Huge|color|definecolor|colorlet|tikzset|pgfset|pgfkeys|pgfplotsset|"
    r"usetikzlibrary|usepgfplotslibrary|pgfsetlinewidth|makeatletter|makeatother|endinput|"
    r"includegraphics|pgfimage|tdplotsetdisplay|loop|repeat|shipout|output|everypar|"
    r"AtBeginDocument|AtEndDocument|tkzSetUpLine|tkzSetUpPoint|tkzSetUpColors|tkzSetUpStyle)"
    r"(?![A-Za-z])")
# Options refusées : épaisseur, échelle et police viennent de la charte.
_FORBIDDEN_OPT = re.compile(
    r"line\s+width|\bsemithick\b|\b(?:ultra\s+|very\s+)?(?:thin|thick)\b|\bscale\s*=|"
    r"transform\s+shape|\bfont\s*=")
_COLOR_KEYS = re.compile(
    r"\b(draw|fill|color|text|pattern color|left color|right color|middle color|top color|"
    r"bottom color|inner color|outer color|ball color|mark options|shading)\s*=\s*"
    r"([A-Za-z][A-Za-z0-9!.]*)")
_BARE_COLORS = re.compile(
    r"[\[,]\s*(red|green|blue|cyan|magenta|yellow|black|gray|grey|darkgray|lightgray|brown|"
    r"lime|olive|orange|pink|purple|teal|violet)\s*(?=[,\]!])")
_TEXTCOLOR = re.compile(r"\\textcolor\s*\{([^}]*)\}")
_COLOR_MODEL = re.compile(r"\{\s*(HTML|RGB|rgb|cmyk|gray|wave)\s*\}")


def _check_color(expr: str, where: str) -> None:
    for token in expr.split("!"):
        token = token.strip()
        if not token or re.fullmatch(r"[0-9.]+", token):
            continue
        if token not in PALETTE:
            fail(where, f"couleur {token!r} hors charte ; utilise {', '.join(sorted(PALETTE - {'none'}))}")


def lint(params) -> str:
    """Contrôle d'une figure TikZ AVANT toute compilation. Retourne le dessin."""
    if not isinstance(params, dict):
        fail("tikz", "objet {\"tikz\": \"...\"} attendu")
    unknown = set(params) - {"tikz"}
    if unknown:
        fail("tikz", f"clé(s) inconnue(s) {sorted(unknown)} : la taille vient du dessin "
                     "(unités en cm réels), jamais d'un width_mm")
    body = params.get("tikz")
    if not isinstance(body, str) or not body.strip():
        fail("tikz", "texte TikZ attendu")
    if len(body) > MAX_LEN:
        fail("tikz", f"{len(body)} caractères, maximum {MAX_LEN}")
    text = body.strip()
    if not text.startswith(r"\begin{tikzpicture}") or not text.endswith(r"\end{tikzpicture}"):
        fail("tikz", "une seule figure, de \\begin{tikzpicture} à \\end{tikzpicture}")
    if text.count(r"\begin{tikzpicture}") != 1 or text.count(r"\end{tikzpicture}") != 1:
        fail("tikz", "une seule tikzpicture par figure")
    if "^^" in text or "\\begin{document}" in text or "\\end{document}" in text:
        fail("tikz", "le document est fourni par la charte : seulement la tikzpicture")
    # commentaires retirés avant les contrôles de fond (un % peut masquer une ligne)
    code = re.sub(r"(?<!\\)%.*", "", text)
    m = _FORBIDDEN_CS.search(code)
    if m:
        fail("tikz", f"\\{m.group(1)} interdit : la charte fixe police, couleurs et styles ; "
                     "pour une valeur calculée, \\pgfmathsetmacro")
    m = _FORBIDDEN_OPT.search(code)
    if m:
        fail("tikz", f"« {m.group(0).strip()} » interdit : épaisseurs, échelle et police "
                     "viennent des styles de la charte (trait, fort, cache, constr, cote…)")
    if _COLOR_MODEL.search(code):
        fail("tikz", "couleur en code (HTML, RGB…) interdite : utilise la palette de la charte")
    for m in _COLOR_KEYS.finditer(code):
        _check_color(m.group(2), f"tikz : {m.group(1)}=")
    m = _BARE_COLORS.search(code)
    if m:
        fail("tikz", f"couleur {m.group(1)!r} hors charte ; utilise la palette (repA…repE, accent…)")
    for m in _TEXTCOLOR.finditer(code):
        _check_color(m.group(1), "tikz : \\textcolor")
    return text


def figure_key(body: str) -> str:
    return hashlib.sha256(f"{CHARTE_VERSION}\n{body}".encode()).hexdigest()


def store_path(body: str) -> Path:
    return STORE_DIR / f"{figure_key(body)}.png"


def tectonic_binary() -> str | None:
    """Tectonic du Mac ; None sur le NAS (qui lit alors la réserve)."""
    for cand in (os.environ.get("MATHPRINT_TECTONIC"), shutil.which("tectonic"),
                 "/opt/homebrew/bin/tectonic", "/usr/local/bin/tectonic"):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    return None


_HEADER = "\\documentclass[border=1mm]{standalone}\n\\input{kn-figures.tex}\n\\begin{document}\n"


def _compile_error(log: str) -> str:
    offset = _HEADER.count("\n")
    for line in log.splitlines():
        m = re.match(r"error: (?:.*/)?([\w.-]+\.tex):(\d+): (.+)", line)
        if m:
            name, num, msg = m.group(1), int(m.group(2)), m.group(3).strip()
            msg = msg.lstrip("! ").strip()
            if name == "figure.tex":
                return f"ligne {max(1, num - offset)} : {msg}"
            return msg
    for line in log.splitlines():
        if line.startswith("! "):
            return line[2:].strip()
    tail = [l for l in log.splitlines() if l.strip()][-3:]
    return " / ".join(tail)[:300] or "compilation impossible"


def compile_png(body: str, binary: str) -> bytes:
    """PDF puis PNG à 300 dpi (taille physique portée par les métadonnées)."""
    import fitz
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="kn-tikz-") as tmp:
        work = Path(tmp)
        charte = CHARTE_PATH.read_text(encoding="utf-8").replace("@FONTS@", str(FONTS_DIR))
        (work / "kn-figures.tex").write_text(charte, encoding="utf-8")
        (work / "figure.tex").write_text(_HEADER + body + "\n\\end{document}\n", encoding="utf-8")
        try:
            proc = subprocess.run(
                [binary, "-X", "compile", "--untrusted", "figure.tex"], cwd=work,
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            fail("tikz", f"compilation de plus de {COMPILE_TIMEOUT_S} s : figure trop lourde")
        pdf = work / "figure.pdf"
        if proc.returncode != 0 or not pdf.exists():
            fail("tikz", _compile_error(proc.stderr + "\n" + proc.stdout))
        doc = fitz.open(pdf)
        page = doc[0]
        w_mm, h_mm = page.rect.width / 72 * 25.4, page.rect.height / 72 * 25.4
        if w_mm > MAX_WIDTH_MM + 2 or h_mm > MAX_HEIGHT_MM + 2:
            fail("tikz", f"figure de {w_mm - 2:.0f} × {h_mm - 2:.0f} mm, maximum "
                         f"{MAX_WIDTH_MM:.0f} × {MAX_HEIGHT_MM:.0f} mm : réduis l'unité (x=, y=)")
        pix = page.get_pixmap(dpi=RENDER_DPI)
        im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        out = io.BytesIO()
        im.save(out, format="PNG", dpi=(RENDER_DPI, RENDER_DPI), optimize=True)
        return out.getvalue()


def render(params: dict, *, dry: bool = False) -> bytes:
    body = lint(params)
    cached = store_path(body)
    if cached.exists():
        return cached.read_bytes()
    binary = tectonic_binary()
    if binary is None:
        fail("tikz", "figure absente de la réserve pré-rendue et TeX indisponible ici : "
                     "les figures TikZ se compilent sur le Mac (scripts/tikz_prerender.py)")
    png = compile_png(body, binary)
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(cached)
    except OSError:
        pass  # dossier en lecture seule : la figure reste servie depuis le cache disque
    return png


def validate(params: dict) -> dict:
    render(params)
    return params


def iter_bodies(obj):
    """Toutes les figures TikZ d'un JSON quelconque (exercice, publication, slot) :
    forme banque {"type": "tikz", "params": …} ou forme forge {"kind": "tikz", "spec": …},
    y compris dans les parties d'un problème."""
    if isinstance(obj, dict):
        for kind_key, spec_key in (("type", "params"), ("kind", "spec")):
            if obj.get(kind_key) == "tikz" and isinstance(obj.get(spec_key), dict):
                body = obj[spec_key].get("tikz")
                if isinstance(body, str):
                    yield body.strip()
        for value in obj.values():
            yield from iter_bodies(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_bodies(value)


__all__ = ["render", "validate", "lint", "store_path", "figure_key", "tectonic_binary",
           "iter_bodies", "CHARTE_VERSION", "FigureSpecError", "PALETTE"]

"""Rendu fiable des formules mathématiques en LaTeX (pipeline exgen-3).

Le contrat de balisage est le même partout (génération LLM, MathALÉA, web,
PDF) : le texte contient des spans `$...$` dont le contenu est un sous-ensemble
de LaTeX accepté À LA FOIS par KaTeX (web) et matplotlib mathtext (PDF).

Fournit :
- sanitize_latex(s)   : valide un span (liste blanche + rendu d'essai). None si refusé.
- to_mathtext(s)      : normalise un span validé vers la syntaxe mathtext.
- split_math_spans(s) : découpe un texte sur les délimiteurs $...$.
- render_math_png(s)  : rasterise en PNG serré + dimensions réelles en points,
                        avec cache disque (PNG + sidecar JSON de dimensions).
- strip_math(s)       : version texte brut (fallback, recherche, dédoublonnage).
"""

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # avant tout import pyplot, backend non interactif

from matplotlib import mathtext  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402

from ..config import settings  # noqa: E402


# ------------------------------------------------------------------ liste blanche
# Commandes acceptées (intersection KaTeX / mathtext, après normalisation).
ALLOWED_COMMANDS = {
    # structure
    "frac", "dfrac", "tfrac", "sqrt", "left", "right", "overline", "underline",
    "hat", "widehat", "bar", "vec", "text", "mathrm", "mathbf", "textbf", "mathit",
    # opérateurs et relations
    "times", "div", "cdot", "pm", "mp", "leq", "geq", "neq", "approx", "equiv",
    "le", "ge", "ne", "lt", "gt", "sim", "propto", "in", "notin", "subset",
    "cup", "cap", "parallel", "perp",
    # symboles
    "pi", "infty", "circ", "degree", "euro", "%", "ldots", "cdots", "dots",
    "angle", "triangle", "Rightarrow", "rightarrow",
    # espacement (normalisé/retiré pour mathtext)
    ",", ";", "!", ":", "quad", "qquad",
    # accolades littérales
    "{", "}",
}

GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "rho",
    "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon",
    "Phi", "Psi", "Omega",
}

_COMMAND_RE = re.compile(r"\\([a-zA-Z]+|[,;!:%{}])")

# Caractères Unicode qu'on remplace par leur équivalent LaTeX sûr
_UNICODE_MAP = {
    "×": r"\times ", "÷": r"\div ", "·": r"\cdot ", "−": "-", "–": "-",
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ", "≈": r"\approx ",
    "π": r"\pi ", "²": "^2", "³": "^3", "°": r"^\circ ", "∞": r"\infty ",
    "→": r"\rightarrow ", "⇒": r"\Rightarrow ",
    "œ": "oe", " ": " ", " ": " ",
}


def _pre_normalize(s: str) -> str:
    """Remplacements Unicode -> LaTeX et nettoyages sans risque."""
    for k, v in _UNICODE_MAP.items():
        s = s.replace(k, v)
    # décimale française : 3,5 -> 3{,}5 (espacement correct dans KaTeX ET mathtext)
    s = re.sub(r"(?<=\d),(?=\d)", "{,}", s)
    return s.strip()


def sanitize_latex(s: str) -> str | None:
    """Valide un span LaTeX (sans les $). Retourne le span normalisé, ou None.

    Contrôles : commandes sur liste blanche uniquement (pas d'injection),
    accolades équilibrées, et rendu d'essai mathtext réussi.
    """
    if not s or not isinstance(s, str):
        return None
    s = _pre_normalize(s)
    if not s or len(s) > 240 or "$" in s:
        return None
    # accolades équilibrées (hors \{ \})
    depth = 0
    prev = ""
    for ch in s:
        if ch == "{" and prev != "\\":
            depth += 1
        elif ch == "}" and prev != "\\":
            depth -= 1
            if depth < 0:
                return None
        prev = ch
    if depth != 0:
        return None

    for cmd in _COMMAND_RE.findall(s):
        if cmd not in ALLOWED_COMMANDS and cmd not in GREEK:
            return None

    try:
        mathtext.MathTextParser("path").parse(f"${to_mathtext(s)}$", dpi=72)
    except Exception:
        return None
    return s


def to_mathtext(s: str) -> str:
    """Adapte un span validé à matplotlib mathtext (rendu PDF).

    mathtext ne connaît pas \\text, \\degree, \\euro ni les commandes
    d'espacement fin — on les traduit vers des équivalents rendus proprement.
    """
    s = _pre_normalize(s)
    # \text{...} -> \mathrm{...} avec espaces préservés via ~ (mathtext ok)
    def _text_repl(m: re.Match) -> str:
        inner = m.group(1).replace(" ", r"\ ")
        return r"\mathrm{%s}" % inner
    s = re.sub(r"\\text(?:bf)?\{([^{}]*)\}", _text_repl, s)
    s = s.replace(r"\degree", r"^\circ").replace(r"\euro", r"\mathrm{€}")
    s = re.sub(r"\\(?:,|;|!|:)", r"\\/", s)
    s = s.replace(r"\qquad", r"\ \ ").replace(r"\quad", r"\ ")
    s = s.replace(r"\dots", r"\ldots")
    return s


def strip_math(text: str) -> str:
    """Texte brut : retire les $ et aplatit le LaTeX (fallback / dédoublonnage)."""
    out = []
    for content, is_math in split_math_spans(text):
        if not is_math:
            out.append(content)
            continue
        s = _pre_normalize(content)
        s = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
        s = re.sub(r"\\text(?:bf)?\{([^{}]*)\}", r"\1", s)
        s = s.replace(r"\times", "×").replace(r"\div", "÷").replace(r"\cdot", "·")
        s = s.replace(r"\pi", "π").replace(r"\%", "%").replace("^\\circ", "°")
        s = re.sub(r"\\[a-zA-Z]+", " ", s)
        s = s.replace("{,}", ",").replace("{", "").replace("}", "")
        out.append(s)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def split_math_spans(text: str) -> list[tuple[str, bool]]:
    """Découpe un texte en spans (contenu, is_math) sur les délimiteurs $...$."""
    if not text:
        return []
    spans: list[tuple[str, bool]] = []
    pos = 0
    while True:
        start = text.find("$", pos)
        if start == -1:
            if pos < len(text):
                spans.append((text[pos:], False))
            break
        if start > pos:
            spans.append((text[pos:start], False))
        end = text.find("$", start + 1)
        if end == -1:
            spans.append((text[start:], False))
            break
        content = text[start + 1:end]
        if content:
            spans.append((content, True))
        pos = end + 1
    return spans


def has_valid_math(text: str) -> bool:
    """Tous les spans $...$ du texte sont-ils du LaTeX accepté ?"""
    return all(not is_math or sanitize_latex(content) is not None
               for content, is_math in split_math_spans(text))


# ------------------------------------------------------------------ rasterisation

_PNG_DPI = 600


def render_math_png(latex: str, font_size_pt: float = 10.0) -> tuple[bytes, float, float, float]:
    """Rasterise un span LaTeX en PNG serré, fond transparent.

    Retourne (png_bytes, largeur_pt, hauteur_pt, profondeur_pt) où la
    profondeur est la descente sous la ligne de base (pour aligner le PNG sur
    la ligne de texte reportlab). Cache disque PNG + sidecar JSON.
    """
    latex = (latex or "").strip()
    if not latex:
        raise ValueError("LaTeX vide")

    key = hashlib.sha256(f"v3:{latex}:{font_size_pt}".encode()).hexdigest()
    cache_dir = Path(settings.data_dir) / "mathcache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    png_file = cache_dir / f"{key}.png"
    meta_file = cache_dir / f"{key}.json"

    if png_file.exists() and meta_file.exists():
        meta = json.loads(meta_file.read_text())
        return png_file.read_bytes(), meta["w_pt"], meta["h_pt"], meta["d_pt"]

    prop = FontProperties(size=font_size_pt)
    buf = BytesIO()
    # math_to_image rend serré et retourne la profondeur (descente) en points
    depth_pt = mathtext.math_to_image(
        f"${to_mathtext(latex)}$", buf, prop=prop, dpi=_PNG_DPI, format="png")
    png = buf.getvalue()

    from PIL import Image
    with Image.open(BytesIO(png)) as im:
        w_px, h_px = im.size
    w_pt = w_px * 72.0 / _PNG_DPI
    h_pt = h_px * 72.0 / _PNG_DPI

    png_file.write_bytes(png)
    meta_file.write_text(json.dumps(
        {"w_pt": w_pt, "h_pt": h_pt, "d_pt": float(depth_pt or 0.0)}))
    return png, w_pt, h_pt, float(depth_pt or 0.0)


__all__ = ["sanitize_latex", "to_mathtext", "split_math_spans", "strip_math",
           "has_valid_math", "render_math_png"]

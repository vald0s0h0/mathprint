"""Place qu'occupe l'écriture d'un élève : la mesure qui décide si une case suffit.

Un élève n'écrit pas au corps imprimé. Une case dimensionnée d'après le texte de la
carte paraît correcte à l'écran et se révèle trop étroite sur la copie. Les valeurs
ci-dessous décrivent une écriture soignée de collégien, volontairement pas minuscule.
Elles servent à la largeur des colonnes (pdfgen) et au contrôle de la forge : une carte
dont la réponse attendue ne tient pas est refusée. À recaler sur de vraies copies.
"""
from __future__ import annotations

import math
import re

MM = 72.0 / 25.4

DIGIT_W = 3.0 * MM          # chiffre manuscrit, avec son blanc
DIGIT_H = 4.2 * MM          # hauteur d'un chiffre
LINE_H = 5.0 * MM           # une ligne d'écriture dans une case
FRACTION_BAR = 1.2 * MM     # trait de fraction et son air
THOUSANDS_GAP = 1.0 * MM    # espace laissé entre les tranches d'un grand nombre
MARGIN_X = 1.0 * MM         # on n'écrit pas contre le trait de la case
MARGIN_Y = 0.6 * MM

_NARROW = {",": 1.3, ".": 1.3, ";": 1.3, ":": 1.3, "'": 1.0, "’": 1.0, "(": 1.6, ")": 1.6,
           "/": 2.0, " ": 1.8, "-": 2.2, "i": 1.5, "l": 1.5, "t": 2.0, "f": 2.0, "j": 1.6}
_WIDE = {"+": 3.0, "×": 3.0, "=": 3.0, "m": 3.6, "w": 3.6, "÷": 3.0, "²": 1.8, "³": 1.8}
_WIDE.update({c: 1.8 for c in "⁰¹⁴⁵⁶⁷⁸⁹⁻⁺ⁿ"})
_WIDE.update({"√": 3.0, "≤": 3.0, "≥": 3.0, "≠": 3.0, "≈": 3.0, "±": 3.0, "<": 3.0, ">": 3.0})
_FRACTION_RE = re.compile(r"^\s*(-?\d[\d\s]*)\s*/\s*(\d[\d\s]*)\s*$")
_DFRAC_RE = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_WRAPPER_RE = re.compile(r"\\(?:text|textbf|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")
_POWER_RE = re.compile(r"\^\s*(?:\{([^{}]*)\}|(-?\w))")
_SUPERSCRIPT = str.maketrans("0123456789-+n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿ")
# Commandes → ce que l'élève trace. Les plus longues d'abord (\leq avant \le).
_LATEX = (("\\times", "×"), ("\\div", "÷"), ("\\cdot", "×"), ("\\leqslant", "≤"), ("\\geqslant", "≥"),
          ("\\leq", "≤"), ("\\geq", "≥"), ("\\neq", "≠"), ("\\le", "≤"), ("\\ge", "≥"), ("\\ne", "≠"),
          ("\\approx", "≈"), ("\\pm", "±"), ("\\pi", "π"), ("\\infty", "∞"), ("\\degree", "°"),
          ("^\\circ", "°"), ("^{\\circ}", "°"), ("\\circ", "°"), ("\\euro", "€"), ("\\%", "%"),
          ("\\Rightarrow", "donc"), ("\\Leftrightarrow", "équivaut à"),
          ("\\rightarrow", "->"), ("\\to", "->"), ("\\mapsto", "->"), ("\\ldots", "..."),
          ("\\dots", "..."), ("\\cdots", "..."), ("\\left", ""), ("\\right", ""),
          ("\\qquad", " "), ("\\quad", " "), ("\\,", ""), ("\\;", " "), ("\\:", " "), ("\\!", ""),
          ("\\ ", " "), ("{,}", ","), ("\\{", "{"), ("\\}", "}"))


def _power(match: re.Match) -> str:
    """x^2 → x² : l'élève écrit l'exposant en petit, pas un accent circonflexe."""
    exp = (match.group(1) if match.group(1) is not None else match.group(2)).strip()
    if re.fullmatch(r"[-+]?(?:\d+|n)", exp):
        return exp.translate(_SUPERSCRIPT)
    return f"^({exp})" if len(exp) > 1 else f"^{exp}"


def char_w(char: str) -> float:
    """Largeur d'un caractère manuscrit, en points."""
    if char in _NARROW:
        return _NARROW[char] * MM
    if char in _WIDE:
        return _WIDE[char] * MM
    if char.isupper():
        return 3.2 * MM
    if char.isdigit():
        return DIGIT_W
    return 2.6 * MM


def text_w(text: str) -> float:
    return sum(char_w(ch) for ch in text)


def plain(value) -> str:
    """Ce que l'élève écrit, en clair : pas de LaTeX, virgule décimale française."""
    text = str(value if value is not None else "").replace("$", "").replace("{,}", ",")
    for _ in range(4):  # imbrications : \sqrt{\dfrac{1}{4}}, \text{\,cm}…
        before = text
        text = _WRAPPER_RE.sub(r"\1", text)
        text = _DFRAC_RE.sub(lambda m: f"{_group(m.group(1))}/{_group(m.group(2))}", text)
        text = _SQRT_RE.sub(lambda m: "√" + _group(m.group(1)), text)
        if text == before:
            break
    for latex, written in _LATEX:
        text = re.sub(re.escape(latex) + (r"(?![a-zA-Z])" if latex[-1].isalpha() else ""),
                      lambda _m, w=written: w, text)
    text = _POWER_RE.sub(_power, text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)  # commande inconnue : rien à écrire
    text = re.sub(r"\s+", " ", re.sub(r"[{}\\]", "", text))
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text)).strip()


def _group(text: str) -> str:
    """Un numérateur ou un radicande composé garde ses parenthèses une fois à plat."""
    text = text.strip()
    return f"({text})" if re.search(r"[+\-×÷/ ]", text.lstrip("-")) else text


def _number(text: str) -> str:
    """Un grand nombre s'écrit par tranches de trois chiffres."""
    digits = re.sub(r"\s", "", text)
    if re.fullmatch(r"-?\d{4,}", digits):
        sign, body = ("-", digits[1:]) if digits.startswith("-") else ("", digits)
        groups = []
        while body:
            groups.insert(0, body[-3:])
            body = body[:-3]
        return sign + "\u202f".join(groups)
    return text.strip()


def forms(value) -> tuple[str, ...]:
    """La réponse telle qu'écrite : (texte,) ou (numérateur, dénominateur)."""
    if isinstance(value, dict):
        kind = value.get("type")
        inner = value.get("value")
        if kind in ("rational", "fraction") and isinstance(inner, (list, tuple)) and len(inner) == 2:
            return _number(str(inner[0])), _number(str(inner[1]))
        if kind == "decimal":
            return (f"{inner:g}".replace(".", ",") if isinstance(inner, (int, float))
                    else plain(inner).replace(".", ","),)
        if kind == "expression":
            # Le stockage reste en syntaxe algébrique canonique ; la copie et
            # son corrigé utilisent la notation de l'élève, jamais ** ou *.
            inner = re.sub(r"\*\*\s*(?:\(([^()]*)\)|([-+]?\d+|[a-zA-Z]))",
                           lambda m: "^{" + (m.group(1) or m.group(2)) + "}", str(inner))
            inner = re.sub(r"(?<!\*)\*(?!\*)", "×", inner)
            inner = re.sub(r"(?<=\d)×(?=[a-zA-Z(])", "", inner)
        value = inner
    text = plain(value)
    match = _FRACTION_RE.match(text)
    if match:
        return _number(match.group(1)), _number(match.group(2))
    return (_number(text.replace(".", ",") if re.fullmatch(r"-?\d+\.\d+", text) else text),)


def _written_w(text: str) -> float:
    return sum(THOUSANDS_GAP if ch == "\u202f" else char_w(ch) for ch in text)


def wrap(text: str, width: float) -> list[str]:
    """Coupe aux espaces ; un mot trop long reste entier et ressortira trop large."""
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and _written_w(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + ([current] if current else [])


def need(value, width: float | None = None) -> tuple[float, float]:
    """(largeur, hauteur) en points, marges comprises. Avec `width`, un texte peut se
    replier sur plusieurs lignes ; une fraction reste empilée, comme à la main."""
    written = forms(value)
    if len(written) == 2:
        return (max(_written_w(written[0]), _written_w(written[1])) + 2 * MARGIN_X,
                2 * DIGIT_H + FRACTION_BAR + 2 * MARGIN_Y)
    text = written[0]
    lines = wrap(text, width - 2 * MARGIN_X) if width else [text]
    return (max((_written_w(ln) for ln in lines), default=0.0) + 2 * MARGIN_X,
            DIGIT_H + (len(lines) - 1) * LINE_H + 2 * MARGIN_Y)


def need_w(value) -> float:
    """Largeur sur une seule ligne : celle qu'une colonne devrait offrir à sa réponse."""
    return need(value)[0]


def fits(value, width: float, height: float) -> bool:
    w, h = need(value, width)
    return w <= width + 0.01 and h <= height + 0.01


def rows_needed(text: str, width: float) -> int:
    """Lignes de rédaction qu'occupe un texte, sur une largeur donnée."""
    return max(1, len(wrap(plain(text), width)))


def row_count(steps: list[str], width: float) -> int:
    """Une étape commence sa ligne, comme sur une copie."""
    return sum(rows_needed(step, width) for step in steps if str(step or "").strip())


def label(value) -> str:
    written = forms(value)
    return "/".join(written) if len(written) == 2 else written[0]


def mm(points: float) -> float:
    return math.floor(points / MM * 10 + 0.5) / 10


def field_size(values) -> tuple[float, float]:
    """Champ confortable, en points : même règle pour ligne, tableau et partie.

    Écriture la plus encombrante + marge, arrondie au-dessus à 2 mm.
    Plancher 14 × 8 mm ; les fractions gagnent naturellement en hauteur.
    """
    sizes = [need(value) for value in values if value is not None]
    width = max((w for w, _ in sizes), default=0) + 4 * MM
    height = max((h for _, h in sizes), default=0) + 2 * MM
    step = 2 * MM
    return max(14 * MM, math.ceil(width / step) * step), max(8 * MM, math.ceil(height / step) * step)

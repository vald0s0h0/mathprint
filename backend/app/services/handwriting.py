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
_FRACTION_RE = re.compile(r"^\s*(-?\d[\d\s]*)\s*/\s*(\d[\d\s]*)\s*$")
_DFRAC_RE = re.compile(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_LATEX = (("\\times", "×"), ("\\div", "÷"), ("\\,", ""), ("\\;", " "), ("\\ ", " "), ("{,}", ","),
          ("\\%", "%"), ("\\pi", "π"), ("\\left", ""), ("\\right", ""), ("\\text", ""))


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
    text = str(value if value is not None else "").replace("$", "")
    text = _DFRAC_RE.sub(r"\1/\2", text)
    for latex, written in _LATEX:
        text = text.replace(latex, written)
    return re.sub(r"\s+", " ", re.sub(r"[{}\\]", "", text)).strip()


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

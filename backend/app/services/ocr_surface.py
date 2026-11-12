"""Forme canonique d'une lecture OCR : `to_surface(raw, zone_class)`.

Ne change JAMAIS la valeur : ni arrondi, ni fraction simplifiée, ni zéro retiré
(`06`, `6,0`, `0,50` restent tels quels), ni réordonnancement, ni évaluation.
On n'uniformise que la FORME écrite : Unicode (NFC), espaces, virgule ou point
décimal (une seule forme : la virgule), `×`, moins, fractions, exposants,
habillage LaTeX. La casse, les accents, les cédilles et « œ » sont conservés.

Les symboles de remplissage (`□`, `\\square`, `\\boxed`) ne sont jamais traduits :
ils ne deviennent pas un `×`. L'équivalence mathématique (`1/2` = `3/6`, `1,8` ≈
`1,79`) se juge uniquement dans `grading.py`.
"""
import re
import unicodedata

_MATH = ("num_line", "math_line")

_SPACES = re.compile(r"[ \t\r\n\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
        "⁸": "8", "⁹": "9"}
_SUP_RUN = re.compile("[" + "".join(_SUP) + "]+")
_ATOM = re.compile(r"^-?[0-9A-Za-zÀ-ÿ,²³^_.]+$")
_SIMPLE_EXP = re.compile(r"^-?[0-9A-Za-z]+$")

# commandes LaTeX d'habillage ou d'espacement, retirées sans toucher au contenu
_WRAP_CMDS = (r"\left", r"\right", r"\displaystyle", r"\bigl", r"\bigr", r"\Bigl", r"\Bigr")
_TEXT_CMD = re.compile(r"\\(?:text|textrm|textit|textbf|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")

_SYMBOLS = (  # (commande, remplacement) : seulement des équivalents d'écriture
    (r"\times", "×"), (r"\cdot", "×"), (r"\ast", "×"), (r"\div", "÷"), (r"\pi", "π"),
    (r"\approx", "≈"), (r"\neq", "≠"), (r"\leq", "≤"), (r"\le", "≤"), (r"\geq", "≥"),
    (r"\ge", "≥"), (r"\circ", "°"), (r"\degree", "°"), (r"\%", "%"), (r"\euro", "€"),
    (r"\pm", "±"),
)


def _group(s: str, i: int) -> tuple[str, int] | None:
    """Contenu du groupe `{...}` qui commence à s[i] et position après `}`."""
    if i >= len(s) or s[i] != "{":
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return None


def _wrap(x: str) -> str:
    x = x.strip()
    return x if _ATOM.match(x) else f"({x})"


def _frac_and_sqrt(s: str) -> str:
    """\\frac{a}{b} -> a/b, \\sqrt{a} -> √a (parenthèses si l'argument est composé)."""
    pat = re.compile(r"\\(?:d|t)?frac|\\sqrt")
    for _ in range(50):                                   # imbrications : jusqu'à stabilité
        out, pos, changed = [], 0, False
        for m in pat.finditer(s):
            if m.start() < pos:
                continue
            i = m.end()
            while i < len(s) and s[i] == " ":
                i += 1
            if m.group().endswith("sqrt"):
                g = _group(s, i)
                if g is None:
                    continue
                out.append(s[pos:m.start()] + "√" + _wrap(g[0]))
                pos, changed = g[1], True
            else:
                g1 = _group(s, i)
                if g1 is None:
                    continue
                j = g1[1]
                while j < len(s) and s[j] == " ":
                    j += 1
                g2 = _group(s, j)
                if g2 is None:
                    continue
                out.append(s[pos:m.start()] + _wrap(g1[0]) + "/" + _wrap(g2[0]))
                pos, changed = g2[1], True
        if not changed:
            break
        s = "".join(out) + s[pos:]
    return s


def _exponents(s: str) -> str:
    """²/³/^{2}/^2 -> ^2 ; ^{10} -> ^10 ; ^{n+1} garde ses accolades."""
    s = _SUP_RUN.sub(lambda m: "^" + "".join(_SUP[c] for c in m.group()), s)
    for _ in range(20):
        new = re.sub(r"([\^_])\s*\{([^{}]*)\}",
                     lambda m: m.group(1) + (m.group(2).strip() if _SIMPLE_EXP.match(m.group(2).strip())
                                             else "{" + m.group(2).strip() + "}"), s)
        if new == s:
            break
        s = new
    return s


def to_surface(raw: str | None, zone_class: str = "text_line") -> str:
    s = unicodedata.normalize("NFC", raw or "")
    if not s.strip():
        return ""
    math = zone_class in _MATH or zone_class == "math_line"
    # habillage
    s = s.replace("$$", " ").replace("$", " ")
    for a, b in (("\\(", " "), ("\\)", " "), ("\\[", " "), ("\\]", " ")):
        s = s.replace(a, b)
    for cmd in _WRAP_CMDS:
        s = s.replace(cmd, "")
    s = s.replace("{,}", ",")
    s = re.sub(r"\\[,;:! ]", " ", s)
    for _ in range(5):
        s2 = _TEXT_CMD.sub(lambda m: m.group(1), s)
        if s2 == s:
            break
        s = s2
    # symboles : AVANT de retirer les espaces, pour que `\times x` ne devienne pas `\timesx`
    for cmd, rep in _SYMBOLS:
        s = re.sub(re.escape(cmd) + r"(?![A-Za-z])", rep, s)
    s = s.replace("*", "×").replace("·", "×").replace("⋅", "×").replace("∙", "×")
    s = re.sub("[\u2212\u2010\u2011\u2012\u2013]", "-", s)      # − ‐ ‑ ‒ – -> -
    s = re.sub("[\u2019\u2018\u02bc]", "'", s)
    # espaces
    if zone_class in _MATH:
        s = _SPACES.sub("", s)
    else:
        s = _SPACES.sub(" ", s).strip()
    # structure
    s = _frac_and_sqrt(s)
    s = _exponents(s)
    # degré : `62^{\circ}`, `62^°`, `62^{°}` et `62º` (ordinal pris pour un degré) -> `62°`.
    # Même écriture, pas un exposant : sans cela trois moteurs d'accord semblaient diverger.
    s = re.sub(r"\^\s*(?:\{\s*°\s*\}|°)", "°", s.replace("º", "°"))
    if zone_class in _MATH:
        s = _SPACES.sub("", s)
    else:
        s = _SPACES.sub(" ", s).strip()
    # virgule décimale unique : 1.79 -> 1,79 (jamais d'arrondi ni de zéro retiré)
    s = re.sub(r"(?<=\d)\.(?=\d)", ",", s)
    return s

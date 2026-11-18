"""Contexte d'une découpe pour le passage « avec contexte » des moteurs OCR.

Les élèves écrivent parfois si mal que l'image seule ne suffit pas : un second
passage de chaque moteur qui le permet reçoit l'énoncé et la réponse attendue.
Ce contexte biaise la lecture vers la réponse attendue ; `ocr_fusion` ne laisse
donc jamais une surface soufflée (`echo_of`) conclure sans une lecture libre
(sans contexte) qui la confirme.

Le contexte est figé à l'inscription de la découpe (`OcrCrop.context_json`) :
une lecture se rejoue avec exactement ce que le moteur a reçu.
"""
import re

from .ocr_surface import to_surface

MAX_STATEMENT_CHARS = 600
MAX_EXPECTED_CHARS = 400
_NUMBER = re.compile(r"[-−]?\d+(?:[,.]\d+)?(?:\s*/\s*\d+)?")
_LATEX_CMD = re.compile(r"\\(?:text|mathrm|textbf|emph)\{([^}]*)\}")


def _plain(text: str) -> str:
    """Énoncé lisible par un moteur : sans habillage LaTeX lourd, espaces réduits."""
    t = _LATEX_CMD.sub(r"\1", str(text or ""))
    t = t.replace("$", "").replace("\\(", "").replace("\\)", "")
    return re.sub(r"\s+", " ", t).strip()


def expected_text(expected: dict) -> str:
    """Réponse attendue en clair (même règle que la simulation hors ligne de la pipeline)."""
    t = expected.get("type")
    try:
        if t == "rational":
            n, d = expected["value"]
            return f"{n}/{d}" if d != 1 else str(n)
        if t == "expression":
            return str(expected["value"]).replace("*", "×")
        if t == "rubric":
            return " ".join(str(s.get("expected_text", "")) for s in expected.get("steps") or []).strip()
    except (KeyError, TypeError, ValueError):
        return ""
    v = expected.get("value")
    return "" if v is None else str(v)


def build(statement: str, expected: str) -> dict | None:
    """{"statement", "expected"} ou None s'il n'y a rien d'utile à donner."""
    st = _plain(statement)[:MAX_STATEMENT_CHARS]
    ex = str(expected or "").strip()[:MAX_EXPECTED_CHARS]
    if not st and not ex:
        return None
    return {"statement": st, "expected": ex}


def for_item(item) -> dict | None:
    return build(item.statement or "", expected_text(item.expected_json or {}))


def for_cell(item, ri: int, ci: int) -> dict | None:
    """Une case de tableau : l'énoncé et la valeur attendue de CETTE case."""
    cells = (item.expected_json or {}).get("cells") or []
    value = ""
    if ri < len(cells) and ci < len(cells[ri]):
        cell = cells[ri][ci] or {}
        v = cell.get("value")
        value = "" if v is None else (f"{v[0]}/{v[1]}" if isinstance(v, list) and len(v) == 2 else str(v))
    return build(item.statement or "", value)


def echo_of(context: dict | None, zone_class: str) -> tuple[frozenset, str]:
    """Ce que le contexte peut souffler : la réponse attendue et chaque nombre de l'énoncé
    ou de la réponse (surfaces exactes), plus, pour les classes de texte, le texte entier
    (une lecture qui en est un morceau est soufflée)."""
    if not context:
        return frozenset(), ""
    st, ex = context.get("statement") or "", context.get("expected") or ""
    out = set()
    if ex:
        out.add(to_surface(ex, zone_class))
    for m in _NUMBER.finditer(f"{st} {ex}"):
        out.add(to_surface(m.group(0), zone_class))
    out.discard("")
    text = ""
    if zone_class in ("text_line", "multiline_line"):
        text = to_surface(f"{ex}\n{st}", zone_class)
    return frozenset(out), text

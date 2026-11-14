"""Briques communes des pages de suivi imprimées (groupes de besoin, bilan pour la
famille, plan de révision) : polices de la charte, en-tête, paragraphes, courbe.

Sobre et économe en encre : pas d'aplat, des filets et du texte. Jamais de rouge :
ces pages vont aux élèves et aux familles.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .delivery_sheet import _register_fonts
from .mathrender import strip_math

INK = HexColor("#16202a")
GREY = HexColor("#6b7782")
RULE = HexColor("#cfd4d9")
ACCENT = HexColor("#1f7a74")     # sarcelle de la charte
SOFT = HexColor("#9cc9c4")
W, H = A4
X0 = 18 * mm
RIGHT = W - 18 * mm
USABLE = RIGHT - X0


def plain(text: str | None) -> str:
    """Texte imprimable : LaTeX aplati, sans marqueurs."""
    return strip_math(text or "")


class Page:
    """Une page A4 qu'on remplit de haut en bas (`y` descend)."""

    def __init__(self, c: canvas.Canvas):
        self.c = c
        self.y = H - 16 * mm

    def header(self, kind: str, title: str, subtitle: str = "") -> None:
        c = self.c
        c.setFillColor(ACCENT); c.setFont("KNSerif", 12); c.drawString(X0, self.y, "Kiro & Nina")
        c.setFillColor(GREY); c.setFont("KNSansMd", 7.5); c.drawRightString(RIGHT, self.y + 0.5, kind.upper())
        self.y -= 4 * mm
        self.rule()
        self.y -= 9 * mm
        self.paragraph(title, "KNSerif", 22, leading=25)
        if subtitle:
            self.y -= 1 * mm
            self.paragraph(subtitle, "KNSans", 10.5, color=GREY)
        self.y -= 4 * mm

    def rule(self) -> None:
        self.c.setStrokeColor(RULE); self.c.setLineWidth(0.5)
        self.c.line(X0, self.y, RIGHT, self.y)

    def label(self, text: str) -> None:
        self.c.setFont("KNSansMd", 7.5); self.c.setFillColor(ACCENT)
        self.c.drawString(X0, self.y, text.upper())
        self.y -= 5 * mm

    def paragraph(self, text: str, font: str = "KNSans", size: float = 10.5, *, color=INK,
                  leading: float | None = None, x: float = X0, width: float | None = None) -> float:
        style = ParagraphStyle("p", fontName=font, fontSize=size, leading=leading or size * 1.3,
                               textColor=color)
        para = Paragraph(escape(text), style)
        _, h = para.wrap(width or (RIGHT - x), 2000)
        para.drawOn(self.c, x, self.y - h)
        self.y -= h
        return h

    def check(self, x: float, y: float, size: float = 3.2 * mm) -> None:
        """Coche tracée (les polices de la charte n'ont pas le glyphe)."""
        c = self.c
        c.setStrokeColor(ACCENT); c.setLineWidth(1.3)
        c.line(x, y + size * 0.45, x + size * 0.38, y + size * 0.1)
        c.line(x + size * 0.38, y + size * 0.1, x + size, y + size * 0.9)

    def box(self, x: float, y: float, size: float = 3.4 * mm) -> None:
        self.c.setStrokeColor(GREY); self.c.setLineWidth(0.7)
        self.c.rect(x, y, size, size, stroke=1, fill=0)

    def footer(self, text: str) -> None:
        self.c.setFont("KNSans", 7.5); self.c.setFillColor(GREY)
        self.c.drawString(X0, 10 * mm, text)


def curve(page: Page, points: list[dict], height: float = 38 * mm, marker_index: int | None = None) -> None:
    """Courbe de l'élève contre son propre passé : valeurs 0-1, sans axe chiffré
    ni repère de classe. `marker_index` = premier point de la période."""
    c = page.c
    top = page.y
    bottom = top - height
    c.setStrokeColor(RULE); c.setLineWidth(0.5)
    c.line(X0, bottom, RIGHT, bottom)
    if len(points) < 2:
        c.setFont("KNSans", 9); c.setFillColor(GREY)
        c.drawString(X0, bottom + height / 2, "La courbe se dessinera au fil des prochains devoirs.")
        page.y = bottom - 4 * mm
        return
    n = len(points)
    xs = [X0 + 4 * mm + i * (USABLE - 8 * mm) / (n - 1) for i in range(n)]
    ys = [bottom + 3 * mm + max(0.0, min(1.0, p["value"])) * (height - 6 * mm) for p in points]
    if marker_index is not None and 0 < marker_index < n:
        mx = (xs[marker_index - 1] + xs[marker_index]) / 2
        c.setStrokeColor(RULE); c.setDash(2, 2); c.line(mx, bottom, mx, top); c.setDash()
        c.setFont("KNSans", 7); c.setFillColor(GREY)
        c.drawString(mx + 1.5 * mm, top - 3 * mm, "cette période")
    c.setStrokeColor(ACCENT); c.setLineWidth(1.6)
    path = c.beginPath(); path.moveTo(xs[0], ys[0])
    for x, y in zip(xs[1:], ys[1:]):
        path.lineTo(x, y)
    c.drawPath(path, stroke=1, fill=0)
    c.setFillColor(ACCENT)
    for x, y in zip(xs, ys):
        c.circle(x, y, 1.1 * mm, stroke=0, fill=1)
    c.setFont("KNSans", 7); c.setFillColor(GREY)
    c.drawString(xs[0] - 2 * mm, bottom - 3.5 * mm, points[0].get("label", ""))
    c.drawRightString(xs[-1] + 2 * mm, bottom - 3.5 * mm, points[-1].get("label", ""))
    page.y = bottom - 7 * mm


def document(title: str, draw_pages) -> bytes:
    """`draw_pages(new_page)` appelle `new_page()` pour chaque page voulue."""
    _register_fonts()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    state = {"open": False}

    def new_page() -> Page:
        if state["open"]:
            c.showPage()
        state["open"] = True
        return Page(c)

    draw_pages(new_page)
    if not state["open"]:
        new_page()
    c.showPage(); c.save()
    return buf.getvalue()

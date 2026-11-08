"""SVG restreint : les dessins qu'aucun gabarit ne couvre.

Le SVG vient d'un modèle : il n'est jamais rendu tel quel. Une liste blanche de
balises et d'attributs écarte scripts, liens externes et entités, et la balise
racine fixe la taille physique. Spec : {"svg": "<svg ...>...</svg>", "width_mm": 60}
"""
from __future__ import annotations

import io
import math
import re
from xml.etree import ElementTree

import fitz
from PIL import Image

from . import figkit as fk
from .figkit import fail

_TAGS = {"svg", "g", "line", "polyline", "polygon", "rect", "circle", "ellipse", "path", "text"}
_ATTRS = {
    "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "points", "d", "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
    "stroke-linejoin", "font-size", "font-family", "font-weight", "text-anchor", "transform",
    "opacity", "xmlns",
}
_COLOR_RE = re.compile(r"^(none|black|white|gr[ae]y|#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6})$")
_MAX_LEN = 20_000
_FORBIDDEN = re.compile(r"<!|<\?|url\(|javascript:|href", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check(svg: str) -> ElementTree.Element:
    if not isinstance(svg, str) or not svg.strip():
        fail("svg", "texte SVG attendu")
    if len(svg) > _MAX_LEN:
        fail("svg", f"{len(svg)} caractères, maximum {_MAX_LEN}")
    # Avant toute analyse XML : pas de DOCTYPE ni d'entité (bombes d'expansion).
    if _FORBIDDEN.search(svg):
        fail("svg", "DOCTYPE, entité, lien, url() ou script interdits")
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        fail("svg", f"XML invalide : {exc}")
    if _local(root.tag) != "svg" or not root.get("viewBox"):
        fail("svg", "racine <svg viewBox=\"…\"> attendue")
    for node in root.iter():
        tag = _local(node.tag)
        if tag not in _TAGS:
            fail("svg", f"balise interdite <{tag}>")
        for name, value in node.attrib.items():
            attr = _local(name)
            if attr not in _ATTRS:
                fail("svg", f"attribut interdit {attr!r} sur <{tag}>")
            if attr in ("fill", "stroke") and not _COLOR_RE.match(value.strip()):
                fail("svg", f"couleur non autorisée {value!r} : noir, gris, blanc ou #hex")
    return root


def render(params: dict, *, dry: bool = False) -> bytes:
    spec = fk.as_dict(params, "params")
    unknown = set(spec) - {"svg", "width_mm"}
    if unknown:
        fail("params", f"clé(s) inconnue(s) {sorted(unknown)}")
    root = _check(spec.get("svg"))
    _expand_dashes(root)
    w_mm = fk.width_mm(spec)
    source = ElementTree.tostring(root, encoding="utf-8")
    try:
        with fitz.open(stream=source, filetype="svg") as doc:
            page = doc[0]
            zoom = (w_mm / 25.4 * fk.RENDER_DPI) / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png = pix.tobytes("png")
    except Exception as exc:  # noqa: BLE001 - MuPDF signale un SVG illisible par exception
        fail("svg", f"rendu impossible ({exc.__class__.__name__})")
    if dry:
        return b""
    # Taille physique dans les métadonnées, relue par pdfgen._figure_image.
    out = io.BytesIO()
    with Image.open(io.BytesIO(png)) as image:
        image.save(out, format="PNG", dpi=(fk.RENDER_DPI, fk.RENDER_DPI))
    return out.getvalue()


def validate(params: dict) -> dict:
    render(params, dry=True)
    return params


def _expand_dashes(root, inherited=None):
    """MuPDF ignore les tirets sur certains éléments SVG : on trace chaque tiret.

    Les courbes non prises en charge sont refusées, jamais imprimées en trait plein.
    """
    dash = root.attrib.pop("stroke-dasharray", inherited)
    tag = _local(root.tag)
    if dash and dash != "none" and tag not in ("g", "svg"):
        try:
            pattern = [float(n) for n in re.split(r"[ ,]+", dash.strip())]
            if not pattern or any(n <= 0 for n in pattern):
                raise ValueError()
            if len(pattern) % 2:
                pattern *= 2
            points = []
            if tag == "line":
                points = [(float(root.get("x1", 0)), float(root.get("y1", 0))),
                          (float(root.get("x2", 0)), float(root.get("y2", 0)))]
            elif tag in ("polyline", "polygon"):
                values = [float(n) for n in re.split(r"[ ,]+", root.get("points", "").strip())]
                points = list(zip(values[::2], values[1::2]))
                if tag == "polygon" and points:
                    points.append(points[0])
            elif tag in ("circle", "ellipse"):
                cx, cy = float(root.get("cx", 0)), float(root.get("cy", 0))
                rx, ry = float(root.get("r", root.get("rx", 0))), float(root.get("r", root.get("ry", 0)))
                points = [(cx + rx * math.cos(i * math.pi / 180), cy + ry * math.sin(i * math.pi / 180)) for i in range(361)]
            else:
                fail("svg", f"pointillés sur {tag} non pris en charge : utilise line/polyline/ellipse")
            pieces, index, remaining = [], 0, pattern[0]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                length = math.hypot(x2 - x1, y2 - y1)
                pos = 0.0
                while pos < length - 1e-9:
                    end = min(length, pos + remaining)
                    if index % 2 == 0:
                        pieces.append(f"M {x1+(x2-x1)*pos/length:g} {y1+(y2-y1)*pos/length:g} L {x1+(x2-x1)*end/length:g} {y1+(y2-y1)*end/length:g}")
                    remaining -= end - pos
                    pos = end
                    if remaining < 1e-9:
                        index = (index + 1) % len(pattern)
                        remaining = pattern[index]
            root.tag = "path"
            root.attrib = {k: v for k, v in root.attrib.items() if k not in ("x1", "y1", "x2", "y2", "points", "cx", "cy", "rx", "ry", "r")}
            root.set("d", " ".join(pieces))
            root.set("fill", "none")
        except (TypeError, ValueError, ZeroDivisionError):
            fail("svg", "pointillés ou coordonnées invalides")
    for child in root:
        _expand_dashes(child, dash)

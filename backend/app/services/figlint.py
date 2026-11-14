"""Contrôle géométrique d'une figure TikZ compilée : ce que l'œil verrait de travers.

La figure est jugée sur ses objets, pas sur une impression d'ensemble :

  - un texte (nom de point, cote, valeur d'angle, graduation) posé sur un trait ;
  - deux textes qui se recouvrent.

Méthode. Le PDF est rendu deux fois, avec et sans son texte : la différence donne
l'encre exacte des textes, quelle que soit leur rotation. Les traits viennent des
tracés vectoriels du PDF, redessinés à leur épaisseur. Un texte fautif est celui
dont l'encre touche un trait. Sont ignorés les traits que la charte dessine très
clairs pour passer sous les textes : quadrillage, ligne de cote, lignes de rappel.

Rien ici ne juge le sens de la figure. Un message dit quel texte déplacer et où il
se trouve, pour que l'auteur corrige sans regarder l'image.
"""
from __future__ import annotations

import io
import re

DPI = 300
_SCALE = DPI / 72
# Un trait plus clair que ce seuil est fait pour passer sous un texte (quadrillage
# `gridc`, ligne de cote `cotec!40`, rappel `ink!25`).
LIGHT_LUMINANCE = 0.68
# Encre commune minimale, en pixels à 300 dpi : en dessous, c'est l'anticrénelage.
MIN_OVERLAP_PX = 10
# Recouvrement minimal de deux textes, en fraction du plus petit.
MIN_TEXT_OVERLAP = 0.12
_BT = re.compile(rb"(?<![A-Za-z0-9/])BT(?![A-Za-z0-9])")


def _luminance(color) -> float:
    r, g, b = color[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _without_text(pdf: bytes) -> bytes:
    """Le même PDF, texte invisible (mode de rendu 3 après chaque début de texte)."""
    import fitz
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for xref in range(1, doc.xref_length()):
            if not doc.xref_is_stream(xref):
                continue
            try:
                data = doc.xref_stream(xref)
            except Exception:  # noqa: BLE001 — flux illisible (police, image) : inchangé
                continue
            if data and _BT.search(data) and b"Tf" in data:
                doc.update_stream(xref, _BT.sub(b"BT 3 Tr", data))
        return doc.tobytes()


def _gray(pdf: bytes):
    import fitz
    import numpy as np
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype("int16")


def _flatten(p0, p1, p2, p3, steps: int = 14):
    out = []
    for k in range(steps + 1):
        t = k / steps
        u = 1 - t
        out.append((u ** 3 * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t ** 3 * p3.x,
                    u ** 3 * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t ** 3 * p3.y))
    return out


def _stroke_mask(page, shape):
    """Masque des traits qui comptent, redessinés à leur épaisseur réelle."""
    import numpy as np
    from PIL import Image, ImageDraw
    image = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(image)
    for path in page.get_drawings():
        color = path.get("color")
        if color is None or _luminance(color) >= LIGHT_LUMINANCE:
            continue
        width = max(1, round((path.get("width") or 0.4) * _SCALE))
        for item in path["items"]:
            kind = item[0]
            if kind == "l":
                points = [(item[1].x, item[1].y), (item[2].x, item[2].y)]
            elif kind == "c":
                points = _flatten(*item[1:5])
            elif kind == "re":
                r = item[1]
                points = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]
            elif kind == "qu":
                q = item[1]
                points = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y),
                          (q.ul.x, q.ul.y)]
            else:
                continue
            draw.line([(x * _SCALE, y * _SCALE) for x, y in points], fill=255, width=width)
    return np.asarray(image) > 0


def _labels(page) -> list[dict]:
    """Chaque texte de la figure : son contenu et sa boîte, en points. Le PDF range
    parfois deux noms voisins dans une même suite de caractères : on les sépare dès
    qu'un caractère commence nettement avant la fin du précédent, ce qu'un texte
    composé normalement ne fait jamais."""
    out = []
    for block in page.get_text("rawdict").get("blocks", []):
        for line in block.get("lines", []):
            # Un texte incliné (cote le long d'un côté) a des caractères dont les boîtes
            # droites se chevauchent par construction : on ne le découpe pas.
            level = abs((line.get("dir") or (1, 0))[1]) < 0.02
            for span in line.get("spans", []):
                current = None
                for char in span.get("chars", []):
                    glyph = char.get("c", "")
                    x0, y0, x1, y1 = char["bbox"]
                    if not glyph.strip():
                        continue
                    if current and level and x0 < current["bbox"][2] - 0.3 * (x1 - x0):
                        out.append(current)
                        current = None
                    if current is None:
                        current = {"text": glyph, "bbox": [x0, y0, x1, y1], "level": level}
                    else:
                        current["text"] += glyph
                        box = current["bbox"]
                        current["bbox"] = [min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)]
                if current:
                    out.append(current)
    return [{"text": label["text"], "bbox": tuple(label["bbox"]), "level": label["level"]}
            for label in out]


def _text_box(label: dict, ink_box: tuple) -> tuple:
    """Boîte servant à comparer deux textes, en pixels. Pour un texte droit : sa boîte
    de police resserrée à la hauteur des lettres, car la boîte entière mord sur les
    lignes voisines. Pour un texte incliné : son encre."""
    if not label.get("level"):
        return ink_box
    x0, y0, x1, y1 = label["bbox"]
    height = y1 - y0
    return (x0 * _SCALE, (y0 + 0.24 * height) * _SCALE, x1 * _SCALE, (y1 - 0.20 * height) * _SCALE)


def _where(bbox, page_rect) -> str:
    """Position lisible par l'auteur : en mm depuis le coin bas gauche de la figure."""
    x = (bbox[0] + bbox[2]) / 2 / 72 * 25.4
    y = (page_rect.height - (bbox[1] + bbox[3]) / 2) / 72 * 25.4
    return f"à {x:.0f} mm du bord gauche et {y:.0f} mm du bas"


def _name(text: str) -> str:
    clean = "".join(ch for ch in text if ch.isprintable())[:24]
    return f"« {clean} »" if clean else "un texte"


def inspect_pdf(pdf: bytes) -> list[str]:
    """Défauts géométriques d'une figure compilée, en phrases pour l'auteur."""
    import fitz
    import numpy as np
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        labels = _labels(page)
        if not labels:
            return []
        full = _gray(pdf)
        bare = _gray(_without_text(pdf))
        if full.shape != bare.shape:
            return []
        ink = np.abs(full - bare).max(axis=2) > 40
        strokes = _stroke_mask(page, ink.shape)
        problems = []
        boxes = []
        for label in labels:
            x0, y0, x1, y1 = (int(v * _SCALE) for v in label["bbox"])
            x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
            x1, y1 = min(ink.shape[1], x1 + 2), min(ink.shape[0], y1 + 2)
            region = ink[y0:y1, x0:x1]
            if not region.any():
                continue
            rows = np.flatnonzero(region.any(axis=1))
            cols = np.flatnonzero(region.any(axis=0))
            boxes.append((label, _text_box(label, (x0 + cols[0], y0 + rows[0], x0 + cols[-1] + 1, y0 + rows[-1] + 1))))
            touching = int((region & strokes[y0:y1, x0:x1]).sum())
            if touching >= MIN_OVERLAP_PX:
                problems.append(f"{_name(label['text'])} est posé sur un trait "
                                f"({_where(label['bbox'], page.rect)}) : déplace-le du côté libre")
        for i, (first, a) in enumerate(boxes):
            for second, b in boxes[i + 1:]:
                w = min(a[2], b[2]) - max(a[0], b[0])
                h = min(a[3], b[3]) - max(a[1], b[1])
                if w <= 0 or h <= 0:
                    continue
                smallest = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if smallest and w * h / smallest >= MIN_TEXT_OVERLAP:
                    problems.append(f"{_name(first['text'])} et {_name(second['text'])} se recouvrent "
                                    f"({_where(first['bbox'], page.rect)}) : écarte-les")
        return problems


def render_debug(pdf: bytes) -> bytes:
    """Image de mise au point : encre des textes en rouge, traits retenus en bleu."""
    import fitz
    import numpy as np
    from PIL import Image
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        full = _gray(pdf)
        bare = _gray(_without_text(pdf))
        ink = np.abs(full - bare).max(axis=2) > 40
        strokes = _stroke_mask(doc[0], ink.shape)
    out = np.full((*ink.shape, 3), 255, dtype=np.uint8)
    out[strokes] = (60, 110, 220)
    out[ink] = (220, 40, 40)
    out[ink & strokes] = (0, 0, 0)
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ carte entière
def inspect_card(pdf: bytes, clip_height: float, frame: tuple[float, float] | None = None) -> list[str]:
    """Défauts géométriques d'une carte rendue : texte sur un trait (case, tableau,
    cadre), texte sur une figure ou une formule, textes qui se recouvrent, texte coupé
    par le bord. `clip_height` : hauteur utile de la carte, en points ; `frame` : bords
    gauche et droit du cadre, en points."""
    import fitz
    import numpy as np
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        labels = _labels(page)
        if not labels:
            return []
        full = _gray(pdf)
        bare = _gray(_without_text(pdf))
        if full.shape != bare.shape:
            return []
        ink = np.abs(full - bare).max(axis=2) > 40
        limit = min(ink.shape[0], int(clip_height * _SCALE))
        strokes = _stroke_mask(page, ink.shape)
        pictures = np.zeros(ink.shape, dtype=bool)
        dark = bare.min(axis=2) < 215
        for info in page.get_image_info():
            x0, y0, x1, y1 = (int(v * _SCALE) for v in info["bbox"])
            x0, y0 = max(0, x0), max(0, y0)
            pictures[y0:y1, x0:x1] = dark[y0:y1, x0:x1]
        problems, boxes = [], []
        for label in labels:
            x0, y0, x1, y1 = (int(v * _SCALE) for v in label["bbox"])
            x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
            x1, y1 = min(ink.shape[1], x1 + 2), min(ink.shape[0], y1 + 2)
            if y0 >= limit:
                continue                      # bande de correction, jamais imprimée ici
            region = ink[y0:y1, x0:x1]
            if not region.any():
                continue
            rows = np.flatnonzero(region.any(axis=1))
            cols = np.flatnonzero(region.any(axis=0))
            box = (x0 + cols[0], y0 + rows[0], x0 + cols[-1] + 1, y0 + rows[-1] + 1)
            boxes.append((label, _text_box(label, box)))
            where = _where(label["bbox"], fitz.Rect(0, 0, page.rect.width, clip_height))
            if int((region & strokes[y0:y1, x0:x1]).sum()) >= MIN_OVERLAP_PX:
                problems.append(f"carte : {_name(label['text'])} touche un trait ({where})")
            if int((region & pictures[y0:y1, x0:x1]).sum()) >= MIN_OVERLAP_PX:
                problems.append(f"carte : {_name(label['text'])} recouvre une figure ou une formule ({where})")
            if frame and (box[0] < frame[0] * _SCALE - 1 or box[2] > frame[1] * _SCALE + 1):
                problems.append(f"carte : {_name(label['text'])} sort du cadre ({where})")
            if box[3] > limit:
                problems.append(f"carte : {_name(label['text'])} est coupé par le bas de la carte")
        for i, (first, a) in enumerate(boxes):
            for second, b in boxes[i + 1:]:
                w = min(a[2], b[2]) - max(a[0], b[0])
                h = min(a[3], b[3]) - max(a[1], b[1])
                if w <= 0 or h <= 0:
                    continue
                smallest = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if smallest and w * h / smallest >= MIN_TEXT_OVERLAP:
                    problems.append(f"carte : {_name(first['text'])} et {_name(second['text'])} se recouvrent")
        return problems

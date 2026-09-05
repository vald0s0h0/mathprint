"""Indigo — accès aux PDF des manuels (élève / prof) pour l'onglet Exercices.

Deux PDF par niveau, configurés dans `settings.indigo_manuals` : "eleve"
(énoncés + badges couleur) et "prof" (corrigés). Ils sont volumineux (le
manuel élève ~200 Mo) et restent LOCAUX à l'instance admin (dossier
`context/`), jamais livrés dans l'image — l'onglet est admin-only et la
construction se fait sur cette instance.

Tout le pipeline Indigo travaille dans UN seul espace de coordonnées : le
raster de la page à `RASTER_DPI`. L'OCR Mistral renvoie ses bbox dans SES
propres dimensions de page ; on les remet à l'échelle du raster (cf.
`services.indigo`). Ainsi crop affiché et analyse CV partagent exactement le
même repère, et l'éditeur peut redéfinir librement le recadrage en pixels.
"""
from __future__ import annotations

import io
import logging

import cv2
import fitz  # PyMuPDF
import numpy as np

from ..config import settings
from .sesamaths_pdf import _open_cached, _resolve_manual_path

logger = logging.getLogger("app.indigo")

# DPI du raster de travail (crop + CV). 150 suffit pour lire une couleur de
# badge et produire un crop net à l'écran sans exploser la mémoire sur un
# manuel de 200 Mo (une page A4 à 150 dpi ≈ 1240×1750 px).
RASTER_DPI = 150
# DPI des vignettes de l'assistant (sélection de pages) — plus léger.
PREVIEW_DPI = 96


def manual_path(grade_level: str, which: str):
    """Chemin résolu du PDF (which = "eleve" | "prof") ou None si absent."""
    cfg = settings.indigo_manuals.get(grade_level) or {}
    path_str = cfg.get(which)
    if not path_str:
        return None
    return _resolve_manual_path(path_str)


def open_pdf(grade_level: str, which: str) -> "fitz.Document | None":
    """Ouvre (avec cache par (chemin, mtime)) le PDF du manuel, ou None.

    Strictement le PDF : sert à ce qui ne peut PAS se faire sans lui (construire
    l'index, exporter un pack de travail)."""
    path = manual_path(grade_level, which)
    if path is None:
        return None
    _sha, doc = _open_cached(path)
    return doc


def open_doc(grade_level: str, which: str):
    """Source de pages du manuel : le PDF s'il est là, sinon le PACK DE TRAVAIL.

    Les manuels ne sont livrés à aucune instance (trop gros, sous droits) : sur
    un déploiement, la fabrication travaille sur les pages rendues d'avance et
    importées via `services.indigo_pack`. Les deux objets répondent aux mêmes
    besoins (`page_count`, `raster_page`), si bien que ni la découpe, ni la CV,
    ni l'éditeur de figure ne savent lequel des deux ils manipulent.

    Le pack ne concerne que le manuel ÉLÈVE (le seul dont on rend des pixels) :
    les corrigés du prof voyagent en TEXTE dans l'index."""
    doc = open_pdf(grade_level, which)
    if doc is not None or which != "eleve":
        return doc
    from . import indigo_pack
    return indigo_pack.load(grade_level)


def page_count(grade_level: str, which: str) -> int:
    doc = open_doc(grade_level, which)
    return doc.page_count if doc else 0


def _pixmap_to_bgr(pix: "fitz.Pixmap") -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)


def render_preview_png(doc, idx: int, dpi: int = PREVIEW_DPI) -> bytes:
    """Vignette PNG d'une page (assistant de sélection), depuis le PDF ou le pack."""
    if getattr(doc, "raster_page", None) is not None:
        return encode_png(raster_page(doc, idx, dpi))
    return doc[idx].get_pixmap(dpi=dpi).tobytes("png")


def raster_page(doc, idx: int, dpi: int = RASTER_DPI) -> np.ndarray:
    """Raster BGR d'une page à `dpi` — repère commun crop + CV.

    `doc` est le PDF du manuel, ou un pack de pages déjà rendues (cf.
    `open_doc`) : celui-ci se reconnaît à sa méthode `raster_page`, qu'un
    `fitz.Document` n'a pas."""
    from_pack = getattr(doc, "raster_page", None)
    if from_pack is not None:
        return from_pack(idx, dpi)
    return _pixmap_to_bgr(doc[idx].get_pixmap(dpi=dpi))


def text_blocks(doc: "fitz.Document", idx: int, dpi: int = RASTER_DPI) -> list[dict]:
    """Blocs de la COUCHE TEXTE d'une page, au format des blocs Mistral.

    Le manuel du PROFESSEUR est un PDF texte (contrairement au manuel élève, qui
    n'est qu'une suite d'images) : ses corrigés se lisent GRATUITEMENT avec
    PyMuPDF, sans passer par l'OCR payant. On rend donc les blocs dans le
    vocabulaire de Mistral (`type`, `content`, `top_left_x/y`,
    `bottom_right_x/y`), à l'échelle du raster de travail, pour que tout l'aval
    — `_order_blocks`, `_leading_num`, `_segment_corrections_by_numbers` —
    fonctionne sans savoir d'où viennent les blocs.

    Rend [] sur une page sans texte : l'appelant repasse alors par l'OCR (cf.
    services.indigo_index), au lieu de croire la page vide."""
    page = doc[idx]
    scale = dpi / 72.0                       # PyMuPDF travaille en points (72 dpi)
    out: list[dict] = []
    for x0, y0, x1, y1, content, _no, _type in page.get_text("blocks"):
        text = str(content or "").strip()
        if not text:
            continue
        out.append({"type": "text", "content": text,
                    "top_left_x": x0 * scale, "top_left_y": y0 * scale,
                    "bottom_right_x": x1 * scale, "bottom_right_y": y1 * scale})
    return out


def page_dims(doc: "fitz.Document", idx: int, dpi: int = RASTER_DPI) -> dict:
    """Dimensions de la page dans le repère du raster (mêmes unités que les
    bbox rendues par `text_blocks`)."""
    rect = doc[idx].rect
    scale = dpi / 72.0
    return {"width": rect.width * scale, "height": rect.height * scale}


def build_mini_pdf(doc: "fitz.Document", page_indices: list[int]) -> bytes:
    """Mini-PDF ne contenant QUE les pages demandées (indices 0-based, dans
    l'ordre donné) — envoyé tel quel à l'OCR Mistral, qui renumérote à partir
    de 0. On garde donc la correspondance mini-page → page source via l'ordre
    de `page_indices` (cf. services.indigo)."""
    sub = fitz.open()
    try:
        for idx in page_indices:
            if 0 <= idx < doc.page_count:
                sub.insert_pdf(doc, from_page=idx, to_page=idx)
        return sub.tobytes()
    finally:
        sub.close()


def encode_png(bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("Échec d'encodage PNG du crop Indigo")
    return buf.tobytes()

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


def open_doc(grade_level: str, which: str) -> "fitz.Document | None":
    """Ouvre (avec cache par (chemin, mtime)) le manuel demandé, ou None."""
    path = manual_path(grade_level, which)
    if path is None:
        return None
    _sha, doc = _open_cached(path)
    return doc


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


def render_preview_png(doc: "fitz.Document", idx: int, dpi: int = PREVIEW_DPI) -> bytes:
    """Vignette PNG d'une page (assistant de sélection)."""
    return doc[idx].get_pixmap(dpi=dpi).tobytes("png")


def raster_page(doc: "fitz.Document", idx: int, dpi: int = RASTER_DPI) -> np.ndarray:
    """Raster BGR d'une page à `dpi` — repère commun crop + CV."""
    return _pixmap_to_bgr(doc[idx].get_pixmap(dpi=dpi))


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

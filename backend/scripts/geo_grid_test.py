#!/usr/bin/env python3
"""Géométrie à tracer : test à faire AVANT tout code de lecture des traits.

Principe prévu : imprimer le quadrillage et les traits de l'énoncé en rouge saumon,
pour que le filtre de lecture les efface et ne laisse que les traits de l'élève. Mais
certains copieurs restituent le saumon en gris : le filtre ne l'enlève plus (cf.
worker_cv). Seul un vrai passage sur vos machines peut le dire.

1. Générer la feuille :
       python scripts/geo_grid_test.py sheet feuille.pdf
2. L'imprimer, la photocopier si vos sujets le sont, tracer quelques traits au crayon
   et au stylo dans les cadres, puis la scanner comme une vraie copie.
3. Mesurer chaque scan (PNG ou PDF) :
       python scripts/geo_grid_test.py measure scan.pdf

Verdict : le résidu de quadrillage après filtre doit rester négligeable, et les traits
de l'élève doivent survivre.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import fitz  # noqa: E402
import numpy as np  # noqa: E402
from reportlab.lib.colors import HexColor  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import worker_cv  # noqa: E402

GRID_STEP = 5 * mm
# (x, y, largeur, hauteur) en mm depuis le coin bas gauche de la page
FRAMES = ((15, 170, 85, 85), (110, 170, 85, 85), (15, 60, 85, 85), (110, 60, 85, 85))
RESIDUE_OK = 0.002          # part de pixels sombres restants dans un cadre vide
RESIDUE_BAD = 0.01


def make_sheet(path: str) -> None:
    c = canvas.Canvas(path, pagesize=A4)
    salmon = HexColor(settings.dropout_color)
    c.setFont("Helvetica", 10)
    c.drawString(15 * mm, 280 * mm, "Test du quadrillage : ne tracer que dans les cadres 2 et 4.")
    for index, (x, y, w, h) in enumerate(FRAMES, start=1):
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor("#000000"))
        c.drawString(x * mm, (y + h + 2) * mm, f"Cadre {index}" + (" : laisser vide" if index % 2 else " : tracer ici"))
        c.setStrokeColor(salmon)
        c.setLineWidth(0.4)
        for i in range(int(w * mm / GRID_STEP) + 1):
            c.line(x * mm + i * GRID_STEP, y * mm, x * mm + i * GRID_STEP, (y + h) * mm)
        for j in range(int(h * mm / GRID_STEP) + 1):
            c.line(x * mm, y * mm + j * GRID_STEP, (x + w) * mm, y * mm + j * GRID_STEP)
        # un trait d'énoncé, dessiné comme le quadrillage : il doit disparaître aussi
        c.setLineWidth(1.0)
        c.line((x + 10) * mm, (y + 10) * mm, (x + 50) * mm, (y + 40) * mm)
    c.save()


def _load(path: str) -> np.ndarray:
    if path.lower().endswith(".pdf"):
        with fitz.open(path) as doc:
            pix = doc[0].get_pixmap(dpi=200)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2BGR)
    image = cv2.imread(path)
    if image is None:
        raise SystemExit(f"Image illisible : {path}")
    return image


def measure_frames(image: np.ndarray) -> list[dict]:
    """Encre restante après le filtre, dans chaque cadre. Sans recalage : la feuille
    de test n'a pas de repères, d'où une marge intérieure de sécurité."""
    height, width = image.shape[:2]
    margin = 3.0
    out = []
    for index, (x, y, w, h) in enumerate(FRAMES, start=1):
        x0 = int((x + margin) / 210 * width)
        x1 = int((x + w - margin) / 210 * width)
        y0 = int((297 - y - h + margin) / 297 * height)
        y1 = int((297 - y - margin) / 297 * height)
        crop = image[y0:y1, x0:x1]
        out.append({"frame": index, "empty": bool(index % 2),
                    "ink": worker_cv.ink_ratio(worker_cv.dropout_filter(crop)),
                    "raw_ink": worker_cv.ink_ratio(crop)})
    return out


def verdict(frames: list[dict]) -> tuple[str, str]:
    empties = [f["ink"] for f in frames if f["empty"]]
    drawn = [f["ink"] for f in frames if not f["empty"]]
    residue = max(empties)
    if residue > RESIDUE_BAD:
        return "ÉCHEC", ("Le quadrillage survit au filtre : ce copieur le rend en gris. Il faudra un "
                         "quadrillage plus clair, ou soustraire le tracé connu.")
    if drawn and max(drawn) <= residue * 2:
        return "ÉCHEC", "Les traits de l'élève ne ressortent pas : ont-ils été tracés dans les cadres 2 et 4 ?"
    if residue > RESIDUE_OK:
        return "À SURVEILLER", "Résidu faible mais présent : à revérifier sur un autre copieur."
    return "OK", "Le quadrillage disparaît et les traits restent : le principe tient sur cette machine."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("sheet", "measure"))
    parser.add_argument("path")
    args = parser.parse_args(argv)
    if args.action == "sheet":
        make_sheet(args.path)
        print(f"Feuille écrite : {args.path}")
        return 0
    frames = measure_frames(_load(args.path))
    for frame in frames:
        print(f"Cadre {frame['frame']} ({'vide' if frame['empty'] else 'tracé'}) : "
              f"{frame['ink']:.4%} d'encre après filtre, {frame['raw_ink']:.4%} avant")
    status, reason = verdict(frames)
    print(f"{status} : {reason}")
    return 0 if status != "ÉCHEC" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Manuel Indigo : captures d'écran de la liseuse → PDF au format des manuels.

Certains manuels ne sont disponibles que dans la liseuse en ligne (cas du
manuel élève Indigo 6e). On les capture double page par double page, puis ce
script en fait un PDF au MÊME format que `context/3_indigo.pdf`, ce qui suffit
pour que tout l'aval fonctionne sans modification (Astra, onglet Exercices,
index OCR) :

  • une page PDF = UNE double page imprimée : page PDF p (1-based) = pages
    imprimées 2p-2 (gauche) et 2p-1 (droite) ;
  • la page 1 porte la couverture (page imprimée 1) sur sa moitié droite ;
  • une capture de page SIMPLE (couverture, dernière page) est placée sur la
    moitié qui correspond à sa parité, l'autre moitié reste blanche.

Les captures sont prises dans l'ordre de leur numéro « Capture d'ecran (n) » ;
des numéros sautés dans le nommage ne sont PAS des pages manquantes (vérifié
sur les pastilles de pagination pour la 6e : 127 et 133 n'existent pas, sans
trou dans le livre). Seul l'intérieur de la page est gardé : l'interface de la
liseuse (bandeaux gris, flèches, zoom) est retirée en détectant le fond gris.

    backend/.venv/bin/python backend/scripts/indigo_screenshots_to_pdf.py \\
        "~/Downloads/Livre 6eme/Screenshots" context/6_indigo.pdf
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

# Fond gris de la liseuse (RVB 245,245,245) et tolérance de détection.
READER_BG = np.array([245, 245, 245])
BG_TOL = 12
# Largeur de page PDF en points (A4 paysage, comme le manuel 3e) ; la hauteur
# suit les proportions de la capture pour ne rien déformer.
PAGE_W_PT = 842.0
# Largeur maximale de l'ombre portée autour de la page, en pixels de capture.
SHADOW_PX = 25


def _capture_number(path: Path) -> int:
    m = re.search(r"\((\d+)\)", path.name)
    if not m:
        raise SystemExit(f"Nom de capture inattendu : {path.name}")
    return int(m.group(1))


def _page_box(img: Image.Image) -> tuple[int, int]:
    """Bornes horizontales [x0, x1) de la zone imprimée (une ou deux pages).

    Une colonne appartient à la page (ou à son ombre portée) quand la plupart
    de ses pixels s'écartent du gris de la liseuse. L'ombre est un dégradé qui
    fonce jusqu'au bord de la page : le bord est juste après son minimum, en
    sautant au besoin une colonne d'anticrénelage."""
    a = np.asarray(img.convert("RGB")).astype(int)
    band = a[150:930]
    off = (np.abs(band - READER_BG).sum(axis=2) > BG_TOL).mean(axis=0)
    cols = np.where(off > 0.5)[0]
    cols = cols[(cols > 120) & (cols < img.width - 120)]   # flèches de navigation
    if not len(cols):
        raise SystemExit("Zone de page introuvable dans une capture.")
    cmin, cmax = int(cols.min()), int(cols.max())
    gray = np.asarray(img.convert("L")).astype(int)[300:800].mean(axis=0)
    x0 = cmin + int(np.argmin(gray[cmin:cmin + SHADOW_PX])) + 1
    if gray[x0] < gray[x0 + 1] - 15:
        x0 += 1
    x1 = cmax - SHADOW_PX + 1 + int(np.argmin(gray[cmax - SHADOW_PX + 1:cmax + 1]))
    if gray[x1 - 1] < gray[x1 - 2] - 15:
        x1 -= 1
    return x0, x1


def _mode(values):
    return max(set(values), key=values.count)


def build(src: Path, out: Path) -> int:
    shots = sorted(src.glob("*.png"), key=_capture_number)
    if not shots:
        raise SystemExit(f"Aucune capture PNG dans {src}")
    boxes = []
    for path in shots:
        with Image.open(path) as img:
            boxes.append(_page_box(img))
            height = img.height
    # Le cadrage est le même pour toutes les doubles pages (et pour toutes les
    # pages simples) : on retient le cadrage le plus fréquent de chaque sorte,
    # qu'une page très sombre ne peut pas fausser.
    widths = [x1 - x0 for x0, x1 in boxes]
    double_w = int(np.median(widths))
    is_single = [w < double_w * 0.75 for w in widths]
    doubles = _mode([b for b, s in zip(boxes, is_single) if not s])
    singles = [b for b, s in zip(boxes, is_single) if s]
    single_box = _mode(singles) if singles else None
    double_w = doubles[1] - doubles[0]
    single_w = double_w // 2
    page_h_pt = PAGE_W_PT * height / double_w
    print(f"Cadrage : doubles pages x={doubles[0]}..{doubles[1]} ({double_w}×{height} px)"
          + (f", pages simples x={single_box[0]}..{single_box[1]}" if single_box else ""))

    doc = fitz.open()
    for i, path in enumerate(shots):
        x0, x1 = single_box if is_single[i] else doubles
        crop = Image.open(path).convert("RGB").crop((x0, 0, x1, height))
        sheet = Image.new("RGB", (double_w, height), "white")
        if is_single[i]:
            # couverture (1re capture) = page imprimée 1, à droite ;
            # dernière capture = page paire, à gauche
            x = double_w - single_w if i == 0 else 0
            sheet.paste(crop.resize((single_w, height)) if crop.width != single_w else crop,
                        (x, 0))
        else:
            sheet.paste(crop.resize((double_w, height)) if crop.width != double_w else crop,
                        (0, 0))
        buf = io.BytesIO()
        sheet.save(buf, format="PNG", optimize=True)
        page = doc.new_page(width=PAGE_W_PT, height=page_h_pt)
        page.insert_image(page.rect, stream=buf.getvalue())
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out), garbage=3, deflate=True)
    return doc.page_count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("screenshots", type=Path, help="Dossier des captures « Capture d'ecran (n).png »")
    ap.add_argument("out", type=Path, help="PDF à écrire (ex. context/6_indigo.pdf)")
    args = ap.parse_args(argv)
    n = build(args.screenshots.expanduser(), args.out)
    print(f"{args.out} : {n} page(s) PDF (doubles pages) — page PDF p = pages "
          f"imprimées 2p-2 et 2p-1. Vérifie les numéros de page sur quelques vignettes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fabrique un scan synthétique pour tester le chemin CV réel (§12.3).

Rasterise subject_batch.pdf, écrit des réponses simulées à l'encre bleu foncé
dans les zones (bonnes réponses ~75 %, croix dans les cases QCM, quelques
zones vides), applique une légère rotation, et produit un PDF multi-pages.

Usage : python scripts/make_synthetic_scan.py <assessment_id> [out.pdf]
"""
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings                       # noqa: E402
from app.services import worker_cv                    # noqa: E402
from app.services.worker_cv import DPI, SCALE, pt_to_px  # noqa: E402

INK = (120, 40, 20)  # BGR ~ bleu foncé stylo


def put_ink_text(img, text, x_px, y_px, scale=1.4):
    cv2.putText(img, text, (int(x_px), int(y_px)), cv2.FONT_HERSHEY_SIMPLEX,
                scale, INK, 3, cv2.LINE_AA)


def cross_box(img, box):
    x0, y1 = pt_to_px(box["x_pt"], box["y_pt"])
    x1, y0 = pt_to_px(box["x_pt"] + box["w_pt"], box["y_pt"] + box["h_pt"])
    for (a, b), (c, d) in [((x0, y0), (x1, y1)), ((x0, y1), (x1, y0))]:
        cv2.line(img, (int(a), int(b)), (int(c), int(d)), INK, 3, cv2.LINE_AA)


def main(assessment_id: str, out_path: str | None = None):
    gen = settings.data_dir / "assessments" / assessment_id / "generated"
    manifest = json.loads((gen / "copy_manifest.json").read_text())
    pdf = gen / "subject_batch.pdf"
    out = Path(out_path or gen.parent / "synthetic_scan.pdf")

    pages = worker_cv.raster_pdf(str(pdf))
    rng = random.Random(42)

    # index page_id -> (page image index, zones)
    page_no = 0
    filled_pages = []
    for copy in manifest["copies"]:
        for p in copy["pages"]:
            img = pages[page_no].copy()
            zones = [z for z in copy["zones"] if z["page_id"] == p["page_id"]]
            for z in zones:
                roll = rng.random()
                if z["type"].startswith("qcm"):
                    boxes = z["meta"].get("boxes", [])
                    if roll < 0.70:      # bonne case (inconnu ici : cocher case 0)
                        cross_box(img, boxes[0])
                    elif roll < 0.85:    # autre case
                        cross_box(img, boxes[min(1, len(boxes) - 1)])
                    elif roll < 0.93:    # double coche -> exception attendue
                        cross_box(img, boxes[0])
                        cross_box(img, boxes[min(1, len(boxes) - 1)])
                    # sinon : vide
                else:
                    if roll < 0.80:
                        x_px, y_px = pt_to_px(z["x_pt"] + 8, z["y_pt"] + 8)
                        put_ink_text(img, "42", x_px, y_px)
                    # sinon : zone laissée vide
            # légère rotation (±1,2°) autour du centre, fond blanc
            angle = rng.uniform(-1.2, 1.2)
            h, w = img.shape[:2]
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            img = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
            filled_pages.append(img)
            page_no += 1

    # PDF multi-pages via encodage JPEG + img2pdf-like avec OpenCV -> utiliser PIL
    from PIL import Image
    pil_pages = [Image.fromarray(cv2.cvtColor(p, cv2.COLOR_BGR2RGB)) for p in filled_pages]
    pil_pages[0].save(out, save_all=True, append_images=pil_pages[1:],
                      resolution=DPI)
    print(f"{len(filled_pages)} pages -> {out}")
    return str(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

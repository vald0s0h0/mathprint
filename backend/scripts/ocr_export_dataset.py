"""Exporte des paires (découpe, vérité du professeur) pour un futur affinage de TrOCR.

DONNÉES PERSONNELLES : ce sont des écritures de mineurs. L'export reste LOCAL (jamais vers un service
externe, ni Hugging Face), sans nom d'élève : chaque élève n'apparaît que par un identifiant de groupe
haché (sel aléatoire propre à l'export). La séparation apprentissage / évaluation se fait PAR ÉLÈVE
(sinon une même écriture serait dans les deux jeux).

Seules les découpes dont la vérité vient du professeur (`teacher_ocr` ou `audit`) sont exportées ;
jamais une lecture de moteur : entraîner sur des sorties de moteurs rendrait leurs erreurs corrélées.

Usage (depuis la racine) :
    backend/.venv/bin/python backend/scripts/ocr_export_dataset.py --out data/ocr_dataset [--class num_line]
        [--eval-share 0.1] [--min-per-class 1] [--synthetic-bench]
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

if "__file__" in globals():
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

NOTICE = """Jeu de données issu de Kiro & Nina : écritures d'élèves et vérité du professeur.
Données personnelles de mineurs : garder en local, ne pas téléverser, ne pas partager.
metadata.jsonl : image, text (vérité, forme canonique), zone_class, group (élève haché), split, truth_source.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="../data/ocr_dataset")
    ap.add_argument("--class", dest="zone_class"); ap.add_argument("--eval-share", type=float, default=0.1)
    ap.add_argument("--min-per-class", type=int, default=1)
    ap.add_argument("--synthetic-bench", action="store_true",
                    help="exporte le banc synthétique du lot 0 (data/ocr_bench, polices) : pour tester l'entraînement")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(8)
    rows = []

    def split_of(group: str) -> str:
        return "eval" if int(hashlib.sha256((group + salt).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < a.eval_share else "train"

    if a.synthetic_bench:
        man = json.loads((Path("..") / "data" / "ocr_bench" / "manifest.json").read_text())
        for i, m in enumerate(x for x in man if x["src"] == "synth"):
            if a.zone_class and m["zone_class"] != a.zone_class:
                continue
            from app.services.ocr_surface import to_surface
            name = f"images/b{i:04d}.png"
            shutil.copyfile(Path("..") / m["path"] if not m["path"].startswith("/") else m["path"], out / name)
            g = f"synthetic-{i % 20}"
            rows.append({"image": name, "text": to_surface(m["truth"], m["zone_class"]), "zone_class": m["zone_class"],
                         "group": g, "split": split_of(g), "truth_source": "synthetic"})
    else:
        from sqlalchemy import select
        from app.config import settings
        from app.db import SessionLocal
        from app.models import Copy, CopyItem, OcrCrop, OcrDecision, ResponseZone
        db = SessionLocal()
        try:
            q = (select(OcrCrop.image_path, OcrCrop.image_sha256, OcrCrop.zone_class, OcrDecision.truth_surface,
                        OcrDecision.truth_source, Copy.student_id)
                 .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
                 .join(ResponseZone, ResponseZone.id == OcrCrop.zone_id).join(CopyItem, CopyItem.id == ResponseZone.item_id)
                 .join(Copy, Copy.id == CopyItem.copy_id)
                 .where(OcrDecision.truth_surface.isnot(None), OcrDecision.truth_source.in_(("teacher_ocr", "audit")),
                        OcrDecision.truth_surface != ""))
            if a.zone_class:
                q = q.where(OcrCrop.zone_class == a.zone_class)
            for path, sha, zc, truth, src, student in db.execute(q):
                p = settings.data_dir / path
                if not p.is_file():
                    continue
                g = hashlib.sha256((student + salt).encode()).hexdigest()[:12]
                name = f"images/{hashlib.sha256((sha + salt).encode()).hexdigest()[:16]}.png"
                shutil.copyfile(p, out / name)
                rows.append({"image": name, "text": truth, "zone_class": zc, "group": g, "split": split_of(g),
                             "truth_source": src})
        finally:
            db.close()
    counts: dict = {}
    for r in rows:
        counts[r["zone_class"]] = counts.get(r["zone_class"], 0) + 1
    short = [c for c, n in counts.items() if n < a.min_per_class]
    (out / "metadata.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""))
    (out / "README.txt").write_text(NOTICE)
    print(f"{len(rows)} paires exportées dans {out} : {counts} ; apprentissage {sum(r['split']=='train' for r in rows)}, "
          f"évaluation {sum(r['split']=='eval' for r in rows)}, élèves distincts {len({r['group'] for r in rows})}")
    if short:
        print("classes sous le minimum demandé :", short)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Pré-rendu des figures TikZ dans la réserve livrée avec le code (app/data/tikz).

Le NAS n'a pas TeX : il lit chaque figure TikZ dans cette réserve. Les figures s'y
rangent seules quand elles sont rendues sur le Mac (forge, aperçus, publication).
Ce script rattrape les manques, en particulier après un changement de la charte
(assets/tikz/kn-figures.tex), qui change la clé de toutes les figures.

Sources parcourues : la publication (exercises.json du dépôt et du volume), les
brouillons et la banque de la base locale, les étalons de forge.

Il rafraîchit aussi l'image des brouillons dont la figure est en TikZ (figure_path,
copiée à la publication), qui sinon garderait l'ancienne charte.

Usage (depuis la racine, sur le Mac) :
    backend/.venv/bin/python backend/scripts/tikz_prerender.py            # rend ce qui manque
    backend/.venv/bin/python backend/scripts/tikz_prerender.py --check    # liste les manques, ne rend rien
    backend/.venv/bin/python backend/scripts/tikz_prerender.py --prune    # retire aussi les images orphelines
"""
import argparse
import json
import os
import sys
from pathlib import Path

if "__file__" in globals():
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

from app.services import tikzfig  # noqa: E402


def _json_sources():
    from app.config import settings
    root = Path(__file__).resolve().parents[2]
    for path in (tikzfig._APP_DIR / "data" / "indigo" / "exercises.json",
                 settings.data_dir / "indigo" / "published" / "exercises.json",
                 *sorted((root / "agents" / "forge" / "contexte" / "exemples").glob("*.json"))):
        if path.is_file():
            yield str(path), json.loads(path.read_text(encoding="utf-8"))


def _db_sources():
    try:
        from app.db import SessionLocal
        from app.models import GeneratedExercise, IndigoExercise
    except Exception as exc:  # noqa: BLE001
        print(f"base ignorée : {exc}")
        return
    db = SessionLocal()
    try:
        for row in db.query(IndigoExercise).all():
            yield f"brouillon {row.id}", [row.raw_ocr_json, row.expected_json, row.grading_json]
        for row in db.query(GeneratedExercise).all():
            yield f"banque {row.id}", [row.figure_json, row.expected_json]
    finally:
        db.close()


def collect() -> dict[str, str]:
    """corps TikZ -> première source qui l'utilise"""
    bodies: dict[str, str] = {}
    for name, data in [*_json_sources(), *_db_sources()]:
        for body in tikzfig.iter_bodies(data):
            bodies.setdefault(body, name)
    return bodies


def refresh_drafts() -> int:
    """Image des brouillons forge en TikZ, recompilée si la charte a changé."""
    from datetime import datetime
    from app.db import SessionLocal
    from app.models import IndigoExercise
    from app.services import indigo
    db = SessionLocal()
    updated = failed = 0
    try:
        for row in db.query(IndigoExercise).all():
            spec = (row.raw_ocr_json or {}).get("figure_spec") or {}
            if spec.get("kind") != "tikz" or not row.figure_path:
                continue
            try:
                png = tikzfig.render(spec.get("spec") or {})
            except tikzfig.FigureSpecError as exc:
                failed += 1
                print(f"  ÉCHEC brouillon {row.id} : {exc}")
                continue
            path = indigo.crop_abs_path(row.figure_path)
            if not path.exists() or path.read_bytes() != png:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(png)
                row.updated_at = datetime.utcnow()
                updated += 1
        db.commit()
    finally:
        db.close()
    print(f"{updated} image(s) de brouillon rafraîchie(s)")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="liste les manques sans rendre")
    ap.add_argument("--prune", action="store_true", help="retire les images que plus rien n'utilise")
    args = ap.parse_args()

    bodies = collect()
    missing = {b: src for b, src in bodies.items() if not tikzfig.store_path(b).exists()}
    print(f"{len(bodies)} figure(s) TikZ, {len(missing)} absente(s) de la réserve "
          f"(charte {tikzfig.CHARTE_VERSION})")
    failed = 0
    if missing and args.check:
        for body, src in missing.items():
            print(f"  manque : {src}")
    elif missing:
        if tikzfig.tectonic_binary() is None:
            print("Tectonic introuvable : à lancer sur le Mac (brew install tectonic)")
            return 1
        for body, src in missing.items():
            try:
                tikzfig.render({"tikz": body})
                print(f"  rendue : {src}")
            except tikzfig.FigureSpecError as exc:
                failed += 1
                print(f"  ÉCHEC {src} : {exc}")
    if not args.check:
        failed += refresh_drafts()
    if args.prune:
        keep = {tikzfig.store_path(b).name for b in bodies}
        removed = [p for p in tikzfig.STORE_DIR.glob("*.png") if p.name not in keep]
        for p in removed:
            p.unlink()
        print(f"{len(removed)} image(s) orpheline(s) retirée(s)")
    return 1 if failed or (args.check and missing) else 0


if __name__ == "__main__":
    sys.exit(main())

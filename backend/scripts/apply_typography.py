"""Applique UNE FOIS la typographie française aux exercices Indigo déjà publiés.

Depuis la racine : backend/.venv/bin/python backend/scripts/apply_typography.py [--apply]

Les nouvelles publications passent par `indigo._published_record`, qui applique
déjà `services.typography` : ce script ne sert qu'aux exercices publiés AVANT
cette règle. Sans --apply : bilan et exemples seulement. Avec --apply : mise à
jour en place du dépôt (backend/app/data/indigo) et de la publication locale du
volume, avec sauvegarde. Identifiants, réponses, barèmes, figures : inchangés.
Idempotent — le relancer ne modifie plus rien. La banque suit au prochain
démarrage (seed_published).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _show(s: str) -> str:
    return s.replace(" ", "⍽").replace(" ", "·")


def _changed_texts(old, new, out: list) -> None:
    if isinstance(old, dict):
        for k in old:
            _changed_texts(old[k], new.get(k), out)
    elif isinstance(old, list):
        for a, b in zip(old, new):
            _changed_texts(a, b, out)
    elif isinstance(old, str) and old != new:
        out.append((old, new))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=BACKEND.parent / "tmp/typography")
    args = parser.parse_args()
    os.chdir(BACKEND)
    from app.services import indigo, typography

    backup = args.output_dir.resolve() / "backup"
    stamp = indigo._publish_stamp()
    report = {}
    for base in (indigo._IMAGE_PUB_DIR, indigo._volume_pub_dir()):
        path = base / "exercises.json"
        if not path.exists():
            continue
        name = "repo" if base == indigo._IMAGE_PUB_DIR else "volume"
        data = json.loads(path.read_text(encoding="utf-8"))
        changes: list = []
        records = []
        for rec in data.get("exercises", []):
            new = typography.apply_to_record(rec)
            _changed_texts(rec, new, changes)
            records.append(new)
        report[name] = {"exercises": len(records), "texts_changed": len(changes)}
        for old, new in changes[:12]:
            print(f"[{name}] {old[:90]!r}\n      -> {_show(new)[:90]}")
        if args.apply and changes:
            backup.mkdir(parents=True, exist_ok=True)
            dest = backup / f"{name}-exercises.json"
            if not dest.exists():
                shutil.copyfile(path, dest)
            data["exercises"] = records
            data["generated_at"] = stamp
            if name == "volume":
                # aligné sur le dépôt réécrit ci-dessus (cf. indigo._read_dir)
                data["base_image"] = stamp
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"apply": args.apply, **report}, ensure_ascii=False))


if __name__ == "__main__":
    main()

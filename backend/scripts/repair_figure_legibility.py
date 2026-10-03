"""Recompose et régénère les figures d'un chapitre Indigo déjà publié.

Depuis la racine : backend/.venv/bin/python backend/scripts/repair_figure_legibility.py
    --grade 6e --chapter B2 [--apply]

Sans --apply : images de contrôle seulement. Avec --apply : mise à jour ciblée
des brouillons, de la banque et des deux publications locales, avec sauvegarde.
Les questions, réponses, barèmes et identifiants sont conservés.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import figkit, figures  # noqa: E402


def reflow_candidates(spec: dict, *, columns: int | None = None) -> dict:
    """Déplace les candidats numérotés en deux colonnes, par translations.

    Les préfixes 0_, 1_, … et les textes 1, 2, … identifient les planches
    Astra. Toute autre figure garde ses coordonnées. Une seconde passe est
    idempotente. Ni angles, ni distances, ni numérotation ne sont modifiés.
    """
    spec = copy.deepcopy(spec)
    if spec.get("axes"):
        spec["width_mm"] = figkit.readable_width(spec, axes=spec["axes"])
        return spec
    points = spec.get("points") or {}
    groups = {}
    for name, point in points.items():
        prefix, sep, _ = name.partition("_")
        if not sep or not prefix.isdigit():
            return spec
        groups.setdefault(int(prefix), []).append(name)
    numbers = sorted(groups)
    texts = spec.get("texts") or []
    if len(numbers) < 3 or numbers != list(range(len(numbers))) or len(texts) != len(numbers):
        return spec
    numbered = {str(t.get("text")): t for t in texts}
    if set(numbered) != {str(i+1) for i in numbers}:
        return spec
    # Ne pas déplacer un tracé liant des panneaux distincts.
    def references(value):
        if isinstance(value, str) and value in points:
            return {int(value.split("_", 1)[0])}
        if isinstance(value, list):
            return set().union(*(references(v) for v in value))
        if isinstance(value, dict):
            return set().union(*(references(v) for k, v in value.items()
                                 if k in {"vertices", "from", "to", "through", "at",
                                          "center", "seg", "segs"}))
        return set()
    for key in ("segments", "lines", "rays", "polygons", "lengths", "equal_marks", "parallel_marks"):
        if any(len(references(item)) > 1 for item in spec.get(key, [])):
            return spec
    if spec.get("functions"):
        return spec
    boxes = {}
    for number, names in groups.items():
        coords = [points[n]["xy"] if isinstance(points[n], dict) else points[n] for n in names]
        for circle in spec.get("circles", []):
            if circle.get("center") in names and "radius" in circle:
                p = points[circle["center"]]
                x, y = p["xy"] if isinstance(p, dict) else p
                radius = circle["radius"]
                coords += [[x-radius, y-radius], [x+radius, y+radius]]
        boxes[number] = (min(p[0] for p in coords), max(p[0] for p in coords),
                         min(p[1] for p in coords), max(p[1] for p in coords))
    cell_w = max(b[1]-b[0] for b in boxes.values())
    cell_h = max(b[3]-b[2] for b in boxes.values())
    gap_x = max(cell_w * 0.55, 1.5)
    gap_y = max(cell_h * 0.85, 2.0)
    columns = columns or (3 if len(numbers) >= 6 else 2)
    for number, names in groups.items():
        x0, x1, y0, y1 = boxes[number]
        col, row = number % columns, number // columns
        dx = col * (cell_w + gap_x) + (cell_w-(x1-x0))/2 - x0
        dy = -row * (cell_h + gap_y) + (cell_h-(y1-y0))/2 - y0
        for name in names:
            point = points[name]
            xy = point["xy"] if isinstance(point, dict) else point
            shifted = [round(xy[0]+dx, 12), round(xy[1]+dy, 12)]
            if isinstance(point, dict):
                point["xy"] = shifted
            else:
                points[name] = shifted
        numbered[str(number+1)]["at"] = [col*(cell_w+gap_x)+cell_w/2,
                                          -row*(cell_h+gap_y)-gap_y*0.55]
    spec["width_mm"] = 93
    return spec


def repair_nested(value, *, columns: int | None = None) -> int:
    """Répare aussi les figures des sous-questions, à toute profondeur."""
    count = 0
    if isinstance(value, dict):
        if value.get("type") == "geo" and isinstance(value.get("params"), dict):
            value["params"] = reflow_candidates(value["params"], columns=columns)
            return 1
        if value.get("kind") == "geo" and isinstance(value.get("spec"), dict):
            value["spec"] = reflow_candidates(value["spec"], columns=columns)
            return 1
        for child in value.values():
            count += repair_nested(child, columns=columns)
    elif isinstance(value, list):
        for child in value:
            count += repair_nested(child, columns=columns)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grade", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=BACKEND.parent / "tmp/figure-legibility")
    args = parser.parse_args()
    os.chdir(BACKEND)
    from app.config import settings
    from app.db import SessionLocal, engine
    from app.models import Competency, GeneratedExercise, IndigoExercise
    from app.services import indigo, generation, pdfgen
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    backup = out / "backup"
    if args.apply:
        backup.mkdir(exist_ok=True)
        if engine.url.get_backend_name() == "sqlite" and not (backup / "mathprint.db").exists():
            with sqlite3.connect(engine.url.database) as source, sqlite3.connect(backup / "mathprint.db") as dest:
                source.backup(dest)
    with SessionLocal() as db:
        rows = db.query(IndigoExercise).join(Competency).filter(
            IndigoExercise.grade_level == args.grade,
            Competency.code.startswith(args.chapter + ".")).all()
        repaired, runs, render_count, pending_images = {}, set(), 0, []
        for row in rows:
            raw = copy.deepcopy(row.raw_ocr_json or {})
            payload = copy.deepcopy(row.payload_json or {})
            expected = copy.deepcopy(row.expected_json or {})
            grading = copy.deepcopy(row.grading_json or {})
            count = repair_nested(raw) + repair_nested(grading)
            if not count:
                continue
            repair_nested(payload)
            repair_nested(expected)
            root = raw.get("figure_spec")
            # Le n°23 porte sur les noms d'angles : les valeurs de coordonnées
            # n'interviennent dans aucune question. Garder une grande grille
            # sans ces nombres libère les noms des sommets et de la place.
            if args.grade == "6e" and args.chapter == "B2" and row.source_number == "23":
                for axis in ("x", "y"):
                    root["spec"]["axes"][axis]["tick_labels"] = False
                root["spec"]["width_mm"] = 75
            def print_height():
                fig = ({"type": "geo", "params": root["spec"]}
                       if root and root.get("kind") == "geo" else
                       {"type": "image", "params": {"path": str(indigo.crop_abs_path(row.figure_path))}}
                       if row.has_figure and row.figure_path else None)
                simulated = SimpleNamespace(
                    statement=row.statement, correction=row.correction_guide, difficulty_level=row.difficulty,
                    response_type=row.response_type, expected_json=expected, grading_json=grading,
                    source="indigo", kind="application", figure_json=fig,
                    raw_extract_json={"indigo": {"badge_type": row.badge_type, "title": row.title,
                                                  "calculator": row.calculator}})
                return pdfgen.estimate_item_height(generation.render_shape(simulated), 9, 9,
                                                   pdfgen.DEFAULT_TEMPLATES["exercise"])
            # Deux grandes planches dans une carte longue : trois colonnes
            # gardent la police et tous les candidats, en économisant la hauteur.
            compact = print_height() > pdfgen.column_capacity(2)
            if compact:
                for value in (raw, payload, expected, grading):
                    repair_nested(value, columns=3)
            if print_height() > pdfgen.column_capacity(2) + 0.1:
                raise ValueError(f"Exercice {row.source_number} {row.variant_kind} encore trop haut")
            figure_json = None
            if root and root.get("kind") == "geo" and row.has_figure and not row.figure_box_json:
                figure_json = {"type": "geo", "params": root["spec"]}
                png = figures.render_figure(figure_json)
                (out / f"{row.source_number}-{row.variant_kind}.png").write_bytes(png)
                render_count += 1
                if args.apply and row.figure_path:
                    dest = indigo.crop_abs_path(row.figure_path)
                    pending_images.append((dest, png))
            for i, part in enumerate(grading.get("parts") or []):
                fig = part.get("figure")
                if fig and fig.get("type") == "geo":
                    (out / f"{row.source_number}-{row.variant_kind}-q{i}.png").write_bytes(figures.render_figure(fig))
                    render_count += 1
            repaired[row.id] = {"raw": raw, "payload": payload, "expected": expected,
                                "grading": grading, "figure_json": figure_json,
                                "number": row.source_number, "variant": row.variant_kind,
                                "compact": compact}
            if raw.get("run"):
                runs.add(raw["run"])
            if args.apply:
                row.raw_ocr_json, row.payload_json = raw, payload
                row.expected_json, row.grading_json = expected, grading
                bank = db.get(GeneratedExercise, row.id)
                if bank:
                    bank.expected_json, bank.grading_json = expected, grading
                    if figure_json:
                        bank.figure_json = figure_json
        if args.apply:
            for dest, png in pending_images:
                if dest.exists() and not (backup / dest.name).exists():
                    shutil.copyfile(dest, backup / dest.name)
                dest.write_bytes(png)
            # Mise à jour en place, sans republier/supprimer les autres chapitres.
            stamp = indigo._publish_stamp()
            for base in (indigo._IMAGE_PUB_DIR, indigo._volume_pub_dir()):
                path = base / "exercises.json"
                if not path.exists():
                    continue
                data = json.loads(path.read_text())
                backup_path = backup / ("repo-exercises.json" if base == indigo._IMAGE_PUB_DIR else "volume-exercises.json")
                if not backup_path.exists():
                    shutil.copyfile(path, backup_path)
                for rec in data.get("exercises", []):
                    fixed = repaired.get(rec.get("id"))
                    if not fixed:
                        continue
                    rec["expected"], rec["grading"] = fixed["expected"], fixed["grading"]
                    if fixed["figure_json"]:
                        rec["figure_json"] = fixed["figure_json"]
                        if rec.get("figure_file"):
                            (base / "figures" / rec["figure_file"]).write_bytes(figures.render_figure(fixed["figure_json"]))
                data["generated_at"] = stamp
                if base == indigo._volume_pub_dir():
                    data["base_image"] = stamp
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            # Les specs du run restent la source éditable des images réparées.
            for run_id in runs:
                path = settings.data_dir / "astra/runs" / run_id / "astra_output.json"
                if path.exists():
                    data = json.loads(path.read_text())
                    choices = {(r["number"], r["variant"]): r for r in repaired.values()}
                    for ex in data.get("exercises", []):
                        for kind, variant in ex.get("variants", {}).items():
                            fixed = choices.get((str(ex.get("source_number")), kind))
                            if fixed:
                                repair_nested(variant, columns=3 if fixed["compact"] else None)
                                if fixed["figure_json"] and variant.get("figure", {}).get("kind") == "geo":
                                    variant["figure"]["spec"] = fixed["figure_json"]["params"]
                    if not (backup / f"{run_id}.json").exists():
                        shutil.copyfile(path, backup / f"{run_id}.json")
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            db.commit()
        print(json.dumps({"apply": args.apply, "exercises": len(repaired), "figures": render_count,
                          "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

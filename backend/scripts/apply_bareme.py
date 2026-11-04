#!/usr/bin/env python3
"""Applique la règle de barème de la forge aux exercices Indigo déjà publiés.

Un sujet qui mêle les deux banques ne doit pas compter deux échelles : un facile
Indigo pèserait deux fois un facile forge. Seul le barème des FUTURES copies change :
une copie imprimée garde le sien, figé dans son instantané. Le contenu n'est jamais
touché.

    python scripts/apply_bareme.py            # aperçu, n'écrit rien
    python scripts/apply_bareme.py --apply    # écrit le fichier publié et la banque
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models import GeneratedExercise  # noqa: E402
from app.services import scoring  # noqa: E402


def rebareme(grading: dict, response_type: str, level: int, problem: bool) -> dict:
    """Copie de `grading` au barème de la règle commune."""
    out = dict(grading or {})
    if response_type == "composite":
        parts = out.get("parts") or []
        values = [scoring.forge_bareme(p.get("response_type"), level)
                  for p in parts if p.get("response_type") in scoring.FORGE_BAREME]
        if len(values) != len(parts) or not parts:
            return out
        total = (scoring.forge_bareme("", level, problem=True) if problem
                 else min(scoring.BAREME_MAX, max(values)))
        shares = scoring.split_bareme(total, values)
        out["parts"] = [{**p, "grading": {**(p.get("grading") or {}), "bareme_points": s}}
                        for p, s in zip(parts, shares)]
        out["bareme_points"] = total
        return out
    if response_type in scoring.FORGE_BAREME:
        out["bareme_points"] = scoring.forge_bareme(response_type, level, problem=problem)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        rows = db.query(GeneratedExercise).filter_by(source="indigo").all()
        changed = 0
        for row in rows:
            new = rebareme(row.grading_json or {}, row.response_type, row.difficulty_level,
                           row.kind == "probleme")
            if new.get("bareme_points") != (row.grading_json or {}).get("bareme_points"):
                changed += 1
                if args.apply:
                    row.grading_json = new
                    if row.response_type == "composite":
                        row.expected_json = {**(row.expected_json or {}), "parts": new.get("parts")}
        print(f"{changed} exercice(s) sur {len(rows)} changent de barème.")
        if args.apply:
            db.commit()
            from app.services import indigo
            print(f"Banque mise à jour. Pour que le changement survive au redémarrage, "
                  f"republie aussi : {indigo._pub_paths()[3]}")
        else:
            print("Aperçu seulement : relance avec --apply pour écrire.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

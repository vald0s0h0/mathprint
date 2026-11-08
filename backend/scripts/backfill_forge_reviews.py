"""Rattache à chaque carte l'archive d'une reprise antérieure (idempotent).

Depuis la racine : backend/.venv/bin/python backend/scripts/backfill_forge_reviews.py ARCHIVE
L'archive contient avis-initiaux.json et bilan.json. N'altère aucun énoncé ni avis courant.
"""
import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
# La configuration .env et SQLite sont relatives à backend, comme run.py.
os.chdir(BACKEND)
from app.db import SessionLocal
from app.models import IndigoExercise
from app.services import exercise_review as review


def backfill(db, folder: Path) -> int:
    initial = json.loads((folder / 'avis-initiaux.json').read_text())
    report = json.loads((folder / 'bilan.json').read_text())
    done = set(report['corrected_refs'])
    pending = set(report['pending'])
    rows = {r.source_number: r for r in db.query(IndigoExercise).all()}
    count = 0
    for old in initial.values():
        ref = old['ref']
        row = rows.get(ref)
        if not row or ref not in done | pending:
            continue
        archive_id = f'{folder.name}/{ref}'
        if any(e.get('archive_id') == archive_id for e in (row.raw_ocr_json or {}).get('review_history', [])):
            continue
        raw = old.get('raw') or {}
        previous = {'comment': raw.get('review_comment', ''), 'verdict': raw.get('review_verdict'),
                    'notes': raw.get('review_notes', []), 'goldfish': (raw.get('quality') or {}).get('goldfish')}
        reason = None
        if ref in pending:
            reason = ('Le moteur ne permettait pas de verrouiller l’échelle 1 de la figure.' if '/s17' in ref
                      else 'Le moteur imposait un tableau pleine largeur ; largeur naturelle et centrage non disponibles.')
        item = review.event('ok' if ref in done else 'failed',
                            'Exercice repris : contrôles et relecture Goldfish terminés' if ref in done else 'Reprise bloquée par le moteur',
                            previous=previous, reason=reason, archive_id=archive_id,
                            goldfish=(row.raw_ocr_json or {}).get('quality', {}).get('goldfish') if ref in done else None)
        review.append(row, item)
        count += 1
    db.commit()
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    arg = parser.parse_args().archive
    folder = (Path(__file__).resolve().parents[2] / arg).resolve() if not arg.is_absolute() else arg
    os.chdir(BACKEND)
    with SessionLocal() as db:
        print(f'{backfill(db, folder)} historique(s) rattaché(s).')

"""Rejoue la fusion LOCALE sur les lectures déjà stockées (aucun moteur relancé, aucun appel Mathpix).

Compare, par classe de zone, ce que la cascade avait décidé à ce qu'elle déciderait avec d'autres
seuils, une autre marge de score ou un autre plan de moteurs : décisions automatiques gagnées ou
perdues, erreurs automatiques avant/après (sur la vérité du professeur), renvois vers Mathpix.

Usage (depuis la racine) :
    backend/.venv/bin/python backend/scripts/ocr_replay.py [--thresholds '{"unimumer":{"min_logprob_mean":-0.3}}']
                                                           [--margin 3.0] [--plan plan.json]
                                                           [--since 30d] [--mode shadow|cascade] [--class num_line]
    --engine X : prépare des tâches de lecture pour le moteur X sur les découpes qu'il n'a jamais lues.
                 SANS EFFET par défaut (liste seulement) ; --yes écrit les tâches. À manier avec soin sur le NAS.
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
    sys.path.insert(0, str(BACKEND / "scripts"))

from ocr_report import parse_since   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--thresholds"); ap.add_argument("--margin", type=float); ap.add_argument("--plan")
    ap.add_argument("--since"); ap.add_argument("--mode", choices=("shadow", "cascade"))
    ap.add_argument("--class", dest="zone_class"); ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--engine"); ap.add_argument("--yes", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    from app.db import SessionLocal
    from app.models import OcrCrop
    from app.services import ocr_analysis, ocr_queue, runtime_settings as rs
    db = SessionLocal()
    try:
        since = parse_since(a.since)
        if a.engine:
            ids = ocr_analysis.missing_jobs(db, a.engine, since=since, mode=a.mode, zone_class=a.zone_class, limit=a.limit)
            print(f"{len(ids)} découpe(s) jamais lue(s) par {a.engine}")
            if not a.yes:
                print("aucune tâche créée (ajoutez --yes). Elles seraient lues par le worker au prochain passage.")
                return 0
            for cid in ids:
                crop = db.get(OcrCrop, cid)
                ocr_queue.enqueue_jobs(db, crop, [a.engine])
            db.commit()
            print(f"{len(ids)} tâche(s) créée(s)")
            return 0
        plan = json.loads(Path(a.plan).read_text()) if a.plan else None
        rep = ocr_analysis.replay_local(db, plan=plan, thresholds=json.loads(a.thresholds) if a.thresholds else None,
                                        margin=a.margin, since=since, mode=a.mode, zone_class=a.zone_class, limit=a.limit)
    finally:
        db.close()
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print("RÉGLAGES REJOUÉS :", rep["settings"])
    for zc, s in rep["by_class"].items():
        print(f"{zc:15s} découpes {s['crops']:6d} · auto avant {s['old_auto']} -> après {s['new_auto']}"
              f" (perdues {s['auto_lost']}, gagnées {s['auto_gained']}) · erreurs auto avant {s['old_auto_errors']}"
              f" -> après {s['new_auto_errors']}/{s['new_auto_known']} vérifiées · Mathpix {s['new_to_mathpix']}"
              f" · manuel {s['new_manual']} · lectures manquantes {s['new_incomplete']}")
    for g in rep["gained_but_wrong"][:20]:
        print("  GAGNÉE MAIS FAUSSE :", g)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

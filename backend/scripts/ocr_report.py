"""Rapport de diagnostic de la lecture OCR multicouche (LECTURE SEULE).

Requêtes SQL agrégées : sur le NAS, à lancer hors des heures de correction.

Usage (depuis la racine) :
    backend/.venv/bin/python backend/scripts/ocr_report.py [--since 7d|2026-11-01] [--mode shadow|cascade]
                                                          [--class num_line|math_line|text_line|multiline_line] [--json]
Sur le NAS :
    sudo docker exec -i mathprint-api-1 python - --json < backend/scripts/ocr_report.py
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if "__file__" in globals():
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))


def parse_since(value: str | None):
    if not value:
        return None
    m = re.fullmatch(r"(\d+)([dh])", value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.now(timezone.utc).replace(tzinfo=None) - (timedelta(days=n) if unit == "d" else timedelta(hours=n))
    return datetime.fromisoformat(value)


def pct(x):
    return "n/a" if x is None else f"{100 * x:.2f} %"


def render(rep: dict) -> str:
    L = []
    ae = rep["auto_errors"]
    L.append(f"ERREURS AUTOMATIQUES : {ae['count']}")
    for e in ae["items"][:20]:
        L.append(f"  crop {e['crop_id']} [{e['zone_class']}] {e['decision']} : accepté {e['accepted_surface']!r}"
                 f" / professeur {e['truth_surface']!r} ({e['truth_source']}, {e['mode']})")
    L.append("")
    L.append("PASSAGE EN CASCADE (par classe) — " + rep["go_no_go"]["criterion"])
    for zc, g in rep["go_no_go"]["by_class"].items():
        a = g["audited"]
        L.append(f"  {zc:15s} décisions {g['decisions']:6d} · auto {pct(g['automatic_share'])} · manuel {pct(g['manual_share'])}"
                 f" · audits {a['n']} (erreurs {a['k']}, borne haute {pct(a['high'])}) · "
                 + ("GO" if g["go"] else f"NO-GO (encore {g['audits_missing']} audits)"))
    v = rep["volume"]
    L.append("")
    L.append(f"VOLUME : {v['total']} découpes ; répartition {v['mix']}")
    L.append("RAISONS DE RENVOI (les plus fréquentes) :")
    for r in rep["reasons"][:10]:
        L.append(f"  {r['count']:6d}  {r['decision']:18s} {r['reason']}")
    L.append("")
    L.append("MOTEURS")
    for zc, engines in rep["engines"].items():
        for eng, e in engines.items():
            acc = e["accuracy"]
            L.append(f"  {zc:15s} {eng:13s} lectures {e['reads']:6d} · illisible {pct(e['illegible_rate'])}"
                     f" · exactitude {pct(acc['rate'])} (n={acc['n']}) · p50 {e['latency_p50_ms']} ms · p95 {e['latency_p95_ms']} ms")
    L.append("")
    L.append("PAIRES (accords à tort = les deux d'accord sur une surface fausse)")
    for p in rep["pairs"]:
        w = p["wrong_agree_rate"]
        L.append(f"  {p['class']:15s} {p['a']:13s} × {p['b']:13s} accord {pct(p['agree_rate'])} · à tort {w['k']}/{w['n']}")
    m = rep["marginal"]
    L.append("")
    L.append(f"APPORT MARGINAL (échantillon complet, {m['crops']} découpes)")
    for eng, e in m["engines"].items():
        L.append(f"  {eng:13s} décisions qui changeraient : {e['decisions_changed']} ({pct(e['share_changed'])}), "
                 f"automatiques perdues : {e['automatic_decisions_lost']}")
    mp = rep["mathpix"]
    L.append("")
    L.append(f"MATHPIX : {mp['cost_eur']} € · {mp['crops_read_by_mathpix']} découpes · {mp['copies']} copies"
             f" · {mp['cost_per_copy_eur']} €/copie · via planche : {mp['crops_via_montage']}")
    sh = rep["shadow"]
    if sh["compared_items"]:
        L.append(f"OMBRE : local = Mathpix sur {sh['agree']}/{sh['compared_items']} éléments ({pct(sh['agreement']['rate'])})")
    w = rep["waiting"]
    if w["waiting"]:
        L.append(f"EN ATTENTE DU MAC : {w['waiting']} lot(s), le plus ancien depuis {w['oldest_hours']} h"
                 f" ({w['over_timeout']} au-delà de {w['timeout_hours']} h)")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since"); ap.add_argument("--mode", choices=("shadow", "cascade"))
    ap.add_argument("--class", dest="zone_class"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(sys.argv[1:] if "__file__" in globals() else [x for x in sys.argv[1:]])
    from app.db import SessionLocal
    from app.services import ocr_analysis
    db = SessionLocal()
    try:
        rep = ocr_analysis.build_report(db, since=parse_since(a.since), mode=a.mode, zone_class=a.zone_class)
    finally:
        db.close()
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str) if a.json else render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

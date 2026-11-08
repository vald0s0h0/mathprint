"""Calibrage du seuil de la géométrie à tracer sur de VRAIES copies.

Relit chaque zone « tracé » déjà scannée avec la lecture ACTUELLE
(services.construction.read) et la compare à la note du professeur : sa
décision la plus récente (`source = teacher`), qu'il ait validé la proposition
ou changé la note. Ces décisions viennent des tracés partis en correction
manuelle (confiance sous le seuil) et du contrôle par échantillon (réglage
« Contrôle par échantillon », Paramètres → Pédagogie), qui envoie au professeur
une part des tracés notés automatiquement : sans lui, on ne saurait rien des
lectures au-dessus du seuil.

Pour chaque seuil : combien de tracés auraient été notés automatiquement, et
combien l'auraient été à tort (note de la lecture ≠ note du professeur). Le
seuil recommandé est le plus petit sans aucune erreur, sur au moins `--min`
tracés notés par le professeur au-dessus de lui.

Usage :
- local (depuis backend/) :
    .venv/bin/python scripts/calibrate_construction.py [<assessment_id ou préfixe>…]
        [--details] [--min 30] [--tolerance 0.01] [--apply] [--audit 0.05]
- sur le NAS, sans rien installer dans l'image (une page en mémoire à la fois) :
    sudo docker exec -i mathprint-api-1 python - [args…] < backend/scripts/calibrate_construction.py

`--apply` écrit le seuil recommandé dans les réglages (rien n'est écrit sans
lui) ; `--audit 0.05` règle en même temps la part contrôlée par échantillon.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if "__file__" in globals():                       # lancé localement : backend/ importable
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

import cv2                                        # noqa: E402

from app.config import settings                   # noqa: E402
from app.db import SessionLocal                   # noqa: E402
from app.models import (                          # noqa: E402
    Assessment, Copy, CopyItem, GradingDecision, ResponseZone, StudentResponse, SystemSetting,
)
from app.services import construction             # noqa: E402
from app.services.runtime_settings import (       # noqa: E402
    construction_audit_share, construction_confidence_threshold,
)

GRID = [round(0.5 + 0.05 * i, 2) for i in range(11)]       # 0,50 à 1,00


def _option(argv: list[str], name: str, default: float) -> float:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return float(argv[i + 1].replace(",", "."))
    return default


def _teacher_ratio(db, response_id: str) -> float | None:
    """Part des points donnée par le professeur (None : pas encore corrigé, ou
    question annulée)."""
    decision = (db.query(GradingDecision)
                .filter(GradingDecision.response_id == response_id, GradingDecision.source == "teacher")
                .order_by(GradingDecision.created_at.desc()).first())
    if decision is None or not decision.max_score:
        return None
    return max(0.0, min(1.0, float(decision.score) / float(decision.max_score)))


def collect(db, wanted: list[str]) -> list[dict]:
    """Une ligne par tracé scanné : lecture actuelle, note du professeur."""
    rows = (db.query(ResponseZone, CopyItem, Copy)
            .join(CopyItem, CopyItem.id == ResponseZone.item_id)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .filter(CopyItem.response_type == "manual_drawing")
            .order_by(ResponseZone.page_id).all())
    if wanted:
        rows = [r for r in rows if any(r[2].assessment_id.startswith(w) for w in wanted)]
    out = []
    page_id, page = None, None
    for zone, item, copy in rows:
        if not (zone.meta_json or {}).get("construction"):
            continue
        response = db.query(StudentResponse).filter_by(zone_id=zone.id).first()
        if response is None:
            continue                              # zone jamais scannée
        if zone.page_id != page_id:               # une seule page en mémoire
            page_id = zone.page_id
            path = (settings.data_dir / "assessments" / copy.assessment_id / "scans"
                    / "derived" / f"page-{zone.page_id}.png")
            page = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        if page is None:
            continue
        try:
            reading = construction.read(page, zone.meta_json)
        except Exception as exc:  # noqa: BLE001 - une zone illisible est comptée, pas fatale
            reading = {"status": "error", "confidence": 0.0, "fraction": 0.0, "reasons": [str(exc)[:120]]}
        fraction = float(reading.get("fraction") or 0.0) if reading.get("status") == "read" else 0.0
        out.append({"zone": zone.id, "assessment": copy.assessment_id, "status": reading.get("status"),
                    "confidence": float(reading.get("confidence") or 0.0), "fraction": fraction,
                    "teacher": _teacher_ratio(db, response.id), "reasons": reading.get("reasons") or [],
                    "error_id": reading.get("error_id")})
    return out


def sweep(rows: list[dict], tolerance: float, minimum: int) -> tuple[list[dict], float | None]:
    """Par seuil : notés auto, dont vérifiés par le professeur, dont faux."""
    graded = [r for r in rows if r["teacher"] is not None]
    candidates = sorted(set(GRID) | {round(r["confidence"], 3) for r in graded if r["confidence"] >= 0.5})
    table, best = [], None
    for t in candidates:
        auto = [r for r in rows if r["confidence"] >= t]
        checked = [r for r in auto if r["teacher"] is not None]
        wrong = [r for r in checked if abs(r["fraction"] - r["teacher"]) > tolerance]
        table.append({"threshold": t, "auto": len(auto), "checked": len(checked), "wrong": len(wrong),
                      "share": len(auto) / len(rows) if rows else 0.0})
        if best is None and not wrong and len(checked) >= minimum:
            best = t
    return table, best


def apply(db, threshold: float, samples: int, audit: float | None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = {"construction_confidence_threshold": {"value": threshold, "calibrated_at": now,
                                                    "samples": samples}}
    if audit is not None:
        values["construction_audit_share"] = {"value": audit}
    for key, value in values.items():
        row = db.get(SystemSetting, key) or SystemSetting(key=key)
        row.value_json = value
        row.version = (row.version or 0) + 1
        db.add(row)
    db.commit()


def main(argv: list[str]) -> int:
    details = "--details" in argv
    tolerance = _option(argv, "--tolerance", 0.01)
    minimum = int(_option(argv, "--min", 30))
    audit = _option(argv, "--audit", -1.0)
    flags = {"--tolerance", "--min", "--audit"}
    wanted = [a for i, a in enumerate(argv)
              if not a.startswith("--") and (i == 0 or argv[i - 1] not in flags)]
    db = SessionLocal()
    titles = {a.id: a.title for a in db.query(Assessment).all()}
    rows = collect(db, wanted)
    current = construction_confidence_threshold(db)
    print(f"{len(rows)} tracé(s) scanné(s), {sum(r['teacher'] is not None for r in rows)} noté(s) "
          f"par le professeur ; seuil actuel {current:.2f}, contrôle par échantillon "
          f"{construction_audit_share(db):.0%}.")
    if not rows:
        print("Aucun tracé scanné : imprimez et corrigez d'abord des sujets avec des tracés.")
        db.close()
        return 0
    table, best = sweep(rows, tolerance, minimum)
    print("\nseuil   notés auto   vérifiés   FAUX")
    for line in table:
        if line["threshold"] in GRID or line["threshold"] == best:
            mark = "  <- recommandé" if line["threshold"] == best else ""
            print(f"{line['threshold']:5.3f}   {line['auto']:5d} ({line['share']:4.0%})   "
                  f"{line['checked']:6d}   {line['wrong']:4d}{mark}")
    if best is None:
        print(f"\nPas de seuil recommandé : aucun seuil n'a encore {minimum} tracés vérifiés par le "
              "professeur sans erreur au-dessus de lui. Gardez le contrôle par échantillon et "
              "relancez après d'autres corrections.")
    else:
        print(f"\nSeuil recommandé : {best:.3f} (plus petit seuil sans note automatique fausse sur "
              f"au moins {minimum} tracés vérifiés).")
    wrong = [r for r in rows if r["teacher"] is not None
             and abs(r["fraction"] - r["teacher"]) > tolerance]
    if wrong:
        print("\nDésaccords lecture / professeur (les plus confiants d'abord) :")
        for r in sorted(wrong, key=lambda r: -r["confidence"])[: None if details else 15]:
            print(f"  conf {r['confidence']:.3f}  lu {r['fraction']:.2f}  prof {r['teacher']:.2f}  "
                  f"{r['status']}  {', '.join(r['reasons'])}  zone={r['zone']}  "
                  f"({titles.get(r['assessment'], r['assessment'])})")
    if details:
        print("\nToutes les lectures :")
        for r in rows:
            teacher = "-" if r["teacher"] is None else f"{r['teacher']:.2f}"
            print(f"  conf {r['confidence']:.3f}  lu {r['fraction']:.2f}  prof {teacher}  {r['status']}  "
                  f"zone={r['zone']}")
    if "--apply" in argv:
        if best is None:
            print("\n--apply ignoré : pas de seuil recommandé.")
        else:
            checked = next(line["checked"] for line in table if line["threshold"] == best)
            apply(db, best, checked, audit if audit >= 0 else None)
            print(f"\nSeuil {best:.3f} enregistré" + (f", contrôle {audit:.0%}" if audit >= 0 else "") + ".")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Calibration de la lecture « points à relier » sur de VRAIES copies (lecture seule).

Relit chaque zone « relier » déjà scannée avec le détecteur ACTUEL
(worker_cv.detect_matching) et la compare à la vérité terrain : la saisie du
professeur (`ocr_attempts.provider = 'teacher_ocr'`, la plus récente). Une zone
lue automatiquement à l'époque sans saisie du professeur sert de vérité si la
copie a été validée telle quelle ; elle est comptée à part (« vérité = lecture
auto validée »).

Objectif : 0 lecture automatique fausse ; le reste en revue doit être des cas
réellement ambigus. Rien n'est écrit en base ni sur disque.

Usage :
- local (depuis backend/) :
    .venv/bin/python scripts/calibrate_matching.py [<assessment_id ou préfixe>…]
- sur le NAS, sans rien installer dans l'image (une page en mémoire à la fois) :
    sudo docker exec -i mathprint-api-1 python - [<id>…] < backend/scripts/calibrate_matching.py

Sans argument : toutes les évaluations qui ont des zones « relier » scannées.
`--details` liste chaque zone (motif de la décision, lecture, vérité).
"""
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

if "__file__" in globals():                       # lancé localement : backend/ importable
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

import cv2                                        # noqa: E402

from app.config import settings                   # noqa: E402
from app.db import SessionLocal                   # noqa: E402
from app.models import (                          # noqa: E402
    Assessment, Copy, CopyItem, OcrAttempt, ResponseZone,
)
from app.services import worker_cv                # noqa: E402


def _latest(db, zone_id: str, provider: str):
    return (db.query(OcrAttempt)
            .filter(OcrAttempt.zone_id == zone_id, OcrAttempt.provider == provider)
            .order_by(OcrAttempt.created_at.desc()).first())


def main(argv: list[str]) -> int:
    details = "--details" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    db = SessionLocal()
    rows = (db.query(ResponseZone, CopyItem, Copy)
            .join(CopyItem, CopyItem.id == ResponseZone.item_id)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .filter(CopyItem.response_type == "matching")
            .order_by(ResponseZone.page_id).all())
    if wanted:
        rows = [r for r in rows if any(r[2].assessment_id.startswith(w) for w in wanted)]
    titles = {a.id: a.title for a in db.query(Assessment).all()}

    stats = defaultdict(Counter)
    reasons = defaultdict(Counter)
    lines = []
    page_id, page = None, None
    for zone, item, copy in rows:
        cv_old = _latest(db, zone.id, "cv_local")
        if cv_old is None:
            continue                              # zone jamais lue (pas scannée)
        if zone.page_id != page_id:               # une seule page en mémoire
            page_id = zone.page_id
            path = (settings.data_dir / "assessments" / copy.assessment_id / "scans"
                    / "derived" / f"page-{zone.page_id}.png")
            page = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        if page is None:
            continue
        meta = zone.meta_json or {}
        diag: dict = {}
        pairs, conf = worker_cv.detect_matching(page, meta.get("left_points") or [],
                                                meta.get("right_points") or [], diag=diag)
        teacher = _latest(db, zone.id, "teacher_ocr")
        if teacher is not None and (teacher.raw_json or {}).get("pairs") is not None:
            truth, source = teacher.raw_json["pairs"], "prof"
        elif cv_old.confidence >= 0.9 and (cv_old.raw_json or {}).get("pairs") is not None:
            truth, source = cv_old.raw_json["pairs"], "auto validée"
        else:
            truth, source = None, "inconnue"
        new = "revue"
        if conf >= 0.9:
            if truth is None:
                new = "auto (vérité inconnue)"
            else:
                new = "auto juste" if sorted(map(list, pairs)) == sorted(map(list, truth)) \
                    else "AUTO FAUSSE"
        old = "auto" if cv_old.confidence >= 0.9 else "revue"
        key = titles.get(copy.assessment_id, copy.assessment_id)
        stats[key][f"{new} | avant : {old} | vérité : {source}"] += 1
        if new == "revue":
            reasons[key][diag.get("reason")] += 1
        if details or new == "AUTO FAUSSE":
            lines.append(f"  {new:22s} zone={zone.id} lu={pairs} vérité={truth} "
                         f"({source}) motif={diag.get('reason')} {diag}")
    db.close()

    for key in stats:
        print(f"== {key}")
        for label, n in sorted(stats[key].items()):
            print(f"  {n:4d}  {label}")
        if reasons[key]:
            print(f"  motifs de revue : {dict(reasons[key])}")
    for line in lines:
        print(line)
    if not stats:
        print("Aucune zone « relier » scannée trouvée.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

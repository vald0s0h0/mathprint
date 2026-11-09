"""Calibration des cases à cocher (QCM, grilles) sur de VRAIES copies (lecture seule).

Relit chaque zone à cases déjà scannée avec la décision ACTUELLE
(worker_cv.box_measures / page_box_thresholds / decide_boxes) et la compare à
la vérité terrain : la saisie du professeur (`ocr_attempts.provider =
'teacher_ocr'`, la plus récente : OCRiser ou « Affiner la correction »). Sans
saisie, la lecture automatique de l'époque sert de vérité — À VÉRIFIER À
L'ŒIL : une lecture certaine fausse que le professeur n'a pas vue y passe pour
juste (cas réel : croix à côté d'une case lue « sans réponse »).

Objectif : 0 lecture automatique fausse ; le reste en revue doit être des cas
réellement ambigus. Rien n'est écrit en base ni sur disque.

Usage :
- local (depuis backend/) :
    .venv/bin/python scripts/calibrate_checkboxes.py [<assessment_id ou préfixe>…] [--details]
- sur le NAS (une page en mémoire à la fois) :
    sudo docker exec -i mathprint-api-1 python - [<id>…] < backend/scripts/calibrate_checkboxes.py
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
    Copy, CopyItem, OcrAttempt, ResponseZone, Student,
)
from app.services import worker_cv                # noqa: E402
from app.services.pipeline import _qcm_exclusive  # noqa: E402


def _latest(db, zone_id: str, provider: str):
    return (db.query(OcrAttempt)
            .filter(OcrAttempt.zone_id == zone_id, OcrAttempt.provider == provider)
            .order_by(OcrAttempt.created_at.desc()).first())


def _value(attempt, grid: bool):
    raw = attempt.raw_json or {}
    v = raw.get("grid_selected") if grid else raw.get("selected")
    return v if grid or v is None else sorted(v)


def main(argv: list[str]) -> int:
    details = "--details" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    db = SessionLocal()
    rows = (db.query(ResponseZone, CopyItem, Copy, Student)
            .join(CopyItem, CopyItem.id == ResponseZone.item_id)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .join(Student, Student.id == Copy.student_id)
            .filter(CopyItem.response_type.like("qcm%")
                    | (CopyItem.response_type == "checkbox_grid"))
            .order_by(ResponseZone.page_id).all())
    if wanted:
        rows = [r for r in rows if any(r[2].assessment_id.startswith(w) for w in wanted)]
    by_page = defaultdict(list)
    for r in rows:
        by_page[(r[2].assessment_id, r[0].page_id)].append(r)

    stats = defaultdict(Counter)
    for (assessment_id, page_id), zrows in by_page.items():
        path = (settings.data_dir / "assessments" / assessment_id / "scans" / "derived"
                / f"page-{page_id}.png")
        page = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        if page is None:
            continue
        meas = {z.id: worker_cv.box_measures(page, (z.meta_json or {}).get("boxes", []))
                for z, *_ in zrows}
        thrs = worker_cv.page_box_thresholds(list(meas.values()))
        for zone, item, copy, student in zrows:
            cv_old = _latest(db, zone.id, "cv_local")
            if cv_old is None:
                continue
            grid = item.response_type == "checkbox_grid"
            sel, conf, why = worker_cv.decide_boxes(
                (zone.meta_json or {}).get("boxes", []), meas[zone.id], thrs,
                grid=grid, exclusive=_qcm_exclusive(item))
            sel = sel if grid or sel is None else sorted(sel)
            teacher = _latest(db, zone.id, "teacher_ocr")
            truth = _value(teacher or cv_old, grid)
            kind = "teacher" if teacher else "auto_old"
            outcome = ("auto_ok" if sel == truth else "ERROR") if conf >= 0.9 else "review"
            stats[(item.response_type, kind)][outcome] += 1
            old = "auto" if cv_old.confidence >= 0.9 else "review"
            if details or outcome == "ERROR":
                print(f"{student.name:14s} ex{item.sequence} {item.response_type:13s} "
                      f"{outcome:7s} (avant: {old:6s}) lu={sel} vérité={truth} [{kind}] {why}")
    print()
    for (rtype, kind), c in sorted(stats.items()):
        print(f"{rtype:13s} vérité={kind:8s} auto_ok={c['auto_ok']:4d} "
              f"review={c['review']:4d} ERROR={c['ERROR']:3d}")
    return 1 if any(c["ERROR"] for c in stats.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

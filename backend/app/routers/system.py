"""Système (phase 4) : sauvegardes, état de santé, calibration imprimante/scanner."""
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user, require_role
from ..models import CalibrationProfile, Job, User
from ..version import __version__
from ..services.runtime_settings import mock_enabled

router = APIRouter(prefix="/api/system", tags=["system"],
                   dependencies=[Depends(current_user)])

BACKUP_DIR_KEY = "backups"


# ------------------------------------------------------------- état de santé

@router.get("/status")
def status(db: Session = Depends(get_db)):
    from ..services import mathalea_client

    disk = shutil.disk_usage(settings.data_dir)
    mathalea = mathalea_client.health()
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    backups = sorted((settings.data_dir / BACKUP_DIR_KEY).glob("*"), reverse=True) \
        if (settings.data_dir / BACKUP_DIR_KEY).exists() else []
    return {
        "version": __version__,
        "build": {"sha": settings.build_sha, "time": settings.build_time},
        "database": {"ok": db_ok, "url_scheme": settings.database_url.split(":")[0]},
        "mathalea": mathalea or {"status": "unreachable"},
        "disk": {"total_gb": round(disk.total / 1e9, 1),
                 "free_gb": round(disk.free / 1e9, 1),
                 "alert": disk.free / disk.total < 0.1},
        "data_dir": str(settings.data_dir),
        "mock_mode": mock_enabled(db),
        "last_backup": backups[0].name if backups else None,
    }


# --------------------------------------------------------------- sauvegardes

@router.post("/backup", dependencies=[Depends(require_role("admin", "teacher"))])
def backup(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Sauvegarde de la base (dump SQLite ou pg_dump) dans /data/backups (§11.6)."""
    out_dir = settings.data_dir / BACKUP_DIR_KEY
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job = Job(type="backup", status="running", payload_json={"by": user.email})
    db.add(job)
    db.flush()
    try:
        if settings.database_url.startswith("sqlite"):
            src_path = settings.database_url.split("///")[-1]
            dest = out_dir / f"mathprint-{stamp}.sqlite"
            src = sqlite3.connect(src_path)
            dst = sqlite3.connect(dest)
            with dst:
                src.backup(dst)  # copie cohérente même en cours d'utilisation
            src.close(); dst.close()
        else:
            dest = out_dir / f"mathprint-{stamp}.dump"
            r = subprocess.run(["pg_dump", "--format=custom",
                                f"--file={dest}", settings.database_url.replace("+psycopg2", "")],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:400])
        # rétention 30 fichiers
        existing = sorted(out_dir.glob("mathprint-*"), reverse=True)
        for old in existing[30:]:
            old.unlink()
        job.status = "done"
        job.payload_json = {**job.payload_json, "file": dest.name,
                            "size": dest.stat().st_size}
        db.commit()
        return {"ok": True, "file": dest.name, "size": dest.stat().st_size}
    except Exception as e:
        job.status = "failed"
        job.error_code = str(e)[:400]
        db.commit()
        raise HTTPException(500, f"Sauvegarde en échec : {e}")


@router.get("/backups")
def list_backups():
    out_dir = settings.data_dir / BACKUP_DIR_KEY
    if not out_dir.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size,
             "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
            for f in sorted(out_dir.glob("mathprint-*"), reverse=True)]


# --------------------------------------------------------------- calibration

@router.post("/calibration/page")
def calibration_page():
    """Génère une page test A4 avec les 4 marqueurs standard : à imprimer à
    100 % puis scanner pour mesurer offsets/échelle (assistant §11.5)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from ..services.pdfgen import _draw_markers, MARGIN
    from ..services.security import sign_page

    out_dir = settings.data_dir / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "calibration_page.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    _draw_markers(c, sign_page("calibration-page"))
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, A4[1] / 2,
                 "Page de calibration MathPrint — imprimer à taille réelle (100 %), "
                 "puis scanner et déposer le fichier dans Paramètres > Calibration.")
    # règle de contrôle : trait de 100 mm
    y = A4[1] / 2 - 20 * mm
    c.line(MARGIN, y, MARGIN + 100 * mm, y)
    c.drawString(MARGIN, y - 5 * mm, "Ce trait doit mesurer exactement 100 mm.")
    c.showPage()
    c.save()
    return FileResponse(path, filename="calibration_page.pdf")


@router.post("/calibration/measure")
async def calibration_measure(file: UploadFile, printer_name: str = "",
                              scanner_name: str = "", db: Session = Depends(get_db)):
    """Analyse le scan de la page test : détecte les 4 marqueurs, calcule
    translation/échelle/rotation et enregistre le profil de calibration."""
    import cv2
    import numpy as np
    from ..services import worker_cv

    content = await file.read()
    tmp = settings.data_dir / "calibration" / "scan_upload.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(content)

    if file.filename and file.filename.lower().endswith(".pdf"):
        images = worker_cv.raster_pdf(str(tmp))
        if not images:
            raise HTTPException(422, "PDF vide")
        img = images[0]
    else:
        arr = np.frombuffer(content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(422, "Image illisible")

    res = worker_cv.analyze_page(img)
    if res.marker_count < 3:
        raise HTTPException(422, f"Marqueurs insuffisants ({res.marker_count}/4) — "
                                 "vérifier l'impression à 100 % et le scan complet")

    # estimation simple : comparer distances entre marqueurs détectés et canoniques
    detections = {}
    for text, quad in worker_cv._detect_qrcodes(img):
        if text.startswith("MP1|"):
            detections["MAIN"] = quad.mean(axis=0)
    for role, center in worker_cv.detect_fiducials(img).items():
        detections.setdefault(role, center)
    if not {"TL", "BL", "BR"} <= set(detections):
        raise HTTPException(422, "Coins TL/BL/BR non détectés")

    px_per_mm_x = abs(detections["BR"][0] - detections["BL"][0]) / \
        ((worker_cv.CANONICAL_CENTERS_PT["BR"][0] - worker_cv.CANONICAL_CENTERS_PT["BL"][0]) / 72 * 25.4)
    px_per_mm_y = abs(detections["BL"][1] - detections["TL"][1]) / \
        ((worker_cv.CANONICAL_CENTERS_PT["TL"][1] - worker_cv.CANONICAL_CENTERS_PT["BL"][1]) / 72 * 25.4)
    nominal = worker_cv.DPI / 25.4
    scale_x, scale_y = px_per_mm_x / nominal, px_per_mm_y / nominal
    dx = np.degrees(np.arctan2(detections["BR"][1] - detections["BL"][1],
                               detections["BR"][0] - detections["BL"][0]))

    profile = CalibrationProfile(
        printer_name=printer_name, scanner_name=scanner_name,
        scale_x=round(float(scale_x), 4), scale_y=round(float(scale_y), 4),
        rotation_deg=round(float(dx), 3),
        offset_x_mm=round(float(detections["BL"][0] / nominal
                                - worker_cv.CANONICAL_CENTERS_PT["BL"][0] / 72 * 25.4), 2),
        offset_y_mm=round(float(detections["TL"][1] / nominal
                                - (worker_cv.PAGE_H - worker_cv.CANONICAL_CENTERS_PT["TL"][1]) / 72 * 25.4), 2),
        validated_at=datetime.now(timezone.utc))
    db.add(profile)
    db.commit()
    return {"scale_x": profile.scale_x, "scale_y": profile.scale_y,
            "rotation_deg": profile.rotation_deg,
            "offset_x_mm": profile.offset_x_mm, "offset_y_mm": profile.offset_y_mm,
            "verdict": "ok" if abs(profile.scale_x - 1) < 0.01 and abs(profile.scale_y - 1) < 0.01
            else "vérifier le réglage taille réelle 100 %"}


@router.get("/calibration/profiles")
def calibration_profiles(db: Session = Depends(get_db)):
    return [{"id": p.id, "printer": p.printer_name, "scanner": p.scanner_name,
             "scale_x": p.scale_x, "scale_y": p.scale_y,
             "rotation_deg": p.rotation_deg, "offset_x_mm": p.offset_x_mm,
             "offset_y_mm": p.offset_y_mm, "validated_at": str(p.validated_at)}
            for p in db.query(CalibrationProfile).all()]

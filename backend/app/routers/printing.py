"""Impression (§11.5) : imprimantes CUPS locales (Mac/PC/NAS) et IPP réseau.

- Les files CUPS déjà configurées sur la machine qui héberge l'API (le Mac du
  professeur en développement, le conteneur sur le NAS en production) sont
  découvertes via lpstat et utilisables directement avec lp.
- Une imprimante réseau IPP peut être enregistrée en base (table printers) ;
  elle est imprimée via lp -d si une file CUPS du même nom existe, sinon via
  l'URI IPP directe (option -h pour un serveur CUPS distant).
- Réglage imposé « taille réelle 100 % » : print-scaling=none (§11.5).
- Chaque job est journalisé : fichier, imprimante, utilisateur, heure, résultat.
"""
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import AuditLog, Job, Printer, User

router = APIRouter(prefix="/api/printers", tags=["printing"],
                   dependencies=[Depends(current_user)])

ALLOWED_FILES = {
    "subject_batch.pdf": "generated",
    "correction_overlay.pdf": "overlays",
    "calibration_page.pdf": "calibration",
}


_LPSTAT_ENV = {"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/usr/local/bin"}


def _local_printers() -> list[dict]:
    """Files CUPS configurées localement (lpstat, sortie forcée en anglais)."""
    printers = []
    try:
        # lpstat -e : liste brute des destinations, non localisée (fiable sur macOS)
        out = subprocess.run(["lpstat", "-e"], capture_output=True, text=True,
                             timeout=10, env=_LPSTAT_ENV).stdout
        for line in out.splitlines():
            name = line.strip()
            if name:
                printers.append({"name": name, "source": "cups_local", "status": "idle"})
        default = subprocess.run(["lpstat", "-d"], capture_output=True, text=True,
                                 timeout=10, env=_LPSTAT_ENV).stdout
        if ":" in default:
            def_name = default.split(":")[-1].strip()
            for p in printers:
                p["default"] = p["name"] == def_name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return printers


@router.get("")
def list_printers(db: Session = Depends(get_db)):
    local = _local_printers()
    network = [{"name": p.name, "source": "network_ipp", "uri": p.uri,
                "status": "registered" if p.active else "disabled"}
               for p in db.query(Printer).all()]
    return {"local": local, "network": network,
            "printing_available": bool(local) or bool(network)}


class NetworkPrinterIn(BaseModel):
    name: str
    uri: str          # ipp://... ou hôte CUPS distant
    protocol: str = "ipp"


@router.post("/network")
def register_network_printer(body: NetworkPrinterIn, db: Session = Depends(get_db)):
    p = db.query(Printer).filter_by(name=body.name).first()
    if not p:
        p = Printer(name=body.name)
        db.add(p)
    p.uri = body.uri
    p.protocol = body.protocol
    p.active = True
    db.commit()
    return {"ok": True}


class PrintIn(BaseModel):
    assessment_id: str
    file: str                  # subject_batch.pdf | correction_overlay.pdf
    printer: str
    copies: int = 1
    duplex: bool = False


@router.post("/print")
def print_file(body: PrintIn, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    if body.file not in ALLOWED_FILES:
        raise HTTPException(422, "Fichier non imprimable")
    path = (settings.data_dir / "assessments" / body.assessment_id /
            ALLOWED_FILES[body.file] / body.file)
    if not Path(path).exists():
        raise HTTPException(404, "Fichier non encore généré")

    cmd = ["lp", "-n", str(max(1, min(50, body.copies))),
           "-o", "media=A4",
           "-o", "print-scaling=none",   # taille réelle 100 % imposée (§11.5)
           "-o", f"sides={'two-sided-long-edge' if body.duplex else 'one-sided'}"]

    net = db.query(Printer).filter_by(name=body.printer, active=True).first()
    local_names = {p["name"] for p in _local_printers()}
    if body.printer in local_names:
        cmd += ["-d", body.printer]
    elif net and net.uri.startswith("ipp"):
        # lp accepte une URI de destination via -d seulement pour les files ;
        # pour une IPP directe on passe par l'hôte CUPS distant si fourni
        host = net.uri.removeprefix("ipp://").removeprefix("ipps://").split("/")[0]
        cmd += ["-h", host, "-d", net.name]
    else:
        raise HTTPException(422, f"Imprimante inconnue : {body.printer}")
    cmd.append(str(path))

    job = Job(type="print", status="running",
              payload_json={"file": str(path), "printer": body.printer,
                            "copies": body.copies, "duplex": body.duplex,
                            "user": user.email})
    db.add(job)
    db.flush()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            job.status = "failed"
            job.error_code = (r.stderr or r.stdout).strip()[:400]
            db.commit()
            raise HTTPException(502, f"Échec impression : {job.error_code}")
        job.status = "done"
        db.add(AuditLog(actor_id=user.id, action="print",
                        entity_type="assessment", entity_id=body.assessment_id,
                        after_json={"printer": body.printer, "file": body.file,
                                    "lp": r.stdout.strip()}))
        db.commit()
        return {"ok": True, "lp_output": r.stdout.strip()}
    except subprocess.TimeoutExpired:
        job.status = "failed"
        job.error_code = "timeout"
        db.commit()
        raise HTTPException(504, "Impression : délai dépassé")


@router.get("/jobs")
def print_jobs(db: Session = Depends(get_db)):
    rows = (db.query(Job).filter_by(type="print")
            .order_by(Job.created_at.desc()).limit(30).all())
    return [{"id": j.id, "status": j.status, "error": j.error_code,
             "payload": j.payload_json, "created_at": str(j.created_at)} for j in rows]

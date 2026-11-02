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
import hashlib
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user, require_role
from ..models import (AuditLog, Assessment, ConnectorPrintJob, Copy,
                      DocumentPage, Job, PrintConnector, Printer, ScanBatch,
                      Student, User, TeacherProfile)
from ..services import overlay_queue
from ..services.print_connectors import (connector_online,
                                         expire_stale_queued_jobs)

router = APIRouter(prefix="/api/printers", tags=["printing"],
                   dependencies=[Depends(current_user)])

OVERLAY_FILE = "correction_overlay.pdf"
ALLOWED_FILES = {
    "subject_batch.pdf": "generated",
    OVERLAY_FILE: "overlays",
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


def _profile_view(p: Printer | None) -> dict:
    return {
        "duplex": bool(p.duplex) if p else False,
        "pickup_reverse_order": bool(p.pickup_reverse_order) if p else False,
        "output_reverse_order": bool(p.output_reverse_order) if p else False,
        "app_default": bool(p.app_default) if p else False,
        "adf_reverse_order": bool(p.adf_reverse_order) if p else False,
        "overlay_offset_x_mm": float(p.overlay_offset_x_mm or 0) if p else 0.0,
        "overlay_offset_y_mm": float(p.overlay_offset_y_mm or 0) if p else 0.0,
    }


def _connector_for_profile(db: Session, profile: Printer | None) -> PrintConnector | None:
    if not profile or profile.protocol != "connector":
        return None
    connector_id = (profile.capabilities_json or {}).get("connector_id")
    return db.get(PrintConnector, connector_id) if connector_id else None


def _can_use_connector(db, connector, user):
    if not connector: return False
    if connector.user_id == user.id: return True
    owner = db.get(User, connector.user_id)
    return user.role in ("admin", "corrector") and owner and owner.role in ("admin", "corrector")


def _assert_profile_access(db: Session, profile: Printer | None, user: User):
    connector = _connector_for_profile(db, profile)
    if user.role == "teacher" and not connector:
        raise HTTPException(403, "Imprimante du centre réservée à l’équipe Kironina")
    if connector and not _can_use_connector(db, connector, user):
        raise HTTPException(404, "Imprimante inconnue")


def _get_or_create_profile(db: Session, name: str, *, protocol: str = "cups") -> Printer:
    p = db.query(Printer).filter_by(name=name).first()
    if p is None:
        p = Printer(name=name, protocol=protocol, active=True)
        db.add(p)
        db.flush()
    return p


def _effective_reverse(profile: Printer | None, file: str) -> bool:
    """Le document doit-il être remis à l'imprimante en ordre inverse ?

    Sujet : seule la pile à la sortie compte, sur des feuilles vierges. Le
    prélèvement et la pose sur le bac se composent par XOR.

    Overlay : chaque feuille prise dans le bac est une copie précise, qui doit
    recevoir SA correction. Seuls l'ordre de cette pile (ADF) et l'ordre de
    traitement (prélèvement) comptent. La pose sur le bac, qui a lieu après
    l'impression, ne change rien : la compter posait les corrections en sens
    inverse sur toute imprimante dont le bac inverse la pile.
    """
    pickup_reverses = bool(profile and profile.pickup_reverse_order)
    if file == OVERLAY_FILE:
        return pickup_reverses ^ bool(profile and profile.adf_reverse_order)
    return pickup_reverses ^ bool(profile and profile.output_reverse_order)


def _lp_command(*, printer: str, path: Path, copies: int, duplex: bool,
                reverse: bool, network: Printer | None,
                local_names: set[str]) -> list[str]:
    """Commande CUPS déterministe : copies collationnées, ordre explicitement
    fixé (jamais abandonné au défaut du pilote) et taille réelle."""
    cmd = ["lp", "-n", str(max(1, min(50, copies))),
           "-o", "media=A4", "-o", "print-scaling=none",
           "-o", "Collate=True",
           "-o", f"outputorder={'reverse' if reverse else 'normal'}",
           "-o", f"sides={'two-sided-long-edge' if duplex else 'one-sided'}"]
    if printer in local_names:
        cmd += ["-d", printer]
    elif network and network.active and network.uri.startswith(("ipp://", "ipps://")):
        host = network.uri.removeprefix("ipp://").removeprefix("ipps://").split("/")[0]
        cmd += ["-h", host, "-d", network.name]
    else:
        raise HTTPException(422, f"Imprimante inconnue : {printer}")
    cmd.append(str(path))
    return cmd


@router.get("")
def list_printers(db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    local = _local_printers()
    profiles = {p.name: p for p in db.query(Printer).all()}
    local_names = {p["name"] for p in local}
    if user.role == "teacher": local = []
    local_rows = [{**p, "display_name": p["name"], "available": True,
                   "system_default": bool(p.get("default")),
                   **_profile_view(profiles.get(p["name"]))} for p in local]
    connectors = {c.id: c for c in db.query(PrintConnector).filter_by(active=True).all()
                  if _can_use_connector(db, c, user)}
    connector_rows = []
    connector_printer_counts: dict[str, int] = {}
    for profile in profiles.values():
        if not profile.active or profile.protocol != "connector":
            continue
        caps = profile.capabilities_json or {}
        connector = connectors.get(caps.get("connector_id"))
        if not connector:
            continue
        online = connector_online(connector)
        connector_printer_counts[connector.id] = connector_printer_counts.get(connector.id, 0) + 1
        local_rows.append({
            "id": profile.id,
            "name": profile.name,
            "display_name": caps.get("native_name") or profile.name,
            "device_name": connector.name,
            "device_platform": connector.platform,
            "connector_id": connector.id,
            "source": "connector_local",
            "status": "online" if online else "offline",
            "available": online,
            "last_seen_at": connector.last_seen_at.isoformat()
                if connector.last_seen_at else None,
            "system_default": bool(caps.get("is_default")),
            **_profile_view(profile),
        })
    for connector in sorted(connectors.values(), key=lambda value: value.name.lower()):
        online = connector_online(connector)
        connector_rows.append({
            "id": connector.id,
            "name": connector.name,
            "platform": connector.platform,
            "status": "online" if online else "offline",
            "last_seen_at": connector.last_seen_at.isoformat()
                if connector.last_seen_at else None,
            "printer_count": connector_printer_counts.get(connector.id, 0),
        })
    network_rows = [{"id": p.id, "name": p.name, "source": "network_ipp",
                     "display_name": p.name, "uri": p.uri,
                     "status": "registered", "available": True,
                     **_profile_view(p)}
                    for p in profiles.values()
                    if p.active and p.protocol not in ("cups", "connector")
                    and p.uri and p.name not in local_names]

    if user.role == "teacher": network_rows = []
    all_rows = [*local_rows, *network_rows]
    return {"local": local_rows, "network": network_rows,
            "connectors": connector_rows,
            "online_connector_count": sum(
                row["status"] == "online" for row in connector_rows),
            "printing_available": any(row["available"] for row in all_rows)}


class NetworkPrinterIn(BaseModel):
    name: str
    uri: str          # ipp://... ou hôte CUPS distant
    protocol: str = "ipp"


@router.post("/network", dependencies=[Depends(require_role("admin", "corrector"))])
def register_network_printer(body: NetworkPrinterIn, db: Session = Depends(get_db)):
    p = db.query(Printer).filter_by(name=body.name).first()
    if p and p.protocol == "cups":
        raise HTTPException(409, "Une file CUPS locale porte déjà ce nom")
    if not p:
        p = Printer(name=body.name)
        db.add(p)
    p.uri = body.uri
    p.protocol = body.protocol
    p.active = True
    db.commit()
    return {"ok": True}


class PrinterPreferencesIn(BaseModel):
    name: str
    duplex: bool | None = None
    pickup_reverse_order: bool | None = None
    output_reverse_order: bool | None = None
    app_default: bool | None = None
    adf_reverse_order: bool | None = None
    overlay_offset_x_mm: float | None = Field(default=None, ge=-20, le=20)
    overlay_offset_y_mm: float | None = Field(default=None, ge=-20, le=20)


@router.patch("/preferences")
def update_printer_preferences(body: PrinterPreferencesIn,
                               db: Session = Depends(get_db),
                               user: User = Depends(current_user)):
    local_names = {p["name"] for p in _local_printers()}
    existing = db.query(Printer).filter_by(name=body.name).first()
    if body.name not in local_names and existing is None:
        raise HTTPException(404, "Imprimante inconnue")
    _assert_profile_access(db, existing, user)
    p = existing or _get_or_create_profile(db, body.name)
    for field in ("duplex", "pickup_reverse_order", "adf_reverse_order"):
        value = getattr(body, field)
        if value is not None:
            setattr(p, field, value)
    if body.output_reverse_order is not None:
        p.output_reverse_order = body.output_reverse_order
    for field in ("overlay_offset_x_mm", "overlay_offset_y_mm"):
        value = getattr(body, field)
        if value is not None:
            setattr(p, field, round(value * 2) / 2)  # pas de 0,5 mm
    if body.app_default:
        for candidate in db.query(Printer).all():
            connector = _connector_for_profile(db, candidate)
            if _can_use_connector(db, connector, user) or (not connector and user.role in ("admin", "corrector")):
                candidate.app_default = False
        p.app_default = True
    db.commit()
    return {"ok": True, "name": p.name, **_profile_view(p)}


@router.delete("/network/{printer_id}", dependencies=[Depends(require_role("admin", "corrector"))])
def delete_network_printer(printer_id: str, db: Session = Depends(get_db)):
    p = db.get(Printer, printer_id)
    if not p or p.protocol in ("cups", "connector") or not p.uri:
        raise HTTPException(404, "Imprimante réseau inconnue")
    db.delete(p)
    db.commit()
    return {"ok": True}


class PrintIn(BaseModel):
    assessment_id: str
    file: str                  # subject_batch.pdf | correction_overlay.pdf
    printer: str
    copies: int = 1
    duplex: bool | None = None
    pass_side: Literal["all", "recto", "verso"] = "all"
    # Overlay uniquement : première feuille à imprimer (1 = début de la file) et
    # élève annoncé au professeur, pour refuser un envoi si la file a changé.
    start_position: int = 1
    expected_student: str | None = None
    # Essai : la seule feuille de `start_position`, sans effet sur le suivi.
    sample: bool = False


def _overlay_path(assessment_id: str) -> Path:
    return (settings.data_dir / "assessments" / assessment_id /
            ALLOWED_FILES[OVERLAY_FILE] / OVERLAY_FILE)


def _overlay_sheets(db: Session, assessment: Assessment, path: Path,
                    batch: ScanBatch) -> list[overlay_queue.Sheet]:
    from pypdf import PdfReader

    sides = overlay_queue.overlay_sides(db, batch, len(PdfReader(str(path)).pages))
    return overlay_queue.group_sheets(sides, duplex=bool(assessment.duplex))


def _remember_overlay_print(batch: ScanBatch, *, status: str, job_id: str | None,
                            pass_side: str, start: int, total: int,
                            selected: list[overlay_queue.Sheet], printer: str,
                            adf_reversed: bool, error: str | None = None):
    batch.overlay_print_json = {
        "status": status, "job_id": job_id, "error": error, "pass_side": pass_side,
        "start_position": start, "total": total, "sheets": len(selected),
        "first_student": selected[0].student, "last_student": selected[-1].student,
        "printer": printer, "adf_reversed": adf_reversed,
        "overlay_rev": batch.overlay_rev or 0,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def _overlay_last_print(db: Session, batch: ScanBatch, *, total: int,
                        adf_reversed: bool) -> dict | None:
    """Dernier envoi de CET overlay : un rendu régénéré repart de zéro."""
    data = batch.overlay_print_json
    if not data or data.get("overlay_rev") != (batch.overlay_rev or 0) or data.get("total") != total:
        return None
    view = dict(data)
    job = db.get(ConnectorPrintJob, data["job_id"]) if data.get("job_id") else None
    if job:
        view["status"], view["error"] = job.status, job.error_message
    # Une position n'a de sens que dans l'ordre où elle a été choisie.
    if data.get("adf_reversed") != adf_reversed:
        view["start_position"] = None
    return view


@router.get("/overlay-queue")
def overlay_print_queue(assessment_id: str, printer: str | None = None,
                        db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    """File d'attente de l'overlay, dans l'ordre où les feuilles seront imprimées."""
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        raise HTTPException(404, "Sujet inaccessible")
    profile = (db.query(Printer).filter_by(name=printer, active=True).first()
               if printer else None)
    if printer:
        _assert_profile_access(db, profile, user)
    adf_reversed = bool(profile and profile.adf_reverse_order)
    expire_stale_queued_jobs(db, user_id=user.id)
    path = _overlay_path(assessment_id)
    batch = overlay_queue.overlay_batch(db, assessment_id)
    blocked = None
    if not path.exists() or not batch:
        blocked = "L'overlay n'est pas encore généré."
    elif (batch.refine_rev or 0) > (batch.overlay_rev or 0):
        blocked = "L'overlay n'est pas à jour des dernières corrections : régénérez-le avant d'imprimer."
    sheets: list[overlay_queue.Sheet] = []
    if not blocked:
        try:
            sheets = _overlay_sheets(db, assessment, path, batch)
        except overlay_queue.OverlayQueueError as error:
            blocked = str(error)
    queue = overlay_queue.physical_queue(sheets, adf_reversed=adf_reversed)
    return {
        "blocked": blocked, "total": len(queue), "adf_reversed": adf_reversed,
        "assessment_duplex": bool(assessment.duplex),
        "entries": [{"position": i + 1, "student": sheet.student,
                     "identified": sheet.identified, "pages": sheet.pages_label}
                    for i, sheet in enumerate(queue)],
        "last_print": _overlay_last_print(db, batch, total=len(queue), adf_reversed=adf_reversed)
                      if batch and queue else None,
    }


def _subject_pass_pdf(db: Session, assessment_id: str, source: Path,
                      pass_side: Literal["recto", "verso"]) -> bytes:
    """Extrait les rectos ou versos de CHAQUE copie, dans l'ordre du PDF.

    La parité globale du PDF ne suffit pas si une copie a débordé sur un nombre
    impair de pages. Les DocumentPage persistées donnent la vraie parité au sein
    de chaque copie et protègent donc l'alignement élève par élève.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source))
    page_rows = (db.query(DocumentPage)
                 .join(Copy, DocumentPage.copy_id == Copy.id)
                 .join(Student, Copy.student_id == Student.id)
                 .filter(Copy.assessment_id == assessment_id)
                 .order_by(Student.order_index, Student.id,
                           DocumentPage.page_no).all())
    if len(page_rows) != len(reader.pages):
        raise HTTPException(
            409, "Le découpage recto/verso de ce sujet ancien est indisponible : "
                 "régénérez-le avant l’impression en deux passes.")
    wanted_parity = 1 if pass_side == "recto" else 0
    writer = PdfWriter()
    for i, (pdf_page, document_page) in enumerate(zip(reader.pages, page_rows)):
        if document_page.page_no % 2 == wanted_parity:
            writer.add_page(pdf_page)
        elif pass_side == "verso" and (i + 1 == len(page_rows) or page_rows[i + 1].copy_id != document_page.copy_id):
            # Une dernière page impaire occupe une feuille : le verso blanc
            # doit traverser l'imprimante pour ne pas décaler l'élève suivant.
            writer.add_blank_page(width=pdf_page.mediabox.width, height=pdf_page.mediabox.height)
    if not writer.pages:
        raise HTTPException(422, f"Ce sujet ne contient aucun {pass_side}")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _reverse_pdf_pages(pdf: bytes, *, duplex: bool = False) -> bytes:
    """Inverse réellement les pages pour ne dépendre d'aucune option pilote.

    Windows n'a pas d'équivalent universel à ``outputorder=reverse``. Le PDF
    remis au connecteur devient donc l'autorité commune macOS/Windows.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(BytesIO(pdf))
    writer = PdfWriter()
    if duplex:
        pages = list(reader.pages)
        if len(pages) % 2:
            from pypdf import PageObject
            last = pages[-1]
            pages.append(PageObject.create_blank_page(width=last.mediabox.width, height=last.mediabox.height))
        # Inverser les FEUILLES ; inverser les pages échangerait recto et verso.
        for i in reversed(range(0, len(pages), 2)):
            writer.add_page(pages[i]); writer.add_page(pages[i + 1])
    else:
        for page in reversed(reader.pages):
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _queue_connector_job(*, db: Session, user: User, profile: Printer,
                         title: str, file_name: str, pass_side: str,
                         document: bytes, assessment_id: str | None,
                         copies: int, duplex: bool, reverse: bool,
                         overlay_offset_mm: tuple[float, float] | None = None) -> ConnectorPrintJob:
    connector = _connector_for_profile(db, profile)
    if not _can_use_connector(db, connector, user):
        raise HTTPException(409, "Le connecteur de cette imprimante est déconnecté")
    if not connector_online(connector):
        raise HTTPException(
            409,
            f"Le poste « {connector.name} » est hors ligne. "
            "Ouvrez Kiro et Nina Connector sur ce poste avant d'imprimer.",
        )
    caps = profile.capabilities_json or {}
    native_name = caps.get("native_name")
    if not isinstance(native_name, str) or not native_name.strip():
        raise HTTPException(409, "Nom local de l'imprimante indisponible")

    prepared = _reverse_pdf_pages(document, duplex=duplex) if reverse else document
    job = ConnectorPrintJob(
        connector_id=connector.id, user_id=user.id, printer_id=profile.id,
        assessment_id=assessment_id, title=title, file_name=file_name,
        pass_side=pass_side, native_printer_name=native_name,
        options_json={
            "copies": max(1, min(50, copies)),
            "media": "A4",
            "scale": "none",
            "collate": True,
            "duplex": bool(duplex),
            "reverse_applied_to_pdf": bool(reverse),
            "pickup_reverse_order": bool(profile.pickup_reverse_order),
            "output_reverse_order": bool(profile.output_reverse_order),
            "adf_reverse_order": bool(profile.adf_reverse_order),
            **({"overlay_offset_mm": list(overlay_offset_mm)} if overlay_offset_mm else {}),
        },
        document_relpath="pending", document_sha256="pending",
        document_size=len(prepared))
    db.add(job)
    db.flush()
    relative = Path("connector_jobs") / f"{job.id}.pdf"
    output = settings.data_dir / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    # Remplacement atomique : un claim ne verra jamais un PDF partiel.
    fd, raw_temp = tempfile.mkstemp(prefix=f"{job.id}-", suffix=".pdf",
                                    dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(prepared)
            stream.flush()
            os.fsync(stream.fileno())
        Path(raw_temp).replace(output)
    finally:
        Path(raw_temp).unlink(missing_ok=True)
    job.document_relpath = str(relative)
    job.document_sha256 = hashlib.sha256(prepared).hexdigest()
    job.document_size = len(prepared)
    db.commit()
    return job


@router.post("/print")
def print_file(body: PrintIn, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    from ..services.education import assert_service_active
    from ..services.delivery_sheet import render_delivery_sheet, with_delivery_sheet
    assessment = db.get(Assessment, body.assessment_id)
    if not assessment: raise HTTPException(404, "Sujet inaccessible")
    assert_service_active(db, assessment)
    if body.file not in ALLOWED_FILES:
        raise HTTPException(422, "Fichier non imprimable")
    path = (settings.data_dir / "assessments" / body.assessment_id /
            ALLOWED_FILES[body.file] / body.file)
    if not Path(path).exists():
        raise HTTPException(404, "Fichier non encore généré")
    if body.file == "correction_overlay.pdf" and (
            db.query(ScanBatch).filter(ScanBatch.assessment_id == body.assessment_id,
                                       ScanBatch.refine_rev > ScanBatch.overlay_rev)
            .count()):
        # corrections affinées pas encore reportées (services.refine) : imprimer
        # maintenant distribuerait des notes périmées
        raise HTTPException(409, "L'overlay n'est pas à jour des dernières corrections : "
                                 "régénérez-le avant d'imprimer (Corrections → Affiner).")

    profile = db.query(Printer).filter_by(name=body.printer, active=True).first()
    _assert_profile_access(db, profile, user)
    if user.role == "teacher" and (not profile or profile.protocol != "connector"):
        raise HTTPException(403, "Les imprimantes du centre sont réservées à l’équipe Kironina")
    assessment = db.get(Assessment, body.assessment_id)
    overlay = body.file == OVERLAY_FILE
    sample = overlay and body.sample
    if body.sample and not overlay:
        raise HTTPException(422, "L’essai ne concerne que l’overlay")
    if body.pass_side != "all":
        if (body.file not in ("subject_batch.pdf", OVERLAY_FILE)
                or not assessment or not assessment.duplex):
            raise HTTPException(422, "Les passes recto/verso ne concernent qu’un sujet recto-verso")
        if profile and profile.duplex:
            raise HTTPException(409, "Cette imprimante est configurée en recto-verso automatique")

    duplex = (profile.duplex if body.duplex is None and profile else bool(body.duplex))
    if overlay:
        # L'overlay suit la feuille déjà imprimée : son recto-verso se déduit du
        # sujet, jamais d'un réglage transmis par l'appelant.
        duplex = bool(assessment.duplex and profile and profile.duplex)
        if sample:
            duplex = False
        elif assessment.duplex and not duplex and body.pass_side == "all":
            raise HTTPException(409, "Cette imprimante n’a pas de recto-verso automatique : "
                                     "lancez les deux passes séparément.")
    if body.pass_side != "all":
        duplex = False
    reverse = _effective_reverse(profile, body.file)

    overlay_batch = overlay_sheets = overlay_selected = overlay_offset = None
    adf_reversed = bool(profile and profile.adf_reverse_order)
    if overlay:
        overlay_batch = overlay_queue.overlay_batch(db, body.assessment_id)
        if not overlay_batch:
            raise HTTPException(404, "Fichier non encore généré")
        try:
            overlay_sheets = _overlay_sheets(db, assessment, Path(path), overlay_batch)
        except overlay_queue.OverlayQueueError as error:
            raise HTTPException(409, str(error))
        queue = overlay_queue.physical_queue(overlay_sheets, adf_reversed=adf_reversed)
        if not 1 <= body.start_position <= len(queue):
            raise HTTPException(422, "Position de reprise hors de la file d’impression")
        overlay_selected = queue[body.start_position - 1:]
        if body.expected_student not in (None, overlay_selected[0].student):
            raise HTTPException(409, "La file d’impression a changé : vérifiez la prochaine "
                                     "copie avant de relancer.")
        if sample:
            overlay_selected = overlay_selected[:1]
        overlay_offset = ((profile.overlay_offset_x_mm or 0.0, profile.overlay_offset_y_mm or 0.0)
                          if profile else (0.0, 0.0))
        document = overlay_queue.overlay_document(
            Path(path), overlay_selected, "recto" if sample else body.pass_side,
            duplex=duplex, offset_mm=overlay_offset)
    elif body.start_position != 1:
        raise HTTPException(422, "La reprise ne concerne que l’overlay")
    elif body.pass_side != "all":
        document = _subject_pass_pdf(db, body.assessment_id, Path(path), body.pass_side)
    else:
        document = Path(path).read_bytes()
    copies = 1 if overlay else body.copies

    def remember_overlay(status: str, job_id: str | None = None, error: str | None = None):
        if overlay and not sample:
            _remember_overlay_print(
                overlay_batch, status=status, job_id=job_id, pass_side=body.pass_side,
                start=body.start_position, total=len(overlay_sheets),
                selected=overlay_selected, printer=body.printer,
                adf_reversed=adf_reversed, error=error)

    teacher = db.get(TeacherProfile, assessment.teacher_profile_id) if assessment.teacher_profile_id else None
    delivery = body.file == "subject_batch.pdf" and teacher and teacher.service_mode == "accompanied"
    if delivery:
        document = with_delivery_sheet(document, render_delivery_sheet(db, assessment),
                                       duplex=duplex, pass_side=body.pass_side)
    if profile and profile.protocol == "connector":
        job = _queue_connector_job(
            db=db, user=user, profile=profile,
            title=f"{assessment.title if assessment else 'Kiro & Nina'} — "
                  f"{body.pass_side if body.pass_side != 'all' else body.file}"
                  + (f" — essai pour {overlay_selected[0].student}" if sample
                     else f" — à partir de {overlay_selected[0].student}" if overlay else ""),
            # Un autre nom de fichier : un essai ne marque jamais l'overlay imprimé.
            file_name="overlay_sample.pdf" if sample else body.file,
            pass_side=body.pass_side, document=document,
            assessment_id=body.assessment_id, copies=copies,
            duplex=duplex, reverse=reverse, overlay_offset_mm=overlay_offset)
        remember_overlay("queued", job_id=job.id)
        db.commit()
        connector = _connector_for_profile(db, profile)
        return {"ok": True, "queued": True, "job_id": job.id,
                "lp_output": f"En attente sur {connector.name if connector else 'le connecteur'}"}

    if reverse:
        document = _reverse_pdf_pages(document, duplex=duplex)
    local_names = {p["name"] for p in _local_printers()}
    print_path = Path(path)
    temp_path: Path | None = None
    try:
        if body.pass_side != "all" or delivery or reverse or overlay:
            fd, raw_path = tempfile.mkstemp(prefix=f"kironina-{body.pass_side}-", suffix=".pdf")
            temp_path = Path(raw_path)
            stream = os.fdopen(fd, "wb")
            with stream:
                stream.write(document)
            print_path = temp_path

        cmd = _lp_command(printer=body.printer, path=print_path, copies=copies,
                          duplex=duplex, reverse=False, network=profile,
                          local_names=local_names)
        job = Job(type="print", status="running", created_by=user.id, assessment_id=body.assessment_id,
                  payload_json={"file": str(path), "printer": body.printer,
                                "copies": copies, "duplex": duplex,
                                "pass_side": body.pass_side,
                                "start_position": body.start_position,
                                "sample": sample,
                                "overlay_offset_mm": overlay_offset,
                                "cups_compensation_reverse": reverse,
                                "pickup_reverse_order": bool(
                                    profile and profile.pickup_reverse_order),
                                "output_reverse_order": bool(
                                    profile and profile.output_reverse_order),
                                "adf_reverse_order": bool(
                                    profile and profile.adf_reverse_order),
                                "user": user.email})
        db.add(job)
        db.flush()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            job.status = "failed"
            job.error_code = "timeout"
            remember_overlay("failed", error="Délai dépassé")
            db.commit()
            raise HTTPException(504, "Impression : délai dépassé")
        if r.returncode != 0:
            job.status = "failed"
            job.error_code = (r.stderr or r.stdout).strip()[:400]
            remember_overlay("failed", error=job.error_code)
            db.commit()
            raise HTTPException(502, f"Échec impression : {job.error_code}")
        job.status = "done"
        db.add(AuditLog(actor_id=user.id, action="print",
                        entity_type="assessment", entity_id=body.assessment_id,
                        after_json={"printer": body.printer, "file": body.file,
                                    "pass_side": body.pass_side,
                                    "start_position": body.start_position,
                                    "cups_compensation_reverse": reverse,
                                    "lp": r.stdout.strip()}))
        if body.file == "subject_batch.pdf" and body.pass_side in ("all", "verso"):
            if assessment and assessment.status == "ready":
                assessment.status = "printed"
        remember_overlay("submitted")
        # L'étape « Overlay imprimé » reflète désormais un vrai envoi CUPS
        # réussi, jamais une coche manuelle dans l'interface. Une passe recto
        # laisse encore tous les versos à imprimer.
        if overlay and not sample and body.pass_side in ("all", "verso"):
            (db.query(ScanBatch).filter(ScanBatch.assessment_id == body.assessment_id)
             .update({ScanBatch.overlay_printed: True}, synchronize_session=False))
        db.commit()
        return {"ok": True, "lp_output": r.stdout.strip()}
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def _test_pages_pdf() -> bytes:
    """Deux feuilles diagnostic sans ambiguïté de haut/bas ni d'ordre."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    for number in (1, 2):
        c.setFont("Helvetica-Bold", 34)
        c.drawCentredString(width / 2, height - 28 * mm, "HAUT")
        c.setFont("Helvetica-Bold", 96)
        c.drawCentredString(width / 2, height / 2 - 25, str(number))
        c.setFont("Helvetica", 16)
        c.drawCentredString(width / 2, height / 2 - 52,
                            f"PAGE {number} SUR 2 — TEST KIRO & NINA")
        c.setFont("Helvetica-Bold", 34)
        c.drawCentredString(width / 2, 18 * mm, "BAS")
        c.showPage()
    c.save()
    return buf.getvalue()


class TestPrintIn(BaseModel):
    printer: str


@router.post("/test")
def print_test_pages(body: TestPrintIn, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """Imprime deux feuilles recto en ordre neutre. Le résultat brut permet de
    renseigner les cases ADF/imprimante sans qu'une compensation déjà cochée ne
    fausse le diagnostic."""
    profile = db.query(Printer).filter_by(name=body.printer, active=True).first()
    _assert_profile_access(db, profile, user)
    if profile and profile.protocol == "connector":
        job = _queue_connector_job(
            db=db, user=user, profile=profile, title="Test Kiro & Nina 1–2",
            file_name="printer_test.pdf", pass_side="all",
            document=_test_pages_pdf(), assessment_id=None, copies=1,
            duplex=False, reverse=False)
        connector = _connector_for_profile(db, profile)
        return {"ok": True, "queued": True, "job_id": job.id,
                "lp_output": f"Test en attente sur {connector.name if connector else 'le connecteur'}"}
    local_names = {p["name"] for p in _local_printers()}
    fd, raw_path = tempfile.mkstemp(prefix="kironina-order-", suffix=".pdf")
    path = Path(raw_path)
    try:
        stream = os.fdopen(fd, "wb")
        fd = -1
        with stream:
            stream.write(_test_pages_pdf())
        cmd = _lp_command(printer=body.printer, path=path, copies=1, duplex=False,
                          reverse=False, network=profile, local_names=local_names)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise HTTPException(502, f"Échec impression test : {(r.stderr or r.stdout).strip()[:400]}")
        db.add(AuditLog(actor_id=user.id, action="print_test",
                        entity_type="printer", entity_id=body.printer,
                        after_json={"printer": body.printer, "lp": r.stdout.strip()}))
        db.commit()
        return {"ok": True, "lp_output": r.stdout.strip()}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Impression test : délai dépassé")
    finally:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)


@router.get("/jobs")
def print_jobs(db: Session = Depends(get_db)):
    rows = (db.query(Job).filter_by(type="print")
            .order_by(Job.created_at.desc()).limit(30).all())
    return [{"id": j.id, "status": j.status, "error": j.error_code,
             "payload": j.payload_json, "created_at": str(j.created_at)} for j in rows]


@router.get("/jobs/{job_id}")
def connector_job_status(job_id: str, db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    expire_stale_queued_jobs(db, user_id=user.id)
    job = db.get(ConnectorPrintJob, job_id)
    if not job or (job.user_id != user.id and user.role not in ("admin", "corrector")):
        raise HTTPException(404, "Travail d'impression inconnu")
    return {"id": job.id, "status": job.status,
            "error": job.error_message, "spool_job_id": job.spool_job_id}

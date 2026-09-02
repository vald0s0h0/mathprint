"""Réception automatique des scans par mail (relève IMAP) : le scanner réseau
de l'établissement (ADF) envoie les copies scannées par mail à une boîte
dédiée ; ce service la relève périodiquement et réinjecte les pièces jointes
reconnues dans le pipeline de dépôt existant (services.sandbox.ingest_files,
même chemin que le bac à sable §5c) — sans dépôt manuel par un professeur.

Même patron que services.job_worker / services.indigo : thread démon +
threading.Event pour un réveil immédiat après une sauvegarde de config,
boucle qui ne meurt jamais sur une exception isolée (une panne IMAP est
journalisée dans MailIntakeConfig.last_error, jamais fatale au thread)."""
import email
import imaplib
import logging
import threading
from datetime import datetime, timezone
from email.message import Message
from email.utils import parseaddr

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import MailIntakeConfig, ScanBatch
from . import sandbox as sandbox_service
from .file_sniff import sniff_file
from .pipeline import process_batch

logger = logging.getLogger(__name__)

_wake = threading.Event()
_started = False
_DEFAULT_POLL_S = 120


def _config(db: Session) -> MailIntakeConfig | None:
    return db.get(MailIntakeConfig, "default")


def _extract_attachments(msg: Message) -> list[tuple[str, str, bytes]]:
    """[(filename, ext, content)] des pièces jointes reconnues (PDF/JPEG/PNG/
    HEIC, par signature d'octets — jamais le Content-Type déclaré par le
    mail, cf. file_sniff)."""
    out: list[tuple[str, str, bytes]] = []
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get("Content-Disposition") or "").lower()
        if not filename and "attachment" not in disposition:
            continue
        content = part.get_payload(decode=True)
        if not content:
            continue
        sniffed = sniff_file(content)
        if sniffed is None:
            continue
        ext, _mime = sniffed
        out.append((filename or f"mail-{i}{ext}", ext, content))
    return out


def _run_pipeline(batch_id: str) -> None:
    """Même logique que routers.scans._run_pipeline (session dédiée par lot,
    rollback AVANT réécriture de l'erreur — cf. piège except/commit sans
    rollback) : pas de BackgroundTasks ici, on est déjà hors requête HTTP,
    dans le thread démon lui-même."""
    db = SessionLocal()
    try:
        batch = db.get(ScanBatch, batch_id)
        process_batch(db, batch)
    except Exception as e:
        db.rollback()
        batch = db.get(ScanBatch, batch_id)
        batch.error = str(e)
        db.commit()
    finally:
        db.close()


def poll_once(db: Session) -> dict:
    """Une relève : connecte, récupère les messages jamais vus (UID >
    last_uid), en extrait les pièces jointes reconnues, les dépose via le
    même chemin que le bac à sable. Ne lève jamais — une panne DE RELÈVE
    (connexion, recherche, lecture) est journalisée dans cfg.last_error.

    La connexion IMAP reste ouverte jusqu'après le commit de l'ingestion :
    la suppression des mails traités (cfg.delete_after_import) ne doit
    jamais s'exécuter avant que leur contenu soit en sécurité en base, et
    un échec de suppression (connexion coupée entre-temps, etc.) ne doit
    jamais faire perdre les lots déjà créés — c'est pourquoi elle est isolée
    dans son propre try/except, après le commit."""
    cfg = _config(db)
    if not cfg or not cfg.active or not cfg.host:
        return {"skipped": True}

    allowlist = {a.strip().lower() for a in (cfg.sender_allowlist_json or []) if a.strip()}
    recognized: list[tuple[str, str, bytes]] = []
    fetched_uids: list[int] = []
    max_uid = cfg.last_uid
    imap: imaplib.IMAP4_SSL | None = None
    try:
        imap = imaplib.IMAP4_SSL(cfg.host, cfg.port)
        imap.login(cfg.username, cfg.encrypted_password)
        imap.select(cfg.folder)
        typ, data = imap.uid("search", None, f"UID {cfg.last_uid + 1}:*")
        if typ != "OK":
            raise RuntimeError(f"Recherche IMAP échouée : {typ}")
        uids = sorted({int(u) for u in (data[0] or b"").split() if int(u) > cfg.last_uid})
        for u in uids:
            typ, msg_data = imap.uid("fetch", str(u), "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            max_uid = max(max_uid, u)
            fetched_uids.append(u)
            if allowlist:
                _, sender = parseaddr(msg.get("From", ""))
                if sender.strip().lower() not in allowlist:
                    continue
            recognized.extend(_extract_attachments(msg))
    except Exception as e:
        logger.exception("Relève IMAP échouée")
        cfg.last_error = str(e)
        cfg.last_checked_at = datetime.now(timezone.utc)
        db.commit()
        _logout(imap)
        return {"error": str(e)}

    batch_ids: list[str] = []
    if recognized:
        result = sandbox_service.ingest_files(db, recognized, uploaded_by=None)
        batch_ids = result["batch_ids"]

    cfg.last_uid = max_uid
    cfg.last_checked_at = datetime.now(timezone.utc)
    cfg.last_error = None
    db.commit()

    if cfg.delete_after_import and fetched_uids:
        try:
            uid_set = ",".join(str(u) for u in fetched_uids)
            imap.uid("store", uid_set, "+FLAGS", "(\\Deleted)")
            imap.expunge()
        except Exception:
            # les lots sont déjà commités : un mail non supprimé est sans
            # conséquence (il ne sera pas retraité, cf. watermark last_uid),
            # jamais une raison d'annuler l'import déjà réussi.
            logger.exception("Suppression des mails traités échouée (import déjà validé)")

    _logout(imap)

    for batch_id in batch_ids:
        _run_pipeline(batch_id)

    return {"batch_ids": batch_ids, "messages_seen": len(fetched_uids)}


def _logout(imap: "imaplib.IMAP4_SSL | None") -> None:
    if imap is None:
        return
    try:
        imap.logout()
    except Exception:
        pass


def test_connection(host: str, port: int, username: str, password: str,
                    folder: str) -> str | None:
    """Connexion+login+SELECT synchrones, sans toucher la config — utilisé
    par l'endpoint « Tester la connexion ». Retourne None si OK, sinon le
    message d'erreur."""
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        try:
            imap.login(username, password)
            typ, _data = imap.select(folder)
            if typ != "OK":
                return f"Dossier « {folder} » introuvable"
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as e:
        return str(e)
    return None


def wake() -> None:
    """Réveille immédiatement la boucle de relève (appelé après une
    sauvegarde de config activée), sans attendre l'intervalle courant."""
    _wake.set()


def _loop() -> None:
    while True:
        db = SessionLocal()
        try:
            cfg = _config(db)
            interval = cfg.poll_interval_s if cfg else _DEFAULT_POLL_S
        finally:
            db.close()
        _wake.wait(timeout=max(10, interval))
        _wake.clear()
        db = SessionLocal()
        try:
            poll_once(db)
        except Exception:
            logger.exception("Boucle de relève mail interrompue par une erreur")
        finally:
            db.close()


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="mathprint-mail-intake").start()

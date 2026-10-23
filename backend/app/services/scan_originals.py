"""Scans déposés conservés TELS QUELS pendant 30 jours.

Le dépôt remplace le fichier reçu par des pages recalées et blanchies (cf.
services.scan_intake) : sans copie de l'original, impossible de savoir après
coup si une anomalie d'image vient du copieur ou de Kiro & Nina, ni de relancer
une correction supprimée par erreur (cas réel : 29 copies d'un contrôle, fichier
du copieur introuvable). Chaque fichier reçu — bac à sable, dépôt ciblé,
réception par mail — est donc copié octet pour octet dans data/scans/originals,
indépendamment des corrections (supprimer une correction ne l'efface pas), puis
purgé au-delà de RETENTION_DAYS : ce sont des copies d'élèves.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

RETENTION_DAYS = 30
_NAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{10}-[A-Za-z0-9._-]{1,80}$")


def originals_dir() -> Path:
    return settings.data_dir / "scans" / "originals"


def prune(now: float | None = None) -> int:
    """Supprime les originaux de plus de RETENTION_DAYS jours ; renvoie le nombre."""
    d = originals_dir()
    if not d.exists():
        return 0
    limit = (now or time.time()) - RETENTION_DAYS * 86400
    n = 0
    for f in d.iterdir():
        if f.is_file() and f.stat().st_mtime < limit:
            f.unlink(missing_ok=True)
            n += 1
    return n


def archive(content: bytes, filename: str) -> Path | None:
    """Copie octet pour octet d'un fichier déposé. Best-effort : un échec
    d'archivage ne doit JAMAIS bloquer un dépôt (journalisé, None)."""
    try:
        d = originals_dir()
        d.mkdir(parents=True, exist_ok=True)
        prune()
        sha = hashlib.sha256(content).hexdigest()[:10]
        existing = next(d.glob(f"*-{sha}-*"), None)
        if existing is not None:                 # même fichier redéposé : une copie suffit
            return existing
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "scan").name)[:80] or "scan"
        path = d / f"{stamp}-{sha}-{safe}"
        path.write_bytes(content)
        return path
    except Exception:  # noqa: BLE001
        log.exception("archivage du scan original impossible")
        return None


def list_originals() -> list[dict]:
    d = originals_dir()
    if not d.exists():
        return []
    out = []
    for f in sorted(d.iterdir(), reverse=True):
        if not f.is_file() or not _NAME_RE.match(f.name):
            continue
        st = f.stat()
        created = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        out.append({"name": f.name, "filename": f.name.split("-", 3)[3],
                    "size": st.st_size, "created": created.isoformat(),
                    "expires": (created + timedelta(days=RETENTION_DAYS)).isoformat()})
    return out


def path_of(name: str) -> Path | None:
    """Chemin d'un original par son nom stocké — None si le nom n'est pas un
    nom d'archive (aucune traversée de répertoire possible)."""
    if not _NAME_RE.match(name or ""):
        return None
    p = originals_dir() / name
    return p if p.is_file() else None

"""Journal des erreurs serveur (500) — capture des tracebacks pour diagnostic.

Écrit dans un FICHIER, jamais en base : une 500 vient souvent d'une transaction
avortée (cf. piège SQLite/Postgres), et écrire l'erreur en base échouerait
aussi. Une ligne JSON par erreur, fichier plafonné aux N dernières. Lu par la
page Paramètres → Journaux — indispensable en prod NAS où la console uvicorn
n'est pas sous les yeux du professeur.
"""
import json
import threading
from datetime import datetime, timezone

from ..config import settings

_LOCK = threading.Lock()
_MAX_ENTRIES = 300


def _path():
    d = settings.data_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "errors.log"


def record(*, method: str, path: str, error: str, traceback: str) -> None:
    """Ajoute une erreur au journal (best-effort : ne doit JAMAIS relancer, sinon
    on masquerait l'erreur d'origine par une erreur de journalisation)."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method, "path": path,
        "error": error, "traceback": traceback,
    }
    try:
        with _LOCK:
            p = _path()
            lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
            lines.append(json.dumps(entry, ensure_ascii=False))
            p.write_text("\n".join(lines[-_MAX_ENTRIES:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def tail(n: int = 50) -> list[dict]:
    """Les `n` erreurs les plus récentes d'abord."""
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(out))[:n]


def clear() -> None:
    with _LOCK:
        p = _path()
        if p.exists():
            p.unlink()

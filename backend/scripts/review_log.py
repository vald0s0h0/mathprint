"""Journal de la revue humaine : lister, exporter en dossier, marquer traité.

Chaque correction du professeur (revue avant correction ou Affiner) a laissé une ligne
`review_events` et un dossier data/review_log/<jour>/<heure>-<id>/ (captures du scan,
squelettes avant/après, event.json). Ce script sert au traitement en lot.

Usage (depuis la racine, base locale) :
    backend/.venv/bin/python backend/scripts/review_log.py list [--all] [--category ocr]
    backend/.venv/bin/python backend/scripts/review_log.py export > dossier.tar.gz
    backend/.venv/bin/python backend/scripts/review_log.py mark ID [ID…] --note "corrigé par …"

Sur le NAS (production), mêmes commandes dans le conteneur :
    ssh … 'sudo -n /usr/local/bin/docker exec -i mathprint-api-1 python - list' < backend/scripts/review_log.py
    ssh … 'sudo -n /usr/local/bin/docker exec -i mathprint-api-1 python - export' < backend/scripts/review_log.py > dossier.tar.gz
    ssh … 'sudo -n /usr/local/bin/docker exec -i mathprint-api-1 python - mark ID --note "…"' < backend/scripts/review_log.py

`export` produit une archive : index.json, RESUME.md (un tableau lisible), et le
dossier de chaque événement ouvert (ou de tous avec --all).
"""
import argparse
import io
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

if "__file__" in globals():
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ReviewEvent  # noqa: E402
from app.services.review_log import event_dict  # noqa: E402


def _short(value, n=40) -> str:
    s = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return s if len(s) <= n else s[:n - 1] + "…"


def _seen(side: dict) -> str:
    syn = (side or {}).get("synthesis") or {}
    if syn.get("cells") is not None:
        return " | ".join(c["text"] for c in syn["cells"])
    if syn.get("boxes") is not None:
        return ",".join(str(i) for i, b in enumerate(syn["boxes"]) if b["on"])
    if syn.get("links") is not None:
        return f"{len(syn['links'])} liaison(s)"
    return str(syn.get("text") or "")


def _score(side: dict) -> str:
    d = (side or {}).get("decision") or {}
    return f"{d.get('score')}/{d.get('max_score')}" if d else "—"


def _query(db, args):
    q = db.query(ReviewEvent)
    if not args.all:
        q = q.filter(ReviewEvent.status == "open")
    if args.category:
        q = q.filter(ReviewEvent.category == args.category)
    return q.order_by(ReviewEvent.created_at).all()


def cmd_list(db, args):
    rows = _query(db, args)
    for ev in rows:
        print(f"{ev.id[:8]}  {ev.created_at:%Y-%m-%d %H:%M}  {ev.stage:<14} {ev.category:<11} "
              f"ex.{ev.sequence} {ev.response_type:<13} {_short(_seen(ev.before_json), 24):<24} -> "
              f"{_short(_seen(ev.after_json), 24):<24} note {_score(ev.before_json)} -> {_score(ev.after_json)}"
              f"  [{ev.status}]")
    print(f"{len(rows)} événement(s)", file=sys.stderr)


def _add_bytes(tar, name: str, data: bytes):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info, io.BytesIO(data))


def cmd_export(db, args):
    rows = _query(db, args)
    index = []
    lines = ["# Revue humaine : événements à traiter", "",
             f"Exporté le {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC — {len(rows)} événement(s).", "",
             "| id | date | étape | catégorie | ex. | type | lu avant | après | note avant | après | dossier |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for ev in rows:
            folder = f"events/{ev.id[:8]}"
            src = settings.data_dir / ev.folder if ev.folder else None
            files = []
            if src and src.is_dir():
                for f in sorted(src.iterdir()):
                    if f.is_file():
                        tar.add(str(f), arcname=f"{folder}/{f.name}")
                        files.append(f.name)
            else:
                _add_bytes(tar, f"{folder}/event.json",
                           json.dumps(event_dict(ev), ensure_ascii=False, indent=2, default=str).encode())
                files.append("event.json")
            index.append({"id": ev.id, "folder": folder, "files": files, "category": ev.category,
                          "stage": ev.stage, "status": ev.status, "created_at": ev.created_at.isoformat()})
            lines.append(f"| {ev.id[:8]} | {ev.created_at:%Y-%m-%d %H:%M} | {ev.stage} | {ev.category} | "
                         f"{ev.sequence} | {ev.response_type} | `{_short(_seen(ev.before_json), 30)}` | "
                         f"`{_short(_seen(ev.after_json), 30)}` | {_score(ev.before_json)} | "
                         f"{_score(ev.after_json)} | {folder} |")
        lines += ["", "Pour marquer traité : `review_log.py mark <id> --note \"…\"`."]
        _add_bytes(tar, "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode())
        _add_bytes(tar, "RESUME.md", "\n".join(lines).encode())
    sys.stdout.buffer.write(buf.getvalue())
    print(f"{len(rows)} événement(s) exporté(s)", file=sys.stderr)


def cmd_mark(db, args):
    n = 0
    for prefix in args.ids:
        rows = db.query(ReviewEvent).filter(ReviewEvent.id.like(f"{prefix}%")).all()
        if len(rows) != 1:
            print(f"{prefix} : {len(rows)} événement(s) correspondant(s), ignoré", file=sys.stderr)
            continue
        ev = rows[0]
        ev.status = "treated"
        ev.treated_at = datetime.now(timezone.utc)
        ev.treated_note = args.note
        n += 1
    db.commit()
    print(f"{n} événement(s) marqué(s) traité(s)", file=sys.stderr)


def main(argv=None, db=None):
    p = argparse.ArgumentParser(description="Journal de la revue humaine")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "export"):
        s = sub.add_parser(name)
        s.add_argument("--all", action="store_true", help="inclure les événements déjà traités")
        s.add_argument("--category", help="ocr | cv_boxes | cv_matching | cv_drawing | grading")
    m = sub.add_parser("mark")
    m.add_argument("ids", nargs="+", help="identifiants (préfixe de 8 caractères suffit)")
    m.add_argument("--note", default="", help="ce qui a été fait")
    args = p.parse_args(argv)
    own = db is None
    db = db or SessionLocal()
    try:
        {"list": cmd_list, "export": cmd_export, "mark": cmd_mark}[args.cmd](db, args)
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    main()

"""Scans déposés conservés tels quels 30 jours (services.scan_originals)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import scan_originals


def test_archive_keeps_the_exact_bytes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    content = b"%PDF-1.4 scan du copieur"
    p = scan_originals.archive(content, "20261016120936.pdf")
    assert p.read_bytes() == content                        # octet pour octet
    assert p.name.endswith("-20261016120936.pdf")
    assert scan_originals.archive(content, "copie.pdf") == p   # même fichier : une copie
    files = scan_originals.list_originals()
    assert [f["filename"] for f in files] == ["20261016120936.pdf"]
    assert scan_originals.path_of(files[0]["name"]) == p


def test_originals_older_than_30_days_are_purged(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    old = scan_originals.archive(b"vieux", "a.pdf")
    recent = scan_originals.archive(b"recent", "b.pdf")
    t = time.time() - 31 * 86400
    os.utime(old, (t, t))
    assert scan_originals.prune() == 1
    assert not old.exists() and recent.exists()


def test_path_of_rejects_anything_but_an_archive_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    scan_originals.archive(b"x", "a.pdf")
    assert scan_originals.path_of("../../runtime_secrets.env") is None
    assert scan_originals.path_of("20261016-120000-0123456789-../x") is None


def test_archive_failure_never_blocks_the_deposit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "fichier")
    (tmp_path / "fichier").write_text("pas un dossier")     # mkdir impossible
    assert scan_originals.archive(b"x", "a.pdf") is None


def test_sandbox_deposit_archives_the_received_file(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.services import sandbox

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sandbox.worker_cv, "raster_any", lambda _path: [])
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    sandbox.ingest_files(db, [("20261016120936.pdf", ".pdf", b"%PDF scan brut")], "prof")
    files = scan_originals.list_originals()
    assert len(files) == 1
    assert scan_originals.path_of(files[0]["name"]).read_bytes() == b"%PDF scan brut"

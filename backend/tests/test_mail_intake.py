"""Tests : relève automatique des scans par mail (IMAP). Réutilise le même
chemin d'ingestion que le bac à sable (services.sandbox.ingest_files) — cf.
test_scan_intake.py pour la génération d'une vraie page MathPrint signée
(PDF -> raster -> QR OpenCV -> HMAC), reprise ici telle quelle. Seule la
« tuyauterie » IMAP (extraction des pièces jointes, liste blanche
d'expéditeurs, watermark last_uid) est spécifique à ce module."""
import io
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401 (enregistre les tables sur Base)
from app.config import settings as app_settings
from app.db import Base
from app.models import Assessment, Copy, DocumentPage, MailIntakeConfig, ScanBatch, SchoolClass, Student
from app.services import mail_intake, pdfgen
from app.services.security import sign_page


@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_signed_page(db):
    """Un sujet avec une page dont le QR est signé — même montage que
    test_scan_intake.test_bulk_upload_recognizes_signed_pages_end_to_end."""
    cls = SchoolClass(name="5A", grade_level="5e")
    db.add(cls)
    db.flush()
    stu = Student(class_id=cls.id, name="Alex Martin", llm_pseudonym="p1")
    db.add(stu)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle 1")
    db.add(a)
    db.flush()
    copy = Copy(assessment_id=a.id, student_id=stu.id)
    db.add(copy)
    db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1)
    db.add(page)
    db.flush()
    payload = sign_page(page.id)
    page.qr_payload = payload
    page.hmac_version = "2"
    db.commit()
    return a, payload


def _marked_pdf(payload: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdfgen.PAGE_W, pdfgen.PAGE_H))
    pdfgen._draw_markers(c, payload)
    pdfgen._draw_header(c, "MARTIN Alex", "5A", "Contrôle 1", "control", "10/08/2026")
    c.showPage()
    c.save()
    return buf.getvalue()


def _mail_bytes(sender: str, attachments: list[tuple[str, bytes]]) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = "scans@ecole.example"
    msg["Subject"] = "Scan ADF"
    msg.attach(MIMEText("Copies scannées en pièce jointe.", "plain"))
    for filename, content in attachments:
        part = MIMEApplication(content, _subtype="octet-stream")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    return msg.as_bytes()


class _FakeImap:
    """Double minimal d'imaplib.IMAP4_SSL : uid("search"/"fetch"/"store", ...),
    login/select/expunge/logout. Ne filtre pas lui-même par UID — c'est le
    rôle de poll_once (watermark last_uid), reproduisant fidèlement ce que
    le vrai serveur ferait après une recherche `UID N:*`. Trace les UID
    marqués \\Deleted et les appels expunge pour vérifier la suppression."""

    def __init__(self, messages: dict[int, bytes]):
        self._messages = messages
        self.deleted_uids: list[str] = []
        self.expunge_calls = 0

    def login(self, _user, _password):
        return "OK", [b"done"]

    def select(self, _folder):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            ids = " ".join(str(u) for u in sorted(self._messages)).encode()
            return "OK", [ids]
        if command == "fetch":
            u = int(args[0])
            raw = self._messages.get(u)
            if raw is None:
                return "NO", [None]
            return "OK", [(b"1 (RFC822 {%d}" % len(raw), raw)]
        if command == "store":
            self.deleted_uids.append(args[0])
            return "OK", [b"done"]
        raise AssertionError(f"commande IMAP inattendue : {command}")

    def expunge(self):
        self.expunge_calls += 1
        return "OK", [None]

    def logout(self):
        return "BYE", [b"logged out"]


def _active_config(db, allowlist=None, delete_after_import=True) -> MailIntakeConfig:
    cfg = MailIntakeConfig(id="default", host="imap.ecole.example", port=993,
                           username="scans@ecole.example", encrypted_password="secret",
                           folder="INBOX", poll_interval_s=120,
                           sender_allowlist_json=allowlist or [], active=True,
                           delete_after_import=delete_after_import)
    db.add(cfg)
    db.commit()
    return cfg


def test_recognized_attachment_creates_batch(mock_db, tmp_path, monkeypatch):
    db = mock_db
    assessment, payload = _seed_signed_page(db)
    cfg = _active_config(db)

    pdf_bytes = _marked_pdf(payload)
    fake = _FakeImap({1: _mail_bytes("scanner@ecole.example", [("scan.pdf", pdf_bytes)])})
    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    ran_pipeline = []
    monkeypatch.setattr(mail_intake, "_run_pipeline", lambda batch_id: ran_pipeline.append(batch_id))

    result = mail_intake.poll_once(db)

    assert result["batch_ids"] and ran_pipeline == result["batch_ids"]
    batches = db.query(ScanBatch).filter_by(assessment_id=assessment.id).all()
    assert len(batches) == 1
    assert batches[0].source_file_id is not None
    db.refresh(cfg)
    assert cfg.last_uid == 1
    assert cfg.last_error is None
    # delete_after_import est activé par défaut : le mail traité est marqué
    # \Deleted puis purgé, mais SEULEMENT après que le lot soit en base.
    assert fake.deleted_uids == ["1"]
    assert fake.expunge_calls == 1


def test_delete_after_import_disabled_keeps_the_email(mock_db, tmp_path, monkeypatch):
    db = mock_db
    _assessment, payload = _seed_signed_page(db)
    _active_config(db, delete_after_import=False)

    pdf_bytes = _marked_pdf(payload)
    fake = _FakeImap({1: _mail_bytes("scanner@ecole.example", [("scan.pdf", pdf_bytes)])})
    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(mail_intake, "_run_pipeline", lambda batch_id: None)

    result = mail_intake.poll_once(db)

    assert result["batch_ids"]
    assert fake.deleted_uids == []
    assert fake.expunge_calls == 0


def test_inactive_config_never_touches_imap(mock_db, monkeypatch):
    db = mock_db
    cfg = MailIntakeConfig(id="default", host="imap.ecole.example", active=False)
    db.add(cfg)
    db.commit()

    def _boom(*_a, **_k):
        raise AssertionError("IMAP ne doit pas être contacté si la config est inactive")
    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", _boom)

    result = mail_intake.poll_once(db)
    assert result == {"skipped": True}


def test_sender_outside_allowlist_is_ignored(mock_db, tmp_path, monkeypatch):
    db = mock_db
    _assessment, payload = _seed_signed_page(db)
    cfg = _active_config(db, allowlist=["prof@ecole.example"])

    pdf_bytes = _marked_pdf(payload)
    fake = _FakeImap({1: _mail_bytes("inconnu@ailleurs.example", [("scan.pdf", pdf_bytes)])})
    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(mail_intake, "_run_pipeline", lambda batch_id: None)

    result = mail_intake.poll_once(db)

    assert result["batch_ids"] == []
    assert db.query(ScanBatch).count() == 0
    db.refresh(cfg)
    # avancé quand même : un message hors liste blanche ne doit pas être
    # rescanné indéfiniment à chaque relève
    assert cfg.last_uid == 1


def test_unrecognized_attachment_is_ignored_without_error(mock_db, tmp_path, monkeypatch):
    db = mock_db
    _active_config(db)

    fake = _FakeImap({1: _mail_bytes("scanner@ecole.example", [("notes.txt", b"pas un scan")])})
    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(mail_intake, "_run_pipeline", lambda batch_id: None)

    result = mail_intake.poll_once(db)

    assert result["batch_ids"] == []
    assert db.query(ScanBatch).count() == 0

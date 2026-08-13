"""Contrat serveur du connecteur local : auth, profils et PDF déterministes."""
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (Assessment, ConnectorPrintJob, Copy, DocumentPage,
                        PrintConnector, Printer, SchoolClass, Student, User)
from app.routers import connectors, printing
from app.services.security import hash_password


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_login_heartbeat_registers_only_the_connectors_local_printers():
    db = _db()
    try:
        user = User(email="prof@example.fr", password_hash=hash_password("secret-pass"))
        db.add(user); db.commit()
        result = connectors.login_connector(connectors.ConnectorLoginIn(
            email=user.email, password="secret-pass",
            installation_id="11111111-2222-3333-4444-555555555555",
            device_name="MacBook du professeur", platform="macos",
            arch="aarch64", app_version="1.0.0"), db)
        connector = db.get(PrintConnector, result["connector"]["id"])
        connectors.heartbeat(connectors.HeartbeatIn(
            app_version="1.0.0", printers=[
                connectors.LocalPrinterIn(name="Canon USB", is_default=True),
                connectors.LocalPrinterIn(name="Epson salle", is_default=False),
            ]), connector, db)

        rows = printing.list_printers(db, user)
        assert [(p["display_name"], p["device_name"], p["status"])
                for p in rows["local"]] == [
            ("Canon USB", "MacBook du professeur", "online"),
            ("Epson salle", "MacBook du professeur", "online"),
        ]
        assert all(p["source"] == "connector_local" for p in rows["local"])
    finally:
        db.close()


def test_manual_duplex_jobs_are_split_then_physically_reversed(tmp_path, monkeypatch):
    db = _db()
    try:
        monkeypatch.setattr(printing.settings, "data_dir", tmp_path)
        school_class = SchoolClass(name="5A", grade_level="5e")
        user = User(email="print@example.fr", password_hash="x")
        db.add_all([school_class, user]); db.flush()
        assessment = Assessment(class_id=school_class.id, title="Chemin papier",
                                status="ready", duplex=True)
        db.add(assessment); db.flush()
        student = Student(class_id=school_class.id, name="Élève",
                          order_index=0, llm_pseudonym="connector-paper")
        db.add(student); db.flush()
        copy = Copy(assessment_id=assessment.id, student_id=student.id, total_pages=3)
        db.add(copy); db.flush()
        for page_no in (1, 2, 3):
            db.add(DocumentPage(copy_id=copy.id, page_no=page_no))

        connector = PrintConnector(
            user_id=user.id, installation_id="connector-paper-path-0001",
            name="PC professeur", token_hash="token", platform="windows",
            active=True)
        db.add(connector); db.flush()
        profile = Printer(
            name="connector-profile", protocol="connector", active=True,
            pickup_reverse_order=True, output_reverse_order=False,
            capabilities_json={"connector_id": connector.id,
                               "native_name": "Canon USB"})
        db.add(profile); db.commit()

        source = (tmp_path / "assessments" / assessment.id / "generated" /
                  "subject_batch.pdf")
        source.parent.mkdir(parents=True)
        writer = PdfWriter()
        for width in (101, 102, 103):
            writer.add_blank_page(width=width, height=200)
        with source.open("wb") as stream:
            writer.write(stream)

        recto_result = printing.print_file(printing.PrintIn(
            assessment_id=assessment.id, file="subject_batch.pdf",
            printer=profile.name, pass_side="recto"), db, user)
        recto = db.get(ConnectorPrintJob, recto_result["job_id"])
        recto_path = tmp_path / recto.document_relpath
        recto_pdf = PdfReader(str(recto_path))
        # Extraction intra-copie (1,3), puis compensation de prélèvement (3,1).
        assert [int(p.mediabox.width) for p in recto_pdf.pages] == [103, 101]
        assert recto.options_json["scale"] == "none"
        assert recto.options_json["duplex"] is False
        assert recto.options_json["reverse_applied_to_pdf"] is True

        connectors.claim_job(connector, db)
        connectors.report_job(recto.id, connectors.JobResultIn(
            status="submitted", spool_job_id="windows-42"), connector, db)
        assert not recto_path.exists()
        db.refresh(assessment)
        assert assessment.status == "ready"  # jamais imprimé avant les versos

        verso_result = printing.print_file(printing.PrintIn(
            assessment_id=assessment.id, file="subject_batch.pdf",
            printer=profile.name, pass_side="verso"), db, user)
        verso = db.get(ConnectorPrintJob, verso_result["job_id"])
        verso_pdf = PdfReader(str(tmp_path / verso.document_relpath))
        assert [int(p.mediabox.width) for p in verso_pdf.pages] == [102]
        connectors.claim_job(connector, db)
        connectors.report_job(verso.id, connectors.JobResultIn(
            status="submitted", spool_job_id="windows-43"), connector, db)
        db.refresh(assessment)
        assert assessment.status == "printed"
    finally:
        db.close()


def test_logout_cancels_waiting_jobs(tmp_path, monkeypatch):
    db = _db()
    try:
        monkeypatch.setattr(printing.settings, "data_dir", tmp_path)
        user = User(email="logout@example.fr", password_hash="x")
        connector = PrintConnector(
            user_id="pending", installation_id="logout-connector-0001",
            name="Mac", token_hash="logout-token", active=True)
        db.add(user); db.flush()
        connector.user_id = user.id
        db.add(connector); db.flush()
        profile = Printer(name="logout-printer", protocol="connector", active=True,
                          capabilities_json={"connector_id": connector.id,
                                             "native_name": "USB"})
        db.add(profile); db.flush()
        job = printing._queue_connector_job(
            db=db, user=user, profile=profile, title="Test",
            file_name="printer_test.pdf", pass_side="all", document=b"%PDF-test",
            assessment_id=None, copies=1, duplex=False, reverse=False)
        connectors.logout_connector(connector, db)
        db.refresh(job); db.refresh(profile)
        assert job.status == "cancelled"
        assert not (tmp_path / job.document_relpath).exists()
        assert profile.active is False
        assert connector.active is False
    finally:
        db.close()


def test_switching_account_never_delivers_the_previous_users_claimed_job():
    db = _db()
    try:
        first = User(email="first@example.fr", password_hash=hash_password("first-secret"))
        second = User(email="second@example.fr", password_hash=hash_password("second-secret"))
        db.add_all([first, second]); db.flush()
        connector = PrintConnector(
            user_id=first.id, installation_id="shared-installation-0001",
            name="Poste partagé", token_hash="old-token", active=True)
        printer = Printer(name="shared-printer", protocol="connector", active=True)
        db.add_all([connector, printer]); db.flush()
        job = ConnectorPrintJob(
            connector_id=connector.id, user_id=first.id, printer_id=printer.id,
            title="Sujet du premier compte", native_printer_name="USB",
            status="claimed", document_relpath="connector_jobs/old.pdf",
            document_sha256="deadbeef", document_size=42)
        db.add(job); db.commit()

        connectors.login_connector(connectors.ConnectorLoginIn(
            email=second.email, password="second-secret",
            installation_id=connector.installation_id,
            device_name="Poste partagé", platform="windows",
            arch="x86_64", app_version="1.0.0"), db)

        db.refresh(connector); db.refresh(job)
        assert connector.user_id == second.id
        assert job.status == "uncertain"
        assert connectors.claim_job(connector, db) == {"job": None}
    finally:
        db.close()

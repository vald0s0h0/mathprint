"""Ordre physique ADF/imprimante : invariants indépendants des pilotes réels."""
import sys
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.db import Base
from app.models import (Assessment, Copy, DocumentPage, Printer, ScanBatch,
                        ScannedPage, SchoolClass, Student, User)
from app.routers import printing
from app.routers.printing import (PrinterPreferencesIn, _effective_reverse,
                                  PrintIn, _lp_command, _subject_pass_pdf,
                                  _test_pages_pdf, list_printers,
                                  update_printer_preferences)
from app.routers.scans import _business_steps
from app.services.pipeline import _copies_in_scan_order
from app.services.sandbox import scan_filename_key


def test_adf_filenames_use_natural_numeric_order():
    names = ["scan-10.jpg", "scan-2.jpg", "scan-001.jpg", "scan-20260811-093001.jpg"]
    assert sorted(names, key=scan_filename_key) == [
        "scan-001.jpg", "scan-2.jpg", "scan-10.jpg", "scan-20260811-093001.jpg"]


def test_pickup_output_and_adf_inversions_are_composed_by_xor():
    profile = Printer(name="P", pickup_reverse_order=False,
                      output_reverse_order=False, adf_reverse_order=False)
    assert _effective_reverse(profile, "subject_batch.pdf") is False
    assert _effective_reverse(profile, "correction_overlay.pdf") is False
    profile.pickup_reverse_order = True
    assert _effective_reverse(profile, "subject_batch.pdf") is True
    assert _effective_reverse(profile, "correction_overlay.pdf") is True
    profile.output_reverse_order = True
    # Double inversion imprimante : prélèvement et bac s'annulent.
    assert _effective_reverse(profile, "subject_batch.pdf") is False
    assert _effective_reverse(profile, "correction_overlay.pdf") is False
    profile.adf_reverse_order = True
    assert _effective_reverse(profile, "subject_batch.pdf") is False
    assert _effective_reverse(profile, "correction_overlay.pdf") is True


def test_lp_command_forces_collation_scale_duplex_and_order(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-test")
    cmd = _lp_command(printer="Salle", path=pdf, copies=3, duplex=True,
                      reverse=True, network=None, local_names={"Salle"})
    joined = " ".join(cmd)
    assert "-n 3" in joined
    assert "Collate=True" in joined
    assert "print-scaling=none" in joined
    assert "outputorder=reverse" in joined
    assert "sides=two-sided-long-edge" in joined
    assert cmd[-2:] == ["Salle", str(pdf)]


def test_diagnostic_pdf_has_two_pages():
    from io import BytesIO
    assert len(PdfReader(BytesIO(_test_pages_pdf())).pages) == 2


def test_overlay_step_changes_only_after_successful_print():
    progress = {name: {} for name in (
        "split", "ocr_complete", "ocr_confirmed", "graded", "finalized", "overlay_ready")}
    pending = _business_steps("overlay_ready", progress, 0, 0, None, False)[-1]
    printed = _business_steps("overlay_ready", progress, 0, 0, None, True)[-1]
    assert pending == {"phase": "overlay_print", "label": "Overlay à imprimer",
                       "state": "blue"}
    assert printed == {"phase": "overlay_print", "label": "Overlay imprimé",
                       "state": "green"}


def test_successful_overlay_print_marks_batch_as_printed(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        school_class = SchoolClass(name="5A", grade_level="5e")
        user = User(email="print@x.fr", password_hash="x")
        db.add_all([school_class, user]); db.flush()
        assessment = Assessment(class_id=school_class.id, title="Overlay", status="finalized")
        db.add(assessment); db.flush()
        batch = ScanBatch(assessment_id=assessment.id, status="overlay_ready",
                          overlay_printed=False)
        printer = Printer(name="Salle", protocol="cups", active=True)
        db.add_all([batch, printer]); db.commit()

        overlay = (tmp_path / "assessments" / assessment.id / "overlays"
                   / "correction_overlay.pdf")
        overlay.parent.mkdir(parents=True)
        overlay.write_bytes(b"%PDF-test")
        monkeypatch.setattr(printing.settings, "data_dir", tmp_path)
        monkeypatch.setattr(printing, "_local_printers", lambda: [
            {"name": "Salle", "source": "cups_local", "status": "idle"}])
        monkeypatch.setattr(printing.subprocess, "run", lambda *args, **kwargs:
                            SimpleNamespace(returncode=0, stdout="job 42", stderr=""))

        result = printing.print_file(PrintIn(
            assessment_id=assessment.id, file="correction_overlay.pdf",
            printer="Salle"), db, user)
        db.refresh(batch)
        assert result["ok"] is True
        assert batch.overlay_printed is True
    finally:
        db.close()


def test_application_default_is_independent_and_exclusive(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(printing, "_local_printers", lambda: [
        {"name": "Bureau", "source": "cups_local", "status": "idle", "default": False},
        {"name": "Salle", "source": "cups_local", "status": "idle", "default": True},
    ])
    try:
        user = User(email="defaults@x.fr", password_hash="x")
        db.add(user); db.commit()
        initial = list_printers(db, user)
        system_default = next(p for p in initial["local"] if p["name"] == "Salle")
        assert system_default["system_default"] is True
        assert system_default["app_default"] is False
        update_printer_preferences(
            PrinterPreferencesIn(name="Bureau", app_default=True, duplex=True,
                                 pickup_reverse_order=True,
                                 output_reverse_order=True), db)
        update_printer_preferences(
            PrinterPreferencesIn(name="Salle", app_default=True), db)
        profiles = db.query(Printer).order_by(Printer.name).all()
        assert [(p.name, p.app_default, p.duplex, p.pickup_reverse_order,
                 p.output_reverse_order) for p in profiles] == [
            ("Bureau", False, True, True, True),
            ("Salle", True, False, False, False),
        ]
    finally:
        db.close()


def test_manual_duplex_passes_use_page_number_inside_each_copy(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        school_class = SchoolClass(name="5A", grade_level="5e")
        db.add(school_class); db.flush()
        assessment = Assessment(class_id=school_class.id, title="Deux passes", duplex=True)
        db.add(assessment); db.flush()
        for student_index in range(2):
            student = Student(class_id=school_class.id, name=f"Élève {student_index + 1}",
                              order_index=student_index, llm_pseudonym=f"manual-{student_index}")
            db.add(student); db.flush()
            copy = Copy(assessment_id=assessment.id, student_id=student.id, total_pages=3)
            db.add(copy); db.flush()
            for page_no in (1, 2, 3):
                db.add(DocumentPage(copy_id=copy.id, page_no=page_no))
        db.commit()

        source = tmp_path / "subject_batch.pdf"
        writer = PdfWriter()
        # Largeur distincte pour vérifier précisément quelles pages survivent.
        for width in range(101, 107):
            writer.add_blank_page(width=width, height=200)
        with source.open("wb") as stream:
            writer.write(stream)

        from io import BytesIO
        rectos = PdfReader(BytesIO(_subject_pass_pdf(db, assessment.id, source, "recto")))
        versos = PdfReader(BytesIO(_subject_pass_pdf(db, assessment.id, source, "verso")))
        assert [int(p.mediabox.width) for p in rectos.pages] == [101, 103, 104, 106]
        assert [int(p.mediabox.width) for p in versos.pages] == [102, 105]
    finally:
        db.close()


def test_overlay_copy_order_follows_scanner_not_class():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        school_class = SchoolClass(name="5A", grade_level="5e")
        db.add(school_class); db.flush()
        assessment = Assessment(class_id=school_class.id, title="Ordre")
        db.add(assessment); db.flush()
        copies = []
        pages = []
        for index, name in enumerate(("Élève 1", "Élève 2", "Élève 3")):
            student = Student(class_id=school_class.id, name=name,
                              order_index=index, llm_pseudonym=f"p{index}")
            db.add(student); db.flush()
            copy = Copy(assessment_id=assessment.id, student_id=student.id)
            db.add(copy); db.flush()
            page = DocumentPage(copy_id=copy.id, page_no=1)
            db.add(page); db.flush()
            copies.append(copy); pages.append(page)
        batch = ScanBatch(assessment_id=assessment.id)
        db.add(batch); db.flush()
        # L'ADF a reçu 3, puis 1, puis 2 : cet ordre doit rester l'autorité.
        for source_index, page_index in enumerate((2, 0, 1)):
            db.add(ScannedPage(batch_id=batch.id, source_index=source_index,
                               page_id=pages[page_index].id, status="registered"))
        db.commit()
        ordered = _copies_in_scan_order(db, batch, assessment.id)
        assert [copy.id for copy in ordered] == [copies[2].id, copies[0].id, copies[1].id]
    finally:
        db.close()

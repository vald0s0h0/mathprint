"""File d'impression de l'overlay : première copie annoncée, ordre et reprise."""
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.db import Base
from app.models import (Assessment, ConnectorPrintJob, Copy, DocumentPage,
                        Printer, PrintConnector, ScanBatch, ScannedPage,
                        SchoolClass, Student, User)
from app.routers import connectors, printing
from app.routers.printing import PrintIn, overlay_print_queue, print_file
from app.services import overlay_queue
from app.services.print_connectors import utcnow

OVERLAY = "correction_overlay.pdf"


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(printing.settings, "data_dir", tmp_path)
    monkeypatch.setattr(printing, "_local_printers", lambda: [
        {"name": "Salle", "source": "cups_local", "status": "idle"}])
    user = User(email="print@x.fr", password_hash="x", role="corrector")
    db.add(user); db.commit()
    yield SimpleNamespace(db=db, user=user, root=tmp_path)
    db.close()


def _seed(world, names, *, pages_per_copy=1, duplex=False, **printer_flags):
    """Lot scanné dans l'ordre de `names` ; chaque page PDF a une largeur propre
    (100, 101, ...) qui permet de vérifier exactement ce qui part à l'impression."""
    db = world.db
    school_class = SchoolClass(name="5A", grade_level="5e")
    db.add(school_class); db.flush()
    assessment = Assessment(class_id=school_class.id, title="Contrôle",
                            status="finalized", duplex=duplex)
    db.add(assessment); db.flush()
    batch = ScanBatch(assessment_id=assessment.id, status="overlay_ready")
    db.add(batch); db.flush()
    index = 0
    for order, name in enumerate(names):
        student = Student(class_id=school_class.id, name=name, order_index=order,
                          llm_pseudonym=f"p{order}")
        db.add(student); db.flush()
        copy = Copy(assessment_id=assessment.id, student_id=student.id)
        db.add(copy); db.flush()
        for page_no in range(1, pages_per_copy + 1):
            page = DocumentPage(copy_id=copy.id, page_no=page_no)
            db.add(page); db.flush()
            db.add(ScannedPage(batch_id=batch.id, source_index=index, page_id=page.id,
                               status="registered"))
            index += 1
    db.add(Printer(name="Salle", protocol="cups", active=True, **printer_flags))
    db.commit()
    _write_overlay(world, assessment.id, index)
    return assessment, batch


def _write_overlay(world, assessment_id, pages):
    path = (world.root / "assessments" / assessment_id / "overlays" / OVERLAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for i in range(pages):
        writer.add_blank_page(width=100 + i, height=200)
    with path.open("wb") as stream:
        writer.write(stream)


def _print(world, assessment, monkeypatch, *, returncode=0, **fields):
    """Imprime via `lp` simulé ; retourne les largeurs de pages du PDF envoyé."""
    sent = {}

    def fake_run(cmd, **_kwargs):
        sent["widths"] = [int(page.mediabox.width) for page in PdfReader(cmd[-1]).pages]
        sent["cmd"] = cmd
        return SimpleNamespace(returncode=returncode, stdout="job 7", stderr="panne")

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    print_file(PrintIn(assessment_id=assessment.id, file=OVERLAY, printer="Salle", **fields),
               world.db, world.user)
    return sent


def _queue(world, assessment, printer="Salle"):
    return overlay_print_queue(assessment.id, printer, world.db, world.user)


def test_first_entry_is_the_first_scanned_copy(world):
    assessment, _ = _seed(world, ["Zoé", "Aïcha", "Moussa"])
    queue = _queue(world, assessment)
    assert [(e["position"], e["student"]) for e in queue["entries"]] == [
        (1, "Zoé"), (2, "Aïcha"), (3, "Moussa")]
    assert queue["last_print"] is None and queue["blocked"] is None


def test_adf_reversal_changes_the_announced_first_copy(world):
    assessment, _ = _seed(world, ["Zoé", "Aïcha", "Moussa"], adf_reverse_order=True)
    assert [e["student"] for e in _queue(world, assessment)["entries"]] == [
        "Moussa", "Aïcha", "Zoé"]


@pytest.mark.parametrize("output_reverse", [False, True])
def test_full_print_follows_the_pile_whatever_the_output_tray(world, monkeypatch, output_reverse):
    assessment, _ = _seed(world, ["A", "B", "C"], output_reverse_order=output_reverse)
    assert _print(world, assessment, monkeypatch)["widths"] == [100, 101, 102]


def test_printed_sequence_matches_the_queue_for_every_printer_profile(world, monkeypatch):
    """Quel que soit le profil, l'imprimante traite les feuilles dans l'ordre annoncé."""
    assessment, _ = _seed(world, ["A", "B", "C"])
    profile = world.db.query(Printer).filter_by(name="Salle").one()
    for adf in (False, True):
        for pickup in (False, True):
            for output in (False, True):
                profile.adf_reverse_order = adf
                profile.pickup_reverse_order = pickup
                profile.output_reverse_order = output
                world.db.commit()
                announced = [e["student"] for e in _queue(world, assessment)["entries"]]
                widths = _print(world, assessment, monkeypatch)["widths"]
                # Une imprimante à prélèvement inversé traite le fichier à rebours.
                processed = list(reversed(widths)) if pickup else widths
                assert ["ABC"[w - 100] for w in processed] == announced


def test_resume_prints_only_the_remaining_sheets(world, monkeypatch):
    assessment, _ = _seed(world, ["A", "B", "C", "D"])
    sent = _print(world, assessment, monkeypatch, start_position=3, expected_student="C")
    assert sent["widths"] == [102, 103]


def test_resume_with_adf_reversal_keeps_the_physical_remainder(world, monkeypatch):
    assessment, _ = _seed(world, ["A", "B", "C", "D"], adf_reverse_order=True)
    # File annoncée : D, C, B, A. Reprise à la 3e position : B puis A.
    sent = _print(world, assessment, monkeypatch, start_position=3, expected_student="B")
    assert sent["widths"] == [101, 100]


def test_resume_position_and_expected_student_are_checked(world, monkeypatch):
    assessment, _ = _seed(world, ["A", "B"])
    with pytest.raises(HTTPException) as bounds:
        _print(world, assessment, monkeypatch, start_position=3)
    assert bounds.value.status_code == 422
    with pytest.raises(HTTPException) as changed:
        _print(world, assessment, monkeypatch, start_position=2, expected_student="A")
    assert changed.value.status_code == 409


def test_subject_cannot_use_resume(world, monkeypatch):
    assessment, _ = _seed(world, ["A"])
    subject = world.root / "assessments" / assessment.id / "generated" / "subject_batch.pdf"
    subject.parent.mkdir(parents=True)
    subject.write_bytes(b"%PDF-test")
    with pytest.raises(HTTPException) as error:
        print_file(PrintIn(assessment_id=assessment.id, file="subject_batch.pdf",
                           printer="Salle", start_position=2), world.db, world.user)
    assert error.value.status_code == 422


def test_last_print_is_remembered_then_forgotten_after_regeneration(world, monkeypatch):
    assessment, batch = _seed(world, ["A", "B", "C"])
    _print(world, assessment, monkeypatch, start_position=2)
    last = _queue(world, assessment)["last_print"]
    assert (last["status"], last["start_position"], last["sheets"],
            last["first_student"], last["last_student"]) == ("submitted", 2, 2, "B", "C")
    batch.overlay_rev = 1
    batch.refine_rev = 1
    world.db.commit()
    assert _queue(world, assessment)["last_print"] is None


def test_failed_print_is_remembered_and_does_not_mark_printed(world, monkeypatch):
    assessment, batch = _seed(world, ["A", "B"])
    with pytest.raises(HTTPException):
        _print(world, assessment, monkeypatch, returncode=1)
    world.db.refresh(batch)
    assert batch.overlay_printed is False
    assert _queue(world, assessment)["last_print"]["status"] == "failed"


def test_stale_or_missing_overlay_is_blocked_not_an_error(world):
    assessment, batch = _seed(world, ["A"])
    batch.refine_rev = 1
    world.db.commit()
    assert "régénérez" in _queue(world, assessment)["blocked"]
    (world.root / "assessments" / assessment.id / "overlays" / OVERLAY).unlink()
    assert "pas encore généré" in _queue(world, assessment)["blocked"]


def test_page_count_mismatch_blocks_printing(world, monkeypatch):
    assessment, _ = _seed(world, ["A", "B"])
    _write_overlay(world, assessment.id, 3)
    assert _queue(world, assessment)["entries"] == []
    with pytest.raises(HTTPException) as error:
        _print(world, assessment, monkeypatch)
    assert error.value.status_code == 409


# ------------------------------------------------------------------ recto-verso

def test_duplex_subject_prints_overlay_duplex_on_a_duplex_printer(world, monkeypatch):
    assessment, _ = _seed_duplex(world, ["A", "B"], pages_per_copy=2)
    queue = _queue(world, assessment)
    assert [(e["student"], e["pages"]) for e in queue["entries"]] == [
        ("A", "p. 1-2"), ("B", "p. 1-2")]
    sent = _print(world, assessment, monkeypatch, start_position=2)
    assert sent["widths"] == [102, 103]
    assert "sides=two-sided-long-edge" in " ".join(sent["cmd"])


def _seed_duplex(world, names, *, pages_per_copy, printer_duplex=True, **flags):
    assessment, batch = _seed(world, names, pages_per_copy=pages_per_copy, duplex=True, **flags)
    world.db.query(Printer).filter_by(name="Salle").one().duplex = printer_duplex
    world.db.commit()
    return assessment, batch


def test_duplex_reversal_swaps_sheets_not_faces(world, monkeypatch):
    assessment, _ = _seed_duplex(world, ["A", "B"], pages_per_copy=2, pickup_reverse_order=True)
    assert _print(world, assessment, monkeypatch)["widths"] == [102, 103, 100, 101]


def test_missing_verso_stays_blank_so_the_next_sheet_keeps_its_place(world, monkeypatch):
    assessment, _ = _seed_duplex(world, ["A", "B"], pages_per_copy=1)
    sent = _print(world, assessment, monkeypatch)
    # Sur chaque feuille, le verso blanc reprend la taille de la 1re page du PDF.
    assert sent["widths"] == [100, 100, 101, 100]


def test_duplex_subject_on_simplex_printer_requires_two_passes(world, monkeypatch):
    assessment, batch = _seed_duplex(world, ["A", "B"], pages_per_copy=2, printer_duplex=False)
    with pytest.raises(HTTPException) as error:
        _print(world, assessment, monkeypatch)
    assert error.value.status_code == 409
    assert _print(world, assessment, monkeypatch, pass_side="recto")["widths"] == [100, 102]
    world.db.refresh(batch)
    assert batch.overlay_printed is False   # il reste tous les versos à imprimer
    assert _print(world, assessment, monkeypatch, pass_side="verso",
                  start_position=2)["widths"] == [103]
    world.db.refresh(batch)
    assert batch.overlay_printed is True


def test_rectos_then_versos_stream_is_grouped_by_physical_sheet():
    def side(name, copy, page_no):
        return {"student": name, "identified": True, "copy_id": copy, "page_no": page_no}

    sheets = overlay_queue.group_sheets(
        [side("A", "a", 1), side("B", "b", 1), side("A", "a", 2), side("B", "b", 2)],
        duplex=True)
    assert [(s.student, s.recto, s.verso) for s in sheets] == [("A", 0, 2), ("B", 1, 3)]


def test_anonymous_side_completes_the_sheet_just_scanned():
    anonymous = {"student": "Non identifié", "identified": False, "copy_id": None, "page_no": None}
    recto = {"student": "A", "identified": True, "copy_id": "a", "page_no": 1}
    sheets = overlay_queue.group_sheets([recto, anonymous, anonymous], duplex=True)
    assert [(s.student, s.recto, s.verso) for s in sheets] == [
        ("A", 0, 1), ("Non identifié", 2, None)]


# ------------------------------------------------------------------- connecteur

def test_connector_job_contains_the_remainder_and_recto_does_not_mark_printed(world, monkeypatch):
    assessment, batch = _seed_duplex(world, ["A", "B", "C"], pages_per_copy=2, printer_duplex=False)
    db = world.db
    connector = PrintConnector(user_id=world.user.id, installation_id="i", name="Poste",
                               token_hash="h", platform="windows", last_seen_at=utcnow())
    db.add(connector); db.flush()
    db.add(Printer(name="connecteur:salle", protocol="connector", active=True,
                   capabilities_json={"connector_id": connector.id, "native_name": "Salle"}))
    db.commit()

    result = print_file(PrintIn(assessment_id=assessment.id, file=OVERLAY,
                                printer="connecteur:salle", pass_side="recto",
                                start_position=2, expected_student="B"), db, world.user)
    job = db.get(ConnectorPrintJob, result["job_id"])
    document = (world.root / job.document_relpath).read_bytes()
    assert [int(p.mediabox.width) for p in PdfReader(BytesIO(document)).pages] == [102, 104]
    assert "à partir de B" in job.title

    queue = overlay_print_queue(assessment.id, "connecteur:salle", db, world.user)
    assert queue["last_print"]["status"] == "queued"
    assert queue["last_print"]["job_id"] == job.id

    job.status = "claimed"
    db.commit()
    connectors.report_job(job.id, connectors.JobResultIn(status="submitted"), connector, db)
    db.refresh(batch)
    assert batch.overlay_printed is False
    queue = overlay_print_queue(assessment.id, "connecteur:salle", db, world.user)
    assert queue["last_print"]["status"] == "submitted"


# --------------------------------------------------------- position de l'overlay

def _text_overlay(world, assessment_id, names):
    """Overlay réel : chaque page porte le nom de l'élève à une position connue."""
    from app.services.pdfgen import render_overlay
    path = world.root / "assessments" / assessment_id / "overlays" / OVERLAY
    render_overlay(str(path), copies_annotations=[
        {"student": name, "assessment_type": "control", "page_zones": []} for name in names])
    return path


def _name_box(pdf_bytes, page=0):
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        words = doc[page].get_text("words")
        return doc[page].rect, words[0][0], words[0][1]


def _print_capturing_bytes(world, assessment, monkeypatch, **fields):
    sent = {}

    def fake_run(cmd, **_kwargs):
        sent["bytes"] = Path(cmd[-1]).read_bytes()
        return SimpleNamespace(returncode=0, stdout="job 7", stderr="")

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    print_file(PrintIn(assessment_id=assessment.id, file=OVERLAY, printer="Salle", **fields),
               world.db, world.user)
    return sent["bytes"]


def test_offset_moves_the_marks_not_the_page_nor_the_source_file(world, monkeypatch):
    assessment, _ = _seed(world, ["Awa"])
    source = _text_overlay(world, assessment.id, ["Awa"])
    before = source.read_bytes()
    _, x0, y0 = _name_box(_print_capturing_bytes(world, assessment, monkeypatch))

    profile = world.db.query(Printer).filter_by(name="Salle").one()
    profile.overlay_offset_x_mm, profile.overlay_offset_y_mm = -5.0, 2.0
    world.db.commit()
    rect, x1, y1 = _name_box(_print_capturing_bytes(world, assessment, monkeypatch))

    points_per_mm = 72 / 25.4
    assert x1 - x0 == pytest.approx(-5 * points_per_mm, abs=0.05)   # plus à gauche
    # fitz compte y vers le bas : déplacer l'overlay vers le haut réduit y.
    assert y1 - y0 == pytest.approx(-2 * points_per_mm, abs=0.05)
    assert (round(rect.width, 1), round(rect.height, 1)) == (595.3, 841.9)
    assert source.read_bytes() == before


def test_offset_follows_each_face_and_leaves_blank_faces_blank(world, monkeypatch):
    assessment, _ = _seed_duplex(world, ["Awa"], pages_per_copy=1)
    _text_overlay(world, assessment.id, ["Awa"])
    profile = world.db.query(Printer).filter_by(name="Salle").one()
    profile.overlay_offset_x_mm = 3.0
    world.db.commit()
    import fitz
    document = _print_capturing_bytes(world, assessment, monkeypatch)
    with fitz.open(stream=document, filetype="pdf") as doc:
        assert len(doc) == 2 and doc[0].get_text().strip() and not doc[1].get_text().strip()


def test_offset_preference_snaps_to_half_millimetres_and_is_bounded(world):
    from pydantic import ValidationError
    from app.routers.printing import PrinterPreferencesIn, update_printer_preferences
    _seed(world, ["A"])
    view = update_printer_preferences(
        PrinterPreferencesIn(name="Salle", overlay_offset_x_mm=-5.2, overlay_offset_y_mm=0.3),
        world.db, world.user)
    assert (view["overlay_offset_x_mm"], view["overlay_offset_y_mm"]) == (-5.0, 0.5)
    with pytest.raises(ValidationError):
        PrinterPreferencesIn(name="Salle", overlay_offset_x_mm=21)


def test_sample_prints_one_sheet_and_changes_nothing(world, monkeypatch):
    assessment, batch = _seed(world, ["A", "B", "C"])
    sent = _print(world, assessment, monkeypatch, sample=True, start_position=2,
                  expected_student="B")
    assert sent["widths"] == [101]
    world.db.refresh(batch)
    assert batch.overlay_printed is False and batch.overlay_print_json is None


def test_sample_on_duplex_subject_needs_no_second_pass(world, monkeypatch):
    assessment, _ = _seed_duplex(world, ["A", "B"], pages_per_copy=2, printer_duplex=False)
    assert _print(world, assessment, monkeypatch, sample=True)["widths"] == [100]


def test_sample_is_refused_outside_overlays(world):
    assessment, _ = _seed(world, ["A"])
    subject = world.root / "assessments" / assessment.id / "generated" / "subject_batch.pdf"
    subject.parent.mkdir(parents=True)
    subject.write_bytes(b"%PDF-test")
    with pytest.raises(HTTPException) as error:
        print_file(PrintIn(assessment_id=assessment.id, file="subject_batch.pdf",
                           printer="Salle", sample=True), world.db, world.user)
    assert error.value.status_code == 422


def test_connector_sample_does_not_mark_printed(world):
    assessment, batch = _seed(world, ["A", "B"])
    db = world.db
    connector = PrintConnector(user_id=world.user.id, installation_id="i", name="Poste",
                               token_hash="h", platform="macos", last_seen_at=utcnow())
    db.add(connector); db.flush()
    db.add(Printer(name="connecteur:salle", protocol="connector", active=True,
                   overlay_offset_x_mm=-5.0,
                   capabilities_json={"connector_id": connector.id, "native_name": "Salle"}))
    db.commit()
    result = print_file(PrintIn(assessment_id=assessment.id, file=OVERLAY, printer="connecteur:salle",
                                sample=True), db, world.user)
    job = db.get(ConnectorPrintJob, result["job_id"])
    assert "essai pour A" in job.title
    assert job.options_json["overlay_offset_mm"] == [-5.0, 0.0]
    job.status = "claimed"
    db.commit()
    connectors.report_job(job.id, connectors.JobResultIn(status="submitted"), connector, db)
    db.refresh(batch)
    assert batch.overlay_printed is False and batch.overlay_print_json is None

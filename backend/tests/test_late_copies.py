"""Copies tardives : un lot déjà corrigé et imprimé reçoit de nouvelles copies.

Vrais PDF signés, vraie lecture QR/repères (OpenCV) : seul le contenu de la
correction est simplifié (réponse `manual_drawing`, notée par le professeur)."""
import io
import sys
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest
from fastapi import BackgroundTasks, HTTPException
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyEvidence, Copy, CopyItem, CopyResult, DocumentPage,
    ExerciseCompetency, ResponseZone, ScanBatch, ScannedPage, SchoolClass, Student,
    StudentCompetencyState, StudentResponse, User,
)
from app.routers import printing, scans as scans_router
from app.services import late_copies, overlay_queue, pdfgen, pipeline, scan_intake, worker_cv
from app.services.security import sign_page

OVERLAY = "correction_overlay.pdf"
ZONE = dict(x_pt=100.0, y_pt=400.0, w_pt=300.0, h_pt=120.0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Mêmes réglages que l'application (app.db) : sans autoflush, un ajout non
    # flushé reste invisible des requêtes suivantes.
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for module in (scan_intake, pipeline, scans_router, printing):
        monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(pipeline, "build_appreciation",
                        lambda *_a, **_k: {"synthesis": "", "progress": []})
    monkeypatch.setattr(printing, "_local_printers", lambda: [
        {"name": "Salle", "source": "cups_local", "status": "idle"}])

    user = User(email="prof@x.fr", password_hash="x", role="corrector")
    school_class = SchoolClass(name="5A", grade_level="5e")
    db.add_all([user, school_class]); db.flush()
    db.add(Printer_salle())
    competency = Competency(framework_id="f1", code="C1", label="Tracer")
    db.add(competency); db.flush()
    db.add(ExerciseCompetency(exercise_id="cat-1", competency_id=competency.id,
                              weight=1.0, evidence_strength=1.0))
    assessment = Assessment(class_id=school_class.id, title="Contrôle", type="control",
                            note_base=20, status="printed")
    db.add(assessment); db.flush()
    pages = {}
    for index, name in enumerate(("Awa", "Bakary", "Chloé")):
        student = Student(class_id=school_class.id, name=name, order_index=index,
                          llm_pseudonym=f"p{index}")
        db.add(student); db.flush()
        copy = Copy(assessment_id=assessment.id, student_id=student.id, status="printed")
        db.add(copy); db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page); db.flush()
        page.qr_payload = sign_page(page.id)
        item = CopyItem(copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
                        response_type="manual_drawing", statement="Trace.",
                        correction="figure", expected_json={"type": "manual"},
                        grading_json={"comparator": "manual", "max_score": 1, "bareme_points": 2})
        db.add(item); db.flush()
        db.add(ResponseZone(page_id=page.id, item_id=item.id, type="manual_drawing",
                            padding_pt=0.0, meta_json={}, **ZONE))
        pages[name] = page
    db.commit()
    yield type("World", (), dict(db=db, user=user, assessment=assessment, pages=pages,
                                 root=tmp_path))
    db.close()


def Printer_salle():
    from app.models import Printer
    return Printer(name="Salle", protocol="cups", active=True)


def _scan(page, *, ink=True, tick=False, shift=(0, 0), noise=False) -> np.ndarray:
    """Page signée rastérisée, comme à la sortie d'un ADF."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdfgen.PAGE_W, pdfgen.PAGE_H))
    pdfgen._draw_markers(c, page.qr_payload)
    c.showPage(); c.save()
    with fitz.open(stream=buf.getvalue(), filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=200)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    k = 200 / 72
    x0, x1 = int(ZONE["x_pt"] * k), int((ZONE["x_pt"] + ZONE["w_pt"]) * k)
    y1 = int((pdfgen.PAGE_H - ZONE["y_pt"]) * k)
    y0 = y1 - int(ZONE["h_pt"] * k)
    if ink:
        cv2.line(img, (x0 + 40, y0 + 40), (x1 - 40, y1 - 40), (40, 40, 40), 4)
        cv2.line(img, (x0 + 40, y1 - 40), (x1 - 40, y0 + 40), (40, 40, 40), 4)
    if tick:
        cv2.line(img, (x0 + 60, y0 + 30), (x0 + 110, y0 + 30), (60, 60, 60), 3)
    if shift != (0, 0):
        matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
        img = cv2.warpAffine(img, matrix, (img.shape[1], img.shape[0]), borderValue=(255, 255, 255))
    if noise:
        rng = np.random.default_rng(7)
        img = np.clip(img.astype(int) + rng.integers(-12, 13, img.shape), 0, 255).astype(np.uint8)
        img = cv2.GaussianBlur(img, (3, 3), 0)
    return img


def _deposit(world, *images):
    out = scan_intake.attach_scan(world.db, world.assessment.id, list(images), world.user.id)
    world.db.commit()
    batch = world.db.get(ScanBatch, out["batch_id"])
    pipeline.process_batch(world.db, batch)
    return out, batch


def _correct_all(world, batch, ratio=0.5):
    for review in scans_router.list_reviews(batch.id, None, world.db):
        scans_router.resolve_review(review["review_id"],
                                    scans_router.ResolveIn(action="set_ratio", ratio=ratio),
                                    world.db, world.user)
    pipeline.finalize_batch(world.db, batch)
    world.db.commit()
    world.db.refresh(batch)


def _first_round(world):
    """Awa et Bakary sont corrigés, l'overlay est imprimé ; Chloé était absente."""
    _, batch = _deposit(world, _scan(world.pages["Awa"]), _scan(world.pages["Bakary"]))
    _correct_all(world, batch)
    batch.overlay_printed = True
    batch.overlay_distributed = True
    world.db.commit()
    return batch


def _copy(world, name):
    return world.db.get(Copy, world.pages[name].copy_id)


def _note(world, name):
    result = world.db.query(CopyResult).filter_by(copy_id=_copy(world, name).id).first()
    return result.note if result else None


def _evidences(world, name):
    return world.db.query(CompetencyEvidence).filter_by(
        student_id=_copy(world, name).student_id).count()


def _queue(world, wave=1):
    return printing.overlay_print_queue(world.assessment.id, "Salle", world.db, world.user, wave=wave)


def _job(world, batch, wave=1):
    # état interne du job (la vue masque les lots refermés et vides)
    return next(j for j in late_copies.jobs_view(world.db, batch) if j["wave"] == wave)


def _overlay_texts(world, wave):
    path = overlay_queue.overlay_file(world.assessment.id, wave)
    return [(page.extract_text() or "").strip() for page in PdfReader(path).pages]


def _printed_pages(world, monkeypatch, **fields):
    sent = {}

    def fake_run(cmd, **_k):
        sent["pages"] = [(p.extract_text() or "").strip() for p in PdfReader(cmd[-1]).pages]
        return type("R", (), dict(returncode=0, stdout="job", stderr=""))

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    printing.print_file(printing.PrintIn(assessment_id=world.assessment.id, file=OVERLAY,
                                         printer="Salle", **fields), world.db, world.user)
    return sent["pages"]


# ----------------------------------------------------------- élève absent

def test_absent_student_late_copy_is_corrected_without_touching_the_class(world):
    batch = _first_round(world)
    assert _copy(world, "Chloé").status == "absent"
    competency = world.db.query(Competency).first()
    note_awa = _note(world, "Awa")
    state_awa = world.db.get(StudentCompetencyState,
                             (_copy(world, "Awa").student_id, competency.id)).mastery

    out, batch = _deposit(world, _scan(world.pages["Chloé"]))
    assert out["pages_added"] == 1 and out["conflicts"] == 0 and out["late_pages"] == 1
    assert batch.overlay_wave == 1
    # La première vague garde son état : ses feuilles sont déjà imprimées et rendues.
    assert batch.overlay_printed is True and batch.overlay_distributed is True
    _correct_all(world, batch, ratio=1.0)

    assert _note(world, "Chloé") == 20 and _copy(world, "Chloé").status == "finalized"
    assert _note(world, "Awa") == note_awa
    assert _evidences(world, "Awa") == 1 and _evidences(world, "Bakary") == 1
    assert _evidences(world, "Chloé") == 1
    assert world.db.get(StudentCompetencyState,
                        (_copy(world, "Awa").student_id, competency.id)).mastery == state_awa


def test_validating_twice_never_counts_evidence_twice(world):
    batch = _first_round(world)
    pipeline.finalize_batch(world.db, batch)
    world.db.commit()
    assert _evidences(world, "Awa") == 1


def test_a_late_deposit_is_its_own_job_with_its_own_overlay(world, monkeypatch):
    batch = _first_round(world)
    first_overlay = overlay_queue.overlay_file(world.assessment.id, 0).read_bytes()
    _, batch = _deposit(world, _scan(world.pages["Chloé"]))
    job = _job(world, batch)
    assert job["stage"] == "review" and job["students"] == ["Chloé"] and job["pending_reviews"] == 1
    assert "correction" in _queue(world)["blocked"]
    _correct_all(world, batch)
    assert batch.status == "overlay_ready"

    job = _job(world, batch)
    assert job["stage"] == "print" and job["sheets"] == 1 and job["overlay_printed"] is False
    queue = _queue(world)
    assert queue["blocked"] is None and [e["student"] for e in queue["entries"]] == ["Chloé"]
    assert [e["student"] for e in _queue(world, 0)["entries"]] == ["Awa", "Bakary"]
    assert PdfReader(overlay_queue.overlay_file(world.assessment.id, 0)).pages
    assert len(first_overlay) > 0

    pages = _printed_pages(world, monkeypatch, wave=1, expected_student="Chloé")
    assert len(pages) == 1 and "Chloé" in pages[0]
    world.db.refresh(batch)
    assert _job(world, batch)["stage"] == "distribute"
    assert batch.overlay_printed is True


def test_two_late_deposits_are_two_jobs_printed_separately(world, monkeypatch):
    batch = _first_round(world)
    world.db.add(_student_page(world, "Diane"))
    world.db.commit()
    _, batch = _deposit(world, _scan(world.pages["Chloé"]))
    _correct_all(world, batch)
    _, batch = _deposit(world, _scan(world.pages["Diane"]))
    _correct_all(world, batch)
    assert batch.overlay_wave == 2
    assert [j["students"] for j in scans_router._batch_view(world.db, batch)["late_jobs"]] == [
        ["Chloé"], ["Diane"]]
    assert "Chloé" in _printed_pages(world, monkeypatch, wave=1)[0]
    assert "Diane" in _printed_pages(world, monkeypatch, wave=2)[0]
    stages = [j["stage"] for j in scans_router._batch_view(world.db, batch)["late_jobs"]]
    assert stages == ["distribute", "distribute"]


def _student_page(world, name):
    db = world.db
    student = Student(class_id=world.assessment.class_id, name=name, order_index=9, llm_pseudonym="p9")
    db.add(student); db.flush()
    copy = Copy(assessment_id=world.assessment.id, student_id=student.id, status="printed")
    db.add(copy); db.flush()
    page = DocumentPage(copy_id=copy.id, page_no=1)
    db.add(page); db.flush()
    page.qr_payload = sign_page(page.id)
    item = CopyItem(copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
                    response_type="manual_drawing", statement="Trace.", correction="figure",
                    expected_json={"type": "manual"},
                    grading_json={"comparator": "manual", "max_score": 1, "bareme_points": 2})
    db.add(item); db.flush()
    world.pages[name] = page
    return ResponseZone(page_id=page.id, item_id=item.id, type="manual_drawing",
                        padding_pt=0.0, meta_json={}, **ZONE)


def test_distribution_is_tracked_per_job(world):
    batch = _first_round(world)
    _, batch = _deposit(world, _scan(world.pages["Chloé"]))
    _correct_all(world, batch)
    late_copies.set_wave_state(batch, 1, printed=True)
    scans_router.update_batch_flags(batch.id, scans_router.BatchFlagsIn(
        overlay_distributed=True, wave=1), world.db)
    world.db.refresh(batch)
    assert _job(world, batch)["stage"] == "done"


def test_view_keeps_the_first_wave_printable_during_a_late_correction(world):
    batch = _first_round(world)
    _, batch = _deposit(world, _scan(world.pages["Chloé"]))
    view = scans_router._batch_view(world.db, batch)
    # le lot principal garde l'état de sa première pile ; la copie tardive a son pipeline
    assert view["status"] == "overlay_ready"
    assert view["first_wave_status"] == "overlay_ready"
    assert view["overlay_wave"] == 1
    assert view["pending_reviews"] == 0 and view["error"] is None
    assert view["segments"][-1]["label"] != "Overlay tardif à imprimer"
    job = view["late_jobs"][0]
    assert job["stage"] == "review"
    assert [s["state"] for s in job["segments"]][:3] == ["green", "green", "green"]
    main = {i["student"] for i in scans_router.list_items(batch.id, "all", world.db, wave=0)}
    late = {i["student"] for i in scans_router.list_items(batch.id, "all", world.db, wave=1)}
    assert late == {"Chloé"} and "Chloé" not in main
    _correct_all(world, batch)
    job = scans_router._batch_view(world.db, batch)["late_jobs"][0]
    assert job["segments"][-1] == {"phase": "overlay_print", "label": "Overlay à imprimer",
                                   "state": "orange"}


def test_stuck_late_correction_resumes_the_pipeline_not_only_the_overlay(world):
    batch = _first_round(world)
    scan_intake.attach_scan(world.db, world.assessment.id, [_scan(world.pages["Chloé"])],
                            world.user.id)
    world.db.commit()
    tasks = BackgroundTasks()
    scans_router.retry_batch(batch.id, tasks, world.db)
    assert [t.func for t in tasks.tasks] == [scans_router._run_pipeline]


# ------------------------------------------------- copie déjà scannée vide

def test_blank_copy_without_note_is_replaced_without_conflict(world, monkeypatch):
    with monkeypatch.context() as patched:
        chloe = _copy(world, "Chloé").id
        patched.setattr(pipeline, "copy_is_blank", lambda _db, copy: copy.id == chloe)
        _, batch = _deposit(world, _scan(world.pages["Awa"]), _scan(world.pages["Bakary"]),
                            _scan(world.pages["Chloé"], ink=False))
        _correct_all(world, batch)
    assert _copy(world, "Chloé").status == "absent" and _note(world, "Chloé") is None
    batch.overlay_printed = True
    world.db.commit()

    out, batch = _deposit(world, _scan(world.pages["Chloé"]))
    assert out["conflicts"] == 0 and out["duplicates_rejected"] == 0
    _correct_all(world, batch, ratio=1.0)
    assert _note(world, "Chloé") == 20
    assert [e["student"] for e in _queue(world)["entries"]] == ["Chloé"]
    # Dans la première pile, l'ancienne feuille garde sa place, sans correction.
    first = _queue(world, 0)["entries"]
    assert [(e["student"], e["pass_through"]) for e in first] == [
        ("Awa", False), ("Bakary", False), ("Chloé", True)]
    texts = _overlay_texts(world, 0)
    assert len(texts) == 3 and "Awa" in texts[0] and "Bakary" in texts[1] and texts[2] == ""


# ------------------------------------------------------ double correction

def test_new_version_of_a_noted_copy_raises_a_conflict_and_changes_nothing(world):
    batch = _first_round(world)
    note = _note(world, "Awa")
    out, batch = _deposit(world, _scan(world.pages["Awa"], tick=True))
    assert out["conflicts"] == 1 and out["duplicates_rejected"] == 0
    assert _note(world, "Awa") == note
    assert world.db.query(StudentResponse).join(
        CopyItem, StudentResponse.copy_item_id == CopyItem.id).filter(
        CopyItem.copy_id == _copy(world, "Awa").id).count() == 1

    conflicts = late_copies.list_conflicts(world.db, batch)
    assert len(conflicts) == 1
    assert conflicts[0]["student"] == "Awa" and conflicts[0]["existing_note"] == "10/20"
    assert conflicts[0]["existing_blank"] is False and conflicts[0]["wave"] == 1
    view = scans_router._batch_view(world.db, batch)
    assert view["conflict_count"] == 1 and _job(world, batch)["stage"] == "conflict"


def test_conflict_blocks_its_own_job_not_the_first_wave(world, monkeypatch):
    batch = _first_round(world)
    _, batch = _deposit(world, _scan(world.pages["Awa"], tick=True))
    assert "double correction" in _queue(world)["blocked"]
    with pytest.raises(HTTPException) as error:
        _printed_pages(world, monkeypatch, wave=1)
    assert error.value.status_code == 409
    assert _queue(world, 0)["blocked"] is None
    assert len(_printed_pages(world, monkeypatch, wave=0)) == 2


def test_keeping_the_old_note_closes_a_job_with_nothing_left(world):
    batch = _first_round(world)
    note = _note(world, "Awa")
    _, batch = _deposit(world, _scan(world.pages["Awa"], tick=True))
    conflict = late_copies.list_conflicts(world.db, batch)[0]
    scans_router.resolve_conflict(batch.id, conflict["id"],
                                  scans_router.ConflictResolveIn(action="keep_old"),
                                  BackgroundTasks(), world.db, world.user)
    world.db.refresh(batch)
    assert _note(world, "Awa") == note
    assert late_copies.list_conflicts(world.db, batch) == []
    assert _job(world, batch)["stage"] == "done"


def test_rejected_sheet_keeps_its_place_in_a_mixed_pile(world):
    batch = _first_round(world)
    out, batch = _deposit(world, _scan(world.pages["Awa"], tick=True), _scan(world.pages["Chloé"]))
    assert out["conflicts"] == 1
    conflict = late_copies.list_conflicts(world.db, batch)[0]
    scans_router.resolve_conflict(batch.id, conflict["id"],
                                  scans_router.ConflictResolveIn(action="keep_old"),
                                  BackgroundTasks(), world.db, world.user)
    _correct_all(world, batch)
    assert [(e["student"], e["pass_through"]) for e in _queue(world)["entries"]] == [
        ("Awa", True), ("Chloé", False)]
    texts = _overlay_texts(world, 1)
    assert texts[0] == "" and "Chloé" in texts[1]


def test_replacing_the_note_recorrects_only_this_copy(world):
    batch = _first_round(world)
    note_bakary = _note(world, "Bakary")
    _, batch = _deposit(world, _scan(world.pages["Awa"], tick=True))
    conflict = late_copies.list_conflicts(world.db, batch)[0]
    scans_router.resolve_conflict(batch.id, conflict["id"],
                                  scans_router.ConflictResolveIn(action="keep_new"),
                                  BackgroundTasks(), world.db, world.user)
    assert _note(world, "Awa") is None
    pipeline.process_batch(world.db, batch)
    _correct_all(world, batch, ratio=1.0)

    assert _note(world, "Awa") == 20 and _note(world, "Bakary") == note_bakary
    assert _evidences(world, "Awa") == 1 and _evidences(world, "Bakary") == 1
    evidence = world.db.query(CompetencyEvidence).filter_by(
        student_id=_copy(world, "Awa").student_id).one()
    assert evidence.score_ratio == 1.0
    assert [e["student"] for e in _queue(world)["entries"]] == ["Awa"]
    assert "20/20" in _overlay_texts(world, 1)[0].replace(" ", "")
    # La note remplacée reste au journal.
    from app.models import AuditLog
    assert world.db.query(AuditLog).filter_by(action="late_copy_replaced").one().before_json["note"] == "10/20"


def test_conflict_on_zero_says_the_old_copy_was_blank(world, monkeypatch):
    # Copie vide notée 0 : sa lecture n'a pas suffi à la déclarer absente.
    monkeypatch.setattr(pipeline, "copy_is_blank", lambda _db, _copy: False)
    _, batch = _deposit(world, _scan(world.pages["Chloé"], ink=False))
    _correct_all(world, batch, ratio=0.0)
    assert _note(world, "Chloé") == 0
    out, batch = _deposit(world, _scan(world.pages["Chloé"]))
    assert out["conflicts"] == 1
    conflict = late_copies.list_conflicts(world.db, batch)[0]
    assert conflict["existing_note"] == "0/20" and conflict["existing_blank"] is True


# ----------------------------------------------------------------- doublons

@pytest.mark.parametrize("variant", [
    dict(), dict(shift=(2, -1)), dict(noise=True), dict(shift=(1, 2), noise=True),
])
def test_rescanning_the_same_sheet_opens_no_job(world, variant):
    batch = _first_round(world)
    out, batch = _deposit_no_pipeline(world, _scan(world.pages["Awa"], **variant))
    assert out["pages_added"] == 0 and out["duplicates_rejected"] == 1 and out["conflicts"] == 0
    assert batch.overlay_wave == 0 and batch.status == "overlay_ready"


def _deposit_no_pipeline(world, *images):
    out = scan_intake.attach_scan(world.db, world.assessment.id, list(images), world.user.id)
    world.db.commit()
    return out, world.db.get(ScanBatch, out["batch_id"])


def test_whole_pile_rescan_keeps_scan_order_for_printing(world, monkeypatch):
    """L'ordre scanné fait foi : la pile repasse entière, seule Chloé reçoit des marques."""
    batch = _first_round(world)
    out, batch = _deposit(world, _scan(world.pages["Awa"]), _scan(world.pages["Chloé"]),
                          _scan(world.pages["Bakary"]))
    assert out["pages_added"] == 1 and out["duplicates_rejected"] == 2 and out["conflicts"] == 0
    _correct_all(world, batch)
    assert [(e["student"], e["pass_through"]) for e in _queue(world)["entries"]] == [
        ("Awa", True), ("Chloé", False), ("Bakary", True)]
    assert _job(world, batch)["students"] == ["Chloé"]
    pages = _printed_pages(world, monkeypatch, wave=1)
    assert pages[0] == "" and "Chloé" in pages[1] and pages[2] == ""


def test_a_pending_conflict_is_not_raised_twice_for_the_same_sheet(world):
    batch = _first_round(world)
    out, batch = _deposit(world, _scan(world.pages["Awa"], tick=True))
    assert out["conflicts"] == 1
    again, batch = _deposit_no_pipeline(world, _scan(world.pages["Awa"], tick=True))
    assert again["conflicts"] == 0 and again["duplicates_rejected"] == 1
    assert len(late_copies.list_conflicts(world.db, batch)) == 1


def test_a_correction_marked_overlay_is_not_a_new_answer(world):
    """Le rouge imprimé par l'overlay n'est pas de l'encre d'élève."""
    batch = _first_round(world)
    sheet = _scan(world.pages["Awa"])
    cv2.putText(sheet, "12/20", (400, 800), cv2.FONT_HERSHEY_SIMPLEX, 2, (40, 40, 198), 4)
    out, batch = _deposit_no_pipeline(world, sheet)
    assert out["conflicts"] == 0 and out["duplicates_rejected"] == 1


def test_late_copy_can_be_corrected_while_a_conflict_stays_open(world):
    batch = _first_round(world)
    out, batch = _deposit(world, _scan(world.pages["Chloé"]), _scan(world.pages["Awa"], tick=True))
    assert out["conflicts"] == 1
    assert _job(world, batch)["pending_reviews"] == 1
    _correct_all(world, batch, ratio=1.0)
    assert _note(world, "Chloé") == 20
    assert _job(world, batch)["stage"] == "conflict"
    assert _queue(world, 0)["blocked"] is None


def test_a_single_open_review_is_counted_by_the_pipeline(world):
    """Une seule réponse à corriger : le lot attend le professeur, sans plantage."""
    _, batch = _deposit(world, _scan(world.pages["Awa"]))
    assert batch.status == "review_pending" and batch.error is None
    assert len(scans_router.list_reviews(batch.id, None, world.db)) == 1


# ------------------------------------------- résolutions pendant une lecture

def _two_conflicts(world):
    """Deux copies tardives d'élèves déjà notés, dans le même enchaînement."""
    batch = _first_round(world)
    out, batch = _deposit(world, _scan(world.pages["Awa"], tick=True),
                          _scan(world.pages["Bakary"], tick=True))
    assert out["conflicts"] == 2
    return batch, late_copies.list_conflicts(world.db, batch)


def _replace(world, batch, conflict):
    scans_router.resolve_conflict(batch.id, conflict["id"],
                                  scans_router.ConflictResolveIn(action="keep_new"),
                                  BackgroundTasks(), world.db, world.user)


def test_late_sheet_still_pending_blocks_its_overlay(world):
    """Incident Sohayb : le lot redevient « prêt » avec une feuille jamais lue. Son
    overlay ne porterait qu'une mention « à corriger manuellement »."""
    batch, conflicts = _two_conflicts(world)
    _replace(world, batch, conflicts[0])
    pipeline.process_batch(world.db, batch)
    _replace(world, batch, conflicts[1])
    # La lecture en cours n'a pas vu la 2e résolution et termine le lot.
    _correct_all(world, batch)
    assert batch.status == "overlay_ready"

    assert _job(world, batch)["stage"] == "processing"
    assert "correction" in _queue(world)["blocked"]


def test_read_requested_during_a_task_is_replayed_after_it(world, monkeypatch):
    """La 2e résolution arrive pendant la lecture lancée par la 1re : elle ne doit
    pas se perdre, sa feuille resterait en attente sans que rien ne la lise."""
    batch, conflicts = _two_conflicts(world)
    monkeypatch.setattr(scans_router, "SessionLocal", lambda: world.db)
    monkeypatch.setattr(world.db, "close", lambda: None)
    runs = []
    process = pipeline.process_batch

    def during_first_read(db, current):
        runs.append(current.id)
        if len(runs) == 1:
            # Lecture déjà passée sur la 2e feuille quand le professeur la tranche.
            process(db, current)
            _replace(world, batch, conflicts[1])
            scans_router._run_pipeline(batch.id)
            assert len(runs) == 1, "jamais deux lectures en parallèle"
        else:
            process(db, current)

    monkeypatch.setattr(scans_router, "process_batch", during_first_read)
    _replace(world, batch, conflicts[0])
    scans_router._run_pipeline(batch.id)

    assert len(runs) == 2 and not scans_router._is_running(batch.id)
    statuses = [row.status for row in world.db.query(ScannedPage).filter_by(batch_id=batch.id, wave=1)]
    assert statuses == ["registered", "registered"]
    assert _job(world, batch)["students"] == ["Awa", "Bakary"]
    _correct_all(world, batch, ratio=1.0)
    assert _note(world, "Awa") == 20 and _note(world, "Bakary") == 20
    texts = _overlay_texts(world, 1)
    assert "Awa" in texts[0] and "Bakary" in texts[1]
    assert not any("manuellement" in text for text in texts)


def test_rescanning_a_sheet_waiting_to_be_read_opens_no_job(world):
    """Redéposer la feuille restée en attente : même feuille, aucun nouveau job."""
    batch, conflicts = _two_conflicts(world)
    _replace(world, batch, conflicts[1])
    again, batch = _deposit_no_pipeline(world, _scan(world.pages["Bakary"], tick=True))
    assert again["pages_added"] == 0 and again["duplicates_rejected"] == 1
    assert batch.overlay_wave == 1


def test_new_version_of_a_sheet_waiting_to_be_read_replaces_it(world):
    """Une autre version pendant l'attente : le professeur tranche, une seule est notée."""
    batch, conflicts = _two_conflicts(world)
    _replace(world, batch, conflicts[1])
    bakary = world.pages["Bakary"].id
    out, batch = _deposit_no_pipeline(world, _scan(world.pages["Bakary"]))
    assert out["conflicts"] == 1 and batch.overlay_wave == 2
    newest = next(c for c in late_copies.list_conflicts(world.db, batch) if c["wave"] == 2)
    _replace(world, batch, newest)
    rows = (world.db.query(ScannedPage)
            .filter_by(batch_id=batch.id, replaces_page_id=bakary, status="pending").all())
    assert [row.wave for row in rows] == [2]


def test_job_left_with_no_sheet_after_a_newer_version_closes_itself(world):
    batch = _first_round(world)
    _, batch = _deposit_no_pipeline(world, _scan(world.pages["Awa"], tick=True))
    first = late_copies.list_conflicts(world.db, batch)[0]
    _replace(world, batch, first)
    _, batch = _deposit_no_pipeline(world, _scan(world.pages["Awa"]))
    newest = next(c for c in late_copies.list_conflicts(world.db, batch) if c["wave"] == 2)
    _replace(world, batch, newest)
    assert late_copies.wave_state(batch, 1).get("closed") is True
    assert _job(world, batch, 1)["stage"] == "done"


def test_sheet_twice_in_a_late_pile_gets_a_silent_page(world, monkeypatch):
    """La correction est sur l'autre exemplaire : pas de « à corriger manuellement »."""
    batch = _first_round(world)
    sheet = _scan(world.pages["Chloé"])
    out, batch = _deposit(world, sheet, sheet)
    assert out["pages_added"] == 2
    _correct_all(world, batch)
    assert [(e["student"], e["pass_through"]) for e in _queue(world)["entries"]] == [
        ("Chloé", False), ("Chloé", True)]
    pages = _printed_pages(world, monkeypatch, wave=1)
    assert "Chloé" in pages[0] and pages[1] == ""


def test_late_pile_of_a_sheet_corrected_elsewhere_closes_itself(world):
    """Enchaînement réduit à une feuille déjà lue dans un autre : rien à imprimer."""
    batch = _first_round(world)
    out, batch = _deposit_no_pipeline(world, _scan(world.pages["Chloé"]))
    assert out["pages_added"] == 1 and batch.overlay_wave == 1
    # 2e dépôt avant toute lecture : la page n'est encore rattachée à personne.
    out, batch = _deposit_no_pipeline(world, _scan(world.pages["Chloé"]))
    assert out["pages_added"] == 1 and batch.overlay_wave == 2
    pipeline.process_batch(world.db, batch)
    _correct_all(world, batch)
    assert _job(world, batch, 1)["stage"] == "print"
    assert _job(world, batch, 2)["stage"] == "done"


def test_a_closed_job_without_sheets_is_not_listed(world):
    batch = _first_round(world)
    _, batch = _deposit(world, _scan(world.pages["Chloé"]))
    # vague refermée dont toutes les feuilles sont passées en doublon : plus rien à corriger
    for row in world.db.query(ScannedPage).filter_by(batch_id=batch.id, wave=1):
        row.status = "late_duplicate"
    late_copies.set_wave_state(batch, 1, closed=True)
    world.db.commit()
    assert scans_router._batch_view(world.db, batch)["late_jobs"] == []

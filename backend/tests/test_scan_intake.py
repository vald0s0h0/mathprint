"""Tests : entrée des scans dans la pipeline — RÈGLE « un sujet = une
correction = un seul ScanBatch ». Couvre le bug du bac à sable (plusieurs
fichiers/images d'un même sujet créaient autant de corrections)."""
import hashlib
import hmac
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401 (enregistre les tables sur Base)
from app.db import Base
from app.config import settings
from app.models import (Assessment, Copy, DocumentPage, FileObject, ScanBatch,
                        ScannedPage, SchoolClass, Student)
from app.services import pdfgen, sandbox, scan_intake, worker_cv
from app.services.security import sign_page


@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db):
    cls = SchoolClass(name="5A", grade_level="5e")
    db.add(cls)
    db.flush()
    stu = Student(class_id=cls.id, name="Alex Martin", llm_pseudonym="p1")
    db.add(stu)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle 1")
    db.add(a)
    db.flush()
    pages = {}
    for tag in (1, 2):
        copy = Copy(assessment_id=a.id, student_id=stu.id)
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        pages[tag] = page.id
    db.commit()
    return a, pages


def _img(tag: int) -> np.ndarray:
    """Petite image dont le page_id est encodé dans le pixel [0,0,0]."""
    im = np.zeros((8, 8, 3), np.uint8)
    im[0, 0, 0] = tag
    return im


def _patch_classify(monkeypatch, db, pages):
    tag_to_page = {tag: pid for tag, pid in pages.items()}

    def fake_classify(_db, img):
        # (page_id, assessment_id, image recalée) — ici l'image sert de « recalée »
        pid = tag_to_page.get(int(img[0, 0, 0]))
        return (pid, scan_intake.page_assessment(_db, pid), img) if pid else (None, None, None)

    monkeypatch.setattr(scan_intake, "classify_page", fake_classify)


def _marked_pdf(payload: str) -> bytes:
    """Une vraie page MathPrint, telle qu'elle arrive dans « Déposer en vrac ».
    Le test passe ensuite par raster_pdf + OpenCV, sans simuler le QR."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdfgen.PAGE_W, pdfgen.PAGE_H))
    pdfgen._draw_markers(c, payload)
    pdfgen._draw_header(c, "MARTIN Alex", "5A", "Contrôle 1", "control", "10/08/2026")
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize("qr_version", ["compact_m2", "legacy_mp1"])
def test_bulk_upload_recognizes_signed_pages_end_to_end(
        mock_db, tmp_path, monkeypatch, qr_version):
    """Génération -> PDF -> raster -> QR OpenCV -> HMAC -> DocumentPage ->
    regroupement du dépôt en vrac. Couvre M2 et les copies MP1 déjà imprimées."""
    db = mock_db
    assessment, pages = _seed(db)
    page_id = pages[1]
    if qr_version == "compact_m2":
        payload = sign_page(page_id)
    else:
        sig = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).hexdigest()[:16]
        payload = f"MP1|{page_id}|{sig}"
    page = db.get(DocumentPage, page_id)
    page.qr_payload = payload
    page.hmac_version = "2" if qr_version == "compact_m2" else "1"
    db.commit()

    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    pdf_bytes = _marked_pdf(payload)
    source = tmp_path / f"{qr_version}.pdf"
    source.write_bytes(pdf_bytes)
    image = worker_cv.raster_pdf(str(source))[0]

    # Dépôt ciblé/non ciblé : le pré-contrôle retrouve bien le sujet.
    assert scan_intake.detect_assessment(db, [image]) == assessment.id
    identified, aid, warped = scan_intake.classify_page(db, image)
    assert identified == page_id and aid == assessment.id and warped is not None

    # Parcours exact du bouton « Déposer en vrac ».
    out = sandbox.ingest_files(
        db, [(f"{qr_version}.pdf", ".pdf", pdf_bytes)], "teacher")
    assert len(out["batch_ids"]) == 1
    assert out["results"][0]["pages_added"] == 1
    assert out["results"][0]["blocked_pages"] == 0

    # Le PDF canonique réencodé pour la pipeline doit encore être reconnaissable
    # au second passage CV, pas seulement au dépôt initial.
    batch = db.get(ScanBatch, out["batch_ids"][0])
    stored = db.get(FileObject, batch.source_file_id)
    reloaded = worker_cv.raster_pdf(stored.storage_path)[0]
    assert worker_cv.analyze_page(reloaded).page_id == page_id


def test_two_uploads_same_assessment_reuse_single_batch(mock_db, tmp_path, monkeypatch):
    db = mock_db
    a, pages = _seed(db)
    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    _patch_classify(monkeypatch, db, pages)

    # deux dépôts distincts (deux photos, deux pages) du MÊME sujet
    r1 = scan_intake.attach_scan(db, a.id, [_img(1)], "u")
    r2 = scan_intake.attach_scan(db, a.id, [_img(2)], "u")
    db.commit()

    batches = db.query(ScanBatch).filter_by(assessment_id=a.id).all()
    assert len(batches) == 1, "un sujet ne doit avoir qu'une seule correction"
    assert r1["batch_id"] == r2["batch_id"]
    assert r1["pages_added"] == 1 and r2["pages_added"] == 1

    # le PDF accumulé contient bien les DEUX pages
    from pypdf import PdfReader
    src = db.get(ScanBatch, r1["batch_id"]).source_file_id
    from app.models import FileObject
    fo = db.get(FileObject, src)
    assert len(PdfReader(fo.storage_path).pages) == 2


def test_sandbox_multiple_files_same_subject_one_batch(mock_db, tmp_path, monkeypatch):
    db = mock_db
    a, pages = _seed(db)
    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    _patch_classify(monkeypatch, db, pages)

    # Deux fichiers déposés en une fois, mais la MÊME page dans les deux. Une
    # seule correction est calculée, mais les DEUX positions physiques restent
    # dans le flux pour ne jamais décaler les feuilles suivantes.
    queue = [[_img(1)], [_img(1)]]
    monkeypatch.setattr(sandbox.worker_cv, "raster_any", lambda _path: queue.pop(0))

    out = sandbox.ingest_files(
        db, [("photo1.jpg", ".jpg", b"file-1"), ("photo2.jpg", ".jpg", b"file-2")], "u")

    assert len(out["batch_ids"]) == 1
    assert db.query(ScanBatch).filter_by(assessment_id=a.id).count() == 1
    assert {r["file_kind"] for r in out["results"]} == {"image"}
    added = sum(r["pages_added"] for r in out["results"])
    dups = sum(r["duplicates_rejected"] for r in out["results"])
    assert added == 2 and dups == 1


def test_targeted_scan_keeps_unidentified_page_in_exact_position(
        mock_db, tmp_path, monkeypatch):
    db = mock_db
    assessment, pages = _seed(db)
    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    _patch_classify(monkeypatch, db, pages)

    result = scan_intake.attach_scan(
        db, assessment.id, [_img(1), _img(0), _img(2)], "u")
    db.commit()

    batch = db.get(ScanBatch, result["batch_id"])
    stored = db.get(FileObject, batch.source_file_id)
    from pypdf import PdfReader
    assert result["pages_added"] == 3
    assert result["blocked_pages"] == 1
    assert len(PdfReader(stored.storage_path).pages) == 3


def test_sandbox_jpeg_sequence_keeps_unknown_file_between_known_copies(
        mock_db, tmp_path, monkeypatch):
    db = mock_db
    assessment, pages = _seed(db)
    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    _patch_classify(monkeypatch, db, pages)
    queue = [[_img(1)], [_img(0)], [_img(2)]]
    monkeypatch.setattr(sandbox.worker_cv, "raster_any", lambda _path: queue.pop(0))

    out = sandbox.ingest_files(db, [
        ("scan-1.jpg", ".jpg", b"one"),
        ("scan-2.jpg", ".jpg", b"unknown"),
        ("scan-3.jpg", ".jpg", b"three"),
    ], "u")

    assert len(out["batch_ids"]) == 1
    assert sum(r["pages_added"] for r in out["results"]) == 3
    assert sum(r["blocked_pages"] for r in out["results"]) == 1
    batch = db.get(ScanBatch, out["batch_ids"][0])
    stored = db.get(FileObject, batch.source_file_id)
    from pypdf import PdfReader
    assert len(PdfReader(stored.storage_path).pages) == 3


def test_overlay_keeps_blocked_positions_as_non_identified_pages(
        mock_db, tmp_path, monkeypatch):
    from app.services import pipeline
    from pypdf import PdfReader

    db = mock_db
    assessment, _pages = _seed(db)
    batch = ScanBatch(assessment_id=assessment.id, page_count=3)
    db.add(batch); db.flush()
    db.add_all([
        ScannedPage(batch_id=batch.id, source_index=i, status="blocked")
        for i in range(3)
    ])
    db.commit()
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)

    output = pipeline.build_overlays(db, batch)
    pdf = PdfReader(output)
    assert len(pdf.pages) == 3
    assert all("Non identifié" in (page.extract_text() or "") for page in pdf.pages)


def test_sandbox_same_file_reuploadable_after_correction_deleted(mock_db, tmp_path, monkeypatch):
    """Après suppression d'une correction, redéposer LE MÊME fichier doit
    repasser : on ne bloque plus sur le sha256 du fichier (qui survivait à la
    suppression), seul le page_id fait autorité — et il est remis à zéro avec la
    correction. Cf. bug « scans refusés en doublon après suppression »."""
    from app.services import data_admin

    db = mock_db
    a, pages = _seed(db)
    monkeypatch.setattr(scan_intake.settings, "data_dir", tmp_path)
    monkeypatch.setattr(data_admin.settings, "data_dir", tmp_path)
    _patch_classify(monkeypatch, db, pages)

    # 1er dépôt : la page est retenue, un batch est créé
    monkeypatch.setattr(sandbox.worker_cv, "raster_any", lambda _path: [_img(1)])
    out1 = sandbox.ingest_files(db, [("copie.jpg", ".jpg", b"same-bytes")], "u")
    assert sum(r["pages_added"] for r in out1["results"]) == 1
    batch = db.query(ScanBatch).filter_by(assessment_id=a.id).first()

    # le pipeline enregistre la page (dédup page_id = page_already_registered)
    from app.models import ScannedPage
    db.add(ScannedPage(batch_id=batch.id, source_index=0, page_id=pages[1], status="registered"))
    db.commit()
    assert scan_intake.page_already_registered(db, pages[1])

    # redéposer le même fichier tel quel : rejeté en doublon (pages enregistrées)
    out_dup = sandbox.ingest_files(db, [("copie.jpg", ".jpg", b"same-bytes")], "u")
    assert out_dup["results"][0]["status"] == "duplicate_file"
    assert sum(r["pages_added"] for r in out_dup["results"]) == 0

    # le prof supprime la correction (erreur) puis redépose LE MÊME fichier
    data_admin.delete_scan_batch(db, batch)
    db.commit()
    assert not scan_intake.page_already_registered(db, pages[1])

    out2 = sandbox.ingest_files(db, [("copie.jpg", ".jpg", b"same-bytes")], "u")
    assert sum(r["pages_added"] for r in out2["results"]) == 1, \
        "le même fichier doit être accepté une fois la correction supprimée"
    assert len(out2["batch_ids"]) == 1

"""Tests : entrée des scans dans la pipeline — RÈGLE « un sujet = une
correction = un seul ScanBatch ». Couvre le bug du bac à sable (plusieurs
fichiers/images d'un même sujet créaient autant de corrections)."""
import sys
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401 (enregistre les tables sur Base)
from app.db import Base
from app.models import Assessment, Copy, DocumentPage, ScanBatch, SchoolClass, Student
from app.services import sandbox, scan_intake


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
    stu = Student(class_id=cls.id, first_name="Alex", last_name="Martin", llm_pseudonym="p1")
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
        pid = tag_to_page.get(int(img[0, 0, 0]))
        return (pid, scan_intake.page_assessment(_db, pid)) if pid else (None, None)

    monkeypatch.setattr(scan_intake, "classify_page", fake_classify)


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

    # deux fichiers déposés en une fois, mais la MÊME page dans les deux
    # (doublon inter-fichiers) : une seule page retenue, une correction unique
    queue = [[_img(1)], [_img(1)]]
    monkeypatch.setattr(sandbox.worker_cv, "raster_any", lambda _path: queue.pop(0))

    out = sandbox.ingest_files(
        db, [("photo1.jpg", ".jpg", b"file-1"), ("photo2.jpg", ".jpg", b"file-2")], "u")

    assert len(out["batch_ids"]) == 1
    assert db.query(ScanBatch).filter_by(assessment_id=a.id).count() == 1
    added = sum(r["pages_added"] for r in out["results"])
    dups = sum(r["duplicates_rejected"] for r in out["results"])
    assert added == 1 and dups == 1

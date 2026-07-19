"""Test d'intégration du chemin de correction manuelle bout en bout (chemin
mock, exercice `manual_drawing` toujours mis en revue) : process_batch ->
list_reviews (barème/groupe) -> resolve set_ratio -> finalize -> overlays.

N'appelle aucun réseau : manual_drawing ne déclenche pas d'OCR, et l'absence
de progrès/compétences dues évite l'appel d'appréciation."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (
    Assessment, Copy, CopyItem, CopyItemResult, DocumentPage, ResponseZone,
    ScanBatch, SchoolClass, Student, User,
)
from app.routers import scans as scans_router
from app.services import pipeline, scan_intake


@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_manual(db):
    cls = SchoolClass(name="5A", grade_level="5e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle", type="control", note_base=20)
    db.add(a)
    db.flush()
    for i in range(2):
        stu = Student(class_id=cls.id, first_name=f"E{i}", last_name="X", llm_pseudonym=f"p{i}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status="printed")
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        item = CopyItem(
            copy_id=copy.id, catalog_id="cat-1", sequence=1, difficulty=3,
            response_type="manual_drawing", statement="Trace la figure.",
            correction="figure correcte",
            expected_json={"type": "manual"},
            grading_json={"comparator": "manual", "max_score": 1, "bareme_points": 1.5})
        db.add(item)
        db.flush()
        db.add(ResponseZone(page_id=page.id, item_id=item.id, type="drawing",
                            x_pt=50, y_pt=50, w_pt=100, h_pt=60, meta_json={}))
    db.commit()
    return a


def test_manual_correction_end_to_end(mock_db, tmp_path, monkeypatch):
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    user = User(email="prof@x.fr", password_hash="x", role="teacher")
    db.add(user)
    db.flush()

    # un seul batch pour le sujet, puis correction (chemin mock)
    batch = scan_intake.get_or_create_batch(db, a.id, user.id)
    db.commit()
    pipeline.process_batch(db, batch)

    # manual_drawing -> toujours une revue par copie
    reviews = scans_router.list_reviews(batch.id, None, db)
    assert len(reviews) == 2
    for r in reviews:
        assert r["bareme_points"] == 1.5
        assert r["group_key"].startswith("cat-1|")
        assert r["group_label"] == "Ex. 1"

    # 2/3 des points via le raccourci -> earned = 2/3 × 1,5 = 1,0
    body = scans_router.ResolveIn(action="set_ratio", ratio=2 / 3)
    for r in reviews:
        scans_router.resolve_review(r["review_id"], body, db, user)

    assert scans_router.list_reviews(batch.id, None, db) == []

    result = pipeline.finalize_batch(db, batch)
    db.commit()
    assert result["results_created"] == 2

    item_results = db.query(CopyItemResult).all()
    assert len(item_results) == 2
    for ir in item_results:
        assert abs(ir.points_earned - 1.0) < 1e-6

    # overlays générés dès la finalisation (aperçus disponibles)
    overlays = tmp_path / "assessments" / a.id / "overlays"
    assert (overlays / "correction_overlay.pdf").exists()
    assert (overlays / "correction_review.pdf").exists()
    assert db.get(ScanBatch, batch.id).status == "overlay_ready"


def test_review_all_and_response_resolve(mock_db, tmp_path, monkeypatch):
    """Voie « corriger manuellement » toujours accessible : scope=all expose
    TOUTES les réponses scannées (pas seulement les signalées) et la correction
    passe par response_id."""
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    user = User(email="p2@x.fr", password_hash="x", role="teacher")
    db.add(user)
    db.flush()
    batch = scan_intake.get_or_create_batch(db, a.id, user.id)
    db.commit()
    pipeline.process_batch(db, batch)

    all_items = scans_router.list_items(batch.id, "all", db)
    assert len(all_items) == 2
    assert all(it["response_id"] for it in all_items)
    assert all(it["flagged"] for it in all_items)  # manual_drawing → signalé
    assert len(scans_router.list_items(batch.id, "flagged", db)) == 2

    # correction d'une réponse par son id (voie « toutes les réponses »)
    body = scans_router.ResolveIn(action="set_ratio", ratio=1.0)
    scans_router.resolve_response(all_items[0]["response_id"], body, db, user)

    # la revue signalée correspondante est close ; scope=all la montre toujours,
    # désormais corrigée par le professeur à plein barème (1,5)
    assert len(scans_router.list_items(batch.id, "flagged", db)) == 1
    again = {it["response_id"]: it for it in scans_router.list_items(batch.id, "all", db)}
    fixed = again[all_items[0]["response_id"]]
    assert fixed["decision_source"] == "teacher"
    assert fixed["full_credit"] is True
    assert abs(fixed["current_points"] - 1.5) < 1e-6


def test_retry_clears_error_and_reschedules(mock_db, tmp_path, monkeypatch):
    """Bouton de déblocage : retry efface l'erreur et replanifie le pipeline."""
    from fastapi import BackgroundTasks

    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    batch.error = "Fichier scan introuvable"
    db.commit()

    tasks = BackgroundTasks()
    r = scans_router.retry_batch(batch.id, tasks, db)
    assert r["ok"] is True
    assert db.get(ScanBatch, batch.id).error is None
    assert len(tasks.tasks) == 1  # pipeline replanifié en tâche de fond
    # pas encore finalisé → on relance tout le pipeline (OCR compris)
    assert tasks.tasks[0].func is scans_router._run_pipeline


def test_finalize_surfaces_overlay_error(mock_db, tmp_path, monkeypatch):
    """Un échec de génération des copies corrigées n'est plus avalé en silence :
    il est remonté sur batch.error (et dans le résultat), pour que l'UI affiche
    « bloqué » avec un bouton de relance au lieu d'un « prêt » sans PDF."""
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)
    body = scans_router.ResolveIn(action="set_ratio", ratio=1.0)
    for it in scans_router.list_items(batch.id, "all", db):
        scans_router.resolve_response(it["response_id"], body, db, None)

    def _boom(_db, _batch):
        raise RuntimeError("rendu KO")

    monkeypatch.setattr(pipeline, "build_overlays", _boom)
    result = pipeline.finalize_batch(db, batch)
    db.commit()

    assert result["results_created"] == 2      # notes bien consolidées malgré tout
    assert "rendu KO" in (result["overlay_error"] or "")
    b = db.get(ScanBatch, batch.id)
    assert b.error and "rendu KO" in b.error
    assert b.status == "finalized"             # pas overlay_ready : bloc visible


def test_retry_after_overlay_error_rebuilds_only_overlays(mock_db, tmp_path, monkeypatch):
    """Relance d'un lot finalisé dont seuls les overlays ont échoué : retry ne
    refait PAS l'OCR, il régénère uniquement les copies corrigées."""
    from fastapi import BackgroundTasks

    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    batch.status = "finalized"
    batch.progress_json = {"finalized": {"done": True}}
    batch.error = "Copies corrigées non générées : rendu KO"
    db.commit()

    tasks = BackgroundTasks()
    r = scans_router.retry_batch(batch.id, tasks, db)
    assert r["ok"] is True
    assert db.get(ScanBatch, batch.id).error is None
    assert tasks.tasks[0].func is scans_router._run_build_overlays


def test_scan_config_flags_missing_mathpix(mock_db):
    """Sans clé Mathpix configurée, la correction se déclare indisponible (l'UI
    bloque le dépôt et affiche la bannière)."""
    db = mock_db
    assert scans_router.scan_config(db) == {"mathpix_configured": False}


def test_batch_summary_previews_notes(mock_db, tmp_path, monkeypatch):
    """La modale « Valider » a de quoi tout vérifier : avant correction, les
    réponses sont signalées et non notées ; après, chaque copie a ses points et
    sa note prévisionnelle, sans rien persister."""
    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)

    before = scans_router.batch_summary(batch.id, db)
    assert before["note_base"] == 20
    assert before["scanned_copies"] == 2
    assert before["pending_reviews"] == 2                 # manual_drawing → à corriger
    assert all(c["flagged"] == 1 and c["note"] is None for c in before["copies"])

    body = scans_router.ResolveIn(action="set_ratio", ratio=1.0)
    for it in scans_router.list_items(batch.id, "all", db):
        scans_router.resolve_response(it["response_id"], body, db, None)

    after = scans_router.batch_summary(batch.id, db)
    assert after["pending_reviews"] == 0
    assert all(c["flagged"] == 0 for c in after["copies"])
    for c in after["copies"]:
        assert abs(c["points_earned"] - 1.5) < 1e-6      # plein barème 1,5
        assert c["note"] == 20                           # sans-faute → 20/20
    # récapitulatif purement lecteur : aucune consolidation n'a été écrite
    from app.models import CopyResult
    assert db.query(CopyResult).count() == 0


def test_unreadable_scan_blocks_with_clear_error(mock_db, tmp_path, monkeypatch):
    """Scan sans aucune page reconnaissable (pas de QR/repères) : le lot ne file
    PAS en silence vers un « corrigé » vide (0 réponse, overlays vides) — il se
    bloque avec un message actionnable, pour que le professeur re-scanne net."""
    import numpy as np
    from app.models import FileObject, StudentResponse
    from app.services import scan_intake

    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.flush()

    # deux pages blanches : rastérisables, mais aucun QR ni fiduciel à trouver
    blank = np.full((400, 300, 3), 255, dtype=np.uint8)
    pdf_bytes = scan_intake.encode_pages_to_pdf([blank, blank])
    d = tmp_path / "assessments" / a.id / "scans" / "original"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{batch.id}.pdf"
    path.write_bytes(pdf_bytes)
    fo = FileObject(owner_type="scan_batch", owner_id=batch.id, storage_path=str(path))
    db.add(fo)
    db.flush()
    batch.source_file_id = fo.id
    db.commit()

    pipeline.process_batch(db, batch)

    b = db.get(ScanBatch, batch.id)
    assert b.error and "Aucune page reconnue" in b.error
    assert b.status != "graded"                       # pas de faux « corrigé »
    assert db.query(StudentResponse).count() == 0     # aucune réponse fabriquée


def test_reset_batch_purges_correction(mock_db, tmp_path, monkeypatch):
    """« Effacer la correction » : supprime le lot et ses réponses/décisions,
    remet les copies à « generated », sans toucher aux copies (CopyItem) ni au
    sujet — le lot disparaît, prêt pour un nouveau dépôt."""
    from app.models import Copy, StudentResponse
    from app.services.security import sign_page

    db = mock_db
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    a = _seed_manual(db)
    # scan « reconnu » : pages signées → ScannedPage.page_id renseigné, ce dont
    # dépend delete_scan_batch pour retrouver zones/réponses à purger
    for page in db.query(DocumentPage).all():
        page.qr_payload = sign_page(page.id)
    db.commit()
    batch = scan_intake.get_or_create_batch(db, a.id, None)
    db.commit()
    pipeline.process_batch(db, batch)
    assert db.query(StudentResponse).count() == 2

    r = scans_router.reset_batch(batch.id, db)
    assert r["ok"] is True
    assert db.get(ScanBatch, batch.id) is None
    assert db.query(StudentResponse).count() == 0
    assert all(c.status == "generated"
               for c in db.query(Copy).filter_by(assessment_id=a.id).all())

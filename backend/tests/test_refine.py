"""« Affiner la correction » (services.refine) : une fois le lot corrigé et
l'overlay prêt, le professeur corrige une note en réécrivant la réponse de
l'élève. On vérifie la renotation, la reconsolidation de la copie (note,
preuves de compétence), le suivi « overlay à régénérer » et que la
régénération ne touche qu'aux pages de la copie affinée."""
import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (
    Annotation, Assessment, Competency, CompetencyEvidence, Copy, CopyItem,
    CopyResult, DocumentPage, ExerciseCompetency, GradingDecision, OcrAttempt,
    ResponseZone, ScanBatch, ScannedPage, SchoolClass, Student,
    StudentCompetencyState, StudentResponse, User,
)
from app.routers import printing as printing_router
from app.routers import scans as scans_router
from app.services import pipeline, refine, scoring
from app.services.forgetting import apply_evidence


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


QCM = {"comparator": "qcm", "max_score": 1, "choices": ["$1$", "$2$", "$3$"],
       "bareme_points": 2}
NUM = {"comparator": "numeric", "max_score": 1, "bareme_points": 3}
TABLE_CELLS = [[{"type": "integer", "value": 5}, {"type": "integer", "value": 8}]]
TABLE = {"comparator": "table_cells", "max_score": 2, "cells": TABLE_CELLS,
         "bareme_points": 2}
DRAW = {"comparator": "manual", "max_score": 1, "bareme_points": 1}


def _item(db, copy, page, seq, rtype, grading, expected, y, *, catalog=None):
    item = CopyItem(copy_id=copy.id, catalog_id=catalog or f"cat-{seq}", sequence=seq,
                    difficulty=3, response_type=rtype, statement=f"Exercice {seq}",
                    correction="corrigé", expected_json=expected, grading_json=grading)
    db.add(item)
    db.flush()
    zone = ResponseZone(page_id=page.id, item_id=item.id, type=rtype,
                        x_pt=60, y_pt=y, w_pt=200, h_pt=40, meta_json={})
    db.add(zone)
    db.flush()
    return item, zone


def _answer(db, item, zone, *, score, max_score, selected=None, text="", cells=None,
            source="deterministic"):
    resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id,
                           selected_choices=selected or [], final_text=text)
    db.add(resp)
    db.flush()
    if cells is not None or text:
        db.add(OcrAttempt(zone_id=zone.id, provider="mathpix", text=text, latex=text,
                          confidence=0.99,
                          raw_json={"cells": cells} if cells is not None else {}))
    db.add(GradingDecision(response_id=resp.id, source=source, score=score,
                           max_score=max_score, confidence=0.99, tier="A",
                           reason_code="seed", status="auto"))
    db.flush()
    return resp


def _seed(db):
    """Deux copies finalisées (A avec des erreurs, B tout juste), lot terminé,
    overlay généré. Le QCM porte une compétence (preuve + courbe d'oubli)."""
    cls = SchoolClass(name="5A", grade_level="5e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Contrôle", type="control", note_base=20)
    db.add(a)
    db.flush()
    comp = Competency(framework_id="fw", code="A1.1", label="Calcul")
    db.add(comp)
    db.flush()
    db.add(ExerciseCompetency(exercise_id="cat-qcm", competency_id=comp.id))
    batch = ScanBatch(assessment_id=a.id, status="overlay_ready",
                      progress_json={"finalized": {"done": True},
                                     "overlay_ready": {"done": True}})
    db.add(batch)
    db.flush()
    responses = {}
    for k, name in enumerate(("Alice A", "Bruno B")):
        stu = Student(class_id=cls.id, name=name, order_index=k, llm_pseudonym=f"p{k}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status="finalized")
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        db.add(ScannedPage(batch_id=batch.id, source_index=k, page_id=page.id,
                           status="registered"))
        right = k == 1
        q, qz = _item(db, copy, page, 1, "qcm_single", QCM,
                      {"type": "choice", "correct": [1]}, 700, catalog="cat-qcm")
        t, tz = _item(db, copy, page, 2, "short_text", NUM,
                      {"type": "integer", "value": 12}, 600)
        tb, tbz = _item(db, copy, page, 3, "table_fill", TABLE,
                        {"type": "table", "cells": TABLE_CELLS}, 500)
        d, dz = _item(db, copy, page, 4, "manual_drawing", DRAW, {"type": "manual"}, 400)
        responses[name] = {
            "qcm": _answer(db, q, qz, score=1.0 if right else 0.0, max_score=1,
                           selected=[1] if right else [0]),
            "text": _answer(db, t, tz, score=1.0 if right else 0.0, max_score=1,
                            text="12" if right else "13"),
            "table": _answer(db, tb, tbz, score=2.0 if right else 1.0, max_score=2,
                             cells=["5", "8"] if right else ["5", "9"]),
            "draw": _answer(db, d, dz, score=1.0 if right else 0.0, max_score=1,
                            source="teacher"),
            "copy": copy, "qcm_item": q,
        }
        ev = CompetencyEvidence(student_id=stu.id, competency_id=comp.id, item_id=q.id,
                                mode="control", score_ratio=1.0 if right else 0.0,
                                difficulty=3)
        db.add(ev)
        db.flush()
        apply_evidence(db, ev)
        copy.appreciation_json = {"progress": [], "synthesis": ""}
        scoring.compute_copy_result(db, copy, a)
    db.commit()
    pipeline.build_overlays(db, batch)
    db.commit()
    return a, batch, responses


def _zone_points(view: dict, response_id: str) -> float:
    return next(z["points"] for p in view["pages"] for z in p["zones"]
                if z["response_id"] == response_id)


def _overlay_texts(assessment) -> list[str]:
    from pypdf import PdfReader
    path = cfg.data_dir / "assessments" / assessment.id / "overlays" / "correction_overlay.pdf"
    return [page.extract_text() or "" for page in PdfReader(path).pages]


def test_overview_lists_scanned_copies_in_pile_order(db):
    a, batch, responses = _seed(db)
    ov = scans_router.refine_overview(batch.id, db)
    assert [c["student"] for c in ov["copies"]] == ["Alice A", "Bruno B"]
    assert ov["stale"] is False and ov["rebuilding"] is False
    alice = responses["Alice A"]
    view = scans_router.refine_copy(alice["copy"].id, db)
    zones = view["pages"][0]["zones"]
    assert [z["sequence"] for z in zones] == [1, 2, 3, 4]
    for z in zones:   # rectangles cliquables normalisés sur la page
        r = z["rect"]
        assert 0 <= r["x"] < 1 and 0 <= r["y"] < 1 and 0 < r["w"] <= 1 and 0 < r["h"] <= 1
    detail = scans_router.refine_response_detail(alice["table"].id, db)
    assert detail["edit_kind"] == "cells"
    assert detail["answer"] == {"cells": ["5", "9"]}
    assert scans_router.refine_response_detail(alice["draw"].id, db)["edit_kind"] == "points"


def test_rewriting_answers_regrades_and_reconsolidates_the_copy(db):
    a, batch, responses = _seed(db)
    alice, bruno = responses["Alice A"], responses["Bruno B"]
    bruno_note = db.query(CopyResult).filter_by(copy_id=bruno["copy"].id).one().note
    before = db.query(CopyResult).filter_by(copy_id=alice["copy"].id).one().points_earned
    mastery_before = db.query(StudentCompetencyState).filter_by(
        student_id=alice["copy"].student_id).one().mastery

    r = scans_router.refine_answer(alice["qcm"].id,
                                   scans_router.RefineAnswerIn(selected_choices=[1]), db, None)
    assert r["uncertain"] is False and r["stale"] is True
    assert _zone_points(r["copy"], alice["qcm"].id) == 2.0
    d = refine._latest_decision(db, alice["qcm"].id)
    assert d.source == "teacher" and d.reason_code == "teacher_answer"

    r = scans_router.refine_answer(alice["text"].id,
                                   scans_router.RefineAnswerIn(text="12"), db, None)
    assert _zone_points(r["copy"], alice["text"].id) == 3.0
    r = scans_router.refine_answer(alice["table"].id,
                                   scans_router.RefineAnswerIn(cells=["5", "8"]), db, None)
    assert _zone_points(r["copy"], alice["table"].id) == 2.0
    # la marque d'overlay suit la nouvelle lecture (la plus récente fait foi)
    latest = (db.query(OcrAttempt).filter_by(zone_id=alice["table"].zone_id)
              .order_by(OcrAttempt.created_at.desc()).first())
    assert latest.provider == "teacher_ocr" and latest.raw_json["cells"] == ["5", "8"]

    result = db.query(CopyResult).filter_by(copy_id=alice["copy"].id).one()
    assert result.points_earned == before + 2 + 3 + 1
    assert r["copy"]["note"] == f"{scoring.format_points(result.note)}/20"
    # preuve de compétence du QCM réécrite, courbe d'oubli rejouée
    ev = db.query(CompetencyEvidence).filter_by(item_id=alice["qcm_item"].id).one()
    assert ev.score_ratio == 1.0
    mastery_after = db.query(StudentCompetencyState).filter_by(
        student_id=alice["copy"].student_id).one().mastery
    assert mastery_after > mastery_before
    # l'autre copie n'a pas bougé
    assert db.query(CopyResult).filter_by(copy_id=bruno["copy"].id).one().note == bruno_note
    assert db.get(ScanBatch, batch.id).refine_rev == 3


def test_unrecognized_answer_counts_wrong_and_points_stay_adjustable(db):
    a, batch, responses = _seed(db)
    bruno = responses["Bruno B"]
    r = scans_router.refine_answer(bruno["text"].id,
                                   scans_router.RefineAnswerIn(text="douze"), db, None)
    assert r["uncertain"] is True
    assert _zone_points(r["copy"], bruno["text"].id) == 0.0
    r = scans_router.refine_points(bruno["text"].id,
                                   scans_router.RefinePointsIn(points=3), db, None)
    assert _zone_points(r["copy"], bruno["text"].id) == 3.0
    r = scans_router.refine_points(bruno["draw"].id,
                                   scans_router.RefinePointsIn(points=0.5), db, None)
    assert _zone_points(r["copy"], bruno["draw"].id) == 0.5


def test_incoherent_answers_are_refused_without_side_effects(db):
    a, batch, responses = _seed(db)
    alice = responses["Alice A"]
    for rid, body in ((alice["qcm"].id, {"selected_choices": [7]}),
                      (alice["table"].id, {"cells": ["5"]}),
                      (alice["draw"].id, {"text": "x"})):
        with pytest.raises(HTTPException) as exc:
            scans_router.refine_answer(rid, scans_router.RefineAnswerIn(**body), db, None)
        assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        scans_router.refine_points(alice["text"].id,
                                   scans_router.RefinePointsIn(points=99), db, None)
    assert exc.value.status_code == 422
    assert db.get(ScanBatch, batch.id).refine_rev == 0
    assert refine._latest_decision(db, alice["qcm"].id).source == "deterministic"


def test_refine_is_refused_before_the_batch_is_finalized(db):
    a, batch, responses = _seed(db)
    batch.status = "review_pending"
    batch.progress_json = {"graded": {"done": True}}
    db.commit()
    with pytest.raises(HTTPException) as exc:
        scans_router.refine_overview(batch.id, db)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        scans_router.refine_answer(responses["Alice A"]["qcm"].id,
                                   scans_router.RefineAnswerIn(selected_choices=[1]), db, None)
    assert exc.value.status_code == 409


def test_stale_overlay_blocks_printing_until_regenerated(db):
    a, batch, responses = _seed(db)
    batch.overlay_printed = True
    db.commit()
    texts_before = _overlay_texts(a)
    scans_router.refine_answer(responses["Alice A"]["qcm"].id,
                               scans_router.RefineAnswerIn(selected_choices=[1]), db, None)
    view = scans_router._batch_view(db, db.get(ScanBatch, batch.id))
    assert view["overlay_stale"] is True
    assert any(s["label"] == "Overlay à régénérer" for s in view["segments"])

    user = User(email="p@x.fr", password_hash="x", role="teacher")
    with pytest.raises(HTTPException) as exc:
        printing_router.print_file(printing_router.PrintIn(
            assessment_id=a.id, file="correction_overlay.pdf", printer="X"), db, user)
    assert exc.value.status_code == 409

    n_annotations = db.query(Annotation).count()
    pipeline.build_overlays(db, db.get(ScanBatch, batch.id))
    b = db.get(ScanBatch, batch.id)
    db.refresh(b)
    assert refine.overlay_stale(b) is False
    # l'impression précédente est périmée : l'étape repasse « à imprimer »
    assert b.overlay_printed is False
    # régénérer n'empile pas les annotations
    assert db.query(Annotation).count() == n_annotations
    texts_after = _overlay_texts(a)
    assert len(texts_after) == len(texts_before) == 2
    # seule la page de la copie affinée change, à la même place dans la pile
    assert texts_after[1] == texts_before[1]
    assert texts_after[0] != texts_before[0]
    assert "Alice A" in texts_after[0]


def test_build_without_refinement_keeps_printed_flag(db):
    a, batch, _ = _seed(db)
    batch.overlay_printed = True
    db.commit()
    pipeline.build_overlays(db, batch)
    db.refresh(batch)
    assert batch.overlay_printed is True


def test_page_preview_is_a_jpeg_versioned_by_its_content(db):
    a, batch, responses = _seed(db)
    alice = responses["Alice A"]
    page = scans_router.refine_copy(alice["copy"].id, db)["pages"][0]
    resp = scans_router.refine_page_preview(alice["copy"].id, page["page_id"],
                                            page["version"], db)
    assert resp.body[:2] == b"\xff\xd8"
    assert resp.headers["X-Page-Version"] == page["version"]
    assert "immutable" in resp.headers["Cache-Control"]
    r = scans_router.refine_answer(alice["qcm"].id,
                                   scans_router.RefineAnswerIn(selected_choices=[1]), db, None)
    assert r["copy"]["pages"][0]["version"] != page["version"]
    stale = scans_router.refine_page_preview(alice["copy"].id, page["page_id"],
                                             page["version"], db)
    assert stale.headers["Cache-Control"] == "no-store"


def test_retry_of_finished_batch_regenerates_a_stale_overlay(db):
    a, batch, responses = _seed(db)
    scans_router.refine_answer(responses["Alice A"]["qcm"].id,
                               scans_router.RefineAnswerIn(selected_choices=[1]), db, None)
    tasks = BackgroundTasks()
    scans_router.retry_batch(batch.id, tasks, db)
    assert tasks.tasks and tasks.tasks[0].func is scans_router._run_build_overlays

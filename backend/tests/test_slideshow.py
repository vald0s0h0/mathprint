"""Diaporama de correction au tableau (services.slideshow) : une famille par groupe de
jumelles, les jumelles non servies comprises, le résultat de chaque élève, les copies
non rendues mises à part, et une page autonome dont les données ne ferment pas le script."""
import json
import re

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401
from app.config import settings as cfg
from app.db import Base
from app.models import (Assessment, Competency, Copy, CopyItem, DocumentPage, GeneratedExercise,
                        GradingDecision, ResponseZone, ScanBatch, SchoolClass, Student, StudentResponse)
from app.routers import slideshow as slideshow_router
from app.services import slideshow


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    # le rendu des cartes a ses propres tests : ici, une image factice
    monkeypatch.setattr(slideshow, "_cards", lambda db, row: {"card_blank": f"blank:{row.id}",
                                                              "card_answers": f"answers:{row.id}"})
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


QCM = {"comparator": "qcm", "max_score": 1, "choices": ["$1$", "$2$"], "bareme_points": 2}


def _seed(db, *, graded=True):
    cls = SchoolClass(name="6e4", grade_level="6e")
    db.add(cls)
    db.flush()
    a = Assessment(class_id=cls.id, title="Décimaux </script> test", type="control")
    db.add(a)
    db.flush()
    comp = Competency(framework_id="fw", code="A1", label="Comparer")
    db.add(comp)
    db.flush()
    twins = []
    for disp, status in ((1, "active"), (2, "twin")):
        g = GeneratedExercise(competency_id=comp.id, difficulty_level=1, statement=f"Quel est le plus petit ? {disp}",
                              response_type="qcm_single", status=status, twin_group="fam-A",
                              twin_jeu=1, twin_disposition=disp)
        db.add(g)
        twins.append(g)
    db.flush()
    batch = ScanBatch(assessment_id=a.id, status="overlay_ready")
    db.add(batch)
    db.flush()
    for k, (name, status, score) in enumerate((("Zoé", "finalized", 1.0), ("Adam", "finalized", 0.0),
                                               ("Basile", "absent", None))):
        stu = Student(class_id=cls.id, name=name, order_index=k, llm_pseudonym=f"p{k}")
        db.add(stu)
        db.flush()
        copy = Copy(assessment_id=a.id, student_id=stu.id, status=status)
        db.add(copy)
        db.flush()
        page = DocumentPage(copy_id=copy.id, page_no=1)
        db.add(page)
        db.flush()
        item = CopyItem(copy_id=copy.id, catalog_id="cat", generated_exercise_id=twins[0].id, sequence=1,
                        difficulty=3, response_type="qcm_single", statement=twins[0].statement,
                        correction="", expected_json={"type": "choice", "correct": [0]}, grading_json=QCM)
        db.add(item)
        db.flush()
        if score is None:
            continue
        zone = ResponseZone(page_id=page.id, item_id=item.id, type="qcm_single",
                            x_pt=60, y_pt=600, w_pt=200, h_pt=40, meta_json={})
        db.add(zone)
        db.flush()
        resp = StudentResponse(copy_item_id=item.id, zone_id=zone.id, selected_choices=[0 if score else 1])
        db.add(resp)
        db.flush()
        if graded:
            db.add(GradingDecision(response_id=resp.id, source="deterministic", score=score, max_score=1,
                                   confidence=1, tier="A", reason_code="qcm_match" if score else "qcm_wrong"))
    db.commit()
    return batch, twins


def test_one_family_with_its_unserved_twin_and_each_student_result(db):
    batch, twins = _seed(db)
    data = slideshow.build(db, batch)

    assert data["lot"] == {"id": batch.id, "title": "Décimaux </script> test", "class_name": "6e4"}
    [fam] = data["families"]
    assert fam["key"] == "fam-A"
    served, other = sorted(fam["variants"], key=lambda v: v["twin_disposition"])
    assert served["key"] == twins[0].id and "unserved" not in served
    assert other["key"] == twins[1].id and other["unserved"] is True
    assert other["card_answers"] == f"answers:{twins[1].id}"
    assert served["level"] == 1 and served["bareme"] == 2

    by_name = {s["name"]: s for s in data["students"]}
    assert [by_name[n]["order"] for n in ("Zoé", "Adam", "Basile")] == [0, 1, 2]
    assert by_name["Basile"]["status"] == "absent"
    res = {r["copy_id"]: r for r in data["responses"]}
    zoe, adam = res[by_name["Zoé"]["copy_id"]], res[by_name["Adam"]["copy_id"]]
    assert (zoe["score"], zoe["max"], zoe["graded"], zoe["blank"]) == (1.0, 1.0, True, False)
    assert (adam["score"], adam["reason"]) == (0.0, "qcm_wrong")
    # pas de scan sur disque : rien à recadrer, la copie reste listée
    assert "scan" not in zoe or zoe["scan"] is None


def test_page_is_self_contained_and_data_cannot_close_the_script(db):
    batch, _ = _seed(db)
    html = slideshow.render_html(slideshow.build(db, batch))
    assert "__DATA__" not in html
    assert html.count("</script>") == html.count("<script>")           # le titre « </script> » est échappé
    assert not re.search(r"<(link|script)[^>]+(href|src)=", html)      # aucune ressource externe
    payload = re.search(r"window\.DATA = (.*?);</script>", html, re.S).group(1)
    assert json.loads(payload.replace("<\\/", "</"))["lot"]["title"] == "Décimaux </script> test"


def test_download_names_the_file_after_the_subject(db):
    batch, _ = _seed(db)
    res = slideshow_router.batch_slideshow(batch.id, download=True, db=db)
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="diaporama-Decimaux-script-test.html"' in disposition
    assert "filename*=UTF-8''diaporama-D%C3%A9cimaux-script-test.html" in disposition
    assert "content-disposition" not in slideshow_router.batch_slideshow(batch.id, db=db).headers


def test_unknown_batch_is_404(db):
    with pytest.raises(HTTPException) as err:
        slideshow_router.batch_slideshow("nope", db=db)
    assert err.value.status_code == 404

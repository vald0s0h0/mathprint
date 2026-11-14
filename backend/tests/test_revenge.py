"""La revanche et l'objectif par devoir : choix de la jumelle, plafond de
remédiation, objectif figé sur la copie, verdicts de la copie corrigée."""
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, CopyItemResult, CopyResult,
    GeneratedExercise, SchoolClass, Student, StudentLearningEvent,
)
from app.services import distribution, draw_rules, error_journal, generation, pdfgen, revenge

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
ERR = "math.calc.priorite_operations"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# ------------------------------------------------------------------ choix de la jumelle

SIBS = {(j, d): f"{j}{d}" for j in (1, 2, 3) for d in (1, 2)}


def test_revenge_takes_a_new_set_of_values():
    assert revenge.revenge_choice(SIBS, {(1, 1): T0}, 2) == (2, 2)
    assert revenge.revenge_choice(SIBS, {(1, 1): T0, (2, 1): T0}, 1) == (3, 1)


def test_all_sets_seen_takes_the_oldest_in_the_other_disposition():
    served = {(1, 1): T0 + timedelta(days=20), (2, 2): T0, (3, 1): T0 + timedelta(days=10)}
    assert revenge.revenge_choice(SIBS, served, 1) == (2, 1)
    served = {k: T0 + timedelta(days=i) for i, k in enumerate(SIBS)}
    choice = revenge.revenge_choice(SIBS, served, 1)
    assert choice[0] == 1 and choice != max(served, key=served.get)


def test_single_disposition_never_repeats_when_another_exists():
    sibs = {(1, 1): "a", (2, 1): "b"}
    assert revenge.revenge_choice(sibs, {(2, 1): T0}, 1) == (1, 1)
    assert revenge.revenge_choice(sibs, {(1, 1): T0, (2, 1): T0 + timedelta(days=1)}, 1) == (1, 1)


def test_revenge_counts_in_the_remediation_ceiling():
    row = GeneratedExercise(id="x", twin_group="fam", grading_json={}, source="forge")
    ctx = draw_rules.DrawContext().for_student({ERR}, {"fam": ERR})
    assert draw_rules.is_revenge(row, ctx) and draw_rules.is_remediation(row, ctx)
    assert draw_rules.preference_key(row, ctx)[0] is False
    ctx.remediation_left = 0
    assert not draw_rules.is_revenge(row, ctx)
    assert draw_rules.DrawContext().for_student(set()).remediation_left == 0


# ------------------------------------------------------------------ bout en bout

def _seed(db):
    fw = CompetencyFramework(grade_level="3e", name="3e")
    db.add(fw); db.flush()
    comps = [Competency(framework_id=fw.id, code=f"A1.{i}", short_id=f"A1.{i}",
                        label=lab, chapter_code="A1", chapter_name="Calcul", order_index=i)
             for i, lab in enumerate(["Calculer avec les priorités opératoires",
                                      "Additionner des nombres relatifs"])]
    db.add_all(comps)
    cls = SchoolClass(name="3eA", grade_level="3e")
    db.add(cls); db.flush()
    st = Student(class_id=cls.id, name="Alex", llm_pseudonym="E-1", order_index=0)
    db.add(st); db.flush()
    n = 0
    for c in comps:
        for level in (1, 2, 3):
            for i in range(8):
                db.add(GeneratedExercise(
                    competency_id=c.id, difficulty_level=level, variant=i, source="indigo",
                    statement=f"[{c.short_id}] Calcule {n + 2} fois {n + 7} puis ajoute {n}.",
                    response_type="short_text", expected_json={"value": f"v{n}"},
                    grading_json={"bareme_points": 1.0}, status="active"))
                n += 1
    family = []
    for j in (1, 2, 3):
        for d in (1, 2):
            row = GeneratedExercise(
                competency_id=comps[0].id, difficulty_level=2, variant=100 + j * 10 + d,
                source="indigo", statement=f"Famille : calcule {j} + {d} × {j + 4}.",
                response_type="short_text", expected_json={"value": f"f{j}{d}"},
                grading_json={"bareme_points": 1.0,
                              "feedback": {"wrong_answers": [{"error": ERR, "text": "Priorité !"}]}},
                status="active" if (j, d) == (1, 1) else "twin",
                twin_group="fam", twin_jeu=j, twin_disposition=d)
            family.append(row)
    db.add_all(family); db.flush()
    other = db.query(GeneratedExercise).filter_by(competency_id=comps[0].id).first()
    # devoir précédent : la jumelle (1,1) servie, l'erreur vue dessus et ailleurs
    old = Assessment(class_id=cls.id, title="Avant", personalization_mode="individual")
    db.add(old); db.flush()
    prev = Copy(assessment_id=old.id, student_id=st.id, generated_at=T0)
    db.add(prev); db.flush()
    for k, ex in enumerate((family[0], other)):
        item = CopyItem(copy_id=prev.id, catalog_id="cat", sequence=k, response_type="short_text",
                        statement="x", correction="", generated_exercise_id=ex.id)
        db.add(item); db.flush()
        db.add(StudentLearningEvent(student_id=st.id, competency_id=comps[0].id, copy_id=prev.id,
                                    copy_item_id=item.id, exercise_id=ex.id, error_code=ERR,
                                    observed=True, occurred_at=T0))
    a = Assessment(class_id=cls.id, type="training", title="Entraînement", pages_target=1,
                   personalization_mode="individual", note_base=20,
                   blueprint_json={"competency_ids": [c.id for c in comps], "exercise_source": "indigo"})
    db.add(a); db.commit()
    return a, comps, st, family




def test_individual_subject_serves_the_revenge_and_states_a_goal(db):
    a, comps, st, family = _seed(db)
    assert error_journal.active_errors(db, st.id) == {ERR}
    assert revenge.revenge_groups(db, st.id) == {"fam": ERR}
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()
    copy = db.query(Copy).filter_by(assessment_id=a.id).one()
    assert copy.goal_json["source"] == "error"
    assert copy.goal_json["competency_id"] == comps[0].id
    assert copy.goal_json["text"] == "Ton objectif : réussir « Calculer avec les priorités opératoires »"
    items = db.query(CopyItem).filter_by(copy_id=copy.id).all()
    marked = [i for i in items if (i.grading_json or {}).get("revanche")]
    assert len(marked) == 1
    served = db.get(GeneratedExercise, marked[0].generated_exercise_id)
    assert served.twin_group == "fam" and served.twin_jeu != 1


def test_goal_falls_back_to_the_most_urgent_competency(db):
    a, comps, st, _f = _seed(db)
    db.query(StudentLearningEvent).delete(); db.commit()
    goal = revenge.choose_goal(db, st.id, "math", [c.id for c in comps], [comps[1].id, comps[0].id])
    assert goal["source"] == "priority" and goal["competency_id"] == comps[1].id


# ------------------------------------------------------------------ copie corrigée

def test_goal_verdict_and_vanquished_items(db):
    a, comps, st, family = _seed(db)
    copy = Copy(assessment_id=a.id, student_id=st.id, generated_at=T0 + timedelta(days=10),
                goal_json={"competency_id": comps[0].id})
    db.add(copy); db.flush()
    assert revenge.goal_verdict(db, copy) is None
    res = CopyResult(copy_id=copy.id, assessment_id=a.id, student_id=st.id)
    db.add(res); db.flush()
    items = []
    for k, ex in enumerate(family[1:3]):
        item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=k, response_type="short_text",
                        statement="x", correction="", generated_exercise_id=ex.id)
        db.add(item); db.flush(); items.append(item)
        db.add(CopyItemResult(copy_result_id=res.id, copy_item_id=item.id, competency_id=comps[0].id,
                              success_ratio=1.0 if k else 0.5))
        db.add(StudentLearningEvent(student_id=st.id, competency_id=comps[0].id, copy_id=copy.id,
                                    copy_item_id=item.id, exercise_id=ex.id, error_code=ERR,
                                    observed=False, occurred_at=T0 + timedelta(days=10 + k)))
    db.commit()
    assert revenge.goal_verdict(db, copy) is True
    assert revenge.vanquished_items(db, copy) == {items[1].id: ERR}
    assert error_journal.active_errors(db, st.id) == set()


# ------------------------------------------------------------------ impression

def test_goal_is_truncated_cleanly_or_omitted():
    long = "Ton objectif : réussir « " + "calculer " * 30 + "»"
    fitted = pdfgen.goal_fit(long, 60 * 2.83)
    assert fitted.endswith("… »") and len(fitted) < len(long)
    assert pdfgen.goal_fit(long, 10) == ""
    assert pdfgen.goal_fit("Ton objectif : réussir « x »", 200) == "Ton objectif : réussir « x »"


def test_header_geometry_is_unchanged_by_the_goal():
    geo = pdfgen.header_geometry("control")
    assert geo["goal_verdict"]["x"] + geo["goal_verdict"]["w"] <= geo["meta"]["x"] + geo["meta"]["w"]
    assert geo["goal"]["x1"] < geo["goal_verdict"]["x"]


def test_copy_and_overlay_render(tmp_path):
    """Rendu réel : sujet avec objectif et carte « Revanche », overlay avec verdict
    et « Erreur vaincue ». Les images sont écrites pour relecture visuelle."""
    import fitz
    item = {"item_id": "i1", "response_type": "short_text", "statement": "Calcule $3 + 4 \\times 2$.",
            "expected": {"value": "11"}, "grading": {"bareme_points": 1.0}, "choices": [],
            "level3": 2, "revanche": True}
    buf = BytesIO()
    c = canvas.Canvas(buf)
    zones = pdfgen.render_copy(c, student_name="Alex", class_name="3eA", title="Entraînement",
                               assessment_type="control", items=[item],
                               pages_meta=[{"page_id": "p1", "payload": "000000000001"}],
                               goal="Ton objectif : réussir « Calculer avec les priorités opératoires »")
    c.save()
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    text = doc[0].get_text()
    assert "Ton objectif" in text and "Revanche" in text
    doc[0].get_pixmap(dpi=110).save(str(tmp_path / "copie.png"))
    z = zones[0]
    strip = (z["meta"] or {}).get("correction_strip") or {
        "x_pt": z["x_pt"], "y_pt": z["y_pt"] - 20, "w_pt": z["w_pt"], "h_pt": 16, "fs": 8}
    out = tmp_path / "overlay.pdf"
    pdfgen.render_overlay(str(out), copies_annotations=[{
        "page_id": "p1", "student": "Alex", "assessment_type": "control", "layout": pdfgen.LAYOUT_V3,
        "note": "15/20", "goal_reached": False,
        "page_zones": [{"x_pt": z["x_pt"], "y_pt": z["y_pt"], "w_pt": z["w_pt"], "h_pt": z["h_pt"],
                        "score": 1, "max_score": 1, "full_credit": True, "response_type": "short_text",
                        "strip": strip, "text": "", "marks": [], "feedback": "",
                        "vanquished": True}]}])
    odoc = fitz.open(str(out))
    otext = odoc[0].get_text()
    assert "Objectif : pas encore" in otext and "Erreur vaincue" in otext
    odoc[0].get_pixmap(dpi=110).save(str(tmp_path / "overlay.png"))

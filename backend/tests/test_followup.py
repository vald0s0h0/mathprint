"""Suivi des élèves : groupes de besoin, bilan pour la famille, prêt pour l'examen.
Tout est déterministe et calculé à la lecture : on fige la date du jour."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import current_user
from app.main import app
from app.models import (
    Assessment, ClassTeaching, Competency, CompetencyEvidence, CompetencyFramework,
    CompetencyStateHistory, Copy, CopyItem, CopyItemResult, CopyResult, GeneratedExercise,
    SchoolClass, Student, StudentCompetencyState, StudentLearningEvent, StudentReport,
    Subject, TeacherProfile, User,
)
from app.services import exam_readiness, family_report, need_groups

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
ERR_A = "math.calc.priorite_operations"
ERR_B = "math.calc.signe"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Subject(id="math", name="Mathématiques"))
    session.commit()
    yield session
    session.close()


def _seed(db, n_students=5):
    fw = CompetencyFramework(program_id="fr", subject_id="math", grade_level="3e", name="3e",
                             status="published")
    db.add(fw); db.flush()
    comps = []
    for i, (label, chapter, hours) in enumerate([
            ("Calculer avec les priorités opératoires", "Calcul", 6.0),
            ("Additionner des nombres relatifs", "Calcul", 6.0),
            ("Construire un triangle", "Géométrie", 12.0),
            ("Utiliser le théorème de Pythagore", "Géométrie", 12.0)]):
        c = Competency(framework_id=fw.id, code=f"C{i}", short_id=f"A{i}", label=label,
                       chapter_code=chapter[:3], chapter_name=chapter, chapter_hours=hours,
                       order_index=i)
        db.add(c); comps.append(c)
    cls = SchoolClass(name="3eA", grade_level="3e", program_id="fr")
    db.add(cls); db.flush()
    students = [Student(class_id=cls.id, name=f"Élève {i}", llm_pseudonym=f"E-{i}", order_index=i)
                for i in range(n_students)]
    db.add_all(students)
    a = Assessment(class_id=cls.id, title="Contrôle 1", type="control", status="finalized",
                   framework_id=fw.id, scheduled_at=NOW - timedelta(days=3))
    db.add(a); db.flush()
    return fw, comps, cls, students, a


def _copy(db, a, student, comp, ratio, errors):
    """Une copie corrigée : un exercice, des erreurs observées ou évitées."""
    copy = Copy(assessment_id=a.id, student_id=student.id, status="finalized")
    db.add(copy); db.flush()
    res = CopyResult(copy_id=copy.id, assessment_id=a.id, student_id=student.id)
    db.add(res); db.flush()
    item = CopyItem(copy_id=copy.id, catalog_id="cat", sequence=0, response_type="short_text",
                    statement="x", correction="", generated_exercise_id="served")
    db.add(item); db.flush()
    db.add(CopyItemResult(copy_result_id=res.id, copy_item_id=item.id, competency_id=comp.id,
                          student_id=student.id, success_ratio=ratio, occurred_at=a.scheduled_at))
    for code, observed in errors.items():
        db.add(StudentLearningEvent(student_id=student.id, competency_id=comp.id, copy_id=copy.id,
                                    copy_item_id=item.id, exercise_id="served", error_code=code,
                                    observed=observed, occurred_at=a.scheduled_at))
    return copy


def _bank(db, comp, code=None, n=6, family=None):
    rows = []
    for i in range(n):
        grading = {"feedback": {"wrong_answers": [{"error": code, "text": "t"}]}} if code else {}
        if family:
            grading["family_id"] = family
        row = GeneratedExercise(competency_id=comp.id, difficulty_level=1 + i % 3, variant=i,
                                statement=f"Calculer $\\frac{{{i}}}{{2}}$", correction=str(i),
                                grading_json=grading, status="active")
        db.add(row); rows.append(row)
    db.flush()
    return rows


def _state(db, student, comp, mastery, days, stability=60.0):
    db.add(StudentCompetencyState(student_id=student.id, competency_id=comp.id, mastery=mastery,
                                  stability=stability, last_seen_at=NOW - timedelta(days=days)))


# ------------------------------------------------------------- groupes de besoin

def test_groups_by_dominant_error_with_board_and_exercises(db):
    fw, comps, cls, st, a = _seed(db)
    for s in st[:3]:
        _copy(db, a, s, comps[0], 0.0, {ERR_A: True, ERR_B: False})
    _copy(db, a, st[3], comps[1], 0.0, {ERR_B: True, ERR_A: False})
    _copy(db, a, st[4], comps[2], 1.0, {})
    _bank(db, comps[0], ERR_A)
    _bank(db, comps[1], ERR_B, n=2)
    retired = _bank(db, comps[0], ERR_A, n=1)[0]
    retired.status = "twin"
    db.commit()
    out = need_groups.need_groups(db, a.id, at=NOW)
    assert out["corrected"] == 5
    first = out["groups"][0]
    assert first["kind"] == "error" and first["code"] == ERR_A
    assert [s["name"] for s in first["students"]] == ["Élève 0", "Élève 1", "Élève 2"]
    assert "priorité" in first["label"]                 # libellé de la taxonomie, pas le code
    assert 3 <= len(first["exercises"]) <= 5
    assert retired.id not in {e["id"] for e in first["exercises"]}
    second = out["groups"][1]
    assert second["code"] == ERR_B and not second["enough_exercises"]
    board = out["board"]
    assert board[0] == {"code": ERR_A, "label": board[0]["label"], "students": 3, "occasions": 4}
    assert board[1]["students"] == 1 and board[1]["occasions"] == 4
    assert out["without_need"] == 1


def test_fragile_competency_when_no_error_and_one_card_per_family(db):
    fw, comps, cls, st, a = _seed(db, n_students=2)
    for s in st:
        _copy(db, a, s, comps[2], 0.2, {})
    _bank(db, comps[2], n=3, family="fam")
    _bank(db, comps[2], n=3)
    db.commit()
    out = need_groups.need_groups(db, a.id, at=NOW)
    g = out["groups"][0]
    assert g["kind"] == "competency" and g["competency_id"] == comps[2].id
    assert len(g["students"]) == 2
    assert len(g["exercises"]) == 4                     # 1 de la famille + 3 libres
    assert out["board"] == []


def test_unseen_exercises_first(db):
    fw, comps, cls, st, a = _seed(db, n_students=1)
    rows = _bank(db, comps[0], ERR_A, n=6)
    copy = _copy(db, a, st[0], comps[0], 0.0, {ERR_A: True})
    # l'élève a déjà eu les trois premières cartes
    for i, r in enumerate(rows[:3]):
        db.add(CopyItem(copy_id=copy.id, catalog_id="cat", sequence=10 + i, response_type="short_text",
                        statement="x", correction="", generated_exercise_id=r.id))
    db.commit()
    g = need_groups.need_groups(db, a.id, at=NOW)["groups"][0]
    ids = [e["id"] for e in g["exercises"]]
    assert {r.id for r in rows[3:]} <= set(ids[:3])
    assert all(e["seen_by"] == 0 for e in g["exercises"][:3])


def test_group_sheet_is_a_pdf(db):
    fw, comps, cls, st, a = _seed(db, n_students=1)
    _copy(db, a, st[0], comps[0], 0.0, {ERR_A: True})
    _bank(db, comps[0], ERR_A)
    db.commit()
    data = need_groups.need_groups(db, a.id, at=NOW)
    assert need_groups.group_sheet_pdf(data, data["groups"][0]).startswith(b"%PDF")


# ------------------------------------------------------------- prêt pour l'examen

def test_readiness_separates_never_evaluated_from_fragile(db):
    fw, comps, cls, st, a = _seed(db, n_students=1)
    _state(db, st[0], comps[0], 0.9, days=1)            # solide
    _state(db, st[0], comps[2], 0.3, days=1)            # à consolider
    _state(db, st[0], comps[3], 0.9, days=300, stability=20)   # maîtrisée, oubliée
    db.commit()
    r = exam_readiness.student_readiness(db, st[0].id, "math", at=NOW)
    assert r["exam"] == "brevet"
    assert (r["solid_count"], r["review_count"], r["never_count"]) == (1, 2, 1)
    # heures : 6 h / 2 compétences = 180 min, 12 h / 2 = 360 min ; total 1080
    assert r["solid_share"] == pytest.approx(180 / 1080, abs=1e-3)
    assert r["never_share"] == pytest.approx(180 / 1080, abs=1e-3)
    reasons = {e["competency_id"]: e["reason"] for e in r["to_review"]}
    assert reasons == {comps[2].id: "à consolider", comps[3].id: "à rafraîchir"}
    assert r["never"][0]["competency_id"] == comps[1].id


def test_class_readiness_and_plan_pdf(db):
    fw, comps, cls, st, a = _seed(db, n_students=2)
    _state(db, st[0], comps[0], 0.9, days=1)
    db.commit()
    out = exam_readiness.class_readiness(db, cls.id, "math", at=NOW)
    assert [r["solid_count"] for r in out["students"]] == [1, 0]
    assert {c["competency_id"] for c in out["never_for_class"]} == {c.id for c in comps[1:]}
    data = exam_readiness.student_readiness(db, st[0].id, "math", at=NOW)
    assert exam_readiness.revision_plan_pdf(data).startswith(b"%PDF")


def test_exam_names():
    assert exam_readiness.exam_name(SchoolClass(program_id="ci", grade_level="3e")) == "BEPC"
    assert exam_readiness.exam_name(SchoolClass(program_id="ci", grade_level="tle")) == "BAC"
    assert exam_readiness.exam_name(SchoolClass(program_id="fr", grade_level="5e")) is None


# ------------------------------------------------------------- bilan pour la famille

def test_periods():
    assert family_report.period_bounds("2026-2027-T2") == (
        datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2027, 3, 1, tzinfo=timezone.utc))
    assert family_report.current_period(NOW) == "2026-2027-T1"
    assert family_report.current_period(datetime(2027, 1, 5, tzinfo=timezone.utc)) == "2026-2027-T2"
    assert family_report.period_label("2026-2027-T1") == "1er trimestre 2026-2027"
    with pytest.raises(ValueError):
        family_report.period_bounds("2026-T1")


def _error_rows(db, student, comp, code, pattern, start):
    """Une ligne par carte, dans l'ordre : True = erreur observée."""
    for i, observed in enumerate(pattern):
        db.add(StudentLearningEvent(student_id=student.id, competency_id=comp.id, copy_id=f"c{i}",
                                    copy_item_id=f"{code}-{i}", exercise_id=f"{code}-ex{i}",
                                    error_code=code, observed=observed,
                                    occurred_at=start + timedelta(days=7 * i)))


def test_vanquished_errors_counts_closures_in_period(db):
    fw, comps, cls, st, a = _seed(db, n_students=1)
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    _error_rows(db, st[0], comps[0], ERR_A, [True, True, False, False], start)   # vaincue
    _error_rows(db, st[0], comps[0], ERR_B, [True, True, False], start)          # encore active
    db.commit()
    assert family_report.vanquished_errors(
        db, st[0].id, "math", *family_report.period_bounds("2026-2027-T1")) == [ERR_A]
    assert family_report.vanquished_errors(
        db, st[0].id, "math", *family_report.period_bounds("2026-2027-T2")) == []


def test_family_report_content_and_workflow(db):
    fw, comps, cls, st, a = _seed(db, n_students=1)
    s = st[0]
    for c, m in zip(comps, (0.95, 0.85, 0.75, 0.3)):
        _state(db, s, c, m, days=2)
    for i, (day, mastery) in enumerate([(60, 0.3), (30, 0.5), (5, 0.7)]):
        ev = CompetencyEvidence(student_id=s.id, competency_id=comps[0].id, score_ratio=1.0,
                                observed_at=NOW - timedelta(days=day))
        db.add(ev); db.flush()
        db.add(CompetencyStateHistory(student_id=s.id, competency_id=comps[0].id,
                                      after_json={"mastery": mastery}, evidence_id=ev.id))
    db.commit()
    data = family_report.compute(db, s.id, "math", "2026-2027-T1", at=NOW)
    assert [x["label"] for x in data["acquired"]] == [c.label for c in comps[:3]]
    assert data["to_work"]["competency_id"] == comps[3].id
    assert data["to_work"]["gesture"] in dict((k, g) for k, _p, g in family_report.GESTURES)["geometrie"]
    assert [p["value"] for p in data["curve"]] == [0.3, 0.5, 0.7]
    assert data["curve_marker"] == 0                    # tous les points sont dans le trimestre
    report = family_report.prepare(db, s.id, "math", "2026-2027-T1", at=NOW)
    assert report.status == "draft" and report.prompt_version == family_report.FAMILY_VERSION
    family_report.edit(report, {"gesture": "Lire ensemble", "teacher_note": "Bravo"})
    report.status = "approved"
    with pytest.raises(ValueError):
        family_report.prepare(db, s.id, "math", "2026-2027-T1", at=NOW)
    read = family_report.read(report)
    assert read["to_work"]["gesture"] == "Lire ensemble" and read["teacher_note"] == "Bravo"
    assert family_report.pdf([read, read]).startswith(b"%PDF")


def test_gesture_kinds():
    assert family_report.competency_kind(Competency(label="Calculer une fraction", chapter_name="",
                                                    domain_name="")) == "calcul"
    assert family_report.competency_kind(Competency(label="Lire", chapter_name="", domain_name="")) == "general"


# ------------------------------------------------------------- routes et droits

@pytest.fixture
def client(db):
    user = User(id="u1", email="prof@test.fr", role="admin")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    yield TestClient(app), user
    app.dependency_overrides.clear()


def test_routes(client, db):
    c, _user = client
    fw, comps, cls, st, a = _seed(db, n_students=2)
    _copy(db, a, st[0], comps[0], 0.0, {ERR_A: True})
    _bank(db, comps[0], ERR_A)
    _state(db, st[0], comps[0], 0.9, days=1)
    db.commit()
    r = c.get(f"/api/grades/assessments/{a.id}/need-groups")
    assert r.status_code == 200 and r.json()["groups"][0]["code"] == ERR_A
    assert c.get(f"/api/grades/assessments/{a.id}/need-groups/1/sheet.pdf").content.startswith(b"%PDF")
    assert c.get(f"/api/grades/assessments/{a.id}/need-groups/9/sheet.pdf").status_code == 404
    assert c.get(f"/api/grades/classes/{cls.id}/exam-readiness").json()["students"][0]["solid_count"] == 1
    assert c.get(f"/api/students/{st[0].id}/exam-readiness").json()["solid_count"] == 1
    assert c.get(f"/api/students/{st[0].id}/revision-plan.pdf").content.startswith(b"%PDF")

    period = "2026-2027-T1"
    preview = c.get(f"/api/students/{st[0].id}/family-report?period={period}").json()
    assert preview["report_id"] is None
    assert c.post(f"/api/students/by-class/{cls.id}/family-reports?period={period}").json() == {"prepared": 2}
    rows = c.get(f"/api/students/by-class/{cls.id}/family-reports?period={period}").json()["students"]
    assert {r["status"] for r in rows} == {"draft"}
    assert c.get(f"/api/students/by-class/{cls.id}/family-reports.pdf?period={period}").status_code == 409
    rid = rows[0]["report_id"]
    assert c.get(f"/api/students/family-reports/{rid}/sheet.pdf").status_code == 409
    assert c.patch(f"/api/students/family-reports/{rid}", json={"teacher_note": "Bien"}).status_code == 200
    assert c.patch(f"/api/students/family-reports/{rid}", json={"status": "approved"}).json()["status"] == "approved"
    assert c.patch(f"/api/students/family-reports/{rid}", json={"gesture": "x"}).status_code == 409
    pdf = c.get(f"/api/students/by-class/{cls.id}/family-reports.pdf?period={period}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert db.get(StudentReport, rid).status == "exported"
    # les bilans famille ne se mêlent pas aux comptes rendus
    assert c.get(f"/api/students/{st[0].id}/reports").json() == []
    assert c.get(f"/api/students/{st[0].id}/family-report?period=2026").status_code == 422


def test_teacher_sees_only_own_classes(client, db):
    from fastapi import Request
    from app.services.access import check_path_resources, scope_session
    c, user = client

    def scoped(request: Request):
        scope_session(db, user, None)
        check_path_resources(db, request)
        return user
    app.dependency_overrides[current_user] = scoped
    fw, comps, cls, st, a = _seed(db, n_students=1)
    _copy(db, a, st[0], comps[0], 0.0, {ERR_A: True})
    other = TeacherProfile(user_id="u1", name="Prof", active=True)
    db.add(other); db.commit()
    a_id, s_id, cls_id, prof_id = a.id, st[0].id, cls.id, other.id
    db.expunge_all()          # une vraie requête part d'une session neuve
    user.role = "teacher"
    assert c.get(f"/api/grades/assessments/{a_id}/need-groups").status_code == 404
    assert c.get(f"/api/students/{s_id}/exam-readiness").status_code == 404
    assert c.get(f"/api/students/by-class/{cls_id}/family-reports").status_code == 404
    db.add(ClassTeaching(class_id=cls_id, subject_id="math", teacher_id=prof_id)); db.commit()
    db.expunge_all()
    assert c.get(f"/api/students/by-class/{cls_id}/family-reports").status_code == 200

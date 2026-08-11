"""Assistant « Créer mon sujet » : le sujet composé à la main doit sortir de
l'imprimante EXACTEMENT comme le professeur l'a posé.

Trois invariants surveillés ici :
  1. le placement — chaque carte sur la page ET la colonne demandées, et une
     page laissée vide reste une page du sujet ;
  2. les guides — trois modes, dont un qui doit vraiment récupérer de la place
     et un qui doit imprimer sans jamais bouger la géométrie ;
  3. les variantes — un élève n'est jamais servi au hasard (niveau, tourniquet).
"""
import sys
import tempfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, DocumentPage,
    GeneratedExercise, ResponseZone, SchoolClass, Student, StudentLevel,
)
from app.services import manual_subject, pdfgen
from app.services.pdfgen import DEFAULT_TEMPLATES

LONG_GUIDE = ("Pense à réduire au même dénominateur avant d'additionner.\n"
              "Vérifie ensuite que la fraction obtenue est bien irréductible.\n"
              "Piège classique : additionner les dénominateurs entre eux.")


def _item(statement: str, correction: str = "", guides: str = pdfgen.GUIDES_OVERLAY,
          **kw) -> dict:
    return {"kind": "exercise", "item_id": kw.get("item_id", statement[:12]),
            "statement": statement, "response_type": kw.get("response_type", "short_text"),
            "choices": [], "level5": 3, "figure": None, "correction": correction,
            "guides": guides,
            "grading": {"max_score": 1, "comparator": "numeric"}, "inline": False}


def _render(items, placement=None, min_pages=0, pages=8):
    out = Path(tempfile.mkdtemp()) / "copy.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    pages_meta = [{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(pages)]
    zones = pdfgen.render_copy(c, student_name="Test Élève", class_name="3eB",
                               title="Test", assessment_type="training", items=items,
                               pages_meta=pages_meta, font_size=9,
                               placement=placement, min_pages=min_pages)
    c.save()
    return zones, out


def _height(item: dict) -> float:
    tpl = DEFAULT_TEMPLATES
    return pdfgen.estimate_item_height(
        item, int(tpl["exercise"]["font_size"]), int(tpl["exercise"]["math_size"]),
        tpl["exercise"], tpl["lesson"])


# ------------------------------------------------------------- placement

def test_placement_puts_each_card_on_the_requested_page_and_column():
    items = [_item(f"Calcule ${i} + {i}$.", item_id=f"i{i}") for i in range(4)]
    # volontairement dispersées : page 1 colonne droite, page 0 colonne droite…
    plan = [(0, 0), (0, 1), (1, 0), (1, 1)]
    order = [0, 1, 2, 3]
    zones, _ = _render([items[i] for i in order], placement=plan)
    got = {z["item_id"]: (z["page_index"], round(z["x_pt"])) for z in zones}
    left_x = round(pdfgen.MARGIN)
    right_x = round(pdfgen.MARGIN + pdfgen.COL_W + pdfgen.COL_GAP)
    assert got["i0"] == (0, left_x)
    assert got["i1"] == (0, right_x)
    assert got["i2"] == (1, left_x)
    assert got["i3"] == (1, right_x)


def test_placement_can_skip_a_page_entirely():
    # rien sur la page 2 : la carte suivante doit quand même atterrir page 3
    items = [_item("Un.", item_id="a"), _item("Deux.", item_id="b")]
    zones, _ = _render(items, placement=[(0, 0), (2, 1)])
    got = {z["item_id"]: z["page_index"] for z in zones}
    assert got == {"a": 0, "b": 2}


def test_placement_overflowing_column_spills_instead_of_overlapping():
    # une colonne ne peut pas contenir 30 cartes : elles débordent sur les
    # colonnes suivantes plutôt que de s'empiler les unes sur les autres.
    items = [_item(f"Calcule ${i} \\times 7$.", item_id=f"x{i}") for i in range(30)]
    zones, _ = _render(items, placement=[(0, 0)] * 30, pages=12)
    assert max(z["page_index"] for z in zones) > 0
    # aucune carte ne descend sous la limite basse de colonne
    assert all(z["y_pt"] >= pdfgen._BOTTOM_LIMIT - 1 for z in zones)


def test_min_pages_emits_pages_left_blank_by_the_teacher():
    from pypdf import PdfReader
    _zones, path = _render([_item("Une seule carte.")], placement=[(0, 0)], min_pages=3)
    assert len(PdfReader(str(path)).pages) == 3


def test_without_placement_the_greedy_layout_is_unchanged():
    # garde-fou de non-régression : la pipeline automatique ne passe pas de
    # placement, son rendu doit rester au bit près celui d'avant.
    items = [_item(f"Calcule ${i} + 1$.") for i in range(9)]
    zones, _ = _render(items)
    assert pdfgen.pages_needed([_height(i) for i in items]) == \
        max(z["page_index"] for z in zones) + 1


# ---------------------------------------------------------------- guides

def test_guides_none_reclaims_the_space_of_the_guide_text():
    with_guide = _item("Additionne.", LONG_GUIDE, pdfgen.GUIDES_OVERLAY)
    without = _item("Additionne.", LONG_GUIDE, pdfgen.GUIDES_NONE)
    saved = _height(with_guide) - _height(without)
    assert saved > 8 * pdfgen.mm, f"seulement {saved / pdfgen.mm:.1f} mm récupérés"


def test_printed_guide_does_not_change_the_geometry():
    # même variante = même mise en page pour tous les élèves : seul l'encre
    # change entre un élève de niveau 1 à 4 et les autres.
    overlay = _item("Additionne.", LONG_GUIDE, pdfgen.GUIDES_OVERLAY)
    printed = _item("Additionne.", LONG_GUIDE, pdfgen.GUIDES_PRINT)
    assert _height(overlay) == _height(printed)
    zo, _ = _render([overlay], placement=[(0, 0)])
    zp, _ = _render([printed], placement=[(0, 0)])
    for k in ("x_pt", "y_pt", "w_pt", "h_pt"):
        assert zo[0][k] == zp[0][k]
    assert zo[0]["meta"]["correction_strip"]["guides"] == pdfgen.GUIDES_OVERLAY
    assert zp[0]["meta"]["correction_strip"]["guides"] == pdfgen.GUIDES_PRINT


def test_overlay_never_reprints_a_guide_it_should_not():
    """L'overlay n'imprime le corrigé QUE dans le mode qui lui a réservé la
    place. En GUIDES_NONE le texte n'a jamais été composé (il déborderait sur
    la carte suivante) ; en GUIDES_PRINT il est déjà sur la feuille."""
    drawn = []

    class _Spy:
        def __getattr__(self, name):
            def _noop(*a, **k):
                return None
            return _noop

    def _fake_rich(c, x, y, layout, **kw):
        drawn.append(layout)
        return y

    original = pdfgen._draw_rich
    pdfgen._draw_rich = _fake_rich
    try:
        for mode, expected in ((pdfgen.GUIDES_NONE, 0), (pdfgen.GUIDES_PRINT, 0),
                               (pdfgen.GUIDES_OVERLAY, 1)):
            drawn.clear()
            pdfgen._draw_correction_strip(_Spy(), {
                "x_pt": 0, "y_pt": 0, "w_pt": 200, "h_pt": 20,
                "score": 1, "max_score": 2, "text": LONG_GUIDE,
                "strip": {"x_pt": 0, "y_pt": 0, "w_pt": 200, "h_pt": 20,
                          "fs": 8, "guides": mode},
            }, col=None)
            assert len(drawn) == expected, mode
    finally:
        pdfgen._draw_rich = original


# -------------------------------------------------------------- variantes

@pytest.mark.parametrize("level,expected", [
    (1, "facile"), (4, "facile"), (5, "moyen"), (7, "moyen"), (8, "difficile"),
    (10, "difficile"),
])
def test_level_variants_follow_the_student_level(level, expected):
    blueprint = {"variant_kind": "level",
                 "variants": [{"key": k} for k in manual_subject.LEVEL_KEYS]}
    idx = manual_subject.variant_for_student(blueprint, level, student_index=0)
    assert blueprint["variants"][idx]["key"] == expected


def test_level_variants_fall_back_when_a_level_was_not_composed():
    # le professeur n'a composé que facile/difficile : un élève « moyen » ne
    # doit pas se retrouver sans sujet.
    blueprint = {"variant_kind": "level",
                 "variants": [{"key": "facile"}, {"key": "difficile"}]}
    idx = manual_subject.variant_for_student(blueprint, 6, student_index=0)
    assert 0 <= idx < 2


def test_anticheat_variants_alternate_between_neighbours():
    blueprint = {"variant_kind": "anticheat",
                 "variants": [{"key": "A"}, {"key": "B"}, {"key": "C"}]}
    got = [manual_subject.variant_for_student(blueprint, 5, i) for i in range(6)]
    assert got == [0, 1, 2, 0, 1, 2]


@pytest.mark.parametrize("mode,level,expected", [
    ("overlay", 2, pdfgen.GUIDES_OVERLAY),
    ("overlay", 9, pdfgen.GUIDES_OVERLAY),
    ("print_fragile", 4, pdfgen.GUIDES_PRINT),
    ("print_fragile", 5, pdfgen.GUIDES_OVERLAY),
    ("none", 1, pdfgen.GUIDES_NONE),
    ("none", 9, pdfgen.GUIDES_NONE),
])
def test_guides_mode_per_student(mode, level, expected):
    assert manual_subject.guides_for_student(mode, level) == expected


# ------------------------------------------------------- bout en bout (DB)

@pytest.fixture
def db():
    # StaticPool + check_same_thread=False : le TestClient sert les requêtes
    # depuis un AUTRE thread que celui du test, et SQLite refuse sinon de
    # rejouer la connexion (« created in thread … used in thread … »).
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db, n_students=4):
    cls = SchoolClass(name="3eB", grade_level="3e")
    db.add(cls)
    db.flush()
    fw = CompetencyFramework(grade_level="3e", name="Test")
    db.add(fw)
    db.flush()
    comps = []
    for i, (chap, label) in enumerate([("A1", "Fractions"), ("A1", "Puissances"),
                                       ("B1", "Aires")]):
        c = Competency(framework_id=fw.id, code=f"C{i}", short_id=f"{chap}.{i}",
                       label=label, order_index=i, chapter_code=chap,
                       chapter_name=f"Chapitre {chap}", domain_code=chap[0],
                       domain_name="Nombres")
        db.add(c)
        comps.append(c)
    db.flush()
    rows = []
    for i, comp in enumerate(comps):
        for k in range(2):
            row = GeneratedExercise(
                competency_id=comp.id, difficulty_level=3, statement=f"Calcule {i}-{k}.",
                correction=LONG_GUIDE, response_type="short_text",
                expected_json={"value": 1}, grading_json={"max_score": 1,
                                                          "comparator": "numeric",
                                                          "bareme_points": 1},
                source="indigo", kind="probleme" if k else "application", status="active")
            db.add(row)
            rows.append(row)
    students = []
    for i in range(n_students):
        s = Student(class_id=cls.id, name=f"E{i} Test", order_index=i,
                    llm_pseudonym=f"eleve-{i}")
        db.add(s)
        db.flush()
        db.add(StudentLevel(student_id=s.id, level=2 if i == 0 else 6))
        students.append(s)
    db.flush()
    db.commit()
    return cls, comps, rows, students


def test_pool_separates_exercises_from_chapter_problems(db):
    _cls, comps, _rows, _st = _seed(db)
    # une SEULE compétence du chapitre A1 est cochée
    out = manual_subject.pool(db, [comps[0].id], pages=2)
    ex_comps = {e["competency_id"] for e in out["exercises"]}
    assert ex_comps == {comps[0].id}, "un exercice appartient à SA compétence"
    # les problèmes du chapitre A1 remontent, y compris ceux de la compétence
    # voisine non cochée ; ceux du chapitre B1 restent dehors
    pb_comps = {p["competency_id"] for p in out["problems"]}
    assert pb_comps == {comps[0].id, comps[1].id}
    assert comps[2].id not in pb_comps
    assert all(p["kind"] == "probleme" for p in out["problems"])
    assert out["metrics"]["cols_per_page"] == 2
    assert len(out["metrics"]["column_h"]) == 2
    assert out["exercises"][0]["height_pt"] > out["exercises"][0]["height_pt_no_guide"]


def _plan(rows, variants):
    return {"mode": "manual", "guides": "print_fragile", "variant_kind": "level",
            "competency_ids": [], "variants": variants}


def test_generate_manual_places_items_and_assigns_variants(db, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    cls, _comps, rows, students = _seed(db)

    variants = [
        {"key": "facile", "label": "Facile", "items": [
            {"exercise_id": rows[0].id, "page": 0, "col": 0, "rank": 0}]},
        {"key": "moyen", "label": "Moyen", "items": [
            {"exercise_id": rows[2].id, "page": 0, "col": 1, "rank": 0},
            {"exercise_id": rows[3].id, "page": 1, "col": 0, "rank": 0}]},
        {"key": "difficile", "label": "Difficile", "items": [
            {"exercise_id": rows[4].id, "page": 0, "col": 0, "rank": 0}]},
    ]
    a = Assessment(class_id=cls.id, title="Sur mesure", pages_target=2,
                   blueprint_json=_plan(rows, variants))
    db.add(a)
    db.commit()

    report = manual_subject.generate_manual_job(db, a, None, font_size=9)
    assert report["mode"] == "manual" and report["copies"] == len(students)
    assert not report["warnings"]

    copies = db.query(Copy).filter_by(assessment_id=a.id).all()
    assert len(copies) == len(students)
    by_student = {c.student_id: c for c in copies}
    # l'élève de niveau 2 reçoit « facile », les autres (niveau 6) « moyen »
    assert by_student[students[0].id].variant_key == "facile"
    assert {by_student[s.id].variant_key for s in students[1:]} == {"moyen"}
    # deux élèves d'une même variante = strictement la même copie (sujet commun)
    seeds = {by_student[s.id].seed for s in students[1:]}
    assert len(seeds) == 1

    # la variante « moyen » porte bien ses 2 cartes, sur les pages demandées
    moyen = by_student[students[1].id]
    items = db.query(CopyItem).filter_by(copy_id=moyen.id).all()
    assert len(items) == 2
    pages = {p.id: p.page_no for p in db.query(DocumentPage).filter_by(copy_id=moyen.id).all()}
    zones = db.query(ResponseZone).filter(
        ResponseZone.item_id.in_([i.id for i in items])).all()
    assert sorted(pages[z.page_id] for z in zones) == [1, 2]
    assert moyen.total_pages == 2       # la 2e page existe même pour les autres
    assert (tmp_path / "assessments" / a.id / "generated" / "subject_batch.pdf").exists()


def test_generate_manual_prints_the_guide_only_for_fragile_students(db, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    cls, _comps, rows, students = _seed(db)
    variants = [{"key": "facile", "label": "Facile", "items": [
        {"exercise_id": rows[0].id, "page": 0, "col": 0, "rank": 0}]}]
    a = Assessment(class_id=cls.id, title="Guides", pages_target=1,
                   blueprint_json={"mode": "manual", "guides": "print_fragile",
                                   "variant_kind": "none", "variants": variants})
    db.add(a)
    db.commit()
    manual_subject.generate_manual_job(db, a, None, font_size=9)

    modes = {}
    for copy in db.query(Copy).filter_by(assessment_id=a.id).all():
        item = db.query(CopyItem).filter_by(copy_id=copy.id).first()
        zone = db.query(ResponseZone).filter_by(item_id=item.id).first()
        modes[copy.student_id] = zone.meta_json["correction_strip"]["guides"]
    assert modes[students[0].id] == pdfgen.GUIDES_PRINT     # niveau 2
    assert modes[students[1].id] == pdfgen.GUIDES_OVERLAY   # niveau 6


def test_generate_manual_refuses_a_plan_pointing_at_a_deleted_exercise(db, tmp_path,
                                                                       monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    cls, _comps, rows, _students = _seed(db)
    a = Assessment(class_id=cls.id, title="Cassé", pages_target=1,
                   blueprint_json={"mode": "manual", "guides": "overlay",
                                   "variant_kind": "none", "variants": [
                                       {"key": "A", "items": [
                                           {"exercise_id": "disparu", "page": 0,
                                            "col": 0, "rank": 0}]}]})
    db.add(a)
    db.commit()
    with pytest.raises(ValueError, match="disparu|banque"):
        manual_subject.generate_manual_job(db, a, None, font_size=9)


# --------------------------------------------------------------- endpoints

@pytest.fixture
def client(db, tmp_path, monkeypatch):
    """API réelle branchée sur la base du test — vérifie surtout que les
    routes littérales de l'assistant ne sont pas avalées par /{assessment_id}."""
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: User(
        email="prof@test", password_hash="x", role="admin")
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_pool_endpoint_is_not_swallowed_by_the_assessment_id_route(client, db):
    _cls, comps, _rows, _st = _seed(db)
    r = client.get(f"/api/assessments/manual/pool?competency_ids={comps[0].id}&pages=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exercises"] and body["problems"]
    assert client.get("/api/assessments/manual/pool").status_code == 422


def test_manual_plan_endpoint_saves_and_validates(client, db):
    cls, comps, rows, _st = _seed(db)
    a = Assessment(class_id=cls.id, title="T", pages_target=1)
    db.add(a)
    db.commit()

    # exercice posé au-delà de la dernière page : refusé
    bad = {"competency_ids": [comps[0].id], "guides": "none", "variant_kind": "none",
           "variants": [{"key": "A", "items": [
               {"exercise_id": rows[0].id, "page": 3, "col": 0, "rank": 0}]}]}
    assert client.post(f"/api/assessments/{a.id}/manual-plan", json=bad).status_code == 422

    ok = {**bad, "variant_kind": "anticheat", "variants": [
        {"key": "A", "items": [{"exercise_id": rows[0].id, "page": 0, "col": 0, "rank": 0}]},
        {"key": "B", "items": [{"exercise_id": rows[2].id, "page": 0, "col": 1, "rank": 0}]},
    ]}
    r = client.post(f"/api/assessments/{a.id}/manual-plan", json=ok)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "variants": 2, "items": 2}
    db.refresh(a)
    assert a.blueprint_json["mode"] == "manual"
    assert a.personalization_mode == "common_variants"      # jamais "individual"
    assert client.get("/api/assessments").json()[0]["manual"] is True


def test_manual_plan_refuses_an_empty_plan(client, db):
    cls, comps, _rows, _st = _seed(db)
    a = Assessment(class_id=cls.id, title="T", pages_target=1)
    db.add(a)
    db.commit()
    r = client.post(f"/api/assessments/{a.id}/manual-plan", json={
        "competency_ids": [comps[0].id], "variants": [{"key": "A", "items": []}]})
    assert r.status_code == 422
    # et la génération d'un sujet manuel sans carte est refusée aussi
    a.blueprint_json = {"mode": "manual", "variants": []}
    db.commit()
    assert client.post(f"/api/assessments/{a.id}/generate", json={}).status_code == 422


def test_duplicate_manual_subject_keeps_each_students_variant(client, db, monkeypatch):
    from types import SimpleNamespace
    from app.services import job_worker

    cls, _comps, rows, students = _seed(db, n_students=2)
    variants = [
        {"key": "A", "label": "Variante A", "items": [
            {"exercise_id": rows[0].id, "page": 0, "col": 0, "rank": 0}]},
        {"key": "B", "label": "Variante B", "items": [
            {"exercise_id": rows[2].id, "page": 0, "col": 0, "rank": 0}]},
    ]
    source = Assessment(
        class_id=cls.id, title="Fractions", status="ready", pages_target=1,
        personalization_mode="common_variants",
        blueprint_json={"mode": "manual", "guides": "print_fragile",
                        "variant_kind": "anticheat", "variants": variants})
    db.add(source)
    db.flush()
    # Affectation volontairement inverse du tourniquet : la duplication doit
    # reprendre cette trace, pas recalculer depuis le rang dans la classe.
    db.add_all([
        Copy(assessment_id=source.id, student_id=students[0].id,
             seed=11, variant_key="B"),
        Copy(assessment_id=source.id, student_id=students[1].id,
             seed=12, variant_key="A"),
    ])
    db.commit()

    def fake_enqueue(session, assessment, _font_size):
        assessment.status = "queued"
        session.commit()
        return SimpleNamespace(id="job-v2")

    monkeypatch.setattr(job_worker, "enqueue_generation", fake_enqueue)
    response = client.post(f"/api/assessments/{source.id}/duplicate")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["title"] == "Fractions v2"
    duplicate = db.get(Assessment, body["id"])
    assert duplicate.blueprint_json["variants"] == variants
    assert duplicate.blueprint_json["duplicate_version"] == 2
    assert duplicate.blueprint_json["duplicate_student_variants"] == {
        students[0].id: "B", students[1].id: "A"}
    listed = {row["id"]: row for row in client.get("/api/assessments").json()}
    assert listed[duplicate.id]["duplicate_version"] == 2

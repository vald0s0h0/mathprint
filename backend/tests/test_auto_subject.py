"""Assistant « Créer un sujet », mode AUTOMATIQUE : une copie personnalisée par
élève, dont la composition suit le niveau 1-10.

Invariants surveillés ici :
  1. l'ordre — toute copie se lit du plus simple au plus difficile, problèmes
     en dernier, et la feuille imprimée est celle qui a été simulée ;
  2. les guides — en dégradé selon le niveau (tout guidé en bas de l'échelle,
     rien en haut), avec la part réglée par le professeur pour l'élève moyen ;
  3. les problèmes — jamais piochés comme des exercices, réservés aux élèves
     forts, nombreux et difficiles pour les très forts ;
  4. l'accès — il faut des sujets corrigés pour connaître le niveau des élèves.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, GeneratedExercise,
    SchoolClass, Student, StudentLevel,
)
from app.services import generation, pdfgen, student_history


# ------------------------------------------------------------ pdfgen : paliers

def _col(b: int) -> float:
    return pdfgen.column_capacity(b)


def test_without_tiers_the_packing_is_the_historical_ffd():
    hs = [120.0, 300.0, 80.0, 200.0, 60.0, 250.0, 90.0]
    tiers = [0] * len(hs)
    assert pdfgen.pack_columns(hs) == pdfgen.pack_columns(hs, tiers)


def test_the_reading_order_never_goes_back_down_in_difficulty():
    hs = [150, 90, 260, 70, 180, 120, 300, 60, 110, 220, 95, 140]
    tiers = [3, 1, 12, 2, 1, 3, 11, 2, 1, 13, 2, 1]
    order = pdfgen.pack_reading_order([float(h) for h in hs], tiers)
    read = [tiers[i] for i in order]
    assert read == sorted(read), f"lecture non monotone : {read}"
    assert sorted(order) == list(range(len(hs)))


def test_problems_always_come_after_every_exercise():
    # un problème « facile » reste APRÈS un exercice difficile
    hs = [100.0, 100.0, 100.0]
    tiers = [pdfgen.reading_tier(1, True), pdfgen.reading_tier(3, False),
             pdfgen.reading_tier(2, False)]
    order = pdfgen.pack_reading_order(hs, tiers)
    assert order[-1] == 0


def test_tiered_packing_never_overfills_a_column():
    import random
    rnd = random.Random(7)
    for _ in range(40):
        n = rnd.randint(3, 20)
        hs = [rnd.uniform(40, 330) for _ in range(n)]
        tiers = [rnd.choice([1, 2, 3, 11, 12, 13]) for _ in range(n)]
        for b, col in enumerate(pdfgen.pack_columns(hs, tiers)):
            assert sum(hs[i] for i in col) <= _col(b) + 1e-6


def test_pack_placement_is_what_render_copy_draws():
    """Le placement explicite doit être rendu tel quel : mêmes pages, mêmes
    colonnes, et le nombre de pages annoncé est celui du PDF."""
    import tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    items, tiers = [], []
    for i in range(14):
        lines = 1 + (i * 5) % 9
        items.append({"kind": "exercise", "item_id": f"it{i}",
                      "statement": "\n".join(f"Ligne {k} de l'exercice {i}." for k in range(lines)),
                      "response_type": "short_text", "choices": [], "level3": 1 + i % 3,
                      "figure": None, "correction": "", "grading": {"max_score": 1},
                      "inline": False})
        tiers.append(pdfgen.reading_tier(1 + i % 3, i % 5 == 0))
    tpl = pdfgen.DEFAULT_TEMPLATES
    hs = [pdfgen.estimate_item_height(it, int(tpl["exercise"].get("font_size", 9)),
                                      int(tpl["exercise"].get("math_size", 9)),
                                      tpl["exercise"]) for it in items]
    order, slots, n_pages = pdfgen.pack_placement(hs, tiers)
    ordered = [items[i] for i in order]

    out = Path(tempfile.mkdtemp()) / "c.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    zones = pdfgen.render_copy(
        c, student_name="Élève", class_name="3e", title="T", assessment_type="training",
        items=ordered, pages_meta=[{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"}
                                   for i in range(8)],
        font_size=9, placement=slots)
    c.save()
    by_item = {z["item_id"]: z for z in zones}
    for it, (page, col) in zip(ordered, slots):
        z = by_item[it["item_id"]]
        assert z["page_index"] == page
        x_col = 0 if z["x_pt"] < pdfgen.PAGE_W / 2 else 1
        assert x_col == col
    assert max(z["page_index"] for z in zones) + 1 == n_pages


# ------------------------------------------------- guides et problèmes : dosage

def test_guide_ratio_is_a_gradient_anchored_on_the_teacher_setting():
    ratios = [student_history.guide_ratio(lvl, 0.4) for lvl in range(1, 11)]
    assert ratios[:3] == [1.0, 1.0, 1.0]
    assert ratios[5] == pytest.approx(0.4)          # niveau 6 = élève moyen
    assert ratios[8:] == [0.0, 0.0]
    assert all(a >= b for a, b in zip(ratios, ratios[1:])), "dégradé monotone"


@pytest.mark.parametrize("ratio", [0.0, 0.25, 0.5, 0.67, 1.0])
def test_guide_quota_hits_its_ratio(ratio):
    q = student_history.GuideQuota(ratio)
    for _ in range(12):
        q.take(True, q.decide(True))
    assert q.guided == round(ratio * 12 + 0.49)     # arrondi vers le haut
    assert not q.decide(False), "une carte sans guide n'a rien à imprimer"


def test_guide_quota_release_gives_the_slot_back():
    q = student_history.GuideQuota(0.5)
    on = q.decide(True)
    q.take(True, on)
    q.release(True, on)
    assert (q.guidable, q.guided) == (0, 0)


def test_only_strong_students_get_problems_and_the_strongest_get_more():
    assert all(student_history.problem_plan(lvl) is None for lvl in range(1, 7))
    shares = [student_history.problem_plan(lvl)[0] for lvl in (7, 8, 9, 10)]
    assert shares == sorted(shares)
    # 9-10 : surtout du difficile
    for lvl in (9, 10):
        mix = student_history.problem_plan(lvl)[1]
        assert mix[2] > 0.5 and mix[0] == 0


# ----------------------------------------------------------- génération réelle

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_TEMPLATES = [
    "Calcule le produit de {a} par {b}.",
    "Quel est le périmètre d'un carré de côté {a} cm ?",
    "Simplifie l'expression $ {a}x + {b}x $.",
    "Convertis {a} minutes en secondes.",
    "Range dans l'ordre croissant : {a} ; {b} ; {c}.",
    "Quelle est la moitié de {a} ?",
    "Donne l'écriture décimale de {a} dixièmes.",
    "Écris {a} sous forme de puissance de {b}.",
]
_PROBLEMS = [
    "Un train roule à {a} km/h pendant {b} h. Quelle distance parcourt-il ?",
    "Une piscine se remplit de {a} litres par minute. Combien de temps pour {b} litres ?",
    "Un commerçant achète {a} articles et en revend {b}. Combien lui en reste-t-il ?",
    "Le périmètre d'un rectangle vaut {a} cm, sa longueur {b} cm. Quelle est sa largeur ?",
    "Un cycliste parcourt {a} km en {b} h. Quelle est sa vitesse moyenne ?",
    "Un réservoir contient {a} L et perd {b} L par jour. Quand sera-t-il vide ?",
]


def _seed(db, levels: list[int], corrected: int = 5, pages: int = 2, problems=True):
    fw = CompetencyFramework(grade_level="3e", name="3e")
    db.add(fw)
    db.flush()
    comps = []
    for i in range(2):
        c = Competency(framework_id=fw.id, code=f"A1.{i}", short_id=f"A1.{i}",
                       label=f"Compétence {i}", domain_code="A", domain_name="Nombres",
                       chapter_code="A1", chapter_name="Opérations", order_index=i)
        db.add(c)
        comps.append(c)
    cls = SchoolClass(name="3eA", grade_level="3e")
    db.add(cls)
    db.flush()
    n = 0
    for comp in comps:
        for lvl in (1, 2):
            for k, tpl in enumerate(_TEMPLATES):
                # un exercice sur deux porte un encadré d'aide
                guide = "\n{{aide}} Pense à poser l'opération." if k % 2 == 0 else ""
                db.add(GeneratedExercise(
                    competency_id=comp.id, difficulty_level=lvl, variant=k,
                    statement=f"[{comp.short_id}·{lvl}] " + tpl.format(a=n + 2, b=n + 3, c=n + 5) + guide,
                    correction="", source="indigo", kind="application",
                    response_type="short_text", expected_json={"value": f"v{n}"},
                    grading_json={"bareme_points": 1.0}, status="active"))
                n += 1
    for lvl in (1, 2, 3):
        for k, tpl in enumerate(_PROBLEMS):
            db.add(GeneratedExercise(
                competency_id=comps[k % 2].id, difficulty_level=lvl, variant=k,
                statement=f"[P{lvl}] " + tpl.format(a=n + 7, b=n + 9),
                correction="", source="indigo", kind="probleme",
                response_type="short_text", expected_json={"value": f"p{n}"},
                grading_json={"bareme_points": 2.0}, status="active"))
            n += 1
    students = []
    for i, lvl in enumerate(levels):
        s = Student(class_id=cls.id, name=f"Élève {i}", order_index=i,
                    llm_pseudonym=f"E{i}", active=True)
        db.add(s)
        db.flush()
        db.add(StudentLevel(student_id=s.id, level=lvl))
        students.append(s)
    for k in range(corrected):
        db.add(Assessment(class_id=cls.id, title=f"Corrigé {k}", status="finalized"))
    a = Assessment(class_id=cls.id, type="training", title="Auto", pages_target=pages,
                   personalization_mode="individual", note_base=20)
    a.blueprint_json = {"mode": "auto", "competency_ids": [c.id for c in comps],
                        "exercise_source": "bank", "guides": "auto",
                        "guides_medium_pct": 50, "problems": problems}
    db.add(a)
    db.commit()
    return a, students


@pytest.fixture
def rendered(monkeypatch):
    """Cartes réellement envoyées à pdfgen, par élève (le PDF lui-même ne dit
    pas quel encadré a été imprimé)."""
    calls: dict[str, list[dict]] = {}
    real = pdfgen.render_copy

    def spy(*args, **kw):
        calls[kw["student_name"]] = list(kw["items"])
        return real(*args, **kw)

    monkeypatch.setattr(pdfgen, "render_copy", spy)
    return calls


def _is_problem(db, item) -> bool:
    ex_id = item.get("item_id")
    from app.models import CopyItem
    ci = db.get(CopyItem, ex_id)
    return db.get(GeneratedExercise, ci.generated_exercise_id).kind == "probleme"


def test_weak_students_get_no_problem_and_every_guide(db, rendered):
    a, students = _seed(db, levels=[2])
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    items = rendered[students[0].name]
    assert not any(_is_problem(db, it) for it in items)
    guidable = [it for it in items if "{{aide}}" in it["statement"]]
    assert guidable, "la banque de test doit fournir des exercices à guide"
    assert all(it["guides"] == pdfgen.GUIDES_INCLUDE for it in guidable)


def test_very_strong_students_get_many_hard_problems_last_and_no_guide(db, rendered):
    a, students = _seed(db, levels=[10])
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    items = rendered[students[0].name]
    flags = [_is_problem(db, it) for it in items]
    assert sum(flags) >= 2, "un élève de niveau 10 reçoit beaucoup de problèmes"
    # problèmes en dernier, sans exception
    first_pb = flags.index(True)
    assert all(flags[first_pb:]), f"exercice après un problème : {flags}"
    pb_levels = [it["level3"] for it, f in zip(items, flags) if f]
    assert pb_levels.count(3) >= len(pb_levels) / 2, pb_levels
    guidable = [it for it in items if "{{aide}}" in it["statement"]]
    assert all(it["guides"] == pdfgen.GUIDES_NONE for it in guidable)


def test_problems_stay_out_when_the_option_is_off(db, rendered):
    a, students = _seed(db, levels=[10], problems=False)
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    assert not any(_is_problem(db, it) for it in rendered[students[0].name])


def test_every_copy_reads_from_easy_to_hard(db, rendered):
    a, students = _seed(db, levels=[3, 6, 8, 10])
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    for st in students:
        items = rendered[st.name]
        tiers = [pdfgen.reading_tier(it["level3"], _is_problem(db, it)) for it in items]
        assert tiers == sorted(tiers), f"{st.name} : {tiers}"
    for copy in db.query(Copy).filter_by(assessment_id=a.id).all():
        assert copy.total_pages <= 2


def test_medium_students_follow_the_teacher_share(db, rendered):
    a, students = _seed(db, levels=[6], problems=False)
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    guidable = [it for it in rendered[students[0].name] if "{{aide}}" in it["statement"]]
    on = sum(it["guides"] == pdfgen.GUIDES_INCLUDE for it in guidable)
    assert guidable and abs(on / len(guidable) - 0.5) <= 1 / len(guidable) + 1e-9


# --------------------------------------------------------------------- l'API

@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: User(
        email="prof@test", password_hash="x", role="admin")
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _draft(db, class_id):
    a = Assessment(class_id=class_id, title="Nouveau", pages_target=1)
    db.add(a)
    db.commit()
    return a


def test_auto_needs_five_corrected_subjects(client, db):
    a, students = _seed(db, levels=[4, 7], corrected=3)
    cls_id = a.class_id
    e = client.get(f"/api/assessments/auto-eligibility?class_id={cls_id}").json()
    assert e["eligible"] is False and e["corrected"] == 3 and e["required"] == 5
    assert {lv["level"]: lv["count"] for lv in e["levels"]}[7] == 1

    draft = _draft(db, cls_id)
    comp_ids = a.blueprint_json["competency_ids"]
    r = client.post(f"/api/assessments/{draft.id}/auto-plan",
                    json={"competency_ids": comp_ids, "problems": True})
    assert r.status_code == 409


def test_auto_plan_saves_an_individual_subject(client, db):
    a, _students = _seed(db, levels=[5], corrected=5)
    draft = _draft(db, a.class_id)
    comp_ids = a.blueprint_json["competency_ids"]
    r = client.post(f"/api/assessments/{draft.id}/auto-plan",
                    json={"competency_ids": comp_ids, "guides_medium_pct": 30,
                          "problems": True})
    assert r.status_code == 200, r.text
    db.refresh(draft)
    assert draft.personalization_mode == "individual"
    bp = draft.blueprint_json
    assert (bp["mode"], bp["guides"], bp["guides_medium_pct"], bp["problems"]) == \
        ("auto", "auto", 30, True)
    row = next(x for x in client.get("/api/assessments").json() if x["id"] == draft.id)
    assert row["auto"] is True and row["manual"] is False


def test_competency_matrix_counts_the_bank(client, db):
    a, _students = _seed(db, levels=[5])
    m = client.get("/api/assessments/competency-matrix?grade_level=3e").json()
    ch = m["domains"][0]["chapters"][0]
    assert ch["problem_count"] == 18
    assert all(c["exercise_count"] == 16 for c in ch["competencies"])


def test_the_first_cards_already_follow_the_level_mix():
    """Régression : la trame est plus longue que la copie ; rangés en bloc,
    les dérivés faciles occupaient toutes les premières cases et un élève de
    niveau 5 ne recevait QUE du facile sur une page."""
    for level in (5, 7, 9):
        quota = student_history.level_quota(level, 24)
        head = student_history._level_sequence(quota)[:8]
        mix = student_history.LEVEL_MIX[level]
        for lvl in (1, 2, 3):
            assert abs(head.count(lvl) - mix[lvl - 1] * 8) <= 1, (level, head)

"""Référentiels ivoiriens : import, identifiants, temps et ordre, mise à jour sûre."""
import copy
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, get_db
from app.deps import current_user
from app.models import (Competency, CompetencyFramework, ExerciseCatalog, ExerciseCompetency,
                        SchoolClass, User)
from app.routers import misc
from app.services.ci_curriculum import load_data, seed_ci_curriculum
from app.services.education import framework_for, seed_structure

ID = re.compile(r"^CI-2023-MATH-(6E|5E|4E|3E|2NDE|1RE|TLE)(-(A|A1|A2|C|D))?-([A-H])(\d+)\.(\d+)$")
EXPECTED = {  # (niveau, série) -> (leçons, compétences = contenus), relevés dans les programmes DPFC
    ("6e", None): (13, 239), ("5e", None): (12, 204), ("4e", None): (11, 148), ("3e", None): (14, 250),
    ("2nde", "ci-A"): (8, 109), ("2nde", "ci-C"): (15, 250),
    ("1re", "ci-A1"): (7, 111), ("1re", "ci-A2"): (7, 110), ("1re", "ci-C"): (17, 386), ("1re", "ci-D"): (15, 332),
    ("tle", "ci-A1"): (8, 165), ("tle", "ci-A2"): (7, 136), ("tle", "ci-C"): (19, 390), ("tle", "ci-D"): (12, 277),
}


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with make() as db:
        seed_structure(db)
        assert seed_ci_curriculum(db) == len(EXPECTED)
    return make


def ci_frameworks(db):
    return {(f.grade_level, f.path_id): f for f in db.query(CompetencyFramework).filter_by(program_id="ci")
            if f.status == "published"}


def test_every_official_framework_is_filled_in_place(factory):
    with factory() as db:
        published = ci_frameworks(db)
        assert set(published) == set(EXPECTED)
        for key, (lessons, total) in EXPECTED.items():
            fw = published[key]
            comps = db.query(Competency).filter_by(framework_id=fw.id).all()
            assert len(comps) == total, key
            assert len({c.chapter_code for c in comps}) == lessons, key
            assert fw.program_year == 2023 and fw.version == "2023.09"
            assert fw.cycle == (1 if key[1] is None else 2)
        # séries techniques hors périmètre : toujours vides, en brouillon
        drafts = db.query(CompetencyFramework).filter_by(program_id="ci", path_id="ci-F2").all()
        assert drafts and all(f.status == "draft" for f in drafts)
        assert not db.query(Competency).filter(Competency.framework_id.in_([f.id for f in drafts])).count()


def test_one_competency_per_official_content_with_its_verb_in_order(factory):
    """Chaque contenu officiel devient une ligne « verbe + contenu », dans l'ordre du
    programme ; le verbe de l'habileté se répète d'une ligne à l'autre."""
    data = load_data()
    with factory() as db:
        for fw_data in data["frameworks"]:
            fw = ci_frameworks(db)[(fw_data["grade_level"], fw_data["path_id"])]
            stored = [(c.code, c.label) for c in db.query(Competency).filter_by(framework_id=fw.id)
                      .order_by(Competency.order_index)]
            expected = [(c["code"], c["label"]) for d in fw_data["domains"] for ch in d["chapters"]
                        for c in ch["competencies"]]
            assert stored == expected
        tc = ci_frameworks(db)[("tle", "ci-C")]
        b4 = [c.label for c in db.query(Competency).filter_by(framework_id=tc.id, chapter_code="B4")
              .order_by(Competency.order_index)]
        assert b4[0] == "Connaître la définition de la fonction logarithme népérien"
        assert b4[1] == "Connaître la définition de la fonction logarithme décimal"


def test_ids_carry_country_year_subject_grade_series_domain_chapter_and_skill(factory):
    with factory() as db:
        published = ci_frameworks(db)
        codes = []
        for (grade, path), fw in published.items():
            for c in db.query(Competency).filter_by(framework_id=fw.id):
                m = ID.match(c.code)
                assert m, c.code
                token = {"2nde": "2NDE", "1re": "1RE", "tle": "TLE"}.get(grade, grade.upper())
                assert m.group(1) == token
                assert m.group(3) == (path.split("-", 1)[1] if path else None)
                assert c.short_id == f"{m.group(4)}{m.group(5)}.{m.group(6)}"
                assert c.domain_code == m.group(4) and c.chapter_code == f"{m.group(4)}{m.group(5)}"
                codes.append(c.code)
        assert len(codes) == len(set(codes)) == 3107
        tc = db.query(Competency).filter_by(code="CI-2023-MATH-TLE-C-H1.1").one()
        assert tc.domain_name == "Arithmétique" and tc.chapter_name == "Divisibilité dans ℤ"


def test_time_and_order_follow_the_official_progression(factory):
    with factory() as db:
        for key, fw in ci_frameworks(db).items():
            comps = db.query(Competency).filter_by(framework_id=fw.id).all()
            by_chapter = {}
            for c in comps:
                by_chapter.setdefault(c.chapter_code, []).append(c)
            # les minutes d'une leçon retombent exactement sur ses heures officielles
            for chapter in by_chapter.values():
                hours = {c.chapter_hours for c in chapter}
                assert len(hours) == 1
                assert sum(c.recommended_minutes for c in chapter) == hours.pop() * 60
                assert all(c.recommended_minutes >= 5 and c.recommended_minutes % 5 == 0 for c in chapter)
            ranks = sorted({c.chapter_rank for c in comps})
            assert ranks == list(range(1, len(by_chapter) + 1)), key
            # ordre annuel : 1..N, leçons dans l'ordre de la progression
            ordered = sorted(comps, key=lambda c: c.teaching_order)
            assert [c.teaching_order for c in ordered] == list(range(1, len(comps) + 1))
            assert [c.chapter_rank for c in ordered] == sorted(c.chapter_rank for c in ordered)
            # order_index : ordre du programme (domaine, leçon, habileté)
            assert [c.order_index for c in sorted(comps, key=lambda c: c.order_index)] == list(range(len(comps)))
        # repère connu : en 3e, « Équations et inéquations … dans ℝ×ℝ » est la 12e leçon de l'année, 7 h
        third = ci_frameworks(db)[("3e", None)]
        a5 = db.query(Competency).filter_by(framework_id=third.id, chapter_code="A5").first()
        assert "ℝ×ℝ" in a5.chapter_name and a5.chapter_rank == 12 and a5.chapter_hours == 7


def test_second_run_changes_nothing_and_keeps_ids(factory):
    with factory() as db:
        before = {c.code: c.id for c in db.query(Competency).filter(Competency.code.like("CI-%"))}
        assert seed_ci_curriculum(db) == 0
        after = {c.code: c.id for c in db.query(Competency).filter(Competency.code.like("CI-%"))}
        assert before == after


def test_new_version_updates_in_place_and_never_drops_a_used_competency(factory):
    data = copy.deepcopy(load_data())
    data["version"] = "2099.01"
    fw_data = next(f for f in data["frameworks"] if f["grade_level"] == "6e")
    chapter = fw_data["domains"][0]["chapters"][0]
    used, unused = chapter["competencies"][-1]["code"], chapter["competencies"][-2]["code"]
    chapter["competencies"] = chapter["competencies"][:-2]
    chapter["competencies"][0]["label"] = "Noter ℕ (libellé corrigé)"
    with factory() as db:
        ids = {c.code: c.id for c in db.query(Competency).filter(Competency.code.like("CI-2023-MATH-6E-%"))}
        ex = ExerciseCatalog(provider_ref="t", title="t", grade_level="6e", response_type="short_text")
        db.add(ex); db.flush()
        db.add(ExerciseCompetency(exercise_id=ex.id, competency_id=ids[used])); db.commit()
        assert seed_ci_curriculum(db, data) == len(EXPECTED)
        now = {c.code: c for c in db.query(Competency).filter(Competency.code.like("CI-2023-MATH-6E-%"))}
        assert used in now and unused not in now
        first = chapter["competencies"][0]["code"]
        assert now[first].id == ids[first] and now[first].label == "Noter ℕ (libellé corrigé)"


def test_ci_class_gets_its_series_framework(factory):
    with factory() as db:
        cls = SchoolClass(name="Tle D1", grade_level="tle", program_id="ci", series_id="ci-D")
        college = SchoolClass(name="6e 2", grade_level="6e", program_id="ci")
        db.add_all([cls, college]); db.commit()
        fw = framework_for(db, cls)
        codes = [c.code for c in db.query(Competency).filter_by(framework_id=fw.id)]
        assert codes and all(code.startswith("CI-2023-MATH-TLE-D-") for code in codes)
        assert framework_for(db, college).grade_level == "6e"


def test_api_exposes_time_and_order(factory):
    app = FastAPI()
    app.include_router(misc.router)

    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_user] = lambda: User(id="u", email="a@b.c", password_hash="x", role="admin")
    client = TestClient(app)
    with factory() as db:
        fw = ci_frameworks(db)[("1re", "ci-C")]
    tree = client.get(f"/api/competencies/tree?framework_id={fw.id}").json()
    chapter = tree[0]["chapters"][0]
    assert chapter["code"] == "A1" and chapter["hours"] == 9 and chapter["rank"] == 1 and chapter["week"] == 1
    first = chapter["competencies"][0]
    assert first["code"] == "CI-2023-MATH-1RE-C-A1.1" and first["recommended_minutes"] > 0
    flat = client.get(f"/api/competencies?framework_id={fw.id}").json()
    assert {c["teaching_order"] for c in flat} == set(range(1, 387))
    assert all(c["chapter_hours"] for c in flat)


def test_exercise_tab_summaries_carry_program_durations(factory):
    """Onglet Exercices : la durée de chaque compétence et les heures officielles de
    sa leçon arrivent dans les deux résumés (admin et professeur)."""
    from app.routers import content, indigo as indigo_router
    app = FastAPI()
    app.include_router(content.router)
    app.include_router(indigo_router.router)

    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_user] = lambda: User(id="u", email="a@b.c", password_hash="x", role="admin")
    client = TestClient(app)
    with factory() as db:
        fw = ci_frameworks(db)[("6e", None)]
    admin_rows = client.get("/api/indigo/summary?grade_level=6e&program_id=ci&subject_id=math").json()
    teacher_rows = client.get(f"/api/content/summary?grade_level=6e&subject_id=math&framework_id={fw.id}").json()
    for rows in (admin_rows, teacher_rows):
        assert len(rows) == 239
        first = next(r for r in rows if r["short_id"] == "A1.1")
        assert first["recommended_minutes"] == 25 and first["chapter_hours"] == 7
    fr = client.get("/api/indigo/summary?grade_level=6e&program_id=fr&subject_id=math").json()
    assert all(r["recommended_minutes"] is None for r in fr)  # pas de durée hors programme ivoirien


def test_series_merge_lines_are_faithful(factory):
    """Lycée : chaque compétence appartient à une ligne de sa filière. Une ligne
    réunit au plus une compétence par série ; deux compétences sur une même ligne
    ont le même libellé, sauf une variante, dont le libellé de chaque série se
    reconstruit exactement depuis le texte commun et sa partie propre."""
    with factory() as db:
        comps = db.query(Competency).filter(Competency.code.like("CI-%")).all()
        fws = {f.id: f for f in db.query(CompetencyFramework).filter_by(program_id="ci")}
        lines = {}
        for c in comps:
            fw = fws[c.framework_id]
            if fw.path_id is None:
                assert c.merge_json is None  # collège : pas de série
                continue
            assert c.merge_json, c.code
            lines.setdefault(c.merge_json["line"], []).append((fw.path_id.split("-")[1], c))
        kinds = {}
        for line, members in lines.items():
            series = [s for s, _ in members]
            assert len(series) == len(set(series)), line
            group = line.split(":")[0]
            if len(members) == 2:
                variant = members[0][1].merge_json["variant"]
                if variant is None:
                    # même texte au signe près : seuls espaces et ponctuation peuvent différer,
                    # sauf les 14 différences de forme relues et validées (form_only)
                    key = lambda t: re.sub(r"[\s,;:.•·]+", "", t.lower())
                    if members[0][1].merge_json.get("form_only"):
                        kinds.setdefault(group, {}).setdefault("forme", 0)
                        kinds[group]["forme"] += 1
                    else:
                        assert key(members[0][1].label) == key(members[1][1].label), line
                    kinds.setdefault(group, {}).setdefault("commun", 0)
                    kinds[group]["commun"] += 1
                else:
                    for s, c in members:
                        assert variant["pre"] + variant["parts"][s] + variant["post"] == c.label
                    kinds.setdefault(group, {}).setdefault("variante", 0)
                    kinds[group]["variante"] += 1
        assert kinds["1RE-C-D"] == {"commun": 300, "variante": 13, "forme": 4}
        assert kinds["TLE-A1-A2"] == {"commun": 130, "forme": 3}
        assert kinds["1RE-A1-A2"] == {"commun": 88, "forme": 6}
        assert kinds["TLE-C-D"] == {"commun": 231, "variante": 4, "forme": 1}
        assert sum(k.get("forme", 0) for k in kinds.values()) == 14
        # f'' + ω²f et f'' − ω²f ne sont jamais réunis (les signes comptent)
        tle = {c.short_id: c.merge_json["line"] for c in comps if c.code.startswith("CI-2023-MATH-TLE-")}
        assert tle.get("B8.8") != tle.get("B8.9")


def test_exercise_tab_shows_a_filiere_with_its_series(factory):
    from app.routers import indigo as indigo_router
    app = FastAPI()
    app.include_router(indigo_router.router)

    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_user] = lambda: User(id="u", email="a@b.c", password_hash="x", role="admin")
    client = TestClient(app)
    base = "/api/indigo/summary?grade_level=1re&program_id=ci&subject_id=math"
    assert client.get(base).json() == []  # sans série demandée : référentiels sans série seulement
    rows = client.get(base + "&paths=ci-C,ci-D").json()
    assert len(rows) == 386 + 332
    assert {r["series"] for r in rows} == {"C", "D"}
    bary = [r for r in rows if r["merge"]["variant"] and r["merge"]["chapter_name"] == "Barycentre"]
    assert {r["series"] for r in bary} == {"C", "D"}
    littéraire = client.get(base.replace("1re", "tle") + "&paths=ci-A1,ci-A2").json()
    assert len(littéraire) == 165 + 136
    # « Tout publier » accepte les compétences des séries affichées
    ids = [r["competency_id"] for r in rows[:3]]
    resp = client.post("/api/indigo/publish-selection/preview", json={
        "grade_level": "1re", "program_id": "ci", "subject_id": "math", "competency_ids": ids})
    assert resp.status_code == 200, resp.text


def test_catalog_counts_twin_families(factory):
    from app.models import GeneratedExercise, IndigoExercise
    from app.routers import content, indigo as indigo_router

    with factory() as db:
        fw = ci_frameworks(db)[('6e', None)]
        comp = db.query(Competency).filter_by(framework_id=fw.id).first()
        for i in range(3):
            db.add(IndigoExercise(id=f'i{i}', competency_id=comp.id,
                                  grading_json={'twin': {'group': 'family'}}))
        db.add(IndigoExercise(id='single', competency_id=comp.id, grading_json={}))
        db.add_all([
            GeneratedExercise(id='g1', competency_id=comp.id, difficulty_level=1, statement='a', twin_group='family', status='active'),
            GeneratedExercise(id='g2', competency_id=comp.id, difficulty_level=1, statement='b', twin_group='family', status='twin'),
            GeneratedExercise(id='g3', competency_id=comp.id, difficulty_level=1, statement='c', status='active'),
        ])
        db.commit()
        admin = next(r for r in indigo_router.summary('6e', db, 'ci', 'math') if r['competency_id'] == comp.id)
        bank = next(r for r in content.summary('6e', db, fw.id, 'math') if r['competency_id'] == comp.id)
        assert admin['family_total'] == 2
        assert admin['draft'] == 4  # les opérations de publication gardent leurs compteurs
        assert bank['family_total'] == 2

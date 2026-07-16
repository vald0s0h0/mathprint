"""Tests de la pipeline Gemini (§ création d'exercices par LLM).

Tout tourne en mode mock (aucune clé API, aucun réseau) : le mock de
providers.gemini_json renvoie un lot au contrat app, seedé par compétence ET
par numéro de lot — c'est ce qui permet d'exercer réellement la boucle « autant
d'appels que nécessaire » et le dédoublonnage entre lots.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import Competency, CompetencyFramework, GeneratedExercise
from app.services import exercise_gen, gemini_gen, providers


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_domain(db, domain_code="A") -> Competency:
    """Un domaine à 2 chapitres / 3 compétences : la cible + ses voisines,
    dont le prompt a besoin pour cadrer le périmètre."""
    fw = CompetencyFramework(grade_level="5e", name="Test 5e")
    db.add(fw)
    db.flush()
    rows = [
        ("A1.1", "Automatismes", "A1", "Opérations", 0),
        ("A1.2", "Divisions euclidiennes", "A1", "Opérations", 1),
        ("A2.1", "Additionner des relatifs", "A2", "Nombres relatifs", 2),
    ]
    comps = []
    for short_id, label, chap_code, chap_name, order in rows:
        c = Competency(framework_id=fw.id, code=short_id, short_id=short_id, label=label,
                       domain_code=domain_code, domain_name="Nombres et calculs",
                       chapter_code=chap_code, chapter_name=chap_name, order_index=order)
        db.add(c)
        comps.append(c)
    db.commit()
    return comps[0]


def test_ensure_bank_gemini_reaches_target_over_several_batches(db_session):
    # Cœur de la demande : une banque vide doit déclencher AUTANT d'appels que
    # nécessaire (lots de 5) pour atteindre la cible, jamais un nombre figé.
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)

    assert len(rows) >= settings.gemini_bank_target
    assert all(r.source == "gemini" for r in rows)
    assert all(r.difficulty_level == 3 for r in rows)       # difficulté figée
    assert all(r.model == settings.gemini_model for r in rows)
    # cible 10, lots de 5 -> 2 appels au moins : la boucle a bien tourné
    assert len({r.variant for r in rows}) == len(rows)


def test_ensure_bank_gemini_calls_llm_until_target_then_stops(db_session, monkeypatch):
    calls: list[dict] = []
    orig = providers.gemini_json

    def counted(db, operation, system, payload, **kw):
        calls.append(payload)
        return orig(db, operation, system, payload, **kw)

    monkeypatch.setattr(providers, "gemini_json", counted)
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    # cible 10 / lots de 5 = 2 appels si tout passe, et surtout PAS un de plus
    assert len(calls) == 2
    assert [c["batch"] for c in calls] == [0, 1]
    assert all(c["count"] == settings.gemini_batch_size for c in calls)
    # le 2e appel connaît les énoncés du 1er (anti-doublon côté prompt)
    assert calls[0]["already_created"] == []
    assert len(calls[1]["already_created"]) == 5

    # banque déjà pleine : plus aucun appel
    calls.clear()
    gemini_gen.ensure_bank(db_session, comp, level=3)
    assert calls == []


def test_ensure_bank_gemini_no_duplicates_in_bank(db_session):
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    keys = [exercise_gen._dedup_key(r.statement, r.expected_json,
                                    (r.grading_json or {}).get("choices"))
            for r in rows]
    assert len(set(keys)) == len(keys)


def test_ensure_bank_gemini_batch_mix_has_qcm_and_written_and_problem(db_session):
    # Le mélange demandé au modèle (3 QCM / 1 réponse écrite / 1 problème) est
    # ce qui remplit une page proprement : distribution.pick_balanced_exercise
    # ne peut équilibrer une copie que si la banque contient les trois.
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    types_ = {r.response_type for r in rows}
    assert "qcm_single" in types_
    assert "short_text" in types_
    assert "multiline_text" in types_
    assert any(r.kind == "probleme" for r in rows)


def test_gemini_rejects_non_automatable_formats(db_session):
    # matching/manual_drawing sont valides pour l'adaptateur Sésamaths (qui
    # subit le format du manuel) mais interdits ici : on invente l'exercice,
    # donc on peut toujours en choisir un qui se corrige automatiquement.
    comp = _seed_domain(db_session)
    raw = {"kind": "application",
           "statement": "Trace un carré de côté $5$ cm en respectant l'échelle.",
           "correction": "Le carré a 4 côtés égaux de $5$ cm.",
           "response_type": "manual_drawing"}
    assert gemini_gen._reject_reason(raw) is not None
    assert gemini_gen._to_candidate(raw, comp, db_session, set()) is None
    # ... alors que le même exercice passe la validation partagée
    assert exercise_gen._validate_exercise(raw, comp, db_session, set()) is not None


def test_gemini_rejects_exercise_that_needs_a_figure(db_session):
    # Le prompt interdit les figures (pas de géométrie pour l'instant) : si le
    # modèle en renvoie une quand même, on rejette l'exercice — le retirer en
    # silence casserait un énoncé qui s'y réfère.
    comp = _seed_domain(db_session)
    raw = {"kind": "application",
           "statement": "Lis la longueur du segment sur la figure ci-contre.",
           "correction": "Le segment mesure $7$ cm.",
           "response_type": "short_text",
           "answer": {"type": "integer", "value": 7},
           "figure": {"type": "number_line", "params": {"min": 0, "max": 10, "points": []}}}
    assert gemini_gen._reject_reason(raw) is not None
    assert gemini_gen._to_candidate(raw, comp, db_session, set()) is None


def test_gemini_geometry_refused_with_clear_message(db_session):
    comp = _seed_domain(db_session, domain_code="EG")
    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "géométrie" in str(exc.value).lower()
    assert db_session.query(GeneratedExercise).count() == 0


def test_gemini_other_levels_generate_nothing(db_session, monkeypatch):
    # La difficulté est figée à 3 : un appel niveau 5 ne doit RIEN créer (sinon
    # on rangerait des exercices moyens sous une étiquette « difficile »).
    calls: list[int] = []
    monkeypatch.setattr(providers, "gemini_json",
                        lambda *a, **kw: calls.append(1) or {"exercises": []})
    comp = _seed_domain(db_session)
    assert gemini_gen.ensure_bank(db_session, comp, level=5) == []
    assert calls == []


def test_bank_rows_near_level_falls_back_to_level3(db_session):
    # Conséquence de la difficulté figée : une demande niveau 5 doit se
    # rabattre proprement sur la banque de niveau 3, sans erreur.
    comp = _seed_domain(db_session)
    rows, level = exercise_gen.bank_rows_near_level(db_session, comp, level=5,
                                                    source="gemini")
    assert level == 3
    assert rows and all(r.source == "gemini" for r in rows)


def test_gemini_pool_never_mixed_with_sesamaths(db_session):
    comp = _seed_domain(db_session)
    db_session.add(GeneratedExercise(
        competency_id=comp.id, difficulty_level=3, variant=0,
        statement="Exercice tiré du manuel.", correction="$1 + 1 = 2$",
        response_type="short_text", expected_json={"type": "integer", "value": 2},
        grading_json={"max_score": 1, "comparator": "numeric"}, source="sesamaths"))
    db_session.commit()

    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert all(r.source == "gemini" for r in rows)
    assert len(rows) >= settings.gemini_bank_target   # la ligne Sésamaths ne compte pas


def test_retired_gemini_exercise_never_recreated(db_session):
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    victim = rows[0]
    victim_statement = victim.statement
    victim.status = "retired"
    db_session.commit()

    gemini_gen.ensure_bank(db_session, comp, level=3)
    active = {r.statement for r in db_session.query(GeneratedExercise)
              .filter_by(competency_id=comp.id, status="active").all()}
    assert victim_statement not in active


def test_gemini_batch_failure_keeps_earlier_batches(db_session, monkeypatch):
    # Un lot en échec (réseau, quota) ne doit pas jeter les exercices déjà
    # produits : on garde une banque partielle plutôt que rien.
    orig = providers.gemini_json
    calls: list[int] = []

    def flaky(db, operation, system, payload, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("panne simulée")
        return orig(db, operation, system, payload, **kw)

    monkeypatch.setattr(providers, "gemini_json", flaky)
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert len(rows) == 5          # le 1er lot seulement
    assert all(r.source == "gemini" for r in rows)


def test_gemini_total_failure_raises_clear_error(db_session, monkeypatch):
    monkeypatch.setattr(providers, "gemini_json",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("panne totale")))
    comp = _seed_domain(db_session)
    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "panne totale" in str(exc.value)


def test_system_prompt_frames_target_competency_among_its_domain(db_session):
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5)
    # la cible est marquée, les voisines présentes (périmètre), et la consigne
    # de ne traiter QUE la cible est explicite
    tree = gemini_gen._competency_tree(db_session, comp)
    assert tree.count("⇦ CIBLE") == 1        # une seule cible, jamais ses voisines
    assert "A1.1 Automatismes  ⇦ CIBLE" in prompt
    assert "A1.2 Divisions euclidiennes" in prompt
    assert "A2 Nombres relatifs" in prompt
    # l'enjeu plateforme (OCR/CV) et le contrat de format partagé sont dedans
    assert "OCR" in prompt and "vision par ordinateur" in prompt
    assert '"exercises"' in prompt
    # aucun placeholder non substitué
    assert "§" not in prompt


def test_system_prompt_forbids_uncorrectable_formats(db_session):
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5)
    assert "INTERDITS" in prompt
    assert "matching" in prompt and "manual_drawing" in prompt


def test_generate_subject_end_to_end_fills_one_page_without_duplicates(db_session):
    """Le sujet complet, tel que l'élève le reçoit : la banque Gemini doit
    remplir la page cible sans la déborder et sans jamais servir deux fois le
    même exercice — c'est l'objectif de la suppression du plafond de 3."""
    from app.models import Assessment, Copy, CopyItem, SchoolClass, Student
    from app.services import generation

    comp = _seed_domain(db_session)
    comps = db_session.query(Competency).filter(Competency.chapter_code == "A1").all()
    cls = SchoolClass(name="5eB", grade_level="5e")
    db_session.add(cls)
    db_session.flush()
    for i in range(3):
        db_session.add(Student(class_id=cls.id, first_name=f"Eleve{i}", last_name="Test",
                               llm_pseudonym=f"E{i}", active=True))
    a = Assessment(class_id=cls.id, type="training", title="Sujet Gemini",
                   pages_target=1, personalization_mode="common")
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "gemini"}
    db_session.add(a)
    db_session.commit()

    report = generation.generate_assessment_job(db_session, a, job=None, font_size=9)
    db_session.commit()

    assert report["warnings"] == []          # aucun débordement de page
    copies = db_session.query(Copy).all()
    assert len(copies) == 3
    for copy in copies:
        items = db_session.query(CopyItem).filter_by(copy_id=copy.id).all()
        assert copy.total_pages == 1         # la cible est tenue
        assert len(items) > 3                # la page est vraiment remplie
        statements = [i.statement for i in items]
        assert len(set(statements)) == len(statements)   # aucun doublon


def test_generated_copies_split_correction_load_between_cv_and_mathpix(db_session):
    """~50 % QCM (corrigés par vision par ordinateur : gratuit) / ~50 % cases
    manuscrites (OCR Mathpix : payant, sous quota). Ce n'est pas un détail
    pédagogique mais la répartition de la charge de correction — un mix qui
    dériverait ferait exploser la facture Mathpix sans que rien ne le signale."""
    from app.models import Assessment, Copy, CopyItem, SchoolClass, Student
    from app.services import generation

    _seed_domain(db_session)
    comps = db_session.query(Competency).filter(Competency.chapter_code == "A1").all()
    cls = SchoolClass(name="5eB", grade_level="5e")
    db_session.add(cls)
    db_session.flush()
    for i in range(4):
        db_session.add(Student(class_id=cls.id, first_name=f"E{i}", last_name="T",
                               llm_pseudonym=f"E{i}", active=True))
    a = Assessment(class_id=cls.id, type="training", title="Mix", pages_target=2,
                   personalization_mode="common")
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "gemini"}
    db_session.add(a)
    db_session.commit()

    report = generation.generate_assessment_job(db_session, a, job=None, font_size=9)
    db_session.commit()

    copy_ids = [c.id for c in db_session.query(Copy).filter_by(assessment_id=a.id).all()]
    items = db_session.query(CopyItem).filter(CopyItem.copy_id.in_(copy_ids)).all()
    qcm = sum(1 for i in items if i.response_type.startswith("qcm"))
    assert len(items) >= 8
    assert 0.4 <= qcm / len(items) <= 0.6      # « environ » : la banque ne permet
    # pas toujours la cible exacte, mais jamais les 10 % de QCM d'avant
    assert report["estimated_mathpix_calls"] == len(items) - qcm


def test_content_generate_endpoint_accepts_gemini_source(db_session):
    from app.routers import content as content_router

    comp = _seed_domain(db_session)
    body = content_router.GenerateExercisesIn(competency_id=comp.id, level=3,
                                              source="gemini")
    result = content_router.generate_exercises(body, db=db_session)
    assert result["count"] >= settings.gemini_bank_target
    assert all(e["source"] == "gemini" for e in result["exercises"])

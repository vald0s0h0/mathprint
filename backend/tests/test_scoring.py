"""Tests du barème d'effort et de la notation (§ barème, services/scoring.py).

Deux niveaux :
  - les règles pures (arrondis, repli, règle de trois) — sans base, ni manuel,
    ni réseau ;
  - la chaîne complète création -> sujet -> correction -> finalisation, qui est
    la vraie exigence : « le barème est utilisé dans tout le process, jusqu'à
    la correction finale ». Elle tourne en mock (aucune clé API) mais a besoin
    du VRAI manuel 5.pdf, la création Gemini étant ancrée dans ses pages (même
    contrat que tests/test_gemini_gen.py) — d'où le skip local, et pas global :
    les règles pures doivent rester exécutables sans manuel.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, CopyItemResult,
    CopyResult, GradingDecision, SchoolClass, ScanBatch, Student, StudentResponse,
)
from app.services import exercise_gen, gemini_gen, pipeline, scoring

MANUAL_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "manuals" / "5.pdf"
needs_manual = pytest.mark.skipif(not MANUAL_PATH.exists(), reason="manuel 5.pdf absent")


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sesamaths_manuals", {"5e": str(MANUAL_PATH)})
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# ================================================================ règles pures

@pytest.mark.parametrize("raw, expected", [
    (1, 1.0), (0.5, 0.5), (2.5, 2.5), (5, 5.0),
    (1.2, 1.0),          # tout barème est ramené à un multiple de 0,5...
    (1.3, 1.5),
    ("2,5", 2.5),        # ... y compris écrit à la française par le modèle
    (0.1, 0.5),          # ... et borné : jamais 0 point (l'exercice vaut quelque chose)
    (99, 5.0),           # ... jamais un quart d'un sujet noté sur 20
])
def test_snap_bareme_always_yields_a_half_point_multiple(raw, expected):
    assert scoring.snap_bareme(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "beaucoup", float("nan"), float("inf"), 0, -2])
def test_snap_bareme_rejects_unusable_values(raw):
    # inexploitable -> None, pour que l'appelant se rabatte sur fallback_bareme :
    # un barème absent se recalcule, un exercice jeté se repaie.
    assert scoring.snap_bareme(raw) is None


@pytest.mark.parametrize("raw, expected", [
    (13.0, 13.0), (13.1, 13.5), (13.5, 13.5), (13.6, 14.0),
    (0.0, 0.0), (0.01, 0.5),
])
def test_note_rounds_up_to_the_half_point(raw, expected):
    # « arrondi au supérieur » : on ne retire jamais un demi-point à un élève.
    assert scoring.round_half_up(raw) == expected


def test_note_rounding_is_immune_to_float_representation():
    # 7/10 * 20 vaut 14 exactement en maths, pas toujours en flottant : sans
    # garde, ceil(28.000000000000004) donnerait 14,5 — un demi-point offert par
    # une erreur de représentation.
    for base in scoring.NOTE_BASES:
        for i in range(0, 21):
            raw, note = scoring.note_from_points(i, 20, base)
            assert note >= raw - 1e-9          # jamais en dessous de la note exacte
            assert note - raw < 0.5 + 1e-9     # ni un arrondi de plus d'un demi-point
            assert (note * 2) % 1 == 0         # toujours un multiple de 0,5


@pytest.mark.parametrize("base, expected_raw, expected_note", [
    (20, 15.0, 15.0),
    (10, 7.5, 7.5),
    (5, 3.75, 4.0),      # 3,75 n'est pas un multiple de 0,5 -> 4
])
def test_note_is_a_rule_of_three_on_the_chosen_base(base, expected_raw, expected_note):
    # 15 points de barème obtenus sur 20 possibles = 75 % du sujet, quelle que
    # soit la base : c'est la base choisie par le professeur qui change, pas la
    # performance de l'élève.
    raw, note = scoring.note_from_points(15, 20, base)
    assert raw == expected_raw
    assert note == expected_note


def test_perfect_paper_never_exceeds_the_base_by_rounding():
    for base in scoring.NOTE_BASES:
        assert scoring.note_from_points(12.5, 12.5, base) == (float(base), float(base))


def test_note_of_an_ungraded_subject_is_zero_not_a_division_by_zero():
    assert scoring.note_from_points(3, 0, 20) == (0.0, 0.0)
    assert scoring.note_from_points(3, 10, 0) == (0.0, 0.0)


def test_earned_points_are_the_success_ratio_of_the_bareme():
    # 3 cellules justes sur 4 dans un exercice qui vaut 2 points -> 1,5 point.
    assert scoring.earned_points(3, 4, 2.0) == 1.5
    assert scoring.earned_points(0, 4, 2.0) == 0.0
    assert scoring.earned_points(4, 4, 2.0) == 2.0


def test_earned_points_never_exceed_the_bareme():
    # une rubrique corrigée par LLM peut renvoyer un total au-dessus du max :
    # il ne doit pas créer des points au-delà du barème de l'exercice.
    assert scoring.earned_points(6, 4, 2.0) == 2.0
    assert scoring.earned_points(-1, 4, 2.0) == 0.0
    assert scoring.earned_points(2, 0, 2.0) == 0.0


def test_bareme_never_overwrites_the_engine_max_score():
    # Le piège central du barème : max_score est l'échelle INTERNE du moteur
    # (1 par cellule) et sert à l'auto-vérification des exercices créés. Un
    # barème qui l'écraserait casserait la correction par cellule en silence.
    grading = {"max_score": 4, "comparator": "table_cells"}
    out = scoring.with_bareme(grading, "table_fill")
    assert out["max_score"] == 4
    assert out["bareme_points"] == 2.0


def test_bareme_from_the_model_wins_over_the_fallback():
    grading = {"max_score": 1, "comparator": "qcm", "bareme_points": 2.5}
    assert scoring.item_bareme(grading, "qcm_single") == 2.5


def test_exercise_without_bareme_falls_back_instead_of_being_worth_zero():
    # Banque antérieure au barème, lot Sésamaths déjà en cache, champ omis par
    # le modèle : l'exercice doit valoir son repli, jamais 0 (il compterait
    # alors pour rien dans la note, en silence).
    for grading, rtype in [
        ({"max_score": 1, "comparator": "qcm"}, "qcm_single"),
        ({"max_score": 1, "comparator": "numeric"}, "short_text"),
        ({"max_score": 6, "comparator": "table_cells"}, "table_fill"),
        ({"max_score": 4, "comparator": "rubric"}, "multiline_text"),
        ({"max_score": 2, "comparator": "symbolic_equiv"}, "short_text"),
        ({}, "manual_drawing"),
    ]:
        bareme = scoring.item_bareme(grading, rtype)
        assert scoring.BAREME_MIN <= bareme <= scoring.BAREME_MAX
        assert (bareme * 2) % 1 == 0        # toujours un multiple de 0,5


def test_fallback_bareme_grows_with_the_work_demanded():
    # Le repli suit la même idée que le prompt : une case à cocher n'est pas un
    # tableau de 6 cellules, qui n'est pas un raisonnement rédigé en 4 étapes.
    qcm = scoring.item_bareme({"max_score": 1, "comparator": "qcm"}, "qcm_single")
    table = scoring.item_bareme({"max_score": 6, "comparator": "table_cells"}, "table_fill")
    rubric = scoring.item_bareme({"max_score": 8, "comparator": "rubric"}, "multiline_text")
    assert qcm < table <= rubric


def test_note_base_ignored_on_a_training_subject():
    # Un sujet créé en contrôle puis repassé en entraînement garde une base en
    # base de données : elle ne doit pas ressusciter une note.
    training = Assessment(class_id="c", type="training", title="T", note_base=20)
    control = Assessment(class_id="c", type="control", title="C", note_base=10)
    assert scoring.assessment_note_base(training) == scoring.NOTE_BASE_UNGRADED
    assert scoring.assessment_note_base(control) == 10


def test_unknown_note_base_falls_back_to_20():
    assert scoring.normalize_note_base(13, graded=True) == 20
    assert scoring.normalize_note_base(None, graded=True) == 20
    assert scoring.normalize_note_base(5, graded=True) == 5


def test_points_are_printed_the_french_way():
    # lu par un élève de 5e sur sa copie : « 1,5 », et « 2 » plutôt que « 2,0 »
    assert scoring.format_points(1.5) == "1,5"
    assert scoring.format_points(2.0) == "2"
    assert scoring.format_points(13.5) == "13,5"


# ================================================================ prompt & création

def _seed_domain(db, domain_code="A") -> Competency:
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


@needs_manual
def test_gemini_prompt_asks_for_an_effort_bareme_not_a_difficulty(db_session):
    """Le cœur de la demande côté prompt : le modèle doit noter l'EFFORT (temps
    de réflexion) et surtout pas le niveau de l'élève — un élève fragile fournit
    plus d'effort sur un exercice facile qu'un bon élève sur un exercice moyen."""
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5, "(title) 1 Calcule.")

    assert "effort_points" in prompt
    assert "TEMPS DE RÉFLEXION" in prompt
    assert "multiple de 0,5" in prompt
    # la consigne « ne dépend pas du niveau de l'élève » est explicite, et
    # l'exemple qui la motive présent
    assert "Il ne dépend JAMAIS du niveau de l'élève" in prompt
    assert "élève fragile" in prompt
    # la difficulté reste, elle, non demandée : les deux grandeurs ne doivent
    # pas se confondre dans le prompt
    assert "AUCUN niveau de difficulté" in prompt


@needs_manual
def test_every_created_exercise_carries_a_half_point_bareme(db_session):
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)

    assert rows
    for r in rows:
        bareme = r.grading_json.get("bareme_points")
        assert bareme is not None, "exercice créé sans barème"
        assert scoring.BAREME_MIN <= bareme <= scoring.BAREME_MAX
        assert (bareme * 2) % 1 == 0
        # l'échelle interne du moteur est intacte à côté
        assert r.grading_json.get("max_score")


def test_validator_snaps_an_off_scale_bareme_instead_of_rejecting(db_session):
    # Un barème hors normes n'est pas un motif de rejet : l'exercice est bon,
    # seul son étiquetage est douteux — on le recale (l'exercice est payé).
    comp = _seed_domain(db_session)
    raw = {"kind": "application", "effort_points": 42,
           "statement": "Calcule $12 + 8$.", "correction": "$12 + 8 = 20$.",
           "response_type": "short_text", "answer": {"type": "integer", "value": 20}}
    valid = exercise_gen._validate_exercise(raw, comp, db_session, set())
    assert valid is not None
    assert valid["grading"]["bareme_points"] == scoring.BAREME_MAX


def test_validator_falls_back_when_the_model_omits_the_bareme(db_session):
    comp = _seed_domain(db_session)
    raw = {"kind": "application",
           "statement": "Calcule $12 + 8$.", "correction": "$12 + 8 = 20$.",
           "response_type": "short_text", "answer": {"type": "integer", "value": 20}}
    valid = exercise_gen._validate_exercise(raw, comp, db_session, set())
    assert valid is not None
    assert valid["grading"]["bareme_points"] == 1.0


# ================================================================ chaîne complète

def _seed_control(db, note_base: int = 20, n_students: int = 2) -> Assessment:
    comp = _seed_domain(db)
    # compétences du référentiel qu'on vient de créer : un test qui compare
    # plusieurs bases en sème plusieurs, et prendre « toutes les A1 » ferait
    # travailler le sujet sur celles des seeds précédents
    comps = db.query(Competency).filter(Competency.framework_id == comp.framework_id,
                                        Competency.chapter_code == "A1").all()
    cls = SchoolClass(name="5eB", grade_level="5e")
    db.add(cls)
    db.flush()
    for i in range(n_students):
        # llm_pseudonym est unique en base : un test qui sème plusieurs classes
        # dans la même session doit varier le pseudonyme
        db.add(Student(class_id=cls.id, first_name=f"Eleve{i}", last_name="Test",
                       llm_pseudonym=f"E{cls.id[:8]}-{i}", active=True))
    a = Assessment(class_id=cls.id, type="control", title="Contrôle barème",
                   pages_target=1, personalization_mode="common", note_base=note_base)
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "gemini"}
    db.add(a)
    db.commit()
    return a


def _run_to_finalized(db, assessment: Assessment) -> ScanBatch:
    """Sujet généré puis corrigé de bout en bout en mock (copies simulées),
    jusqu'à la finalisation qui consolide les résultats."""
    from app.services import generation

    generation.generate_assessment_job(db, assessment, job=None, font_size=9)
    db.commit()
    batch = ScanBatch(assessment_id=assessment.id)
    db.add(batch)
    db.commit()
    pipeline.process_batch(db, batch)
    # le mock produit des réponses fausses/ambiguës : on tranche les revues
    # ouvertes comme le ferait le professeur, sinon la finalisation refuse
    from app.models import ManualReview
    for r in db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).all():
        old = db.get(GradingDecision, r.decision_id)
        db.add(GradingDecision(response_id=old.response_id, source="teacher",
                               score=0.0, max_score=old.max_score, confidence=1.0,
                               tier="D", reason_code="teacher_set_score",
                               status="validated"))
        old.status = "revised"
        from datetime import datetime, timezone
        r.resolved_at = datetime.now(timezone.utc)
    db.commit()
    pipeline.finalize_batch(db, batch)
    db.commit()
    return batch


@needs_manual
def test_finalization_stores_note_and_per_exercise_results_for_each_student(db_session):
    """L'exigence « enregistrer dans la DB les résultats des exercices + notes
    + appréciations de chaque sujet » : après finalisation, le suivi d'un élève
    tient dans une ligne, sans rejouer 4 jointures ni reconstituer le barème."""
    a = _seed_control(db_session, note_base=10)
    _run_to_finalized(db_session, a)

    results = db_session.query(CopyResult).filter_by(assessment_id=a.id).all()
    assert len(results) == 2                       # une ligne par élève

    for result in results:
        assert result.note_base == 10
        assert result.points_total > 0
        assert 0 <= result.points_earned <= result.points_total
        # note exacte ET note arrondie au 0,5 supérieur, cohérentes entre elles
        assert result.note_raw is not None and result.note is not None
        assert result.note == scoring.round_half_up(result.note_raw)
        assert 0 <= result.note <= 10
        assert (result.note * 2) % 1 == 0

        # résultat par exercice, avec les deux échelles conservées
        items = db_session.query(CopyItemResult).filter_by(copy_result_id=result.id).all()
        assert items
        assert sum(i.bareme_points for i in items) == pytest.approx(result.points_total)
        assert sum(i.points_earned for i in items) == pytest.approx(result.points_earned)
        for i in items:
            assert (i.bareme_points * 2) % 1 == 0
            assert i.max_score > 0                # échelle interne du moteur
            assert i.competency_id                # rattaché à une compétence (suivi)


@needs_manual
def test_the_note_follows_the_base_chosen_at_creation(db_session):
    """La règle de trois, sur une copie RÉELLE corrigée de bout en bout : la
    même copie vaut la même PART du sujet, seule la base choisie par le
    professeur change ce qui est imprimé.

    La base est la seule variable : on rejoue la consolidation sur LA MÊME
    copie plutôt que de comparer trois sujets (dont les réponses simulées
    diffèrent — le mock est seedé par sujet — ce qui comparerait des élèves
    différents et pas des bases différentes)."""
    a = _seed_control(db_session, note_base=20, n_students=1)
    _run_to_finalized(db_session, a)
    copy = db_session.query(Copy).filter_by(assessment_id=a.id).first()

    notes: dict[int, float] = {}
    for base in scoring.NOTE_BASES:
        a.note_base = base
        db_session.commit()
        result = scoring.compute_copy_result(db_session, copy, a)
        db_session.commit()
        assert result.note_base == base
        assert result.note == scoring.round_half_up(result.note_raw)
        assert 0 <= result.note <= base
        notes[base] = result.note_raw

    assert 0 < notes[20] <= 20                    # copie réellement corrigée
    assert notes[20] == pytest.approx(notes[10] * 2)
    assert notes[10] == pytest.approx(notes[5] * 2)


@needs_manual
def test_a_training_subject_is_tracked_but_never_graded(db_session):
    a = _seed_control(db_session, n_students=1)
    a.type = "training"
    db_session.commit()
    _run_to_finalized(db_session, a)

    result = db_session.query(CopyResult).filter_by(assessment_id=a.id).first()
    assert result is not None
    assert result.points_total > 0        # les points restent : c'est le suivi
    assert result.note_base == scoring.NOTE_BASE_UNGRADED
    assert result.note is None and result.note_raw is None


@needs_manual
def test_finalizing_twice_recomputes_instead_of_piling_up(db_session):
    a = _seed_control(db_session, n_students=1)
    batch = _run_to_finalized(db_session, a)
    pipeline.finalize_batch(db_session, batch)
    db_session.commit()

    assert db_session.query(CopyResult).filter_by(assessment_id=a.id).count() == 1
    result = db_session.query(CopyResult).filter_by(assessment_id=a.id).first()
    items = db_session.query(CopyItemResult).filter_by(copy_result_id=result.id).count()
    n_items = db_session.query(CopyItem).join(Copy, CopyItem.copy_id == Copy.id) \
        .filter(Copy.assessment_id == a.id).count()
    assert items == n_items


@needs_manual
def test_a_cancelled_question_leaves_the_bareme_of_the_subject(db_session):
    """« Annuler la question » (max_score remis à 0) : l'exercice ne doit peser
    ni au numérateur ni au dénominateur — sinon l'élève est noté sur un exercice
    que le professeur vient de retirer."""
    a = _seed_control(db_session, n_students=1)
    _run_to_finalized(db_session, a)
    before = db_session.query(CopyResult).filter_by(assessment_id=a.id).first()
    total_before = before.points_total

    copy = db_session.query(Copy).filter_by(assessment_id=a.id).first()
    item = (db_session.query(CopyItem).filter_by(copy_id=copy.id)
            .order_by(CopyItem.sequence).first())
    resp = db_session.query(StudentResponse).filter_by(copy_item_id=item.id).first()
    old = (db_session.query(GradingDecision).filter_by(response_id=resp.id)
           .order_by(GradingDecision.created_at.desc()).first())
    db_session.add(GradingDecision(response_id=resp.id, source="teacher", score=0.0,
                                   max_score=0.0, confidence=1.0, tier="D",
                                   reason_code="teacher_cancel_item", status="validated"))
    db_session.commit()

    after = scoring.compute_copy_result(db_session, copy, a)
    db_session.commit()
    cancelled_bareme = scoring.item_bareme(item.grading_json, item.response_type)
    assert after.points_total == pytest.approx(total_before - cancelled_bareme)
    assert old.max_score > 0          # la décision d'origine reste (append-only)


@needs_manual
def test_overlay_prints_the_stored_note_on_the_chosen_base(db_session):
    """La note imprimée est CELLE STOCKÉE à la finalisation : deux formules
    pour une même note finiraient par diverger."""
    a = _seed_control(db_session, note_base=5, n_students=1)
    batch = _run_to_finalized(db_session, a)

    captured: list[dict] = []
    import app.services.pipeline as pipeline_mod
    orig = pipeline_mod.render_overlay
    pipeline_mod.render_overlay = lambda path, **kw: (
        captured.extend(kw["copies_annotations"]), orig(path, **kw))[1]
    try:
        pipeline.build_overlays(db_session, batch)
    finally:
        pipeline_mod.render_overlay = orig
    db_session.commit()

    result = db_session.query(CopyResult).filter_by(assessment_id=a.id).first()
    assert captured
    page = captured[0]
    assert page["note"] == f"{scoring.format_points(result.note)}/5"

    # les points affichés à côté des exercices sont ceux du BARÈME et
    # s'additionnent jusqu'aux points totaux de la note — pas le score interne
    # du moteur (3 cellules justes sur 4 ne valent pas « 3 points »)
    assert sum(z["max_score"] for z in page["page_zones"]) == pytest.approx(
        result.points_total)
    assert sum(z["score"] for z in page["page_zones"]) == pytest.approx(
        result.points_earned)

    # l'appréciation imprimée rejoint le résultat consolidé (suivi personnalisé)
    assert result.progress_json is not None
    assert result.appreciation == (page.get("synthesis") or "")

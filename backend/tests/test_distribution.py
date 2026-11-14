"""Tests unitaires de services.distribution (répartition automatique des
exercices : difficulté, mix homogène des types, anti-répétition dans une copie).

Le choix des exercices d'un sujet INDIVIDUEL, lui, est testé dans
test_student_history.py : il ne se fait plus ici."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import GeneratedExercise
from app.services import distribution


def _row(kind: str, response_type: str = "short_text") -> GeneratedExercise:
    return GeneratedExercise(kind=kind, response_type=response_type, difficulty_level=3)


def test_difficulty_level3_neutral_for_common_modes():
    assert distribution.difficulty_level3("common", student_level_1_10=10) == \
        distribution.difficulty_level3("common", student_level_1_10=1)
    assert distribution.difficulty_level3("common_variants", student_level_1_10=10) == \
        distribution.difficulty_level3("common", student_level_1_10=1)


def test_difficulty_level3_individual_adapts_to_student():
    weak = distribution.difficulty_level3("individual", student_level_1_10=1)
    strong = distribution.difficulty_level3("individual", student_level_1_10=10)
    assert weak < strong


def test_variant_seed_common_is_identical_for_all_students():
    seeds = {distribution.variant_seed(1000, "common", i) for i in range(20)}
    assert seeds == {1000}


def test_variant_seed_common_variants_caps_at_three():
    seeds = {distribution.variant_seed(1000, "common_variants", i) for i in range(20)}
    assert len(seeds) == 3


def test_variant_seed_individual_is_unique_per_student():
    seeds = [distribution.variant_seed(1000, "individual", i) for i in range(20)]
    assert len(set(seeds)) == 20


def test_pick_balanced_exercise_favors_underrepresented_kind():
    rows = [_row("application"), _row("application"), _row("probleme")]
    target_mix = {"application": 0.55, "probleme": 0.35, "qcm": 0.10}
    counts = {"application": 10, "probleme": 0, "qcm": 0}
    picked = distribution.pick_balanced_exercise(rows, counts, target_mix, seed=0)
    assert distribution.exercise_bucket(picked) == "probleme"


def test_pick_balanced_exercise_qcm_bucket_from_response_type():
    row = _row("application", response_type="qcm_single")
    assert distribution.exercise_bucket(row) == "qcm"


def test_pick_balanced_exercise_empty_bank_raises():
    try:
        distribution.pick_balanced_exercise([], {}, {}, 0)
        assert False, "devait lever ValueError"
    except ValueError:
        pass


def _statement_row(statement: str, value: int, row_id: str = "") -> GeneratedExercise:
    # id explicite : le défaut SQLAlchemy n'est appliqué qu'à l'insertion
    return GeneratedExercise(id=row_id or f"row-{id(statement)}-{value}",
                             kind="application", response_type="short_text",
                             difficulty_level=3, statement=statement,
                             expected_json={"type": "integer", "value": value},
                             grading_json={"max_score": 1, "comparator": "numeric"})


def test_exercise_identity_is_content_based_not_row_id():
    # Deux LIGNES distinctes (banques de deux compétences voisines) portant le
    # MÊME exercice : pour l'élève c'est un doublon, même si le dédoublonnage
    # de la banque — par compétence — les a légitimement laissées passer.
    a = _statement_row("Calcule $7 \\times 8$.", 56, row_id="ligne-competence-A1.1")
    b = _statement_row("Calcule $7 \\times 8$.", 56, row_id="ligne-competence-A1.2")
    assert a.id != b.id
    assert distribution.exercise_identity(a) == distribution.exercise_identity(b)
    other = _statement_row("Calcule $9 \\times 8$.", 72)
    assert distribution.exercise_identity(other) != distribution.exercise_identity(a)


def test_pick_balanced_exercise_excludes_same_exercise_from_another_competency():
    served = _statement_row("Calcule $7 \\times 8$.", 56)
    twin = _statement_row("Calcule $7 \\times 8$.", 56)      # autre ligne, même contenu
    fresh = _statement_row("Calcule $9 \\times 8$.", 72)
    picked = distribution.pick_balanced_exercise(
        [twin, fresh], {}, {"application": 1.0}, seed=0,
        exclude_keys={distribution.exercise_identity(served)})
    assert picked is fresh


def test_pick_balanced_exercise_falls_back_when_everything_excluded():
    # Filet de sécurité : mieux vaut répéter un exercice que ne rien imprimer.
    row = _statement_row("Calcule $7 \\times 8$.", 56)
    picked = distribution.pick_balanced_exercise(
        [row], {}, {"application": 1.0}, seed=0,
        exclude_keys={distribution.exercise_identity(row)})
    assert picked is row


# ------------------------------------------------------------------ jumelles (anti-triche)
import pytest  # noqa: E402


@pytest.mark.parametrize("jeux,dispositions,period", [(3, 2, 6), (2, 2, 4), (3, 1, 3), (2, 1, 2)])
def test_neighbours_never_share_values_and_identical_copies_are_far_apart(jeux, dispositions, period):
    seats = [distribution.twin_choice(i, jeux, dispositions) for i in range(60)]
    assert all(a[0] != b[0] for a, b in zip(seats, seats[1:]))            # voisins : autres valeurs
    assert set(seats) == {(j, d) for j in range(1, jeux + 1) for d in range(1, dispositions + 1)}
    for i, seat in enumerate(seats):
        again = next((k for k in range(i + 1, 60) if seats[k] == seat), None)
        assert again is None or again - i >= period


def test_twin_for_serves_the_sibling_of_the_seat_and_history_counts_the_family():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import Competency, CompetencyFramework
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    framework = CompetencyFramework(grade_level="3e", name="Test")
    db.add(framework)
    db.flush()
    comp = Competency(framework_id=framework.id, code="Z1.1", label="Test")
    db.add(comp)
    db.flush()
    rows = {}
    for jeu in (1, 2):
        for disposition in (1, 2):
            head = (jeu, disposition) == (1, 1)
            row = GeneratedExercise(competency_id=comp.id, difficulty_level=1, statement=f"j{jeu}d{disposition}",
                                    status="active" if head else "twin", twin_group="run/s01",
                                    twin_jeu=jeu, twin_disposition=disposition)
            db.add(row)
            rows[(jeu, disposition)] = row
    lone = GeneratedExercise(competency_id=comp.id, difficulty_level=1, statement="seule")
    db.add(lone)
    db.flush()
    head = rows[(1, 1)]
    served = [distribution.twin_for(db, head, seat) for seat in range(4)]
    assert [(r.twin_jeu, r.twin_disposition) for r in served] == [(1, 1), (2, 1), (1, 2), (2, 2)]
    assert distribution.twin_for(db, lone, 3) is lone
    assert distribution.family_heads(db, [rows[(2, 2)].id, lone.id]) == {rows[(2, 2)].id: head.id}


def test_the_class_list_follows_the_last_scanned_batch():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import Assessment, Copy, DocumentPage, ScanBatch, ScannedPage, SchoolClass, Student
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    room = SchoolClass(name="3e A", grade_level="3e")
    db.add(room)
    db.flush()
    names = ["Awa", "Bintou", "Cédric", "Désiré", "Esther"]
    students = [Student(class_id=room.id, name=n, order_index=i, llm_pseudonym=f"p{i}")
                for i, n in enumerate(names)]
    db.add_all(students)
    assessment = Assessment(title="Devoir", class_id=room.id)
    db.add(assessment)
    db.flush()
    batch = ScanBatch(assessment_id=assessment.id)
    db.add(batch)
    db.flush()

    def scan(order, start=0):
        for index, name in enumerate(order, start):
            student = next(s for s in students if s.name == name)
            copy = Copy(assessment_id=assessment.id, student_id=student.id)
            db.add(copy)
            db.flush()
            page = DocumentPage(copy_id=copy.id, page_no=1)
            db.add(page)
            db.flush()
            db.add(ScannedPage(batch_id=batch.id, source_index=index, page_id=page.id))
        db.flush()

    scan(["Désiré", "Awa"])                      # moins de la moitié : rien ne bouge
    assert distribution.reorder_class_from_scan(db, batch.id) == 0
    scan(["Esther"], start=2)
    assert distribution.reorder_class_from_scan(db, batch.id) > 0
    roster = db.query(Student).order_by(Student.order_index).all()
    assert [s.name for s in roster] == ["Désiré", "Awa", "Esther", "Bintou", "Cédric"]
    assert distribution.reorder_class_from_scan(db, batch.id) == 0      # déjà à jour

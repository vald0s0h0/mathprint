"""Le papier ne doit pas être gaspillé : après génération, il ne doit plus
rester en bas de page un trou qu'un exercice disponible aurait pu combler.

Ce test tourne sur la source « indigo » — pool FINI, sans cartes de remplissage
(`exercise_gen.filler_bank_rows` renvoie [] pour elle) et source par défaut des
3e. C'est précisément le cas où l'ancienne passe de remplissage ne faisait
RIEN : elle tirait un exercice au hasard, le créait en base, mesurait, le
supprimait s'il débordait, et abandonnait après quelques échecs — sans avoir
essayé la petite carte qui tenait.
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
    Competency, CompetencyFramework, Assessment, Copy, CopyItem,
    GeneratedExercise, SchoolClass, Student,
)
from app.services import generation, pdfgen
from app.services.pdfgen import DEFAULT_TEMPLATES


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# Énoncés VRAIMENT distincts : `exercise_gen._dedup_key` efface les chiffres
# avant de comparer (deux exercices qui ne diffèrent que par leurs nombres sont
# le même exercice pour l'élève). Une banque de test faite de « Calcule $a×b$ »
# répétés ne contient donc qu'UN exercice aux yeux du moteur — et un test bâti
# dessus mesurerait le dédoublonnage, pas le remplissage.
_COURTS = [
    "Calcule le produit de {a} par {b}.",
    "Donne l'écriture décimale de $\\dfrac{{{a}}}{{{b}}}$.",
    "Quel est le périmètre d'un carré de côté {a} cm ?",
    "Simplifie l'expression $ {a}x + {b}x $.",
    "Convertis {a} minutes en secondes.",
    "Range dans l'ordre croissant : {a} ; {b} ; {c}.",
    "Quelle est la moitié de {a} ?",
    "Écris {a} sous forme de puissance de {b}.",
]
_LONGS = [
    "Un train quitte la gare à {a} h et roule à {b} km/h. Explique ta démarche, "
    "justifie chaque étape du raisonnement, puis conclus par une phrase.",
    "Une piscine de {a} m de long se remplit de {b} litres par minute. Détaille "
    "le calcul du temps de remplissage et rédige ta conclusion complètement.",
    "Un commerçant achète {a} articles et en revend {b}. Présente ton "
    "raisonnement étape par étape, puis rédige une phrase de conclusion.",
    "Le périmètre d'un rectangle vaut {a} cm et sa longueur {b} cm. Explique "
    "comment tu retrouves la largeur, en justifiant chacune de tes étapes.",
]


def _bank(db, comp, n_per_level=20):
    """Pool fini et VARIÉ en hauteur : des cartes courtes (un calcul) et des
    cartes longues (un problème rédigé), pour qu'il existe toujours une petite
    carte capable de combler un bas de colonne. Chaque ligne a un énoncé et une
    réponse réellement différents des autres."""
    rows = []
    n = 0
    for level in (1, 2, 3):
        for i in range(n_per_level):
            court = i % 2 == 0
            tpl = (_COURTS[(i // 2) % len(_COURTS)] if court
                   else _LONGS[(i // 2) % len(_LONGS)])
            statement = f"[{comp.short_id} niveau {level}] " + tpl.format(
                a=n + 2, b=n + 3, c=n + 5)
            rows.append(GeneratedExercise(
                competency_id=comp.id, difficulty_level=level, variant=i,
                statement=statement, correction="", source="indigo",
                kind="application" if court else "probleme",
                response_type="short_text",
                expected_json={"value": f"{comp.short_id}-{level}-{i}"},
                grading_json={"bareme_points": 1.0}, status="active"))
            n += 1
    db.add_all(rows)
    db.commit()
    return rows


def _seed(db, mode="individual", pages=1, n_comp=2):
    fw = CompetencyFramework(grade_level="3e", name="Test 3e")
    db.add(fw)
    db.flush()
    comps = []
    for i in range(n_comp):
        c = Competency(framework_id=fw.id, code=f"A1.{i}", short_id=f"A1.{i}",
                       label=f"Compétence {i}", domain_code="A",
                       domain_name="Nombres", chapter_code="A1",
                       chapter_name="Opérations", order_index=i)
        db.add(c)
        comps.append(c)
    cls = SchoolClass(name="3eA", grade_level="3e")
    db.add(cls)
    db.flush()
    db.add(Student(class_id=cls.id, name="Alex Test", order_index=0,
                   llm_pseudonym=f"E-{cls.id[:8]}", active=True))
    a = Assessment(class_id=cls.id, type="training", title="Entraînement",
                   pages_target=pages, personalization_mode=mode, note_base=20)
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "indigo"}
    db.add(a)
    db.commit()
    for c in comps:
        _bank(db, c)
    return a, comps


def _served_heights(db, copy_id):
    """Hauteurs des cartes réellement posées sur la copie, mesurées comme le
    fait la génération."""
    tpl = DEFAULT_TEMPLATES
    items = (db.query(CopyItem).filter_by(copy_id=copy_id)
             .order_by(CopyItem.sequence).all())
    seen, shapes = set(), []
    for it in items:
        # un composite porte plusieurs CopyItem pour UNE carte : ici, aucun
        if it.generated_exercise_id in seen:
            continue
        seen.add(it.generated_exercise_id)
        row = db.get(GeneratedExercise, it.generated_exercise_id)
        shapes.append(generation.render_shape(row))
    return [pdfgen.estimate_item_height(
        sh, int(tpl["exercise"].get("font_size", 9)),
        int(tpl["exercise"].get("math_size", 12)), tpl["exercise"]) for sh in shapes], seen


def _smallest_unused(db, comps, used_ids, levels):
    """Le plus petit exercice que la copie AURAIT PU accueillir : même
    compétences, mêmes dérivés que ceux déjà servis (un sujet commun ne sert
    qu'un dérivé, un sujet individuel le mix de l'élève), et pas déjà utilisé."""
    tpl = DEFAULT_TEMPLATES
    heights = []
    for c in comps:
        for row in db.query(GeneratedExercise).filter_by(competency_id=c.id).all():
            if row.id in used_ids or row.difficulty_level not in levels:
                continue
            heights.append(pdfgen.estimate_item_height(
                generation.render_shape(row), int(tpl["exercise"].get("font_size", 9)),
                int(tpl["exercise"].get("math_size", 12)), tpl["exercise"]))
    return min(heights) if heights else None


@pytest.mark.parametrize("mode", ["common", "individual"])
@pytest.mark.parametrize("pages", [1, 2])
def test_no_hole_big_enough_for_an_available_exercise(db, mode, pages):
    """L'exigence, telle qu'elle se vérifie : la place libre restante est plus
    petite que le plus petit exercice encore disponible. Sinon, du papier a été
    imprimé pour rien."""
    a, comps = _seed(db, mode=mode, pages=pages)
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()

    copy = db.query(Copy).filter_by(assessment_id=a.id).first()
    heights, used = _served_heights(db, copy.id)
    holes = pdfgen.free_space(heights, pages)
    levels = {db.get(GeneratedExercise, ex_id).difficulty_level for ex_id in used}
    smallest = _smallest_unused(db, comps, used, levels)

    assert smallest is not None, (
        "la banque doit garder des exercices non servis, sinon le test ne prouve "
        "rien : un pool épuisé ne peut évidemment plus combler aucun trou")
    assert max(holes) < smallest, (
        f"{max(holes):.0f} pt libres alors qu'un exercice de {smallest:.0f} pt "
        f"était disponible ({mode}, {pages} page(s))")


def test_the_copy_never_exceeds_its_page_target(db):
    a, _comps = _seed(db, mode="individual", pages=1)
    report = generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()
    copy = db.query(Copy).filter_by(assessment_id=a.id).first()
    assert copy.total_pages <= 1
    assert not report["warnings"]


def test_an_exercise_is_never_served_twice_on_the_same_copy(db):
    a, _comps = _seed(db, mode="individual", pages=2)
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()
    copy = db.query(Copy).filter_by(assessment_id=a.id).first()
    ids = [i.generated_exercise_id for i in
           db.query(CopyItem).filter_by(copy_id=copy.id).all()]
    assert len(ids) == len(set(ids))
    assert all(i is not None for i in ids), "l'exercice servi doit être tracé"


def test_every_checked_competency_appears_on_the_copy(db):
    """La personnalisation ne rétrécit jamais le périmètre coché."""
    a, comps = _seed(db, mode="individual", pages=2, n_comp=3)
    generation.generate_assessment_job(db, a, job=None, font_size=9)
    db.commit()
    copy = db.query(Copy).filter_by(assessment_id=a.id).first()
    served = {db.get(GeneratedExercise, i.generated_exercise_id).competency_id
              for i in db.query(CopyItem).filter_by(copy_id=copy.id).all()}
    assert served == {c.id for c in comps}

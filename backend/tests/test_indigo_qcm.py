"""Indigo — mode « QCM only » : barème CODÉ, vérification Python, trio de dérivés.

Ces tests ne touchent ni au réseau ni aux gros PDF. Ils fixent les trois
promesses de la pipeline : le barème n'est plus une opinion du modèle, un QCM
mathématiquement faux ne passe pas, et un exercice du manuel donne trois lignes
(base + dérivé facile + dérivé difficile).
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import Competency, CompetencyFramework, IndigoExercise
from app.services import (exercise_gen, grading, indigo, indigo_check, indigo_llm,
                          indigo_qcm, scoring)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _comp(db):
    fw = CompetencyFramework(grade_level="3e", name="T")
    db.add(fw); db.flush()
    c = Competency(framework_id=fw.id, code="A1.1", short_id="A1.1", label="Diviseurs",
                   domain_code="A", domain_name="Nombres", chapter_code="A1",
                   chapter_name="Nombres entiers", order_index=1)
    db.add(c); db.commit()
    return c


def _single(**over):
    base = {"response_type": "qcm_single",
            "statement": "Calcule le PGCD de 1925 et 4125.",
            "choices": ["55", "175", "275", "385"], "correct": [2],
            "check": {"kind": "value", "expr": "gcd(1925, 4125)", "choice": 2}}
    return {**base, **over}


def _multiple(**over):
    base = {"response_type": "qcm_multiple",
            "statement": "Coche les nombres premiers de la liste.",
            "choices": ["17", "21", "23", "27"], "correct": [0, 2],
            "check": {"kind": "set",
                      "exprs": ["isprime(17)", "isprime(21)", "isprime(23)", "isprime(27)"]}}
    return {**base, **over}


def _grid(**over):
    base = {"response_type": "checkbox_grid",
            "statement": "Vrai ou faux ? Coche une case par ligne.",
            "cols": ["Vrai", "Faux"],
            "rows": [{"label": "$12$ est un multiple de $3$", "correct": 0},
                     {"label": "$14$ est un multiple de $4$", "correct": 1}],
            "check": {"kind": "rows", "exprs": ["Eq(Mod(12,3),0)", "Eq(Mod(14,4),0)"]}}
    return {**base, **over}


# ------------------------------------------------------------------ barème codé

def test_qcm_bareme_is_computed_not_asked():
    """1 point pour un choix unique, 0,5 par CASE d'un QCM multiple, 0,5 par
    LIGNE d'une grille — la règle entière, sans intervention du modèle."""
    assert scoring.qcm_bareme("qcm_single", {"choices": ["a", "b", "c"]}) == 1.0
    assert scoring.qcm_bareme("qcm_multiple", {"choices": ["a", "b", "c", "d"]}) == 2.0
    assert scoring.qcm_bareme("checkbox_grid", {"rows": [{}] * 6}) == 3.0


def test_qcm_bareme_refuses_out_of_bounds_instead_of_clamping():
    """Au-delà des bornes, on LÈVE : un barème écrêté en silence ne
    correspondrait plus aux points réellement attribués par le moteur."""
    with pytest.raises(ValueError):
        scoring.qcm_bareme("qcm_multiple", {"choices": ["x"] * 9})
    with pytest.raises(ValueError):
        scoring.qcm_bareme("checkbox_grid", {"rows": [{}] * 12})
    with pytest.raises(ValueError):
        scoring.qcm_bareme("short_text", {})


def _earned(expected, gpolicy, response_type, selected):
    """Points barème réellement obtenus, en passant par le VRAI moteur de
    correction — surtout pas par une reproduction du calcul dans le test."""
    verdict = grading.grade(expected, gpolicy, "", 0.99, selected_choices=selected)
    bareme = scoring.qcm_bareme(response_type, gpolicy)
    return scoring.earned_points(verdict["score"], verdict["max_score"], bareme)


def test_single_choice_is_one_point_all_or_nothing():
    exp = {"type": "choice", "correct": [2]}
    pol = {"max_score": 4, "comparator": "qcm", "choices": ["a", "b", "c", "d"],
           "exclusive": True}
    assert _earned(exp, pol, "qcm_single", [2]) == 1.0
    assert _earned(exp, pol, "qcm_single", [1]) == 0.0


def test_multiple_choice_pays_half_a_point_per_right_decision():
    """Une case laissée vide À RAISON rapporte ses 0,5 point, au même titre
    qu'une case cochée à raison (décision explicite du 03/08)."""
    exp = {"type": "choice", "correct": [0, 2]}
    pol = {"max_score": 4, "comparator": "qcm", "choices": ["a", "b", "c", "d"],
           "exclusive": False}
    assert _earned(exp, pol, "qcm_multiple", [0, 2]) == 2.0     # 4 décisions justes
    assert _earned(exp, pol, "qcm_multiple", [0]) == 1.5        # 3 justes sur 4
    assert _earned(exp, pol, "qcm_multiple", [0, 1, 2, 3]) == 1.0   # 2 justes sur 4


def test_grid_pays_half_a_point_per_right_row():
    rows = [{"label": "a", "correct": 0}, {"label": "b", "correct": 1},
            {"label": "c", "correct": 0}]
    exp = {"type": "grid", "cols": ["Vrai", "Faux"], "rows": rows}
    pol = {"max_score": 3, "comparator": "grid", "cols": ["Vrai", "Faux"], "rows": rows}
    assert _earned(exp, pol, "checkbox_grid", [0, 1, 0]) == 1.5
    assert _earned(exp, pol, "checkbox_grid", [0, 1, 1]) == 1.0
    assert _earned(exp, pol, "checkbox_grid", [1, 0, 1]) == 0.0


# --------------------------------------------------- vérification déterministe

def test_verify_accepts_the_three_formats():
    for variant in (_single(), _multiple(), _grid()):
        assert indigo_check.verify(variant, has_figure=False) == []


def test_verify_catches_a_distractor_equal_to_the_answer():
    """Deux cases justes = exercice incorrigeable, quelle que soit l'écriture
    (le LaTeX est aplati avant comparaison)."""
    problems = indigo_check.verify(
        _single(choices=["55", "175", "275", "$275$"]), has_figure=False)
    assert problems and "identiques" in problems[0]


def test_verify_catches_a_mathematically_wrong_answer():
    """Le cœur du filet : sympy recalcule ce que le modèle a déclaré."""
    problems = indigo_check.verify(
        _single(correct=[1], check={"kind": "value", "expr": "gcd(1925, 4125)",
                                    "choice": 1}), has_figure=False)
    assert problems and "ne vaut pas" in problems[0]


def test_verify_computes_medians_and_means_from_explicit_series():
    median = _single(
        statement="Calcule la médiane de la série proposée.",
        choices=["$1{,}84$", "$1{,}89$", "$1{,}92$"], correct=[1],
        check={"kind": "value",
               "expr": "median([1.78,1.81,1.84,1.89,1.92,1.93,2.02])",
               "choice": 1})
    assert indigo_check.verify(median, has_figure=False) == []
    mean = _single(
        statement="Calcule la moyenne de la série proposée.",
        choices=["$3$", "$4$", "$5$"], correct=[1],
        check={"kind": "value", "expr": "statistics.mean([2,4,6])", "choice": 1})
    assert indigo_check.verify(mean, has_figure=False) == []
    assert indigo_check._truth("Eq(mean([2,4,6]),4)") is True


def test_verify_compares_numeric_choices_that_carry_units():
    speed = _single(
        statement="Calcule la vitesse moyenne obtenue.",
        choices=["156 km/h", "168 km/h", "180 km/h"], correct=[1],
        check={"kind": "value", "expr": "168", "choice": 1})
    assert indigo_check.verify(speed, has_figure=False) == []
    price = _single(
        statement="Calcule le prix moyen des produits.",
        choices=["$3{,}00$ €", "$3{,}15$ €", "$3{,}50$ €"], correct=[1],
        check={"kind": "value", "expr": "3.15", "choice": 1})
    assert indigo_check.verify(price, has_figure=False) == []


def test_verify_catches_a_wrong_truth_value_in_a_multiple_qcm():
    problems = indigo_check.verify(_multiple(correct=[0, 1, 2]), has_figure=False)
    assert problems and "21" in problems[0]


def test_verify_catches_a_wrong_column_in_a_grid():
    rows = [{"label": "$12$ est un multiple de $3$", "correct": 1},
            {"label": "$14$ est un multiple de $4$", "correct": 1}]
    problems = indigo_check.verify(_grid(rows=rows), has_figure=False)
    assert problems and "colonne correcte devrait être 0" in problems[0]


def test_verify_refuses_a_statement_that_points_outside_the_sheet():
    """L'élève n'a que sa copie : « reprends l'exercice 12 » est insoluble."""
    problems = indigo_check.verify(
        _single(statement="Reprends l'exercice 12 et calcule le PGCD de 1925 et 4125."),
        has_figure=False)
    assert problems and "hors de la feuille" in problems[0]


def test_a_figure_mentioned_without_a_figure_is_a_note_not_a_refusal():
    """Le manque d'image ne casse pas l'exercice : il donne du travail au
    relecteur, qui a la page sous les yeux et ajoute la figure au brouillon.

    Le refuser était en plus une impasse : on demandait au modèle de
    « reformuler sans visuel » un énoncé dont les données SONT un tableau, et il
    repartait pour quatre tentatives identiques avant que la source ne soit
    jetée (pages 86-87 : 9 sources sur 33)."""
    stmt = "Lis la figure ci-contre et donne le PGCD de 1925 et 4125."
    assert indigo_check.verify(_single(statement=stmt), has_figure=True) == []
    assert indigo_check.verify(_single(statement=stmt), has_figure=False) == []
    assert indigo_check.figure_note(stmt, has_figure=False)
    assert indigo_check.figure_note(stmt, has_figure=True) == ""


def test_table_and_histogram_are_recognized_as_visual_references():
    for word in ("tableau", "histogramme", "image"):
        statement = f"Lis le {word} ci-dessous et choisis la bonne valeur."
        assert indigo_check.verify(_single(statement=statement), has_figure=True) == []
        assert indigo_check.figure_note(statement, has_figure=False)


def test_verify_refuses_an_answer_field_marker_in_a_qcm():
    problems = indigo_check.verify(
        _single(statement="Calcule le PGCD de 1925 et 4125 : {{blank}}"), has_figure=False)
    assert problems and "coche" in problems[0]


def test_verify_refuses_a_grid_too_tall_for_the_coded_bareme():
    rows = [{"label": f"ligne {i}", "correct": 0} for i in range(12)]
    problems = indigo_check.verify(_grid(rows=rows, check={"kind": "none"}),
                                   has_figure=False)
    assert problems and "2 à 10" in problems[0]


def test_a_non_computational_question_is_only_linted():
    """Reconnaissance de vocabulaire : aucun calcul à vérifier, et on n'en
    invente pas — mais le lint structurel s'applique quand même."""
    variant = _single(statement="Quel mot désigne le résultat d'une multiplication ?",
                      choices=["la somme", "le produit", "le quotient"],
                      correct=[1], check={"kind": "none"})
    assert indigo_check.verify(variant, has_figure=False) == []
    assert indigo_check.verify({**variant, "correct": [0, 1]}, has_figure=False)


# ------------------------------------------------------------------ trio

def _fake_llm(monkeypatch, payloads):
    """Remplace l'appel LLM par une file de réponses (une par appel)."""
    calls = []

    def fake(db, stage, system, payload, correlation_id):
        calls.append((stage, payload))
        return payloads.pop(0) if payloads else {"exercises": []}

    monkeypatch.setattr(indigo_llm, "call", fake)
    return calls


GUIDE = ("Le PGCD divise les deux nombres à la fois. Décompose chaque nombre en "
         "facteurs premiers, puis garde ce qu'ils ont en commun.")


def _trio_payload(number="34"):
    return {"exercises": [{
        "source_number": number,
        "guide": GUIDE,
        "base": _single(),
        "facile": _single(statement="Calcule le PGCD de 12 et 18.",
                          choices=["2", "3", "6", "9"], correct=[2],
                          check={"kind": "value", "expr": "gcd(12, 18)", "choice": 2}),
        "difficile": _single(
            statement="Calcule le PGCD de 1925, 4125 et 2200.",
            choices=["25", "55", "175", "275"], correct=[3],
            check={"kind": "value", "expr": "gcd(gcd(1925, 4125), 2200)", "choice": 3}),
    }]}


def test_generate_batch_returns_a_verified_trio(db, monkeypatch):
    comp = _comp(db)
    _fake_llm(monkeypatch, [_trio_payload()])
    out = indigo_qcm.generate_batch(
        db, comp, "3e", [{"number": "34", "statement": "PGCD ?", "correction": "",
                          "has_figure": False}], set())
    assert set(out) == {"34"}
    kinds = [k for k, _v in out["34"]["variants"]]
    assert kinds == ["base", "facile", "difficile"]
    for _kind, valid in out["34"]["variants"]:
        assert valid["response_type"] in indigo_check.QCM_TYPES
        # barème CODÉ, jamais celui du modèle
        assert valid["grading"]["bareme_points"] == 1.0


def test_one_guide_is_shared_by_the_three_variants(db, monkeypatch):
    """Le guide est écrit UNE fois par exercice : même notion, même règle, même
    piège pour les trois variantes — seuls les nombres changent. En faire écrire
    trois, c'était payer deux paraphrases."""
    comp = _comp(db)
    _fake_llm(monkeypatch, [_trio_payload()])
    manual = {"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}
    out = indigo_qcm.generate_batch(db, comp, "3e", [manual], set())
    assert out["34"]["guide"] == GUIDE
    assert {v["correction"] for _k, v in out["34"]["variants"]} == {GUIDE}


def test_the_prof_solution_comes_from_the_manual_not_from_the_model(db, monkeypatch):
    """Le corrigé du professeur n'est plus demandé au modèle : il est déjà lu
    gratuitement dans le manuel prof. Le champ reste rempli (il est affiché dans
    l'aperçu et éditable), mais sans un token de sortie."""
    comp = _comp(db)
    _fake_llm(monkeypatch, [_trio_payload()])
    manual = {"number": "34", "statement": "PGCD ?",
              "correction": "PGCD(1925 ; 4125) = 275.", "has_figure": False}
    out = indigo_qcm.generate_batch(db, comp, "3e", [manual], set())
    # aucune variante ne porte de corrigé prof
    assert all("correction_solution" not in v for _k, v in out["34"]["variants"])

    row = IndigoExercise(id="base-id", competency_id=comp.id, grade_level="3e",
                         source_number="34", badge_type="exercice")
    indigo._persist_qcm_trio(db, row, manual, out["34"])
    db.commit()
    rows = db.query(IndigoExercise).all()
    assert all(r.correction_solution == "PGCD(1925 ; 4125) = 275." for r in rows)
    assert all(r.correction_guide == GUIDE for r in rows)


def test_a_missing_guide_never_costs_the_three_qcm(db, monkeypatch):
    """Un guide absent est un placeholder à compléter, pas trois exercices
    perdus — c'est le texte le moins coûteux à réécrire à la main."""
    payload = _trio_payload()
    payload["exercises"][0]["guide"] = "trop court"
    _fake_llm(monkeypatch, [payload, {"exercises": []}])
    manual = {"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}
    out = indigo_qcm.generate_batch(db, _comp(db), "3e", [manual], set())
    assert [k for k, _v in out["34"]["variants"]] == ["base", "facile", "difficile"]
    assert "À compléter" in out["34"]["guide"]


def test_a_variant_that_fails_verification_is_dropped_not_published(db, monkeypatch):
    """Le dérivé difficile ment sur ses mathématiques, et la réparation échoue :
    il est ABANDONNÉ. La base et le dérivé facile, eux, sont conservés."""
    payload = _trio_payload()
    payload["exercises"][0]["difficile"] = _single(
        statement="Calcule le PGCD de 1925 et 4125 autrement.",
        choices=["25", "55", "175", "999"], correct=[3],
        check={"kind": "value", "expr": "gcd(1925, 4125)", "choice": 3})
    _fake_llm(monkeypatch, [payload, {"exercises": []}])
    out = indigo_qcm.generate_batch(
        db, _comp(db), "3e",
        [{"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}],
        set())
    assert [k for k, _v in out["34"]["variants"]] == ["base", "facile"]


def test_a_repaired_variant_is_kept(db, monkeypatch):
    """Une seule tentative de réparation, avec les raisons trouvées par Python."""
    payload = _trio_payload()
    payload["exercises"][0]["facile"] = _single(choices=["55", "55", "275", "385"])
    fixed = {"exercises": [{"source_number": "34", "variant": "facile",
                            "fixed": _single(statement="Calcule le PGCD de 12 et 18.",
                                             choices=["2", "3", "6", "9"], correct=[2],
                                             check={"kind": "value", "expr": "gcd(12, 18)",
                                                    "choice": 2})}]}
    calls = _fake_llm(monkeypatch, [payload, fixed])
    out = indigo_qcm.generate_batch(
        db, _comp(db), "3e",
        [{"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}],
        set())
    assert len(calls) == 2                       # une génération + UNE réparation
    assert [k for k, _v in out["34"]["variants"]] == ["base", "facile", "difficile"]
    # les raisons envoyées au modèle sont celles de la vérification Python
    problems = calls[1][1]["rejected"][0]["problems"]
    assert any("identiques" in p for p in problems)


def test_a_derivative_identical_to_the_base_is_dropped_never_the_base(db, monkeypatch):
    payload = _trio_payload()
    payload["exercises"][0]["facile"] = _single()      # copie conforme de la base
    _fake_llm(monkeypatch, [payload, {"exercises": []}])
    out = indigo_qcm.generate_batch(
        db, _comp(db), "3e",
        [{"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}],
        set())
    kinds = [k for k, _v in out["34"]["variants"]]
    assert "base" in kinds and "facile" not in kinds


def test_persist_trio_creates_three_rows_at_the_three_levels(db, monkeypatch):
    comp = _comp(db)
    _fake_llm(monkeypatch, [_trio_payload()])
    manual = {"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}
    out = indigo_qcm.generate_batch(db, comp, "3e", [manual], set())
    row = IndigoExercise(id="base-id", competency_id=comp.id, grade_level="3e",
                         source_number="34", badge_type="exercice")
    made = indigo._persist_qcm_trio(db, row, manual, out["34"])
    db.commit()
    assert made == 3
    rows = db.query(IndigoExercise).all()
    assert sorted(r.difficulty for r in rows) == [1, 2, 3]
    by_kind = {r.variant_kind: r for r in rows}
    assert by_kind["base"].derived_from_id is None
    assert by_kind["facile"].derived_from_id == "base-id"
    assert by_kind["difficile"].derived_from_id == "base-id"
    # les trois niveaux de la plateforme, sans exception
    assert {r.difficulty for r in rows} == set(exercise_gen.DIFFICULTY_LEVELS)


def test_no_usable_variant_falls_back_to_raw_ocr_never_to_nothing(db):
    """Un exercice du manuel ne disparaît JAMAIS : sans variante exploitable, il
    est conservé en repli OCR brut, à compléter par l'admin."""
    comp = _comp(db)
    manual = {"number": "34", "statement": "Énoncé OCR brut.", "correction": "",
              "has_figure": False}
    row = IndigoExercise(id="solo", competency_id=comp.id, grade_level="3e",
                         source_number="34", badge_type="exercice")
    assert indigo._persist_qcm_trio(db, row, manual, None) == 1
    db.commit()
    kept = db.query(IndigoExercise).one()
    assert kept.response_type == "short_text"
    assert kept.statement == "Énoncé OCR brut."


# ------------------------------------------------------------------ aiguillage

def test_qcm_mode_selects_deepseek_pro_and_the_qcm_pipeline(db):
    indigo_llm.set_provider(db, "anthropic")
    assert indigo_llm.mode(db) == indigo_llm.MODE_CLASSIC
    indigo_llm.set_provider(db, "qcm")
    assert indigo_llm.mode(db) == indigo_llm.MODE_QCM
    assert indigo_llm.model_for(db, "qcm") == settings.indigo_qcm_model
    assert indigo_llm.config_provider_key(db) == "deepseek-pro"
    assert "QCM" in indigo_llm.label(db)


def test_qcm_mode_never_produces_a_non_qcm_format(db, monkeypatch):
    """Le mode est un CONTRAT : même si le modèle renvoie une réponse courte,
    elle est refusée — la correction par CV en dépend."""
    comp = _comp(db)
    payload = _trio_payload()
    payload["exercises"][0]["base"] = {
        "response_type": "short_text", "statement": "Donne le PGCD de 1925 et 4125.",
        "correction": "275", "check": {"kind": "none"}}
    _fake_llm(monkeypatch, [payload, {"exercises": []}])
    out = indigo_qcm.generate_batch(
        db, comp, "3e",
        [{"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}],
        set())
    assert [k for k, _v in out["34"]["variants"]] == ["facile", "difficile"]


def test_deleting_a_base_takes_its_derivatives_with_it(db, monkeypatch):
    """Un dérivé pointant une base disparue laisserait un trio incohérent dans
    l'onglet ET dans la banque."""
    comp = _comp(db)
    _fake_llm(monkeypatch, [_trio_payload()])
    manual = {"number": "34", "statement": "PGCD ?", "correction": "", "has_figure": False}
    out = indigo_qcm.generate_batch(db, comp, "3e", [manual], set())
    row = IndigoExercise(id="base-id", competency_id=comp.id, grade_level="3e",
                         source_number="34", badge_type="exercice")
    indigo._persist_qcm_trio(db, row, manual, out["34"])
    db.commit()
    assert db.query(IndigoExercise).count() == 3

    indigo.delete_exercise(db, db.get(IndigoExercise, "base-id"))
    assert db.query(IndigoExercise).count() == 0


def test_regenerating_a_derivative_keeps_it_a_derivative(db, monkeypatch):
    """Régénérer un dérivé ne le promeut pas en base : sinon les trois lignes
    d'un même exercice finiraient toutes au même niveau."""
    comp = _comp(db)
    indigo_llm.set_provider(db, "qcm")
    ex = IndigoExercise(id="d1", competency_id=comp.id, grade_level="3e",
                        source_number="34", variant_kind="facile", difficulty=1,
                        raw_ocr_json={"statement": "PGCD ?", "correction": ""})
    db.add(ex); db.commit()
    _fake_llm(monkeypatch, [_trio_payload()])

    res = indigo.regenerate_exercises(db, ["d1"])
    assert res == {"regenerated": 1, "failed": 0}
    again = db.get(IndigoExercise, "d1")
    assert again.variant_kind == "facile"
    assert again.response_type in indigo_check.QCM_TYPES
    # c'est bien la variante FACILE qui a été reprise, pas la base
    assert "12 et 18" in again.statement


# ------------------------------------------- tolérance numérique du filet sympy

def test_float_noise_never_makes_a_right_answer_wrong():
    """« 8.4 - 3.1 » vaut 5.300000000000001 en binaire. Comparé à 5,3 par une
    égalité exacte, il déclarait FAUSSE une réponse juste — et renvoyait la
    famille en génération, où le modèle ne trouvait évidemment rien à corriger."""
    noisy = _single(choices=["$5{,}1$", "$5{,}3$", "$5{,}9$"], correct=[1],
                    check={"kind": "value", "expr": "8.4-3.1", "choice": 1})
    assert indigo_check.verify(noisy, has_figure=False) == []


def test_a_correctly_rounded_answer_is_accepted_but_a_wrong_one_is_not():
    """Une bonne réponse écrite « 294 183,33 € » pour 1765100/6 est ce qu'un
    élève écrit et ce qu'un professeur attend. Elle n'est acceptée qu'à la
    décimale près de ce qui est ÉCRIT : la tolérance ne couvre pas une erreur."""
    rounded = _single(choices=["$294\\,183{,}33$ €", "$294\\,000$ €", "$300\\,000$ €"],
                      correct=[0],
                      check={"kind": "value", "expr": "1765100/6", "choice": 0})
    assert indigo_check.verify(rounded, has_figure=False) == []
    # 172/11 = 15,63… : « 15,9 » n'en est pas l'arrondi, il reste refusé
    wrong = _single(choices=["$15{,}9$", "$16{,}4$", "$17{,}1$"], correct=[0],
                    check={"kind": "value", "expr": "172/11", "choice": 0})
    assert any("ne vaut pas" in p for p in indigo_check.verify(wrong, has_figure=False))


def test_the_rounding_tolerance_reads_the_written_precision_not_the_value():
    """« 3,00 € » est écrit à deux décimales même si sa VALEUR est l'entier 3.
    Lire la précision sur le rationnel aurait arrondi la bonne réponse 3,15 à
    l'unité — et déclaré le distracteur « 3,00 € » également juste."""
    price = _single(choices=["$3{,}00$ €", "$3{,}15$ €", "$3{,}50$ €"], correct=[1],
                    check={"kind": "value", "expr": "3.15", "choice": 1})
    assert indigo_check.verify(price, has_figure=False) == []

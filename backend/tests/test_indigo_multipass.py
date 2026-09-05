"""Indigo — mode « QCM multipass » : cinq passes, une famille indivisible.

Ni réseau, ni gros PDF. Ces tests fixent ce qui distingue réellement ce mode des
deux autres, et rien de plus :

  • le tri de la passe 1 SUPPRIME une source douteuse au lieu de la réparer ;
  • les trois formats corrigeables par CV passent — QCM à réponse unique, QCM à
    plusieurs réponses et grille à cocher — et aucun autre ;
  • les SOUS-QUESTIONS de la source sont exploitées : un exercice composite les
    porte, chacune avec son propre format et sa propre réponse ;
  • la FIGURE est tranchée à la passe 1 et recoupée par Python : besoin sans
    image = source écartée, image sans besoin = image détachée, et un énoncé qui
    ignore l'image qu'on lui colle est refusé ;
  • le solveur de la passe 3 est INDÉPENDANT — il ne voit ni la bonne réponse,
    ni le guide, ni le champ de vérification ;
  • AUCUN défaut ne renvoie un exercice en génération : la passe 5 le reçoit
    nommé, le répare sur place, et signale d'un badge ce qu'elle n'a pas su
    réparer. Seul un incident de transport fait tout rejouer ;
  • Python refuse gratuitement ce qu'il peut refuser (guide trop long, guide qui
    vend la mèche) et RENSEIGNE la retouche au lieu de l'arrêter ;
  • une retouche ne dégrade jamais : elle est mesurée variante par variante avec
    la règle même qu'on lui a donnée ;
  • une famille conservée part en BROUILLON, sans corrigé du professeur ;
  • les heures creuses ouvrent et ferment au bon moment, minuit compris.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (Competency, CompetencyFramework, GeneratedExercise,
                        IndigoExercise)
from app.services import (indigo, indigo_check, indigo_llm, indigo_multipass,
                          indigo_offpeak, prompts, providers)
from app.services import statement as statement_mod


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # dossier « livré dans l'image » vidé : sans ça, les tests de publication
    # liraient les exercices Indigo réellement versionnés dans le dépôt
    # (app/data/indigo/exercises.json) et compteraient les leurs par-dessus.
    image_dir = tmp_path / "image" / "indigo"
    image_dir.mkdir(parents=True)
    monkeypatch.setattr(indigo, "_IMAGE_PUB_DIR", image_dir)
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


MANUAL = {"number": "34", "statement": "Calculer le PGCD de 1925 et 4125.",
          "has_figure": False}

# Ce que rend la passe 1 : la source NETTOYÉE (OCR réparé, mobilier de page
# retiré, sous-questions conservées). Pas de plan, pas de blueprint.
SOURCE = "Calculer le PGCD de $1925$ et $4125$."

GUIDES = {
    "facile": "Décompose chaque nombre en facteurs premiers puis garde les facteurs communs.",
    "base": "Le PGCD divise les deux nombres. Cherche les facteurs premiers communs.",
}


def _single(statement, choices, correct, guide, expr=None):
    return {"response_type": "qcm_single", "statement": statement,
            "choices": choices, "correct": [correct], "guide": guide,
            "check": ({"kind": "value", "expr": expr, "choice": correct} if expr
                      else {"kind": "none"})}


def _multiple(statement, choices, correct, guide, exprs=None):
    return {"response_type": "qcm_multiple", "statement": statement,
            "choices": choices, "correct": sorted(correct), "guide": guide,
            "check": ({"kind": "set", "exprs": exprs} if exprs else {"kind": "none"})}


def _grid(statement, cols, rows, guide, exprs=None):
    return {"response_type": "checkbox_grid", "statement": statement,
            "cols": cols, "rows": rows, "guide": guide,
            "check": ({"kind": "rows", "exprs": exprs} if exprs else {"kind": "none"})}


def _trio():
    """Le duo par défaut : deux QCM à réponse unique."""
    return {
        "facile": _single("Calcule le PGCD de $12$ et $18$.",
                          ["$2$", "$3$", "$6$", "$9$"], 2, GUIDES["facile"],
                          "gcd(12, 18)"),
        "base": _single("Calcule le PGCD de $1925$ et $4125$.",
                        ["$55$", "$175$", "$275$", "$385$"], 2, GUIDES["base"],
                        "gcd(1925, 4125)"),
    }


def _mixed_trio():
    """Un duo qui exerce DEUX des trois formats corrigeables par CV — le
    troisième (choix unique) est déjà éprouvé par le duo par défaut, utilisé
    dans la quasi-totalité de ce module."""
    return {
        "facile": _multiple("Coche les nombres premiers de la liste.",
                            ["$17$", "$21$", "$23$", "$27$"], [0, 2], GUIDES["facile"],
                            ["isprime(17)", "isprime(21)", "isprime(23)", "isprime(27)"]),
        "base": _grid("Vrai ou faux ? Coche une case par ligne.",
                     ["Vrai", "Faux"],
                     [{"label": "$12$ est un multiple de $3$", "correct": 0},
                      {"label": "$14$ est un multiple de $4$", "correct": 1}],
                     GUIDES["base"],
                     ["Eq(Mod(12,3),0)", "Eq(Mod(14,4),0)"]),
    }


def _composite(statement, questions, guide):
    return {"response_type": "composite", "statement": statement,
            "questions": questions, "guide": guide}


def _part(leaf):
    """Une variante simple → une sous-question : même contenu, sans guide."""
    return {k: v for k, v in leaf.items() if k != "guide"}


def _composite_trio():
    """Le cas réel d'un manuel : un contexte commun et des sous-questions de
    formats différents, gradué sur deux niveaux."""
    contexte = ("Une salle rectangulaire mesure $12$ m sur $9$ m. On veut la "
                "carreler avec des dalles carrées identiques, sans en couper aucune.")
    dalles = _part(_multiple("Coche les côtés de dalle qui conviennent.",
                             ["$1$ m", "$2$ m", "$3$ m", "$4$ m"], [0, 2], "",
                             ["Eq(Mod(9,1),0)", "Eq(Mod(9,2),0)",
                              "Eq(Mod(9,3),0)", "Eq(Mod(9,4),0)"]))
    plus_grande = _part(_single("Quel est le côté, en mètres, de la plus grande dalle ?",
                                ["$1$", "$3$", "$4$", "$6$"], 1, "", "gcd(12, 9)"))
    return {
        "facile": _composite(
            "Un couloir mesure $84$ cm sur $126$ cm. On le carrelle avec des "
            "dalles carrées identiques, sans en couper aucune.",
            [_part(_grid("Vrai ou faux ? Coche une case par ligne.",
                         ["Vrai", "Faux"],
                         [{"label": "Une dalle de $7$ cm convient", "correct": 0},
                          {"label": "Une dalle de $9$ cm convient", "correct": 1}],
                         "", ["Eq(Mod(84,7)+Mod(126,7),0)",
                              "Eq(Mod(84,9)+Mod(126,9),0)"])),
             _part(_single("Combien de dalles de $42$ cm faut-il pour couvrir le couloir ?",
                           ["$2$", "$6$", "$12$", "$18$"], 1, "",
                           "(84*126)/(42*42)"))],
            GUIDES["facile"]),
        "base": _composite(contexte, [dalles, plus_grande], GUIDES["base"]),
    }


FIGURE_DESC = "un triangle $ABC$ rectangle en $A$, avec $AB = 6$ cm et $AC = 8$ cm."


def _figure_trio():
    """Deux exercices qui s'appuient sur LE MÊME dessin du manuel."""
    return {
        "facile": _single("Sur la figure ci-contre, quel côté est l'hypoténuse ?",
                          ["$[AB]$", "$[AC]$", "$[BC]$"], 2,
                          "L'hypoténuse est le côté opposé à l'angle droit."),
        "base": _single("Calcule $BC$ sur la figure ci-contre, en cm.",
                        ["$10$", "$12$", "$14$", "$48$"], 0,
                        "Le théorème de Pythagore relie les trois côtés d'un "
                        "triangle rectangle.", "sqrt(6**2 + 8**2)"),
    }


def _answer_part(part):
    if part.get("response_type") == "checkbox_grid":
        return [r["correct"] for r in part.get("rows") or []]
    return list(part.get("correct") or [])


def _answer(variant):
    """La réponse attendue sous la forme rendue par le solveur : une liste
    d'entiers PAR QUESTION, quel que soit le format (vide pour une variante
    volontairement mal formée, que Python refusera avant d'appeler le solveur)."""
    if variant.get("response_type") == "composite":
        return [_answer_part(q) for q in variant.get("questions") or []]
    return [_answer_part(variant)]


def _solutions(trio=None, override=None):
    trio = trio or _trio()
    override = override or {}
    return {"solutions": [
        {"niveau": indigo_multipass.VARIANT_LABEL[k],
         "reponses": override.get(k, _answer(trio[k])), "valeur": "", "justification": ""}
        for k in indigo_multipass.VARIANTS]}


def _script(monkeypatch, replies):
    """Remplace l'appel LLM par un scénario {stage -> file de réponses}.

    Rend la liste des appels réellement passés — c'est elle qui permet de
    vérifier l'ordre des passes, leur effort, et surtout ce que chacune reçoit."""
    calls = []

    def fake(db, stage, system, payload, correlation_id):
        calls.append({"stage": stage, "payload": payload, "cid": correlation_id})
        queue = replies.get(stage)
        if queue is None:
            return None
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(indigo_llm, "call", fake)
    return calls


def _happy(trio=None):
    trio = trio or _trio()
    return {"mp_filter": [{"verdict": "keep", "enonce": SOURCE,
                           "besoin_figure": False}],
            "mp_generate": [{"exercices": trio}],
            "mp_solve": [_solutions(trio)],
            "mp_repair": [{"exercices": trio, "signalements": []}]}


# ---------------------------------------------------------------- les 5 passes

def test_the_six_passes_run_in_order(db, monkeypatch):
    """La MISE EN PAGE tombe entre la résolution et la retouche : elle ne
    travaille que sur un trio déjà résolu, et la retouche voit le texte
    réellement imprimé. La passe CONTEXTE (§ `_pass_context`) tombe entre le
    filtre et la génération, jamais ailleurs."""
    calls = _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    assert [c["stage"] for c in calls] == [
        "mp_filter", "mp_context", "mp_generate", "mp_solve", "mp_layout",
        "mp_repair"]


def test_the_ready_family_is_a_duo_at_the_two_levels(db, monkeypatch):
    """Le dérivé Difficile a été retiré pour de bon le 05/09 (§ module) : une
    famille conservée n'a plus jamais que deux niveaux, jamais un troisième."""
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert [k for k, _v in family.variants] == ["base", "facile"]
    for _kind, valid in family.variants:
        assert valid["response_type"] == "qcm_single"
        assert valid["grading"]["bareme_points"] == 1.0     # barème CODÉ
    assert {indigo_multipass.VARIANT_LEVEL[k] for k, _v in family.variants} == {1, 2}


def test_each_variant_carries_its_own_guide(db, monkeypatch):
    """Différence assumée avec « QCM only », qui n'écrit qu'un guide pour ses
    variantes : ici chaque exercice a le sien, adapté à SA difficulté."""
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    guides = {k: v["correction"] for k, v in family.variants}
    assert guides == GUIDES
    assert len(set(guides.values())) == 2


# ------------------------------------------------------- les trois formats CV

def test_the_other_two_cv_correctable_formats_go_through(db, monkeypatch):
    """Exactement ceux de « QCM only » : ce qui les réunit, c'est d'être corrigés
    par vision par ordinateur — aucune copie chez Mathpix, aucune note par LLM.
    Le choix unique (troisième format) est déjà éprouvé par le duo par défaut
    utilisé dans la quasi-totalité de ce module ; celui-ci ajoute le QCM à
    PLUSIEURS réponses et la grille, ensemble dans la même famille."""
    assert set(indigo_multipass.ALLOWED_TYPES) == set(indigo_check.QCM_TYPES) | {"composite"}
    _script(monkeypatch, _happy(_mixed_trio()))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    by_kind = dict(family.variants)
    assert by_kind["facile"]["response_type"] == "qcm_multiple"
    assert by_kind["base"]["response_type"] == "checkbox_grid"
    # barème CODÉ, et donc différent selon le format (0,5 pt la case d'un QCM
    # multiple, 0,5 pt la ligne d'une grille)
    assert by_kind["facile"]["grading"]["bareme_points"] == 2.0
    assert by_kind["base"]["grading"]["bareme_points"] == 1.0


def test_a_format_that_cannot_be_corrected_by_cv_is_refused(db, monkeypatch):
    """Le mode est un CONTRAT : une réponse courte, même juste, casserait la
    correction gratuite. Et elle est refusée AVEC SON NOM, pas transformée en
    QCM au petit bonheur (la réponse attendue ne suivrait pas)."""
    trio = _trio()
    trio["base"] = {"response_type": "short_text",
                    "statement": "Donne le PGCD de $1925$ et $4125$.",
                    "guide": GUIDES["base"], "check": {"kind": "none"}}
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    # la réponse écrite ne franchit JAMAIS la porte : elle casserait la
    # correction gratuite. Les deux autres niveaux, eux, sont bons à relire.
    _flagged(family, "short_text", dropped=("base",))
    assert _handed_to_the_repair(calls, "short_text")


@pytest.mark.parametrize("expr, why", [
    ("Eq(3,3)", "une VÉRITÉ écrite sous un `value`, qui attend un nombre"),
    ("count_class_45_50", "un NOM inventé, que rien ne peut lier"),
])
def test_a_meaningless_check_is_neutralised_not_blamed_on_the_answer(expr, why):
    """Ces deux-là accusaient la BONNE réponse (« $f(3) = -5$ ne vaut pas
    Eq(3,3) », « "12" ne vaut pas count_class_45_50 ») et renvoyaient toute la
    famille en génération pour un champ que l'élève ne voit jamais. C'était le
    premier motif de refus des pages 86-87 : 23 familles sur 33."""
    part = {"response_type": "qcm_single", "statement": "Quelle est l'image de $3$ ?",
            "choices": ["$-5$", "$2$", "$0$"], "correct": [0],
            "check": {"kind": "value", "expr": expr, "choice": 0}}
    assert indigo_multipass._normalize_leaf(part)["check"] == {"kind": "none"}, why


def test_a_single_choice_question_may_be_verified_by_truth_values(db, monkeypatch):
    """« Lequel de ces nombres divise $133$ ? » ne se vérifie pas en comparant
    la bonne proposition à un calcul : c'est une VÉRITÉ par proposition. Le
    `check` de type `set` vaut donc aussi en choix unique — sans ça, le modèle
    écrivait `expr: Mod(133, 7)` sous un `value`, sympy comparait « $7$ » à
    $0$, et la famille entière repartait en génération (extraction A1.2)."""
    trio = _trio()
    trio["base"] = _multiple("Lequel de ces nombres divise $133$ ?",
                             ["$3$", "$7$", "$9$", "$11$"], [1], GUIDES["base"],
                             ["Eq(Mod(133,3),0)", "Eq(Mod(133,7),0)",
                              "Eq(Mod(133,9),0)", "Eq(Mod(133,11),0)"])
    trio["base"]["response_type"] = "qcm_single"
    assert indigo_multipass._local_problems(trio, has_figure=False) == []
    _script(monkeypatch, _happy(trio))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY


def test_a_grid_whose_column_is_wrong_is_caught_by_sympy(db, monkeypatch):
    """Le filet déterministe couvre les grilles comme les QCM : la colonne
    déclarée juste est recalculée ligne par ligne."""
    trio = _mixed_trio()
    trio["base"]["rows"][0]["correct"] = 1        # « 12 multiple de 3 » = Faux ?
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    # La variante n'est pas détruite : elle part en brouillon avec la réserve
    # EXACTE, ligne nommée. C'est ce que le professeur peut corriger d'un clic ;
    # un rejet muet ne lui aurait rien laissé à corriger.
    _flagged(family, "Base", "colonne correcte devrait être 0")


def test_a_grid_guide_is_never_flagged_for_saying_vrai(db, monkeypatch):
    """Contre-épreuve : la « bonne réponse » d'une grille est un libellé de
    colonne (« Vrai »), qu'un guide peut légitimement employer. Chercher dedans
    ne trouverait que des faux positifs."""
    trio = _mixed_trio()
    trio["base"]["guide"] = "Une affirmation est vraie si elle tient pour tous les cas."
    _script(monkeypatch, _happy(trio))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY


def test_a_multiple_qcm_guide_that_names_one_right_answer_is_refused(db, monkeypatch):
    """Un QCM multiple a plusieurs bonnes réponses : en citer UNE suffit à
    vendre la mèche."""
    trio = _mixed_trio()
    trio["facile"]["guide"] = "Commence par $23$, puis teste les autres par divisions."
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "bonne réponse")


def test_a_grid_disagreement_names_the_row_and_the_column(db, monkeypatch):
    """Ce que lit l'audit doit être lisible : « telle affirmation → telle
    colonne », jamais une liste d'indices nus."""
    trio = _mixed_trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [[1, 1]]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    disagreement = " ".join(repair["payload"]["desaccords_detectes"])
    assert "multiple de $3$" in disagreement and "Vrai" in disagreement and "Faux" in disagreement


def test_a_multiple_qcm_disagreement_compares_the_whole_set(db, monkeypatch):
    """Cocher « 17 » quand il fallait cocher « 17 et 23 » est un désaccord :
    c'est l'ENSEMBLE des cases qui doit coïncider, pas la première."""
    trio = _mixed_trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"facile": [[0]]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("Facile" in d and "$23$" in d
               for d in repair["payload"]["desaccords_detectes"])


def test_an_unreadable_solver_answer_is_a_disagreement_not_an_agreement(db, monkeypatch):
    """Une seule case illisible rend TOUTE la réponse inexploitable : la
    comparer partiellement inventerait un accord qui n'existe pas."""
    trio = _mixed_trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [[0, "je ne sais pas"]]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("n'a pas su trancher" in d for d in repair["payload"]["desaccords_detectes"])


# --------------------------------------------------------------- la figure

MANUAL_FIG = {**MANUAL, "has_figure": True}


def _flagged(family, *needles, dropped=()):
    """Nouveau contrat : un défaut que le modèle n'a pas su réparer ne détruit
    plus la famille. Il est NOMMÉ dans les réserves, la variante fautive est
    écartée du trio, et ce qui tient debout part en brouillon sous les yeux du
    professeur. `dropped` liste les niveaux qui ne doivent PAS avoir survécu."""
    assert family.state == indigo_multipass.NEEDS_REVIEW, family.state
    joined = " ".join(family.notes)
    for needle in needles:
        assert needle in joined, f"{needle!r} absent de {joined!r}"
    kept = {kind for kind, _ in family.variants}
    for kind in dropped:
        assert kind not in kept, f"la variante « {kind} » fautive a été conservée"
    return family


def _kept_with_figure(source=None):
    """Passe 1 : source gardée, et qui a besoin du dessin."""
    return {"verdict": "keep", "enonce": source or SOURCE,
            "besoin_figure": True, "figure": FIGURE_DESC}


def test_a_source_that_needs_a_drawing_the_ocr_never_isolated_is_kept_and_flagged(db, monkeypatch):
    """Une image manquante N'EST PAS un motif de rejet : le professeur l'ajoute
    au brouillon en trente secondes. Jeter la source revenait à lui retirer la
    seule chose qu'il pouvait réparer lui-même — et sur les pages 86-87, à
    perdre neuf exercices sur trente-trois pour le mot « tableau »."""
    replies = _happy()
    replies["mp_filter"] = [_kept_with_figure()]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.kept and len(family.variants) == 2
    # Les portes sont passées : l'état reste READY. Ce qui manque est une IMAGE,
    # et cela se dit en réserve, sur la carte que le professeur va relire.
    assert family.state == indigo_multipass.READY
    assert any("aucun crop n'a isolé" in n for n in family.notes)
    # les six passes ont bien tourné : la réserve ne coupe pas la génération
    assert [c["stage"] for c in calls] == ["mp_filter", "mp_context", "mp_generate",
                                           "mp_solve", "mp_layout", "mp_repair"]


def test_a_drawing_the_exercise_does_not_need_is_detached(db, monkeypatch):
    """Une figure isolée par l'OCR mais dont l'exercice ne se sert pas
    s'imprimerait en décor à côté d'un exercice RÉÉCRIT. On la détache, et la
    génération travaille sans elle."""
    calls = _script(monkeypatch, _happy())          # besoin_figure: False
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    assert family.state == indigo_multipass.READY
    assert family.figure is False
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["has_figure"] is False
    assert "figure" not in gen["payload"]["source"]


def test_a_needed_and_available_drawing_reaches_every_pass_that_uses_it(db, monkeypatch):
    """La description de la figure est faite UNE fois, à la passe 1, et
    redistribuée : la génération écrit avec, le solveur résout avec, l'audit
    juge la cohérence avec."""
    replies = _happy(_figure_trio())
    replies["mp_filter"] = [_kept_with_figure()]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    assert family.state == indigo_multipass.READY
    assert family.figure is True
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["has_figure"] is True
    assert gen["payload"]["source"]["figure"] == FIGURE_DESC
    assert next(c for c in calls if c["stage"] == "mp_solve")["payload"]["figure"] == FIGURE_DESC
    assert next(c for c in calls if c["stage"] == "mp_repair")["payload"]["figure"] == FIGURE_DESC


def test_the_cleaned_statement_decides_the_need_even_against_the_model(db, monkeypatch):
    """Un énoncé qui parle de « la figure ci-contre » a besoin d'un dessin, quoi
    qu'en dise la case cochée à côté. Le recoupement est en Python : il ne
    dépend pas de la bonne foi du modèle. Il POSE UNE RÉSERVE, il ne jette
    plus la source."""
    replies = _happy()
    replies["mp_filter"] = [{"verdict": "keep", "besoin_figure": False,
                             "enonce": "Calcule $BC$ sur la figure ci-contre."}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.kept
    assert any("aucun crop n'a isolé" in n for n in family.notes)


def test_a_drawing_nobody_described_is_generated_but_flagged(db, monkeypatch):
    """Aucune des passes suivantes ne VOIT l'image : sans la description de la
    passe 1, elles écrivent à l'aveugle à côté du dessin. C'est une raison de
    PRÉVENIR le relecteur, pas de détruire l'exercice — lui, il voit la page."""
    replies = _happy()
    replies["mp_filter"] = [{"verdict": "keep", "enonce": SOURCE,
                             "besoin_figure": True}]        # sans « figure »
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    assert family.kept
    assert any("non décrite" in n for n in family.notes)
    assert [c["stage"] for c in calls].count("mp_generate") == 1


def test_an_exercise_that_ignores_the_drawing_beside_it_is_flagged(db, monkeypatch):
    """Une figure imprimée que l'énoncé n'utilise jamais laisse l'élève chercher
    à quoi elle sert. On le SIGNALE au relecteur, qui retirera l'image ou
    appuiera une question dessus — deux gestes d'un clic, contre une famille
    entière régénérée pour rien."""
    trio = _figure_trio()
    trio["base"] = _trio()["base"]                  # ne parle plus du dessin
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    replies["mp_generate"] = [{"exercices": trio}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    assert family.kept
    assert any("ne s'en sert jamais" in n for n in family.notes)
    # la réserve n'interrompt rien : l'audit et la relecture ont bien eu lieu
    assert [c["stage"] for c in calls].count("mp_solve") == 1


def test_a_vision_figure_must_be_used_explicitly_before_the_audit(db, monkeypatch):
    """Une image attachée ne doit jamais devenir un simple décor en silence."""
    trio = _figure_trio()
    trio["base"] = _trio()["base"]
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    calls = _script(monkeypatch, replies)
    manual = {**MANUAL_FIG, "vision_extracted": True,
              "figure_description": FIGURE_DESC}
    family = indigo_multipass.run_family(db, _comp(db), "3e", manual, set())
    assert family.kept
    assert any("ne s'en sert jamais" in n for n in family.notes)
    assert any(c["stage"] == "mp_repair" for c in calls)


def test_a_solver_that_cannot_see_the_drawing_is_not_a_faulty_statement(db, monkeypatch):
    """Sans cette distinction, tous les exercices de géométrie seraient rejetés
    comme « ambigus ». On le dit pour ce que c'est : une vérification perdue,
    dont l'audit doit tenir compte."""
    trio = _figure_trio()
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    replies["mp_solve"] = [_solutions(trio, {"base": None})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    said = " ".join(repair["payload"]["desaccords_detectes"])
    assert "sans VOIR la figure" in said and "AUCUNE" in said
    # contre-épreuve : sans figure, le même null reste un énoncé douteux
    replies = _happy()
    replies["mp_solve"] = [_solutions(override={"base": None})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("ambigu" in d for d in repair["payload"]["desaccords_detectes"])


def test_the_figure_description_never_carries_an_answer_to_the_solver(db, monkeypatch):
    """La description vient de la passe 1, donc de la SOURCE : elle ne dérive
    d'aucune réponse écrite par la passe 2. L'indépendance du solveur tient."""
    trio = _figure_trio()
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL_FIG, set())
    solve = next(c for c in calls if c["stage"] == "mp_solve")
    blob = str(solve["payload"])
    assert "correct" not in blob and "guide" not in blob and "check" not in blob


# ------------------------------------------- les sous-questions (composite)

def test_a_source_with_sub_questions_stays_one_exercise_with_several_answers(db, monkeypatch):
    """Un exercice de manuel se déroule en a., b., c. — l'aplatir en une seule
    question jetterait l'essentiel de la source. Le composite les garde : UNE
    carte, un contexte commun, et une réponse par sous-question."""
    _script(monkeypatch, _happy(_composite_trio()))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    base = dict(family.variants)["base"]
    assert base["response_type"] == "composite"
    parts = base["expected"]["parts"]
    assert [p["response_type"] for p in parts] == ["qcm_multiple", "qcm_single"]
    # le contexte porte les données communes et ne se répond pas
    assert "dalles carrées" in base["statement"]
    assert all(p["statement"] for p in parts)


def test_the_bareme_of_a_composite_is_the_sum_of_its_sub_questions(db, monkeypatch):
    """Barème CODÉ jusqu'au bout : 0,5 pt la case d'un QCM multiple (4 cases = 2)
    plus 1 pt le choix unique. Un barème annoncé pour l'exercice entier ne serait
    jamais lu — le composite se déplie en un CopyItem par sous-question."""
    _script(monkeypatch, _happy(_composite_trio()))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    by_kind = dict(family.variants)
    assert [p["grading"]["bareme_points"]
            for p in by_kind["base"]["grading"]["parts"]] == [2.0, 1.0]
    assert by_kind["base"]["grading"]["bareme_points"] == 3.0
    # grille (2 lignes × 0,5) + choix unique (1)
    assert by_kind["facile"]["grading"]["bareme_points"] == 2.0


def test_each_sub_question_keeps_its_own_response_format(db, monkeypatch):
    """« Plusieurs formats dans le même exercice, un seul par sous-question » :
    c'est tout l'intérêt de l'assemblage."""
    _script(monkeypatch, _happy(_composite_trio()))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    facile = dict(family.variants)["facile"]
    assert [p["response_type"] for p in facile["expected"]["parts"]] == [
        "checkbox_grid", "qcm_single"]


def test_a_sub_question_in_a_format_the_cv_cannot_correct_is_refused(db, monkeypatch):
    """Le contrat du mode vaut jusque dans les sous-questions : une réponse
    écrite y casserait la correction gratuite tout autant."""
    trio = _composite_trio()
    trio["base"]["questions"][0] = {"response_type": "short_text",
                                    "statement": "Donne le côté d'une dalle.",
                                    "check": {"kind": "none"}}
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "sous-question a.", "short_text", dropped=("base",))
    assert _handed_to_the_repair(calls, "sous-question a.", "short_text")


def test_a_wrong_sub_question_is_caught_by_sympy_and_named(db, monkeypatch):
    """Le filet déterministe descend dans les sous-questions, et dit LAQUELLE :
    « corrige la famille » sans dire où est une consigne inapplicable."""
    trio = _composite_trio()
    trio["base"]["questions"][1]["correct"] = [2]      # ni le check, ni le calcul
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "sous-question b.")


def test_sub_questions_make_a_composite_even_under_a_leaf_label(db, monkeypatch):
    """Cas réel, n°13 des pages 67-68 : Flash annonce « qcm_single » et livre
    DEUX sous-questions. Lu comme une feuille, l'exercice perdait ses questions
    ET ses cases (« 0 proposition(s) »), et cette contradiction — invisible dans
    la sortie du modèle — renvoyait la famille en génération quatre fois."""
    trio = _trio()
    trio["base"] = {"response_type": "qcm_single", "guide": GUIDES["base"],
                    "statement": "On donne $h(-2)=-1$ et $h(3)=-2$.",
                    "questions": [
                        _part(_single("Quelle est l'image de $-2$ ?",
                                      ["$-1$", "$3$", "$-2$"], 0, "")),
                        _part(_single("Quel est un antécédent de $-2$ ?",
                                      ["$-1$", "$3$", "$-2$"], 1, ""))]}
    _script(monkeypatch, _happy(trio))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    base = dict(family.variants)["base"]
    assert base["response_type"] == "composite"
    assert len(base["expected"]["parts"]) == 2


def test_a_one_question_composite_is_flattened_without_rejecting_the_family(db, monkeypatch):
    """Le mauvais conteneur ne doit pas faire perdre une question exploitable."""
    trio = _composite_trio()
    trio["base"]["questions"] = trio["base"]["questions"][:1]
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    base = dict(family.variants)["base"]
    assert base["response_type"] == "qcm_multiple"
    assert "Une salle rectangulaire" in base["statement"]


def test_a_sub_question_may_lean_on_the_previous_one(db, monkeypatch):
    """« La question précédente » est SUR LA CARTE quand c'est une sous-question
    du même exercice — c'est même la façon normale d'enchaîner. Le même renvoi
    dans un exercice autonome reste un refus : rien ne le précède."""
    trio = _composite_trio()
    trio["base"]["questions"][1]["statement"] = (
        "En utilisant la question précédente, donne le côté, en mètres, de la "
        "plus grande dalle.")
    _script(monkeypatch, _happy(trio))
    assert indigo_multipass.run_family(
        db, _comp(db), "3e", MANUAL, set()).state == indigo_multipass.READY

    seul = _trio()
    seul["base"]["statement"] = "Calcule le PGCD des deux nombres donnés ci-dessus."
    _first_generation_only(monkeypatch, seul)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "renvoi hors de la feuille")


def test_the_single_guide_of_a_composite_covers_every_sub_question(db, monkeypatch):
    """Un composite n'a qu'UN guide : la réponse de n'importe laquelle de ses
    sous-questions y est donc interdite."""
    trio = _composite_trio()
    trio["base"]["guide"] = "Commence par $3$, puis compte les dalles."
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "bonne réponse")


def test_a_composite_disagreement_names_the_sub_question(db, monkeypatch):
    """Sur un composite, savoir QUELLE sous-question fait désaccord est
    exactement ce que la passe 4 a besoin de trancher."""
    trio = _composite_trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [[0, 2], [0]]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("Base" in d and "sous-question b." in d
               for d in repair["payload"]["desaccords_detectes"])


def test_a_flat_answer_to_a_composite_is_ambiguous_not_an_agreement(db, monkeypatch):
    """Une liste plate sur plusieurs questions ne dit pas où s'arrête la
    première : la répartir au jugé inventerait un accord. On refuse."""
    trio = _composite_trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [0, 1]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("Base" in d and "n'a pas su trancher" in d
               for d in repair["payload"]["desaccords_detectes"])


def test_a_flat_answer_to_a_single_question_stays_readable(db, monkeypatch):
    """Contre-épreuve : sur UNE question, la forme plate est sans ambiguïté —
    la refuser rejetterait des solveurs parfaitement clairs."""
    trio = _trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [2]})]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert repair["payload"]["desaccords_detectes"] == []


def test_two_composites_that_differ_only_by_their_sub_questions_are_two_exercises(db):
    """L'empreinte d'une variante couvre le contexte ET les sous-questions :
    comparer le seul contexte ferait passer deux exercices pour un seul."""
    trio = _composite_trio()
    trio["facile"]["statement"] = trio["base"]["statement"]
    assert indigo_multipass._local_problems(trio, has_figure=False) == []
    trio["facile"]["questions"] = trio["base"]["questions"]
    assert any("CLONES" in p
               for p in indigo_multipass._local_problems(trio, has_figure=False))


def test_the_generation_prompt_is_short_and_has_no_cargo_cult_examples(db):
    """Le contrat suffit : des exemples longs poussaient Flash à les imiter."""
    text = prompts.prompt_path("indigo", "multipass_generate").read_text(encoding="utf-8")
    lines, examples, buffer = text.splitlines(), [], None
    for line in lines:
        if buffer is None and line.startswith('{"response_type"'):
            buffer = [line]
        elif buffer is not None:
            buffer.append(line)
        if buffer is None:
            continue
        try:
            examples.append(json.loads("\n".join(buffer)))
            buffer = None
        except json.JSONDecodeError:
            continue
    assert examples == []
    # Relevé le 04/09 (350 -> 500) pour la réécriture Facile/Base ; la porte de
    # FAISABILITÉ elle-même a depuis DÉMÉNAGÉ dans la passe contexte
    # (§ `_pass_context`), ce qui a fait revenir ce fichier à 419 mots — du
    # CONTRAT, pas des exemples : la garde ci-dessus reste ce qui compte.
    assert len(text.split()) < 500
    assert "chaque tâche" in text.lower()
    assert "bonne réponse" in text.lower()


# ------------------------------------------------------------ passe 1 : le tri

def test_a_doubtful_source_is_rejected_never_repaired(db, monkeypatch):
    calls = _script(monkeypatch, {
        "mp_filter": [{"verdict": "reject", "reason": "figure absente du texte OCR"}]})
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.REJECTED_SOURCE
    assert "figure absente" in family.reason
    assert family.variants == []
    assert [c["stage"] for c in calls] == ["mp_filter"]   # rien n'a été généré


def test_an_unreadable_filter_output_rejects_the_source(db, monkeypatch):
    """Sans énoncé nettoyé il n'y a rien à générer, et se rabattre sur l'OCR
    brut reviendrait exactement à ce que la passe est là pour empêcher."""
    _script(monkeypatch, {"mp_filter": [{"verdict": "keep", "enonce": None}]})
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.REJECTED_SOURCE


def test_the_generation_receives_the_cleaned_source_not_the_raw_ocr(db, monkeypatch):
    """La passe 1 nettoie, la passe 2 travaille sur le NETTOYÉ. Lui montrer
    l'OCR brut en plus rouvrirait la porte à tout ce que le nettoyage enlève —
    mobilier de page, coupures, symboles perdus."""
    replies = _happy()
    replies["mp_filter"] = [{"verdict": "keep",
                             "enonce": "Calcule le PGCD de $1925$ et $4125$.\n"
                                       "a. Décompose chaque nombre en facteurs premiers."}]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["statement"] == (
        "Calcule le PGCD de $1925$ et $4125$.\n"
        "a. Décompose chaque nombre en facteurs premiers.")
    assert MANUAL["statement"] not in str(gen["payload"])
    # et la passe 1, elle, a bien vu l'OCR brut : c'est sa matière première
    filt = next(c for c in calls if c["stage"] == "mp_filter")
    assert filt["payload"]["source"]["statement"] == MANUAL["statement"]


def test_an_indexed_teacher_correction_reaches_the_context_pass(db, monkeypatch):
    """§ `_correction_candidates` : le corrigé du manuel PROF, quand l'index en
    est déjà construit (§ services.indigo_index), doit atteindre le payload de
    la passe CONTEXTE — la SEULE à voir plusieurs candidats et à trancher
    lequel, s'il y en a un, parle vraiment de cet exercice. La compétence
    utilisée pour le repérage est celle RÉELLEMENT résolue par la passe 1, pas
    une devinette d'avant."""
    from app.services import indigo_index

    indigo_index._save("3e", "prof", {
        "version": indigo_index.INDEX_VERSION, "grade_level": "3e", "which": "prof",
        "sha256": "", "page_count": 1, "pages": {"0": {
            "source_page": 0, "dims": {"width": 1000, "height": 1000},
            "chapter": "Nombres entiers", "numbers": [34],
            "blocks": [{"type": "text", "content": "34 PGCD(1925 ; 4125) = 275.",
                       "top_left_x": 0, "top_left_y": 0,
                       "bottom_right_x": 200, "bottom_right_y": 20}]}}})
    replies = _happy()
    replies["mp_context"] = [{"mode": "source_corrige", "corrige": "PGCD = 275."}]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    ctx = next(c for c in calls if c["stage"] == "mp_context")
    assert ctx["payload"]["source"]["corriges_candidats"] == [
        "34 PGCD(1925 ; 4125) = 275."]
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["corrige_prof"] == "PGCD = 275."


def test_a_validated_teacher_correction_reaches_only_the_generation_payload(db, monkeypatch):
    """Depuis le 04/09 soir, la passe CONTEXTE (§ `_pass_context`) est la SEULE
    à juger un corrigé du professeur — son verdict `source_corrige` devient
    `source.corrige_prof` dans le payload de `mp_generate` SEULEMENT. Aucune
    autre passe (filtre, solveur, mise en page, retouche) ne doit le voir : le
    solveur en particulier perdrait son indépendance."""
    replies = _happy()
    replies["mp_context"] = [{"mode": "source_corrige",
                              "corrige": "PGCD(1925 ; 4125) = 275."}]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["corrige_prof"] == "PGCD(1925 ; 4125) = 275."
    for call in calls:
        if call["stage"] == "mp_generate":
            continue
        assert "275" not in str(call["payload"]).replace("$275$", "")


def test_an_empty_teacher_manual_omits_the_field_entirely(db, monkeypatch):
    """Pas de corrigé retrouvé, ni jugé nécessaire (cas courant : la passe
    contexte reste en mode `source` par défaut) : `corrige_prof` n'apparaît
    même pas, plutôt qu'une chaîne vide qui laisserait croire à une absence
    vérifiée."""
    calls = _script(monkeypatch, _happy())
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert "corrige_prof" not in gen["payload"]["source"]


def test_an_infeasible_source_is_rejected_without_burning_a_retry(db, monkeypatch):
    """La passe CONTEXTE juge qu'elle ne peut rien écrire de juste, même en
    s'en inspirant pour inventer : la source est REJETÉE directement, AVANT
    tout appel de génération — il n'y a rien à réparer, et aucune tentative
    de la boucle de transport n'est consommée."""
    replies = _happy()
    replies["mp_context"] = [{"mode": "reject",
                              "raison": "la figure donne les trois longueurs, "
                                        "l'énoncé n'en redonne aucune, et rien "
                                        "d'autre ne situe le problème"}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.REJECTED_SOURCE
    assert "longueurs" in family.reason
    assert [c["stage"] for c in calls] == ["mp_filter", "mp_context"]
    assert family.attempts == 0


def test_a_source_too_degraded_for_the_corrige_but_pedagogically_clear_is_invented(db, monkeypatch):
    """Ni l'énoncé ni un corrigé ne suffisent, mais le contexte pédagogique,
    lui, suffit à inventer : `invent_context` doit atteindre `mp_generate`
    (jamais `corrige_prof`, les deux verdicts sont exclusifs), et la famille
    n'est PAS rejetée — elle se génère normalement."""
    replies = _happy()
    replies["mp_context"] = [{"mode": "invent",
                              "contexte_pedagogique": "PGCD de deux entiers à "
                                                      "trois chiffres, méthode "
                                                      "des différences ou "
                                                      "Euclide."}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert gen["payload"]["source"]["contexte_invention"] == (
        "PGCD de deux entiers à trois chiffres, méthode des différences ou Euclide.")
    assert "corrige_prof" not in gen["payload"]["source"]


# ------------------------------------------------- passe 3 : solveur indépendant

def test_the_solver_sees_neither_answer_nor_guide_nor_check(db, monkeypatch):
    """Le cœur du mode. Un modèle à qui l'on montre la réponse la confirme
    toujours : si ce test tombe, la passe 5 « valide » sans rien vérifier.

    Éprouvé sur les TROIS formats : une grille aussi doit partir sans la colonne
    juste de chacune de ses lignes."""
    calls = _script(monkeypatch, _happy(_mixed_trio()))
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    solve = next(c for c in calls if c["stage"] == "mp_solve")
    views = {v["niveau"]: v for v in solve["payload"]["qcm"]}
    assert set(views["Facile"]) == {"niveau", "questions"}
    assert set(views["Facile"]["questions"][0]) == {"format", "enonce", "propositions"}
    assert set(views["Base"]["questions"][0]) == {"format", "enonce",
                                                   "colonnes", "lignes"}
    blob = str(solve["payload"])
    assert "correct" not in blob and "guide" not in blob and "check" not in blob


def test_the_solver_sees_a_composite_question_by_question(db, monkeypatch):
    """Un composite lui arrive comme son contexte et ses sous-questions — sans
    aucune réponse, exactement comme un exercice simple."""
    calls = _script(monkeypatch, _happy(_composite_trio()))
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    solve = next(c for c in calls if c["stage"] == "mp_solve")
    view = next(v for v in solve["payload"]["qcm"] if v["niveau"] == "Base")
    assert set(view) == {"niveau", "contexte", "questions"}
    assert "dalles carrées" in view["contexte"]
    assert [q["format"] for q in view["questions"]] == [
        "plusieurs cases peuvent être cochées", "une seule case à cocher"]
    blob = str(solve["payload"])
    assert "correct" not in blob and "guide" not in blob and "check" not in blob


# --------------------------------------------------- passe 4 : mise en page

# Le cas réel qui a motivé la passe (B4.1 n°14) : un titre qui n'apprend rien,
# une consigne d'aiguillage, puis quatre fois la même question à deux cases.
TF_CONTEXT = ("**Vrai ou faux ?**\n"
              "On donne les tailles d'élèves d'une classe de 5ᵉ dans le tableau "
              "ci-dessous.\n"
              "| Taille (en cm) | de 140 à 150 | de 150 à 160 | de 160 à 170 |\n"
              "|---|---|---|---|\n"
              "| Effectif | 10 | 14 | 3 |\n"
              "Peut-on en déduire les affirmations suivantes ?")
TF_CLEAN = ("On donne les tailles d'élèves d'une classe de 5ᵉ dans le tableau "
            "ci-dessous.\n"
            "| Taille (en cm) | de 140 à 150 | de 150 à 160 | de 160 à 170 |\n"
            "|---|---|---|---|\n"
            "| Effectif | 10 | 14 | 3 |")
TF_CLAIMS = ["17 élèves mesurent au moins 150 cm.",
             "10 élèves mesurent moins de 150 cm.",
             "3 élèves mesurent 165 cm.",
             "L'amplitude de chacune des classes est 10 cm."]
TF_COLUMNS = [1, 0, 1, 0]                 # 0 = « Vrai », 1 = « Faux »
TF_GUIDE = ("Lis chaque effectif dans le tableau, puis additionne les classes "
            "concernées avant de trancher.")


# Le cas réel du 03/09 (B3.1-14) : quatre situations, chacune un choix multiple
# des MÊMES trois propositions a/b/c — jamais mutualisé jusqu'ici, faute d'un
# geste 3b (§ prompts/indigo/multipass_layout.txt). Trois situations seulement
# (neuf lignes) pour rester sous le plafond de dix lignes d'une grille.
QM_CHOICES = ["$12$ est pair", "$12$ est multiple de $3$", "$12$ est premier"]
QM_CORRECT = [[0, 1], [1], [0]]


def _qm_composite(guide=TF_GUIDE):
    return {"response_type": "composite", "statement": TF_CONTEXT, "guide": guide,
            "questions": [
                _multiple(f"Situation {i + 1} : coche les affirmations vraies.",
                         QM_CHOICES, correct, "")
                for i, correct in enumerate(QM_CORRECT)]}


def _qm_trio():
    trio = _trio()
    trio["base"] = _qm_composite()
    return trio


def _qm_layout(sources=(0, 1, 2), lignes=None, contexte=TF_CLEAN):
    lignes = [f"Situation {i + 1}" for i in range(3)] if lignes is None else lignes
    question = {"sources": list(sources), "enonce": "", "lignes": lignes}
    return {"variantes": [{"niveau": "Base", "contexte": contexte,
                           "questions": [question]}]}


def _tf_composite(claims=None, guide=TF_GUIDE, context=TF_CONTEXT):
    """Le composite « quatre affirmations à deux cases » d'avant mise en page."""
    claims = claims or TF_CLAIMS
    return {"response_type": "composite", "statement": context, "guide": guide,
            "questions": [
                _single(f"**Affirmation {chr(97 + i)} :** « {claim} »",
                        ["Vrai", "Faux"], TF_COLUMNS[i], "")
                for i, claim in enumerate(claims)]}


def _tf_trio():
    trio = _trio()
    trio["base"] = _tf_composite()
    return trio


def _tf_layout(sources=(0, 1, 2, 3), lignes=None, contexte=TF_CLEAN, enonce="",
               niveau="Base"):
    """La réponse de la passe 4 : un plan, du texte, AUCUNE réponse."""
    question = {"sources": list(sources), "enonce": enonce}
    question["lignes"] = list(TF_CLAIMS if lignes is None else lignes)
    return {"variantes": [{"niveau": niveau, "contexte": contexte,
                           "questions": [question]}]}


def _laid_out_base(db, monkeypatch, layout, trio=None):
    """Lance la famille avec CE plan de mise en page et rend (variante, appels)."""
    trio = trio or _tf_trio()
    replies = _happy(trio)
    replies["mp_layout"] = [layout]
    # la RETOUCHE (passe 5) vient après la mise en page : lui faire renvoyer le
    # trio scripté remettrait la version d'avant. Ici elle ne réécrit rien,
    # comme le fait une retouche qui n'a rien trouvé à reprendre.
    replies["mp_repair"] = [{}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY, family.reason
    return dict(family.variants)["base"], calls


def test_the_layout_pass_sees_no_answer_at_all(db, monkeypatch):
    """Elle ne corrige rien : ce n'est pas son rôle, et lui montrer les réponses
    l'inviterait à les « arranger » elle aussi. Elle reçoit exactement ce que
    voit le solveur, plus le numéro par lequel sa réécriture reviendra se poser."""
    _, calls = _laid_out_base(db, monkeypatch, _tf_layout())
    layout = next(c for c in calls if c["stage"] == "mp_layout")
    view = next(v for v in layout["payload"]["variantes"] if v["niveau"] == "Base")
    assert set(view) == {"niveau", "contexte", "questions"}
    assert set(view["questions"][0]) == {"n", "format", "enonce", "propositions"}
    assert [q["n"] for q in view["questions"]] == [0, 1, 2, 3]
    blob = str(layout["payload"])
    assert "correct" not in blob and "guide" not in blob and "check" not in blob


def test_four_identical_true_false_questions_become_one_grid(db, monkeypatch):
    """Le geste attendu : quatre sous-questions à deux cases identiques tiennent
    en UN tableau que l'élève lit d'un coup d'œil — sans que la moindre réponse
    soit repassée au modèle, puisque Python reporte les colonnes lui-même."""
    base, _ = _laid_out_base(db, monkeypatch, _tf_layout())
    assert base["response_type"] == "checkbox_grid"
    assert base["expected"]["cols"] == ["Vrai", "Faux"]
    assert [r["label"] for r in base["expected"]["rows"]] == TF_CLAIMS
    assert [r["correct"] for r in base["expected"]["rows"]] == TF_COLUMNS
    # le guide de l'exercice survit à l'aplatissement (les sous-questions n'en
    # portent pas, et celui de la variante ne doit pas être écrasé par le leur)
    assert base["correction"] == TF_GUIDE
    # les consignes qui n'apprennent rien ont disparu, les DONNÉES sont restées
    assert "Vrai ou faux ?" not in base["statement"]
    assert "affirmations suivantes" not in base["statement"]
    assert "| Effectif | 10 | 14 | 3 |" in base["statement"]
    # barème CODÉ, recalculé sur la nouvelle forme (0,5 pt la ligne)
    assert base["grading"]["bareme_points"] == 2.0


def test_qcm_multiple_batteries_explode_into_one_row_per_proposition(db, monkeypatch):
    """Geste 3b : un choix multiple répété (mêmes propositions) ne se reporte
    pas une ligne par question — une grille ne coche qu'UNE case par ligne —
    mais une ligne PAR PROPOSITION, préfixée par sa situation. Cas réel B3.1-14 :
    quatre lots « a : oui, b : oui, c : non » jamais mutualisés faute de ce
    geste."""
    base, _ = _laid_out_base(db, monkeypatch, _qm_layout(), _qm_trio())
    assert base["response_type"] == "checkbox_grid"
    assert base["expected"]["cols"] == ["Vrai", "Faux"]
    rows = base["expected"]["rows"]
    assert len(rows) == 9
    assert [r["label"] for r in rows[:3]] == [f"Situation 1 — {c}" for c in QM_CHOICES]
    expected = [0 if j in QM_CORRECT[i] else 1 for i in range(3) for j in range(3)]
    assert [r["correct"] for r in rows] == expected
    assert base["grading"]["bareme_points"] == 4.5      # 9 lignes à 0,5 pt


def test_a_multiple_choice_merge_over_the_row_cap_stays_separate(db, monkeypatch):
    """Quatre situations de trois propositions feraient douze lignes : au-delà du
    plafond d'une grille (dix, § scoring.QCM_MAX_GRID_ROWS), la mutualisation est
    refusée AVANT d'être tentée plutôt que produite puis rejetée — les questions
    restent séparées, ce qui n'est jamais une perte face à l'existant."""
    trio = _trio()
    trio["base"] = {"response_type": "composite", "statement": TF_CONTEXT,
                    "guide": TF_GUIDE, "questions": [
                        _multiple(f"Situation {i + 1} : coche les affirmations vraies.",
                                 QM_CHOICES, [0], "") for i in range(4)]}
    layout = _qm_layout(sources=(0, 1, 2, 3),
                        lignes=[f"Situation {i + 1}" for i in range(4)])
    base, _ = _laid_out_base(db, monkeypatch, layout, trio)
    assert base["response_type"] == "composite"
    assert len(base["expected"]["parts"]) == 4


def test_the_audit_judges_the_text_that_will_be_printed(db, monkeypatch):
    """La mise en page tombe AVANT l'audit : ce que l'audit relit est ce que
    l'élève recevra, pas la version d'avant."""
    _, calls = _laid_out_base(db, monkeypatch, _tf_layout())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    base = next(v for v in repair["payload"]["variantes"] if v["niveau"] == "Base")
    assert base["response_type"] == "checkbox_grid"
    assert "Vrai ou faux ?" not in base["statement"]


def test_the_independent_solution_follows_the_new_shape(db, monkeypatch):
    """La résolution de la passe 3 porte sur l'ANCIENNE découpe. Transposée sur
    la grille, une case cochée par question devient une colonne par ligne — sans
    quoi l'audit lirait des réponses décalées et refuserait un trio correct."""
    _, calls = _laid_out_base(db, monkeypatch, _tf_layout())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    base = next(v for v in repair["payload"]["variantes"] if v["niveau"] == "Base")
    assert base["resolution_independante"] == [TF_COLUMNS]
    assert repair["payload"]["desaccords_detectes"] == []


def test_a_plan_that_loses_a_question_changes_nothing(db, monkeypatch):
    """Une question oubliée par le plan, c'est une tâche perdue pour l'élève. La
    variante garde alors la présentation qu'elle avait : refuser ne coûte rien."""
    base, _ = _laid_out_base(db, monkeypatch, _tf_layout(sources=(0, 1, 2),
                                                         lignes=TF_CLAIMS[:3]))
    assert base["response_type"] == "composite"
    assert base["statement"] == TF_CONTEXT


def test_questions_with_different_choices_are_never_merged(db, monkeypatch):
    """« Mutualiser » n'a de sens que si les cases sont les mêmes : sinon la
    grille poserait à l'élève des colonnes qui ne correspondent pas à sa ligne.

    La grille est refusée, mais l'ALLÈGEMENT du texte reste : tout jeter pour
    une mutualisation impossible perdrait aussi le nettoyage, qui, lui, était
    bon — et chaque question garde évidemment ses propres propositions."""
    trio = _tf_trio()
    trio["base"]["questions"][2]["choices"] = ["Vrai", "Faux", "On ne peut pas savoir"]
    base, _ = _laid_out_base(db, monkeypatch, _tf_layout(), trio)
    assert base["response_type"] == "composite"
    parts = base["expected"]["parts"]
    assert [p["statement"] for p in parts] == TF_CLAIMS
    assert parts[2]["grading"]["choices"] == ["Vrai", "Faux", "On ne peut pas savoir"]
    assert [p["expected"]["correct"] for p in parts] == [[c] for c in TF_COLUMNS]


def test_a_rewrite_that_drops_a_datum_is_ignored(db, monkeypatch):
    """Compacter, ce n'est pas jeter : un nombre présent avant la mise en page
    doit s'y retrouver. Le tableau d'effectifs EST l'énoncé."""
    stripped = TF_CLEAN.split("\n")[0]           # le tableau a disparu
    base, _ = _laid_out_base(db, monkeypatch, _tf_layout(contexte=stripped))
    assert base["response_type"] == "composite"
    assert "| Effectif | 10 | 14 | 3 |" in base["statement"]


def test_a_rewrite_that_fails_the_python_gates_is_ignored(db, monkeypatch):
    """Ne dégrade JAMAIS, comme la relecture finale : deux lignes identiques ne
    se départagent pas, donc cette grille-là ne remplace pas le composite."""
    base, _ = _laid_out_base(db, monkeypatch,
                             _tf_layout(lignes=[TF_CLAIMS[0]] * 4))
    assert base["response_type"] == "composite"


def test_the_layout_keeps_the_figure_marker(db, monkeypatch):
    """Le placement de l'image est déterministe : une réécriture qui oublie le
    marqueur ne doit pas décrocher la figure du manuel."""
    trio = _tf_trio()
    trio["base"]["statement"] = TF_CONTEXT + "\n{{figure}}"
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    replies["mp_layout"] = [_tf_layout()]
    replies["mp_repair"] = [{}]              # mesurer la passe 4 seule
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e",
                                         {**MANUAL, "has_figure": True}, set())
    base = dict(family.variants)["base"]
    assert base["response_type"] == "checkbox_grid"
    assert statement_mod.has_figure_marker(base["statement"])


def test_an_unusable_layout_leaves_the_trio_alone(db, monkeypatch):
    """Sortie vide, illisible ou muette : le trio audité reste celui qu'a produit
    la génération. Une passe de forme ne fait jamais échouer une famille."""
    for reply in ({}, {"variantes": []}, {"variantes": [{"niveau": "Base"}]}):
        replies = _happy(_tf_trio())
        replies["mp_layout"] = [reply]
        replies["mp_repair"] = [{}]          # mesurer la passe 4 seule
        _script(monkeypatch, replies)
        family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
        assert family.state == indigo_multipass.READY
        assert dict(family.variants)["base"]["response_type"] == "composite"


def test_a_lone_question_only_loses_its_useless_instructions(db, monkeypatch):
    """Sans rien à mutualiser, la passe reste utile : elle retire la consigne qui
    n'apprend rien et laisse tout le reste intact."""
    trio = _trio()
    trio["base"] = _single("Réponds à la question suivante.\n"
                           "Calcule le PGCD de $1925$ et $4125$.",
                           ["$55$", "$175$", "$275$", "$385$"], 2, GUIDES["base"],
                           "gcd(1925, 4125)")
    replies = _happy(trio)
    replies["mp_layout"] = [{"variantes": [{"niveau": "Base", "questions": [
        {"sources": [0], "enonce": "Calcule le PGCD de $1925$ et $4125$."}]}]}]
    replies["mp_repair"] = [{}]              # mesurer la passe 4 seule
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    base = dict(family.variants)["base"]
    assert base["statement"] == "Calcule le PGCD de $1925$ et $4125$."
    assert base["response_type"] == "qcm_single"
    assert base["grading"]["choices"] == ["$55$", "$175$", "$275$", "$385$"]
    assert base["expected"]["correct"] == [2]


def test_a_merged_grid_keeps_no_useless_lead_in(db, monkeypatch):
    """Cas réel B4.1 n°16 : six affirmations Vrai/Faux et une septième question à
    trois cases. Les six deviennent une grille, la septième reste à part — et la
    grille n'a AUCUN texte au-dessus d'elle : ses colonnes disent quoi cocher,
    ses lignes ce qu'il faut juger. Lui réclamer une phrase, c'est réclamer le
    « Vrai ou faux ? » que cette passe vient de supprimer."""
    trio = _tf_trio()
    aside = _single("Le nombre de voitures bleues dépasse-t-il celui de la ville B ?",
                    ["Vrai", "Faux", "On ne peut pas savoir"], 2, "")
    trio["base"]["questions"].append(aside)
    layout = _tf_layout()
    layout["variantes"][0]["questions"].append(
        {"sources": [4], "enonce": aside["statement"]})
    base, _ = _laid_out_base(db, monkeypatch, layout, trio)
    assert base["response_type"] == "composite"
    grid, last = base["expected"]["parts"]
    assert grid["response_type"] == "checkbox_grid"
    assert grid["statement"] == ""
    assert [r["label"] for r in grid["expected"]["rows"]] == TF_CLAIMS
    assert last["grading"]["choices"] == ["Vrai", "Faux", "On ne peut pas savoir"]


def test_choices_mistaken_for_row_labels_are_ignored(db, monkeypatch):
    """Cas réel B4.3 n°46 : le modèle range les PROPOSITIONS dans `lignes`. Les
    recopier remplacerait chaque question par sa propre réponse — l'énoncé
    d'origine est gardé, et la mutualisation refusée avec lui."""
    trio = _tf_trio()
    for i, part in enumerate(trio["base"]["questions"]):
        part["choices"] = [f"{160 + i}", f"{150 + i}", f"{110 + i}"]
        part["correct"] = [0]
    base, _ = _laid_out_base(
        db, monkeypatch, _tf_layout(lignes=["160", "161", "162", "163"]), trio)
    assert base["response_type"] == "composite"
    assert [p["statement"] for p in base["expected"]["parts"]] == [
        f"**Affirmation {chr(97 + i)} :** « {claim} »"
        for i, claim in enumerate(TF_CLAIMS)]


def test_the_data_moved_out_of_a_lone_question_is_kept(db, monkeypatch):
    """Cas réel B4.1 n°18 : le modèle sort la série de données de la question et
    la range dans `contexte`. Une variante simple n'a pas de contexte séparé :
    les deux se recollent, sinon les quarante diamètres disparaissaient."""
    trio = _trio()
    trio["base"] = _single("Voici les diamètres : 49, 42, 57, 41.\n"
                           "Dans quelle classe y a-t-il le plus de tomates ?",
                           ["$[40;45[$", "$[45;50[$", "$[50;55[$", "$[55;60[$"],
                           1, GUIDES["base"])
    replies = _happy(trio)
    replies["mp_layout"] = [{"variantes": [{
        "niveau": "Base", "contexte": "Voici les diamètres : 49, 42, 57, 41.",
        "questions": [{"sources": [0],
                       "enonce": "Dans quelle classe y a-t-il le plus de tomates ?"}]}]}]
    replies["mp_repair"] = [{}]              # mesurer la passe 4 seule
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    base = dict(family.variants)["base"]
    assert base["statement"] == ("Voici les diamètres : 49, 42, 57, 41.\n"
                                 "Dans quelle classe y a-t-il le plus de tomates ?")


# ------------------------------------------------------------ passe 5 : retouche

def test_a_defect_is_repaired_in_place_and_never_regenerated(db, monkeypatch):
    """LA règle de la révision du 04/09. Un défaut ne renvoie plus la famille en
    génération : la passe 5 le reçoit, réécrit la variante, et c'est SA version
    qui part en brouillon. Une seule génération, pour un exercice meilleur."""
    trio = _trio()
    broken = _trio()
    broken["base"]["guide"] = "Le PGCD vaut $275$, vérifie en divisant."   # vend la mèche
    replies = _happy(broken)
    replies["mp_repair"] = [{"exercices": trio, "signalements": []}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    assert family.attempts == 1
    stages = [c["stage"] for c in calls]
    assert stages.count("mp_generate") == 1
    # la passe 5 a reçu le défaut EXACT, nommé par Python, pas un « refais-le »
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    defects = " ".join(d for entry in repair["payload"]["defauts"]
                       for d in entry["defauts"])
    assert "bonne réponse" in defects and "Base" in str(repair["payload"]["defauts"])
    assert "$275$" not in dict(family.variants)["base"]["correction"]


def test_what_the_repair_cannot_fix_becomes_a_badge_not_a_new_generation(db, monkeypatch):
    """« Si l'exercice est trop mal fait, apposer un badge adapté. » Le
    signalement bloquant voyage jusqu'à la carte ; la génération, elle, n'est
    jamais rejouée."""
    replies = _happy()
    replies["mp_repair"] = [{"exercices": _trio(), "signalements": [
        {"niveau": "Facile", "gravite": "bloquant",
         "probleme": "la question est incompréhensible sans la figure"},
        {"niveau": "Base", "gravite": "reserve", "probleme": "formulation lourde"}]}]
    calls = _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.NEEDS_REVIEW
    assert len(family.variants) == 2           # rien n'est jeté
    assert family.blocking == ["Facile : la question est incompréhensible "
                               "sans la figure"]
    # la réserve suit aussi, mais sans badge rouge
    assert any("formulation lourde" in note for note in family.notes)
    assert [c["stage"] for c in calls].count("mp_generate") == 1


def test_a_repair_that_makes_it_worse_is_dropped_variant_by_variant(db, monkeypatch):
    """Sans regénération derrière, ce comptage est la SEULE chose qui empêche une
    retouche malheureuse d'aller telle quelle en brouillon."""
    worse = _trio()
    worse["base"]["choices"] = ["$275$", "$275$", "$275$", "$275$"]   # plus de distracteurs
    worse["facile"]["statement"] = "Calcule le PGCD de $12$ et $18$, joliment reformulé."
    replies = _happy()
    replies["mp_repair"] = [{"exercices": worse, "signalements": []}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    kept = dict(family.variants)
    assert family.state == indigo_multipass.READY
    assert kept["base"]["grading"]["choices"] == ["$55$", "$175$", "$275$", "$385$"]
    # la variante SAINE garde bien la retouche : on ne jette pas le trio entier
    assert "joliment reformulé" in kept["facile"]["statement"]


def test_an_unreadable_repair_leaves_the_trio_exactly_as_it_was(db, monkeypatch):
    """Une sortie vide ne vaut ni acceptation ni refus : elle ne vaut rien, et le
    trio mis en page part tel quel."""
    replies = _happy()
    replies["mp_repair"] = [{}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    assert dict(family.variants)["base"]["statement"] == \
        "Calcule le PGCD de $1925$ et $4125$."


def test_a_repair_that_forgets_the_guide_gets_the_old_one_back(db, monkeypatch):
    """Le guide n'est pas l'objet de la retouche. Qu'elle l'oublie en réécrivant
    l'énoncé est une perte sèche — et le validateur partagé refuse ensuite
    l'exercice pour une correction vide (cas réel n°29 des pages 67-68)."""
    silent = _trio()
    silent["base"]["statement"] = "Calcule le PGCD de $1925$ et de $4125$."
    silent["base"]["guide"] = ""
    replies = _happy()
    replies["mp_repair"] = [{"exercices": silent, "signalements": []}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    base = dict(family.variants)["base"]
    assert base["statement"] == "Calcule le PGCD de $1925$ et de $4125$."
    assert base["correction"] == GUIDES["base"]


def test_the_repair_never_writes_a_check_python_carries_it(db, monkeypatch):
    """Lui demander du sympy pour des questions qu'elle n'a pas touchées, c'est
    lui donner une façon de casser une retouche par ailleurs bonne. Le contrôle
    des questions INCHANGÉES est reporté ; celui d'une réponse changée tombe."""
    repaired = _trio()
    repaired["base"] = _single("Calcule le PGCD de $1925$ et $4125$.",
                               ["$55$", "$175$", "$275$", "$385$"], 2, GUIDES["base"])
    repaired["base"].pop("check")
    repaired["facile"] = _single("Calcule le PGCD de $12$ et $18$.",
                                 ["$2$", "$3$", "$6$", "$9$"], 1, GUIDES["facile"])
    repaired["facile"].pop("check")
    before = _trio()
    assert indigo_multipass._carry_checks(
        before["base"], repaired["base"])["check"]["expr"] == "gcd(1925, 4125)"
    # la réponse a changé (choix $3$ au lieu de $6$) : l'ancien contrôle ne
    # vérifie plus rien, il disparaît
    assert indigo_multipass._carry_checks(
        before["facile"], repaired["facile"])["check"] == {"kind": "none"}


def test_the_suspicion_of_a_given_away_answer_is_a_question_not_a_verdict(db, monkeypatch):
    """Python voit qu'une proposition est déjà écrite dans l'énoncé, mais il ne
    peut pas distinguer « la réponse est recopiée » de « la donnée nécessaire au
    calcul est donnée » : sur toute lecture de tableau, la bonne case EST l'un
    des nombres écrits au-dessus. Le soupçon part donc à la retouche, qui, elle,
    sait trancher — et il ne compte PAS dans le score qui la mesure, sans quoi
    aucune retouche ne passerait sur un exercice de statistiques."""
    trio = _trio()
    trio["base"] = _multiple("Parmi $2$, $4$ et $5$, coche les nombres premiers.",
                             ["$2$", "$4$", "$5$"], [0, 2], GUIDES["base"])
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    suspicions = str(repair["payload"]["indices_soupconnes"])
    assert "Base" in suspicions and "répétées dans l'énoncé" in suspicions
    # ce n'est pas un défaut : l'exercice reste publiable en l'état
    assert indigo_multipass._variant_problems(trio["base"], has_figure=False) == []
    assert family.state == indigo_multipass.READY


@pytest.mark.parametrize("stage", ["mp_generate", "mp_repair"])
def test_a_trio_rendered_as_a_list_is_read_not_thrown_away(db, monkeypatch, stage):
    """Flash rend parfois `exercices` en LISTE, chaque entrée portant son
    `niveau`. L'y chercher par clé levait un AttributeError qui coûtait la
    famille entière — quatre tentatives puis REJECTED_GENERATION pour une sortie
    parfaitement exploitable (n°47 des pages 67-68)."""
    trio = _trio()
    as_list = [{"niveau": indigo_multipass.VARIANT_LABEL[k], **trio[k]}
               for k in indigo_multipass.VARIANTS]
    replies = _happy(trio)
    replies[stage] = [{"exercices": as_list, "signalements": []}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    assert len(family.variants) == 2


def test_a_second_repair_round_runs_only_while_something_remains_to_fix(db, monkeypatch):
    """Deux tours au plus, et le second seulement s'il reste un défaut. Réparer
    deux fois le même texte reste réparer sur place : l'exercice ne repart
    jamais de zéro, et le tour de trop coûte UN appel là où l'ancienne relance
    en coûtait quatre."""
    broken = _trio()
    broken["base"]["guide"] = " ".join(["mot"] * 31)
    replies = _happy(broken)
    replies["mp_repair"] = [{}]                    # ne répare rien, jamais
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert [c["stage"] for c in calls].count("mp_repair") == \
        settings.indigo_multipass_repair_rounds

    # contre-épreuve : plus rien à réparer après le premier tour, on s'arrête
    calls = _script(monkeypatch, _happy())
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert [c["stage"] for c in calls].count("mp_repair") == 1


def test_a_platform_contract_refusal_reaches_the_repair_instead_of_the_bin(db, monkeypatch):
    """Les refus du validateur partagé (« span LaTeX refusé », « label de ligne
    invalide ») n'apparaissaient qu'à la toute fin, quand plus personne ne
    pouvait les corriger : l'exercice était perdu après cinq passes de travail.
    Nommés à la retouche, ils se réparent en une phrase."""
    trio = _trio()
    trio["base"]["statement"] = "Calcule le PGCD de $1925$ et $4125$. " + "x" * 1300
    calls = _first_generation_only(monkeypatch, trio)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    defauts = str(repair["payload"]["defauts"])
    assert "contrat de la plateforme" in defauts or "trop long" in defauts


def test_a_repair_that_makes_two_levels_indistinguishable_is_reverted(db, monkeypatch):
    """Le validateur efface les NOMBRES avant de comparer : deux niveaux que la
    retouche rapproche passaient le contrôle au caractère près pour tomber au
    doublon, et la variante était perdue au lieu d'être rendue à sa version
    d'avant (cas réel n°14 des pages 67-68)."""
    same = _trio()
    same["facile"] = _single("Calcule le PGCD de $12$ et $18$.",
                             ["$2$", "$3$", "$6$", "$9$"], 2, GUIDES["facile"])
    same["base"] = _single("Calcule le PGCD de $1925$ et $4125$.",
                           ["$55$", "$175$", "$275$", "$385$"], 2, GUIDES["base"])
    replies = _happy()
    replies["mp_repair"] = [{"exercices": same, "signalements": []}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    # les deux niveaux survivent : le second est revenu à sa version d'avant
    assert {kind for kind, _ in family.variants} == {"base", "facile"}


def test_the_solver_disagreement_is_handed_to_the_repair(db, monkeypatch):
    trio = _trio()
    replies = _happy(trio)
    replies["mp_solve"] = [_solutions(trio, {"base": [[0]]})]
    calls = _script(monkeypatch, replies)
    indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    assert any("Base" in d and "solveur" in d
               for d in repair["payload"]["desaccords_detectes"])
    assert any("Python/SymPy confirme" in d
               for d in repair["payload"]["desaccords_detectes"])


def test_a_self_contradictory_signal_cannot_flag_the_answer_it_confirms():
    trio = _trio()
    assert indigo_multipass._signal_confirms_declared({
        "niveau": "Base",
        "probleme": ("Le solveur trouve 175, mais le calcul donne 275 ; "
                     "la bonne réponse est 275.")}, trio)
    assert not indigo_multipass._signal_confirms_declared({
        "niveau": "Base",
        "probleme": ("Le solveur trouve 175 et le calcul confirme que la "
                     "bonne réponse est 175.")}, trio)


def test_a_confirming_signal_is_downgraded_instead_of_badging_a_right_answer(db, monkeypatch):
    """Le même filet, de bout en bout : un « bloquant » qui confirme la réponse
    qu'il dénonce ne doit pas coller un badge rouge sur un exercice juste."""
    replies = _happy()
    replies["mp_repair"] = [{"exercices": _trio(), "signalements": [
        {"niveau": "Base", "gravite": "bloquant",
         "probleme": ("Le solveur trouve 175, mais le calcul donne 275 ; "
                      "la bonne réponse est 275.")}]}]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.blocking == []
    assert family.state == indigo_multipass.READY


# ------------------------------------------------- portes Python (gratuites)
# Elles n'arrêtent plus la pipeline : elles la RENSEIGNENT. Ce que Python sait
# reprocher gratuitement part, nommé, à la passe 5 qui le répare — et ce qui
# subsiste après elle voyage jusqu'au brouillon. Avant, chacun de ces défauts
# coûtait une génération complète de plus, pour le même exercice.

def _first_generation_only(monkeypatch, trio):
    """Scénario où la génération est jouée une seule fois — c'est-à-dire le cas
    NORMAL : elle n'est plus jamais rejouée pour un défaut de qualité."""
    replies = _happy(trio)
    replies["mp_generate"] = [{"exercices": trio}]
    replies["mp_repair"] = [{}]          # la retouche ne réécrit rien ici
    return _script(monkeypatch, replies)


def _handed_to_the_repair(calls, *needles):
    """Le défaut que Python a trouvé est-il arrivé à la passe 5, en clair ?"""
    assert [c["stage"] for c in calls].count("mp_generate") == 1, "génération rejouée"
    repair = next(c for c in calls if c["stage"] == "mp_repair")
    defects = " ".join(d for entry in repair["payload"]["defauts"]
                       for d in entry["defauts"])
    for needle in needles:
        assert needle in defects, f"{needle!r} absent de {defects!r}"
    return True


def test_a_guide_over_thirty_words_is_named_to_the_repair(db, monkeypatch):
    trio = _trio()
    trio["base"]["guide"] = " ".join(["mot"] * 31)
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "maximum 30")
    assert _handed_to_the_repair(calls, "maximum 30")


def test_a_guide_that_gives_the_answer_away_is_refused(db, monkeypatch):
    """« ne jamais révéler la réponse » : Python attrape au moins la réponse
    recopiée telle quelle, gratuitement et à coup sûr."""
    trio = _trio()
    trio["base"]["guide"] = "Le PGCD vaut $275$, vérifie en divisant les deux nombres."
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "bonne réponse")
    assert _handed_to_the_repair(calls, "bonne réponse")


def test_a_guide_is_not_flagged_for_a_number_that_only_looks_like_the_answer(db, monkeypatch):
    """Contre-épreuve du filet : « 2 diviseurs » ne doit pas faire tomber un
    guide dont la réponse est « 275 » — un filet qui refuse les bons guides ne
    vaut rien."""
    trio = _trio()
    trio["base"]["guide"] = "Un diviseur commun divise 1925 et 4125 sans reste. Compare-les."
    _script(monkeypatch, _happy(trio))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY


def test_two_identical_variants_are_not_a_gradation(db, monkeypatch):
    trio = _trio()
    trio["facile"] = dict(trio["base"])
    calls = _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    # Le clone est écarté par le dédoublonnage du validateur partagé : il ne
    # reste que des niveaux réellement distincts, et le manque est dit.
    # Ce défaut-là n'est PAS soumis à la retouche : distinguer deux niveaux
    # demande d'inventer un autre exercice, c'est-à-dire de générer.
    _flagged(family, "CLONES", dropped=("facile",))
    assert [c["stage"] for c in calls].count("mp_generate") == 1


def test_a_mathematically_wrong_variant_is_caught_by_sympy(db, monkeypatch):
    """Le filet déterministe de « QCM only » (services.indigo_check) tourne ici
    aussi : la réponse déclarée est recalculée."""
    trio = _trio()
    trio["base"]["correct"] = [1]
    trio["base"]["check"] = {"kind": "value", "expr": "gcd(1925, 4125)", "choice": 1}
    _first_generation_only(monkeypatch, trio)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    _flagged(family, "ne vaut pas")


def test_a_family_that_never_repeats_itself_keeps_its_two_prints(db, monkeypatch):
    """Régression : le dé-doublonnage travaille sur une COPIE tant que la famille
    n'est pas conservée. Sans cela, une reprise après incident se ferait refuser
    ses propres variantes comme « doublons » de l'essai interrompu."""
    trio = _trio()
    replies = _happy(trio)
    _script(monkeypatch, replies)
    norms: set[str] = set()
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, norms)
    assert family.state == indigo_multipass.READY
    assert len(norms) == 2          # deux empreintes, une par variante retenue


# ----------------------------------------------------- persistance + publication

def _row(comp):
    return IndigoExercise(id="base-id", competency_id=comp.id, grade_level="3e",
                          source_number="34", badge_type="exercice")


def test_a_red_badge_lands_only_on_the_variant_it_names(db, monkeypatch):
    """Un badge qu'on voit partout ne signale plus rien : le défaut du niveau
    base ne doit pas faire douter de la carte facile. Ce qui ne nomme aucun
    niveau, en revanche, vaut pour les deux.

    Utilise une source « experte » pour vérifier au passage que le badge suit
    le niveau GÉNÉRÉ (« Base ») même quand la persistance le reclasse sous une
    autre étiquette (§ `_multipass_variant_tag`) — la ligne qui reçoit le
    reproche est celle du niveau généré nommé, pas celle qui porte son
    étiquette finale."""
    replies = _happy()
    replies["mp_repair"] = [{"exercices": _trio(), "signalements": [
        {"niveau": "Base", "gravite": "bloquant",
         "probleme": "la réponse annoncée est fausse"},
        {"gravite": "bloquant", "probleme": "la figure du manuel manque"}]}]
    _script(monkeypatch, replies)
    comp = _comp(db)
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    row = _row(comp)
    row.badge_type = "expert"
    rows = indigo._persist_multipass_family(db, row, MANUAL, family)
    db.commit()
    by_kind = {r.variant_kind: (r.raw_ocr_json or {}).get("review_blocking") or []
               for r in rows}
    # remappée (source experte) : le niveau généré « base » devient la carte
    # « difficile » du dossier, et c'est bien ELLE qui reçoit le reproche nommé.
    assert by_kind["base"] == ["la figure du manuel manque"]
    assert "Base : la réponse annoncée est fausse" in by_kind["difficile"]
    assert "la figure du manuel manque" in by_kind["difficile"]


def test_an_expert_source_remaps_its_two_variants(db, monkeypatch):
    """Un exercice « expert » du manuel (badge CV) est déjà le plus dur : sa
    variante Base générée — même niveau que la source — tient lieu de dérivé
    Difficile de la plateforme, et son dérivé Facile généré (plus simple qu'un
    expert, pas plus qu'un exercice ordinaire) devient une variante Base. Les
    prompts n'en savent rien : seule l'étiquette change à l'écriture
    (§ indigo._multipass_variant_tag)."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    row = _row(comp)
    row.badge_type = "expert"
    rows = indigo._persist_multipass_family(db, row, MANUAL, family)
    db.commit()
    by_kind = {r.variant_kind: r for r in rows}
    assert set(by_kind) == {"difficile", "base"}
    assert by_kind["difficile"].difficulty == 3
    assert by_kind["base"].difficulty == 2


def test_a_normal_source_keeps_base_and_facile_untouched(db, monkeypatch):
    """Contre-épreuve : une source ordinaire (n'importe quel badge autre
    qu'« expert ») ne subit aucun reclassement — c'est le comportement
    d'avant, inchangé."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    row = _row(comp)
    assert row.badge_type == "exercice"
    rows = indigo._persist_multipass_family(db, row, MANUAL, family)
    db.commit()
    by_kind = {r.variant_kind: r for r in rows}
    assert set(by_kind) == {"base", "facile"}
    assert by_kind["base"].difficulty == 2
    assert by_kind["facile"].difficulty == 1


def test_persisting_a_family_writes_two_draft_rows_without_a_teacher_solution(db, monkeypatch):
    """« les corrigés profs ne sont pas rédigés » : le champ reste VIDE, et pas
    rempli d'un « à compléter » qui annoncerait du travail humain.

    Et les lignes sont des BROUILLONS. Le mode se validait lui-même au motif
    qu'aucun de ses exercices ne devait avoir besoin d'une main humaine ; c'était
    se donner raison d'avance. Valider est un geste du professeur."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    rows = indigo._persist_multipass_family(db, _row(comp), MANUAL, family)
    db.commit()
    assert len(rows) == 2
    saved = db.query(IndigoExercise).all()
    assert sorted(r.difficulty for r in saved) == [1, 2]
    assert all(r.status == "draft" for r in saved)
    assert all(r.validated_at is None and r.validated_by is None for r in saved)
    assert all(r.correction_solution == "" for r in saved)
    assert {r.correction_guide for r in saved} == set(GUIDES.values())
    by_kind = {r.variant_kind: r for r in saved}
    assert by_kind["base"].derived_from_id is None
    assert by_kind["facile"].derived_from_id == "base-id"
    assert all(r.prompt_version == indigo_multipass.PROMPT_VERSION for r in saved)


def test_a_ready_family_is_published_immediately(db, monkeypatch):
    """READY = en banque tout de suite, sans attendre la fin de l'extraction ni
    un clic sur « Publier »."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    rows = indigo._persist_multipass_family(db, _row(comp), MANUAL, family)
    db.commit()
    assert indigo.publish_rows(db, rows) == 2
    assert len(indigo.load_published()["exercises"]) == 2
    seeded = db.query(GeneratedExercise).filter_by(source="indigo").all()
    assert len(seeded) == 2
    assert sorted(g.difficulty_level for g in seeded) == [1, 2]
    # le guide élève est bien ce qui part en banque comme correction
    assert {g.correction for g in seeded} == set(GUIDES.values())


def test_a_composite_family_reaches_the_bank_with_its_sub_questions(db, monkeypatch):
    """Publication immédiate d'un exercice à sous-questions : ce sont les parties
    qui doivent arriver en banque, pas un exercice vidé de ses questions."""
    comp = _comp(db)
    _script(monkeypatch, _happy(_composite_trio()))
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    rows = indigo._persist_multipass_family(db, _row(comp), MANUAL, family)
    db.commit()
    assert indigo.publish_rows(db, rows) == 2
    seeded = db.query(GeneratedExercise).filter_by(source="indigo").all()
    assert len(seeded) == 2
    assert {g.response_type for g in seeded} == {"composite"}
    base = next(g for g in seeded if g.difficulty_level == 2)
    assert [p["response_type"] for p in base.expected_json["parts"]] == [
        "qcm_multiple", "qcm_single"]
    assert base.grading_json["bareme_points"] == 3.0


def _row_with_drawing(comp, row_id="base-id"):
    """Une ligne telle que l'extraction la prépare : extrait du manuel + figure
    isolée, tous deux sur le disque (les dérivés les recopient)."""
    row = _row(comp)
    row.id = row_id
    row.crop_path, row.figure_path, row.has_figure = (
        f"indigo/drafts/{row_id}.png", f"indigo/drafts/{row_id}_fig.png", True)
    for rel in (row.crop_path, row.figure_path):
        path = indigo.crop_abs_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    return row


def test_an_unused_drawing_is_detached_and_never_replaced_by_the_whole_crop(db, monkeypatch):
    """Deux pièges d'un coup : la figure inutile ne doit pas s'imprimer, et le
    repli « à défaut de figure, l'extrait complet du manuel » ne doit pas se
    déclencher — il collerait l'énoncé d'ORIGINE à côté de l'exercice réécrit."""
    comp = _comp(db)
    _script(monkeypatch, _happy())                  # besoin_figure: False
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL_FIG, set())
    rows = indigo._persist_multipass_family(db, _row_with_drawing(comp),
                                            MANUAL_FIG, family)
    db.commit()
    assert all(not r.has_figure and not r.figure_path for r in rows)
    assert all("{{figure}}" not in r.statement for r in rows)
    assert not list((settings.data_dir / "indigo" / "drafts").glob("*_fig_fallback.png"))


def test_a_used_drawing_is_placed_before_the_questions(db, monkeypatch):
    """Le placement est déterministe, aucun prompt ne s'en occupe : le marqueur
    est posé entre l'énoncé et la question sur un exercice simple, à la fin du
    contexte sur un composite — jamais après les questions."""
    comp = _comp(db)
    replies = _happy(_figure_trio())
    replies["mp_filter"] = [_kept_with_figure()]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL_FIG, set())
    rows = indigo._persist_multipass_family(db, _row_with_drawing(comp),
                                            MANUAL_FIG, family)
    db.commit()
    # exercice simple d'une ligne : la figure passe DEVANT la question
    assert all(r.has_figure and r.statement.startswith("{{figure}}") for r in rows)

    # composite : la figure va à la fin du CONTEXTE, pas au milieu
    contexte = ("Voici un triangle $ABC$ rectangle en $A$.\n"
                "On donne $AB = 6$ cm et $AC = 8$ cm, comme sur la figure ci-contre.")
    trio = _composite_trio()
    for kind in trio:
        trio[kind]["statement"] = contexte + f"\n({kind})"
    replies = _happy(trio)
    replies["mp_filter"] = [_kept_with_figure()]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL_FIG, set())
    rows = indigo._persist_multipass_family(db, _row_with_drawing(comp, "compo-id"),
                                            MANUAL_FIG, family)
    for r in rows:
        lines = r.statement.split("\n")
        assert lines[-1] == "{{figure}}"                 # après tout le contexte
        assert "$AC = 8$ cm" in "\n".join(lines[:-1])    # et pas au milieu


def test_regenerating_drops_a_drawing_the_new_exercise_no_longer_uses(db, monkeypatch):
    """Régénérer rejoue les cinq passes, donc rejoue la décision sur la figure.
    Un exercice qui ne s'appuie plus dessus ne doit pas continuer à l'imprimer."""
    comp = _comp(db)
    row = _row_with_drawing(comp)
    row.variant_kind, row.response_type = "base", "qcm_single"
    row.raw_ocr_json = {"statement": MANUAL["statement"], "correction": ""}
    db.add(row); db.commit()
    monkeypatch.setattr(indigo_llm, "mode", lambda db_: indigo_llm.MODE_MULTIPASS)
    _script(monkeypatch, _happy())                  # besoin_figure: False
    assert indigo.regenerate_exercises(db, [row.id])["regenerated"] == 1
    db.refresh(row)
    assert not row.has_figure and not row.figure_path
    assert "{{figure}}" not in row.statement
    assert not list((settings.data_dir / "indigo" / "drafts").glob("*_fig_fallback.png"))


def test_a_figure_family_carries_its_drawing_all_the_way_to_the_bank(db, monkeypatch):
    """Le dessin doit survivre au trajet complet : les trois lignes le portent
    (chacune sa copie), le fichier publié le référence, et la banque reçoit une
    figure image — sinon l'élève lit un énoncé qui parle d'un dessin absent."""
    comp = _comp(db)
    replies = _happy(_figure_trio())
    replies["mp_filter"] = [_kept_with_figure()]
    _script(monkeypatch, replies)
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL_FIG, set())
    rows = indigo._persist_multipass_family(db, _row_with_drawing(comp),
                                            MANUAL_FIG, family)
    db.commit()
    assert len({r.figure_path for r in rows}) == 2      # une copie par variante
    assert indigo.publish_rows(db, rows) == 2
    published = indigo.load_published()["exercises"]
    assert all(rec["has_figure"] and rec["figure_file"] for rec in published)
    seeded = db.query(GeneratedExercise).filter_by(source="indigo").all()
    assert all(g.figure_json["type"] == "image" for g in seeded)
    assert all("{{figure}}" in g.statement for g in seeded)


def test_publishing_the_same_family_twice_replaces_it(db, monkeypatch):
    """Idempotent : une famille régénérée écrase la précédente, elle ne
    s'ajoute pas à côté."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    rows = indigo._persist_multipass_family(db, _row(comp), MANUAL, family)
    db.commit()
    indigo.publish_rows(db, rows)
    indigo.publish_rows(db, rows)
    assert len(indigo.load_published()["exercises"]) == 2
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 2


def test_publishing_a_family_leaves_the_already_published_ones_alone(db, monkeypatch):
    """`publish_rows` écrit PARTIELLEMENT : contrairement à `publish`, elle n'a
    rien à effacer."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    family = indigo_multipass.run_family(db, comp, "3e", MANUAL, set())
    indigo.publish_rows(db, indigo._persist_multipass_family(db, _row(comp), MANUAL, family))
    other = IndigoExercise(id="autre", competency_id=comp.id, grade_level="3e",
                           source_number="99", badge_type="exercice", status="validated",
                           statement="Un autre exercice tout à fait valable.",
                           response_type="qcm_single")
    db.add(other); db.commit()
    indigo.publish_rows(db, [other])
    ids = {r["id"] for r in indigo.load_published()["exercises"]}
    assert "autre" in ids and "base-id" in ids and len(ids) == 3


# --------------------------------------------------- la cible, de bout en bout

def test_an_unusable_source_leaves_nothing_but_a_usable_one_makes_drafts(db, monkeypatch):
    """LA différence avec les deux autres modes : pas de repli OCR brut. Une
    source que la passe 1 déclare illisible ne laisse AUCUNE ligne.

    Ce qui est retenu, en revanche, part en BROUILLON et n'est PAS publié :
    la banque ne reçoit que ce que le professeur a relu et validé."""
    comp = _comp(db)
    replies = _happy()
    replies["mp_filter"] = [{"verdict": "keep", "enonce": SOURCE},
                            {"verdict": "reject", "reason": "OCR inexploitable"}]
    _script(monkeypatch, replies)
    prepared = [
        (IndigoExercise(id="ex-a", competency_id=comp.id, grade_level="3e",
                        source_number="34", badge_type="exercice"), MANUAL),
        (IndigoExercise(id="ex-b", competency_id=comp.id, grade_level="3e",
                        source_number="35", badge_type="exercice"),
         {**MANUAL, "number": "35"}),
    ]
    msgs: list[str] = []
    made, ok, stopped, errors = indigo._run_multipass(
        db, comp, "3e", prepared, msgs.append)
    assert (made, ok, stopped, errors) == (2, 2, "", [])
    # deux lignes pour la source retenue, zéro pour l'autre
    rows = db.query(IndigoExercise).all()
    assert len(rows) == 2 and {r.source_number for r in rows} == {"34"}
    assert all(r.status == "draft" for r in rows)
    # RIEN en banque : la publication attend la validation du professeur
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 0
    assert any("REJECTED_SOURCE" in m for m in msgs)
    assert any("1/2 source(s) retenue(s)" in m for m in msgs)


def test_a_duplicate_trio_is_named_as_such_not_left_unexplained(db, monkeypatch):
    """Deux sources qui donnent le MÊME trio : la seconde est refusée comme
    doublon, et le générateur reçoit une raison exploitable. Sans elle il
    relançait trois tentatives à l'aveugle — douze appels pour rien."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    prepared = [(IndigoExercise(id=f"ex-{i}", competency_id=comp.id, grade_level="3e",
                                source_number=str(34 + i), badge_type="exercice"),
                 {**MANUAL, "number": str(34 + i)}) for i in range(2)]
    msgs: list[str] = []
    made, _ok, _stopped, _errors = indigo._run_multipass(
        db, comp, "3e", prepared, msgs.append)
    assert made == 2                                   # la 1re source seulement
    assert any("doublon" in m for m in msgs)


def test_a_budget_ceiling_stops_the_target_without_creating_stubs(db, monkeypatch):
    """Au plafond de dépense, on s'arrête NET. Pas de repli OCR brut : les
    exercices restants n'existent tout simplement pas encore."""
    comp = _comp(db)

    def broke(db_, stage, system, payload, correlation_id):
        raise providers.BudgetExceeded("Budget deepseek-flash quotidien atteint")

    monkeypatch.setattr(indigo_llm, "call", broke)
    prepared = [(IndigoExercise(id="ex-a", competency_id=comp.id, grade_level="3e",
                                source_number="34", badge_type="exercice"), MANUAL)]
    msgs: list[str] = []
    made, ok, stopped, _errors = indigo._run_multipass(
        db, comp, "3e", prepared, msgs.append)
    assert (made, ok) == (0, 0)
    assert "quotidien atteint" in stopped
    assert db.query(IndigoExercise).count() == 0
    assert any("ARRÊTÉE" in m for m in msgs)


def test_the_off_peak_gate_is_crossed_before_every_pass(db, monkeypatch):
    """« ne pas commencer un nouvel appel » après la fermeture : le portillon est
    franchi avant CHAQUE passe, jamais au milieu de l'une d'elles."""
    comp = _comp(db)
    _script(monkeypatch, _happy())
    seen: list[int] = []
    monkeypatch.setattr(indigo_offpeak, "wait_until_open",
                        lambda db_, **kw: seen.append(1))
    prepared = [(IndigoExercise(id="ex-a", competency_id=comp.id, grade_level="3e",
                                source_number="34", badge_type="exercice"), MANUAL)]
    indigo._run_multipass(db, comp, "3e", prepared, lambda m: None)
    assert len(seen) == 6           # une fois par passe, et pas une de plus


# ----------------------------------------------- incidents, pas défauts

def test_a_truncated_call_is_retried_with_a_wider_output_budget(db, monkeypatch):
    """Le raisonnement de DeepSeek V4 mange le budget de sortie et le JSON
    n'arrive jamais. L'échelle de budgets existe pour ça — encore faut-il
    qu'elle se déclenche : côté DeepSeek, la troncature ne portait pas le mot
    que `is_truncated` cherche, et l'appel échouait définitivement."""
    budgets = []

    def fake_deepseek(db_, op, system, payload, *, max_tokens, model,
                      correlation_id, total_timeout=None, **kw):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            raise ValueError(f"Sortie DeepSeek invalide après 2 tentatives : "
                             f"Réponse DeepSeek TRONQUÉE ({model}) : budget de "
                             f"sortie max_tokens={max_tokens} épuisé avant le JSON "
                             f"(arrêt=length, raisonnement présent).")
        return {"verdict": "keep", "enonce": SOURCE, "besoin_figure": False}

    monkeypatch.setattr(indigo_llm.providers, "deepseek_json", fake_deepseek)
    monkeypatch.setattr(indigo_llm, "get_provider", lambda db_: "multipass")
    out = indigo_llm.call(db, "mp_filter", "system", {}, "cid")
    assert out["verdict"] == "keep"
    assert budgets == [settings.indigo_multipass_max_output_tokens,
                       settings.indigo_multipass_max_output_tokens * 2]


def test_a_transport_breakdown_costs_one_attempt_not_the_source(db, monkeypatch):
    """Un délai dépassé n'est pas un défaut de l'exercice. Il coûte UNE
    tentative, comme un refus d'audit — pas la source. Avant, la moindre
    coupure jetait la famille entière du premier coup."""
    calls = _script(monkeypatch, _happy())
    real = indigo_multipass._pass_generate
    tries = []

    def flaky(*a, **kw):
        tries.append(1)
        if len(tries) == 1:
            raise providers.LLMTimeout("DeepSeek : pas de réponse complète après 600s")
        return real(*a, **kw)

    monkeypatch.setattr(indigo_multipass, "_pass_generate", flaky)
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.READY
    assert family.attempts == 2
    assert [c["stage"] for c in calls].count("mp_filter") == 1   # la passe 1 n'est pas rejouée


def test_repeated_breakdowns_name_the_real_cause(db, monkeypatch):
    """Trois coupures d'affilée finissent bien par écarter la source — mais le
    motif doit dire CE QUI s'est passé, sinon on cherche un problème de qualité
    là où le réseau a lâché."""
    _script(monkeypatch, _happy())
    monkeypatch.setattr(indigo_multipass, "_pass_generate",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            providers.LLMTimeout("pas de réponse complète après 600s")))
    family = indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())
    assert family.state == indigo_multipass.REJECTED_GENERATION
    assert family.attempts == settings.indigo_multipass_max_attempts
    assert "LLMTimeout" in family.reason and "600s" in family.reason


def test_a_budget_ceiling_still_stops_everything_at_once(db, monkeypatch):
    """Contre-épreuve : le plafond de dépense n'est PAS un incident à retenter.
    Il remonte tel quel, la cible s'arrête net."""
    monkeypatch.setattr(indigo_multipass, "_pass_generate",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            providers.BudgetExceeded("Budget deepseek-flash quotidien atteint")))
    _script(monkeypatch, _happy())
    with pytest.raises(providers.BudgetExceeded):
        indigo_multipass.run_family(db, _comp(db), "3e", MANUAL, set())


# --------------------------------------------------------------- heures creuses
# La plage n'est plus configurable (§ indigo_offpeak, réécrit le 05/09) : les
# heures PLEINES de DeepSeek sont CODÉES EN DUR — 01h-04h et 06h-10h UTC, du
# lundi au vendredi — et le seul réglage restant est la case « enabled ».

def _cfg(enabled=True):
    return {"enabled": enabled}


def test_a_weekday_peak_window_is_closed(db):
    """Heures PLEINES : 01h-04h et 06h-10h UTC, du lundi au vendredi — le reste
    de la semaine est creux."""
    at = lambda h, m=0: datetime(2026, 9, 3, h, m)          # jeudi
    assert indigo_offpeak.is_peak_hour(at(2))
    assert indigo_offpeak.is_peak_hour(at(7))
    assert indigo_offpeak.is_peak_hour(at(1))               # borne de début incluse
    assert not indigo_offpeak.is_peak_hour(at(4))            # borne de fin exclue
    assert not indigo_offpeak.is_peak_hour(at(5))
    assert not indigo_offpeak.is_peak_hour(at(11))
    assert not indigo_offpeak.is_open(_cfg(), at(2))
    assert indigo_offpeak.is_open(_cfg(), at(5))


def test_the_weekend_is_always_off_peak(db):
    """Le week-end entier est creux, même aux heures qui seraient pleines en
    semaine."""
    samedi = lambda h: datetime(2026, 9, 5, h)
    dimanche = lambda h: datetime(2026, 9, 6, h)
    for at in (samedi, dimanche):
        assert not indigo_offpeak.is_peak_hour(at(2))
        assert not indigo_offpeak.is_peak_hour(at(7))
        assert indigo_offpeak.is_open(_cfg(), at(2))


def test_disabled_means_always_open(db):
    assert indigo_offpeak.is_open(_cfg(enabled=False), datetime(2026, 9, 3, 2))


def test_next_open_is_the_end_of_the_current_window(db):
    assert indigo_offpeak.next_open(_cfg(), datetime(2026, 9, 3, 2, 30)) == \
        datetime(2026, 9, 3, 4, 0)
    assert indigo_offpeak.next_open(_cfg(), datetime(2026, 9, 3, 7, 15)) == \
        datetime(2026, 9, 3, 10, 0)
    # déjà ouverte : maintenant
    ouverte = datetime(2026, 9, 3, 5, 0)
    assert indigo_offpeak.next_open(_cfg(), ouverte) == ouverte


def test_the_setting_is_just_a_checkbox_and_it_persists(db):
    assert indigo_offpeak.get_config(db) == {"enabled": False}       # défaut : décochée
    saved = indigo_offpeak.set_config(db, enabled=True)
    assert saved == {"enabled": True}
    assert indigo_offpeak.get_config(db) == saved


def test_waiting_returns_at_once_when_the_window_is_open(db, monkeypatch):
    """Le portillon ne coûte rien quand le tarif est déjà creux : pas la moindre
    veille."""
    indigo_offpeak.set_config(db, enabled=True)
    slept: list[float] = []
    monkeypatch.setattr(indigo_offpeak.time_mod, "sleep", lambda s: slept.append(s))
    indigo_offpeak.wait_until_open(db, now_fn=lambda: datetime(2026, 9, 3, 5))
    assert slept == []


def test_waiting_announces_the_reopening_then_releases(db, monkeypatch):
    """Fermée puis ouverte : un seul message d'attente, et la reprise annoncée."""
    indigo_offpeak.set_config(db, enabled=True)
    monkeypatch.setattr(indigo_offpeak, "POLL_S", 0)
    clock = iter([datetime(2026, 9, 3, 2), datetime(2026, 9, 3, 4, 1),
                 datetime(2026, 9, 3, 4, 1)])
    msgs: list[str] = []
    indigo_offpeak.wait_until_open(db, progress_cb=msgs.append, now_fn=lambda: next(clock))
    assert len(msgs) == 2
    assert msgs[0].startswith("⏸") and "04:00" in msgs[0]
    assert msgs[1].startswith("⏳")


# ------------------------------------------------------------------ aiguillage

def test_multipass_selects_deepseek_flash_and_the_multipass_pipeline(db):
    indigo_llm.set_provider(db, "anthropic")
    assert indigo_llm.mode(db) == indigo_llm.MODE_CLASSIC
    indigo_llm.set_provider(db, "multipass")
    assert indigo_llm.mode(db) == indigo_llm.MODE_MULTIPASS
    # DeepSeek FLASH, pas le pro — et la clé de fournisseur suit le MODÈLE
    assert indigo_llm.model_for(db, "adapt") == settings.indigo_multipass_model
    assert "flash" in indigo_llm.model_for(db, "adapt")
    assert indigo_llm.config_provider_key(db) == "deepseek-flash"
    assert "multipass" in indigo_llm.label(db)


def test_the_first_call_starts_above_the_default_deepseek_budget(db, monkeypatch):
    """Le budget par défaut de DeepSeek (8192) ne suffit pas à ce mode : mesuré
    sur l'extraction A1.2 du 03/09, TOUS les appels de génération le
    consommaient entièrement en réflexion sans rendre le moindre JSON. Partir
    plus haut n'évite pas l'échec (l'échelle rattrape), ça évite de payer un
    appel perdu par exercice."""
    assert (settings.indigo_multipass_max_output_tokens
            > settings.deepseek_max_output_tokens)
    seen = []
    monkeypatch.setattr(indigo_llm, "get_provider", lambda db_: "multipass")
    monkeypatch.setattr(
        indigo_llm.providers, "deepseek_json",
        lambda *a, max_tokens, **kw: seen.append(max_tokens) or {"ok": True})
    indigo_llm.call(db, "mp_generate", "system", {}, "cid")
    assert seen == [settings.indigo_multipass_max_output_tokens]


def test_every_pass_runs_on_the_same_plain_flash_model(db, monkeypatch):
    """Un seul modèle pour les cinq passes, sans variante raisonneuse ni réglage
    par passe : ce qui distingue les passes, c'est ce qu'on leur MONTRE."""
    sent = []

    def fake(db, operation, system, payload, **kw):
        sent.append({"operation": operation, **kw})
        return {}

    monkeypatch.setattr(providers, "deepseek_json", fake)
    indigo_llm.set_provider(db, "multipass")
    for stage in ("mp_filter", "mp_generate", "mp_solve", "mp_layout", "mp_repair"):
        indigo_llm.call(db, stage, "sys", {}, "cid")
    assert {s["model"] for s in sent} == {settings.indigo_multipass_model}
    assert len({s["max_tokens"] for s in sent}) == 1
    # RAISONNEMENT DÉSACTIVÉ, et pour les cinq de la même façon : c'est lui qui
    # consommait tout le budget de sortie sans rendre de JSON. Le mode ne perd
    # rien à s'en passer — sa justesse vient des cinq relectures, dont une
    # résolution indépendante, pas d'un appel qui réfléchit plus longtemps.
    assert all(s["thinking"] is False for s in sent)
    # chaque passe reste traçable séparément sur la page Coûts
    assert [s["operation"] for s in sent] == [
        "indigo_mp_filter", "indigo_mp_generate", "indigo_mp_solve",
        "indigo_mp_layout", "indigo_mp_repair"]


# ---------------------------------------------- passe 2 PARTAGÉE (le lot)

MANUAL_B = {"number": "35", "statement": "Calculer le PGCD de 240 et 900.",
           "has_figure": False}


def _trio_b():
    return {
        "facile": _single("Calcule le PGCD de $24$ et $36$.",
                          ["$6$", "$8$", "$12$", "$18$"], 2, GUIDES["facile"],
                          "gcd(24, 36)"),
        "base": _single("Calcule le PGCD de $240$ et $900$.",
                        ["$20$", "$60$", "$90$", "$120$"], 1, GUIDES["base"],
                        "gcd(240, 900)"),
    }


MANUAL_C = {"number": "36", "statement": "Calculer le PGCD de 100 et 250.",
           "has_figure": False}


def _trio_c():
    return {
        "facile": _single("Calcule le PGCD de $10$ et $25$.",
                          ["$5$", "$10$", "$25$", "$50$"], 0, GUIDES["facile"],
                          "gcd(10, 25)"),
        "base": _single("Calcule le PGCD de $100$ et $250$.",
                        ["$25$", "$50$", "$100$", "$250$"], 1, GUIDES["base"],
                        "gcd(100, 250)"),
    }


def _lot_reply(trio_by_number: dict[str, dict]) -> dict:
    return {"lots": [{"source_number": num, "exercices": trio}
                     for num, trio in trio_by_number.items()]}


def _batch_replies(trio_a, trio_b, *, lot=None) -> dict:
    return {
        "mp_filter": [{"verdict": "keep", "enonce": SOURCE, "besoin_figure": False},
                     {"verdict": "keep", "enonce": "Calculer le PGCD de $240$ et $900$.",
                      "besoin_figure": False}],
        "mp_generate": [lot if lot is not None
                        else _lot_reply({"34": trio_a, "35": trio_b})],
        "mp_solve": [_solutions(trio_a), _solutions(trio_b)],
        "mp_layout": [{}, {}],
        "mp_repair": [{}, {}],
    }


def test_two_sources_of_the_same_competency_share_one_generation_call(db, monkeypatch):
    """Le cœur du réglage indigo_multipass_batch_size : deux sources RATTACHÉES
    à la même compétence n'appellent la passe 2 qu'UNE fois, chacune recevant
    quand même son propre trio, distinct de l'autre."""
    trio_a, trio_b = _trio(), _trio_b()
    calls = _script(monkeypatch, _batch_replies(trio_a, trio_b))
    families = indigo_multipass.run_family_pair(
        db, _comp(db), "3e", [MANUAL, MANUAL_B], {})
    assert [f.number for f in families] == ["34", "35"]
    assert [f.state for f in families] == [indigo_multipass.READY] * 2
    assert [c["stage"] for c in calls].count("mp_generate") == 1
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert {s["number"] for s in gen["payload"]["sources"]} == {"34", "35"}
    base_a = dict(families[0].variants)["base"]
    base_b = dict(families[1].variants)["base"]
    assert base_a["statement"] != base_b["statement"]
    assert "240" in base_b["statement"] and "1925" in base_a["statement"]


def test_sources_of_different_competencies_are_not_batched(db, monkeypatch):
    """Le prompt système de la passe 2 ne décrit qu'UNE compétence : deux
    sources rattachées à des compétences différentes retombent chacune sur sa
    propre génération solo, jamais un appel partagé qui n'aurait de sens pour
    aucune des deux."""
    fw = CompetencyFramework(grade_level="3e", name="T2")
    db.add(fw); db.flush()
    other = Competency(framework_id=fw.id, code="B2.1", short_id="B2.1",
                       label="Fractions", domain_code="B", domain_name="Nombres",
                       chapter_code="B2", chapter_name="Fractions", order_index=2)
    db.add(other); db.commit()
    trio_a, trio_b = _trio(), _trio_b()
    replies = _batch_replies(trio_a, trio_b)
    # deux générations SOLO, une par source, dans l'ordre où chaque famille
    # atteint sa propre passe 2
    replies["mp_generate"] = [{"exercices": trio_a}, {"exercices": trio_b}]
    calls = _script(monkeypatch, replies)
    families = indigo_multipass.run_family_pair(
        db, [_comp(db), other], "3e", [MANUAL, {**MANUAL_B, "competency_title": "Fractions"}], {})
    assert [f.state for f in families] == [indigo_multipass.READY] * 2
    assert [c["stage"] for c in calls].count("mp_generate") == 2
    for c in calls:
        if c["stage"] == "mp_generate":
            assert "sources" not in c["payload"]     # jamais l'enveloppe partagée


def test_one_infeasible_source_does_not_block_pairing_of_the_others(db, monkeypatch):
    """La passe CONTEXTE tranche la faisabilité de CHAQUE source AVANT même
    l'idée d'un appel partagé (§ `_resolve_family`, phase A) : l'une peut être
    rejetée pendant que les DEUX autres, elles, partagent quand même un seul
    appel de génération entre elles — le rejet de la source du milieu ne casse
    pas le partage des deux qui restent."""
    trio_a, trio_c = _trio(), _trio_c()
    replies = {
        "mp_filter": [{"verdict": "keep", "enonce": SOURCE, "besoin_figure": False},
                     {"verdict": "keep", "enonce": "Calculer le PGCD de $240$ et $900$.",
                      "besoin_figure": False},
                     {"verdict": "keep", "enonce": "Calculer le PGCD de $100$ et $250$.",
                      "besoin_figure": False}],
        "mp_context": [{"mode": "source"},
                       {"mode": "reject", "raison": "la figure donne les "
                                                     "longueurs, l'énoncé aucune"},
                       {"mode": "source"}],
        "mp_generate": [_lot_reply({"34": trio_a, "36": trio_c})],
        "mp_solve": [_solutions(trio_a), _solutions(trio_c)],
        "mp_layout": [{}, {}],
        "mp_repair": [{}, {}],
    }
    calls = _script(monkeypatch, replies)
    families = indigo_multipass.run_family_pair(
        db, _comp(db), "3e", [MANUAL, MANUAL_B, MANUAL_C], {})
    assert families[0].state == indigo_multipass.READY
    assert families[1].state == indigo_multipass.REJECTED_SOURCE
    assert "figure" in families[1].reason
    assert families[2].state == indigo_multipass.READY
    assert [c["stage"] for c in calls].count("mp_generate") == 1
    gen = next(c for c in calls if c["stage"] == "mp_generate")
    assert {s["number"] for s in gen["payload"]["sources"]} == {"34", "36"}


def test_a_failed_shared_call_falls_back_to_solo_generation_per_source(db, monkeypatch):
    """L'appel partagé peut échouer (transport) sans perdre les deux familles :
    chacune refait sa PROPRE génération, sans repayer la passe 1 déjà tranchée."""
    trio_a, trio_b = _trio(), _trio_b()
    replies = _batch_replies(trio_a, trio_b)
    replies["mp_generate"] = [
        providers.LLMTimeout("DeepSeek : pas de réponse complète après 600s"),
        {"exercices": trio_a}, {"exercices": trio_b}]
    calls = []

    def fake(db_, stage, system, payload, correlation_id):
        calls.append({"stage": stage, "payload": payload})
        queue = replies.get(stage)
        if queue is None:
            return None
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(indigo_llm, "call", fake)
    families = indigo_multipass.run_family_pair(
        db, _comp(db), "3e", [MANUAL, MANUAL_B], {})
    assert [f.state for f in families] == [indigo_multipass.READY] * 2
    assert [c["stage"] for c in calls].count("mp_generate") == 3   # 1 partagé + 2 solo
    assert [c["stage"] for c in calls].count("mp_filter") == 2     # jamais rejouée

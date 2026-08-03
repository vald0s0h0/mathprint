"""Indigo (onglet Exercices) — briques pures : lecture de couleur (CV) et
segmentation OCR. Ces tests ne touchent PAS aux gros PDF (manuels locaux à
l'admin, non versionnés) ni au réseau ; ils fixent le comportement du CV
(classification par teinte) et du découpage en exercices/problèmes.
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (Competency, CompetencyFramework, GeneratedExercise,
                        IndigoExercise)
from app.services import exercise_gen, figures, indigo, indigo_cv, pdfgen


# ------------------------------------------------------------------- CV couleur

def _badge_crop(bgr):
    img = np.full((160, 240, 3), 255, np.uint8)
    cv2.circle(img, (28, 28), 16, bgr, -1)
    return img


def test_cv_classifies_exercice_flash_enigme():
    # teal -> exercice, orange -> flash, rouge -> enigme (BGR = RVB inversé)
    assert indigo_cv.analyze(_badge_crop((185, 180, 40)), False)["badge_type"] == "exercice"
    assert indigo_cv.analyze(_badge_crop((47, 134, 240)), False)["badge_type"] == "flash"
    assert indigo_cv.analyze(_badge_crop((43, 70, 240)), False)["badge_type"] == "enigme"


def test_cv_probleme_difficulty_from_title_color():
    def title(bgr):
        img = np.full((120, 300, 3), 255, np.uint8)
        cv2.putText(img, "Titre", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, bgr, 4)
        return img
    # vert -> moyen (diff 3), orange -> facile (diff 2)
    assert indigo_cv.analyze(title((58, 198, 118)), True)["difficulty"] == 3
    assert indigo_cv.analyze(title((42, 133, 244)), True)["difficulty"] == 2
    # gris/noir (peu saturé) -> difficile (diff 4)
    assert indigo_cv.analyze(title((32, 31, 35)), True)["difficulty"] == 4


def test_cv_expert_from_mint_background():
    """Fond vert d'eau (RVB 225,243,243) au coin bas-droit du numéro => Expert,
    même si le badge est teal (comme un exercice normal)."""
    nb = {"x0": 12, "y0": 12, "x1": 44, "y1": 44}
    mint = np.full((160, 240, 3), (243, 243, 225), np.uint8)   # BGR de (225,243,243)
    cv2.circle(mint, (28, 28), 16, (185, 180, 40), -1)          # badge teal
    assert indigo_cv.analyze(mint, False, number_box=nb)["badge_type"] == "expert"
    assert indigo_cv.analyze(mint, False, number_box=nb)["difficulty"] == 5
    # même badge sur fond BLANC -> reste exercice (le blanc est hors tolérance)
    white = _badge_crop((185, 180, 40))
    assert indigo_cv.analyze(white, False, number_box=nb)["badge_type"] == "exercice"


def test_cv_no_color_defaults_to_exercice():
    blank = np.full((120, 200, 3), 255, np.uint8)
    out = indigo_cv.analyze(blank, False)
    assert out["badge_type"] == "exercice"
    assert out["calculator"] == "autorisee"


def test_cv_enigme_only_from_badge_not_from_figure():
    """Une FIGURE rouge dans le crop (ex. engrenage rouge d'un « Mode Expert »)
    ne doit PAS être lue comme une énigme : l'énigme ne vaut que si le ROUGE est
    sur le badge (zone du numéro), pas via le repli plein-crop."""
    nb = {"x0": 8, "y0": 8, "x1": 48, "y1": 48}          # zone du numéro
    fig = np.full((200, 240, 3), 255, np.uint8)
    cv2.rectangle(fig, (10, 130), (230, 195), (43, 70, 240), -1)   # figure rouge (BGR), loin du numéro
    assert indigo_cv.analyze(fig, False, number_box=nb)["badge_type"] != "enigme"
    # mais un vrai badge rouge DANS la zone du numéro reste une énigme
    badged = np.full((200, 240, 3), 255, np.uint8)
    cv2.circle(badged, (28, 28), 16, (43, 70, 240), -1)
    assert indigo_cv.analyze(badged, False, number_box=nb)["badge_type"] == "enigme"


def test_cv_red_badge_is_enigme_even_if_probleme():
    """Badge rouge => énigme, même si le texte ressemble à un problème (seul
    indicateur d'énigme = couleur du badge, cf. utilisateur)."""
    red = _badge_crop((43, 70, 240))
    assert indigo_cv.analyze(red, True)["badge_type"] == "enigme"


# ------------------------------------------------------------- segmentation OCR

def _block(t, content, x0, y0, x1, y1):
    return {"type": t, "content": content, "top_left_x": x0, "top_left_y": y0,
            "bottom_right_x": x1, "bottom_right_y": y1}


class _C:
    def __init__(self, id, label):
        self.id, self.label = id, label
        self.short_id = self.code = id


def test_segment_detects_all_exercises_by_number_sequence():
    """Les numéros d'exercices (13, 14…) sont au DÉBUT de blocs text/list, pas
    dans des titres : la détection est par séquence croissante, et un « 5 × … »
    en corps ne fragmente pas (5 < 13)."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("list", "13 Calcule. Combien font 3 + 4 ?", 40, 100, 400, 150),
                _block("text", "5 × ... = 20", 40, 155, 400, 175),   # corps -> pas un début
                _block("text", "14 Complète les égalités.", 40, 200, 400, 230),
            ]}
    exos = indigo._segment_target(page, T, [T])
    assert [e["number"] for e in exos] == ["13", "14"]
    assert len(exos[0]["blocks"]) == 2   # énoncé + ligne de corps


def test_segment_ignores_other_competency_after_its_title():
    """Une AUTRE compétence sur la même page (titre = son libellé) exclut ses
    exercices ; « QUESTIONS FLASH » ne change rien."""
    target = _C("a1", "Déterminer les diviseurs d'un nombre entier")
    other = _C("a2", "Reconnaître un nombre premier")
    page = {"source_page": 16, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("title", "# Déterminer les diviseurs d'un nombre entier", 40, 60, 500, 90),
                _block("title", "# QUESTIONS FLASH", 40, 100, 300, 120),
                _block("text", "13 Donne les diviseurs de 12.", 40, 130, 400, 160),
                _block("text", "14 Déterminer les diviseurs de 30.", 40, 170, 400, 200),
                _block("title", "# Reconnaître un nombre premier", 40, 230, 500, 260),
                _block("text", "15 Vrai ou faux : 7 est premier ?", 40, 270, 400, 300),
            ]}
    exos = indigo._segment_target(page, target, [target, other])
    assert [e["number"] for e in exos] == ["13", "14"]   # 15 (A1.2) exclu


def test_parse_int_range_inclusive():
    assert indigo.parse_int_range("34-67")[0] == 34
    assert indigo.parse_int_range("34-67")[-1] == 67
    assert len(indigo.parse_int_range("34-67")) == 34
    assert indigo.parse_int_range("40") == [40]
    assert indigo.parse_int_range("") == []
    assert indigo.parse_int_range(None) == []
    assert indigo.parse_int_range("67-34") == list(range(34, 68))   # bornes remises dans l'ordre
    assert indigo.parse_int_range("12 à 15") == [12, 13, 14, 15]


def test_normalize_target_expands_ranges():
    t = indigo.normalize_target({"competency_id": "c", "eleve_page_range": "34-36",
                                 "prof_page_range": "180", "number_range": "34-67"})
    assert t["eleve_pages"] == [33, 34, 35]      # 1-based (PDF) -> 0-based
    assert t["prof_pages"] == [179]
    assert t["numbers"][0] == 34 and t["numbers"][-1] == 67


def test_segment_by_numbers_never_merges():
    """La PLAGE de numéros découpe même sans filtre de compétence ; un nombre
    HORS plage dans le corps (« 5 à 9 ») ne démarre pas d'exercice."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("text", "34 Calcule A.", 40, 100, 400, 130),
                _block("text", "35 Calcule B.", 40, 140, 400, 170),
                _block("text", "36 Calcule C.", 40, 180, 400, 210),
                _block("text", "Range de 5 à 9.", 40, 220, 400, 240),   # « 5 » hors plage
            ]}
    exos = indigo._segment_by_numbers(page, T, set(range(34, 68)))
    assert [e["number"] for e in exos] == ["34", "35", "36"]
    assert len(exos[-1]["blocks"]) == 2          # « 5 à 9 » rattaché au 36, pas un nouvel exo


def test_segment_corrections_by_numbers():
    page = {"source_page": 200, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("text", "34 Le résultat est 12.", 40, 100, 400, 130),
                _block("text", "détail du calcul", 40, 140, 400, 160),
                _block("text", "35 Le résultat est 7.", 40, 180, 400, 210),
            ]}
    out = indigo._segment_corrections_by_numbers(page, set(range(34, 68)))
    assert set(out) == {"34", "35"}
    assert "détail du calcul" in out["34"]


def test_choose_batch_size_stays_in_window_and_fills_last_batch():
    """Fenêtre ramenée à 3-4 (02/08) : un lot de 5 à 7 exercices demandait
    couramment 12 000 à 25 000 tokens de SORTIE en une réponse — au-delà du
    plafond de sortie et du délai d'appel, donc systématiquement en échec."""
    from app.services import indigo_gemini as g
    lo, hi = g.BATCH_MIN, g.BATCH_MAX
    assert (lo, hi) == (3, 4)
    assert g.choose_batch_size(hi) == hi          # <= BATCH_MAX -> un seul lot
    for n in range(hi + 1, 60):
        s = g.choose_batch_size(n)
        assert lo <= s <= hi
        last = n % s or s
        for alt in range(lo, hi + 1):
            assert last >= (n % alt or alt)      # aucune taille ne remplit mieux le dernier lot


# --------------------------------------------------- relecture finale (Sonnet)

def test_choose_review_batch_size_window():
    """Vérification DeepSeek par lots de 6 à 8 (contrainte = plafond de sortie
    DeepSeek ≈ 8k, le modèle réémet tous les champs) : un seul lot en dessous de
    8, sinon la taille qui remplit au mieux le dernier lot dans [6, 8]."""
    from app.services import indigo_verify as v
    assert v.choose_review_batch_size(7) == 7        # <= 8 -> un seul lot
    assert v.choose_review_batch_size(8) == 8
    assert v.choose_review_batch_size(4) == 4         # vérifie tout ce qu'on a, même < 6
    for n in range(9, 200):
        s = v.choose_review_batch_size(n)
        assert 6 <= s <= 8
        last = n % s or s
        for alt in range(6, 9):
            assert last >= (n % alt or alt)           # aucune taille ne remplit mieux le dernier lot


def test_review_item_carries_source_for_independent_check(db):
    """Le cœur de la fiabilisation : la vérification reçoit AUSSI la source (OCR
    brut + corrigé prof), pas seulement le candidat — sans elle, Opus ne peut
    détecter qu'une incohérence interne, jamais une lecture OCR infidèle."""
    from app.services import indigo_verify
    comp = _comp(db)
    manual, valid = _adapted_valid(db, comp, "34")
    item = indigo_verify._review_item("34", manual, valid)
    assert item is not None
    # la SOURCE (vérité terrain)
    assert item["source_statement"] == manual["statement"]
    assert item["source_correction"] == manual["correction"]
    assert item["has_figure"] is False
    # le CANDIDAT à contrôler (réponse attendue visible)
    assert item["source_number"] == "34"
    assert item["answer"]["value"] == 5
    assert item["statement"] == valid["statement"]


def test_indigo_llm_provider_default_and_models(db):
    """Défaut = Anthropic (Sonnet découpage+génération, Opus vérification) ; le
    câblage DeepSeek met les trois étapes sur DeepSeek pro v4."""
    from app.services import indigo_llm
    assert indigo_llm.get_provider(db) == "anthropic"        # défaut demandé
    assert "sonnet" in indigo_llm.model_for(db, "segment")
    assert "sonnet" in indigo_llm.model_for(db, "adapt")
    assert "opus" in indigo_llm.model_for(db, "review")
    indigo_llm.set_provider(db, "deepseek")
    assert indigo_llm.get_provider(db) == "deepseek"
    for stage in ("segment", "adapt", "review"):
        m = indigo_llm.model_for(db, stage)
        assert "deepseek" in m and "pro" in m


def test_indigo_llm_call_routes_to_chosen_provider(db, monkeypatch):
    """Le toggle câble le bon fournisseur : anthropic → claude_json (Sonnet/Opus),
    deepseek → deepseek_json (modèle pro). On capture le modèle réellement passé."""
    from app.services import indigo_llm, providers
    seen: dict = {}

    def cap_claude(db_, op, system, payload, **kw):
        seen["fn"], seen["op"], seen["model"] = "claude", op, kw.get("model")
        return {"ok": True}

    def cap_deepseek(db_, op, system, payload, **kw):
        seen["fn"], seen["op"], seen["model"] = "deepseek", op, kw.get("model")
        return {"ok": True}

    monkeypatch.setattr(providers, "claude_json", cap_claude)
    monkeypatch.setattr(providers, "deepseek_json", cap_deepseek)

    indigo_llm.call(db, "review", "sys", {}, "cid")           # défaut anthropic
    assert seen["fn"] == "claude" and seen["op"] == "indigo_review"
    assert "opus" in seen["model"]

    indigo_llm.set_provider(db, "deepseek")
    indigo_llm.call(db, "adapt", "sys", {}, "cid")
    assert seen["fn"] == "deepseek" and seen["op"] == "indigo_adapt"
    assert "deepseek" in seen["model"] and "pro" in seen["model"]


def test_matching_answer_schema_validates(db):
    """Le format `matching` corrigé dans generation.txt (colonnes/paires DANS
    `answer`, avec `type`) est bien celui qu'accepte le validateur partagé — le
    bug d'origine mettait left/right/pairs à la racine (jamais validé)."""
    from app.services import indigo_gemini
    comp = _comp(db)
    raw = {"kind": "application", "bareme_points": 1,
           "statement": "Associe chaque calcul à son résultat.",
           "response_type": "matching",
           "answer": {"type": "matching", "left": ["$2+3$", "$4+5$"],
                      "right": ["$5$", "$9$"], "pairs": [[0, 0], [1, 1]]},
           "correction": "Calcule chaque somme.",
           "correction_solution": "$2+3=5$ ; $4+5=9$.",
           "source_number": "12", "needs_figure": False}
    manual = {"number": "12", "statement": raw["statement"],
              "correction": raw["correction_solution"], "has_figure": False}
    valid = indigo_gemini._finalize(raw, comp, db, manual)
    assert valid is not None and valid["response_type"] == "matching"
    assert valid["expected"]["pairs"] == [[0, 0], [1, 1]]


def _grid_raw(correct=(0, 1, 0)):
    return {"kind": "application", "bareme_points": 2,
            "statement": "Pour chaque affirmation, coche la bonne case.",
            "response_type": "checkbox_grid",
            "answer": {"type": "grid", "cols": ["Vrai", "Faux"],
                       "rows": [{"label": "$2$ divise $A$", "correct": correct[0]},
                                {"label": "$A$ est multiple de $7$", "correct": correct[1]},
                                {"label": "$A$ est divisible par $6$", "correct": correct[2]}]},
            "correction": "Un diviseur divise sans reste.",
            "correction_solution": "$A=2\\times3^2\\times5^2$.",
            "source_number": "63", "needs_figure": False}


def test_checkbox_grid_schema_validates(db):
    """Nouveau type `checkbox_grid` : une grille cochée (Vrai/Faux) accepte
    2-4 colonnes, 2-12 lignes, chaque `correct` dans les bornes des colonnes."""
    from app.services import indigo_gemini
    comp = _comp(db)
    raw = _grid_raw()
    manual = {"number": "63", "statement": raw["statement"],
              "correction": raw["correction_solution"], "has_figure": False}
    valid = indigo_gemini._finalize(raw, comp, db, manual)
    assert valid is not None and valid["response_type"] == "checkbox_grid"
    assert valid["expected"]["cols"] == ["Vrai", "Faux"]
    assert [r["correct"] for r in valid["expected"]["rows"]] == [0, 1, 0]
    assert valid["grading"]["comparator"] == "grid"
    assert valid["grading"]["max_score"] == 3


def test_checkbox_grid_rejects_out_of_range_correct(db):
    from app.services import exercise_gen
    comp = _comp(db)
    raw = _grid_raw(correct=(0, 5, 0))          # 5 hors [0,1]
    assert exercise_gen._validate_exercise(raw, comp, db, set(),
                                           allow_geometry_text=True) is None
    assert "correct" in exercise_gen.diagnose_rejection(raw, comp)


def test_grid_grading_scores_per_row():
    from app.services import grading
    g = {"max_score": 3, "comparator": "grid", "cols": ["Vrai", "Faux"],
         "rows": [{"correct": 0}, {"correct": 1}, {"correct": 0}]}
    assert grading.grade({}, g, "", 0.99, selected_choices=[0, 1, 0])["score"] == 3.0
    assert grading.grade({}, g, "", 0.99, selected_choices=[0, 0, 0])["score"] == 2.0
    # blanc (rien coché) sur une ligne = 0 pour cette ligne, pas de revue
    assert grading.grade({}, g, "", 0.99, selected_choices=[0, -1, 0])["score"] == 2.0
    # lecture ambiguë (None) -> revue
    assert grading.grade({}, g, "", 0.99, selected_choices=None)["reason_code"] == "grid_unreadable"


def test_detect_grid_picks_checked_column_per_row(monkeypatch):
    from app.services import worker_cv
    boxes = [{"row": 0, "col": 0}, {"row": 0, "col": 1},
             {"row": 1, "col": 0}, {"row": 1, "col": 1}]
    thr = worker_cv.QcmThreshold(value=0.2, band=(0.1, 0.2), adapted=False)
    monkeypatch.setattr(worker_cv, "qcm_densities", lambda w, b: [0.5, 0.02, 0.01, 0.6])
    sel, _d = worker_cv.detect_grid(None, boxes, thr)
    assert sel == [0, 1]                          # ligne0 -> col0, ligne1 -> col1
    monkeypatch.setattr(worker_cv, "qcm_densities", lambda w, b: [0.5, 0.6, 0.01, 0.6])
    assert worker_cv.detect_grid(None, boxes, thr)[0] is None   # double coche -> revue
    monkeypatch.setattr(worker_cv, "qcm_densities", lambda w, b: [0.15, 0.02, 0.01, 0.6])
    assert worker_cv.detect_grid(None, boxes, thr)[0] is None   # densité dans la bande -> revue


def _composite_raw():
    return {"kind": "application", "bareme_points": 3,
            "statement": "On considère $A = 2 \\times 3^2$ et $B = 2^2 \\times 3 \\times 7$.",
            "response_type": "composite",
            "answer": {"type": "composite", "parts": [
                {"response_type": "checkbox_grid",
                 "statement": "Coche la bonne case.",
                 "answer": {"type": "grid", "cols": ["Vrai", "Faux"],
                            "rows": [{"label": "$2$ divise $A$", "correct": 0},
                                     {"label": "$A$ est multiple de $7$", "correct": 1}]}},
                {"response_type": "short_text", "statement": "Donne le PGCD de $A$ et $B$.",
                 "answer": {"type": "integer", "value": 6}}]},
            "correction": "Un diviseur divise sans reste.",
            "correction_solution": "$PGCD(A,B)=2\\times3=6$.",
            "source_number": "63", "needs_figure": False}


def test_composite_validates_and_sums_parts(db):
    """Exercice composite : chaque partie est validée comme une feuille autonome,
    le barème total est la SOMME des parties."""
    from app.services import indigo_gemini
    comp = _comp(db)
    raw = _composite_raw()
    manual = {"number": "63", "statement": raw["statement"],
              "correction": raw["correction_solution"], "has_figure": False}
    valid = indigo_gemini._finalize(raw, comp, db, manual)
    assert valid is not None and valid["response_type"] == "composite"
    parts = valid["expected"]["parts"]
    assert [p["response_type"] for p in parts] == ["checkbox_grid", "short_text"]
    assert valid["grading"]["max_score"] == 3.0        # 2 (grille) + 1 (case)


def test_composite_part_labels_are_stripped(db):
    """« a. a. Donne la décomposition… » (vu sur A1.3 n°70, par intermittence
    selon les régénérations) : le RENDU numérote déjà chaque partie
    (pdfgen._composite_layout préfixe par sa lettre), donc une étiquette laissée
    en tête de sous-question s'imprimait en double. Elle est retirée d'office."""
    from app.services import indigo_gemini
    comp = _comp(db)
    raw = _composite_raw()
    raw["answer"]["parts"][0]["statement"] = "a. Coche la bonne case."
    raw["answer"]["parts"][1]["statement"] = "b. Donne le PGCD de $A$ et $B$."
    manual = {"number": "63", "statement": raw["statement"],
              "correction": raw["correction_solution"], "has_figure": False}
    valid = indigo_gemini._finalize(raw, comp, db, manual)
    assert valid is not None
    assert [p["statement"] for p in valid["expected"]["parts"]] == [
        "Coche la bonne case.", "Donne le PGCD de $A$ et $B$."]


def test_composite_rejects_forbidden_part_types(db):
    from app.services import exercise_gen
    comp = _comp(db)
    raw = _composite_raw()
    raw["answer"]["parts"][1] = {"response_type": "manual_drawing",
                                 "statement": "Trace la figure.", "answer": {}}
    assert exercise_gen._validate_exercise(raw, comp, db, set(),
                                           allow_geometry_text=True) is None


def test_composite_card_renders_one_zone_per_part():
    """Le composite s'imprime en UNE carte mais produit une zone PAR PARTIE (une
    CopyItem par partie), chacune corrigée par la pipeline existante."""
    import io
    from reportlab.pdfgen import canvas
    from app.services import pdfgen, scoring
    grid_g = {"comparator": "grid", "max_score": 2, "cols": ["Vrai", "Faux"],
              "rows": [{"label": "$2$ divise $A$", "correct": 0}]}
    parts = [
        {"response_type": "checkbox_grid", "statement": "Coche.",
         "grading": scoring.with_bareme(grid_g, "checkbox_grid"),
         "expected": {"type": "grid", "cols": ["Vrai", "Faux"], "rows": grid_g["rows"]}},
        {"response_type": "short_text", "statement": "PGCD ?",
         "grading": scoring.with_bareme({"comparator": "numeric", "max_score": 1}, "short_text"),
         "expected": {"type": "integer", "value": 6}}]
    item = {"kind": "exercise", "response_type": "composite", "item_id": "i0",
            "part_item_ids": ["i0", "i1"], "statement": "Contexte de l'exercice composite.",
            "correction": "Guide.", "grading": {"parts": parts}, "level5": 3,
            "calc": "autorisee", "is_probleme": False, "figure": None}
    c = canvas.Canvas(io.BytesIO())
    zones = pdfgen.render_copy(c, student_name="T", class_name="3e", title="T",
                               assessment_type="DS", items=[item],
                               pages_meta=[{"page_id": "p0", "payload": "MP1|p0|0"}], font_size=9)
    assert [z["item_id"] for z in zones] == ["i0", "i1"]
    assert [z["type"] for z in zones] == ["checkbox_grid", "short_text"]
    assert zones[0]["meta"].get("boxes")               # la grille a ses cases CV


def test_place_figure_marker_never_after_questions():
    from app.services import statement as s
    t = "On considère le triangle $ABC$.\na. Longueur de $AB$ ? {{blank}}\n{{figure}}"
    out = s.place_figure_marker(t, True)
    lines = out.split("\n")
    assert lines.index("{{figure}}") < next(i for i, ln in enumerate(lines)
                                            if s.subquestion_label(ln))
    # sans figure disponible : marqueur parasite retiré
    assert "{{figure}}" not in s.place_figure_marker("Texte\n{{figure}}", False)


def test_regenerate_leaves_exercise_unchanged_when_llm_fails(db, monkeypatch):
    """La régénération ne DÉGRADE jamais : si l'adaptation échoue (renvoie None),
    l'exercice existant est laissé tel quel (jamais réécrit en repli OCR brut)."""
    from app.services import indigo, indigo_gemini
    c = _comp(db)
    row = IndigoExercise(id="rg1", extraction_id="e", competency_id=c.id, grade_level="3e",
                         source_page=0, source_number="1", order_index=0,
                         badge_type="exercice", difficulty=3, calculator="autorisee",
                         status="validated", response_type="qcm_single",
                         statement="Énoncé adapté.", correction_solution="Corrigé.",
                         correction_guide="Guide.",
                         raw_ocr_json={"statement": "OCR brut", "correction": "", "adapted": True})
    db.add(row)
    db.commit()
    monkeypatch.setattr(indigo_gemini, "adapt_one", lambda *a, **k: None)
    out = indigo.regenerate_exercises(db, ["rg1"])
    assert out == {"regenerated": 0, "failed": 1}
    again = db.get(IndigoExercise, "rg1")
    assert again.statement == "Énoncé adapté." and again.status == "validated"


def test_adapt_batch_splits_in_half_instead_of_going_solo(db, monkeypatch):
    """Incident A1.3 (02/08) : un lot en échec repartait aussitôt en N appels
    UNITAIRES — 5 à 7 fois plus d'appels, plafond de dépense quotidien épuisé au
    milieu de l'extraction. Un lot en échec est maintenant COUPÉ EN DEUX."""
    from app.services import indigo_gemini
    comp = _comp(db)
    sizes: list[int] = []

    def fake_call(db_, stage, system, payload, correlation_id):
        n = len(payload["exercises_to_adapt"])
        sizes.append(n)
        if n > 1:
            raise ValueError("Réponse Claude JSON TRONQUÉE (test)")
        return {"exercises": []}          # feuille : appel OK, sortie vide

    monkeypatch.setattr(indigo_gemini.indigo_llm, "call", fake_call)
    manuals = [{"number": str(i), "statement": "x", "correction": "y"} for i in range(4)]
    errors: list[str] = []
    assert indigo_gemini.adapt_batch(db, comp, "3e", manuals, errors) == {}
    # 4 -> 2 + 2 -> 1+1 + 1+1 : jamais 4 appels unitaires d'emblée
    assert sizes == [4, 2, 1, 1, 2, 1, 1]
    assert errors == []          # récupéré par le découpage : rien à signaler

    # même lot, mais l'appel échoue AUSSI à l'unité : la cause est collectée pour
    # être affichée (c'est son absence qui rendait l'incident indéchiffrable)
    monkeypatch.setattr(indigo_gemini.indigo_llm, "call",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("API HS")))
    assert indigo_gemini.adapt_batch(db, comp, "3e", manuals, errors) == {}
    assert len(errors) == 4 and all("API HS" in e for e in errors)


def test_budget_exceeded_stops_the_run_instead_of_silently_failing(db, monkeypatch):
    """Cause RACINE de « 1/21 adapté » : le plafond de dépense atteint faisait
    échouer chaque appel suivant, en silence. Il doit maintenant REMONTER (arrêt
    net) au lieu d'être avalé exercice par exercice."""
    from app.services import indigo_gemini, providers
    comp = _comp(db)

    def broke(db_, stage, system, payload, correlation_id):
        raise providers.BudgetExceeded("Budget anthropic quotidien atteint")

    monkeypatch.setattr(indigo_gemini.indigo_llm, "call", broke)
    manuals = [{"number": "1", "statement": "x", "correction": "y"}]
    for fn in (lambda: indigo_gemini.adapt_batch(db, comp, "3e", manuals),
               lambda: indigo_gemini.adapt_one(db, comp, "3e", manuals[0])):
        try:
            fn()
        except providers.BudgetExceeded:
            continue
        raise AssertionError("BudgetExceeded doit remonter, pas être avalé")


def test_budget_state_reports_spend_and_cap(db):
    from app.services import providers
    from app.models import ApiUsageEvent
    db.add(ApiUsageEvent(provider="anthropic", model="claude-sonnet-5",
                         operation="indigo_adapt", estimated_cost=0.75))
    db.commit()
    spent, cap = providers.budget_state(db, "anthropic")
    assert spent == 0.75 and cap == settings.llm_daily_cost_limit_eur


def test_review_prompt_wraps_shared_contract(db):
    """Le prompt de relecture (contenu ÉDITABLE dans prompts/indigo/verification.txt)
    reste ENVELOPPÉ par le contrat partagé que le CODE ajoute (mêmes formats/règles
    que l'adaptateur, sinon rejets silencieux) + l'ajout de schéma Indigo. On teste
    la plomberie, pas la prose (que l'utilisateur règle librement)."""
    from app.services import indigo_verify
    comp = _comp(db)
    sys_prompt = indigo_verify._system_prompt(db, comp, "3e")
    # menu de formats + règle de case + LaTeX : viennent de format_contract (code)
    assert "QCM UNIQUE" in sys_prompt and "TABLEAU À REMPLIR" in sys_prompt
    assert "{{blank}}" in sys_prompt and "\\times" in sys_prompt
    assert "source_number" in sys_prompt          # ajout de schéma Indigo (code)
    for ph in ("§GRADE§", "§COMPETENCY§", "§COMPETENCY_TREE§"):
        assert ph not in sys_prompt               # placeholders bien substitués


def _adapted_valid(db, comp, number="34"):
    """Un exercice adapté (contrat interne enrichi + `_raw`), tel que
    indigo_gemini.adapt_batch le produit, obtenu via le VRAI validateur."""
    from app.services import indigo_gemini
    raw = {"kind": "application", "bareme_points": 1,
           "statement": "Calcule $2+3$ : {{blank}}", "response_type": "short_text",
           "answer": {"type": "integer", "value": 5},
           "correction": "Additionne les deux nombres.",
           "correction_solution": "$2 + 3 = 5$.", "source_number": number,
           "needs_figure": False}
    manual = {"number": number, "statement": raw["statement"],
              "correction": raw["correction_solution"], "has_figure": False}
    valid = indigo_gemini._finalize(raw, comp, db, manual)
    assert valid is not None and "_raw" in valid       # le contrat brut est archivé
    return manual, valid


def test_review_offline_keeps_adapted_and_strips_raw(db):
    """Hors ligne (mock DeepSeek sans sortie de relecture), la relecture ne change
    RIEN : chaque exercice garde sa version adaptée, `_raw` retiré (jamais 0
    exercice, jamais un exercice dégradé)."""
    from app.services import indigo_verify
    comp = _comp(db)
    manual, valid = _adapted_valid(db, comp, "34")
    before = valid["statement"]
    out = indigo_verify.review(db, comp, "3e", [(None, manual, valid)])
    assert set(out) == {"34"}
    assert out["34"]["statement"] == before
    assert "_raw" not in out["34"]


def test_review_replaces_with_corrected_when_it_revalidates(db, monkeypatch):
    """Quand la relecture renvoie une version CORRIGÉE qui repasse le validateur,
    elle remplace la version adaptée ; format strict conservé en entrée/sortie."""
    from app.services import indigo_verify
    comp = _comp(db)
    manual, valid = _adapted_valid(db, comp, "34")

    def fake_call(db_, stage, system, payload, correlation_id):
        assert stage == "review"                        # aiguillé sur la bonne étape
        item = payload["exercises_to_review"][0]
        assert item["source_number"] == "34"           # même format à l'entrée
        assert item["source_statement"] == manual["statement"]   # la SOURCE est transmise
        assert item["answer"]["value"] == 5             # le relecteur VOIT la réponse attendue
        corrected = dict(item)
        corrected["statement"] = "Calcule la somme $2 + 3$. {{blank}}"   # formulation clarifiée
        for k in ("has_figure", "source_statement", "source_correction"):
            corrected.pop(k, None)                      # champs d'entrée, non réémis
        return {"exercises": [corrected]}

    monkeypatch.setattr(indigo_verify.indigo_llm, "call", fake_call)
    out = indigo_verify.review(db, comp, "3e", [(None, manual, valid)])
    assert "somme" in out["34"]["statement"]           # version relue adoptée
    assert out["34"]["response_type"] == "short_text"
    assert out["34"]["expected"]["value"] == 5         # réponse préservée
    assert "_raw" not in out["34"]


def test_review_keeps_original_when_corrected_fails_validation(db, monkeypatch):
    """Si la relecture casse un exercice (sortie non conforme au validateur), on
    GARDE la version adaptée : la relecture ne dégrade jamais."""
    from app.services import indigo_verify
    comp = _comp(db)
    manual, valid = _adapted_valid(db, comp, "34")
    before = valid["statement"]

    def broken(db_, stage, system, payload, correlation_id):
        return {"exercises": [{"source_number": "34", "response_type": "short_text",
                               "statement": "x", "answer": {}}]}   # trop court, answer vide

    monkeypatch.setattr(indigo_verify.indigo_llm, "call", broken)
    out = indigo_verify.review(db, comp, "3e", [(None, manual, valid)])
    assert out["34"]["statement"] == before            # inchangé
    assert "_raw" not in out["34"]


def test_review_disabled_is_noop(db, monkeypatch):
    """indigo_review_enabled=False : aucune relecture, aucun appel LLM, `_raw`
    retiré quand même."""
    from app.services import indigo_verify
    monkeypatch.setattr(settings, "indigo_review_enabled", False)
    monkeypatch.setattr(indigo_verify.indigo_llm, "call",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("appel LLM interdit")))
    comp = _comp(db)
    manual, valid = _adapted_valid(db, comp, "34")
    out = indigo_verify.review(db, comp, "3e", [(None, manual, valid)])
    assert set(out) == {"34"} and "_raw" not in out["34"]


def test_collect_exercises_keeps_gemini_only_number(db, monkeypatch):
    """Un exercice que la géométrie n'a pas vu (OCR fusionné) mais que le
    pré-découpage Gemini retrouve est CONSERVÉ, sans crop ; le texte Gemini est
    préféré au texte géométrique quand les deux existent."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("text", "34 Calcule A.", 40, 100, 400, 130),
                _block("text", "35 Calcule B.", 40, 140, 400, 170),
            ]}
    monkeypatch.setattr(indigo.indigo_segment, "segment_statements",
                        lambda *a, **k: {"34": "Calcule A propre.", "36": "Exercice fusionné."})
    exos = indigo._collect_exercises(db, T, [T], "3e", [page], [4],
                                     [34, 35, 36], {34, 35, 36}, lambda *a, **k: None)
    assert [e["number"] for e in exos] == ["34", "35", "36"]
    g34 = next(e for e in exos if e["number"] == "34")
    assert g34["text"] == "Calcule A propre." and g34["blocks"]       # géométrie + texte Gemini
    g35 = next(e for e in exos if e["number"] == "35")
    assert "Calcule B." in g35["text"] and g35["blocks"]              # géométrie seule (texte flatten)
    g36 = next(e for e in exos if e["number"] == "36")
    assert g36["blocks"] == [] and g36["text"] == "Exercice fusionné."  # placeholder sans crop


def test_segment_statements_falls_back_offline(db):
    """Hors ligne (mock Gemini sans numéros), le pré-découpage rend {} → repli
    géométrie côté appelant (aucune exception)."""
    from app.services import indigo_segment
    comp = type("C", (), {"code": "A1.1", "short_id": "A1.1", "label": "Diviseurs"})()
    out = indigo_segment.segment_statements(
        db, comp, "3e", [(4, "34 Calcule A.\n35 Calcule B.")], [34, 35])
    assert isinstance(out, dict)


def test_collect_corrections_keeps_gemini_only_number(db, monkeypatch):
    """Même politique que les énoncés (cf. test_collect_exercises_keeps_gemini_
    only_number), appliquée cette fois au manuel PROF : un corrigé que la
    géométrie n'a pas vu (OCR fusionné) mais que le pré-découpage Gemini
    retrouve est CONSERVÉ ; le texte Gemini est préféré au texte géométrique
    quand les deux existent."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("text", "34 Réponse : 12.", 40, 100, 400, 130),
                _block("text", "35 Réponse : 7.", 40, 140, 400, 170),
            ]}
    monkeypatch.setattr(indigo.indigo_segment, "segment_corrections",
                        lambda *a, **k: {"34": "Réponse propre : 12.",
                                         "36": "Corrigé fusionné : 9."})
    corr = indigo._collect_corrections(db, T, "3e", [page], [34, 35, 36], {34, 35, 36},
                                       lambda *a, **k: None)
    assert corr["34"] == "Réponse propre : 12."      # géométrie + texte Gemini préféré
    assert "Réponse : 7." in corr["35"]               # géométrie seule (flatten)
    assert corr["36"] == "Corrigé fusionné : 9."      # reconnu par Gemini seul


def test_collect_corrections_offline_falls_back_to_geometry(db):
    """Sans Gemini exploitable (repli {}), on garde le découpage géométrique —
    aucun corrigé perdu, aucune exception."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [_block("text", "34 Réponse : 12.", 40, 100, 400, 130)]}
    corr = indigo._collect_corrections(db, T, "3e", [page], [34], {34}, lambda *a, **k: None)
    assert "Réponse : 12." in corr["34"]


def test_segment_corrections_falls_back_offline(db):
    """Hors ligne (mock Gemini sans numéros), le pré-découpage des corrigés
    rend {} → repli géométrie côté appelant (aucune exception)."""
    from app.services import indigo_segment
    comp = type("C", (), {"code": "A1.1", "short_id": "A1.1", "label": "Diviseurs"})()
    out = indigo_segment.segment_corrections(
        db, comp, "3e", [(4, "34 Réponse : 12.\n35 Réponse : 7.")], [34, 35])
    assert isinstance(out, dict)


def test_enrich_detects_probleme_via_tags_and_figure():
    T = _C("t", "Cible")
    page = {"source_page": 7, "dims": {"width": 1000, "height": 1400},
            "blocks": [
                _block("text", "8 Le partage", 40, 100, 400, 130),
                _block("text", "Chercher Calculer", 40, 135, 400, 155),
                _block("text", "Un jardinier plante des fleurs...", 40, 160, 400, 220),
                _block("image", "", 420, 100, 700, 300),
            ]}
    ex = indigo._segment_target(page, T, [T])[0]
    assert ex["is_probleme"] is True
    assert "Chercher" in ex["tags"] and "Calculer" in ex["tags"]
    assert ex["has_figure"] is True
    assert ex["title"] == "Le partage"
    assert "Chercher" not in ex["text"]          # la ligne de marqueurs est retirée de l'énoncé


def test_competence_line_detects_markers_not_statement_verbs():
    assert indigo._competence_line("Raisonner, Calculer") == ["Raisonner", "Calculer"]
    assert indigo._competence_line("Modéliser") == ["Modéliser"]
    assert indigo._competence_line("Chercher Communiquer") == ["Chercher", "Communiquer"]
    # un énoncé qui commence par un verbe impératif n'est PAS une ligne de marqueurs
    assert indigo._competence_line("Calculer le PGCD de 24 et 36") == []
    assert indigo._competence_line("Calcule A puis range dans l'ordre") == []


def test_enrich_regular_exercise_with_verb_is_not_probleme():
    """« Calculer » AU FIL d'un énoncé ordinaire ne fait pas un problème (seule
    une LIGNE de marqueurs après le titre le fait) — corrige les faux problèmes."""
    T = _C("t", "Cible")
    page = {"source_page": 5, "dims": {"width": 1000, "height": 1400},
            "blocks": [_block("text", "50 Calculer le PGCD de 24 et 36.", 40, 100, 400, 140)]}
    ex = indigo._segment_target(page, T, [T])[0]
    assert ex["is_probleme"] is False
    assert ex["tags"] == [] and ex["title"] == ""


def test_persist_guide_never_equals_solution(db):
    # Gemini a recopié la solution dans le guide -> guide remplacé (jamais le corrigé)
    row = IndigoExercise(id="x1", extraction_id="e", competency_id="c", grade_level="3e",
                         source_page=1, source_number="50", order_index=0)
    manual = {"number": "50", "statement": "Calcule.", "correction": "Corrigé prof complet."}
    valid = {"statement": "Calcule $2+2$.", "response_type": "short_text", "expected": {},
             "grading": {}, "correction": "Résultat : 4.", "correction_solution": "Résultat : 4."}
    indigo._persist_exercise(db, row, manual, valid)
    assert row.correction_solution == "Résultat : 4."
    assert row.correction_guide != row.correction_solution
    # adaptation échouée -> corrigé = solution du manuel, mais guide != solution
    row2 = IndigoExercise(id="x2", extraction_id="e", competency_id="c", grade_level="3e",
                          source_page=1, source_number="51", order_index=1)
    manual2 = {"number": "51", "statement": "Range.", "correction": "La vraie solution."}
    indigo._persist_exercise(db, row2, manual2, None)
    assert row2.correction_solution == "La vraie solution."
    assert row2.correction_guide != "La vraie solution."


def test_exercise_out_flags_unadapted_fallback(db):
    """Un exercice non adapté (repli OCR brut, valid=None) est SIGNALÉ
    `adapted:false` — c'est ce drapeau qui distingue « adaptation LLM échouée »
    (clé Anthropic absente / budget / erreur) d'une « mauvaise génération »."""
    c = _comp(db)
    row = IndigoExercise(id="a1", extraction_id="e", competency_id=c.id, grade_level="3e",
                         source_page=1, source_number="14", order_index=0)
    indigo._persist_exercise(db, row, {"number": "14", "statement": "Donne les diviseurs de 12.",
                                       "correction": ""}, None)
    db.commit()
    out = indigo.exercise_out(db, row)
    assert out["adapted"] is False                 # repli OCR brut, visible
    assert out["response_type"] == "short_text"    # signature du repli

    row2 = IndigoExercise(id="a2", extraction_id="e", competency_id=c.id, grade_level="3e",
                          source_page=1, source_number="15", order_index=1)
    valid = {"statement": "Coche les diviseurs de $12$.", "response_type": "qcm_single",
             "expected": {"type": "choice", "correct": [0]},
             "grading": {"comparator": "qcm", "max_score": 1, "choices": ["$2$", "$5$"]},
             "correction": "Un diviseur divise sans reste.", "correction_solution": "$2$"}
    indigo._persist_exercise(db, row2, {"number": "15", "statement": "x", "correction": ""}, valid)
    db.commit()
    assert indigo.exercise_out(db, row2)["adapted"] is True


def test_bareme_lives_only_in_grading_json(db):
    """`bareme_points` est le SEUL barème, et il vit dans grading_json — plus de
    colonne parallèle (`effort_points`) que la banque ignorait et qui pouvait
    diverger de la note réellement calculée."""
    c = _comp(db)
    row = IndigoExercise(id="b1", extraction_id="e", competency_id=c.id, grade_level="3e",
                         source_page=1, source_number="20", order_index=0)
    valid = {"statement": "Coche les nombres pairs.", "response_type": "qcm_multiple",
             "expected": {"type": "choice", "correct": [0, 2]},
             "grading": {"comparator": "qcm", "max_score": 4, "bareme_points": 0.625,
                         "choices": ["$2$", "$3$", "$8$", "$5$"]},
             "correction": "Un nombre pair finit par 0, 2, 4, 6 ou 8.",
             "correction_solution": "$2$ et $8$"}
    indigo._persist_exercise(db, row, {"number": "20", "statement": "x", "correction": ""}, valid)
    db.commit()
    assert not hasattr(row, "effort_points")
    assert row.grading_json["bareme_points"] == 0.625
    assert indigo.exercise_out(db, row)["bareme_points"] == 0.625

    # édition manuelle : le barème s'écrit LÀ où il vit, calé sur le pas de 0,125
    indigo.update_exercise(db, row, {"bareme_points": 1.3})
    assert row.grading_json["bareme_points"] == 1.25
    assert row.grading_json["choices"] == ["$2$", "$3$", "$8$", "$5$"]   # rien d'autre perdu


def test_exercise_without_bareme_is_repaired_not_worth_zero(db):
    """Un exercice réécrit par un correctif qui n'a pas reposé le barème (c'est
    arrivé : 4 QCM de la banque) vaut son repli, jamais 0 en silence."""
    c = _comp(db)
    row = IndigoExercise(id="b2", extraction_id="e", competency_id=c.id, grade_level="3e",
                         source_page=1, source_number="21", order_index=1)
    valid = {"statement": "Coche les multiples de $3$.", "response_type": "qcm_multiple",
             "expected": {"type": "choice", "correct": [1]},
             "grading": {"comparator": "qcm", "max_score": 3,
                         "choices": ["$4$", "$9$", "$10$"]},   # pas de bareme_points
             "correction": "Un multiple de 3 a une somme de chiffres multiple de 3.",
             "correction_solution": "$9$"}
    indigo._persist_exercise(db, row, {"number": "21", "statement": "x", "correction": ""}, valid)
    db.commit()
    assert indigo.exercise_out(db, row)["bareme_points"] == 0.75   # 3 cases × 0,25


def test_persist_applies_field_engine_mini_case(db):
    # une réponse entière courte COLLÉE À UNE FORMULE (équation à trous, cf.
    # indigo_fields._adjacent_to_formula) -> mini-case posée à l'enregistrement
    row = IndigoExercise(id="f1", extraction_id="e", competency_id="c", grade_level="3e",
                         source_page=1, source_number="7", order_index=0)
    manual = {"number": "7", "statement": "Le PGCD.", "correction": "12"}
    valid = {"statement": "$\\text{PGCD}(24, 36) =${{blank}}", "response_type": "short_text",
             "expected": {"type": "integer", "value": 12, "inline": True},
             "grading": {"max_score": 1, "comparator": "numeric"},
             "correction": "Rappelle la décomposition.", "correction_solution": "12"}
    indigo._persist_exercise(db, row, manual, valid)
    assert "{{mini}}" in row.statement and "{{blank}}" not in row.statement


def test_persist_isolated_short_integer_stays_standard(db):
    # même réponse courte, mais la case n'est PAS collée à une formule : reste
    # une case standard (mini réservée aux équations à trous, demande utilisateur)
    row = IndigoExercise(id="f2", extraction_id="e", competency_id="c", grade_level="3e",
                         source_page=1, source_number="8", order_index=0)
    manual = {"number": "8", "statement": "Le PGCD.", "correction": "12"}
    valid = {"statement": "Le PGCD de 24 et 36 est {{blank}}", "response_type": "short_text",
             "expected": {"type": "integer", "value": 12, "inline": True},
             "grading": {"max_score": 1, "comparator": "numeric"},
             "correction": "Rappelle la décomposition.", "correction_solution": "12"}
    indigo._persist_exercise(db, row, manual, valid)
    assert "{{blank}}" in row.statement and "{{mini}}" not in row.statement


def test_list_exercises_orders_by_numeric_exercise_number(db):
    comp = _comp(db)
    # insérés dans le désordre, numéros à un et deux chiffres
    for i, (num, page) in enumerate([("10", 2), ("2", 1), ("1", 1), ("21", 3)]):
        db.add(IndigoExercise(id=f"e{i}", extraction_id="x", competency_id=comp.id,
                              grade_level="3e", source_page=page, source_number=num,
                              order_index=i))
    db.commit()
    got = [ex.source_number for ex in indigo.list_exercises(db, competency_id=comp.id)]
    # tri NUMÉRIQUE (pas lexicographique « 10 » avant « 2 »)
    assert got == ["1", "2", "10", "21"]


# ------------------------------------------------------- publication (bake+seed)

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(indigo, "_PUB_DIR", tmp_path / "pub")   # pas d'écriture dans le repo
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _comp(db):
    fw = CompetencyFramework(grade_level="3e", name="T")
    db.add(fw); db.flush()
    c = Competency(framework_id=fw.id, code="A1.1", short_id="A1.1", label="Diviseurs",
                   domain_code="A", domain_name="Nombres", chapter_code="A1",
                   chapter_name="Nombres entiers", order_index=1)
    db.add(c); db.commit()
    return c


def test_publish_seed_and_bank_source(db, tmp_path):
    c = _comp(db)
    # crop + figure sur disque (comme le pipeline les produit)
    (tmp_path / "indigo" / "drafts").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "indigo" / "drafts" / "x.png"), np.full((40, 60, 3), 210, np.uint8))
    cv2.imwrite(str(tmp_path / "indigo" / "drafts" / "x_fig.png"), np.full((30, 30, 3), 180, np.uint8))
    db.add(IndigoExercise(
        id="x", competency_id=c.id, grade_level="3e", source_number="12",
        badge_type="probleme", difficulty=4, calculator="interdite", title="Le partage",
        statement="Combien de parts ?", response_type="short_text",
        expected_json={"type": "integer", "value": 4}, grading_json={"comparator": "numeric", "max_score": 1},
        correction_guide="Attention aux unités.", correction_solution="4 parts.",
        status="validated", crop_path="indigo/drafts/x.png",
        has_figure=True, figure_path="indigo/drafts/x_fig.png"))
    db.commit()

    res = indigo.publish(db)
    assert res == {"published": 1, "seeded": 1}
    assert indigo._pub_paths()[3].exists()                      # exercises.json
    assert (indigo._PUB_DIR / "figures" / "x.png").exists()     # figure copiée

    ge = db.query(GeneratedExercise).filter_by(source="indigo").one()
    assert ge.kind == "probleme"
    assert ge.figure_json["type"] == "image"                    # figure = crop image
    assert (ge.raw_extract_json["indigo"]["calculator"]) == "interdite"

    # sélectionnable comme source de sujet
    pool = exercise_gen.ensure_bank(db, c, 4, source="indigo")
    assert len(pool) == 1 and pool[0].id == "x"


def test_delete_exercises_for_competency_wipes_all(db, tmp_path):
    """« Tout supprimer » : retire TOUS les exercices d'une compétence (brouillons
    ET validés), nettoie les crops, la banque et le fichier versionné."""
    c = _comp(db)
    (tmp_path / "indigo" / "drafts").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "indigo" / "drafts" / "d1.png"), np.full((10, 10, 3), 200, np.uint8))
    db.add(IndigoExercise(id="d1", competency_id=c.id, grade_level="3e", source_number="1",
                          statement="Q1", response_type="short_text",
                          expected_json={"type": "integer", "value": 1},
                          status="validated", crop_path="indigo/drafts/d1.png"))
    db.add(IndigoExercise(id="d2", competency_id=c.id, grade_level="3e", source_number="2",
                          statement="Q2", response_type="short_text",
                          expected_json={"type": "integer", "value": 2}, status="draft"))
    db.commit()
    indigo.publish(db)                                   # d1 (validé) -> banque + fichier versionné
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 1

    n = indigo.delete_exercises_for_competency(db, c.id)
    assert n == 2
    assert db.query(IndigoExercise).count() == 0
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 0   # dé-publié
    assert not (tmp_path / "indigo" / "drafts" / "d1.png").exists()               # crop nettoyé
    assert indigo.load_published().get("exercises") == []                         # fichier vidé
    assert indigo.delete_exercises_for_competency(db, "inexistante") == 0         # sûr si vide


def test_seed_resolves_competency_by_code_not_id(db, tmp_path):
    """Les ids de compétence sont régénérés par déploiement : le seed doit
    résoudre par CODE, pas par l'id stocké à la publication."""
    c = _comp(db)
    (tmp_path / "indigo" / "drafts").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "indigo" / "drafts" / "y.png"), np.full((40, 60, 3), 210, np.uint8))
    db.add(IndigoExercise(id="y", competency_id=c.id, grade_level="3e", statement="Q",
                          response_type="short_text", status="validated",
                          crop_path="indigo/drafts/y.png"))
    db.commit()
    indigo.publish(db)
    # nouvelle DB "déploiement" : même compétence, id DIFFÉRENT
    eng2 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng2)
    from sqlalchemy.orm import sessionmaker as sm
    db2 = sm(bind=eng2)()
    _comp(db2)
    assert indigo.seed_published(db2) == 1
    assert db2.query(GeneratedExercise).filter_by(source="indigo").count() == 1


def test_delete_exercise_unpublishes_from_bank_and_file(db, tmp_path):
    """Supprimer un brouillon déjà publié doit le retirer de la banque LIVE
    (GeneratedExercise) et du fichier versionné — sinon il reste servi aux
    élèves et seed_published le resème tel quel au prochain démarrage (cf.
    demande utilisateur : câblage banque <-> onglet Exercices)."""
    c = _comp(db)
    (tmp_path / "indigo" / "drafts").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "indigo" / "drafts" / "z.png"), np.full((40, 60, 3), 210, np.uint8))
    db.add(IndigoExercise(
        id="z", competency_id=c.id, grade_level="3e", statement="Q",
        response_type="short_text", status="validated", crop_path="indigo/drafts/z.png"))
    db.commit()

    indigo.publish(db)
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 1
    assert len(indigo.load_published()["exercises"]) == 1

    indigo.delete_exercise(db, db.get(IndigoExercise, "z"))

    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 0
    assert indigo.load_published()["exercises"] == []
    # une purge/redémarrage ne doit pas le faire réapparaître
    assert indigo.seed_published(db) == 0


def test_delete_exercise_never_published_is_a_noop_on_file(db):
    """Un brouillon jamais publié se supprime sans toucher au fichier versionné
    (pas de fichier à réécrire, pas d'erreur)."""
    c = _comp(db)
    db.add(IndigoExercise(id="w", competency_id=c.id, grade_level="3e", statement="Q",
                          response_type="short_text", status="draft"))
    db.commit()
    indigo.delete_exercise(db, db.get(IndigoExercise, "w"))
    assert db.get(IndigoExercise, "w") is None


def test_render_figure_image_type(tmp_path):
    p = tmp_path / "f.png"
    cv2.imwrite(str(p), np.full((20, 20, 3), 128, np.uint8))
    out = figures.render_figure({"type": "image", "params": {"path": str(p)}})
    assert out[:4] == b"\x89PNG"
    assert figures.validate_figure({"type": "image", "params": {"path": str(p)}}) is not None


def test_calc_icon_draws_without_error():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    c = canvas.Canvas("/dev/null", pagesize=A4)
    pdfgen._draw_calc_icon(c, 100 * mm, 200 * mm, 3.6 * mm, forbidden=False)
    pdfgen._draw_calc_icon(c, 100 * mm, 200 * mm, 3.6 * mm, forbidden=True)


# ------------------------------------------------ rédaction / mise en page (bug 30/07)

def test_normalize_repairs_times_tab_wrapped_blank_and_spacing():
    """Le rapport de bug Indigo : « \\times » cassé en tabulation par un JSON mal
    échappé, case « {{blank}} » enveloppée de $ et d'accolades, espaces manquants."""
    corrupted = ("Complète :\n"
                 "- $60 = 2 \times${{${{blank}}$}}\n"
                 "-$60 = 3 \times${{${{blank}}$}}\n"
                 "Le nombre$60$ possède {{blank}} diviseurs.")
    from app.services import statement as s
    out = s.normalize(corrupted)
    assert "\\times" in out and "\t" not in out           # tabulation -> \times
    assert "{{${{blank}}$}}" not in out                    # plus d'enveloppe parasite
    assert out.count("{{blank}}") == 3
    # une ligne ne commence jamais par « - » : la puce est convertie en « • »
    assert "• $60 = 2 \\times${{blank}}" in out            # puce, case collée à la formule
    assert not any(ln.startswith("-") for ln in out.split("\n"))
    assert "Le nombre $60$ possède" in out                 # espace avant la formule


def test_repair_latex_control_chars_only_inside_math():
    from app.services import statement as s
    # \frac cassé (form-feed) dans une formule -> restauré
    assert s.repair_latex_control_chars("$\x0crac{1}{2}$") == "$\\frac{1}{2}$"
    # un vrai saut de ligne hors formule n'est jamais touché
    assert s.repair_latex_control_chars("Ligne 1\nLigne 2") == "Ligne 1\nLigne 2"


def test_indigo_prompts_externalized_and_wrapped(db):
    """Les prompts Indigo sont chargés depuis prompts/indigo/*.txt (contenu
    ÉDITABLE) puis enveloppés par le contrat partagé (code). On teste la PLOMBERIE,
    pas la prose (que l'utilisateur règle librement) : chaque fichier se charge et
    n'est pas vide ; le prompt système assemblé porte le contrat (menu de formats,
    case, LaTeX, ajout de schéma) et ses placeholders sont substitués."""
    from app.services import indigo_gemini, indigo_segment, indigo_verify
    for loader in (indigo_gemini._intro, indigo_verify._review_intro,
                   indigo_segment._system_statements, indigo_segment._system_corrections):
        assert loader().strip(), f"prompt vide: {loader.__name__}"
    comp = _comp(db)
    sp = indigo_gemini._system_prompt(db, comp, "3e")
    assert "QCM UNIQUE" in sp and "{{blank}}" in sp and "\\times" in sp
    assert "source_number" in sp
    for ph in ("§GRADE§", "§COMPETENCY§", "§COMPETENCY_TREE§"):
        assert ph not in sp


def test_indigo_prompt_carries_priority_order_and_allows_drawing(db):
    """L'ordre de priorité Indigo (cf. prompts/indigo/generation.txt) doit
    ARRIVER au modèle, et il PRÉVAUT sur le menu partagé qui le suit — lequel
    présente encore `matching` et `manual_drawing` comme des derniers recours.
    Les règles de géométrie sont celles d'Indigo (le tracé est autorisé), pas
    celles du contrat partagé (« l'élève ne trace JAMAIS rien »)."""
    from app.services import indigo_gemini, indigo_verify
    comp = _comp(db)
    for sp in (indigo_gemini._system_prompt(db, comp, "3e"),
               indigo_verify._system_prompt(db, comp, "3e")):
        assert "manual_drawing" in sp
        assert "ne trace, ne construit" not in sp        # règle géométrie partagée écartée
        assert "échelon de l'ordre de priorité" in sp
    sp = indigo_gemini._system_prompt(db, comp, "3e")
    for fmt in ("qcm_single", "checkbox_grid", "matching", "short_text",
                "table_fill", "multi_blank", "multiline_text", "manual_drawing"):
        assert fmt in sp
    assert "PRÉVAUT" in sp


def test_manual_drawing_is_no_longer_refused(db):
    """Le tracé/dessin est de nouveau un format valide côté Indigo (priorité 7) :
    l'adaptation ne le rejette plus."""
    from app.services import indigo_gemini
    comp = _comp(db)
    raw = {"kind": "application", "bareme_points": 1,
           "statement": "Construis la médiatrice du segment $[AB]$ ci-contre.",
           "response_type": "manual_drawing",
           "correction": "Reporte la même ouverture de compas de part et d'autre.",
           "correction_solution": "Deux arcs de même rayon depuis $A$ et $B$.",
           "source_number": "41", "needs_figure": True}
    valid = indigo_gemini._finalize(raw, comp, db, {"number": "41", "has_figure": True})
    assert valid is not None and valid["response_type"] == "manual_drawing"


def test_persist_adds_missing_answer_field(db):
    """Filet déterministe à l'enregistrement : une réponse courte SANS case en
    reçoit une, une case orpheline dans un format à zone dessinée est retirée."""
    row = IndigoExercise(id="fx1", extraction_id="e", competency_id="c", grade_level="3e",
                         source_page=1, source_number="9", order_index=0)
    valid = {"statement": "Calcule $17 \\times 14$.", "response_type": "short_text",
             "expected": {"type": "integer", "value": 238},
             "grading": {"max_score": 1, "comparator": "numeric"},
             "correction": "Pose la multiplication.", "correction_solution": "$238$"}
    indigo._persist_exercise(db, row, {"number": "9", "statement": "x", "correction": ""}, valid)
    assert row.statement.endswith("{{blank}}")
    assert row.expected_json["inline"] is True

    row2 = IndigoExercise(id="fx2", extraction_id="e", competency_id="c", grade_level="3e",
                          source_page=1, source_number="10", order_index=1)
    valid2 = {"statement": "Coche les diviseurs de $12$. {{blank}}",
              "response_type": "qcm_multiple",
              "expected": {"type": "choice", "correct": [0]},
              "grading": {"max_score": 1, "comparator": "qcm", "choices": ["$2$", "$5$"]},
              "correction": "Un diviseur divise sans reste.", "correction_solution": "$2$"}
    indigo._persist_exercise(db, row2, {"number": "10", "statement": "x", "correction": ""}, valid2)
    assert "{{blank}}" not in row2.statement


def test_mathtext_accepts_short_inequality_commands():
    """\\le / \\ge (formes courtes, fréquentes dans les corrigés de division)
    étaient refusées car mathtext ne connaît que \\leq / \\geq."""
    from app.services import mathrender
    assert mathrender.sanitize_latex(r"0 \le r < b") is not None
    assert mathrender.sanitize_latex(r"x \ge 5") is not None
    assert mathrender.sanitize_latex(r"a \leq b") is not None   # forme longue inchangée


def test_normalize_keeps_blank_between_two_formulas():
    """Bug multi_blank : une case propre ENTRE deux formules ($34$ … {{blank}}
    … $85$) était emballée à tort (le $ fermant de l'une + le $ ouvrant de
    l'autre lus comme un span). La réparation est désormais consciente des spans."""
    from app.services import statement as s
    raw = "a. Le nombre $34$ ? {{blank}}\nb. Le nombre $85$ ? {{blank}}"
    assert s.normalize(raw) == raw
    # la case DANS une vraie formule reste extraite ; le double-wrap réel réparé
    assert s.normalize("Le nombre $85blank$.") == "Le nombre $85${{blank}}."
    assert "{{blank}}" in s.normalize(r"$60 = 2 \times${{${{blank}}$}}")
    assert "${{blank}}$" not in s.normalize(r"$60 = 2 \times${{${{blank}}$}}")


def test_strip_leading_number_only_removes_that_number():
    from app.services import statement as s
    assert s.strip_leading_number("13 Range les nombres.", "13") == "Range les nombres."
    assert s.strip_leading_number("13. Range les nombres.", 13) == "Range les nombres."
    # un autre nombre en tête n'est PAS le numéro de l'exercice : on n'y touche pas
    assert s.strip_leading_number("12 élèves partent.", "13") == "12 élèves partent."
    assert s.strip_leading_number("Range les nombres.", "13") == "Range les nombres."


def test_normalize_breaks_numbered_subquestions():
    """« 1. … 2. … » : chaque sous-question numérotée sur sa propre ligne."""
    from app.services import statement as s
    out = s.normalize("Calcule. 1. $2+3$ 2. $4+5$")
    assert out == "Calcule.\n1. $2+3$\n2. $4+5$"
    # étiquette numérotée reconnue comme sous-question (pastille)
    assert s.subquestion_label("1. $2+3$") == ("1", "$2+3$")


def test_normalize_never_starts_line_with_dash():
    from app.services import statement as s
    out = s.normalize("Étapes :\n- Jour 1\n- Jour 2")
    assert not any(ln.startswith("-") for ln in out.split("\n"))
    assert out.count("•") == 2


def test_prompts_loader(tmp_path, monkeypatch):
    """services.prompts : charge prompts/<pipeline>/<name>.txt et lève une erreur
    CLAIRE (jamais un prompt vide en silence) si le fichier manque — les tests de
    CONTENU des prompts sont retirés : ce contenu est désormais ÉDITABLE par
    l'utilisateur (hors code), il ne doit pas faire échouer la suite."""
    from app.services import prompts
    monkeypatch.setattr(settings, "prompts_dir", tmp_path)
    (tmp_path / "indigo").mkdir()
    (tmp_path / "indigo" / "generation.txt").write_text("CONTENU DE TEST", encoding="utf-8")
    assert prompts.load("indigo", "generation") == "CONTENU DE TEST"
    with pytest.raises(prompts.PromptNotFound):
        prompts.load("indigo", "inexistant")


def test_figure_marker_split_and_strip():
    """Marqueur de placement d'image {{figure}} : découpage avant/après, retrait
    propre, et survie à la normalisation (le rendu s'en sert pour poser l'image
    au bon endroit)."""
    from app.services import statement as s
    assert s.has_figure_marker("Voici.\n{{figure}}\na. Q ?") is True
    assert s.has_figure_marker("Pas d'image ici.") is False
    before, after = s.split_figure_marker("Voici.\n{{figure}}\na. Q ? {{blank}}")
    assert before == "Voici." and after == "a. Q ? {{blank}}"
    assert s.split_figure_marker("aucun marqueur") == ("aucun marqueur", None)
    assert "{{figure}}" not in s.strip_figure_marker("x\n{{figure}}\ny")
    # normalize conserve le marqueur (sur sa ligne) — le rendu en a besoin
    assert "{{figure}}" in s.normalize("Voici le losange.\n{{figure}}\na. Nature ? {{blank}}")

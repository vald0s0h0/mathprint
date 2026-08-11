"""Indigo — moteur de champs de réponse (services.indigo_fields) + rendu des
deux nouvelles variantes de case (mini / pleine largeur) dans le PDF.

On fixe ici : le CHOIX de la taille de case selon la réponse attendue, la
RÉPARTITION d'une case par sous-question a./b./c., l'ESTIMATION du nombre de
lignes d'un raisonnement, et le fait que le rendu dimensionne chaque case
comme prévu (mini fixe, pleine largeur étirée au bord de colonne).
"""
import sys
from pathlib import Path

import pytest
from reportlab.lib.units import mm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import indigo_fields as F
from app.services import pdfgen
from app.services import statement as S


def _int(v, given=False):
    c = {"type": "integer", "value": v}
    if given:
        c["given"] = True
    return c


def _multi(cells):
    return {"type": "table", "rows": 1, "cols": len(cells), "cells": [cells]}


# ------------------------------------------------------------- choix de la taille de case

def test_isolated_short_integer_stays_standard_not_mini():
    # un petit entier dans une phrase ordinaire n'est PAS une équation à trous :
    # la mini-case ne s'applique qu'à une case COLLÉE à une formule (cf. demande
    # utilisateur — mini-case réservée aux équations à trous).
    st, _e, _g = F.adapt_fields("Le PGCD est {{blank}}", "short_text",
                           {"type": "integer", "value": 12, "inline": True}, {})
    assert S.BLANK_TOKEN in st and S.MINI_TOKEN not in st


def test_equation_a_trous_all_mini():
    # « ... » du manuel déjà transformé en {{blank}} par Gemini ; petits entiers
    st, _e, _g = F.adapt_fields("$60 =${{blank}} $\\times${{blank}}", "multi_blank",
                           _multi([_int(2), _int(30)]), {"max_score": 2})
    assert st.count(S.MINI_TOKEN) == 2 and S.BLANK_TOKEN not in st


def test_long_expression_at_eol_becomes_blank_right():
    st, _e, _g = F.adapt_fields("Développe : {{blank}}", "short_text",
                           {"type": "expression", "value": "2x+6", "inline": True}, {})
    assert st.endswith(S.WIDE_TOKEN)


def test_expression_not_at_eol_stays_standard():
    # une case suivie de texte n'est pas éligible à la pleine largeur
    st, _e, _g = F.adapt_fields("On a {{blank}} donc c'est fini.", "short_text",
                           {"type": "expression", "value": "2x+6", "inline": True}, {})
    assert S.BLANK_TOKEN in st and S.WIDE_TOKEN not in st


def test_big_integer_stays_standard_not_mini():
    st, _e, _g = F.adapt_fields("Résultat {{blank}}", "short_text",
                           {"type": "integer", "value": 12345, "inline": True}, {})
    assert S.BLANK_TOKEN in st and S.MINI_TOKEN not in st


# ------------------------------------------------------------- une case par sous-question

def test_answers_dumped_at_end_are_redistributed_per_subquestion():
    st = "a. Calcule $2+3$\nb. Calcule $4-1$\nRéponses : {{blank}} {{blank}}"
    out, _e, _g = F.adapt_fields(st, "multi_blank", _multi([_int(5), _int(3)]), {})
    lines = out.split("\n")
    # a. et b. portent chacune leur case (réponses intermédiaires) — la case
    # rajoutée en fin de ligne n'est pas collée à la formule ($2+3$ est fermée
    # avant l'espace) : ce n'est pas une équation à trous, la case reste standard.
    a_line = next(l for l in lines if l.startswith("a."))
    b_line = next(l for l in lines if l.startswith("b."))
    assert S.BLANK_TOKEN in a_line and S.BLANK_TOKEN in b_line
    assert out.count(S.BLANK_TOKEN) == 2 and S.MINI_TOKEN not in out


def test_inline_blank_in_equation_is_respected_not_moved():
    # chaque sous-question a DÉJÀ sa case au milieu d'une égalité -> on n'y touche pas
    st = "a. $3 \\times${{blank}}$ = 12$\nb. $4 +${{blank}}$ = 9$"
    out, _e, _g = F.adapt_fields(st, "multi_blank", _multi([_int(4), _int(5)]), {})
    # la case reste collée à la formule (pas rejetée en fin de ligne)
    assert "\\times${{mini}}" in out and "+${{mini}}" in out
    assert out.count(S.MINI_TOKEN) == 2


def test_redistribution_skipped_when_counts_mismatch():
    # 3 sous-questions mais 2 réponses : on ne réinvente pas la structure
    st = "a. Observe\nb. Calcule {{blank}}\nc. Calcule {{blank}}"
    out, _e, _g = F.adapt_fields(st, "multi_blank", _multi([_int(5), _int(8)]), {})
    assert not out.split("\n")[0].endswith(S.MINI_TOKEN)  # a. reste sans case


# ------------------------------------------------------------- raisonnement : nb de lignes

def test_reasoning_lines_estimated_from_steps():
    steps = [{"expected_text": "On pose la division euclidienne de 250 par 12.", "points": 1},
             {"expected_text": "$250 = 12 \\times 20 + 10$ donc il reste 10.", "points": 1},
             {"expected_text": "Il faut donc 21 boîtes.", "points": 1}]
    _s, _e, g = F.adapt_fields("Résous le problème.", "multiline_text",
                          {"type": "rubric", "steps": steps},
                          {"rubric": steps, "lines": 6})
    assert 3 <= g["lines"] <= 12
    # 3 étapes courtes -> plus que le minimum, moins que le plafond
    assert g["lines"] >= 4


def test_reasoning_lines_more_for_longer_solution():
    short = [{"expected_text": "x = 4.", "points": 1}, {"expected_text": "y = 2.", "points": 1}]
    longg = [{"expected_text": "x" * 200, "points": 1}, {"expected_text": "y" * 200, "points": 1}]
    _s, _e, gs = F.adapt_fields("Q", "multiline_text", {"type": "rubric", "steps": short},
                           {"rubric": short})
    _s2, _e2, gl = F.adapt_fields("Q", "multiline_text", {"type": "rubric", "steps": longg},
                           {"rubric": longg})
    assert gl["lines"] > gs["lines"]


def test_reasoning_lines_falls_back_to_existing_when_no_steps():
    _s, _e, g = F.adapt_fields("Q", "multiline_text", {}, {"lines": 7})
    assert g["lines"] == 7


# ------------------------------------------------------------- robustesse / idempotence

def test_engine_is_idempotent():
    st = "$60 =${{blank}} $\\times${{blank}}"
    exp = _multi([_int(2), _int(30)])
    once, _e, _g = F.adapt_fields(st, "multi_blank", exp, {})
    twice, _e2, _g2 = F.adapt_fields(once, "multi_blank", exp, {})
    assert once == twice


def test_engine_never_raises_on_garbage():
    st, _e, g = F.adapt_fields(None, "multi_blank", None, None)  # type: ignore[arg-type]
    assert st == "" and isinstance(g, dict)


def test_non_inline_formats_are_untouched():
    st = "Quelle est la bonne réponse ?"
    out, _e, _g = F.adapt_fields(st, "qcm_single", {"type": "choice", "correct": [0]}, {})
    assert out == st


# ------------------------------------------------------ présence garantie du champ de réponse

def test_short_text_without_blank_gets_one_at_the_end():
    """Le prompt exige la case ; le moteur la POSE si le modèle l'a oubliée —
    plus aucun exercice « sans endroit où répondre » (demande utilisateur)."""
    out, exp, _g = F.adapt_fields("Calcule $17 \\times 14$.", "short_text",
                                  {"type": "integer", "value": 238}, {})
    assert out.endswith(S.BLANK_TOKEN)
    assert exp["inline"] is True          # la case se lit dans le fil du texte


def test_added_blank_is_typed_like_any_other():
    """La case posée d'office passe par le MÊME typage que les autres : une
    réponse longue en fin de ligne devient une case pleine largeur."""
    out, _e, _g = F.adapt_fields("Développe et réduis.", "short_text",
                                 {"type": "expression", "value": "2x+6"}, {})
    assert out.endswith(S.WIDE_TOKEN)


def test_blank_never_lands_on_the_figure_line():
    out, _e, _g = F.adapt_fields("Observe la figure.\n{{figure}}", "short_text",
                                 {"type": "integer", "value": 5}, {})
    lines = out.split("\n")
    assert lines[0].endswith(S.BLANK_TOKEN) and lines[1] == "{{figure}}"


def test_orphan_blank_removed_from_non_inline_formats():
    """Une case laissée dans un QCM/grille/tableau serait IMPRIMÉE mais jamais
    relue par la correction : le moteur la retire."""
    out, _e, _g = F.adapt_fields("Coche la bonne réponse : {{blank}}", "qcm_single",
                                 {"type": "choice", "correct": [0]}, {})
    assert not S.has_answer_field(out)
    assert out == "Coche la bonne réponse :"


def test_manual_drawing_needs_no_inline_field():
    st = "Construis la médiatrice de $[AB]$."
    out, _e, _g = F.adapt_fields(st, "manual_drawing", {"type": "manual"}, {})
    assert out == st                      # le cadre de tracé est dessiné par le rendu


def test_ensure_answer_field_is_idempotent():
    once, exp, _g = F.adapt_fields("Calcule $2+3$.", "short_text",
                                   {"type": "integer", "value": 5}, {})
    twice, _e, _g2 = F.adapt_fields(once, "short_text", exp, {})
    assert once == twice


# ------------------------------------------------------------- audit (journalisé, non bloquant)

def test_audit_reports_missing_and_orphan_fields():
    assert F.audit("Calcule $2+3$.", "short_text", {"type": "integer", "value": 5})
    assert F.audit("Coche : {{blank}}", "qcm_single", {"type": "choice", "correct": [0]})
    assert not F.audit("Calcule $2+3$. {{blank}}", "short_text",
                       {"type": "integer", "value": 5, "inline": True})


def _grid(cols, n_rows=10):
    return {"type": "grid", "cols": cols,
            "rows": [{"label": f"${i}$", "correct": 0} for i in range(n_rows)]}


def test_audit_flags_true_false_grid_that_should_be_a_qcm():
    """Défaut relevé sur A1.3 n°70 : une grille Oui/Non de 10 lignes là où
    « Coche tous les diviseurs de 28 » tient en 3 colonnes. Les colonnes qui sont
    des CATÉGORIES (numérateur/dénominateur) restent légitimes, comme une grille
    à 4 colonnes (plus de 3 cases par ligne)."""
    assert F.audit("Coche.", "checkbox_grid", _grid(["Oui", "Non"]))
    assert F.audit("Coche.", "checkbox_grid", _grid(["Vrai", "Faux"]))
    assert not F.audit("Classe.", "checkbox_grid", _grid(["numérateur", "dénominateur"]))
    assert not F.audit("Classe.", "checkbox_grid",
                       _grid(["jamais", "parfois", "souvent", "toujours"]))


def test_audit_descends_into_composite_parts():
    """C'est DANS une sous-question de composite que la grille Oui/Non de
    l'exercice 70 était passée : l'audit doit y descendre — sans réclamer de case
    aux sous-questions (leur zone est dessinée sous leur énoncé)."""
    expected = {"type": "composite", "parts": [
        {"statement": "Coche s'il divise $28$.", "response_type": "checkbox_grid",
         "expected": _grid(["Oui", "Non"])},
        {"statement": "Décomposition de $28$ :", "response_type": "short_text",
         "expected": {"type": "text", "value": "2^2"}},
    ]}
    msgs = F.audit("On donne $28$ et $30$.", "composite", expected)
    assert len(msgs) == 1
    assert "sous-question" in msgs[0] and "Oui/Non" in msgs[0]


def test_audit_reports_subquestions_without_their_own_field():
    st = "a. Calcule $2+3$.\nb. Calcule $4-1$. {{blank}}"
    msgs = F.audit(st, "short_text", {"type": "integer", "value": 5, "inline": True})
    assert any("sous-questions" in m for m in msgs)
    msgs = F.audit("a. Démontre.\nb. Conclus.", "multiline_text",
                   {"type": "rubric", "steps": []})
    assert any("raisonnement" in m for m in msgs)


# ------------------------------------------------------------- tokens sûrs pour normalize

def test_normalize_is_idempotent_on_new_tokens():
    st = "Complète $3 \\times${{mini}}\na. Résultat : {{blank_right}}"
    assert S.normalize(st) == S.normalize(S.normalize(st))


def test_has_answer_field_includes_all_size_variants():
    # le corps de texte suit TOUJOURS sa case, mini comprise (cf. demande
    # utilisateur — uniformiser la taille de police, plus de traitement à part
    # pour la mini-case) : has_answer_field est le SEUL signal, pour les trois.
    assert S.has_answer_field("x {{blank}}")
    assert S.has_answer_field("x {{blank_right}}")
    assert S.has_answer_field("x {{mini}}")


# ------------------------------------------------------------- rendu PDF des variantes

def _blank_segs(text, width_mm=80, fs=10.5):
    lay = pdfgen._rich_layout(text, width_mm * mm, fs)
    return [s for ln in lay["lines"] for s in ln["segs"] if s[0] == "blank"]


def test_render_mini_case_fixed_width():
    segs = _blank_segs("$3 \\times${{mini}} $= 12$")
    assert len(segs) == 1
    assert segs[0][4] == "mini"
    assert abs(segs[0][1] - pdfgen.MINI_BLANK_W) < 0.1


def test_render_standard_case_width():
    segs = _blank_segs("Réponse {{blank}}")
    assert segs[0][4] == "normal"
    assert abs(segs[0][1] - pdfgen.BLANK_W) < 0.1


def test_render_blank_right_stretches_to_column_edge():
    width = 80 * mm
    lay = pdfgen._rich_layout("Développe : {{blank_right}}", width, 10.5)
    line = lay["lines"][0]
    seg = line["segs"][-1]
    assert seg[0] == "blank" and seg[4] == "right"
    # la case s'étire pour combler la ligne : largeur de ligne ≈ largeur de colonne
    assert abs(line["w"] - width) < 0.5
    assert seg[1] > pdfgen.BLANK_W  # nettement plus large qu'une case standard


# ------------------------------------------------------------- colonnes QCM (liste vs colonnes)

def test_qcm_layout_uses_up_to_three_columns_without_truncating_labels():
    """La géométrie, pas le LLM ni un seuil de caractères, choisit les colonnes."""
    long_choices = [
        "La somme des angles d'un triangle vaut 180 degrés",
        "Un carré possède quatre angles droits égaux",
        "Le périmètre est la somme des longueurs des côtés",
        "Deux droites parallèles ne se coupent jamais",
    ]
    width = 80 * mm
    _long_items, _long_h, long_cols = pdfgen._qcm_layout(long_choices, width, 9)
    yes_no, yes_no_h, yes_no_cols = pdfgen._qcm_layout(["Oui", "Non"], width, 9)
    short, short_h, short_cols = pdfgen._qcm_layout(
        ["$2$", "$3$", "$4$", "$6$", "$8$", "$12$"], width, 9)
    assert long_cols == 1
    assert yes_no_cols == 2
    assert short_cols == 3
    assert yes_no[0]["dy"] == yes_no[1]["dy"]       # Oui / Non sur la même ligne
    assert yes_no[0]["dx"] < yes_no[1]["dx"]
    assert yes_no_h < short_h                         # 1 rangée contre 2


# ------------------------------------------------------------- placement de l'image (marqueur)

def test_statement_layout_places_figure_at_marker(tmp_path):
    """L'image se pose AU marqueur {{figure}} (avant/après), pas en fin d'énoncé ;
    sans marqueur elle reste en fin ; sans image le marqueur est retiré."""
    import cv2
    import numpy as np
    p = tmp_path / "fig.png"
    cv2.imwrite(str(p), np.full((80, 120, 3), 200, np.uint8))
    fig = {"type": "image", "params": {"path": str(p)}}
    st = ("On considère le losange $ABCD$ ci-dessous.\n{{figure}}\n"
          "a. Quelle est la nature du triangle $ABC$ ? {{blank}}")
    lay = pdfgen._statement_layout(st, 80 * mm, 10.5, 9, figure_json=fig)
    assert lay["figure_inline"] is True and lay["figure"] is not None
    assert lay["intro_after"] is not None                    # l'énoncé continue sous l'image

    # même figure, PAS de marqueur -> placée en fin (comportement historique)
    st2 = "On considère le losange $ABCD$.\na. Nature du triangle $ABC$ ? {{blank}}"
    lay2 = pdfgen._statement_layout(st2, 80 * mm, 10.5, 9, figure_json=fig)
    assert lay2["figure_inline"] is False and lay2["intro_after"] is None

    # marqueur mais AUCUNE image -> marqueur retiré, jamais imprimé en texte
    lay3 = pdfgen._statement_layout(st, 80 * mm, 10.5, 9, figure_json=None)
    words = [seg[1] for ln in lay3["intro"]["lines"] for seg in ln["segs"] if seg[0] == "word"]
    assert lay3["figure_inline"] is False
    assert not any("figure" in w for w in words)


# ------------------------------------------------------------- largeur colonnes tableau (énoncé/élève)

def test_table_rowlab_capped_and_answer_columns_widen():
    """Bug « colonne énoncé pleine de blanc, colonne élève étroite » : la colonne
    énoncé prend ce qu'il lui faut (bornée), le surplus élargit les cases élève."""
    w = pdfgen.COL_W
    cells = [[_int(4)], [_int(9)], [_int(16)]]
    row_labels = ["$2^2 =$", "$3^2 =$", "$4^2 =$"]     # libellés courts
    geo = pdfgen._table_geometry(w, None, row_labels, cells, 9)
    assert geo["rowlab_w"] <= (w - 2 * pdfgen.CARD_PAD) * 0.6      # colonne énoncé bornée
    # la case élève est ÉLARGIE : plus large que sa taille désirée de base
    assert geo["col_ws"][0] > pdfgen._COL_KIND_WIDTH[geo["col_kinds"][0]]


def test_table_answer_box_fills_its_cell():
    """La case à remplir OCCUPE sa cellule (au retrait près) : elle était
    plafonnée à 20x8 mm, ce qui laissait le reste de la cellule en blanc perdu
    et l'élève écrivait à l'étroit. La cellule, elle, ne bouge pas."""
    import tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    cells = [[_int(4)], [_int(9)], [_int(16)]]
    row_labels = ["$2^2 =$", "$3^2 =$", "$4^2 =$"]
    w = pdfgen.COL_W
    geo = pdfgen._table_geometry(w, None, row_labels, cells, 9)
    h = pdfgen._table_zone_height(w, None, row_labels, cells, 9)
    c = canvas.Canvas(str(Path(tempfile.mkdtemp()) / "t.pdf"), pagesize=A4)
    meta = pdfgen._draw_table_zone(c, 0, 0, w, h, None, row_labels, cells, 9)
    pad = pdfgen._TABLE_CELL_PAD
    for i, row in enumerate(meta["cells"]):
        for j, box in enumerate(row):
            assert abs(box["w_pt"] - (geo["col_ws"][j] - 2 * pad)) < 0.01
            assert abs(box["h_pt"] - (geo["row_hs"][i] - 2 * pad)) < 0.01
            assert box["w_pt"] > pdfgen.BLANK_W      # nettement plus large qu'avant


def test_fraction_table_row_gains_exactly_3mm():
    integer = pdfgen._table_geometry(pdfgen.COL_W, None, ["Réponse"], [[_int(2)]], 9)
    fraction = pdfgen._table_geometry(
        pdfgen.COL_W, None, ["Réponse"],
        [[{"type": "rational", "value": [1, 2]}]], 9)
    assert fraction["row_hs"][0] - integer["row_hs"][0] == pytest.approx(3 * mm)


def test_fraction_in_multiline_reasoning_gains_exactly_3mm():
    grading = {"lines": 5, "max_score": 2, "comparator": "rubric"}
    plain = {"type": "rubric", "steps": [{"expected_text": "Le résultat vaut 2."}]}
    fractional = {"type": "rubric", "steps": [
        {"expected_text": "Le résultat vaut $\\dfrac{1}{2}$."}]}
    h_plain = pdfgen._zone_height("multiline_text", [], pdfgen.COL_W, 9,
                                  grading, expected=plain)
    h_fraction = pdfgen._zone_height("multiline_text", [], pdfgen.COL_W, 9,
                                     grading, expected=fractional)
    assert h_fraction - h_plain == pytest.approx(3 * mm)


def test_full_copy_renders_with_mini_and_blank_right():
    """Rendu bout-en-bout : une copie qui mélange mini-cases (multi_blank) et
    case pleine largeur (short_text) se dessine sans erreur, et chaque marque de
    case ressort bien comme une case en ligne."""
    import tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    items = [
        {"kind": "exercise", "item_id": "m1",
         "statement": "Complète : $60 =${{mini}} $\\times${{mini}}",
         "correction": "Rappelle-toi la décomposition en facteurs.",
         "response_type": "multi_blank", "choices": [], "level5": 3, "figure": None,
         "grading": {"max_score": 2, "comparator": "table_cells",
                     "cells": [[_int(2), _int(30)]]}, "inline": True},
        {"kind": "exercise", "item_id": "w1",
         "statement": "Développe et réduis : {{blank_right}}",
         "correction": "Distributivité simple.",
         "response_type": "short_text", "choices": [], "level5": 3, "figure": None,
         "grading": {"max_score": 2, "comparator": "symbolic_equiv"}, "inline": True},
    ]
    out = Path(tempfile.mkdtemp()) / "copy.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    pages_meta = [{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(4)]
    zones = pdfgen.render_copy(c, student_name="Élève Test", class_name="3A",
                               title="T", assessment_type="training", items=items,
                               pages_meta=pages_meta, font_size=10)
    c.save()
    # la mini-case multi_blank donne deux cellules ; la case pleine largeur une zone
    z_multi = next(z for z in zones if z["item_id"] == "m1")
    assert len(z_multi["meta"]["cells"][0]) == 2
    assert out.exists() and out.stat().st_size > 800

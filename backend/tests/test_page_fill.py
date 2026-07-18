"""Remplissage de page : pdfgen.pages_needed doit prédire EXACTEMENT le
placement réel de render_copy, et la génération ne doit jamais déborder de la
cible de pages.

Le test central est test_pages_needed_matches_real_render_copy : la simulation
et le dessin sont deux codes distincts, et c'est leur écart qui faisait
déborder les copies (somme des hauteurs ≤ capacité, mais une carte ne se coupe
pas en deux). S'ils divergent un jour, ce test tombe.
"""
import sys
import tempfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import pdfgen
from app.services.pdfgen import DEFAULT_TEMPLATES


def _exercise(statement: str, response_type: str = "short_text", **kw) -> dict:
    return {"kind": "exercise", "item_id": statement[:12], "statement": statement,
            "response_type": response_type, "choices": kw.get("choices", []),
            "level5": 3, "figure": None,
            "grading": kw.get("grading", {"max_score": 1, "comparator": "numeric"}),
            "inline": False}


def _render_pages(items: list[dict]) -> int:
    """Pages réellement occupées par render_copy (dessin complet, jeté après)."""
    out = Path(tempfile.mkdtemp()) / "copy.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    pages_meta = [{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(12)]
    zones = pdfgen.render_copy(c, student_name="Test Élève", class_name="5eB",
                               title="Test", assessment_type="training", items=items,
                               pages_meta=pages_meta, font_size=9)
    c.save()
    return max((z["page_index"] for z in zones), default=0) + 1


def _heights(items: list[dict]) -> list[float]:
    tpl = DEFAULT_TEMPLATES
    return [pdfgen.estimate_item_height(
        i, int(tpl["exercise"].get("font_size", 9)), int(tpl["exercise"].get("math_size", 12)),
        tpl["exercise"], tpl["lesson"]) for i in items]


@pytest.mark.parametrize("n", [1, 3, 5, 7, 8, 9, 12, 20])
def test_pages_needed_matches_real_render_copy(n):
    items = [_exercise(f"Calcule le produit ${i} \\times {i + 3}$.") for i in range(n)]
    assert pdfgen.pages_needed(_heights(items)) == _render_pages(items)


def test_pages_needed_matches_real_render_copy_with_tall_cards():
    # Cartes hétérogènes : c'est là que le bas de colonne perdu se voit, et que
    # la somme des hauteurs mentait le plus.
    rubric = {"max_score": 2, "comparator": "rubric", "lines": 10,
              "steps": [{"description": "Étape", "expected_text": "$1 + 1 = 2$", "points": 1}]}
    items = []
    for i in range(6):
        items.append(_exercise(f"Calcule ${i} + {i}$."))
        items.append(_exercise(
            f"Problème {i} : détaille entièrement ton raisonnement avant de conclure.",
            "multiline_text", grading=rubric))
    assert pdfgen.pages_needed(_heights(items)) == _render_pages(items)


def test_pages_needed_empty_copy_is_one_page():
    assert pdfgen.pages_needed([]) == 1


def test_pages_needed_counts_the_lost_bottom_of_column():
    # Le cœur du bug : 4 cartes de 60 % de colonne = 240 % de colonne, soit
    # « 1,2 page » en somme brute — mais aucune ne se coupant en deux, il en
    # tient une par demi-colonne : 2 pages.
    column_h = pdfgen._top_of_page(0) - pdfgen._BOTTOM_LIMIT
    assert pdfgen.pages_needed([column_h * 0.6] * 4) == 2


def test_pack_reading_order_never_worse_than_raw_order():
    # Le remplissage colonne par colonne (FFD) ne doit JAMAIS déborder d'une
    # page de plus que l'ordre brut — et le supprime souvent (les petites cartes
    # comblent les bas de colonne au lieu de laisser un grand vide).
    import random
    col = pdfgen._top_of_page(1) - pdfgen._BOTTOM_LIMIT
    random.seed(3)
    for _ in range(200):
        hs = [col * random.uniform(0.2, 0.7) for _ in range(random.randint(4, 10))]
        raw = pdfgen.pages_needed(hs)
        ffd = pdfgen.pages_needed([hs[i] for i in pdfgen.pack_reading_order(hs)])
        assert ffd <= raw


def test_pack_reading_order_is_reproduced_by_real_render_copy():
    # L'ordre FFD, posé tel quel par le placement glouton de render_copy, doit
    # retomber EXACTEMENT sur le nombre de pages simulé (invariant central) :
    # un FFD ne laisse jamais un trou qu'une carte ultérieure aurait pu combler.
    rubric = {"max_score": 2, "comparator": "rubric", "lines": 9,
              "steps": [{"description": "Étape", "expected_text": "$1 + 1 = 2$", "points": 1}]}
    items = []
    for i in range(9):
        items.append(_exercise(f"Calcule ${i} + {i}$."))
        items.append(_exercise(f"Problème {i} : détaille ton raisonnement.",
                               "multiline_text", grading=rubric))
    order = pdfgen.pack_reading_order(_heights(items))
    packed = [items[i] for i in order]
    assert pdfgen.pages_needed(_heights(packed)) == _render_pages(packed)

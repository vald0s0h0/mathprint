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
            "level3": 3, "figure": None,
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
        tpl["exercise"]) for i in items]


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


def test_first_page_header_is_governed_by_the_unchanged_qr_height():
    """L'assistant manuel consomme column_metrics : cette géométrie partagée
    garantit que le gain de place de l'en-tête est réellement disponible à
    l'écran et dans le PDF, sans réduire le QR physique de 24 mm."""
    assert pdfgen.HEADER_H == pdfgen.QR_MAIN == 24 * pdfgen.mm
    geo = pdfgen.header_geometry("control")
    for zone in ("note", "appreciation", "meta", "qr"):
        assert geo[zone]["y"] == geo["qr"]["y"]
        assert geo[zone]["h"] == geo["qr"]["h"]
    metrics = pdfgen.column_metrics(1)
    assert metrics["column_h"][0] == pytest.approx(
        pdfgen._top_of_page(0) - pdfgen._BOTTOM_LIMIT)


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


# ---------------------------------------------------- la place encore libre

def test_free_space_agrees_with_pages_needed():
    """`free_space` et `pages_needed` doivent lire le MÊME placement : si une
    colonne est annoncée libre alors que la copie déborde déjà, le remplissage
    vise un trou qui n'existe pas."""
    import random
    col = pdfgen._top_of_page(1) - pdfgen._BOTTOM_LIMIT
    random.seed(11)
    for _ in range(200):
        hs = [col * random.uniform(0.15, 0.8) for _ in range(random.randint(1, 12))]
        pages = pdfgen.pages_needed([hs[i] for i in pdfgen.pack_reading_order(hs)])
        holes = pdfgen.free_space(hs, pages)
        # autant de colonnes que de pages, et jamais de trou négatif dans le budget
        assert len(holes) == 2 * pages
        assert all(h >= -1e-6 for h in holes)
        # la place libre totale + la place occupée = la capacité des colonnes
        capacity = sum(pdfgen.column_capacity(b) for b in range(2 * pages))
        assert sum(holes) + sum(hs) == pytest.approx(capacity, abs=1e-6)


def test_free_space_is_zero_when_the_pages_are_exactly_full():
    col0, col1 = pdfgen.column_capacity(0), pdfgen.column_capacity(1)
    assert pdfgen.free_space([col0, col1], 1) == pytest.approx([0.0, 0.0])


def test_pack_columns_never_exceeds_a_column():
    import random
    col = pdfgen._top_of_page(1) - pdfgen._BOTTOM_LIMIT
    random.seed(17)
    for _ in range(100):
        hs = [col * random.uniform(0.1, 0.9) for _ in range(random.randint(1, 15))]
        for b, column in enumerate(pdfgen.pack_columns(hs)):
            assert sum(hs[i] for i in column) <= pdfgen.column_capacity(b) + 1e-9


def test_a_hole_bigger_than_a_card_means_the_page_was_not_filled():
    """La définition opérationnelle de « pas de vide gaspillé » : s'il reste un
    trou plus grand qu'une carte encore disponible, la copie n'est pas finie.

    C'est ce que la passe best-fit de services.generation garantit, et ce que
    l'ancien remplissage (qui tirait au hasard puis annulait) ratait dès que la
    source n'avait pas de cartes « filler » — le cas d'Indigo, source par défaut
    des 3e."""
    petite = _exercise("Calcule $2 + 2$.")
    h_petite = _heights([petite])[0]
    # une colonne pleine à ras bord sauf la hauteur d'une petite carte
    reste = pdfgen.column_capacity(0) - h_petite
    holes = pdfgen.free_space([reste], 1)
    assert max(holes) >= h_petite          # il RESTE de la place pour elle
    # une fois posée, le trou de cette colonne ne l'accueille plus
    holes_apres = pdfgen.free_space([reste, h_petite], 1)
    assert holes_apres[0] == pytest.approx(0.0, abs=1e-6)

"""Blocs de présentation d'un énoncé : tableaux, séries de valeurs, gras.

Trois lignes d'un énoncé ne sont pas des phrases, et les rendre au fil du texte
les rendait illisibles :
- un tableau de données recopié en Markdown par l'extraction s'imprimait en
  bouillie de barres verticales, repliée n'importe où ;
- une série de valeurs (« 10 W 8 W 6 W 10 W … ») se recollait, et l'élève ne
  voyait plus où une valeur finissait ;
- le `**gras**` du modèle s'imprimait avec ses astérisques.

Deux exigences se croisent ici, et c'est pour ça que les tests vont jusqu'au
PDF : la DÉTECTION doit être avare (une phrase qui contient des nombres reste
une phrase), et la GÉOMÉTRIE doit être mesurée là où elle est dessinée — une
colonne mesurée en romain puis dessinée en gras déborde sur sa voisine.

Le miroir TypeScript (`frontend/src/utils/richblocks.ts`) applique les mêmes
règles pour que l'aperçu de l'écran montre la feuille imprimée.
"""
import sys
from pathlib import Path

import pytest
from reportlab.lib.units import mm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import blocks, pdfgen, statement
from app.services.pdfgen import CARD_PAD, COL_W

WIDTH = COL_W - 2 * CARD_PAD

TABLE = ("| Taille (en cm) | de 140 à 150 | de 150 à 160 |\n"
         "|---|---|---|\n"
         "| Effectif | 10 | 14 |")


# ------------------------------------------------------------- détection

def test_a_markdown_table_becomes_one_block():
    (block,) = blocks.parse(TABLE)
    assert block.kind == "table"
    assert block.header is True          # la ligne de tirets a été consommée
    assert block.rows == [["Taille (en cm)", "de 140 à 150", "de 150 à 160"],
                          ["Effectif", "10", "14"]]


def test_a_table_cell_may_carry_a_formula_with_brackets():
    """`$[30,35[$` porte des crochets, pas des barres — mais une borne écrite
    `$|x|$` en porterait : on ne découpe donc qu'en dehors des spans."""
    (block,) = blocks.parse("| Classe | $[30,35[$ | $|x|$ |\n| Effectif | 1 | 2 |")
    assert block.rows[0] == ["Classe", "$[30,35[$", "$|x|$"]


def test_an_isolated_pipe_line_stays_text():
    """Une seule ligne ne fait pas un tableau : sans deuxième ligne, c'est du
    texte, et le rendu ne doit pas inventer une géométrie."""
    (block,) = blocks.parse("| a | b |")
    assert block.kind == "text"


def test_a_bare_list_of_values_becomes_a_series():
    (block,) = blocks.parse("10 W 8 W 6 W 10 W 10 W 10 W 8 W 4 W 8 W")
    assert block.kind == "series"
    assert block.items == ["10 W", "8 W", "6 W", "10 W", "10 W", "10 W",
                           "8 W", "4 W", "8 W"]


@pytest.mark.parametrize("line, items", [
    ("37,2 39,4 38 38,2 39 38,6", ["37,2", "39,4", "38", "38,2", "39", "38,6"]),
    ("2,5 ; 4 ; 5,4 ; 4,5 ; 3,6", ["2,5", "4", "5,4", "4,5", "3,6"]),
    ("8, 12, 15, 17, 19", ["8", "12", "15", "17", "19"]),
    ("100 % 25 % 50 % 75 %", ["100 %", "25 %", "50 %", "75 %"]),
    ("$1,5$ ; $2,5$ ; $3,5$ ; $4,5$", ["$1,5$", "$2,5$", "$3,5$", "$4,5$"]),
])
def test_the_usual_separators_are_all_recognised(line, items):
    assert blocks.parse_series(line) == items


@pytest.mark.parametrize("line", [
    "Voici les notes d'un élève : 8, 12, 15, 17, 19. On ajoute ensuite 10 et 20.",
    "12, 18, 21 et 25",                       # « et » n'est pas une unité
    "3 km 4 5 km 6 km",                       # unité sur une valeur seulement
    "2 3 5",                                  # trop court pour une mise en grille
    "Complète : {{blank}} ; {{blank}} ; {{blank}} ; {{blank}}",
])
def test_a_sentence_with_numbers_is_never_a_series(line):
    """La ligne doit être consommée EN ENTIER : c'est ce qui sépare une série
    d'une phrase qui contient des nombres. Sans ça, la moitié des énoncés de
    statistiques seraient dessinés en grille."""
    assert blocks.parse_series(line) is None


def test_an_announced_series_splits_from_its_lead():
    """« Voici les prix : 4,50 € ; … » — la phrase reste une phrase, les valeurs
    passent en grille sous elle."""
    lead, series = blocks.parse(
        "Voici les prix : 4,50 € ; 2,50 € ; 2,10 € ; 3,00 € ; 2,90 €.")
    assert (lead.kind, lead.text) == ("text", "Voici les prix :")
    assert series.items == ["4,50 €", "2,50 €", "2,10 €", "3,00 €", "2,90 €"]


def test_a_labelled_series_keeps_its_subquestion_label():
    """« b. 2,5 ; 4 ; … » reste la sous-question b. : l'étiquette voyage avec le
    bloc pour rester une pastille, jamais du texte perdu."""
    (block,) = blocks.parse("b. 2,5 ; 4 ; 5,4 ; 4,5 ; 3,6")
    assert (block.kind, block.label) == ("series", "b")


# ------------------------------------------------------------------ gras

def test_bold_spans_are_split_and_the_markers_disappear():
    assert blocks.split_bold("**Vrai ou faux ?** puis **a** et **b**") == [
        ("Vrai ou faux ?", True), (" puis ", False), ("a", True),
        (" et ", False), ("b", True)]


def test_a_multiplication_is_not_bold():
    """« 3 ** 4 ** 5 » n'est pas du gras : le contenu doit commencer ET finir
    par un caractère visible."""
    assert blocks.split_bold("3 ** 4 ** 5") == [("3 ** 4 ** 5", False)]


def test_a_bold_subquestion_label_becomes_a_pastille():
    """Le modèle écrit « **a.** 17 élèves ». Tant que l'étiquette porte ses
    astérisques, SUBQUESTION_RE ne la voit pas : la ligne perd sa pastille et
    « **a.** » s'imprime tel quel."""
    assert statement.normalize("**a.** 17 élèves. **b.** 14 rations.") == (
        "a. 17 élèves.\nb. 14 rations.")
    assert statement.normalize("**a**. x") == "a. x"


# ------------------------------------------------------- géométrie du PDF

def _entry(text: str, width: float = WIDTH, fs: float = 9.0) -> dict:
    (line,) = pdfgen._rich_layout(text, width, fs)["lines"]
    return line


def test_the_table_is_drawn_as_a_table_not_as_words():
    entry = _entry(TABLE)
    assert entry["segs"] == []
    assert entry["table"]["cols"] and len(entry["table"]["rows"]) == 2


def test_table_columns_are_adjusted_to_their_content():
    """Colonnes ajustées, pas égales : la colonne d'étiquettes est plus large
    que les colonnes de nombres, sinon les nombres flottent au milieu de vide
    pendant que les libellés se replient sur quatre lignes."""
    cols = _entry("| Nombre de biscuits brisés | 2 | 4 |\n"
                  "|---|---|---|\n"
                  "| Effectif | 5 | 8 |")["table"]["cols"]
    assert cols[0] > cols[1] * 2
    assert cols[1] == pytest.approx(cols[2], abs=0.5)


def test_a_header_is_measured_in_the_font_it_is_drawn_with():
    """L'en-tête s'imprime en gras. Mesuré en romain, il déborde sur la colonne
    voisine — le bug exact vu à l'impression (« Nombre de biscuits brisé2 »)."""
    entry = _entry(TABLE)
    head = entry["table"]["rows"][0]
    assert head["header"] is True
    words = [seg for cell in head["cells"] for ln in cell["lines"]
             for seg in ln["segs"] if seg[0] == "word"]
    assert words and all(seg[2] for seg in words)      # seg[2] = gras


def test_a_narrow_table_is_centred_rather_than_stretched():
    """Un petit tableau reste petit et se centre dans la carte ; il n'est pas
    étiré jusqu'aux bords."""
    entry = _entry("| a | b |\n|---|---|\n| 1 | 2 |")
    assert entry["w"] < WIDTH
    assert entry["indent"] == pytest.approx((WIDTH - entry["w"]) / 2)


def test_a_wide_table_never_overflows_the_card():
    entry = _entry("| " + " | ".join(f"colonne {i}" for i in range(9)) + " |\n"
                   + "|" + "---|" * 9 + "\n"
                   + "| " + " | ".join(str(i) for i in range(9)) + " |")
    assert entry["w"] <= WIDTH + 0.5


SERIES = "10 W 8 W 6 W 10 W 10 W 10 W 8 W 4 W 8 W"


def test_a_series_that_fits_stays_on_one_row():
    series = _entry(SERIES)["series"]
    assert series["cols"] == 9
    assert series["col_w"] == pytest.approx(WIDTH / 9)


def test_a_series_too_wide_is_balanced_over_its_rows():
    """Dans une colonne étroite, huit valeurs tiennent et la neuvième passe à la
    ligne : laissée seule, elle donnerait une grille bancale. On rééquilibre sur
    le nombre de lignes obtenu — deux lignes de 5 puis 4."""
    series = _entry(SERIES, width=85 * mm)["series"]
    assert series["cols"] == 5
    assert series["col_w"] == pytest.approx(85 * mm / 5)


def test_bold_words_carry_their_own_font():
    """Le gras vit dans le SEGMENT, pas dans la ligne : « **Vrai ou faux ?**
    Coche » n'a qu'une partie en gras, et la mesure doit suivre le dessin."""
    segs = pdfgen._paragraph_segs("**Vrai ou faux ?** Coche.", 9, 9)
    bold = {seg[1]: seg[2] for seg in segs if seg[0] == "word"}
    assert bold == {"Vrai": True, "ou": True, "faux": True, "?": True, "Coche.": False}
    assert pdfgen._seg_font(("word", "Vrai", True, False)) == pdfgen._font("bold")


def test_bold_survives_a_formula_inside_it():
    """« **Prix : $3$ €** » a ses deux marques de part et d'autre d'un span :
    cherchées après le découpage mathématique, elles resteraient orphelines et
    s'imprimeraient telles quelles."""
    segs = pdfgen._paragraph_segs("**Prix : $3$ €**", 9, 9)
    assert all("*" not in seg[1] for seg in segs if seg[0] == "word")
    assert all(seg[2] for seg in segs if seg[0] == "word")


def test_a_statement_height_still_drives_the_page_filling():
    """La mesure des blocs passe par le MÊME layout que le texte : c'est cette
    hauteur-là qui décide du remplissage des pages (cf. pages_needed), donc un
    tableau doit compter pour sa vraie hauteur, jamais pour une ligne de texte."""
    plain = pdfgen._statement_layout("Voici les tailles.", WIDTH, 9, 11)
    with_table = pdfgen._statement_layout("Voici les tailles.\n" + TABLE, WIDTH, 9, 11)
    assert with_table["height"] > plain["height"] + 10 * mm

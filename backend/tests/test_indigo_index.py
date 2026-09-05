"""Indigo — index du manuel : plus aucune plage à saisir à la main.

Aucun réseau, aucun gros PDF : on écrit un index synthétique sur disque et on
vérifie qu'il rend les mêmes cibles (pages + numéros) que celles que l'admin
saisissait auparavant. Un test à part construit un vrai PDF texte à deux
colonnes pour fixer la lecture GRATUITE du manuel du professeur.
"""
import json
import sys
from pathlib import Path

import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import Competency, CompetencyFramework
from app.services import indigo, indigo_index, indigo_manual


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


def _comps(db):
    fw = CompetencyFramework(grade_level="3e", name="T")
    db.add(fw); db.flush()
    a = Competency(framework_id=fw.id, code="A1.1", short_id="A1.1",
                   label="Reconnaître un nombre premier", domain_code="A",
                   domain_name="Nombres", chapter_code="A1",
                   chapter_name="Nombres entiers", order_index=1)
    b = Competency(framework_id=fw.id, code="A1.2", short_id="A1.2",
                   label="Calculer un PGCD", domain_code="A",
                   domain_name="Nombres", chapter_code="A1",
                   chapter_name="Nombres entiers", order_index=2)
    db.add_all([a, b]); db.commit()
    return a, b


def _block(content, x, y, kind="text"):
    return {"type": kind, "content": content, "top_left_x": x, "top_left_y": y,
            "bottom_right_x": x + 500, "bottom_right_y": y + 40}


def _write_index(grade, which, pages, sha=""):
    data = {"version": indigo_index.INDEX_VERSION, "grade_level": grade,
            "which": which, "sha256": sha, "page_count": len(pages), "pages": pages}
    indigo_index.index_path(grade, which).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _eleve_pages():
    """Deux pages : la compétence A1.1 (n° 12 à 14) puis A1.2 (n° 15 à 17). Le
    titre de A1.2 tombe EN COURS de page — c'est le cas qui piégeait le
    découpage par simple séquence."""
    dims = {"width": 1240, "height": 1754}
    page40 = {"source_page": 40, "dims": dims, "blocks": [
        _block("Reconnaître un nombre premier", 60, 50, "title"),
        _block("12 Le nombre 51 est-il premier ?", 60, 120),
        _block("13 Donne la liste des nombres premiers inférieurs à 20.", 60, 260),
        _block("1. Commence par 2.", 60, 320),
        _block("14 Décompose 84 en facteurs premiers.", 60, 420),
    ]}
    page41 = {"source_page": 41, "dims": dims, "blocks": [
        _block("15 Le nombre 153 est-il premier ?", 60, 60),
        _block("Calculer un PGCD", 60, 200, "title"),
        _block("16 Calcule le PGCD de 24 et 36.", 60, 260),
        _block("17 Calcule le PGCD de 1925 et 4125.", 60, 400),
    ]}
    for p in (page40, page41):
        p["numbers"] = indigo_index._page_numbers(p["blocks"])
    return {"40": page40, "41": page41}


def _prof_pages():
    dims = {"width": 1240, "height": 1754}
    page180 = {"source_page": 180, "dims": dims, "chapter": "Nombres entiers",
               "numbers": [12, 13, 14, 15], "blocks": [
                   _block("12 Non, 51 = 3 x 17.", 60, 60),
                   _block("13 2, 3, 5, 7, 11, 13, 17, 19.", 60, 160),
                   _block("14 84 = 2^2 x 3 x 7.", 60, 260),
                   _block("15 Non, 153 = 9 x 17.", 60, 360)]}
    page181 = {"source_page": 181, "dims": dims, "chapter": "Nombres entiers",
               "numbers": [16, 17], "blocks": [
                   _block("16 PGCD(24, 36) = 12.", 60, 60),
                   _block("17 PGCD(1925, 4125) = 275.", 60, 160)]}
    page200 = {"source_page": 200, "dims": dims, "chapter": "Équations",
               "numbers": [16, 17], "blocks": [
                   _block("16 x = 4.", 60, 60), _block("17 x = -2.", 60, 160)]}
    return {"180": page180, "181": page181, "200": page200}


def test_resolve_targets_replaces_the_three_hand_typed_ranges(db):
    a, b = _comps(db)
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", _prof_pages())

    targets, missing = indigo_index.resolve_targets(db, "3e", [a.id, b.id])
    assert missing == []
    by_comp = {t["competency_id"]: t for t in targets}

    # A1.1 : ses exercices s'arrêtent au titre de la compétence suivante
    assert by_comp[a.id]["numbers"] == [12, 13, 14, 15]
    assert by_comp[a.id]["eleve_pages"] == [40, 41]
    # A1.2 : ceux qui suivent son titre, sur la même page
    assert by_comp[b.id]["numbers"] == [16, 17]
    assert by_comp[b.id]["eleve_pages"] == [41]


def test_sub_question_numbers_never_open_an_exercise(db):
    """« 1. Commence par 2. » suit l'exercice 13 : c'est une sous-question, pas
    l'exercice n°1 (la croissance stricte l'écarte)."""
    a, _b = _comps(db)
    _write_index("3e", "eleve", _eleve_pages())
    targets, _ = indigo_index.resolve_targets(db, "3e", [a.id])
    assert 1 not in targets[0]["numbers"]


def test_prof_pages_are_restricted_to_the_right_chapter(db):
    """Les numéros REPARTENT à 1 d'un chapitre à l'autre : le corrigé n°16 du
    chapitre « Équations » ne doit pas être appairé à l'exercice n°16 de
    « Nombres entiers »."""
    _a, b = _comps(db)
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", _prof_pages())
    targets, _ = indigo_index.resolve_targets(db, "3e", [b.id])
    assert targets[0]["prof_pages"] == [181]


def test_the_running_header_carries_over_to_the_pages_that_lack_it(db):
    """Le livre du professeur n'imprime son en-tête que sur UNE PAGE SUR DEUX
    (133 pages sur 216 n'en portent aucune) : sans report de l'en-tête courant,
    le filtre par chapitre ne s'appliquait qu'à la moitié du livre. Une page
    muette du chapitre « Équations » passait donc pour n'importe quel chapitre,
    et son corrigé n°16 pouvait devenir celui du n°16 des « Nombres entiers »."""
    _a, b = _comps(db)
    pages = _prof_pages()
    # page 201 : même chapitre que 200 (« Équations »), mais sans en-tête —
    # exactement la page de droite d'une double page du livre.
    pages["201"] = {"source_page": 201, "dims": {"width": 1240, "height": 1754},
                    "chapter": "", "numbers": [16, 17], "blocks": [
                        _block("16 x = 9.", 60, 60), _block("17 x = 1.", 60, 160)]}
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", pages)
    targets, _ = indigo_index.resolve_targets(db, "3e", [b.id])
    assert targets[0]["prof_pages"] == [181], \
        "la page muette héritait du chapitre de la page précédente, pas de tous"


def test_a_chapter_absent_from_the_prof_manual_still_yields_corrections(db):
    """Resserrer le filtre ne doit pas priver de corrigé une compétence dont le
    libellé de chapitre ne figure nulle part côté professeur : mieux vaut les
    numéros seuls (ancien comportement) que rien du tout."""
    _a, b = _comps(db)
    b.chapter_name = "Chapitre qui n'existe pas côté prof"
    db.commit()
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", _prof_pages())
    targets, _ = indigo_index.resolve_targets(db, "3e", [b.id])
    assert targets[0]["prof_pages"], "aucun corrigé n'aurait été proposé"


def test_targets_feed_the_pipeline_without_being_re_expanded(db):
    """Les listes de l'index l'emportent sur les plages : ré-étaler « 12-17 »
    ferait rentrer dans A1.1 des exercices d'une autre compétence."""
    a, _b = _comps(db)
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", _prof_pages())
    targets, _ = indigo_index.resolve_targets(db, "3e", [a.id])
    normalized = indigo.normalize_target(targets[0])
    assert normalized["numbers"] == [12, 13, 14, 15]
    assert normalized["eleve_pages"] == [40, 41]


def test_a_competency_absent_from_the_index_is_named_not_silently_skipped(db):
    a, b = _comps(db)
    _write_index("3e", "eleve", {"40": _eleve_pages()["40"]})
    targets, missing = indigo_index.resolve_targets(db, "3e", [a.id, b.id])
    assert [t["competency_id"] for t in targets] == [a.id]
    assert missing == [b.short_id]


def test_coverage_reports_what_the_index_knows(db):
    a, _b = _comps(db)
    _write_index("3e", "eleve", _eleve_pages())
    _write_index("3e", "prof", _prof_pages())
    cov = indigo_index.coverage(db, "3e")
    assert cov["eleve"]["indexed"] == 2 and cov["prof"]["indexed"] == 3
    row = next(r for r in cov["competencies"] if r["code"] == a.code)
    assert row["numbers"] == [12, 13, 14, 15]


def test_a_changed_manual_invalidates_the_index(db, monkeypatch):
    _write_index("3e", "eleve", _eleve_pages(), sha="empreinte-du-vieux-pdf")
    monkeypatch.setattr(indigo_index, "_fingerprint", lambda g, w: "une-autre-empreinte")
    assert indigo_index.load("3e", "eleve") is None


# ------------------------------------------- lecture GRATUITE du manuel prof

class _FakeDoc:
    """Document PyMuPDF minimal : rend les blocs mesurés sur le VRAI manuel.

    On ne fabrique pas un PDF de test pour ça : PyMuPDF regroupe alors le numéro
    et son corrigé dans un même bloc, ce qui est justement le cas facile. La
    géométrie ci-dessous est celle relevée page 50 du livre du professeur —
    numéro seul dans la gouttière (x ≈ 306), corps de corrigé juste à droite
    (x ≈ 322), même hauteur à un point près."""

    def __init__(self, blocks, width=595.0, height=842.0):
        self._blocks = blocks
        self.rect = fitz.Rect(0, 0, width, height)
        self.page_count = 1

    def __getitem__(self, _idx):
        return self

    def get_text(self, _kind):
        return list(self._blocks)


_PROF_PAGE = [
    (17.3, 445.3, 27.8, 700.0, "© Hachette Livre 2020 – Mission Indigo 3e", 0, 0),
    (341.5, 813.2, 543.6, 828.0,
     "Livre du professeur – Chapitre 1  Nombres entiers\n51\n", 1, 0),
    (305.9, 546.1, 317.6, 558.0, "102 \n", 2, 0),
    (321.7, 545.3, 543.1, 660.0, "1. Si n est pair, il peut s’écrire n = 2k.", 3, 0),
    (305.9, 671.8, 317.6, 683.0, "103 \n", 4, 0),
    (321.7, 671.9, 543.1, 780.0, "1. Dans une division euclidienne par 3…", 5, 0),
    (439.1, 103.8, 447.3, 115.0, "36\n", 6, 0),          # cote d'un schéma
    (420.5, 176.7, 436.9, 190.0, "4\n2\n", 7, 0),        # cotes d'un schéma
]


def test_prof_manual_is_read_from_its_text_layer_without_any_ocr():
    doc = _FakeDoc(_PROF_PAGE)
    blocks = indigo_index.correction_blocks(doc, 0)
    contents = [b["content"] for b in blocks]
    assert any(c.startswith("102 1. Si n est pair") for c in contents)
    assert any(c.startswith("103 1. Dans une division") for c in contents)
    # les cotes de schéma ne s'apparient à aucun corrigé : elles sont ÉCARTÉES,
    # sans quoi « 36 » ouvrirait un exercice fantôme
    assert not any(c.startswith("36") for c in contents)
    assert indigo_index.page_chapter(blocks) == "Nombres entiers"


def test_prof_text_blocks_feed_the_existing_correction_segmentation():
    """Le vrai gain : les blocs ressortent dans le vocabulaire de Mistral, donc
    le découpage des corrigés fonctionne SANS savoir d'où ils viennent — et sans
    un centime d'OCR."""
    doc = _FakeDoc(_PROF_PAGE)
    blocks = indigo_index.correction_blocks(doc, 0)
    page = {"blocks": blocks, "dims": indigo_manual.page_dims(doc, 0), "source_page": 0}
    seg = indigo._segment_corrections_by_numbers(page, {102, 103})
    assert sorted(seg) == ["102", "103"]
    assert "n est pair" in seg["102"]


def test_a_number_already_merged_with_its_correction_still_works():
    """Sur certaines pages, PyMuPDF rend déjà le numéro et son corrigé dans le
    MÊME bloc. Rien à apparier : le bloc passe tel quel, et le découpage y lit
    le numéro comme sur une page OCRisée."""
    doc = _FakeDoc([(40.0, 88.2, 287.6, 103.3, "88\nSpirographe. Le PPCM vaut 60.", 0, 0)])
    contents = [b["content"] for b in indigo_index.correction_blocks(doc, 0)]
    assert contents == ["88\nSpirographe. Le PPCM vaut 60."]
    assert indigo._leading_num(contents[0]) == 88


def test_only_paired_numbers_are_recorded_as_exercise_numbers():
    """Le résumé `numbers` d'une page ne retient QUE les numéros appariés.

    Relire tous les blocs avec `_leading_num` ramasserait les résultats de calcul
    en tête de ligne : mesuré sur le vrai manuel, 36 « numéros » pour 13
    exercices — de quoi désigner des pages de corrigés sans rapport."""
    noisy = list(_PROF_PAGE) + [
        (321.7, 300.0, 543.1, 340.0, "585\n3\n195\n5\n", 8, 0),   # arbre de facteurs
        (321.7, 350.0, 543.1, 390.0, "756 est divisible par 4.", 9, 0),
    ]
    _blocks, numbers = indigo_index.correction_page(_FakeDoc(noisy), 0)
    assert numbers == [102, 103]

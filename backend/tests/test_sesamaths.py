"""Tests de la pipeline Sésamaths (§ extraction manuel PDF).

- sesamaths_pdf : parsing de la table des matières et résolution des pages de
  chapitre, contre le VRAI manuel context/5.pdf (aucun réseau, aucun LLM).
- figures.py : round-trip du type "image" (figures extraites de manuel).
- ensure_bank(source="sesamaths") : intégration de bout en bout en mode mock
  (aucune clé API requise), sans jamais toucher au chemin MathALÉA existant.
"""
import json
import sys
from pathlib import Path

import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import Competency, CompetencyFramework, GeneratedExercise
from app.services import figures, sesamaths, sesamaths_pdf

MANUAL_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "manuals" / "5.pdf"
COMPETENCIES_JSON = Path(__file__).resolve().parents[1] / "app" / "data" / "competencies_fr.json"

pytestmark = pytest.mark.skipif(not MANUAL_PATH.exists(), reason="manuel 5.pdf absent")


@pytest.fixture(scope="module")
def manual_doc():
    return fitz.open(str(MANUAL_PATH))


@pytest.fixture(scope="module")
def toc(manual_doc):
    return sesamaths_pdf.parse_toc(manual_doc)


def _grade_5e_chapter_codes() -> set[str]:
    data = json.loads(COMPETENCIES_JSON.read_text(encoding="utf-8"))
    codes = set()
    for fw in data["frameworks"]:
        if fw["grade_level"] != "5e":
            continue
        for dom in fw["domains"]:
            for chap in dom["chapters"]:
                codes.add(chap["code"])
    return codes


def test_toc_matches_competencies_json_5e(toc):
    assert set(toc.keys()) == _grade_5e_chapter_codes()
    assert toc["A1"]["name"] == "Opérations"
    assert toc["A2"]["name"] == "Nombres relatifs"


def test_chapter_page_range_a1_a2_boundary(manual_doc, toc):
    s1, e1 = sesamaths_pdf.chapter_page_range(manual_doc, toc, "A1")
    s2, _ = sesamaths_pdf.chapter_page_range(manual_doc, toc, "A2")
    assert (s1, e1) == (4, 23)   # vérifié manuellement : pages fichier 5-24
    assert s2 == 24              # A2 démarre juste après A1 (page fichier 25)


def test_chapter_page_range_b4_two_page_lesson_recap(manual_doc, toc):
    # B4 a un rappel de leçon sur 2 pages sans le code "B4" en pied de page —
    # la règle table des matières + 2 doit primer sur le contrôle croisé
    # (cf. commentaire chapter_page_range)
    s, e = sesamaths_pdf.chapter_page_range(manual_doc, toc, "B4")
    assert (s, e) == (96, 109)


def test_toc_captures_series_and_excludes_culture(manual_doc, toc):
    # A1 « Opérations » a 8 Séries dans la ToC dont « Série 8 Culture … » :
    # la carte capture les 8 mais series_page_ranges n'en garde que 7 (Culture exclue).
    a1_series = toc["A1"]["series"]
    assert [s["number"] for s in a1_series] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert a1_series[-1]["is_culture"] is True
    ranges = sesamaths_pdf.series_page_ranges(manual_doc, toc, "A1")
    assert len(ranges) == 7                       # Culture (Série 8) exclue
    assert all(not s.get("is_culture") for s in ranges)
    # Série 1 « Automatismes » : page imprimée 4 -> page fichier index 5
    assert ranges[0]["number"] == 1 and ranges[0]["start_index"] == 5
    # pages d'exercices ordonnées, dédupliquées, chacune annotée de sa Série
    pages = sesamaths_pdf.chapter_exercise_pages(manual_doc, toc, "A1")
    idxs = [p["index"] for p in pages]
    assert idxs == sorted(set(idxs))
    assert pages[0]["index"] == 5 and pages[0]["series_number"] == 1


def test_frozen_map_matches_live_parse(manual_doc, toc):
    # La maquette gelée (app/data/sesamaths_5e_map.json) doit rester alignée sur
    # le parsing live du 5.pdf (régénérer via scripts/build_sesamaths_map.py).
    map_path = (Path(__file__).resolve().parents[1] / "app" / "data"
                / "sesamaths_5e_map.json")
    if not map_path.exists():
        pytest.skip("carte gelée absente")
    frozen = json.loads(map_path.read_text(encoding="utf-8"))["chapters"]
    for code in toc:
        live_pages = [p["index"]
                      for p in sesamaths_pdf.chapter_exercise_pages(manual_doc, toc, code)]
        assert frozen[code]["exercise_pages"] == live_pages, code


def test_render_page_png_is_png(manual_doc):
    png = sesamaths_pdf.render_page_png(manual_doc, 5, dpi=100)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_crop_bbox_png_roundtrip_and_rejects_degenerate(manual_doc, tmp_path):
    ok = sesamaths_pdf.crop_bbox_png(manual_doc, 5, [0.1, 0.1, 0.5, 0.5],
                                     tmp_path / "fig.png", dpi=100)
    assert ok and (tmp_path / "fig.png").exists()
    # bbox dégénérée (aire quasi nulle) -> refus, pas de fichier
    assert sesamaths_pdf.crop_bbox_png(manual_doc, 5, [0.1, 0.1, 0.105, 0.105],
                                       tmp_path / "bad.png", dpi=100) is False
    assert not (tmp_path / "bad.png").exists()


def test_figure_image_type_roundtrip(tmp_path):
    png_path = tmp_path / "fig.png"
    png_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-content")
    fig = figures.validate_figure({"type": "image", "params": {"path": str(png_path)}})
    assert fig is not None
    assert figures.render_figure(fig) == png_path.read_bytes()


def test_figure_image_missing_path_invalid():
    assert figures.validate_figure({"type": "image", "params": {"path": "/no/such/file.png"}}) is None
    assert figures.validate_figure({"type": "image", "params": {}}) is None


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sesamaths_manuals", {"5e": str(MANUAL_PATH)})
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_competency(db, chapter_code: str, chapter_name: str, label: str) -> Competency:
    fw = CompetencyFramework(grade_level="5e", name="Test 5e")
    db.add(fw)
    db.flush()
    comp = Competency(framework_id=fw.id, code=f"test:{chapter_code}", label=label,
                      domain_code=chapter_code[0], domain_name="Domaine test",
                      chapter_code=chapter_code, chapter_name=chapter_name)
    db.add(comp)
    db.commit()
    return comp


def test_ensure_bank_sesamaths_end_to_end_mock(db_session):
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer une division euclidienne")
    rows = sesamaths.ensure_bank(db_session, comp, level=2, min_variants=3)
    assert len(rows) >= 1
    assert all(r.source in sesamaths.SOURCE_POOL for r in rows)
    stored = db_session.query(GeneratedExercise).filter_by(competency_id=comp.id).all()
    assert len(stored) == len(rows)
    # l'extraction vision (mock) alimente bien la source "sesamaths" (vrais
    # exercices du manuel), avec au moins un tableau reconstruit (table_fill)
    assert any(r.source == "sesamaths" for r in stored)
    assert any(r.response_type == "table_fill" for r in stored)


def test_to_candidate_crops_figure_from_bbox(db_session, manual_doc):
    # Un exercice qui référence une figure par bbox doit voir cette zone recadrée
    # du PDF et attachée en figure "image" (extraction des formes géométriques).
    from app.config import settings
    comp = _seed_competency(db_session, "B4", "Triangles", "Calculer un angle")
    out_dir = settings.data_dir / "figs"
    raw = {"kind": "application",
           "statement": "On considère le triangle $ABC$ ci-contre. Que vaut l'angle "
                        "$\\widehat{BAC}$ si $\\widehat{ABC} = 50$ et $\\widehat{ACB} = 60$ ?",
           "correction": "La somme des angles vaut $180$ : $180 - 50 - 60 = 70$.",
           "response_type": "short_text",
           "answer": {"type": "integer", "value": 70},
           "figure_ref": {"bbox_pct": [0.55, 0.2, 0.9, 0.45]},
           "difficulty": 3}
    cand = sesamaths._to_candidate(raw, manual_doc, 98, comp, db_session, set(), out_dir)
    assert cand is not None
    fig = cand["figure_json"]
    assert fig and fig["type"] == "image"
    assert Path(fig["params"]["path"]).exists()


def test_ensure_bank_sesamaths_missing_manual_raises_clear_error(db_session, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "sesamaths_manuals", {})
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer un calcul")
    # PDF introuvable -> erreur CLAIRE, JAMAIS d'invention DeepSeek à la place
    # d'exercices qu'on n'a pas su extraire (exigence explicite).
    with pytest.raises(sesamaths.SesamathsExtractionError) as exc:
        sesamaths.ensure_bank(db_session, comp, level=3, min_variants=1)
    assert "introuvable" in str(exc.value).lower()
    # aucune ligne inventée n'a été stockée
    assert db_session.query(GeneratedExercise).filter_by(competency_id=comp.id).count() == 0


def test_bank_rows_near_level_propagates_missing_manual(db_session, monkeypatch):
    # bank_rows_near_level ne doit PAS avaler l'erreur ni retomber sur une
    # banque vide/inventée : le message clair remonte tel quel à la génération.
    from app.config import settings
    from app.services import exercise_gen
    monkeypatch.setattr(settings, "sesamaths_manuals", {})
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer un calcul")
    with pytest.raises(sesamaths.SesamathsExtractionError):
        exercise_gen.bank_rows_near_level(db_session, comp, level=3, source="sesamaths")

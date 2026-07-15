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

MANUAL_PATH = Path(__file__).resolve().parents[2] / "context" / "5.pdf"
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


def test_extract_chapter_raw_smallest_chapter(manual_doc, toc, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    manual = sesamaths_pdf.get_or_create_manual(db, "5e")
    manual.sha256, manual.toc_json = "test", toc
    db.commit()

    start, end = sesamaths_pdf.chapter_page_range(manual_doc, toc, "B1")
    raw = sesamaths_pdf.extract_chapter_raw(db, manual_doc, manual, "B1", start, end)
    assert len(raw["pages"]) == end - start + 1
    assert any(p["text"] for p in raw["pages"])
    assert any(p["figures"] for p in raw["pages"])
    assert raw["master_pdf_file_object_id"]


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
    comp = _seed_competency(db_session, "B1", "Repérages", "Se repérer sur un axe gradué")
    rows = sesamaths.ensure_bank(db_session, comp, level=3, min_variants=1)
    assert len(rows) >= 1
    assert all(r.source in sesamaths.SOURCE_POOL for r in rows)
    stored = db_session.query(GeneratedExercise).filter_by(competency_id=comp.id).all()
    assert len(stored) == len(rows)


def test_ensure_bank_sesamaths_missing_manual_falls_back_to_deepseek(db_session, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "sesamaths_manuals", {})
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer un calcul")
    rows = sesamaths.ensure_bank(db_session, comp, level=3, min_variants=1)
    # manuel absent -> harvest() renvoie [], le complément DeepSeek Pro seul
    # doit suffire (jamais d'exception bloquante, juste un log d'erreur)
    assert all(r.source == "sesamaths_deepseek" for r in rows)

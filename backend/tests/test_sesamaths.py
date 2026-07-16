"""Tests de la pipeline Sésamaths (§ extraction manuel PDF).

- sesamaths_pdf : parsing de la table des matières et résolution des pages de
  chapitre, contre le VRAI manuel context/5.pdf (aucun réseau, aucun LLM).
- figures.py : round-trip du type "image" (figures extraites de manuel).
- ensure_bank(source="sesamaths") : intégration de bout en bout en mode mock
  (aucune clé API requise), extracteur Mistral OCR + adaptateur Claude.
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


def test_extract_page_range_pdf_is_valid_pdf_with_right_page_count(manual_doc):
    data = sesamaths_pdf.extract_page_range_pdf(manual_doc, 5, 6)
    assert data[:5] == b"%PDF-"
    sub = fitz.open(stream=data, filetype="pdf")
    assert sub.page_count == 2
    sub.close()


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


def test_flatten_blocks_tags_correct_manual_page_across_series():
    # Le champ "page" de chaque bloc aplati doit être décalé par start_index
    # (l'index Mistral repart de 0 dans le mini-PDF de la Série) — c'est ce
    # qui permet à l'adaptateur de repérer un saut de page en cours de lecture
    # et de fusionner un exercice coupé en deux, sans mécanisme spécial.
    raw = {"pages": [
        {"index": 0, "dimensions": {"width": 1000, "height": 2000},
         "blocks": [{"type": "title", "content": "1 Titre",
                    "top_left_x": 100, "top_left_y": 200,
                    "bottom_right_x": 900, "bottom_right_y": 260}]},
        {"index": 1, "dimensions": {"width": 1000, "height": 2000},
         "blocks": [{"type": "text", "content": "suite sur la page suivante",
                    "top_left_x": 100, "top_left_y": 50,
                    "bottom_right_x": 900, "bottom_right_y": 100}]},
    ]}
    flat = sesamaths._flatten_blocks(raw, start_index=5)
    assert [b["page"] for b in flat] == [5, 6]      # décalé par start_index, PAS 0/1
    assert [b["i"] for b in flat] == [0, 1]
    assert flat[0]["type"] == "title" and flat[1]["type"] == "text"
    assert flat[0]["bbox_pct"] == pytest.approx([0.1, 0.1, 0.9, 0.13])


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
    # l'extraction (mock) alimente bien la source "sesamaths" (vrais exercices
    # du manuel), avec au moins un tableau reconstruit (table_fill)
    assert any(r.source == "sesamaths" for r in stored)
    assert any(r.response_type == "table_fill" for r in stored)


def test_raw_extract_json_populated_by_mock_pipeline(db_session):
    # Chaque ligne issue de la pipeline Sésamaths doit conserver les blocs OCR
    # BRUTS dont elle provient, pour l'affichage "avant/après" de la page
    # Banque (cf. content.py::_exercise_out).
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer une division euclidienne")
    sesamaths.ensure_bank(db_session, comp, level=2, min_variants=3)
    stored = (db_session.query(GeneratedExercise)
              .filter_by(competency_id=comp.id, source="sesamaths").all())
    assert stored
    assert all(r.raw_extract_json is not None for r in stored)
    assert all(r.raw_extract_json.get("blocks") for r in stored)


def test_dedup_key_distinguishes_table_fill_with_different_cells():
    # _normalize_statement_for_dedup seul ne suffit pas : pour un table_fill,
    # "statement" ne porte que la consigne commune, souvent générique et
    # IDENTIQUE pour deux exercices aux cellules totalement différentes —
    # cause identifiée des exercices extraits mais silencieusement rejetés.
    from app.services import exercise_gen
    statement = "Calcule."
    expected_a = {"type": "table", "rows": 1, "cols": 1,
                 "cells": [[{"type": "integer", "value": 7}]]}
    expected_b = {"type": "table", "rows": 1, "cols": 1,
                 "cells": [[{"type": "integer", "value": 9}]]}
    key_a = exercise_gen._dedup_key(statement, expected_a)
    key_b = exercise_gen._dedup_key(statement, expected_b)
    assert key_a != key_b
    # même statement, même contenu -> même clé (un VRAI doublon reste détecté)
    assert exercise_gen._dedup_key(statement, dict(expected_a)) == key_a


def test_retired_exercise_not_reinserted(db_session):
    # "Retirer" un exercice (Bank.tsx) doit rester définitif : il ne doit
    # jamais redevenir piochable dans le pool caché de la Série au prochain
    # ensure_bank — cause identifiée de "les mêmes exercices reviennent".
    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer une division euclidienne")
    rows1 = sesamaths.ensure_bank(db_session, comp, level=2, min_variants=3)
    victim = next(r for r in rows1 if r.source == "sesamaths")
    victim_statement = victim.statement
    victim.status = "retired"
    db_session.commit()

    try:
        sesamaths.ensure_bank(db_session, comp, level=2, min_variants=3)
    except ValueError:
        pass  # pool mock minuscule, épuisé au niveau 2 — pas ce qui est testé ici
    active_statements = {r.statement for r in
                         db_session.query(GeneratedExercise)
                         .filter_by(competency_id=comp.id, status="active").all()}
    assert victim_statement not in active_statements


def test_adapt_version_bump_reuses_cached_extraction(db_session, monkeypatch):
    # Bumper ADAPT_PROMPT_VERSION seul doit ré-adapter depuis le JSON brut
    # Mistral déjà en cache, SANS repayer l'OCR — c'est tout l'intérêt de
    # séparer extraction et adaptation en 2 appels.
    from app.models import SesamathsChapterExtraction
    from app.services import providers

    comp = _seed_competency(db_session, "A1", "Opérations", "Automatismes")
    comp.code = "A1.1"
    db_session.commit()

    ocr_calls: list[int] = []
    adapt_calls: list[int] = []
    orig_ocr, orig_adapt = providers.mistral_ocr, providers.claude_json

    def counted_ocr(*a, **kw):
        ocr_calls.append(1)
        return orig_ocr(*a, **kw)

    def counted_adapt(*a, **kw):
        adapt_calls.append(1)
        return orig_adapt(*a, **kw)

    monkeypatch.setattr(providers, "mistral_ocr", counted_ocr)
    monkeypatch.setattr(providers, "claude_json", counted_adapt)

    doc, manual, chapter_code = sesamaths._resolve_chapter(db_session, comp)
    pool1 = sesamaths.ensure_chapter_pool(db_session, doc, manual, chapter_code, comp)
    assert pool1
    assert len(ocr_calls) > 0
    assert len(adapt_calls) > 0

    row = (db_session.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code="A1.1").first())
    assert row.step == "done"

    ocr_calls.clear()
    adapt_calls.clear()
    monkeypatch.setattr(sesamaths, "ADAPT_PROMPT_VERSION", "sesamaths-adapt-TEST")
    pool2 = sesamaths.ensure_chapter_pool(db_session, doc, manual, chapter_code, comp)
    assert pool2
    assert len(ocr_calls) == 0       # aucun nouvel appel OCR
    assert len(adapt_calls) > 0      # ré-adaptation bien effectuée


def test_extract_version_bump_forces_full_reextraction(db_session, monkeypatch):
    # Bumper EXTRACT_PROMPT_VERSION doit au contraire déclencher une
    # ré-extraction complète (la fidélité de lecture a changé).
    from app.services import providers

    comp = _seed_competency(db_session, "A1", "Opérations", "Automatismes")
    comp.code = "A1.1"
    db_session.commit()

    ocr_calls: list[int] = []
    orig_ocr = providers.mistral_ocr
    monkeypatch.setattr(providers, "mistral_ocr",
                        lambda *a, **kw: (ocr_calls.append(1), orig_ocr(*a, **kw))[1])

    doc, manual, chapter_code = sesamaths._resolve_chapter(db_session, comp)
    sesamaths.ensure_chapter_pool(db_session, doc, manual, chapter_code, comp)
    assert len(ocr_calls) > 0

    ocr_calls.clear()
    monkeypatch.setattr(sesamaths, "EXTRACT_PROMPT_VERSION", "sesamaths-extract-TEST")
    pool2 = sesamaths.ensure_chapter_pool(db_session, doc, manual, chapter_code, comp)
    assert pool2
    assert len(ocr_calls) > 0        # ré-extraction complète


def test_leaked_marker_rejected(db_session):
    # Un statement adapté qui laisse fuiter un marqueur d'extraction non
    # transformé ({{lineN}}/{{check}}/{{dot}}) ne doit jamais atteindre le
    # rendu PDF (qui ne connaît que {{blank}}) : rejet déterministe.
    from app.services import exercise_gen
    comp = _seed_competency(db_session, "A1", "Opérations", "Calculer")
    raw = {"kind": "application",
           "statement": "Explique ta démarche pour calculer $12 + 8$. {{line2}}",
           "correction": "$12 + 8 = 20$",
           "response_type": "short_text",
           "answer": {"type": "text", "value": "vingt"}}
    assert exercise_gen._validate_exercise(raw, comp, db_session, set()) is None
    assert "marqueur" in exercise_gen.diagnose_rejection(raw, comp)


def test_to_candidate_crops_figure_from_bbox(db_session, manual_doc):
    # Un exercice dont les source_blocks référencent un bloc "image" doit voir
    # cette zone recadrée du PDF et attachée en figure "image" (extraction des
    # formes géométriques) — bbox déterministe, fournie par l'OCR.
    from app.config import settings
    comp = _seed_competency(db_session, "B4", "Triangles", "Calculer un angle")
    out_dir = settings.data_dir / "figs"
    blocks_by_index = {
        7: {"i": 7, "page": 98, "type": "image", "content": "",
            "bbox_pct": [0.55, 0.2, 0.9, 0.45]},
    }
    item = {"kind": "application",
            "statement": "On considère le triangle $ABC$ ci-contre. Que vaut l'angle "
                         "$\\widehat{BAC}$ si $\\widehat{ABC} = 50$ et $\\widehat{ACB} = 60$ ?",
            "correction": "La somme des angles vaut $180$ : $180 - 50 - 60 = 70$.",
            "response_type": "short_text",
            "answer": {"type": "integer", "value": 70},
            "source_blocks": [7],
            "difficulty": 3}
    cand = sesamaths._to_candidate(item, manual_doc, blocks_by_index, comp, db_session,
                                   set(), out_dir)
    assert cand is not None
    fig = cand["figure_json"]
    assert fig and fig["type"] == "image"
    assert Path(fig["params"]["path"]).exists()
    assert cand["raw_extract_json"]["blocks"][0]["type"] == "image"


def test_multi_field_exercise_stays_one_table_fill(db_session):
    # Manuel, exercice 12 « Calcule chacun des produits suivants » : UN badge
    # numéroté, 10 sous-questions a. à j. => UN exercice table_fill à 10 lignes,
    # pas 10 exercices. La borne historique (6 lignes) le recalait.
    from app.services import exercise_gen
    comp = _seed_competency(db_session, "A1", "Opérations", "Calculer")
    rows = [["$0,4 \\times 7$", "$2,8$"], ["$8 \\times 0,09$", "$0,72$"],
            ["$0,7 \\times 6$", "$4,2$"], ["$0,5 \\times 0,3$", "$0,15$"],
            ["$0,4 \\times 0,06$", "$0,024$"], ["$300 \\times 9$", "$2700$"],
            ["$50 \\times 0,7$", "$35$"], ["$0,02 \\times 9$", "$0,18$"],
            ["$30 \\times 0,06$", "$1,8$"], ["$900 \\times 0,05$", "$45$"]]
    raw = {"kind": "application",
           "statement": "Calcule chacun des produits suivants.",
           "correction": "On multiplie sans la virgule, puis on place la virgule "
                         "selon le nombre de décimales : $0,4 \\times 7 = 2,8$.",
           "response_type": "table_fill",
           "answer": {"type": "table", "rows": 10, "cols": 2,
                      "col_labels": ["Calcul", "Résultat"],
                      "row_labels": [f"{c}." for c in "abcdefghij"],
                      "cells": [[{"type": "text", "value": calc},
                                 {"type": "text", "value": res}] for calc, res in rows]}}
    valid = exercise_gen._validate_exercise(raw, comp, db_session, set())
    assert valid is not None, exercise_gen.diagnose_rejection(raw, comp)
    assert valid["response_type"] == "table_fill"
    assert valid["expected"]["rows"] == 10          # les 10 sous-questions préservées


def test_series_scoped_to_competency(db_session, manual_doc, toc):
    # Dans le manuel, une « Série » EST une compétence (A1.1 « Automatismes » =
    # Série 1). L'extraction ne doit lire que les pages de CETTE Série, pas les
    # 17 du chapitre — sinon on envoie tout le chapitre à l'OCR pour rien.
    import types
    assert sesamaths.series_number_for(types.SimpleNamespace(code="A1.1")) == 1
    assert sesamaths.series_number_for(types.SimpleNamespace(code="A1.7")) == 7
    assert sesamaths.series_number_for(types.SimpleNamespace(code="A1")) is None

    comp = _seed_competency(db_session, "A1", "Opérations", "Automatismes")
    comp.code = "A1.1"
    db_session.commit()
    doc, manual, chapter_code = sesamaths._resolve_chapter(db_session, comp)
    sesamaths.ensure_chapter_pool(db_session, doc, manual, chapter_code, comp)

    from app.models import SesamathsChapterExtraction
    row = (db_session.query(SesamathsChapterExtraction)
           .filter_by(manual_id=manual.id, chapter_code="A1.1").first())
    assert row is not None, "l'état d'extraction est keyé par compétence, pas par chapitre"
    assert row.page_range_json["series_number"] == 1
    # Série 1 : pages fichier 5-6 uniquement (pas les 17 pages du chapitre A1)
    assert (row.page_range_json["start_index"], row.page_range_json["end_index"]) == (5, 6)


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


def test_purge_bank_clears_exercises_and_extraction_state(db_session):
    # Purger seulement GeneratedExercise ne suffirait pas : le pool mis en
    # cache par Série (SesamathsChapterExtraction.validated_json) resservirait
    # le même contenu à la prochaine génération sans jamais ré-extraire.
    from app.models import SesamathsChapterExtraction, SesamathsLlmCache
    from app.routers import content as content_router

    comp = _seed_competency(db_session, "A1", "Opérations", "Effectuer une division euclidienne")
    sesamaths.ensure_bank(db_session, comp, level=2, min_variants=2)
    assert db_session.query(GeneratedExercise).count() > 0
    assert db_session.query(SesamathsChapterExtraction).count() > 0
    assert db_session.query(SesamathsLlmCache).count() > 0

    result = content_router.purge_bank(db_session)
    assert result["exercises_deleted"] > 0
    assert db_session.query(GeneratedExercise).count() == 0
    assert db_session.query(SesamathsChapterExtraction).count() == 0
    assert db_session.query(SesamathsLlmCache).count() == 0

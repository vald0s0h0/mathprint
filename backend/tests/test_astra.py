"""Pipeline Astra (agents/astra) : ce qui transforme la sortie de l'agent Codex en
brouillons MathPrint.

  • les EXEMPLES du prompt (ASTRA.md) passent la validation — un prompt qui
    enseignerait un format refusé ferait échouer chaque run ;
  • chaque défaut typique est refusé avec un message qui nomme l'exercice, le
    niveau et le problème (réponse écrite, guide qui donne la réponse, Facile
    sans guide, caractère non imprimable, figure fautive, clone Base/Facile…) ;
  • une découpe blanchit ses masques et porte le dpi des pages ;
  • l'aperçu est un vrai PDF de copie ;
  • la persistance écrit Base + Facile liés, en BROUILLON, sans ancien guide, et
    ne double jamais un exercice déjà écrit.
"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "agents" / "astra"))

import astra  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Competency, CompetencyFramework, IndigoExercise, IndigoExtraction  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    fw = CompetencyFramework(grade_level="3e", name="T")
    s.add(fw)
    s.flush()
    for i, (code, label) in enumerate((("A3.1", "Simplifier"), ("A3.2", "Développer"))):
        s.add(Competency(framework_id=fw.id, code=code, short_id=code, label=label,
                         domain_code="A", domain_name="Nombres", chapter_code="A3",
                         chapter_name="Calcul littéral", order_index=i))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _examples() -> dict:
    """Les blocs JSON COMPLETS d'ASTRA.md, indexés par leur contenu."""
    text = (REPO / "agents" / "astra" / "ASTRA.md").read_text(encoding="utf-8")
    out = {}
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        data = json.loads(block)
        key = (data.get("source_number") or data.get("response_type") or data.get("kind"))
        out[key] = data
    return out


def _run(db, tmp_path, exercises) -> Path:
    comps = {c.short_id: c.id for c in db.query(Competency).all()}
    run = tmp_path / "run"
    (run / "pages").mkdir(parents=True)
    img = Image.new("RGB", (1280, 1818), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([100, 1260, 450, 1360], outline="black", width=4)      # le visuel utile
    d.rectangle([470, 1250, 490, 1290], fill="black")                   # texte « parasite »
    img.save(run / "pages" / "p075.png")
    payload = {"run_id": "run", "grade": "3e", "dpi": 220,
               "chapter": {"code": "A3", "name": "Calcul littéral"},
               "competencies": [{"code": k, "label": k, "id": v} for k, v in comps.items()],
               "pages": [{"id": "p075", "file": "pages/p075.png", "role": "exercises",
                          "pdf_page": 38, "side": "right", "printed_page": 75,
                          "width_px": 1280, "height_px": 1818}]}
    (run / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    (run / astra.OUTPUT_FILE).write_text(json.dumps(
        {"chapter": "Calcul littéral", "grade": "3e", "exercises": exercises, "skipped": []},
        ensure_ascii=False), encoding="utf-8")
    return run


def _good() -> list[dict]:
    ex = _examples()
    n44 = copy.deepcopy(ex["44"])
    matching_facile = copy.deepcopy(ex["matching"])
    n39 = {"source_number": "39", "source_page": "p075", "source_bbox_px": [50, 200, 900, 500],
           "competency_code": "A3.2", "calculator": "interdite", "figure": None,
           "variants": {
               "base": {"response_type": "matching",
                        "statement": "Relie chaque expression à son écriture développée et réduite.",
                        "left": ["$2(b+6)+7(b-1)$", "$10(b-9)+6(5-b)$", "$3(2b+11)+5(3b-8)$",
                                 "$4(3b-1)+6(5b-9)$"],
                        "right": ["$21b-7$", "$42b-58$", "$9b+5$", "$4b-60$"],
                        "pairs": [[0, 2], [1, 3], [2, 0], [3, 1]]},
               "facile": matching_facile}}
    n36 = {"source_number": "36", "source_page": "p075", "source_bbox_px": [40, 1215, 500, 1382],
           "competency_code": "A3.2", "calculator": "interdite",
           "figure": {**ex["crop"], "masks": [[470, 1244, 492, 1300]]},
           "variants": {
               "base": {"response_type": "qcm_single",
                        "statement": "Samuel a développé le produit ci-dessous. Coche le développement correct de $(4x+2)(5x-1)$.",
                        "choices": ["$20x^2+6x-2$", "$26x-2$", "$20x^2-6x+2$"], "correct": [0],
                        "check": {"kind": "value", "expr": "expand((4*x+2)*(5*x-1))", "choice": 0}},
               "facile": {"response_type": "qcm_single",
                          "statement": "Samuel a développé le produit ci-dessous.\n{{aide}} Le premier produit est $4x \\times 5x$ : un $x$ multiplié par un $x$ donne un $x^2$.\nQuelle erreur Samuel a-t-il faite ?",
                          "choices": ["Il a écrit $20x$ au lieu de $20x^2$", "Il n'a fait aucune erreur"],
                          "correct": [0]}}}
    return [n39, n44, n36]


def test_the_prompt_examples_pass_validation(db, tmp_path):
    rep, contracts = astra.validate(db, _run(db, tmp_path, _good()))
    assert rep.ok, rep.errors
    assert set(contracts) == {"39", "44", "36"}
    assert set(contracts["44"]) == {"base", "facile"}
    # barème CODÉ : 3 QCM à réponse unique = 3 points ; 4 paires reliées = 2 points
    assert contracts["44"]["base"]["grading"]["bareme_points"] == 3
    assert contracts["39"]["base"]["grading"]["bareme_points"] == 2
    # les guides font partie de l'énoncé, jamais du champ `correction`
    assert contracts["44"]["facile"]["correction"] == ""


def test_each_leaf_example_of_the_prompt_is_valid(db, tmp_path):
    ex = _examples()
    for key in ("qcm_single", "checkbox_grid"):
        leaf = ex[key]
        facile = {**leaf, "statement": "{{aide}} Relis la définition du cours avant de choisir.\n"
                  + leaf["statement"]}
        exo = {"source_number": key, "source_page": "p075", "competency_code": "A3.1",
               "variants": {"base": leaf, "facile": facile}}
        rep, _ = astra.validate(db, _run(db, tmp_path / key, [exo]))
        assert rep.ok, (key, rep.errors)


def _defect(db, tmp_path, mutate) -> list[str]:
    exos = _good()
    mutate(exos)
    rep, _ = astra.validate(db, _run(db, tmp_path, exos))
    return rep.errors


@pytest.mark.parametrize("mutate,needle", [
    (lambda e: e[2]["variants"]["facile"].update(
        statement="Samuel a développé le produit ci-dessous.\nQuelle erreur Samuel a-t-il faite ?"),
     "aucun encadré guide"),
    (lambda e: e[2]["variants"]["base"].update(response_type="short_text"),
     "interdit"),
    (lambda e: e[2]["variants"]["facile"].update(
        statement="{{aide}} La bonne réponse est Il a écrit $20x$ au lieu de $20x^2$ bien sûr.\nQuelle erreur ?"),
     "donne la bonne réponse"),
    (lambda e: e[2]["variants"]["base"].update(statement="Coche le résultat ① de $(4x+2)(5x-1)$."),
     "non imprimable"),
    (lambda e: e[1]["figure"]["spec"]["polygons"].append(["A", "Z", "C"]),
     "point inconnu 'Z'"),
    (lambda e: e[2]["variants"]["base"].update(correct=[0, 1]),
     "exactement une"),
    (lambda e: e[2]["variants"]["base"].update(
        correct=[1], check={"kind": "value", "expr": "expand((4*x+2)*(5*x-1))", "choice": 1}),
     "Base : "),
    (lambda e: e[0]["variants"]["base"].update(pairs=[[0, 2], [1, 2], [2, 0], [3, 1]]),
     "ne se relie qu'une fois"),
    (lambda e: e[0]["variants"].update(facile=copy.deepcopy(e[0]["variants"]["base"])),
     "identiques"),
    (lambda e: e[0].update(competency_code="B3.1"), "hors du chapitre"),
    (lambda e: e[2]["figure"].update(bbox_px=[0, 0, 5000, 10]), "hors de la page"),
])
def test_typical_defects_are_named(db, tmp_path, mutate, needle):
    errors = _defect(db, tmp_path, mutate)
    assert any(needle in e for e in errors), errors


def test_several_distinct_guides_are_allowed_without_a_count_warning(db, tmp_path):
    exos = _good()
    v = exos[0]["variants"]["base"]
    v["statement"] = ("{{aide}} Développe chaque produit avant de comparer.\n"
                      "Relie chaque expression.\n{{aide}} Regroupe ensuite les termes semblables.")
    rep, _ = astra.validate(db, _run(db, tmp_path, exos))
    assert rep.ok, rep.errors
    assert not any("encadrés guide en Base" in w for w in rep.warnings)


@pytest.mark.parametrize("intro", ["Bilan : ", "Automatismes, ceinture jaune. ",
                                  "Questions flash : ", "Ceinture noire. ",
                                  "Problème — ", "Énigme : ", "Exercice — "])
def test_editorial_headings_are_rejected_in_questions(db, tmp_path, intro):
    errors = _defect(db, tmp_path, lambda e: e[1]["variants"]["facile"]["questions"][0].update(
        statement=intro + "Quelle est l'aire du rectangle ?"))
    assert any("rubrique éditoriale" in e for e in errors)


def test_editorial_check_keeps_legitimate_story_and_flags_repeated_observation():
    assert not astra._editorial_problems({"statement": "Le bilan de l'association donne les recettes. Calcule leur somme."})
    assert not astra._editorial_problems({"statement": "Problème de rangement : combien de boîtes faut-il ?"})
    assert astra._editorial_problems({"statement": "Observe la figure.\nObserve le polygone."})


def test_all_visible_angles_require_answers_even_in_facile(db, tmp_path):
    ex = _good()[2]
    ex["figure"]["items"] = [f"Angle {i}" for i in range(1, 7)]
    for kind in astra.VARIANTS:
        ex["variants"][kind] = {
            "response_type": "checkbox_grid", "statement": "Classe chaque angle de la figure.",
            "cols": ["Aigu", "Obtus"],
            "rows": [{"label": f"Angle {i}", "correct": i % 2} for i in range(1, 7)]}
    facile = ex["variants"]["facile"]
    facile["statement"] += "\n{{aide}} Compare chaque ouverture à un coin de feuille."
    facile["rows"] = facile["rows"][:3]
    run = _run(db, tmp_path, [ex])
    rep, _ = astra.validate(db, run)
    assert any("Facile" in e and "Angle 6" in e and "sans question/réponse" in e for e in rep.errors)
    # A failed preview must not silently omit cards or overwrite the last PDF.
    (run / "preview").mkdir()
    previous = run / "preview" / "preview.pdf"
    previous.write_bytes(b"last reviewed preview")
    with pytest.raises(SystemExit, match="PDF complet"):
        astra.preview(db, run)
    assert previous.read_bytes() == b"last reviewed preview"
    with pytest.raises(SystemExit, match="Validation en échec"):
        astra.persist(db, run)
    # Restoring the complete answer grid resolves the error.
    facile["rows"] = copy.deepcopy(ex["variants"]["base"]["rows"])
    (run / astra.OUTPUT_FILE).write_text(json.dumps({"exercises": [ex]}))
    rep, _ = astra.validate(db, run)
    assert rep.ok, rep.errors


def test_numbered_geo_candidates_must_all_remain_selectable():
    fig = {"kind": "geo", "spec": {"texts": [{"text": str(i)} for i in (1, 2, 3)]}}
    v = {"response_type": "qcm_single", "statement": "Choisis la figure.",
         "choices": ["Figure 1", "Figure 3"], "correct": [1]}
    assert any("Figure 2" in e for e in astra._figure_coverage_problems(v, fig))
    v["choices"].insert(1, "Figure 2")
    assert not astra._figure_coverage_problems(v, fig)


def test_question_figure_can_serve_following_questions_but_not_another_panel():
    fig = {"kind": "crop", "items": ["Angle a", "Angle b"]}
    v = {"response_type": "composite", "statement": "", "questions": [
        {"statement": "Mesure Angle a.", "figure": fig}, {"statement": "Mesure Angle b."}]}
    assert not astra._figure_coverage_problems(v, None)
    v["questions"][1]["figure"] = {"kind": "crop"}
    assert any("Angle b" in e for e in astra._figure_coverage_problems(v, None))


def test_repeated_guides_trigger_a_semantic_review_warning(db, tmp_path):
    exos = _good()
    guide = "Repère le sommet puis compare son ouverture à un angle droit."
    for ex in exos:
        ex["variants"]["facile"]["statement"] += "\n{{aide}} " + guide
    rep, _ = astra.validate(db, _run(db, tmp_path, exos))
    assert any("Guide identique dans 3 sources" in w for w in rep.warnings)


def test_multiple_question_guides_survive_persistence(db, tmp_path):
    ex = _good()[1]
    run = _run(db, tmp_path, [ex])
    rep, _ = astra.validate(db, run)
    assert rep.ok, rep.errors
    astra.persist(db, run)
    row = db.query(IndigoExercise).filter_by(variant_kind="facile").one()
    qs = row.expected_json["parts"]
    assert "{{aide}}" in qs[0]["statement"]
    assert "{{aide}}" in qs[2]["statement"]
    assert "Compare les deux expressions" in qs[2]["statement"]


def test_a_crop_whitens_its_masks_and_keeps_the_page_dpi(db, tmp_path):
    run = _run(db, tmp_path, _good())
    figs = astra.build_figures(run)
    crop = Image.open(figs[("36", "base")])
    assert round(crop.info["dpi"][0]) == 220
    # le rectangle noir « parasite » (x 470-490) a été blanchi, le visuel gardé
    px = crop.convert("L").load()
    dark = sum(1 for x in range(crop.width) for y in range(crop.height) if px[x, y] < 100)
    assert dark > 0
    assert crop.width < 420                     # rogné sur le visuel, masque compris
    assert ("44", "facile") in figs and figs[("44", "base")] == figs[("44", "facile")]


def test_preview_is_a_real_copy_pdf(db, tmp_path):
    out = astra.preview(db, _run(db, tmp_path, _good()))
    assert (out / "preview.pdf").stat().st_size > 0
    assert list(out.glob("page-*.png"))
    assert "n°44  Facile" in (out / "index.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("badge", ["probleme", "enigme"])
def test_printed_problem_keeps_title_without_editorial_type(db, tmp_path, badge):
    import fitz
    data = _good()
    ex = data[0]
    ex.update(badge=badge, title="Distance inaccessible", difficulty=1)
    ex["variants"] = {"original": ex["variants"]["base"]}
    out = astra.preview(db, _run(db, tmp_path, data))
    with fitz.open(out / "preview.pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf)
    assert "Distance inaccessible" in text
    assert "Problème" not in text and "Énigme" not in text


def test_persist_writes_linked_drafts_once(db, tmp_path):
    run = _run(db, tmp_path, _good())
    res = astra.persist(db, run)
    assert len(res["written"]) == 6 and not res["skipped"]
    rows = db.query(IndigoExercise).all()
    bases = {r.source_number: r for r in rows if r.variant_kind == "base"}
    faciles = [r for r in rows if r.variant_kind == "facile"]
    assert len(bases) == 3 and len(faciles) == 3
    for f in faciles:
        assert f.derived_from_id == bases[f.source_number].id
        assert f.difficulty == 1 and "{{aide}}" in (f.statement + json.dumps(f.expected_json))
    for r in rows:
        assert r.status == "draft" and r.correction_guide == ""
        assert r.raw_ocr_json["pipeline"] == "astra" and r.model == astra.MODEL
    geo = bases["44"]
    assert geo.has_figure and (settings.data_dir / geo.figure_path).exists()
    assert geo.raw_ocr_json["figure_spec"]["kind"] == "geo"
    assert bases["36"].figure_box_json["masks"]
    assert (settings.data_dir / bases["39"].crop_path).exists()    # extrait source de relecture
    assert db.query(IndigoExtraction).one().status == astra.ST_DONE

    again = astra.persist(db, run)
    assert not again["written"] and len(again["skipped"]) == 3
    assert db.query(IndigoExercise).count() == 6

    replaced = astra.persist(db, run, replace=True)
    assert len(replaced["written"]) == 6
    assert db.query(IndigoExercise).count() == 6


def test_persist_refuses_an_invalid_run(db, tmp_path):
    exos = _good()
    exos[0]["variants"]["base"]["response_type"] = "short_text"
    with pytest.raises(SystemExit):
        astra.persist(db, _run(db, tmp_path, exos))
    assert db.query(IndigoExercise).count() == 0


def test_chapter_lookup_is_tolerant():
    assert astra.find_chapter("thales")["code"] == "C2"
    assert astra.find_chapter("3e B3")["name"] == "Fonctions affines"
    assert astra.find_chapter("B3", "3e")["name"] == "Fonctions affines"
    assert astra.find_chapter("CALCUL LITTERAL")["code"] == "A3"
    with pytest.raises(SystemExit):
        astra.find_chapter("fonction")                  # Fonctions / Fonctions affines


def test_chapter_lookup_resolves_the_grade():
    """Les codes A1, B3… existent dans les deux manuels : le niveau vient du
    préfixe, de --grade, ou d'un nom propre à un seul manuel — jamais deviné."""
    assert astra.find_chapter("Angles") == {**astra.find_chapter("6e B2"), "grade": "6e"}
    assert astra.find_chapter("thales")["grade"] == "3e"
    assert astra.find_chapter("6ème triangles")["code"] == "B3"   # exact : pas « Triangles rectangles »
    assert astra.find_chapter("6e 10")["name"] == "Triangles"     # numéro du livre
    for ambiguous in ("B3", "Probabilités"):
        with pytest.raises(SystemExit):
            astra.find_chapter(ambiguous)
    with pytest.raises(SystemExit):
        astra.find_chapter("6e Thalès")                 # chapitre absent du manuel 6e


@pytest.mark.parametrize("grade", ["3e", "6e"])
def test_chapter_table_matches_the_competency_framework(grade):
    """Chaque chapitre de la table d'Astra existe dans le référentiel du niveau
    (sinon `prepare` échoue : « aucune compétence en base »), et ses pages sont
    ordonnées : leçon avant exercices, sans chevauchement d'un chapitre à l'autre."""
    data = json.loads((REPO / "backend/app/data/competencies_fr.json").read_text(encoding="utf-8"))
    fw = next(f for f in data["frameworks"] if f["grade_level"] == grade)
    framework = {ch["code"]: [c["code"] for c in ch["competencies"]]
                 for dom in fw["domains"] for ch in dom["chapters"]}
    table = astra.chapters(grade)
    assert [ch["code"] for ch in table] == list(framework)
    last = 0
    for ch in table:
        (l0, l1), (e0, e1) = ch["lesson"], ch["exercises"]
        assert last < l0 <= l1 < e0 <= e1, ch["code"]
        last = e1
        if "competency_pages" in ch:
            assert list(ch["competency_pages"]) == framework[ch["code"]]


def test_every_figure_example_of_the_prompt_renders():
    from app.services import figures
    ex = _examples()
    specs = [ex["chart"], ex["44"]["figure"]]
    for fig in specs:
        assert figures.figure_error({"type": fig["kind"], "params": fig["spec"]}) is None


def test_chapter_problem_replaces_old_pair_without_leaving_a_derivative(db, tmp_path):
    data = _good()
    run = _run(db, tmp_path, data)
    astra.persist(db, run)
    ex = data[0]
    source = ex['source_number']
    ex.update(badge='probleme', title='Un problème du chapitre', difficulty=3, chapter_code='A3')
    ex.pop('competency_code')
    ex['variants'] = {'original': ex['variants']['base']}
    astra.dump_json(run / astra.OUTPUT_FILE, {'exercises':data})
    rep, contracts=astra.validate(db,run)
    assert rep.ok,rep.errors
    assert set(contracts[source]) == {'original'}
    astra.persist(db,run,replace=True)
    rows=db.query(IndigoExercise).filter_by(source_number=source).all()
    assert len(rows)==1
    assert rows[0].variant_kind=='original' and rows[0].difficulty==3
    assert rows[0].derived_from_id is None
    assert rows[0].payload_json['kind']=='probleme'


def test_empty_context_and_question_figure_survive_persistence(db, tmp_path):
    data=_good()
    ex=next(e for e in data if e['source_number']=='44')
    v=ex['variants']['base']
    v['statement']=''
    v['figure']=None
    v['questions'][1]['figure']=ex['figure']
    run=_run(db,tmp_path,data)
    rep,_=astra.validate(db,run)
    assert rep.ok,rep.errors
    astra.persist(db,run)
    row=db.query(IndigoExercise).filter_by(source_number='44',variant_kind='base').one()
    assert row.statement==''
    assert row.expected_json['parts'][1]['figure']['type']=='geo'
    assert row.grading_json['parts'][1]['figure']==row.expected_json['parts'][1]['figure']


def test_problem_page_and_card_preview_route(db, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deps import current_user
    from app.db import get_db
    from app.routers.content import router as content_router
    from app.routers.indigo import list_exercises
    from app.services import indigo
    data=_good()
    ex=data[0]
    ex.update(badge='probleme',title='Titre de chapitre',difficulty=1)
    ex['variants']={'original':ex['variants']['base']}
    run=_run(db,tmp_path,data)
    astra.persist(db,run)
    cid=db.query(Competency).first().id
    rows=list_exercises(chapter_id=cid,db=db)
    assert len(rows)==1 and rows[0]['ref'].startswith('A3-')
    comp_rows=list_exercises(competency_id=rows[0]['competency_id'],category='exercise',db=db)
    assert not any(r['badge_type']=='probleme' for r in comp_rows)
    app=FastAPI();app.include_router(content_router)
    app.dependency_overrides[current_user]=lambda: object()
    app.dependency_overrides[get_db]=lambda: db
    with TestClient(app) as client:
        r=client.post('/api/content/card-preview.png',json={'exercise':rows[0], 'show_answers':True})
        assert r.status_code==200,r.text
        assert r.content.startswith(b'\x89PNG')
        bad={**rows[0],'figure_url':'https://example.com/x.png'}
        assert client.post('/api/content/card-preview.png',json={'exercise':bad}).status_code==422

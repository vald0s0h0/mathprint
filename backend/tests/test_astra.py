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


def test_base_with_several_guides_is_only_a_reserve(db, tmp_path):
    exos = _good()
    v = exos[0]["variants"]["base"]
    v["statement"] = ("{{aide}} Développe chaque produit avant de comparer.\n"
                      "Relie chaque expression.\n{{aide}} Regroupe ensuite les termes semblables.")
    rep, _ = astra.validate(db, _run(db, tmp_path, exos))
    assert rep.ok, rep.errors
    assert any("encadrés guide en Base" in w for w in rep.warnings)


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
    assert astra.find_chapter("B3")["name"] == "Fonctions affines"
    assert astra.find_chapter("CALCUL LITTERAL")["code"] == "A3"
    with pytest.raises(SystemExit):
        astra.find_chapter("fonction")                  # Fonctions / Fonctions affines


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

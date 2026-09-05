"""Extraction Vision du mode QCM multipass : contrat, crops et non-rejet."""
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import ProviderConfig
from app.services import (indigo, indigo_llm, indigo_multipass, indigo_vision,
                          providers)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _comp(code, label):
    return SimpleNamespace(id=f"id-{code}", code=code, short_id=code, label=label,
                           chapter_code="A1", chapter_name="Nombres",
                           domain_code="A", domain_name="Nombres")


def test_vision_boxes_are_normalized_and_mapped_to_the_real_raster():
    assert indigo_vision.pixel_box([100, 200, 900, 800], 2000, 1000) == (
        200, 200, 1800, 800)
    # ordre inversé et dépassements sont réparés, boîte dégénérée refusée
    assert indigo_vision.pixel_box([1100, 900, -20, 100], 1000, 1000) == (
        0, 100, 1000, 900)
    assert indigo_vision.pixel_box([10, 10, 11, 11], 1000, 1000) is None


def test_a_figure_without_a_usable_crop_degrades_instead_of_losing_the_exercise():
    """Le crop était EXIGÉ : un exercice entier disparaissait faute d'un
    rectangle, et la colonne échouait après deux réparations en emportant ses
    voisins. Une image manquante ne rend pas un exercice inutilisable — le
    professeur l'ajoute à la relecture. On dégrade, on ne perd rien."""
    base = {"number": "12", "statement": "Observe puis calcule.",
            "exercise_bbox": [10, 10, 900, 900], "has_figure": True,
            "figure_description": "Un histogramme des effectifs."}
    for broken in ({}, {"figure_crop": {"page": "next", "bbox": [20, 30, 400, 500]}},
                   {"figure_crop": {"page": "current", "bbox": [1, 1, 2, 2]}}):
        out = indigo_vision._page_contract({"exercises": [{**base, **broken}]})
        item = out["exercises"][0]
        assert item["has_figure"] is False and item["figure_missing_crop"] is True
        # la DESCRIPTION reste : elle porte les données du dessin
        assert item["figure_description"] == "Un histogramme des effectifs."
    # un crop exploitable est conservé tel quel
    ok = indigo_vision._page_contract({"exercises": [{
        **base, "figure_crop": {"page": "current", "bbox": [20, 30, 400, 500]}}]})
    assert ok["exercises"][0]["has_figure"] is True


def test_an_exercise_without_a_number_or_a_statement_is_still_a_hard_error():
    """Le contrat garde ce sans quoi il n'y a pas d'exercice — et le retry
    correctif de DeepSeek ne sert qu'à ça."""
    with pytest.raises(ValueError, match="number"):
        indigo_vision._page_contract({"exercises": [{"statement": "Calcule tout."}]})
    with pytest.raises(ValueError, match="statement"):
        indigo_vision._page_contract({"exercises": [{"number": "12"}]})


def test_one_bad_exercise_box_is_repaired_without_losing_the_page():
    data = {"exercises": [
        {"number": "17", "statement": "Lis le diagramme puis calcule.",
         "has_figure": False, "exercise_bbox": [10, 10, 10, 20],
         "number_bbox": [30, 100, 80, 140]},
        {"number": "18", "statement": "Construis un histogramme.",
         "has_figure": False, "exercise_bbox": [10, 300, 450, 600],
         "number_bbox": [30, 300, 80, 340]}]}
    assert indigo_vision._page_contract(data) is data
    # L'image ne contient QU'UNE colonne : la boîte réparée en occupe toute la
    # largeur, et s'arrête au badge suivant. Plus rien à deviner sur « à quelle
    # colonne appartient l'exercice d'après » — c'est le suivant, point.
    assert data["exercises"][0]["exercise_bbox"] == [0.0, 88.0, 1000.0, 292.0]
    assert data["exercises"][0]["exercise_bbox_repaired"] is True


def test_page_extraction_is_isolated_and_carries_the_active_title(monkeypatch):
    """Une COLONNE par appel : quatre par raster, dans l'ordre de lecture."""
    doc = SimpleNamespace(page_count=2)

    def raster(_doc, idx):
        page = np.full((80, 60, 3), idx * 40, dtype=np.uint8)
        # bandeau rose en haut de la 1re colonne : c'est la CV qui le trouve,
        # le modèle ne fait que le lire (§ find_banners)
        page[4:20, 5:16] = indigo_vision.BANNER_BGR
        return page

    monkeypatch.setattr(indigo_vision.indigo_manual, "raster_page", raster)
    calls = []

    def fake(_db, _system, payload, images, _cid, validator=None):
        calls.append((payload, images, validator))
        if payload["current_page"] == 1 and payload["column_index"] == 1:
            assert payload["banners"], "la CV doit signaler le bandeau à lire"
            return {"banners": [{"title": "Calculer un PGCD"}],
                    "exercises": [{"number": "34",
                        "statement": "34 Calcule le PGCD de $12$ et $18$.",
                        "has_figure": True,
                        "figure_description": "Triangle $ABC$ rectangle en $A$.",
                        "exercise_bbox": [20, 30, 950, 980],
                        "number_bbox": [20, 30, 100, 100],
                        "figure_crop": {"page": "current", "bbox": [100, 120, 800, 700]}}]}
        return {"banners": [], "exercises": []}

    monkeypatch.setattr(indigo_vision.indigo_llm, "call_vision", fake)
    out = indigo_vision.extract_pages(None, doc, "3e", [0, 1])
    assert len(out) == 1 and out[0]["number"] == "34"
    assert out[0]["figure_page"] == 0
    assert out[0]["text"].startswith("Calcule")
    # boîte locale à la 1re colonne (0 → 1/4 de la double page) remise dans le
    # repère du raster complet
    assert indigo_vision._page_box([0, 20, 1000, 900], .5, .75) == [
        500.0, 20.0, 750.0, 900.0]
    assert calls[0][0]["image_order"] == ["current"]
    assert [c[0]["column_index"] for c in calls[:4]] == [1, 2, 3, 4]
    assert all(c[0]["column_count"] == 4 for c in calls)
    assert calls[1][0]["previous_competency_title"] == "Calculer un PGCD"
    # les numéros se suivent d'une colonne à l'autre : le repère est transmis
    assert calls[1][0]["previous_number"] == "34"
    # 2 pages × 4 colonnes, une image par appel
    assert len(calls) == 8 and all(len(call[1]) == 1 for call in calls)


def test_a_range_starting_mid_section_never_sends_pages_outside_the_range(monkeypatch):
    doc = SimpleNamespace(page_count=3)
    monkeypatch.setattr(indigo_vision.indigo_manual, "raster_page",
                        lambda _doc, idx: np.full((20, 20, 3), idx, dtype=np.uint8))
    seen = {}

    def fake(_db, _system, payload, images, _cid, validator=None):
        seen.update(payload)
        seen["image_count"] = len(images)
        return {"active_competency_title": "Calculer un PGCD", "exercises": []}

    monkeypatch.setattr(indigo_vision.indigo_llm, "call_vision", fake)
    indigo_vision.extract_pages(None, doc, "3e", [1])
    assert seen["image_order"] == ["current"]
    assert seen["image_count"] == 1


def test_filter_never_rejects_a_vision_source_and_associates_the_title(monkeypatch):
    comps = [_comp("A1.1", "Reconnaître un nombre premier"),
             _comp("A1.2", "Calculer un PGCD")]
    monkeypatch.setattr(indigo_multipass, "_call", lambda *_a, **_k: {
        "verdict": "reject", "reason": "doute inutile",
        "competency_code": "A1.2", "enonce": ""})
    manual = {"number": "34", "statement": "Calcule le PGCD de $12$ et $18$.",
              "competency_title": "Calculer un PGCD", "vision_extracted": True,
              "has_figure": False}
    source, reason = indigo_multipass._pass_filter(None, comps, "3e", manual, "cid")
    assert source is not None and reason == ""
    assert source.statement == manual["statement"]
    assert indigo_multipass._resolve_competency(source, manual, comps)[0].code == "A1.2"


def test_an_exact_visual_title_wins_over_a_wrong_llm_classification():
    comps = [_comp("B4.1", "Représenter graphiquement des données"),
             _comp("B4.2", "Calculer une moyenne")]
    source = indigo_multipass.Source(
        statement="Lis puis calcule.", competency_code="B4.2")
    manual = {"competency_title": "Représenter graphiquement des données"}
    comp, confirmed = indigo_multipass._resolve_competency(source, manual, comps)
    assert comp.code == "B4.1" and confirmed is True


def test_filter_cannot_shorten_a_vision_source_aggressively(monkeypatch):
    comp = _comp("A1.2", "Calculer un PGCD")
    monkeypatch.setattr(indigo_multipass, "_call", lambda *_a, **_k: {
        "verdict": "keep", "competency_code": "A1.2", "enonce": "Calcule."})
    original = ("Calcule le PGCD de $12$ et $18$, puis explique chaque étape "
                "de ton raisonnement.")
    source, _ = indigo_multipass._pass_filter(None, [comp], "3e", {
        "number": "34", "statement": original, "competency_title": comp.label,
        "vision_extracted": True, "has_figure": False}, "cid")
    assert source.statement == original
    assert source.original_statement == original


def test_filter_cannot_replace_a_vision_source_even_at_the_same_length(monkeypatch):
    comp = _comp("B4.1", "Représenter graphiquement des données")
    original = ("1. Quel pourcentage est réservé à l'alimentation ?\n"
                "2. Combien de litres sont consacrés aux sanitaires ?")
    monkeypatch.setattr(indigo_multipass, "_call", lambda *_a, **_k: {
        "verdict": "keep", "competency_code": "B4.1",
        "enonce": "x" * len(original),
        "taches_source": ["Calculer le pourcentage alimentaire.",
                           "Calculer les litres des sanitaires."]})
    source, _ = indigo_multipass._pass_filter(None, [comp], "3e", {
        "number": "17", "statement": original, "competency_title": comp.label,
        "vision_extracted": True, "has_figure": True,
        "figure_description": "Diagramme des usages de 150 L."}, "cid")
    assert source.statement == original
    assert len(source.tasks) == 2


def test_every_source_task_must_survive_in_every_variant():
    source = indigo_multipass.Source(
        statement="Deux questions sur un diagramme.",
        tasks=["Calculer un pourcentage.", "Calculer un volume."])
    simple = {"response_type": "qcm_single", "statement": "Calcule.",
              "choices": ["1", "2", "3"], "correct": [0], "guide": "Une piste utile."}
    trio = {kind: dict(simple) for kind in indigo_multipass.VARIANTS}
    problems = indigo_multipass._source_fidelity_problems(trio, source)
    assert len(problems) == len(indigo_multipass.VARIANTS)
    assert all("2 dans la source" in problem for problem in problems)


def test_a_repeated_answer_is_a_note_for_the_reviewer_not_a_refusal():
    """Le soupçon est signalé, jamais opposé — et c'est ce qui le rend utile.

    Python ne peut pas distinguer « la réponse est recopiée dans la consigne »
    de « la donnée nécessaire au calcul est dans la consigne ». Sur toute
    lecture de tableau ou de graphique, la bonne réponse EST l'un des nombres
    écrits au-dessus : en faire une porte revenait à refuser la compétence
    entière (pages 86-87, quatre tentatives brûlées par exercice)."""
    repeated = {"response_type": "qcm_multiple",
                "statement": "Parmi $2$, $4$ et $5$, coche les nombres premiers.",
                "choices": ["$2$", "$4$", "$5$"], "correct": [0, 2]}
    assert indigo_multipass._response_repetition_notes(repeated)
    revealed = {"response_type": "qcm_single",
                "statement": ("Un Français consomme 150 litres par jour. "
                              "Quelle quantité consomme-t-il ?"),
                "choices": ["15 L", "150 L", "1500 L"], "correct": [1]}
    assert any("bonne réponse numérique" in note
               for note in indigo_multipass._response_repetition_notes(revealed))
    operation = {"response_type": "qcm_single",
                 "statement": "La consommation totale est 150 L. Quel calcul convient ?",
                 "choices": ["150 × 0,2", "150 × 20", "150 ÷ 0,2"],
                 "correct": [0]}
    assert indigo_multipass._response_repetition_notes(operation) == []


def test_an_unreadable_check_is_dropped_but_a_contradictory_one_is_kept():
    unreadable = indigo_multipass._normalize_variant({
        "response_type": "qcm_multiple", "statement": "Coche les valeurs justes.",
        "choices": ["6", "7", "20"], "correct": [1],
        "check": {"kind": "set", "exprs": ["6", "7", "20"]}})
    assert unreadable["check"] == {"kind": "none"}
    contradictory = indigo_multipass._normalize_variant({
        "response_type": "qcm_multiple", "statement": "Coche les valeurs justes.",
        "choices": ["6", "7", "20"], "correct": [1],
        "check": {"kind": "set",
                  "exprs": ["Eq(6,7)", "Eq(7,8)", "Eq(20,20)"]}})
    assert contradictory["check"]["kind"] == "set"


def test_deepseek_multimodal_request_keeps_images_in_the_user_message(db, monkeypatch):
    db.add(ProviderConfig(provider="deepseek-flash", model="deepseek-v4-flash",
                          encrypted_secret="test", active=True))
    db.commit()
    sent = {}

    class Response:
        def raise_for_status(self): return None
        def json(self):
            return {"choices": [{"message": {"content": '{"exercises":[]}'},
                                  "finish_reason": "stop"}], "usage": {}}

    def fake_post(_url, **kwargs):
        sent.update(kwargs["json_body"])
        return Response()

    monkeypatch.setattr(providers, "_post_with_deadline", fake_post)
    raw = b"not-a-real-png"
    providers.deepseek_json(
        db, "indigo_mp_vision_extract", "court", {"current_page": 1},
        model=settings.indigo_multipass_vision_model, images=[raw],
        thinking=False)
    content = sent["messages"][1]["content"]
    assert sent["model"] == "deepseek-v4-flash-vision-exp"
    assert content[0]["type"] == "text" and content[1]["type"] == "image_url"
    assert content[1]["image_url"]["detail"] == "original"
    assert base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == raw


def test_multipass_stages_keep_the_flash_model_even_if_the_ui_mode_changes(db):
    # Le mode par défaut de ce test n'est pas multipass ; le nom de la passe
    # suffit à verrouiller le modèle du run mis en file auparavant.
    assert indigo_llm.model_for(db, "mp_filter") == settings.indigo_multipass_model


def test_vision_range_is_stored_as_inclusive_one_based_pages(db, monkeypatch):
    monkeypatch.setattr(indigo.indigo_manual, "open_doc",
                        lambda *_a, **_k: SimpleNamespace(page_count=12))
    ext = indigo.create_vision_extraction(db, "3e", 8, 6, created_by="admin")
    target = ext.targets_json[0]
    assert target["eleve_page_start"] == 6
    assert target["eleve_page_end"] == 8
    assert target["eleve_pages"] == [5, 6, 7]
    with pytest.raises(ValueError, match="entre 1 et 12"):
        indigo.create_vision_extraction(db, "3e", 1, 13)


# ------------------------------------------------ colonnes, brouillons, sandbox

def test_the_columns_are_cut_inside_the_page_not_across_the_whole_raster():
    """Le raster est une CAPTURE de lecteur PDF : ses bords portent des flèches,
    des boutons et une vignette. Découper la largeur totale en quatre plaçait
    deux coupes au milieu des colonnes 1 et 4 — sur les pages 86-87, trois
    exercices lus deux fois et trois purement perdus. Les coupes doivent tomber
    dans les gouttières, où il n'y a rien à couper."""
    bounds = indigo_vision.columns(1755)
    assert len(bounds) == 4
    # la boîte de contenu mesurée sur le manuel 3e : 123 px à 1592 px sur 1755
    assert bounds[0][0] == 123 and bounds[-1][1] == 1592
    # colonnes jointives et de largeur régulière
    assert [b[0] for b in bounds[1:]] == [b[1] for b in bounds[:-1]]
    assert {b[1] - b[0] for b in bounds} <= {367, 368}
    # une image dégénérée ne casse pas l'extraction
    assert indigo_vision.columns(1) == [(0, 1)]


def test_the_same_number_read_twice_keeps_the_most_complete_reading():
    """Filet de sécurité du découpage : les numéros du manuel se suivent et ne
    se répètent pas. Si une coupe tombe malgré tout dans une colonne, l'exercice
    est lu partiellement des deux côtés — on garde le texte le plus long, celui
    auquel il manque le moins."""
    kept = indigo_vision._dedupe_by_number([
        {"number": "40", "text": "Calcule la"},
        {"number": "41", "text": "Range ces nombres."},
        {"number": "40", "text": "Calcule la médiane de cette série."},
    ])
    assert [k["number"] for k in kept] == ["40", "41"]          # ordre de lecture
    assert kept[0]["text"] == "Calcule la médiane de cette série."


def test_a_family_without_a_usable_variant_still_leaves_a_draft(db, monkeypatch):
    """« l'extraction Vision est déjà bonne » : un exercice correctement LU que
    les cinq passes n'ont pas su mettre en cases (« recopie et complète ce
    tableau ») reste un exercice que le professeur peut reprendre — s'il le
    voit. Le repli écrit la source telle quelle, en brouillon."""
    from app.models import Competency, CompetencyFramework, IndigoExercise

    fw = CompetencyFramework(id="fw", grade_level="3e", name="3e")
    comp = Competency(id="c1", framework_id="fw", code="B4.3", short_id="B4.3",
                      label="Déterminer une médiane", order_index=1,
                      chapter_code="B4", chapter_name="Statistiques",
                      domain_code="B", domain_name="Données")
    db.add_all([fw, comp])
    db.commit()

    source = {"number": "46", "text": "Recopier et compléter ce tableau.",
              "source_page": 86, "competency_title": "Déterminer une médiane",
              "has_figure": False, "figure_description": "",
              "exercise_bbox": None, "figure_bbox": None, "vision_extracted": True}
    monkeypatch.setattr(indigo_vision, "extract_pages",
                        lambda *a, **k: [source])
    monkeypatch.setattr(indigo, "_prepare_vision_exercise",
                        lambda *a, **k: (IndigoExercise(
                            id="ex-46", competency_id=comp.id, grade_level="3e",
                            source_number="46", badge_type="exercice"),
                            {"number": "46", "statement": source["text"],
                             "correction": "", "has_figure": False,
                             "competency_title": source["competency_title"],
                             "vision_extracted": True}))
    monkeypatch.setattr(indigo_multipass, "run_family_pair", lambda *a, **k: [
        indigo_multipass.Family(number="46", state=indigo_multipass.REJECTED_GENERATION,
                                attempts=4, competency_id="c1", competency_code="B4.3")])
    monkeypatch.setattr(indigo.indigo_offpeak, "wait_until_open", lambda *a, **k: None)

    made = indigo._run_multipass_vision(db, None, "3e", [85], "ext-1", lambda _m: None, [])
    rows = db.query(IndigoExercise).all()
    assert made == 1 and len(rows) == 1
    assert rows[0].status == "draft" and rows[0].statement.startswith("Recopier")
    # et c'est un badge ROUGE : le QCM reste à écrire, ce n'est pas un simple
    # « à regarder » que le professeur peut valider d'un clic.
    assert any("aucune variante QCM exploitable" in n
               for n in rows[0].raw_ocr_json["review_blocking"])


def test_the_teacher_correction_lookup_moved_out_of_vision_prep(db, monkeypatch):
    """Depuis la passe CONTEXTE (§ indigo_multipass._pass_context, révision du
    04/09 soir), le corrigé du manuel prof n'est PLUS cherché ici, sur un
    chapitre DEVINÉ avant tout rattachement réel : `manuals[i]` ne porte plus
    de champ `correction` du tout. C'est `indigo_multipass._resolve_family`
    qui le cherche lui-même, sur la compétence RÉELLEMENT résolue par sa
    propre passe 1 — et qui reçoit TOUS les candidats quand le chapitre du
    manuel prof en porte plusieurs (§ test_an_indexed_teacher_correction_
    reaches_the_context_pass, dans test_indigo_multipass.py, qui couvre cette
    partie-là du pipeline en profondeur)."""
    from app.models import Competency, CompetencyFramework, IndigoExercise

    fw = CompetencyFramework(id="fw", grade_level="3e", name="3e")
    comp = Competency(id="c1", framework_id="fw", code="A1.1", short_id="A1.1",
                      label="Diviseurs", order_index=1, chapter_code="A1",
                      chapter_name="Nombres entiers", domain_code="A",
                      domain_name="Nombres")
    db.add_all([fw, comp])
    db.commit()

    source = {"number": "46", "text": "Calculer le PGCD de 1925 et 4125.",
              "source_page": 33, "competency_title": "Diviseurs",
              "has_figure": False, "figure_description": "",
              "exercise_bbox": None, "figure_bbox": None, "vision_extracted": True}
    monkeypatch.setattr(indigo_vision, "extract_pages", lambda *a, **k: [source])
    monkeypatch.setattr(indigo, "_prepare_vision_exercise",
                        lambda *a, **k: (IndigoExercise(
                            id="ex-46", competency_id=comp.id, grade_level="3e",
                            source_number="46", badge_type="exercice"),
                            {"number": "46", "statement": source["text"],
                             "correction": "", "has_figure": False,
                             "competency_title": source["competency_title"],
                             "vision_extracted": True}))
    seen = {}

    def fake_run_family_pair(db_, comps_, grade_, manuals, *a, **k):
        seen["manual"] = manuals[0]
        return [indigo_multipass.Family(number="46",
                                        state=indigo_multipass.REJECTED_GENERATION,
                                        attempts=1, competency_id="c1", competency_code="A1.1")]

    monkeypatch.setattr(indigo_multipass, "run_family_pair", fake_run_family_pair)
    monkeypatch.setattr(indigo.indigo_offpeak, "wait_until_open", lambda *a, **k: None)

    indigo._run_multipass_vision(db, None, "3e", [32], "ext-1", lambda _m: None, [])
    assert "correction" not in seen["manual"]


def test_an_unconfirmed_competency_is_flagged_and_reassignable(db):
    """Le repli par ressemblance rend TOUJOURS une compétence — la première du
    référentiel quand rien ne ressemble à rien. L'exercice partait donc se ranger
    sous une compétence qui n'était pas la sienne, sans que personne le sache :
    pire qu'un rejet, qui se voit. On le garde, on le signale, on le déplace."""
    comps = [_comp("B4.1", "Représenter graphiquement des données"),
             _comp("B4.2", "Calculer une moyenne")]
    source = indigo_multipass.Source(statement="Calcule la moyenne.")
    # ni bandeau lu, ni code rendu par la passe 1 : rien ne confirme le choix
    comp, confirmed = indigo_multipass._resolve_competency(source, {}, comps)
    assert comp is not None and confirmed is False
    # un seul candidat = la cible choisie par l'utilisateur : rien à confirmer
    assert indigo_multipass._resolve_competency(source, {}, comps[:1])[1] is True


def test_each_exercise_takes_the_last_pink_banner_above_it():
    """Le modèle rendait lui-même le titre de chaque exercice, et il s'ancrait
    sur celui qu'on lui passait : sur la page 86, la pastille « Calculer une
    moyenne » était sous ses yeux en haut de la colonne 4, et il recopiait quand
    même la compétence de la colonne précédente. Treize exercices rangés sous la
    mauvaise compétence, sans que rien ne le signale.

    Il ne rend plus qu'une OBSERVATION — les bandeaux et leur hauteur — et le
    rattachement devient un calcul, qui ne peut pas hésiter."""
    items = [{"number_bbox": [10, 100, 60, 140]},      # sous le 1er bandeau
             {"number_bbox": [10, 300, 60, 340]},
             {"number_bbox": [10, 700, 60, 740]}]      # sous le 2nd
    banners = [{"title": "Calculer une moyenne", "y": 60},
               {"title": "Déterminer une médiane", "y": 500}]
    titles, active = indigo_vision._titles_by_banner(items, banners, "Précédente")
    assert titles == ["Calculer une moyenne", "Calculer une moyenne",
                      "Déterminer une médiane"]
    assert active == "Déterminer une médiane"   # report vers la colonne suivante

    # colonne SANS bandeau : cas le plus fréquent, tout hérite du report
    titles, active = indigo_vision._titles_by_banner(items, [], "Précédente")
    assert titles == ["Précédente"] * 3 and active == "Précédente"

    # bandeau au milieu : ce qui est au-dessus garde le report
    titles, _ = indigo_vision._titles_by_banner(
        items, [{"title": "Médiane", "y": 500}], "Précédente")
    assert titles == ["Précédente", "Précédente", "Médiane"]

    # bandeau sans titre ou sans y exploitable : ignoré, jamais de titre vide
    titles, _ = indigo_vision._titles_by_banner(
        items, [{"title": "", "y": 10}, {"title": "Moyenne", "y": "?"}], "Précédente")
    assert titles == ["Moyenne"] * 3


def test_the_pink_banner_is_found_by_colour_not_by_the_model():
    """Le modèle voyait la pastille et répondait quand même la compétence de la
    colonne précédente : treize exercices rangés sous la mauvaise compétence, en
    silence. Une couleur exacte ne se laisse pas influencer par un report.

    Le piège à éviter est l'inverse : les barres d'un histogramme rose (exercice
    35, page 87) sont de la même teinte. Ce qui les sépare d'un bandeau, c'est
    qu'aucune LIGNE n'est rose sur toute la largeur."""
    h, w = 200, 100
    page = np.full((h, w, 3), 255, dtype=np.uint8)
    page[20:45, 5:95] = indigo_vision.BANNER_BGR          # pastille pleine largeur
    page[30:40, 30:70] = 255                              # texte blanc dedans
    # histogramme : barres roses étroites, jamais une ligne pleine
    for x in range(10, 90, 20):
        page[120:180, x:x + 6] = indigo_vision.BANNER_BGR
    found = indigo_vision.find_banners(page)
    assert len(found) == 1, found
    assert 18 <= found[0][0] <= 22 and 43 <= found[0][1] <= 47
    # une image sans rien de rose n'invente aucun bandeau
    assert indigo_vision.find_banners(np.full((50, 50, 3), 255, dtype=np.uint8)) == []


def test_titles_read_by_the_model_are_pinned_to_the_positions_found_by_cv():
    """L'ordre fait la correspondance : positions de la CV, titres du modèle.
    Un titre manquant ne décale rien et n'invente rien."""
    assert indigo_vision._read_banners(
        [{"title": "Calculer une moyenne"}, {"title": "Médiane"}], [60, 500]) == [
        {"y": 60, "title": "Calculer une moyenne"}, {"y": 500, "title": "Médiane"}]
    # le modèle en rend moins que repéré : on garde ce qui est lisible
    assert indigo_vision._read_banners([{"title": "Moyenne"}], [60, 500]) == [
        {"y": 60, "title": "Moyenne"}]
    # ...et jamais un titre vide, qui effacerait le report
    assert indigo_vision._read_banners([{"title": "  "}], [60]) == []

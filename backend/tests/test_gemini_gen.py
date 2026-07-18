"""Tests de la pipeline Gemini (§ création d'exercices ancrée dans le manuel).

Tout tourne en mode mock (aucune clé API, aucun réseau) : le mock de
providers.gemini_json renvoie un lot au contrat app, seedé par compétence ET
par numéro de lot — c'est ce qui permet d'exercer réellement la boucle « autant
d'appels que nécessaire » et le dédoublonnage entre lots.

Le VRAI manuel 5.pdf est en revanche nécessaire : depuis que la création est
ancrée dans les pages du manuel traitant la compétence, `ensure_bank` résout
un vrai chapitre dans la vraie table des matières (seul l'appel OCR lui-même
est mocké). Sans manuel, la pipeline refuse de créer — c'est le comportement
voulu, testé explicitement ci-dessous, mais qui rend le reste du module non
exécutable, d'où le skip global (même contrat que tests/test_sesamaths.py).
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import Competency, CompetencyFramework, GeneratedExercise
from app.services import exercise_gen, gemini_gen, providers, sesamaths

MANUAL_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "manuals" / "5.pdf"

pytestmark = pytest.mark.skipif(not MANUAL_PATH.exists(), reason="manuel 5.pdf absent")


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sesamaths_manuals", {"5e": str(MANUAL_PATH)})
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_domain(db, domain_code="A") -> Competency:
    """Un domaine à 2 chapitres / 3 compétences : la cible + ses voisines,
    dont le prompt a besoin pour cadrer le périmètre."""
    fw = CompetencyFramework(grade_level="5e", name="Test 5e")
    db.add(fw)
    db.flush()
    rows = [
        ("A1.1", "Automatismes", "A1", "Opérations", 0),
        ("A1.2", "Divisions euclidiennes", "A1", "Opérations", 1),
        ("A2.1", "Additionner des relatifs", "A2", "Nombres relatifs", 2),
    ]
    comps = []
    for short_id, label, chap_code, chap_name, order in rows:
        c = Competency(framework_id=fw.id, code=short_id, short_id=short_id, label=label,
                       domain_code=domain_code, domain_name="Nombres et calculs",
                       chapter_code=chap_code, chapter_name=chap_name, order_index=order)
        db.add(c)
        comps.append(c)
    db.commit()
    return comps[0]


def test_ensure_bank_gemini_reaches_target_over_several_batches(db_session):
    # Cœur de la demande : une banque vide doit déclencher AUTANT d'appels que
    # nécessaire (lots de 5) pour atteindre la cible, jamais un nombre figé.
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)

    assert len(rows) >= settings.gemini_bank_target
    assert all(r.source == "gemini" for r in rows)
    assert all(r.difficulty_level == 3 for r in rows)       # difficulté figée
    assert all(r.model == settings.gemini_model for r in rows)
    assert len({r.variant for r in rows}) == len(rows)


def _counted_gemini(monkeypatch) -> list[dict]:
    """Enregistre les payloads envoyés à Gemini, sans changer le comportement
    (le mock répond toujours)."""
    calls: list[dict] = []
    orig = providers.gemini_json

    def counted(db, operation, system, payload, **kw):
        calls.append(payload)
        return orig(db, operation, system, payload, **kw)

    monkeypatch.setattr(providers, "gemini_json", counted)
    return calls


def test_ensure_bank_gemini_calls_llm_until_target_then_stops(db_session, monkeypatch):
    calls = _counted_gemini(monkeypatch)
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    # cible 30 / lots de 5 = 6 appels classiques (et PAS un de plus), PUIS un
    # unique appel dédié aux petites cartes de remplissage
    classic = calls[:6]
    assert [c["batch"] for c in classic] == [0, 1, 2, 3, 4, 5]
    assert all(c["count"] == settings.gemini_batch_size for c in classic)
    # chaque lot connaît les énoncés de tous les précédents (anti-doublon côté
    # prompt ; le dédoublonnage déterministe reste le filet, cf. _dedup_key)
    assert classic[0]["already_created"] == []
    assert len(classic[1]["already_created"]) == 5
    assert len(classic[5]["already_created"]) == 25
    # le 7e et dernier appel est le lot de remplissage : un index de lot distinct
    # (jamais 0..5) et il connaît déjà les 30 exercices classiques
    assert len(calls) == 7
    assert calls[6]["batch"] not in range(6)
    assert len(calls[6]["already_created"]) == 30


def test_ensure_bank_gemini_full_bank_creates_nothing_and_reads_no_manual(
        db_session, monkeypatch):
    # « si plus de 30 exos > ne rien faire, prendre ces exos pour les prochains
    # sujets » : banque pleine = aucun appel Gemini ET aucune lecture du manuel
    # (les 30 exercices resservent tels quels, gratuitement).
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    calls = _counted_gemini(monkeypatch)
    monkeypatch.setattr(sesamaths, "ensure_series_ocr",
                        lambda *a, **kw: pytest.fail("manuel relu alors que la "
                                                     "banque est pleine"))
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert calls == []
    assert len(rows) >= settings.gemini_bank_target


def test_ensure_bank_gemini_partial_bank_creates_only_the_remainder(db_session, monkeypatch):
    # « si banque contient moins de 30 exercices > faire les exos restants » :
    # 25 déjà en stock -> un seul lot de 5, pas 30 de plus.
    comp = _seed_domain(db_session)
    monkeypatch.setattr(settings, "gemini_bank_target", 25)
    gemini_gen.ensure_bank(db_session, comp, level=3)
    assert len(gemini_gen._bank_rows(db_session, comp, 3)) == 25

    monkeypatch.setattr(settings, "gemini_bank_target", 30)
    calls = _counted_gemini(monkeypatch)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert len(calls) == 1
    assert len(rows) == 30
    # le lot complémentaire connaît les 25 déjà en banque
    assert len(calls[0]["already_created"]) == 25


def test_ensure_bank_gemini_no_duplicates_in_bank(db_session):
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    keys = [exercise_gen._dedup_key(r.statement, r.expected_json,
                                    (r.grading_json or {}).get("choices"))
            for r in rows]
    assert len(set(keys)) == len(keys)


def test_ensure_bank_also_creates_short_filler_cards(db_session):
    # 2e appel dédié : des petites cartes de remplissage (kind=filler), en PLUS
    # des 30 classiques. Bornées aux petits formats et EXCLUES de la sélection
    # normale (elles ne servent qu'à combler les bas de page).
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    filler = gemini_gen.filler_rows(db_session, comp, 3)
    assert filler                                             # au moins une carte courte
    assert all(f.kind == gemini_gen.FILLER_KIND for f in filler)
    assert all(f.response_type in gemini_gen.FILLER_RESPONSE_TYPES for f in filler)
    # jamais mélangées aux exercices classiques
    classic = gemini_gen._bank_rows(db_session, comp, 3)
    assert all(c.kind != gemini_gen.FILLER_KIND for c in classic)
    assert not (set(f.id for f in filler) & set(c.id for c in classic))


def test_ensure_bank_generates_filler_only_once(db_session, monkeypatch):
    # Le remplissage est un bonus, pas un contenu qu'on re-paie sujet après
    # sujet : une fois qu'il existe des cartes courtes, aucun nouvel appel.
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)
    before = len(gemini_gen.filler_rows(db_session, comp, 3))
    assert before

    calls = _counted_gemini(monkeypatch)
    gemini_gen.ensure_bank(db_session, comp, level=3)         # banque pleine
    assert calls == []                                       # ni classique ni filler
    assert len(gemini_gen.filler_rows(db_session, comp, 3)) == before


def test_filler_bank_rows_empty_for_sesamaths_source(db_session):
    # Le remplissage est propre à la CRÉATION Gemini (pool infini) : la source
    # Sésamaths, finie, n'en a pas.
    comp = _seed_domain(db_session)
    assert exercise_gen.filler_bank_rows(db_session, comp, 3, source="sesamaths") == []


def test_ensure_bank_gemini_batch_mix_has_qcm_and_written_and_problem(db_session):
    # Le mélange demandé au modèle (3 QCM / 1 réponse écrite / 1 problème) est
    # ce qui remplit une page proprement : distribution.pick_balanced_exercise
    # ne peut équilibrer une copie que si la banque contient les trois.
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    types_ = {r.response_type for r in rows}
    assert "qcm_single" in types_
    assert "short_text" in types_
    assert "multiline_text" in types_
    assert any(r.kind == "probleme" for r in rows)


def test_gemini_rejects_non_automatable_formats(db_session):
    # matching/manual_drawing sont valides pour l'adaptateur Sésamaths (qui
    # subit le format du manuel) mais interdits ici : on invente l'exercice,
    # donc on peut toujours en choisir un qui se corrige automatiquement.
    comp = _seed_domain(db_session)
    raw = {"kind": "application",
           "statement": "Trace un carré de côté $5$ cm en respectant l'échelle.",
           "correction": "Le carré a 4 côtés égaux de $5$ cm.",
           "response_type": "manual_drawing"}
    assert gemini_gen._reject_reason(raw) is not None
    assert gemini_gen._to_candidate(raw, comp, db_session, set()) is None
    # ... alors que le même exercice passe la validation partagée
    assert exercise_gen._validate_exercise(raw, comp, db_session, set()) is not None


def test_gemini_rejects_exercise_that_needs_a_figure(db_session):
    # Le prompt interdit les figures (pas de géométrie pour l'instant) : si le
    # modèle en renvoie une quand même, on rejette l'exercice — le retirer en
    # silence casserait un énoncé qui s'y réfère.
    comp = _seed_domain(db_session)
    raw = {"kind": "application",
           "statement": "Lis la longueur du segment sur la figure ci-contre.",
           "correction": "Le segment mesure $7$ cm.",
           "response_type": "short_text",
           "answer": {"type": "integer", "value": 7},
           "figure": {"type": "number_line", "params": {"min": 0, "max": 10, "points": []}}}
    assert gemini_gen._reject_reason(raw) is not None
    assert gemini_gen._to_candidate(raw, comp, db_session, set()) is None


def test_gemini_geometry_refused_with_clear_message(db_session):
    comp = _seed_domain(db_session, domain_code="EG")
    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "géométrie" in str(exc.value).lower()
    assert db_session.query(GeneratedExercise).count() == 0


def test_gemini_refuses_to_create_without_the_manual_context(db_session, monkeypatch):
    # Sans les pages du manuel, le modèle n'a que le libellé de la compétence :
    # c'est EXACTEMENT ce qui produisait des exercices hors programme et au
    # mauvais niveau. Pas de contexte = pas de création, message actionnable —
    # jamais un repli silencieux sur l'invention libre.
    monkeypatch.setattr(settings, "sesamaths_manuals", {})
    calls = _counted_gemini(monkeypatch)
    comp = _seed_domain(db_session)

    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "manuel" in str(exc.value).lower()
    assert calls == []                                   # rien n'a été payé
    assert db_session.query(GeneratedExercise).count() == 0


def test_gemini_refuses_to_create_when_ocr_returns_no_usable_text(db_session, monkeypatch):
    # Un manuel lisible mais dont l'OCR ne rend que du bruit de mise en page
    # (en-têtes/pieds) ne cale ni le programme ni le niveau : c'est un « pas de
    # contexte » déguisé, à refuser aussi.
    monkeypatch.setattr(providers, "mistral_ocr", lambda *a, **kw: {"pages": [
        {"index": 0, "dimensions": {"width": 600, "height": 800},
         "blocks": [{"type": "footer", "content": "42",
                     "top_left_x": 0, "top_left_y": 0,
                     "bottom_right_x": 10, "bottom_right_y": 10}]}]})
    calls = _counted_gemini(monkeypatch)
    comp = _seed_domain(db_session)

    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "aucun texte" in str(exc.value).lower()
    assert calls == []


def test_gemini_grounds_every_batch_in_the_manual_ocr_text(db_session, monkeypatch):
    # Le contexte manuel doit arriver dans le prompt SYSTÈME de CHAQUE lot :
    # un seul lot non ancré recrée des exercices hors programme.
    systems: list[str] = []
    orig = providers.gemini_json

    def capture(db, operation, system, payload, **kw):
        systems.append(system)
        return orig(db, operation, system, payload, **kw)

    monkeypatch.setattr(providers, "gemini_json", capture)
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    ocr = gemini_gen._manual_context(sesamaths.ensure_series_ocr(db_session, comp))
    # 6 lots classiques + 1 lot de remplissage : le contexte manuel ancre CHACUN
    assert ocr and len(systems) == 7
    assert all(ocr in s for s in systems)


def test_gemini_reads_the_manual_via_ocr_only_never_the_paid_adapter(db_session, monkeypatch):
    # L'adaptateur Sésamaths (Claude Sonnet) transforme les blocs OCR en
    # exercices au contrat app : Gemini n'en a aucun besoin (il veut le TEXTE
    # du manuel) et le payer serait absurde. L'OCR, lui, est mis en cache et
    # partagé avec la pipeline Sésamaths : une seule facture par Série.
    ocr_calls: list[int] = []
    orig_ocr = providers.mistral_ocr

    def counted_ocr(db, operation, pdf_bytes, n_pages, **kw):
        ocr_calls.append(1)
        return orig_ocr(db, operation, pdf_bytes, n_pages, **kw)

    monkeypatch.setattr(providers, "mistral_ocr", counted_ocr)
    monkeypatch.setattr(providers, "claude_json",
                        lambda *a, **kw: pytest.fail("adaptateur Claude appelé "
                                                     "alors que Gemini ne veut que l'OCR"))
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)
    assert len(ocr_calls) == 1          # une Série = un appel OCR, pas un par lot

    # ... et la Série reste prête à être adaptée gratuitement par la pipeline
    # Sésamaths le jour où elle passe dessus (extraction déjà en cache)
    ocr_calls.clear()
    gemini_gen._manual_context(sesamaths.ensure_series_ocr(db_session, comp))
    assert ocr_calls == []


def _counted_ocr(monkeypatch) -> list[int]:
    calls: list[int] = []
    orig = providers.mistral_ocr

    def counted(db, operation, pdf_bytes, n_pages, **kw):
        calls.append(1)
        return orig(db, operation, pdf_bytes, n_pages, **kw)

    monkeypatch.setattr(providers, "mistral_ocr", counted)
    return calls


def test_manual_ocr_paid_by_gemini_is_reused_by_the_sesamaths_pipeline(db_session, monkeypatch):
    # Les deux pipelines lisent les MÊMES pages : l'OCR payé par l'une doit
    # servir à l'autre. Gemini extrait (phase 1) et laisse la Série prête à
    # adapter ; Sésamaths passe ensuite dessus sans repayer l'OCR.
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)

    ocr_calls = _counted_ocr(monkeypatch)
    rows = sesamaths.ensure_bank(db_session, comp, level=3)
    assert ocr_calls == []                       # OCR déjà payé par Gemini
    assert rows and all(r.source in sesamaths.SOURCE_POOL for r in rows)


def test_extract_version_bump_still_reextracts_a_series_left_extracted_by_gemini(
        db_session, monkeypatch):
    # Garantie documentée de la pipeline Sésamaths : bumper
    # EXTRACT_PROMPT_VERSION force une ré-extraction. Gemini laisse
    # couramment des Séries en "extracted" (il extrait sans jamais adapter) —
    # sans contrôle de version sur cet état, elles seraient adaptées depuis un
    # raw_json périmé PUIS estampillées de la version courante.
    comp = _seed_domain(db_session)
    gemini_gen.ensure_bank(db_session, comp, level=3)     # Série -> "extracted"

    ocr_calls = _counted_ocr(monkeypatch)
    monkeypatch.setattr(sesamaths, "EXTRACT_PROMPT_VERSION", "sesamaths-extract-TEST")
    sesamaths.ensure_bank(db_session, comp, level=3)
    assert len(ocr_calls) == 1                   # ré-extraction bien déclenchée


def test_gemini_other_levels_generate_nothing(db_session, monkeypatch):
    # La difficulté est figée à 3 : un appel niveau 5 ne doit RIEN créer (sinon
    # on rangerait des exercices moyens sous une étiquette « difficile »).
    calls: list[int] = []
    monkeypatch.setattr(providers, "gemini_json",
                        lambda *a, **kw: calls.append(1) or {"exercises": []})
    comp = _seed_domain(db_session)
    assert gemini_gen.ensure_bank(db_session, comp, level=5) == []
    assert calls == []


def test_bank_rows_near_level_falls_back_to_level3(db_session):
    # Conséquence de la difficulté figée : une demande niveau 5 doit se
    # rabattre proprement sur la banque de niveau 3, sans erreur.
    comp = _seed_domain(db_session)
    rows, level = exercise_gen.bank_rows_near_level(db_session, comp, level=5,
                                                    source="gemini")
    assert level == 3
    assert rows and all(r.source == "gemini" for r in rows)


def test_gemini_pool_never_mixed_with_sesamaths(db_session):
    comp = _seed_domain(db_session)
    db_session.add(GeneratedExercise(
        competency_id=comp.id, difficulty_level=3, variant=0,
        statement="Exercice tiré du manuel.", correction="$1 + 1 = 2$",
        response_type="short_text", expected_json={"type": "integer", "value": 2},
        grading_json={"max_score": 1, "comparator": "numeric"}, source="sesamaths"))
    db_session.commit()

    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert all(r.source == "gemini" for r in rows)
    assert len(rows) >= settings.gemini_bank_target   # la ligne Sésamaths ne compte pas


def test_retired_gemini_exercise_never_recreated(db_session):
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    victim = rows[0]
    victim_statement = victim.statement
    victim.status = "retired"
    db_session.commit()

    gemini_gen.ensure_bank(db_session, comp, level=3)
    active = {r.statement for r in db_session.query(GeneratedExercise)
              .filter_by(competency_id=comp.id, status="active").all()}
    assert victim_statement not in active


def test_gemini_batch_failure_keeps_earlier_batches(db_session, monkeypatch):
    # Un lot en échec (réseau, quota) ne doit pas jeter les exercices déjà
    # produits : on garde une banque partielle plutôt que rien.
    orig = providers.gemini_json
    calls: list[int] = []

    def flaky(db, operation, system, payload, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("panne simulée")
        return orig(db, operation, system, payload, **kw)

    monkeypatch.setattr(providers, "gemini_json", flaky)
    comp = _seed_domain(db_session)
    rows = gemini_gen.ensure_bank(db_session, comp, level=3)
    assert len(rows) == 5          # le 1er lot seulement
    assert all(r.source == "gemini" for r in rows)


def test_gemini_total_failure_raises_clear_error(db_session, monkeypatch):
    monkeypatch.setattr(providers, "gemini_json",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("panne totale")))
    comp = _seed_domain(db_session)
    with pytest.raises(gemini_gen.GeminiGenerationError) as exc:
        gemini_gen.ensure_bank(db_session, comp, level=3)
    assert "panne totale" in str(exc.value)


def test_system_prompt_frames_target_competency_among_its_domain(db_session):
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5, "(title) 1 Calcule.")
    # la cible est marquée, les voisines présentes (périmètre), et la consigne
    # de ne traiter QUE la cible est explicite
    tree = gemini_gen._competency_tree(db_session, comp)
    assert tree.count("⇦ CIBLE") == 1        # une seule cible, jamais ses voisines
    assert "A1.1 Automatismes  ⇦ CIBLE" in prompt
    assert "A1.2 Divisions euclidiennes" in prompt
    assert "A2 Nombres relatifs" in prompt
    # l'enjeu plateforme (OCR/CV) et le contrat de format partagé sont dedans
    assert "OCR" in prompt and "vision par ordinateur" in prompt
    assert '"exercises"' in prompt
    # aucun placeholder non substitué
    assert "§" not in prompt


def test_system_prompt_forbids_uncorrectable_formats(db_session):
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5, "(title) 1 Calcule.")
    assert "INTERDITS" in prompt
    assert "matching" in prompt and "manual_drawing" in prompt


def test_system_prompt_carries_the_manual_ocr_text_and_how_to_use_it(db_session):
    # La raison d'être de la v2 : sans les pages du manuel, le modèle n'avait
    # que le libellé de la compétence et créait hors programme / au mauvais
    # niveau. Le texte OCR doit arriver TEL QUEL dans le prompt, avec le droit
    # de reprendre ET d'inventer en s'en inspirant.
    comp = _seed_domain(db_session)
    ocr = "(title) 12 Calcule chacun des produits\n(text) a. $7 \\times 8$"
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5, ocr)
    assert ocr in prompt
    for expected in ("PROGRAMME", "NIVEAU", "OBJECTIF D'APPRENTISSAGE",
                     "REPRENDRE", "INVENTER"):
        assert expected in prompt


def test_manual_context_drops_layout_noise_and_tags_pages():
    # L'OCR décrit la mise en page : en-têtes/pieds de page ne disent rien du
    # programme, et une "image" a un contenu vide (la figure vit dans son bbox)
    # — la citer annoncerait au modèle un exercice qu'il ne peut pas voir.
    blocks = [
        {"i": 0, "page": 12, "type": "header", "content": "Chapitre A1"},
        {"i": 1, "page": 12, "type": "title", "content": "12 Calcule."},
        {"i": 2, "page": 12, "type": "image", "content": ""},
        {"i": 3, "page": 13, "type": "text", "content": "a. $7 \\times 8$"},
    ]
    out = gemini_gen._manual_context(blocks)
    assert "Chapitre A1" not in out
    assert "(image)" not in out
    assert "(title) 12 Calcule." in out
    assert "(text) a. $7 \\times 8$" in out
    assert "--- page 12 du manuel ---" in out and "--- page 13 du manuel ---" in out


def test_manual_ocr_text_cannot_forge_a_prompt_placeholder(db_session):
    # Le texte du manuel est le SEUL fragment non maîtrisé du prompt (OCR d'un
    # PDF imprimé). Substitué en DERNIER, un marqueur §...§ qu'il contiendrait
    # reste littéral au lieu d'être interprété et de réécrire le prompt.
    comp = _seed_domain(db_session)
    prompt = gemini_gen._system_prompt(db_session, comp, "5e", 5,
                                       "(text) §COMPETENCY_TREE§ §GRADE§")
    assert "(text) §COMPETENCY_TREE§ §GRADE§" in prompt
    # l'arbre des compétences n'a PAS été réinjecté à la place du marqueur
    assert prompt.count("A1.2 Divisions euclidiennes") == 1


def test_generate_subject_end_to_end_fills_one_page_without_duplicates(db_session):
    """Le sujet complet, tel que l'élève le reçoit : la banque Gemini doit
    remplir la page cible sans la déborder et sans jamais servir deux fois le
    même exercice — c'est l'objectif de la suppression du plafond de 3."""
    from app.models import Assessment, Copy, CopyItem, SchoolClass, Student
    from app.services import generation

    comp = _seed_domain(db_session)
    comps = db_session.query(Competency).filter(Competency.chapter_code == "A1").all()
    cls = SchoolClass(name="5eB", grade_level="5e")
    db_session.add(cls)
    db_session.flush()
    for i in range(3):
        db_session.add(Student(class_id=cls.id, first_name=f"Eleve{i}", last_name="Test",
                               llm_pseudonym=f"E{i}", active=True))
    a = Assessment(class_id=cls.id, type="training", title="Sujet Gemini",
                   pages_target=1, personalization_mode="common")
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "gemini"}
    db_session.add(a)
    db_session.commit()

    report = generation.generate_assessment_job(db_session, a, job=None, font_size=9)
    db_session.commit()

    assert report["warnings"] == []          # aucun débordement de page
    copies = db_session.query(Copy).all()
    assert len(copies) == 3
    for copy in copies:
        items = db_session.query(CopyItem).filter_by(copy_id=copy.id).all()
        assert copy.total_pages == 1         # la cible est tenue
        assert len(items) > 3                # la page est vraiment remplie
        statements = [i.statement for i in items]
        assert len(set(statements)) == len(statements)   # aucun doublon


def test_generated_copies_split_correction_load_between_cv_and_mathpix(db_session):
    """~50 % QCM (corrigés par vision par ordinateur : gratuit) / ~50 % cases
    manuscrites (OCR Mathpix : payant, sous quota). Ce n'est pas un détail
    pédagogique mais la répartition de la charge de correction — un mix qui
    dériverait ferait exploser la facture Mathpix sans que rien ne le signale."""
    from app.models import Assessment, Copy, CopyItem, SchoolClass, Student
    from app.services import generation

    _seed_domain(db_session)
    comps = db_session.query(Competency).filter(Competency.chapter_code == "A1").all()
    cls = SchoolClass(name="5eB", grade_level="5e")
    db_session.add(cls)
    db_session.flush()
    for i in range(4):
        db_session.add(Student(class_id=cls.id, first_name=f"E{i}", last_name="T",
                               llm_pseudonym=f"E{i}", active=True))
    a = Assessment(class_id=cls.id, type="training", title="Mix", pages_target=2,
                   personalization_mode="common")
    a.blueprint_json = {"competency_ids": [c.id for c in comps],
                        "exercise_source": "gemini"}
    db_session.add(a)
    db_session.commit()

    report = generation.generate_assessment_job(db_session, a, job=None, font_size=9)
    db_session.commit()

    copy_ids = [c.id for c in db_session.query(Copy).filter_by(assessment_id=a.id).all()]
    items = db_session.query(CopyItem).filter(CopyItem.copy_id.in_(copy_ids)).all()
    qcm = sum(1 for i in items if i.response_type.startswith("qcm"))
    assert len(items) >= 8
    assert 0.4 <= qcm / len(items) <= 0.6      # « environ » : la banque ne permet
    # pas toujours la cible exacte, mais jamais les 10 % de QCM d'avant
    assert report["estimated_mathpix_calls"] == len(items) - qcm


def test_content_generate_endpoint_accepts_gemini_source(db_session):
    from app.routers import content as content_router

    comp = _seed_domain(db_session)
    body = content_router.GenerateExercisesIn(competency_id=comp.id, level=3,
                                              source="gemini")
    result = content_router.generate_exercises(body, db=db_session)
    assert result["count"] >= settings.gemini_bank_target
    assert all(e["source"] == "gemini" for e in result["exercises"])

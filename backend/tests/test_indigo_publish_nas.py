"""Publication depuis le NAS : ce qui est publié ne doit JAMAIS disparaître à
la mise à jour du conteneur.

Le piège que ces tests verrouillent : `publish` écrivait dans le dossier du
package `app`, c'est-à-dire DANS L'IMAGE. Le conteneur étant recréé à chaque
`docker compose pull && up -d`, la publication du professeur partait avec lui —
et `seed_published`, appelé au démarrage, remettait la banque à l'état du dépôt
sans le moindre message.
"""
import json
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import (Competency, CompetencyFramework, GeneratedExercise,
                        IndigoExercise)
from app.services import indigo


@pytest.fixture
def db(tmp_path, monkeypatch):
    # /data du conteneur : le volume, seul endroit qui survit à une mise à jour
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    # le dossier « livré dans l'image » : recréé à neuf à chaque déploiement
    image_dir = tmp_path / "image" / "indigo"
    image_dir.mkdir(parents=True)
    monkeypatch.setattr(indigo, "_IMAGE_PUB_DIR", image_dir)
    # StaticPool + check_same_thread : TestClient sert la requête dans un autre
    # thread que celui qui a semé la base (cf. tests/test_users_admin.py)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    fw = CompetencyFramework(grade_level="3e", name="T")
    s.add(fw)
    s.flush()
    s.add(Competency(framework_id=fw.id, code="A1.1", short_id="A1.1",
                     label="Fractions", domain_code="A", domain_name="Nombres",
                     chapter_code="A1", chapter_name="Opérations"))
    s.commit()
    yield s
    s.close()


def _validated(db, n=2):
    comp = db.query(Competency).first()
    for i in range(n):
        db.add(IndigoExercise(
            competency_id=comp.id, grade_level="3e", status="validated",
            source_number=str(i), badge_type="exercice", difficulty=2,
            statement=f"Calcule la somme des {i + 3} premiers entiers.",
            response_type="short_text", expected_json={"value": i},
            grading_json={"bareme_points": 1.0}, correction_guide="g"))
    db.commit()


def _seed_image(image_dir: Path, ids=("livre-1",)):
    """Contenu publié livré dans l'image (celui du dépôt)."""
    (image_dir / "crops").mkdir(exist_ok=True)
    (image_dir / "figures").mkdir(exist_ok=True)
    (image_dir / "exercises.json").write_text(json.dumps({
        "version": "2", "grade_level": "3e", "generated_at": "2026-01-01",
        "exercises": [{"id": i, "competency_code": "A1.1", "grade_level": "3e",
                       "statement": "Exercice livré", "response_type": "short_text",
                       "expected": {}, "grading": {}, "difficulty": 2,
                       "correction_guide": "", "crop_file": "", "figure_file": ""}
                      for i in ids]}), encoding="utf-8")


# ------------------------------------------------ la publication doit survivre

def test_publication_updates_tracks_content_not_review_or_validation_dates(db):
    _validated(db, 1)
    row = db.query(IndigoExercise).one()
    assert indigo.publication_updates(db, [row]) == {row.id: False}
    indigo.publish(db)
    assert not indigo.publication_updates(db, [row])[row.id]
    indigo.set_review_verdict(db, row, 'like', 'Relu')
    indigo.validate_exercise(db, row, None)
    assert not indigo.publication_updates(db, [row])[row.id]
    indigo.update_exercise(db, row, {'grading_json': {
        **row.grading_json, 'feedback': {'fallback': {'text': 'Nouvelle correction'}}}})
    assert indigo.publication_updates(db, [row])[row.id]
    from app.routers.indigo import summary
    assert summary('3e', db)[0]['needs_update'] == 1
    indigo.publish_competency(db, row.competency_id, None)
    assert not indigo.publication_updates(db, [row])[row.id]
    assert summary('3e', db)[0]['needs_update'] == 0


def test_publication_updates_detects_figure_changes_at_same_path(db):
    from PIL import Image
    _validated(db, 1)
    row = db.query(IndigoExercise).one()
    row.has_figure = True
    row.figure_path = 'indigo/drafts/figure.png'
    image = indigo.crop_abs_path(row.figure_path)
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (20, 20), 'white').save(image)
    db.commit()
    indigo.publish(db)
    assert not indigo.publication_updates(db, [row])[row.id]
    Image.new('RGB', (20, 20), 'black').save(image)
    assert indigo.publication_updates(db, [row])[row.id]
    # Lire le compteur ne recopie jamais la nouvelle image dans la publication.
    assert indigo.publication_updates(db, [row])[row.id]
    indigo.publish_rows(db, [row])
    assert not indigo.publication_updates(db, [row])[row.id]


def test_publish_displayed_selection_is_incremental_and_includes_problems(db):
    from types import SimpleNamespace
    from app.routers.indigo import PublishSelection, preview_publication_selection, publish_selection
    _validated(db, 3)
    comp = db.query(Competency).one()
    rows = db.query(IndigoExercise).order_by(IndigoExercise.source_number).all()
    other = Competency(framework_id=comp.framework_id, code='A1.2', short_id='A1.2', label='Autre')
    db.add(other); db.flush()
    outside = IndigoExercise(competency_id=other.id, grade_level='3e', status='validated',
                             statement='Hors sélection', response_type='short_text')
    db.add(outside); db.commit()
    indigo.publish(db)
    indigo.update_exercise(db, rows[0], {'statement': 'Version corrigée'})
    indigo.update_exercise(db, rows[1], {'statement': 'À reprendre'})
    indigo.set_review_verdict(db, rows[1], 'dislike')
    indigo.update_exercise(db, outside, {'statement': 'Autre modification hors sélection'})
    problem = IndigoExercise(competency_id=comp.id, grade_level='3e', status='draft',
                             statement='Problème nouveau', badge_type='probleme', response_type='short_text')
    db.add(problem); db.commit()
    body = PublishSelection(grade_level='3e', program_id='fr', subject_id='math', competency_ids=[comp.id])
    plan = preview_publication_selection(body, db)
    assert plan['pending'] == 2 and plan['skipped'] == 1
    body.fingerprint = plan['fingerprint']
    result = publish_selection(body, db, SimpleNamespace(id=None))
    assert result == {'published': 2, 'skipped': 1}
    records = {r['id']: r for r in indigo.load_published()['exercises']}
    assert records[rows[0].id]['statement'] == rows[0].statement
    assert records[outside.id]['statement'] == 'Hors sélection'
    assert rows[1].status == outside.status == 'draft'
    assert problem.id in records and rows[2].id in records
    assert not preview_publication_selection(body, db)['pending']


def test_publish_displayed_selection_rejects_another_cycle_or_country(db):
    from fastapi import HTTPException
    from app.routers.indigo import PublishSelection, preview_publication_selection
    _validated(db, 1)
    comp = db.query(Competency).one()
    for country, cycle in [('ci', '3e'), ('fr', '6e')]:
        body = PublishSelection(grade_level=cycle, program_id=country, subject_id='math', competency_ids=[comp.id])
        with pytest.raises(HTTPException) as error:
            preview_publication_selection(body, db)
        assert error.value.status_code == 400
    assert not indigo.load_published().get('exercises')


def test_publish_displayed_selection_rejects_changed_review_since_preview(db):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routers.indigo import PublishSelection, preview_publication_selection, publish_selection
    _validated(db, 1)
    row = db.query(IndigoExercise).one()
    body = PublishSelection(grade_level='3e', program_id='fr', subject_id='math', competency_ids=[row.competency_id])
    body.fingerprint = preview_publication_selection(body, db)['fingerprint']
    indigo.set_review_verdict(db, row, 'dislike', 'Nouvelle erreur')
    with pytest.raises(HTTPException) as error:
        publish_selection(body, db, SimpleNamespace(id=None))
    assert error.value.status_code == 409
    assert not indigo.load_published().get('exercises')


def test_publish_writes_to_the_volume_not_the_image(db, tmp_path):
    _validated(db)
    indigo.publish(db)

    volume_json = tmp_path / "data" / "indigo" / "published" / "exercises.json"
    assert volume_json.exists(), "la publication doit atterrir sur le volume"
    assert not (indigo._IMAGE_PUB_DIR / "exercises.json").exists(), \
        "rien ne doit être écrit dans l'image : le conteneur est jetable"


def test_a_container_update_does_not_lose_the_publication(db, tmp_path):
    """La mise à jour du NAS : l'image est remplacée, le volume reste."""
    _validated(db, n=3)
    indigo.publish(db)
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 3

    # `docker compose pull && up -d` : conteneur neuf, dossier d'image tout neuf
    import shutil
    shutil.rmtree(indigo._IMAGE_PUB_DIR)
    indigo._IMAGE_PUB_DIR.mkdir(parents=True)
    db.query(GeneratedExercise).filter_by(source="indigo").delete()
    db.commit()

    assert indigo.seed_published(db) == 3      # redémarrage : tout revient
    assert db.query(GeneratedExercise).filter_by(source="indigo").count() == 3


def test_a_deployment_that_never_published_reads_the_image(db):
    """Tous les autres utilisateurs : pas de volume, ils lisent le contenu livré."""
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1", "livre-2"))
    status = indigo.published_status()
    assert status["source"] == "image" and status["count"] == 2
    assert indigo.seed_published(db) == 2


def test_the_volume_takes_over_once_the_instance_has_published(db):
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1",))
    _validated(db, n=2)
    indigo.publish(db)
    status = indigo.published_status()
    assert status["source"] == "volume" and status["count"] == 2


# ------------------------------------------------------------- les garde-fous

def test_publishing_nothing_never_wipes_what_is_already_published(db):
    """Base de brouillons vide (instance neuve, purge, restauration partielle) :
    publier effacerait tout. On refuse, avec la raison."""
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1", "livre-2"))
    with pytest.raises(indigo.PublishRefused) as err:
        indigo.publish(db)
    assert "effacerait" in str(err.value)
    assert indigo.published_status()["count"] == 2      # rien n'a bougé


def test_an_explicit_reset_is_still_possible(db):
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1",))
    assert indigo.publish(db, force=True)["published"] == 0


def test_removing_an_exercise_survives_the_next_restart(db):
    """Un exercice retiré ne doit pas ressusciter au redémarrage — l'écriture
    partielle doit donc, elle aussi, atterrir sur le volume."""
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("garde", "retire"))
    assert indigo._unpublish("retire") is True
    assert {e["id"] for e in indigo.load_published()["exercises"]} == {"garde"}
    assert indigo.published_status()["source"] == "volume"
    # l'image, elle, n'a pas été touchée
    image = json.loads((indigo._IMAGE_PUB_DIR / "exercises.json").read_text())
    assert len(image["exercises"]) == 2


# ------------------------------------------------------------------- l'export

def test_export_bundle_mirrors_the_repo_layout(db):
    _validated(db, n=2)
    indigo.publish(db)
    blob, filename = indigo.export_bundle()
    assert filename.startswith("indigo-publication-") and filename.endswith(".zip")
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as z:
        names = z.namelist()
        assert "exercises.json" in names
        # l'arborescence doit se décompresser telle quelle sur app/data/indigo/
        assert all(n == "exercises.json" or n.startswith(("crops/", "figures/"))
                   for n in names), names
        data = json.loads(z.read("exercises.json"))
    assert len(data["exercises"]) == 2


def test_export_carries_no_orphan_image(db, tmp_path):
    """Une image d'un exercice retiré n'a rien à faire dans le dépôt."""
    _validated(db, n=1)
    indigo.publish(db)
    orphan = tmp_path / "data" / "indigo" / "published" / "crops" / "orphelin.png"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"\x89PNG\r\n\x1a\n")
    blob, _ = indigo.export_bundle()
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as z:
        assert "crops/orphelin.png" not in z.namelist()


# ------------------------------------------------------------- l'API HTTP

def test_the_export_endpoint_serves_a_usable_zip(db, monkeypatch):
    """Bout en bout : le professeur clique, il obtient une archive prête à
    décompresser dans le dépôt."""
    import io as _io

    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    _validated(db, n=2)
    indigo.publish(db)
    app.dependency_overrides[get_db] = lambda: db
    # require_role("admin") fabrique une nouvelle fonction à chaque appel : la
    # surcharger ne porterait pas. On surcharge current_user, dont elle dépend.
    app.dependency_overrides[current_user] = lambda: User(
        id="u", email="admin@test.fr", role="admin")
    try:
        client = TestClient(app)
        r = client.get("/api/indigo/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert "indigo-publication-" in r.headers["content-disposition"]
        with zipfile.ZipFile(_io.BytesIO(r.content)) as z:
            assert "exercises.json" in z.namelist()
            assert len(json.loads(z.read("exercises.json"))["exercises"]) == 2

        # le statut dit d'où vient le contenu : ici le volume de l'instance
        status = client.get("/api/indigo/published").json()
        assert status["source"] == "volume" and status["published"] == 2
    finally:
        app.dependency_overrides.clear()


def test_publishing_nothing_answers_409_not_a_silent_wipe(db):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1",))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: User(
        id="u", email="admin@test.fr", role="admin")
    try:
        r = TestClient(app).post("/api/indigo/publish")
        assert r.status_code == 409
        assert "effacerait" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def _admin_client(db):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.deps import current_user
    from app.main import app
    from app.models import User

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: User(
        id="u", email="admin@test.fr", role="admin")
    return TestClient(app), app


def test_publish_competency_skips_disliked_cards_and_problems(db):
    _validated(db, 3)
    comp = db.query(Competency).first()
    db.add(IndigoExercise(
        competency_id=comp.id, grade_level="3e", status="draft", source_number="pb",
        badge_type="probleme", difficulty=2, statement="Un problème du chapitre entier.",
        response_type="short_text", expected_json={"value": 1}, grading_json={"bareme_points": 1.0}))
    rows = db.query(IndigoExercise).filter_by(badge_type="exercice").order_by(IndigoExercise.source_number).all()
    rows[0].status = rows[1].status = "draft"
    db.commit()
    client, app = _admin_client(db)
    try:
        assert client.put(f"/api/indigo/exercises/{rows[1].id}/review-verdict",
                          json={"value": "dislike"}).status_code == 200
        response = client.post(f"/api/indigo/competencies/{comp.id}/publish")
        assert response.status_code == 200
        assert response.json() == {"published": 2, "skipped": 1}
        assert client.get(f"/api/indigo/exercises/{rows[1].id}").json()["review_verdict"] == "dislike"
    finally:
        app.dependency_overrides.clear()
    db.expire_all()
    assert rows[0].status == "validated" and rows[1].status == "draft"
    published = {e["id"] for e in indigo.load_published()["exercises"]}
    assert published == {rows[0].id, rows[2].id}
    assert db.get(GeneratedExercise, rows[0].id) is not None
    assert db.get(GeneratedExercise, rows[1].id) is None


def test_review_verdict_can_be_cleared_without_touching_the_rest(db):
    _validated(db, 1)
    row = db.query(IndigoExercise).one()
    row.raw_ocr_json = {"review_notes": ["À vérifier"]}
    db.commit()
    stamp = row.updated_at
    indigo.set_review_verdict(db, row, "like")
    assert indigo.exercise_out(db, row)["review_verdict"] == "like"
    indigo.set_review_verdict(db, row, None)
    assert indigo.exercise_out(db, row)["review_verdict"] is None
    assert row.raw_ocr_json["review_notes"] == ["À vérifier"]
    assert [e["status"] for e in row.raw_ocr_json["review_history"]] == ["accepted", "unrated"]
    assert row.updated_at == stamp


def test_review_comment_survives_reload_and_verdict_changes(db):
    _validated(db, 1)
    row = db.query(IndigoExercise).one()
    goldfish = {"flag": True, "notes": ["Consigne ambiguë"], "severity": "corriger"}
    row.raw_ocr_json = {"quality": {"goldfish": goldfish}}
    db.commit()
    client, app = _admin_client(db)
    url = f"/api/indigo/exercises/{row.id}/review-verdict"
    try:
        response = client.put(url, json={"value": "dislike", "comment": "  Préciser l’unité.  "})
        assert response.status_code == 200
        assert response.json()["review_comment"] == "Préciser l’unité."
        db.expire_all()
        card = client.get(f"/api/indigo/exercises/{row.id}").json()
        assert card["review_comment"] == "Préciser l’unité."
        assert card["review_notes"] == goldfish["notes"]
        assert client.put(url, json={"value": "like"}).status_code == 200
        db.refresh(row)
        assert row.raw_ocr_json["quality"]["goldfish"] == goldfish
        assert row.raw_ocr_json["review_comment"] == "Préciser l’unité."
        assert not indigo.needs_review(row)
        assert client.put(url, json={"value": "dislike", "comment": ""}).json()["review_comment"] == ""
        assert client.put(url, json={"value": "dislike", "comment": "x" * 5001}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_publish_competency_needs_a_known_competency(db):
    client, app = _admin_client(db)
    try:
        assert client.post("/api/indigo/competencies/absente/publish").status_code == 404
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------- le dépôt, source de vérité livrée

def test_a_newer_image_replaces_a_stale_volume(db):
    """Serveur qui a publié une fois : une image livrée PLUS TARD (dépôt mis à
    jour) doit l'emporter, sinon le contenu du dépôt n'y arriverait jamais."""
    _validated(db, n=2)
    indigo.publish(db)                                   # volume daté d'aujourd'hui
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1",))
    img = indigo._IMAGE_PUB_DIR / "exercises.json"
    data = json.loads(img.read_text())
    data["generated_at"] = "2999-01-01T00:00:00+00:00"
    img.write_text(json.dumps(data))
    assert indigo.published_status()["source"] == "image"
    assert indigo.seed_published(db) == 1


def test_a_partial_write_starts_from_the_newer_image(db):
    _validated(db, n=2)
    indigo.publish(db)
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("garde", "retire"))
    img = indigo._IMAGE_PUB_DIR / "exercises.json"
    data = json.loads(img.read_text())
    data["generated_at"] = "2999-01-01T00:00:00+00:00"
    img.write_text(json.dumps(data))
    assert indigo._unpublish("retire") is True
    assert {e["id"] for e in indigo.load_published()["exercises"]} == {"garde"}


def test_publish_mirrors_to_the_repo_when_enabled(db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "indigo_publish_to_repo", True)
    (indigo._IMAGE_PUB_DIR / "crops").mkdir(parents=True, exist_ok=True)
    (indigo._IMAGE_PUB_DIR / "crops" / "orpheline.png").write_bytes(b"x")
    _validated(db, n=2)
    indigo.publish(db)
    repo = json.loads((indigo._IMAGE_PUB_DIR / "exercises.json").read_text())
    assert len(repo["exercises"]) == 2
    assert not (indigo._IMAGE_PUB_DIR / "crops" / "orpheline.png").exists()
    assert indigo.published_status()["count"] == 2


def test_no_repo_mirror_inside_a_docker_image(db):
    # dossier d'image redirigé (comme dans un conteneur sans .git) : jamais écrit
    _validated(db)
    indigo.publish(db)
    assert not (indigo._IMAGE_PUB_DIR / "exercises.json").exists()


def test_a_volume_dated_later_but_built_on_an_old_image_gives_way(db):
    """Cas réel du NAS : une publication locale PLUS RÉCENTE en date, mais faite
    sur l'ANCIEN contenu livré, ne doit jamais masquer le nouveau dépôt."""
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("ancien",))
    _validated(db, n=2)
    indigo.publish(db)                       # volume construit sur l'image « ancien »
    assert indigo.published_status()["source"] == "volume"
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("depot-1", "depot-2", "depot-3"))
    img = indigo._IMAGE_PUB_DIR / "exercises.json"
    data = json.loads(img.read_text())
    data["generated_at"] = "2000-01-01T00:00:00+00:00"   # daté AVANT le volume
    img.write_text(json.dumps(data))
    assert indigo.published_status()["source"] == "image"
    assert indigo.seed_published(db) == 3


def test_a_legacy_volume_without_base_never_hides_the_repo(db, tmp_path):
    vol = tmp_path / "data" / "indigo" / "published"
    vol.mkdir(parents=True)
    (vol / "exercises.json").write_text(json.dumps(
        {"version": "2", "generated_at": "2999-01-01", "exercises": []}))
    _seed_image(indigo._IMAGE_PUB_DIR, ids=("livre-1", "livre-2"))
    assert indigo.published_status()["source"] == "image"
    assert indigo.seed_published(db) == 2

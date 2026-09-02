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

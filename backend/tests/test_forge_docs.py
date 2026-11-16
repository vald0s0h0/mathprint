"""Page admin « Forge » : lecture seule d'une liste fermée de documents."""
import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers import forge_docs


def test_known_documents_are_served():
    out = forge_docs.read_file("forge/contexte/exercice.md")
    assert out["kind"] == "markdown" and out["content"].startswith("# Le bon exercice")
    assert forge_docs.read_file("forge/CONSIGNE-LECON.md")["name"] == "CONSIGNE-LECON.md"
    assert forge_docs.read_file("forge/contexte/exemples/math.json")["kind"] == "json"
    assert forge_docs.read_file("erreurs/math.json")["kind"] == "json"
    assert forge_docs.read_file("programmes/ci/3e.md")["kind"] == "markdown"


@pytest.mark.parametrize("path", [
    "forge/forge.py", "forge/../../AGENTS.md", "forge/contexte/../../../backend/app/config.py",
    "erreurs/../../config.py", "inconnu/x.md", "forge/", "/etc/passwd", "lecons/../../mathprint.db",
    "forge/run.py",
])
def test_anything_outside_the_list_is_refused(path):
    with pytest.raises(HTTPException) as error:
        forge_docs.read_file(path)
    assert error.value.status_code == 404


def test_lessons_are_listed_with_their_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert forge_docs.lessons() == {"available": False, "lessons": []}
    folder = tmp_path / "forge" / "lecons" / "math"
    folder.mkdir(parents=True)
    (folder / "fr-3e-C2.md").write_text("# C2\n- **F1** : un fait.\n- **F2** : un autre.\n", encoding="utf-8")
    (folder / "fr-3e-A4.md").write_text("# Ancienne fiche\n", encoding="utf-8")
    listed = {l["name"]: l for l in forge_docs.lessons()["lessons"]}
    assert listed["fr-3e-C2"]["facts"] == 2 and listed["fr-3e-A4"]["facts"] == 0
    assert forge_docs.read_file("lecons/math/fr-3e-C2.md")["content"].startswith("# C2")
    with pytest.raises(HTTPException):
        forge_docs.read_file("lecons/math/absente.md")

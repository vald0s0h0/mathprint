"""Chargement des prompts LLM — et surtout : une IMAGE DÉPLOYÉE en a.

Les prompts vivent dans `prompts/` à la RACINE du dépôt, hors du contexte de
build de l'API (`./backend`). Livrés nulle part, ils faisaient échouer toute
fabrication d'exercices en dehors de la machine de développement, sur un
PromptNotFound au premier appel LLM. La CI en dépose donc une copie dans
`app/data/prompts`, et le chargeur la prend en second recours.

Ces tests fixent les deux moitiés du contrat : le repli existe, et le dossier
éditable garde la priorité (on continue d'affiner un prompt à chaud sans
redéployer). Le dernier vérifie qu'aucun prompt appelé par le code ne manque à
l'appel dans le dépôt — c'est ce qui sera copié dans l'image.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import prompts

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_la_copie_embarquee_prend_le_relais_du_dossier_editable(tmp_path, monkeypatch):
    """Sur une instance déployée, `prompts/` n'existe pas : c'est la copie de
    l'image qui doit répondre, sinon aucun exercice ne peut être fabriqué."""
    monkeypatch.setattr(settings, "prompts_dir", tmp_path / "absent")
    bundled = tmp_path / "bundled"
    (bundled / "indigo").mkdir(parents=True)
    (bundled / "indigo" / "qcm.txt").write_text("EMBARQUÉ", encoding="utf-8")
    monkeypatch.setattr(prompts, "_BUNDLED_DIR", bundled)
    monkeypatch.setattr(prompts, "_cache", {})
    assert prompts.load("indigo", "qcm") == "EMBARQUÉ"


def test_le_dossier_editable_garde_la_priorite(tmp_path, monkeypatch):
    """Éditer un prompt à chaud doit continuer de marcher, y compris quand une
    version embarquée existe — sinon la copie de l'image masquerait le réglage
    en cours."""
    editable = tmp_path / "prompts"
    (editable / "indigo").mkdir(parents=True)
    (editable / "indigo" / "qcm.txt").write_text("ÉDITABLE", encoding="utf-8")
    bundled = tmp_path / "bundled"
    (bundled / "indigo").mkdir(parents=True)
    (bundled / "indigo" / "qcm.txt").write_text("EMBARQUÉ", encoding="utf-8")
    monkeypatch.setattr(settings, "prompts_dir", editable)
    monkeypatch.setattr(prompts, "_BUNDLED_DIR", bundled)
    monkeypatch.setattr(prompts, "_cache", {})
    assert prompts.load("indigo", "qcm") == "ÉDITABLE"


def test_un_prompt_absent_partout_nomme_les_deux_emplacements(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "prompts_dir", tmp_path / "a")
    monkeypatch.setattr(prompts, "_BUNDLED_DIR", tmp_path / "b")
    monkeypatch.setattr(prompts, "_cache", {})
    with pytest.raises(prompts.PromptNotFound) as e:
        prompts.load("indigo", "qcm")
    assert str(tmp_path / "a") in str(e.value) and str(tmp_path / "b") in str(e.value)


def test_tous_les_prompts_appeles_par_le_code_existent_dans_le_depot():
    """Filet anti-oubli : ce qui n'est pas dans `prompts/` ne partira pas dans
    l'image, et l'absence ne se verrait qu'en production, au premier appel."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    called = set()
    for py in app_dir.rglob("*.py"):
        for pipeline, name in re.findall(r'prompts\.load\(\s*"([^"]+)"\s*,\s*"([^"]+)"',
                                         py.read_text(encoding="utf-8")):
            called.add((pipeline, name))
    assert called, "aucun appel prompts.load repéré — le test ne vérifie plus rien"
    manquants = [f"{p}/{n}.txt" for p, n in sorted(called)
                 if not (_REPO_ROOT / "prompts" / p / f"{n}.txt").exists()]
    assert not manquants, f"prompts appelés mais absents du dépôt : {manquants}"

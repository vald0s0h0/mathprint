"""Chargement des prompts LLM ÉDITABLES (fichiers texte, hors code).

Les pipelines de création d'exercices — Indigo (API) et cli-exos (abonnement) —
ne codent plus leurs prompts en dur : ils les lisent dans `settings.prompts_dir`
(par défaut `<racine>/prompts`), un fichier par étape, rangés par pipeline :

    prompts/
      indigo/    segmentation.txt  segmentation_corrections.txt
                 generation.txt    verification.txt
      cli-exos/  decoupage.txt     generation.txt  verification.txt

Objectif : pouvoir affiner un prompt (qualité des appels LLM) SANS toucher au code
ni redéployer. Le fichier ne contient que le BLOC D'INSTRUCTIONS (prose) ; le
schéma JSON strict du contrat reste ajouté par le code (exercise_gen.format_contract)
— l'éditer casserait le validateur.

DEUX propriétés importantes :
- DEUX EMPLACEMENTS, dans cet ordre : le dossier ÉDITABLE (`settings.prompts_dir`,
  par défaut `prompts/` à la racine du dépôt) puis la copie EMBARQUÉE dans l'image
  (`app/data/prompts`, remplie au build). Le premier permet d'affiner un prompt à
  chaud en développement ; le second fait qu'une instance déployée — qui n'a pas
  la racine du dépôt — peut fabriquer des exercices. Sans lui, toute génération
  échouait sur PromptNotFound hors de la machine de développement.
- CHARGEMENT PARESSEUX : `load()` n'est appelé qu'au moment où une extraction
  tourne, jamais à l'import des modules. Un prompt manquant lève une erreur
  CLAIRE au moment de l'utiliser, pas un plantage silencieux.
- RELECTURE À CHAUD : le contenu est mis en cache par (chemin, mtime) — éditer un
  fichier prend effet au prochain appel, sans redémarrer l'application.
"""
from __future__ import annotations

from pathlib import Path

from ..config import _APP_DIR, settings

# Copie EMBARQUÉE dans l'image (le Dockerfile la reçoit dans le contexte de
# build, cf. .github/workflows/deploy.yml). Sans elle, une instance déployée ne
# peut pas fabriquer d'exercices : le dossier `prompts/` de la racine du dépôt
# n'est pas dans l'image, et le premier appel LLM échouerait sur PromptNotFound.
# Le dossier ÉDITABLE (settings.prompts_dir) reste prioritaire : sur la machine
# de développement, on continue d'éditer prompts/ à la racine, à chaud.
_BUNDLED_DIR = _APP_DIR / "data" / "prompts"


class PromptNotFound(RuntimeError):
    """Fichier de prompt attendu mais introuvable (message actionnable)."""


# cache { chemin -> (mtime, contenu) } : re-lit seulement si le fichier a changé
_cache: dict[Path, tuple[float, str]] = {}


def prompt_path(pipeline: str, name: str) -> Path:
    """Chemin du prompt : dossier éditable d'abord, copie embarquée ensuite.

    Le chemin rendu quand AUCUN des deux n'existe est celui du dossier éditable
    — c'est lui qu'il faut nommer dans le message d'erreur."""
    rel = Path(pipeline) / f"{name}.txt"
    editable = settings.prompts_dir / rel
    if editable.exists():
        return editable
    bundled = _BUNDLED_DIR / rel
    return bundled if bundled.exists() else editable


def load(pipeline: str, name: str) -> str:
    """Contenu du prompt `prompts/<pipeline>/<name>.txt`. Lève `PromptNotFound`
    avec un message clair si le fichier manque (jamais un prompt vide en silence)."""
    path = prompt_path(pipeline, name)
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        raise PromptNotFound(
            f"Prompt « {pipeline}/{name} » introuvable : {path}\n"
            f"Ni dans le dossier éditable ({settings.prompts_dir}), ni dans la copie "
            f"embarquée ({_BUNDLED_DIR}). En développement, les prompts vivent dans "
            f"`prompts/` à la racine du dépôt ; dans l'image, ils sont copiés au "
            f"build. Une image qui n'en a aucun est mal construite — mets-la à jour, "
            f"ou surcharge MATHPRINT_PROMPTS_DIR.") from e
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text

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
- CHARGEMENT PARESSEUX : `load()` n'est appelé qu'au moment où une extraction
  tourne, jamais à l'import des modules. L'app démarre donc normalement même si le
  dossier `prompts/` est absent (cas de l'image Docker slim, qui ne copie que
  `app/` — comme les manuels Indigo dans `context/`, ce dossier reste local à
  l'instance qui construit les exercices). Un prompt manquant lève une erreur
  CLAIRE au moment de l'utiliser, pas un plantage silencieux.
- RELECTURE À CHAUD : le contenu est mis en cache par (chemin, mtime) — éditer un
  fichier prend effet au prochain appel, sans redémarrer l'application.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


class PromptNotFound(RuntimeError):
    """Fichier de prompt attendu mais introuvable (message actionnable)."""


# cache { chemin -> (mtime, contenu) } : re-lit seulement si le fichier a changé
_cache: dict[Path, tuple[float, str]] = {}


def prompt_path(pipeline: str, name: str) -> Path:
    return settings.prompts_dir / pipeline / f"{name}.txt"


def load(pipeline: str, name: str) -> str:
    """Contenu du prompt `prompts/<pipeline>/<name>.txt`. Lève `PromptNotFound`
    avec un message clair si le fichier manque (jamais un prompt vide en silence)."""
    path = prompt_path(pipeline, name)
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        raise PromptNotFound(
            f"Prompt « {pipeline}/{name} » introuvable : {path}\n"
            "Les prompts éditables vivent dans le dossier `prompts/` à la racine du "
            "repo (rangés par pipeline), comme les manuels dans `context/`. Vérifie "
            "que le dossier existe là où tourne la construction, ou surcharge "
            "MATHPRINT_PROMPTS_DIR.") from e
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text

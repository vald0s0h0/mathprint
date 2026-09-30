# Astra — exercices d'un chapitre du manuel, par GPT-6 Astra (Codex)

**Astra** est l'agent Codex `gpt-6-astra` (abonnement, aucune clé d'API) lancé
depuis le chat VSCode. Il lit lui-même les images des pages d'un chapitre du
manuel élève Indigo 3e (leçon + exercices). Pour chaque exercice, il écrit une
version **Base** et une version **Facile**, avec des réponses uniquement
**cochées ou reliées** (correction par vision par ordinateur). Les guides sont des
encadrés jaunes dans l'énoncé, et les figures sont redessinées ou découpées.

Les exercices arrivent en **brouillons** dans l'onglet **Exercices** de l'app
(badge « Astra »), où tu les valides, modifies ou supprimes, puis les publies.

## Lancer (dans le chat Codex, à la racine du repo)

```
astra "Fonctions affines"
```

Variantes :

- `astra "Thalès" pages 116-118` : seulement ces pages d'exercices (pages PDF) ;
- `astra B3` : le chapitre par son code.

Codex lit alors [`ASTRA.md`](ASTRA.md), qui contient la procédure, les règles et les
exemples, et enchaîne les commandes ci-dessous.

## Prérequis

- Codex configuré sur `gpt-6-astra` (`~/.codex/config.toml`).
- Manuel élève présent : `context/3_indigo.pdf` (cf. `settings.indigo_manuals`).
- L'application a démarré au moins une fois, ce qui crée et migre la base.

## Commandes (appelées par Astra ; utilisables à la main)

```bash
PY=backend/.venv/bin/python
$PY agents/astra/run.py chapters                          # chapitres, pages, compétences
$PY agents/astra/run.py prepare --chapter "Fonctions affines" [--pages 76-78] [--lesson 74-75]
$PY agents/astra/run.py figures  [RUN]                    # PNG des figures à vérifier
$PY agents/astra/run.py validate [RUN]                    # contrat MathPrint + règles Astra
$PY agents/astra/run.py preview  [RUN] [--no-guides]      # PDF réel des copies + PNG
$PY agents/astra/run.py persist  [RUN] [--replace]        # brouillons onglet Exercices
```

`RUN` est l'identifiant du run (ex. `b3-20260929-204419`) ou son dossier ; par
défaut, c'est le dernier run.

Chaque run vit dans `data/astra/runs/<run>/`, hors dépôt car les pages du manuel
sont sous droits :

- `pages/` : images des pages ;
- `payload.json` : chapitre, compétences, pages ;
- `astra_output.json` : écrit par Astra ;
- `report.json` : rapport de validation ;
- `figures/` : PNG des figures ;
- `preview/` : aperçu PDF et PNG.

## Fichiers

| Fichier | Rôle |
|---|---|
| [`ASTRA.md`](ASTRA.md) | Le prompt : procédure, fidélité, formats, guides, figures, exemples |
| [`schema.md`](schema.md) | Contrat exact de `astra_output.json` (dont les specs `geo` / `chart`) |
| `chapters_3e.json` | Pages PDF de la leçon et des exercices de chaque chapitre (relevées à la main) |
| `astra.py` | Préparation, validation, figures, aperçu, persistance (réutilise le backend) |
| `run.py` | Ligne de commande |

## Ce qui est réutilisé du backend

- **Validation** :
  - `exercise_gen._validate_exercise` (le validateur partagé, avec
    `require_correction=False`) ;
  - `indigo_check.verify` (bornes, distracteurs, `check` SymPy) ;
  - les normaliseurs de `indigo_multipass` ;
  - `scoring.with_qcm_bareme` (barème codé).
- **Guides** : `statement.GUIDE_TOKEN` (`{{aide}}`), rendus par `pdfgen` et par l'aperçu
  web.
- **Figures** : `services/geofig.py` (géométrie) et `services/chartfig.py`
  (graphiques), déclarés dans `figures.FIGURE_TYPES`.
- **Rendu** : `generation.render_shape` et `pdfgen.render_copy`, le rendu réel des
  copies.
- **Persistance** : lignes `IndigoExercise` (`variant_kind` base/facile,
  `derived_from_id`, `raw_ocr_json.pipeline = "astra"`), puis le chemin de
  publication Indigo habituel.

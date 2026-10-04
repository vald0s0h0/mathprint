# Astra — exercices d'un chapitre du manuel, par GPT-6 Astra (Codex)

**Astra** est l'agent Codex `gpt-6-astra` (abonnement, aucune clé d'API) lancé
depuis le chat VSCode. Il lit lui-même les images des pages d'un chapitre d'un
manuel élève Indigo, 3e ou 6e (leçon + exercices). Pour chaque exercice, il écrit une
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
- `astra "6e B3"` : le chapitre par son code, précédé du niveau (les codes A1, B3…
  existent dans les deux manuels ; une demande ambiguë est refusée) ;
- `astra "6e 10"` : le chapitre par son numéro dans le livre (6e).

Codex lit alors [`ASTRA.md`](ASTRA.md), qui contient la procédure, les règles et les
exemples, et enchaîne les commandes ci-dessous.

## Prérequis

- Codex configuré sur `gpt-6-astra` (`~/.codex/config.toml`).
- Manuel élève présent : `context/3_indigo.pdf` ou `context/6_indigo.pdf`
  (cf. `settings.indigo_manuals`).
- L'application a démarré au moins une fois, ce qui crée et migre la base.

## Commandes (appelées par Astra ; utilisables à la main)

```bash
PY=backend/.venv/bin/python
$PY agents/astra/run.py chapters                          # chapitres, pages, compétences (tous niveaux)
$PY agents/astra/run.py prepare --chapter "Fonctions affines" [--pages 76-78] [--lesson 74-75]
$PY agents/astra/run.py figures  [RUN]                    # PNG des figures à vérifier
$PY agents/astra/run.py validate [RUN]                    # contrat MathPrint + règles Astra
$PY agents/astra/run.py preview  [RUN] --variant base|facile|original [--no-guides]  # sujet séparé, sur demande
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
- `subject-<variante>/` : sujet séparé (PDF et PNG), seulement sur demande.

## Fichiers

| Fichier | Rôle |
|---|---|
| [`ASTRA.md`](ASTRA.md) | Le prompt : procédure, fidélité, formats, guides, figures, exemples |
| [`schema.md`](schema.md) | Contrat exact de `astra_output.json` (dont les specs `geo` / `chart`) |
| `chapters_3e.json`, `chapters_6e.json` | Pages PDF de la leçon et des exercices de chaque chapitre (relevées à la main) ; un fichier par niveau |
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

## Manuel 6e : reconstruit depuis la liseuse

Le manuel élève Mission Indigo 6e n'existe qu'en liseuse en ligne. On l'a capturé
double page par double page (`Capture d’ecran (n).png`, de la couverture à
l'index p. 320), puis converti au format des manuels (une page PDF = une double
page imprimée, page PDF p = pages 2p-2 et 2p-1) :

```bash
backend/.venv/bin/python backend/scripts/indigo_screenshots_to_pdf.py \
    "~/Downloads/Livre 6eme/Screenshots" context/6_indigo.pdf
```

Le script retire l'interface de la liseuse et prend les captures dans l'ordre de
leur numéro : un numéro sauté dans le nommage n'est pas une page manquante (127 et
133 n'existent pas, sans trou dans le livre). Le corrigé, `context/6_indigo_prof.pdf`,
est le guide pédagogique (PDF texte). Les pages sont à ≈ 130 dpi natifs (contre
≈ 220 pour le 3e) : lisibles, mais les petits indices méritent un `crop` en cas de
doute. Les particularités de mise en page (compétence en titre, ceintures, énigmes)
sont décrites dans `ASTRA.md`.

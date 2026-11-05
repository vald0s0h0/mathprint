# Contrat d'un emplacement : `slots/<emplacement>.json`

Un fichier par exercice. `run.py check` vérifie ce contrat et renvoie une erreur courte en cas d'écart.

## Commun à tout exercice

| Champ | Contenu |
|---|---|
| `slot` | identifiant de l'emplacement (`s07`, `p03`) |
| `response_type` | format de la grille, ou autre si `slot_swap` |
| `statement` | énoncé. Formules dans `$...$`. Pas de numéro ni de rubrique |
| `estimated_seconds` | entier : temps réel d'un élève de ce niveau |
| `micro_skill` | le savoir-faire précis évalué |
| `family_id` | courte étiquette de structure : deux exercices de même structure ont la même |
| `figure` | voir plus bas, ou `null` |
| `figure_layout` | ne le renseigne pas : le script essaie les dispositions et garde la plus compacte. `left`, `right` : contexte à côté de la figure, puis question et réponses sur toute la largeur. `grid_left`, `grid_right` : énoncé et réponses à côté de la figure, jamais pour une réponse à écrire |
| `leak_keys` | ce que la réponse révèle : `["formule:aire_disque", "valeur:3/4"]` |
| `gives` | ce que l'énoncé affiche, dans la même notation |
| `slot_swap` | facultatif : `{"reason": "..."}` pour changer de format ou de taille |
| `proposed_errors` | facultatif : `[{"code": "math.x.y", "label": "..."}]` |
| `review_flags` | facultatif : `["..."]`, ce que le relecteur doit regarder sur cette carte. Jamais imprimé |
| `title` | problème seulement |

## Guides, sur chaque facile et chaque moyen

Un difficile n'en a pas. Champ `guides` de la question : `[{"text": "...", "position": "..."}]`. Sur un composite, les guides de la tâche commune vont sur la carte, les autres sur chaque partie.

| `position` | Où il s'imprime | Quand le choisir |
|---|---|---|
| `avant_question` | entre le contexte et la question (sépare-les par un saut de ligne) ; sur une partie, avant sa lettre | il sert à lire la situation |
| `apres_question` | après la question, avant la zone de réponse | il donne une méthode pour répondre : cas le plus fréquent |
| `apres_enonce` | composite seulement : après le contexte commun | une méthode vaut pour toutes les parties |

- 4 à 30 mots, tutoiement. Une prochaine étape ou une méthode du cours, ciblée sur cette question.
- Jamais la réponse, un calcul résolu, ni un indice qui désigne la bonne proposition. Écrire la bonne réponse telle quelle est refusé par `check`.
- La carte reste complète sans lui, car un sujet peut retirer les guides.
- Pas de guide passe-partout recopié d'une carte à l'autre.

## Micro-corrections, pour chaque question

| Champ | Contenu |
|---|---|
| `blank_feedback` | obligatoire : message si l'élève ne répond pas |
| `fallback_feedback` | obligatoire : message générique si la réponse est fausse et imprévue |
| `correct_feedback` | message si c'est juste. Obligatoire sur un moyen ou un difficile |

Budget : 2 lignes (environ 100 caractères) pour une réponse cochée ou courte, 4 lignes (environ 200 caractères) pour une rédaction. Le script mesure le texte réel.

Un message s'adresse à l'élève (« Tu as… »), dit ce qu'il a fait et ce qu'il faut faire. Il ne cite **jamais une lettre ni une position** (« la 2e case ») : le placement des réponses changera d'une copie à l'autre. Les messages de comptage (« Tu as coché une case de trop ») sont ajoutés par l'application : ne les écris pas.

Dans un message, écris une fraction en ligne (`$1/2$`), jamais `\dfrac` : une fraction haute prend la place de deux lignes.

## Formats

### `qcm_single`, `qcm_multiple`

```json
{"choices": [
  {"id": "a", "text": "$11$", "correct": true},
  {"id": "b", "text": "$14$", "correct": false, "calc": "(3+4)*2",
   "error": "math.calc.priorite_operations",
   "feedback": "Tu as additionné avant de multiplier : la multiplication passe d'abord."}],
 "answer_expr": "3+4*2"}
```

- `id` : stable, sans rapport avec la position. L'ordre pourra changer d'une copie à l'autre.
- `calc` : le calcul exact qui mène à ce distracteur, en SymPy ASCII. Vérifié.
- `answer_expr` : le calcul de la bonne réponse. Vérifié. Si rien ne se calcule, remplace-le par `no_check_reason`.
- Un `qcm_multiple` ne marque jamais toutes les propositions justes.

### `checkbox_grid`

```json
{"cols": ["Vrai", "Faux"],
 "rows": [{"id": "r1", "label": "$2^3 = 8$", "correct": 0,
           "error": "math.calc.puissance_produit",
           "feedback": "$2^3$ signifie $2 \\times 2 \\times 2$, pas $2 \\times 3$."}]}
```

### `matching`

```json
{"left": ["Quotient", "Produit", "Somme"],
 "right": [
  {"id": "d1", "text": "Résultat d'une division", "error": "math.geo.vocabulaire", "feedback": "..."},
  {"id": "d2", "text": "Résultat d'une multiplication", "error": "math.geo.vocabulaire", "feedback": "..."},
  {"id": "d3", "text": "Résultat d'une addition", "error": "math.geo.vocabulaire", "feedback": "..."},
  {"id": "piege1", "text": "Résultat d'une soustraction", "error": "math.geo.vocabulaire", "feedback": "..."}],
 "pairs": [[0, "d1"], [1, "d2"], [2, "d3"]]}
```

- Chaque élément de gauche a exactement un lien. Chaque point de droite a au plus un lien et son message.
- Un point de droite sans lien est un **piège**. Le script ajoute lui-même la consigne « Attention : N propositions ne servent pas. ». Ne l'écris pas.
- 2 à 6 éléments de chaque côté, 80 caractères au plus.

### `short_text`

Ne mets `{{blank}}` dans l'énoncé que pour une amorce courte qui tient sur une ligne (`$x =$ {{blank}}`). Après une phrase complète, la case passe seule à la ligne, ce que le moteur fait tout seul : termine simplement par ta question.

```json
{"answer": {"type": "rational", "value": [3, 4]},
 "answer_form": "irreducible",
 "answer_expr": "1/2+1/4",
 "wrong_answers": [
  {"value": "6/8", "error": "math.frac.non_simplifiee",
   "feedback": "Ta fraction est juste, mais elle n'est pas irréductible."},
  {"value": "2/6", "error": "math.frac.addition_num_den",
   "feedback": "Tu as additionné numérateurs et dénominateurs : réduis d'abord au même dénominateur."}]}
```

- `answer.type` : `integer`, `decimal`, `rational` (`[num, den]`), `expression` (avec `variable`), `text`.
- `answer_form` : `any`, `irreducible`, `decimal`, `factored`, `expanded`, `scientific`, `exact_decimal`. **Il doit concorder avec l'énoncé**, dans les deux sens.
- `exact_decimal` : obligatoire dès que l'énoncé demande d'arrondir ou d'encadrer, aussi en `multi_blank` et `table_fill`. Seule la valeur attendue rapporte des points : sur « arrondis $1{,}786$ au centième », $1{,}79$ est juste, $1{,}786$ et $1{,}8$ valent $0$. Écris ces réponses en `decimal` ou `integer`, jamais en `text`. Deux écritures de même valeur sont justes ($6$ et $6{,}0$).

### `multi_blank`

Un `{{blank}}` par valeur, dans l'ordre : `"blanks": [{"type": "integer", "value": 12}, ...]`, avec `answer_form` et `wrong_answers` (ajoute `"cell": [0, k]` pour viser une case).

Quand plusieurs réponses sont justes dans n'importe quel ordre, déclare-le : `"interchangeable": [[2, 3]]` (rangs des cases, à partir de 0). Sur `$x =$ {{blank}} ou $x =$ {{blank}}`, $7$ puis $-7$ et $-7$ puis $7$ rapportent alors tous les points, et une valeur écrite deux fois ne compte qu'une fois. Les autres cases restent comparées à leur place. Une `wrong_answers` d'une telle case ne doit pas être une réponse du groupe. Deux cases séparées par « ou » sans cette déclaration sont refusées. N'impose jamais un ordre artificiel à l'élève.

Réservé aux égalités courtes à trous, sur une ligne. Deux cas sont refusés, car ils s'impriment mal :
- des cases étiquetées (`Abscisse de $A$ : {{blank}}`) : utilise `table_fill` ;
- une phrase à trous qui se replie : les cases écartent son interligne. Utilise `table_fill`, ou mets-la sur sa propre ligne.

### `table_fill`

```json
{"rows_count": 2, "cols_count": 3, "col_labels": ["A", "B", "C"], "row_labels": ["x", "y"],
 "cells": [[{"type": "integer", "value": 1, "given": true}, {"type": "integer", "value": 2},
            {"type": "integer", "value": 3}],
           [{"type": "integer", "value": 2, "given": true}, {"type": "integer", "value": 4},
            {"type": "integer", "value": 6}]],
 "wrong_answers": [{"cell": [0, 1], "value": "3", "error": "math.prop.additif", "feedback": "..."}]}
```

Si le tableau nomme ses lignes **et** ses colonnes, déclare ce qui peut être mélangé d'une copie à l'autre, car le moteur ne le devine pas, et une carte sans `mix` est refusée :

| `mix` | Quand le choisir |
|---|---|
| `rows` | chaque ligne est une entité (une équation, un point) et les colonnes se lisent dans l'ordre (« puis ») |
| `columns` | chaque colonne est une entité (un produit, un point) et les lignes décrivent ses grandeurs ou des étapes |
| `none` | aucun des deux axes ne peut bouger |

Le tableau exige au moins 2 lignes (`rows_count`) ; pour une seule série de valeurs, mets les libellés dans `row_labels`, une valeur par ligne. Les colonnes sont égales sauf si l'écriture de la réponse l'empêche. Avec un libellé de ligne, compte 4 colonnes au plus pour des nombres de 4 chiffres.

### `multiline_text`

```json
{"steps": [{"description": "Pose l'équation", "expected_text": "$2x + 3 = 11$", "points": 1},
           {"description": "Résout", "expected_text": "$x = 4$", "points": 1}],
 "expected_errors": [{"id": "e1", "error": "math.eq.transposition",
                      "feedback": "Quand un terme change de membre, son opération change aussi."}],
 "llm_brief": {
   "objective": "Mettre un problème en équation et la résoudre",
   "expected_steps": ["Choisir l'inconnue", "Poser l'équation", "Résoudre", "Conclure"],
   "other_methods": ["Essais organisés, s'ils sont justifiés"],
   "expected_errors": [{"message_id": "e1", "trap": "Change le signe sans changer l'opération"}],
   "do_not_penalize": ["Une conclusion sans phrase complète"]}}
```

Le nombre de lignes est calculé d'après `expected_text`. Les étapes sont visibles du correcteur, jamais de l'élève.

### `composite`

Contexte dans `statement`, questions dans `parts` : chaque partie suit le format de son type, avec ses micro-corrections. Dans un problème, chaque partie porte `"competency": "A1.2"`. Une partie peut avoir sa propre `figure`.

## Route de correction

Le script la fixe d'après le format. Tu peux seulement demander le LLM pour une réponse manuscrite courte qui admet beaucoup de formes valables (« Donne un exemple de… ») : `"route": "llm"`, `"route_reason": "..."`, et `llm_brief` devient obligatoire.

## Figures

`{"kind": "...", "spec": {...}}`, par ordre de préférence. **Jamais de découpe d'une page de manuel.**

### `geo` : géométrie exacte

```json
{"kind": "geo", "spec": {
  "points": {"A": [0, 0], "B": [4, 0], "C": [4, 3]},
  "polygons": [["A", "B", "C"]],
  "angles": [{"at": "B", "from": "A", "to": "C", "right": true}],
  "lengths": [{"seg": ["A", "B"], "label": "4 cm"}],
  "width_mm": 45}}
```

Le rendu suit les coordonnées : rends-les vraies et cohérentes avec les valeurs affichées.

Clés : `points`, `segments`, `lines`, `rays`, `polygons`, `circles`, `arcs`, `angles`, `lengths`, `equal_marks`, `parallel_marks`, `texts`, `axes`, `functions`, `width_mm`. Leur détail est en tête de `backend/app/services/geofig.py`.

### `chart` : données

Repère et courbes, barres, histogramme, secteurs, arbre de probabilités. Détail en tête de `backend/app/services/chartfig.py`.

### `template` : gabarits

`{"kind": "template", "spec": {"name": "cube", "labels": {"width": "3 cm"}, "width_mm": 35}}`

| `name` | Paramètres |
|---|---|
| `cube` | `labels` : `width`, `height`, `depth` |
| `pave` | `width`, `height`, `depth`, `labels` |
| `cylindre`, `cone` | `labels` : `height`, `radius` |
| `pyramide` | `labels` : `height` |
| `boule` | `labels` : `radius` |
| `patron_cube` | `cells` (6 positions), `marks` (`{"0": "A"}`) |
| `fraction` | `total`, `filled`, `shape` (`bar` ou `disc`) |
| `droite_graduee` | `min`, `max`, `step`, `label_every`, `points` (`[{"x": 2, "label": "A"}]`) |
| `balance` | `left`, `right`, `state` (`equal`, `left`, `right`) |

### `svg` : dessin qu'aucun gabarit ne couvre

`{"kind": "svg", "spec": {"svg": "<svg viewBox=\"0 0 100 60\">...</svg>", "width_mm": 50}}`

Balises : `svg`, `g`, `line`, `polyline`, `polygon`, `rect`, `circle`, `ellipse`, `path`, `text`. Couleurs : `none`, `black`, `white`, `gray` ou `#hex`. Ni script, ni lien, ni image.

Choisis la taille la plus petite qui reste lisible. Une figure ne porte aucune information par la seule couleur, car l'impression peut être en noir et blanc.

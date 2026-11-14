# Contrat d'un emplacement : `slots/<emplacement>.json`

Un fichier par exercice. `run.py check` vérifie ce contrat et renvoie une erreur courte en cas d'écart.

## Familles : `familles/<emplacement>.json`

Tu écris la carte une fois, avec ses valeurs à remplir. `check` en tire les jumelles (chaque jeu, dans chaque disposition), les contrôle toutes et les rend toutes.

```json
{"slot": "s10",
 "jeux": [{"a": 5, "b": 2, "c": 6}, {"a": 4, "b": 3, "c": 7}, {"a": 8, "b": 2, "c": 9}],
 "carte": {
   "response_type": "qcm_single",
   "statement": "Calcule $<<a>> + <<b>> \\times <<c>>$.",
   "choices": [
     {"id": "a", "text": "$<<a+b*c>>$", "correct": true},
     {"id": "b", "text": "$<<(a+b)*c>>$", "correct": false, "calc": "(<<a>>+<<b>>)*<<c>>",
      "error": "math.calc.priorite_operations", "feedback": "Tu as additionné d'abord : …"},
     {"id": "c", "text": "$<<a+b+c>>$", "correct": false, "calc": "<<a>>+<<b>>+<<c>>",
      "error": "math.calc.operation_inversee", "feedback": "Tu as additionné $<<b>>$ et $<<c>>$ : …"}],
   "answer_expr": "<<a>>+<<b>>*<<c>>",
   "dispositions": [["a", "b", "c"], ["c", "a", "b"]],
   "…": "le reste du contrat ci-dessous"}}
```

**`jeux`** : 2 ou 3 jeux, mêmes noms dans chacun. Une valeur est un nombre (`5`, `"2,5"`, `"3/4"`) ou un texte (`"M"`, `"Awa"`, `"un sac de riz"`). `check` refuse des jeux de difficulté inégale (un entier ici, un décimal là ; un signe qui change ; une valeur dix fois plus grande) et deux jeux qui donnent la même réponse.

**`<<…>>`** se remplit partout dans `carte` : énoncé, propositions, messages, guides, figure, dossier du correcteur.

| Tu écris | Il s'imprime | Remarque |
|---|---|---|
| `<<a>>` | la valeur du jeu | un texte est repris tel quel |
| `<<a*b+1>>` | le résultat, calculé par SymPy | **n'écris jamais toi-même un résultat** : il serait faux dans les autres jeux |
| dans `$…$` | `2{,}5`, ou `\dfrac{1}{3}` si le décimal ne tombe pas juste | choisi seul |
| hors formule | `2,5` | choisi seul |
| dans `calc`, `answer_expr` | `5/2` | écriture SymPy, choisie seule |
| dans une figure TikZ | `2.5` | pour une valeur affichée, écris `<<a\|txt>>` |
| `"value": "<<a*b>>"` (seul dans le champ) | le nombre `10`, pas un texte | pour `answer.value`, une cellule, un `blank` |

Formats à ajouter après une barre quand le choix automatique ne convient pas : `|txt` (2,5), `|tex` (2{,}5), `|frac` (fraction même si le décimal tombe juste), `|dec2` (arrondi à 2 décimales), `|calc`, `|pt` (2.5), `|num` (nombre), `|rat` (`[5, 2]`, pour une réponse de type `rational`).

**`dispositions`**, sur la question (ou sur chaque partie d'un composite) : deux ordres, écrits par toi.

| Format | Ce que tu ordonnes |
|---|---|
| `qcm_single`, `qcm_multiple` | les `id` des propositions |
| `checkbox_grid` | les `id` des lignes, seulement si elles sont indépendantes |
| `matching` | les `id` de la colonne de droite |
| `table_fill` | les rangs des colonnes, à partir de 0 (`[[0, 1, 2], [2, 0, 1]]`) ; les lignes ne bougent jamais |

Sur ces quatre formats, `dispositions` est obligatoire, sauf pour le tableau. Si l'ordre ne peut pas changer, écris à la place `"ordre_impose": "étapes d'un raisonnement"` (ou chronologie, valeurs croissantes, protocole) : la carte n'a alors qu'une disposition. `check` refuse deux dispositions identiques, une disposition incomplète, et un QCM dont la bonne réponse garde la même place.

Une famille fautive ne donne aucune carte : l'erreur est rendue sous le nom de l'emplacement. Une jumelle refusée (`s10.j2d1 : …`) fait tomber toute sa famille : corrige la famille, jamais une jumelle.

## Commun à tout exercice

| Champ | Contenu |
|---|---|
| `slot` | identifiant de l'emplacement (`s07`, `p03`) |
| `response_type` | format de la grille, ou autre si `slot_swap` |
| `statement` | énoncé. Formules dans `$...$`. Pas de numéro ni de rubrique |
| `estimated_seconds` | entier : temps réel d'un élève de ce niveau |
| `micro_skill` | le savoir-faire précis évalué |
| `family_id` | courte étiquette de structure : deux exercices de même structure ont la même |
| `figure` | voir plus bas, ou `null` (toujours `null` pour un tracé) |
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

### `geo_construction` : géométrie à tracer

L'élève construit sur la copie. La CV lit le tracé et le note quand sa confiance atteint le seuil réglé ; sinon, le professeur corrige. Deux modes, selon `given` :

La grille fixe le mode de chaque tracé (`trace_mode`, annoncé par `suivant`) : `quadrillage` (ancré, `grid` vrai), `case` (ancré, `"grid": false`) ou `libre` (`given` vide). Un autre mode demande `slot_swap.reason`.

- **Ancré** : une figure de départ imprimée en noir (`given`), sur quadrillage saumon (carreaux de 5 mm) ou, avec `"grid": false`, dans une case saumon. Le tracé attendu a une place exacte.
- **Libre** : `"given": {}`. Une case saumon à coins arrondis, sans quadrillage. L'élève pose sa figure où il veut, tournée ou retournée : la CV la retrouve, puis mesure longueurs, angles et rayons (longueur à 2 mm près).

```json
{"construction": {
   "grid": true, "cols": 16, "rows": 9,
   "points": {"A": [1, 1], "B": [6, 2], "C": [2, 7], "P": [8, 0], "Q": [8, 9],
              "A'": [15, 1], "B'": [10, 2], "C'": [14, 7],
              "A1": [9, 1], "B1": [14, 2], "C1": [10, 7]},
   "given": {"points": ["A", "B", "C"], "polygons": [["A", "B", "C"]],
             "lines": [{"through": ["P", "Q"], "label": "(d)"}]},
   "expected": {"polygons": [["A'", "B'", "C'"]]}},
 "expected_errors": [
   {"id": "glisse", "error": "math.geo.symetrie",
    "feedback": "Tu as fait glisser le triangle : la symétrie le retourne.",
    "trace": {"polygons": [["A1", "B1", "C1"]]}}]}
```

Libre, un angle de 50° :

```json
{"construction": {
   "cols": 16, "rows": 9, "given": {},
   "points": {"O": [2, 1.5], "X": [12, 1.5], "Y": [7.785, 8.394], "Z": [-2.5, 6.862]},
   "expected": {"rays": [["O", "X"], ["O", "Y"]]}},
 "expected_errors": [
   {"id": "graduation", "error": "math.geo.angle_mesure",
    "feedback": "Ton angle mesure $130^\\circ$ : lis la graduation qui part de $0$ sur ton premier côté.",
    "trace": {"rays": [["O", "X"], ["O", "Z"]]}}]}
```

- **Coordonnées en carreaux** (5 mm), origine en bas à gauche. `cols` de 4 à 16, `rows` de 3 à 14. Sur quadrillage, mets les sommets sur des nœuds ; avec un axe qui suit une diagonale des carreaux, chaque sommet imprimé est à un nombre entier de pas de l'axe (`check` le vérifie). En mode libre, la figure modèle tient dans la case avec un demi-carreau de marge : elle donne les vraies dimensions (6 cm = 12 carreaux).
- **`points`** nomme tous les points : donnés, attendus, et ceux des erreurs. Seuls les points de `given.points` s'impriment, avec une croix et leur nom. En mode ancré, tout point reste dans la zone, traces d'erreur comprises (la CV ne lit rien au-dehors) : seul un point qui ne sert qu'à porter une droite peut en sortir. Choisis donc des erreurs dont l'image tient dans la zone.
- **Éléments** de `given`, `expected`, d'une alternative ou d'une trace : `points`, `segments`, `polygons`, `lines` (`[["A", "B"]]` ou `{"through": [...], "label": "(d)"}`), `rays` (demi-droite issue du premier point), `circles` (`{"center": "O", "radius": 3}` ou `{"center": "O", "through": "A"}`).
- **`expected`** : le tracé juste, jamais imprimé. `expected.points` sert à un point à placer (croix de l'élève). En mode libre, un segment a une longueur exigée, une demi-droite ou une droite non. **`alternatives`** : autres tracés justes (les deux triangles possibles au compas, en ancré ; en libre, une figure retournée est déjà acceptée).
- `figure` reste `null` : la figure de départ est dans `given`. Un tracé n'est jamais une partie de composite.
- `tolerance_mm` (1 à 3, défaut 1,5) : écart toléré au tracé attendu.

**Barème et temps.** `suivant` annonce, pour chaque tracé, le barème calculé par le script et les bornes de temps (facile 1 à 5 min, moyen 2 à 8 min, difficile 3 à 12 min). Donne `estimated_seconds` dans ces bornes. La note se partage également entre les éléments du tracé : deux côtés justes sur trois rapportent deux tiers des points. `render` et `persist` rappellent barème et temps de chaque carte.

**Micro-corrections.** Budget de 2 lignes (environ 90 caractères) à tous les niveaux, comme une réponse cochée. Comme pour les autres formats : `blank_feedback`, `fallback_feedback`, `correct_feedback` (moyen et difficile), et `expected_errors`. Donne une `trace` à chaque erreur qui se dessine, pour que la CV la reconnaisse et imprime son message :

| Mode | Erreurs qui se dessinent |
|---|---|
| ancré | image glissée au lieu de retournée ; symétrie centrale au lieu d'axiale ; mauvais centre ou mauvais axe ; parallèle au lieu de perpendiculaire ; rotation dans le mauvais sens |
| libre | angle supplémentaire (rapporteur lu sur l'autre graduation) ; longueur décalée d'1 cm (règle commencée à 1) ; cercle de rayon double (rayon et diamètre confondus) ; carré tracé en rectangle |

Une trace doit se distinguer du tracé juste : en libre, une figure de mêmes dimensions (côtés dans un autre ordre, figure retournée) se confond avec lui et `check` la refuse. L'application ajoute seule les messages de mesure (« Ton côté [AB] mesure 5,6 cm au lieu de 6 cm », « Il manque [BC] ») : ne les écris pas.

`check` refuse un élément trop court pour être mesuré, un tracé attendu caché sous la figure imprimée, une figure libre qui déborde de la case, ou une erreur qui se confond avec le tracé juste. `render` fait tracer la carte par un élève simulé (stylo, crayon, copie blanche, chaque erreur ; en libre, figure posée au hasard dans la case) : `TRACÉ MAL LU` bloque `persist`. L'image du tracé juste est dans `ecrit/`.

### `composite`

Contexte dans `statement`, questions dans `parts` : chaque partie suit le format de son type, avec ses micro-corrections. Dans un problème, chaque partie porte `"competency": "A1.2"`. Une partie peut avoir sa propre `figure`.

## Route de correction

Le script la fixe d'après le format. Tu peux seulement demander le LLM pour une réponse manuscrite courte qui admet beaucoup de formes valables (« Donne un exemple de… ») : `"route": "llm"`, `"route_reason": "..."`, et `llm_brief` devient obligatoire.

## Figures

`{"kind": "tikz", "spec": {"tikz": "\\begin{tikzpicture}...\\end{tikzpicture}"}}`. **Toute nouvelle figure s'écrit en TikZ**, jamais de découpe d'une page de manuel. Les anciens moteurs (`geo`, `chart`, `template`, `svg`) ne servent plus qu'aux exercices déjà en banque ; `check` les refuse dans une nouvelle carte.

Tu n'écris que le dessin. La charte `backend/app/assets/tikz/kn-figures.tex` fixe tout le reste : épaisseurs, couleurs, police, codages, cotes. Les figures de tous les exercices se ressemblent donc, quel que soit l'auteur. tikz, tkz-euclide, tikz-3dplot et pgfplots sont chargés ; les bibliothèques `calc`, `angles`, `quotes`, `arrows.meta`, `intersections`, `positioning`, `decorations` et `fit` aussi.

### Échelle

- **Une unité vaut 1 cm imprimé.** Une figure fait au plus 93 × 90 mm ; vise 40 à 75 mm de large.
- Si la figure est trop grande, réduis `x` et `y` ensemble, par palier : `[x=0.8cm,y=0.8cm]`, puis `0.6cm`, puis `0.5cm`. Un quadrillage de 5 mm : `[x=0.5cm,y=0.5cm]`. Garde les vraies valeurs dans les coordonnées : un côté de 6 cm vaut 6 unités.
- Perspective cavalière (collège) : `[x={(0.85cm,0)},y={(0.36cm,0.25cm)},z={(0,0.85cm)}]`, fuyante à 45° environ et réduite de moitié. Vraie perspective (lycée) : `\tdplotsetmaincoords{70}{110}` puis `[tdplot_main_coords]`.
- La taille des textes ne bouge pas : 10 pt pour les noms, 9,5 pt pour les cotes. Ne réduis pas une figure pour la caser.

### Styles

| Style | Pour |
|---|---|
| `trait` | tout trait de la figure |
| `fort` | ce qu'il faut voir : section, segment cherché |
| `cache` | arête cachée, tirets |
| `constr` | trait de construction, hauteur auxiliaire, pointillés de lecture |
| `aplat`, `aplatB` | fond très clair d'une surface, purement décoratif |
| `section` | section d'un solide (aplat et contour bleus) ; l'énoncé dit « la section colorée », jamais « grisée » |
| `sectionbord` | bords visibles d'une section, quand le fond est tracé à part (`\fill[section,draw=none]`) |
| `grille` | quadrillage : `\draw[grille] (0,0) grid[step=1] (12,8);` |
| `axe` | axe fléché |
| `origine` | figure de départ à repérer : aplat bleu soutenu |
| `figA` à `figE` | figures à distinguer entre elles : bleu, vert, violet, orange, framboise (couleurs `repA` à `repE`) |
| `faceH`, `faceV`, `faceD` | faces du dessus, de face et de côté d'un solide |
| `cylL`, `cylT` | surface et dessus d'un cylindre ou d'un cône |
| `etiq` | nom d'un point ou d'une figure (gras, bleu) |
| `petit` | graduation, légende secondaire |
| `cote` | valeur d'une longueur ou d'un angle (gras, orange) |
| `boite` | expression dans une case : programme de calcul, plateau de balance |

L'ordre de dessin est imposé par la charte, quel que soit l'ordre de ton code : les fonds (`aplat`, faces, `origine`, `figA`…) tout en bas, puis les codages d'angle, puis les traits et les textes. Un angle ne recouvre donc jamais un trait.

Le quadrillage `grille` est gris-bleu et bien visible : il sert à lire une figure, jamais à tracer. L'élève ne trace que dans une géométrie à tracer (`geo_construction`), dont le quadrillage et la case restent saumon : la CV les efface pour lire le tracé.

Interdit dans une figure : `line width`, `thin`, `thick`, `scale=`, `font=`, `\fontsize`, `\definecolor`, `\tikzset`, `\def`, `\newcommand`, `\input` et toute couleur hors palette. Pour une valeur calculée : `\pgfmathsetmacro{\r}{2}`.

### Couleurs

- Palette : `ink` (traits), `nomc` (noms), `cotec` (cotes), `accent`, `fillA`, `fillB`, `angfill`, `gridc`, `repA` à `repE`, `white`. Mélanges permis : `repA!40`, `fillA!50`.
- **Les sujets sont imprimés en couleur.** Une couleur sert à repérer, jamais à décorer :
  - la figure d'origine en `origine` ;
  - ses images ou les figures candidates en `figA` à `figE`, une couleur par figure ;
  - une courbe ou un tracé : `draw=repA` ; plusieurs courbes, une couleur chacune ;
  - une section en `section`.
- Ne donne jamais une réponse par la couleur : quatre images candidates ont quatre couleurs différentes, pas la bonne en vert.

### Macros

| Macro | Effet |
|---|---|
| `\Pt{(2,1)}{A}{above right}` | point marqué d'une croix, et son nom |
| `\Nom{(A)}{A}{below left}` | nom seul : sommet d'un polygone, numéro d'une figure |
| `\PtA{(A)}{A}{215}`, `\NomA{(A)}{A}{215}` | même chose, nom posé à 3,2 mm dans la direction donnée en degrés : la plus dégagée, à l'opposé des traits qui partent du point |
| `\AngDroit{A}{B}{C}` | angle droit en B (points définis par `\coordinate`) |
| `\AngMark{A}{B}{C}{37°}{1}` | angle en B avec 1 ou 2 arcs et sa valeur ; `{}` sans valeur ; `0` arc pour la valeur seule |
| `\Cote{(A)}{(B)}{6 cm}{4mm}` | cote fléchée parallèle à [AB], décalée de 4 mm à gauche du sens A→B (`-4mm` : à droite), avec lignes de rappel |
| `\CoteV{(5,0)}{(5,3)}{3 m}` | cote verticale, valeur à droite |
| `\Marque{(A)}{(B)}{2}` | codage de longueurs égales (1 à 3 traits) |
| `\DroiteGraduee{-5}{7}` | droite graduée d'entiers ; points avec `\Pt{(2,0)}{A}{above}` |
| `\Balance{$5x+4$}{$34$}` | balance de Roberval ; `\Balance[gauche]{…}{…}` si le plateau gauche est plus lourd, `[droite]` sinon |

Une figure qui se replie ou se mesure est géométriquement juste. Patron de cône : secteur d'angle 360° × r / g, disque tangent à l'arc en son milieu. Patron de cylindre : longueur du rectangle égale à 2πr, disques tangents aux grands côtés. Dans un commentaire `%`, note le calcul qui fixe ces dimensions.

Place chaque cote **hors de la figure**, du côté libre ; une valeur ne chevauche jamais un trait. Les nombres s'écrivent à la française (`2,4`, `37°`) ; une expression en `$…$` prend la police de la figure.

### Exemples

Triangle coté et codé :

```latex
\begin{tikzpicture}[x=0.85cm,y=0.85cm]
\coordinate (A) at (2.7,4.2); \coordinate (B) at (0,0); \coordinate (C) at (7,0);
\coordinate (M) at (1.62,2.52); \coordinate (N) at (4.43,2.52);
\filldraw[trait,aplat] (A)--(B)--(C)--cycle;
\draw[trait] (M)--(N);
\Nom{(A)}{A}{above} \Nom{(B)}{B}{below left} \Nom{(C)}{C}{below right}
\Nom{(M)}{M}{left} \Nom{(N)}{N}{right}
\Cote{(A)}{(M)}{2}{-3mm} \Cote{(A)}{(N)}{2,4}{3mm} \Cote{(B)}{(C)}{7}{-5mm}
\end{tikzpicture}
```

Figures à repérer sur quadrillage :

```latex
\begin{tikzpicture}[x=0.45cm,y=0.45cm]
\draw[grille] (0,0) grid[step=1] (15,9);
\filldraw[origine] (6,6)--(9,6)--(9,7)--(7,7)--(7,8)--(6,8)--cycle;
\filldraw[figA] (6,4)--(6,1)--(7,1)--(7,3)--(8,3)--(8,4)--cycle;
\filldraw[figB] (4,6)--(1,6)--(1,7)--(3,7)--(3,8)--(4,8)--cycle;
\Pt{(5,5)}{O}{above right}
\Nom{(8.2,7.6)}{F}{} \Nom{(7.6,1.8)}{1}{} \Nom{(2.2,7.6)}{2}{}
\end{tikzpicture}
```

Pavé en perspective cavalière avec section :

```latex
\begin{tikzpicture}[x={(0.85cm,0)},y={(0.36cm,0.25cm)},z={(0,0.85cm)}]
\foreach \n/\c in {A/{0,0,0},B/{6,0,0},C/{6,4,0},D/{0,4,0},E/{0,0,3},F/{6,0,3},
  G/{6,4,3},H/{0,4,3},I/{2.5,0,0},J/{2.5,4,0},K/{2.5,4,3},L/{2.5,0,3}} \coordinate (\n) at (\c);
\fill[faceH] (E)--(F)--(G)--(H)--cycle; \fill[faceV] (A)--(B)--(F)--(E)--cycle;
\fill[faceD] (B)--(C)--(G)--(F)--cycle;
\fill[section,draw=none] (I)--(J)--(K)--(L)--cycle;
\draw[cache] (A)--(D)--(C) (D)--(H) (I)--(J)--(K);
\draw[trait] (A)--(B)--(C)--(G)--(H)--(E)--cycle (E)--(F)--(G) (F)--(B);
\draw[sectionbord] (I)--(L)--(K);
\Nom{(A)}{A}{below left} \Nom{(B)}{B}{below} \Nom{(L)}{L}{above left}
\Cote{(A)}{(B)}{6 cm}{-6mm} \Cote{(E)}{(A)}{3 cm}{-6mm}
\end{tikzpicture}
```

Courbe dans un repère (pgfplots) :

```latex
\begin{tikzpicture}
\begin{axis}[kn repere,width=6cm,height=5cm,xmin=-1,xmax=5,ymin=-1,ymax=6,xtick={0,...,4},ytick={0,...,5}]
\addplot[draw=repA,domain=-1:5,samples=50] {0.5*x^2-x+1};
\end{axis}
\end{tikzpicture}
```

`check` compile chaque figure et renvoie l'erreur avec son numéro de ligne (« tikz : ligne 4 : Undefined control sequence »). Il refuse aussi une figure trop grande ou un réglage hors charte. Regarde la carte rendue : aucun nom ni aucune valeur ne doit chevaucher un trait.


### Kits par matière

Un kit est chargé **seulement** si ta figure emploie une de ses macros (la clé et le temps de compilation des autres figures ne changent pas). Les palettes, styles et polices sont ceux de la charte ; les interdits de `check` valent pour ton dessin, pas pour les kits. Les dimensions des macros de verrerie et de mécanique sont en cm réels : garde l'unité de la figure à 1 cm.

**Supports imprimés** (`kn-supports.tex`)

| Macro | Effet |
|---|---|
| `\Calculatrice{haut}{bas}` | écran de calculatrice (4,6 cm), police à chasse fixe, ligne du bas à droite : `2,9999999`, `1.5E-4` |
| `\Ticket{titre}{Riz 5 kg/3 500,Huile 1 L/1 200}{4 700 F}` | ticket de caisse de 4,2 cm : une ligne `libellé/prix` par article (séparées par des virgules, pas de virgule dans un libellé), TOTAL en bas |
| `\Copie[Awa]{3x + 2 = 11\\3x = 9}` | feuille d'élève de 6 cm, lignes de cahier, « Copie de Awa » ; texte manuscrit violet foncé, qui n'est ni le noir de l'imprimé ni le bleu de l'élève. Écris les calculs en texte simple : un `$…$` reprend la police mathématique |
| `\Bulle{texte}` | bulle de message à pointe, 4,4 cm de texte au plus ; se pose en `(0,0)` |
| `\Mascotte[x,y]{kiro}{doute}` | personnage de la planche, 14 mm de haut ; refusé tant que la planche n'est pas fournie ou si le personnage ou la pose n'y figure pas |

```latex
\begin{tikzpicture}
\Ticket{Marché de Cocody}{Riz 5 kg/3 500,Huile 1 L/1 200,Sucre 2 kg/1 600}{6 300 F}
\begin{scope}[yshift=-5.1cm]
\Calculatrice{6 300 ÷ 3 =}{2 100}
\end{scope}
\end{tikzpicture}
```

**Physique-chimie** (`kn-pc.tex`) : charge circuitikz (symboles européens), chemfig et mhchem 4. Un kit se déclenche dès qu'une figure contient `to[` (composant de circuit), `\ce`, `\chemfig` ou une macro ci-dessous. Fils et composants sont à 0,6 pt comme `trait`.

| Macro | Effet |
|---|---|
| `\Becher{(x,y)}{0.6}{étiquette}` | bécher posé par son point bas au centre ; niveau de liquide de 0 à 1 ; étiquette facultative (`{}`), placée dessous |
| `\Erlenmeyer`, `\Eprouvette`, `\TubeEssai`, `\Ballon` | mêmes arguments (point, niveau, étiquette) |
| `\Burette{(x,y)}{0.7}{étiquette}` | burette verticale graduée à robinet ; son point bas est la pointe |
| `\Support{(x,y)}{6}` | potence : socle de 2,2 cm au point donné, tige de la hauteur donnée (en cm) |
| `\Force{(point)}{angle}{longueur}{$\vec{P}$}` | vecteur-force `repD`, angle en degrés, longueur en cm, étiquette au bout |
| `\PlanIncline{30}{5}` | plan incliné (angle en bas à gauche, longueur de la pente en cm) |
| `\Solide{(x,y)}{étiquette}` | bloc de 1,1 × 0,7 cm posé par le milieu de sa base ; étiquette vide `{}` si une force part de son centre ; sur une pente, dans `\begin{scope}[rotate=30]` |
| `\draw (0,0) to[battery1,l=$E$] (0,2) to[R,l=$R$] (3,2) to[lamp] (3,0) -- (0,0);` | circuit (composants de circuitikz) |
| `\ce{H2O}`, `\chemfig{H-C(-[2]H)(-[6]H)-O-H}` | formules et schémas de molécules. La flèche `->` de `\ce` est dessinée avec des tirets qui se recouvrent : le contrôle géométrique la signale à tort |

```latex
\begin{tikzpicture}
\Support{(2.2,0)}{6.6}
\draw[trait] (0,5.4) -- (2.2,5.4);
\Burette{(0,2.6)}{0.7}{}
\Becher{(0,0)}{0.4}{\ce{HO-}}
\end{tikzpicture}
```

Circuit :

```latex
\begin{tikzpicture}
\draw (0,0) to[battery1,l=$E$] (0,2) to[R,l=$R$] (3,2) to[lamp] (3,0) -- (0,0);
\end{tikzpicture}
```

Mécanique :

```latex
\begin{tikzpicture}
\PlanIncline{30}{5}
\begin{scope}[rotate=30]
\Solide{(3,0)}{}
\Force{(3,0.7)}{90}{1.3}{$\vec{R}$}
\end{scope}
\Force{(2.42,1.8)}{-90}{1.1}{$\vec{P}$}
\end{tikzpicture}
```

**SVT** (`kn-svt.tex`)

| Macro | Effet |
|---|---|
| `\BrinADN{ATGCCGTA}` | deux brins face à face, le complémentaire calculé (A-T, C-G), une case et une couleur par base, liaisons à 2 ou 3 traits |
| `\BrinADN[simple]{ATGCCGTA}` | un seul brin, cases vides en face à compléter |
| `\Echiquier{A,a}{A,a}` | échiquier de croisement ; gamètes en en-têtes (colonnes puis lignes), génotypes calculés, allèle dominant en premier ; un ou deux gènes (`AB,Ab,aB,ab`) |
| `\Legende{(point)}{(numéro)}{1}` | flèche fine vers le point d'un schéma, terminée par un numéro cerclé |

```latex
\begin{tikzpicture}
\BrinADN[simple]{ATGCCGTA}
\begin{scope}[yshift=-3.6cm]
\Echiquier{A,a}{A,a}
\end{scope}
\end{tikzpicture}
```

Légendes d'un schéma :

```latex
\begin{tikzpicture}
\draw[trait,aplat] (0,0) ellipse (1.5 and 0.9);
\draw[trait,aplat] (0.3,0.1) circle (0.4);
\Legende{(0.3,0.1)}{(2.2,1.2)}{1}
\Legende{(-1.2,-0.3)}{(-2,-1.2)}{2}
\end{tikzpicture}
```

**Histoire-géographie** (`kn-hg.tex`)

| Macro | Effet |
|---|---|
| `\Frise{1789}{1815}{5}` | axe gradué de 9 cm à l'échelle, une graduation tous les 5 ans (choisis une fin multiple du pas, depuis le début) ; à écrire en premier |
| `\Evenement{1789}{texte}` | repère sur l'axe et texte au-dessus ; `\Evenement[1]{…}{…}` éloigne le texte (niveau 1, 2…) pour deux dates voisines |
| `\Periode{1789}{1799}{texte}` | bande colorée sous l'axe, une couleur par appel ; `\Periode[1]{…}{…}{…}` l'empile sous la précédente |

```latex
\begin{tikzpicture}
\Frise{1789}{1819}{5}
\Evenement{1789}{Prise de la Bastille}
\Evenement[1]{1799}{Coup d'État de Bonaparte}
\Evenement{1804}{Sacre de Napoléon}
\Evenement{1815}{Waterloo}
\Periode{1789}{1799}{Révolution}
\Periode{1799}{1815}{Consulat et Empire}
\end{tikzpicture}
```


## Contraintes de rendu et de reprise

- `rework_summary` : résumé concret de ce qui a été corrigé ; apparaît dans l’historique admin après une reprise.
- `response_layout: {"width": "content", "align": "center", "uniform_fields": true}` : largeur naturelle et centrage (défauts), champs homogènes. `width: "full"` impose la largeur de la carte ; `align: "left"` aligne un tableau à gauche.
- Les champs manuscrits suivent une règle commune : empreinte de la réponse et des erreurs prévues, marge horizontale de 4 mm et verticale de 2 mm, arrondi supérieur à 2 mm, minimum 14 × 8 mm. Une fraction reçoit la hauteur de deux lignes. Une ligne de réponse se centre avec son libellé et son unité ; les tableaux restent centrés. Pas de largeur propre à un exercice.
- `figure_constraints: {"min_scale": 1, "min_width_mm": 70, "min_label_pt": 7}` : contraintes optionnelles d’échelle physique effective et de lisibilité ; une disposition latérale ne peut pas les réduire. `min_label_pt` contrôle les textes SVG. Les contraintes impossibles bloquent le rendu. Inutile pour une figure TikZ, déjà à sa taille réelle : ne la renseigne pas.
- `guide_omission_reason` : raison explicite autorisant une carte facile/moyenne sans guide lorsque l’aide révélerait la réponse. Goldfish lit la carte sans connaître cette raison.

### Cellules mixtes et politiques numériques

Une cellule d’un `table_fill` peut être une décision à cocher :

```json
{"type": "text", "value": "Oui", "input": "single_choice", "choices": ["Oui", "Non"]}
```

Les autres cellules restent manuscrites (`input: "text"`, implicite). L’énoncé précise « Complète les calculs puis coche Oui ou Non ». La CV lit les coches ; une double coche ou une lecture ambiguë exige une relecture humaine. L’ordre des cellules et leurs coordonnées sont communs au PDF, à l’aperçu, à l’OCR/CV et à la correction.

Chaque cellule numérique peut déclarer `answer_form`, qui prime sur la forme globale, et `precision: "exact_value"` pour exiger la valeur exacte tout en acceptant des écritures équivalentes. Exemple : tangente `{ "type": "rational", "value": [7,10], "answer_form": "any" }` ; angle arrondi `{ "type": "integer", "value": 35, "precision": "exact_value", "answer_form": "any" }`. La consigne doit préciser la contrainte correspondant à chaque réponse. Une forme décimale expressément évaluée conserve `answer_form: "decimal"`.

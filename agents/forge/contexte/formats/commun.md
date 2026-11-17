# Formats : ce qui vaut pour toute carte

Le contrat d'un emplacement : `familles/<emplacement>.json` que tu écris, `slots/<emplacement>.json` que `check` en tire. `run.py check` vérifie ce contrat et renvoie une erreur courte en cas d'écart. Chaque format a ses règles dans `courants.md` (ou `geo_construction.md` pour un tracé).

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
| `faits` | les faits de la leçon que la carte mobilise : `["F3", "F7"]`. Au moins un, tous dans l'extrait de la leçon du run. `check` refuse un fait inconnu |
| `archetype` | l'archétype donné par la grille (`"ERR"`) |
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

- Jamais de question dans un guide : formule une étape à suivre ou une méthode.
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

## Route de correction

Le script la fixe d'après le format. Tu peux seulement demander le LLM pour une réponse manuscrite courte qui admet beaucoup de formes valables (« Donne un exemple de… ») : `"route": "llm"`, `"route_reason": "..."`, et `llm_brief` devient obligatoire.

## Contraintes de rendu et de reprise

- `rework_summary` : résumé concret de ce qui a été corrigé ; apparaît dans l’historique admin après une reprise.
- `response_layout: {"width": "content", "align": "center", "uniform_fields": true}` : largeur naturelle et centrage (défauts), champs homogènes. `width: "full"` impose la largeur de la carte ; `align: "left"` aligne un tableau à gauche.
- Les champs manuscrits suivent une règle commune : empreinte de la réponse et des erreurs prévues, marge horizontale de 4 mm et verticale de 2 mm, arrondi supérieur à 2 mm, minimum 14 × 8 mm. Une fraction reçoit la hauteur de deux lignes. Une ligne de réponse se centre avec son libellé et son unité ; les tableaux restent centrés. Pas de largeur propre à un exercice.
- `figure_constraints: {"min_scale": 1, "min_width_mm": 70, "min_label_pt": 7}` : contraintes optionnelles d’échelle physique effective et de lisibilité ; une disposition latérale ne peut pas les réduire. `min_label_pt` contrôle les textes SVG. Les contraintes impossibles bloquent le rendu. Inutile pour une figure TikZ, déjà à sa taille réelle : ne la renseigne pas.
- `guide_omission_reason` : raison explicite autorisant une carte facile/moyenne sans guide lorsque l’aide révélerait la réponse. Goldfish lit la carte sans connaître cette raison.

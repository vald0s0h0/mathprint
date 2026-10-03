# Contrat de `astra_output.json`

Référence exacte du fichier qu'Astra écrit dans le dossier du run. `run.py validate`
vérifie tout ce qui est décrit ici et nomme chaque écart.

## Racine

```json
{
  "chapter": "Calcul littéral",
  "grade": "3e",
  "exercises": [ EXERCICE, ... ],
  "skipped": [ {"source_number": "52", "reason": "Exercice tableur sur ordinateur : aucune réponse cochable fidèle"} ]
}
```

## EXERCICE (un par exercice numéroté du manuel)

| Champ | Obligatoire | Contenu |
|---|---|---|
| `source_number` | oui | Numéro imprimé dans le badge (`"39"`) ; énigmes 6e numérotées à part : `"E1"`. Unique dans le fichier. |
| `source_page` | oui | Id de page du payload où l'exercice COMMENCE (`"p075"`). |
| `source_bbox_px` | recommandé | `[x0, y0, x1, y1]` en pixels de cette page : tout l'exercice (extrait montré au professeur à la relecture). |
| `competency_code` | exercices | Code d'une compétence de `payload.competencies` (3e : bandeau rose au-dessus de l'exercice ; 6e : titre numéroté en haut des pages d'exercices). |
| `badge` | non | `exercice` (défaut), `flash` (questions flash), `expert` (mode expert), `probleme`, `enigme`. |
| `calculator` | non | `autorisee` (défaut), `interdite` (calculatrice barrée), `necessaire` (pictogramme calculatrice). |
| `title` | non | Titre d'un problème ou d'une énigme (« Course cycliste »). |
| `source_statement` | recommandé | Transcription FIDÈLE de l'énoncé du manuel (texte, LaTeX `$...$`). |
| `figure` | non | FIGURE commune aux deux niveaux, ou `null`. |
| `variants` | oui | Exercice : `{"base": V, "facile": V}`. Problème/énigme : `{"original": V}` uniquement. |
| `chapter_code` | problèmes | Code du chapitre, ex. `"A2"`. Omettre `competency_code` pour un problème. |
| `difficulty` | problèmes | `1` Facile, `2` Moyen, `3` Difficile. 3e : titre vert/orange/noir ; 6e : ceinture jaune/verte/noire. |
| `difficulty_source` | recommandé | `"manual_title"` ou `"estimated"` si le manuel n’a pas de code exploitable. |

## V — une variante (Base ou Facile)

Question simple :

```json
{"response_type": "qcm_single" | "qcm_multiple" | "checkbox_grid" | "matching",
 "statement": "texte, lignes {{aide}} permises",
 ... champs du format ...,
 "check": CHECK,
 "figure": FIGURE | null,        // facultatif : remplace la figure de l'exercice pour CE niveau
 "solution": "corrigé court pour le professeur (facultatif)"}
```

Exercice à plusieurs questions :

```json
{"response_type": "composite",
 "statement": "contexte commun (données, tableau), lignes {{aide}} permises",
 "questions": [ QUESTION, QUESTION, ... ],     // 2 à 8
 "figure": ..., "solution": ...}
```

Une QUESTION est une question simple avec `figure` facultative, sans `solution`.
La figure propre à une question s’imprime avant cette question ; `{{figure}}` dans
son texte permet de préciser la position. Le contexte commun peut être `""`.
Les énoncés ne contiennent pas de rubriques (« Bilan », « Automatismes », ceintures,
niveaux, préfixes « Problème — » ou « Énigme — ») ni d'invitations à observer répétées. Les guides doivent correspondre à la
tâche de leur portée ; plusieurs guides de sous-questions sont autorisés.
Les marqueurs `{{aide}}` vont à la fin du `statement` concerné. Une aide de question
s’imprime APRÈS sa zone de réponse ; une aide du contexte s’imprime à la FIN de la
carte. Plusieurs aides distinctes sont permises. Ne numérote pas les questions :
le moteur ajoute les lettres. Les figures peuvent donc être réparties entre les
questions, par exemple une seconde pyramide juste avant D.

### Champs par format

| Format | Champs | Bornes |
|---|---|---|
| `qcm_single` | `choices: [str]`, `correct: [i]` | 2 à 8 propositions (viser 3 à 5), exactement 1 juste |
| `qcm_multiple` | `choices: [str]`, `correct: [i, ...]` | 2 à 8, au moins 1 juste, jamais toutes |
| `checkbox_grid` | `cols: [str]`, `rows: [{"label": str, "correct": j}]` | 2 à 4 colonnes, 2 à 10 lignes, une case par ligne |
| `matching` | `left: [str]`, `right: [str]`, `pairs: [[i, j], ...]` | 2 à 6 de chaque côté ; chaque élément de gauche relié exactement une fois ; à droite, un élément sert au plus une fois (un élément en trop = distracteur) |

Propositions, libellés et éléments : ≤ 120 caractères (grille : ≤ 200 ; relier : ≤ 80),
distincts une fois le LaTeX aplati.

### CHECK — vérification recalculée en Python (SymPy ASCII, jamais de LaTeX)

| Cas | Forme |
|---|---|
| valeur de la bonne case | `{"kind": "value", "expr": "expand((7*x+3)*(4*x+5))", "choice": 0}` — `choice` = l'indice juste ; puissance `**` (jamais `^`) |
| vérité de chaque proposition (qcm_multiple) | `{"kind": "set", "exprs": ["isprime(17)", "isprime(21)"]}` — une par proposition, dans l'ordre |
| vérité de chaque ligne (grille à 2 colonnes Vrai/Faux) | `{"kind": "rows", "exprs": ["Eq(Mod(12,3),0)", "2 > 5"]}` |
| non calculable (vocabulaire, lecture, relier) | `{"kind": "none"}` ou champ absent |

Chaque `exprs[i]` est BOOLÉEN (`Eq(6, 7)`, `3 < 5`), jamais un nombre seul. Fonctions
utiles : `gcd`, `lcm`, `isprime`, `Mod`, `expand`, `factor`, `sqrt`, `Rational(3, 4)`,
`mean([..])`, `median([..])`.

## FIGURE

Trois sortes, par ordre de préférence (un tableau n'est JAMAIS une figure : Markdown
dans l'énoncé).

Pour un ensemble de dessins ou d'objets indépendants à traiter, ajoute `items`
à la FIGURE : par exemple `"items": ["Angle 1", "Angle 2", "Angle 3"]` ou
`"items": ["Figure 1", "Figure 2", "Figure 3"]`. Chaque libellé doit apparaître
dans les questions ou les zones de réponse qui utilisent cette figure. Il s'agit
des objets effectivement visibles, pas d'une liste tronquée aux questions retenues.
Le validateur signale les éléments non repris ; la relecture visuelle vérifie
que l'inventaire est complet. Pour un QCM de constructions, toutes les figures
candidates doivent avoir leur choix. Une figure Facile conservée impose de garder
toutes ses réponses, même si leur nombre dépasse l'objectif de simplification.
Un dessin unique partagé n'exige pas un item pour chacun de ses points.

### Découpe du manuel — `crop`

```json
{"kind": "crop", "page": "p075", "bbox_px": [75, 1244, 492, 1378],
 "masks": [[400, 1244, 492, 1262]]}
```

`bbox_px` et chaque masque en pixels de l'image de la page (`width_px` × `height_px`
du payload). Les `masks` sont BLANCHIS avant la découpe : texte résiduel d'un exercice
voisin, numéro, réponse, légende inutile. Les marges blanches sont rognées
automatiquement. Vérifie le PNG produit par `run.py figures`.

### Figure géométrique — `geo`

```json
{"kind": "geo", "spec": {
  "points": {"A": [0, 0], "B": [4, 0], "C": {"xy": [0, 3], "pos": "nw"}, "D": [4, 3],
             "K": {"xy": [2, 0], "label": ""}},
  "polygons": [["A", "B", "C"]],
  "segments": [["C", "K"], {"from": "A", "to": "B", "style": "dashed", "label": "$d$"}],
  "lines": [{"through": ["A", "B"], "label": "$(d)$"}],
  "rays": [{"from": "A", "through": "C"}],
  "circles": [{"center": "A", "radius": 2}, {"center": "B", "through": "C"}],
  "arcs": [{"center": "A", "radius": 1.5, "from_deg": 0, "to_deg": 90}],
  "angles": [{"at": "A", "from": "B", "to": "C", "right": true},
             {"at": "B", "from": "C", "to": "A", "label": "$37^\\circ$", "marks": 1}],
  "lengths": [{"seg": ["A", "B"], "label": "4 cm", "side": "auto"}],
  "equal_marks": [{"segs": [["A", "K"], ["K", "B"]], "ticks": 2}],
  "parallel_marks": [{"segs": [["A", "B"], ["C", "D"]], "arrows": 1}],
  "texts": [{"at": [1, 1], "text": "Figure 1"}],
  "axes": {"x": {"min": -4, "max": 4, "step": 1}, "y": {"min": -3, "max": 3, "step": 1}, "grid": "major", "origin": "O"},
  "functions": [{"expr": "2*x+1", "domain": [-2, 1], "label": "$f$"}],
  "width_mm": 60}}
```

- `points` est obligatoire ; tout le reste désigne les points par leur NOM.
- Point : `[x, y]` ou `{"xy", "label", "pos" (n, s, e, w, ne, nw, se, sw), "hide", "dot"}`.
  `label: ""` = sommet anonyme (ni nom ni point dessiné).
- `side` d'une cote : `auto` (vers l'extérieur), `left`/`right` en allant de A vers B.
- Repère orthonormé automatique ; avec `axes`, la figure se place dans le repère.
- `width_mm` : 25 à 93 (défaut 70). Une figure trop haute est réduite.

### Graphique — `chart`

UNE famille par graphique :

**Repère** (`axes` obligatoire) + tracés superposables :

```json
{"kind": "chart", "spec": {
  "axes": {"x": {"min": 0, "max": 24, "step": 4, "minor": 1, "label": "heure (h)"},
           "y": {"min": 0, "max": 30, "step": 5, "label": "T (°C)"},
           "grid": "major" | "both" | "none", "origin": "O" | "", "arrows": true,
           "break": false, "equal": false},
  "functions": [{"expr": "0.5*x+1", "domain": [0, 6], "label": "$f$", "style": "solid"},
                {"pieces": [{"expr": "x", "domain": [0, 2]}, {"expr": "2", "domain": [2, 5]}], "label": "$g$"}],
  "curves": [{"points": [[0, 12], [4, 10], [8, 15]], "smooth": true, "markers": true, "label": "..."}],
  "points": [{"xy": [2, 2], "label": "A", "reading": true, "pos": "ne"}],
  "scatter": [{"points": [[1, 2], [2, 3]], "label": "..."}],
  "histogram": {"edges": [0, 10, 20, 30], "values": [4, 7, 2]},
  "legend": false, "width_mm": 75}}
```

- `curves` : courbe passant par des points relevés, lissée sans dépasser les points
  (`smooth: false` = segments). Abscisses strictement croissantes.
- `points[].reading: true` : pointillés de lecture graphique vers les deux axes.
  Ne l'utilise JAMAIS sur la valeur que l'élève doit lire.
- `expr` : variable `x`, `+ - * / ^`, `sqrt abs exp log sin cos tan pi`.

**Diagramme en barres / bâtons** (`axes.y` donne l'échelle) :

```json
{"kind": "chart", "spec": {"axes": {"y": {"min": 0, "max": 12, "step": 2, "label": "effectif"}},
  "bars": {"categories": ["Lun", "Mar", "Mer"], "series": [{"values": [4, 7, 10], "label": "3eA"}],
           "style": "bars" | "sticks", "value_labels": false},
  "legend": true}}
```

**Diagramme circulaire** :

```json
{"kind": "chart", "spec": {"pie": {"values": [120, 90, 150], "labels": ["Bus", "Vélo", "À pied"],
  "unit": "angle" | "count", "half": false, "show": "none" | "value" | "percent"}, "width_mm": 55}}
```

(`unit: "angle"` : la somme doit valoir 360°, ou 180° si `half`.)

**Arbre de probabilités** :

```json
{"kind": "chart", "spec": {"tree": {"outcomes": true, "children": [
  {"label": "R", "prob": "$\\frac{2}{5}$", "children": [{"label": "R", "prob": "$\\frac{1}{4}$"}, {"label": "B", "prob": "$\\frac{3}{4}$"}]},
  {"label": "B", "prob": "$\\frac{3}{5}$", "children": [...]}]}}}
```

Toutes les étiquettes : texte + formules `$...$` (liste blanche MathPrint).

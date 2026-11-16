# Format `geo_construction` : géométrie à tracer

Chargé seulement quand la grille du run contient des tracés.

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

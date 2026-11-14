# Musée des défauts

Chaque défaut trouvé sur une carte (par une relecture, un professeur ou une copie)
devient un fichier ici, pour ne se produire qu'une fois. `backend/tests/test_musee.py`
rejoue tous les cas : un cas dont le contrôle ne dit plus rien fait échouer la suite.

Un cas est un fichier JSON :

```json
{"nom": "nom-sur-un-cote",
 "constat": "Le nom M était posé sur le côté [BC] (3e C2.1, relecture du 2026-11-08).",
 "type": "figure",
 "tikz": "\\begin{tikzpicture}…\\end{tikzpicture}",
 "attendu": ["est posé sur un trait"]}
```

- `type` : `figure` (le contrôle géométrique de la figure) ou `melange` (le mélange
  des réponses d'une carte, avec `shape` à la place de `tikz` ; `attendu` vaut alors
  `"inchangé"`).
- `attendu` : un fragment de chaque message que le contrôle doit rendre.

Quand un défaut n'est pas encore détectable par un script, écris quand même le cas,
avec `"attendu": []` et `"a_faire": "…"` : il reste visible dans la liste.

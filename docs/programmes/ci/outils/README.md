# Chaîne de production du référentiel ivoirien

Ces scripts ont produit `../referentiel-ci.json`, les fichiers Markdown du dossier parent et
`backend/app/data/competencies_ci.json`, que l'application importe au démarrage
(`app/services/ci_curriculum.py`). Ils servent à la traçabilité et à une future mise à jour du programme.

On travaille dans un dossier `W` hors du dépôt : PDF copiés dans `W/dl/pNN.pdf` (01 = 6e … 14 = Tle D),
textes bruts dans `W/dl/pNN.txt` (pypdf), extractions dans `W/json/`.

1. `extract.py W/dl/pNN.pdf W/json/pNN.json` : arborescence et tableaux d'habiletés (pdfplumber), un PDF à la fois.
2. `fixsplit.py W` : recolle les contenus coupés en deux, en vérifiant contre le texte brut.
3. `apply.py W` : applique `corrections.py` (formules réécrites en LaTeX, relues contre les PDF)
   et produit `W/json/clean_pNN.json`. Les clés de `corrections.py` sont des positions
   (document, leçon, habileté, contenu) dans l'extraction : elles ne valent que pour ces PDF.
4. `progressions.py <progressions.pdf> W/json/progressions.json` : heures, rang et semaine de chaque leçon.
   Contrôle intégré : rangs continus. Les totaux retombent sur ceux des pages officielles.
5. `build.py W docs/programmes/ci backend/app/data/competencies_ci.json` (depuis la racine du dépôt) :
   codes, ID, durées, ordre annuel, chevauchements, Markdown et JSON.
6. `node katex_check.cjs <frontend> backend/app/data/competencies_ci.json` : toutes les formules
   doivent passer KaTeX.

Un changement de `VERSION` dans `build.py` met à jour les référentiels au démarrage suivant, en place :
les compétences gardent leur identifiant en base (exercices et suivi des élèves conservés).

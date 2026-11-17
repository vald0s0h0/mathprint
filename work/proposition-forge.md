# Proposition de modifications pour Forge

Cette proposition distingue les changements envisagés et les vérifications à effectuer. Aucun des fichiers de code ou de contexte ci-dessous n’a été modifié dans le cadre de cette proposition.

1. **Fiabiliser les reprises de familles et la lecture des variantes.** En reprise ciblée, développer chaque emplacement demandé en toutes ses variantes présentes dans les contrats ; lire chaque image et enregistrer son empreinte et son verdict. La validation ne doit annoncer sa réussite que si toutes les preuves nécessaires à `persist --reprise` existent et correspondent aux rendus actuels. Conserver les vérifications du manifeste, des avis et des statuts avant tout remplacement.

   - Scripts : [`agents/forge/goldfish.py`](../agents/forge/goldfish.py) pour sélectionner et lire les variantes ; [`agents/forge/run.py`](../agents/forge/run.py) pour vérifier la complétude avant d’annoncer la réussite ; [`agents/forge/rework.py`](../agents/forge/rework.py) et [`agents/forge/forge.py`](../agents/forge/forge.py) à examiner pour conserver les mêmes exigences lors de l’enregistrement.
   - Contexte : [`agents/forge/CONSIGNE-EXERCICES.md`](../agents/forge/CONSIGNE-EXERCICES.md), pour expliquer la reprise et ses contrôles.
   - Tests : [`backend/tests/test_forge.py`](../backend/tests/test_forge.py). Couvrir deux jeux et deux dispositions, une variante manquante, une image modifiée après lecture, un avis changé depuis l’export et l’absence d’écriture en cas d’échec. Vérifier aussi la stabilité des empreintes entre deux rendus identiques.

2. **Interdire les questions dans les guides.** Ajouter au contrôle des guides le refus du point d’interrogation et demander une consigne qui indique une étape à suivre. La relecture pédagogique doit aussi repérer les questions indirectes, que ce contrôle simple ne détectera pas.

   - Script : [`agents/forge/forge.py`](../agents/forge/forge.py), fonction `_guide_problems`, pour les guides de cartes et de sous-questions.
   - Contextes : [`agents/forge/contexte/formats/commun.md`](../agents/forge/contexte/formats/commun.md) pour la règle de rédaction ; [`agents/forge/contexte/exercice.md`](../agents/forge/contexte/exercice.md) si la consigne générale doit être rappelée.
   - Tests : [`backend/tests/test_forge.py`](../backend/tests/test_forge.py), avec guides valides et questions dans les différentes positions.

3. **Corriger le symbole de degré.** Rendre un angle numérique et son symbole comme un ensemble visuellement cohérent, sans espace parasite avant ou après le degré, dans les énoncés, les formules et les fiches monospace. Vérifier séparément le comportement de `°C`.

   - Rendu : [`backend/app/services/mathrender.py`](../backend/app/services/mathrender.py) et [`backend/app/services/pdfgen.py`](../backend/app/services/pdfgen.py) ; examiner [`backend/app/services/typography.py`](../backend/app/services/typography.py) pour les règles d’espacement ; [`backend/app/assets/tikz/kn-supports.tex`](../backend/app/assets/tikz/kn-supports.tex) si le rendu des supports l’exige.
   - Contextes : [`agents/forge/contexte/formats/commun.md`](../agents/forge/contexte/formats/commun.md) et [`agents/forge/contexte/figures/commun.md`](../agents/forge/contexte/figures/commun.md), après choix de l’écriture recommandée.
   - Tests : [`backend/tests/test_typography.py`](../backend/tests/test_typography.py) et [`backend/tests/test_forge.py`](../backend/tests/test_forge.py) ; ajouter des rendus de `40° avec`, `15°.`, d’une formule et d’une fiche monospace.

4. **Donner sa place à la première colonne des tableaux.** Calculer la largeur nécessaire au libellé pour conserver une ligne lorsque l’espace le permet, tout en conservant aux cases d’écriture leur largeur utile ; éviter leur étirement automatique sur toute la carte.

   - Rendu : [`backend/app/services/pdfgen.py`](../backend/app/services/pdfgen.py), en particulier `_shortest_label_column` et `_table_geometry`.
   - Contextes : [`agents/forge/contexte/formats/commun.md`](../agents/forge/contexte/formats/commun.md) et [`agents/forge/contexte/formats/courants.md`](../agents/forge/contexte/formats/courants.md), si les règles de largeur ou les options de tableau changent.
   - Tests : [`backend/tests/test_forge.py`](../backend/tests/test_forge.py), avec `AC (en cm)`, des libellés plus longs et plusieurs besoins d’écriture ; vérifier les dimensions et les PNG.

5. **Améliorer les bulles et leur placement.** Éviter la césure d’un mot lorsqu’un retour entre deux mots suffit. Permettre une bulle centrée avant une figure, avec la figure centrée et un espace maîtrisé ; mesurer aussi la pointe de la bulle.

   - Supports et rendu : [`backend/app/assets/tikz/kn-supports.tex`](../backend/app/assets/tikz/kn-supports.tex) pour la bulle ; examiner [`backend/app/services/pdfgen.py`](../backend/app/services/pdfgen.py) si le placement entre énoncé et figure doit devenir une option distincte.
   - Contexte : [`agents/forge/contexte/figures/commun.md`](../agents/forge/contexte/figures/commun.md), pour documenter le support et son placement.
   - Tests : [`backend/tests/test_tikzfig.py`](../backend/tests/test_tikzfig.py), avec textes de différentes longueurs et différentes tailles de figure.

6. **Standardiser les fiches techniques.** Utiliser une police monospace dans chaque cellule de texte, conserver la police mathématique pour les formules, aligner libellés et valeurs et lier les nombres à leurs unités par une espace insécable. Prévoir un espacement compact quand une calculatrice accompagne la fiche.

   - Supports : [`backend/app/assets/tikz/kn-supports.tex`](../backend/app/assets/tikz/kn-supports.tex), pour une macro de fiche réutilisable et sa composition avec une calculatrice ; examiner [`backend/app/services/typography.py`](../backend/app/services/typography.py) pour les unités hors TikZ.
   - Contexte : [`agents/forge/contexte/figures/commun.md`](../agents/forge/contexte/figures/commun.md), pour remplacer les fiches composées librement par le support documenté.
   - Tests : [`backend/tests/test_tikzfig.py`](../backend/tests/test_tikzfig.py) et [`backend/tests/test_typography.py`](../backend/tests/test_typography.py), avec unités, valeurs longues, formules et calculatrice.

7. **Rendre les copies manuscrites et les formules lisibles.** Conserver une écriture manuscrite très lisible, y compris pour les éléments mathématiques lorsque c’est réalisable, et définir explicitement le comportement des fractions et exposants. Vérifier le résultat à la taille d’impression et en noir et blanc.

   - Supports : [`backend/app/assets/tikz/kn-supports.tex`](../backend/app/assets/tikz/kn-supports.tex) et les polices utilisées par ce support ; tout ajout de police dépendra de sa disponibilité et de sa licence.
   - Contexte : [`agents/forge/contexte/figures/commun.md`](../agents/forge/contexte/figures/commun.md), pour les consignes de composition des copies.
   - Tests : [`backend/tests/test_tikzfig.py`](../backend/tests/test_tikzfig.py), avec texte, fractions, exposants et symboles de degré.

8. **Rendre visible le traitement séparé des situations.** Lors de la préparation d’une leçon entière, signaler explicitement les compétences « Traiter » sans famille et afficher la commande de problèmes correspondante. Ne pas lancer automatiquement un run de problèmes sans choix explicite du volume.

   - Scripts : [`agents/forge/run.py`](../agents/forge/run.py) pour le message ; [`agents/forge/forge.py`](../agents/forge/forge.py) et [`agents/forge/plan.py`](../agents/forge/plan.py) à examiner pour exposer les compétences sautées sans modifier la règle pédagogique.
   - Contexte : [`agents/forge/CONSIGNE-EXERCICES.md`](../agents/forge/CONSIGNE-EXERCICES.md), pour documenter le message et la procédure.
   - Tests : [`backend/tests/test_forge.py`](../backend/tests/test_forge.py) et [`backend/tests/test_forge_plan.py`](../backend/tests/test_forge_plan.py), pour une compétence isolée et une leçon comprenant plusieurs types de compétences.

Les exemples visuels proposés complètent les tests de structure et la relecture pédagogique. Les fichiers cités pour examen ne nécessiteront pas forcément tous une modification : ce sera à confirmer pendant l’implémentation.

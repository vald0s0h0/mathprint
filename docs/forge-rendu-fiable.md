# Proposition pour les prochains runs Forge

Cette proposition distingue le dimensionnement déjà présent dans le code des protections à ajouter. Elle ne modifie pas le moteur pendant la reprise des cartes. Les exercices acceptés restent hors de la sélection.

État vérifié le 8 novembre 2026 : six reprises enregistrées en brouillons, six relectures Terra complètes sans signalement, aucun des 246 autres exercices du domaine C modifié. Compteurs C : zéro dislike, zéro exercice à surveiller. Le correctif de l'outil Forge décrit ci-dessous est appliqué ; les autres évolutions de cette proposition restent à implémenter. Validation : 202 tests ciblés passent.

## 1. Conserver une seule règle pour les champs manuscrits

`backend/app/services/handwriting.py::field_size` est déjà la source commune : mesurer l'écriture de la réponse attendue et des erreurs prévues ; ajouter 4 mm horizontalement et 2 mm verticalement à cette empreinte (qui comprend déjà les marges de base) ; arrondir au multiple supérieur de 2 mm ; appliquer le minimum 14 × 8 mm. Une fraction utilise deux étages. La police imprimée ne détermine jamais la place laissée à la main.

`pdfgen` utilise cette fonction pour les réponses courtes, les égalités à trous, les tableaux et les parties d'un composite. Le groupe « libellé + champ + unité » est centré, ainsi que le tableau. Aucun identifiant de carte, de run ou de compétence n'intervient. La carte C1.1/s20 sert de référence visuelle, pas d'exception codée en dur.

Pour rendre cette implémentation durable :

1. Nommer les constantes de confort dans `handwriting.py` : `FIELD_MIN_W_MM=14`, `FIELD_MIN_H_MM=8`, `FIELD_EXTRA_X_MM=4`, `FIELD_EXTRA_Y_MM=2`, `FIELD_STEP_MM=2`. Garder une fonction pure, sans accès au modèle ni à la base.
2. Centraliser la collecte des valeurs d'un champ dans un helper partagé : réponse attendue et erreurs affectées à cette cellule seulement ; exclure les cellules données et les cases à cocher. Pour un groupe homogène, prendre le maximum des besoins de ses champs. Le tableau peut grandir pour ses en-têtes, jamais rétrécir sous le besoin manuscrit.
3. Éliminer progressivement les règles héritées `MINI`, `RIGHT` et les suppléments de hauteur fixes sur les chemins qui disposent d'une réponse typée ; réserver leur compatibilité aux anciens contenus sans réponse exploitable. Actualiser les commentaires qui parlent encore de 20 × 8 mm ou d'un supplément universel de 3 mm.
4. Si la place manque, essayer un retour de ligne entre groupes complets ou une autre disposition du tableau ; sinon bloquer le rendu. Ne pas diminuer la case ni la police pour faire entrer artificiellement la carte.
5. Faire consommer les mêmes rectangles calculés par l'aperçu, le PDF, la simulation manuscrite et l'OCR/CV. Aucune seconde formule de largeur dans ces consommateurs.

Tests : nombres négatifs, décimaux français, grands nombres, fraction, expression, erreur prévue plus longue que la réponse, cellule donnée très longue et cellule à cocher. Vérifier les dimensions physiques et l'absence de chevauchement, la conservation des coordonnées de correction après mélange et le centrage du groupe complet. Tester aussi l'invariance des dimensions quand la police imprimée change. Conserver les tests existants de la référence à deux bandes et des composites.

## 2. Séparer les couches des figures de solides

Problème observé : dans un SVG libre, un aplat de section ajouté tard peut effacer une arête déjà dessinée. L'opacité ne constitue pas une garantie suffisante avec le moteur de rendu utilisé. Des arêtes supposées cachées ne sont pas toujours déclarées comme telles.

Extension proposée dans le contrat de figure SVG : des couches nommées optionnelles `section_fills`, `hidden_edges`, `visible_edges`, `section_outlines`, `dimensions`, `labels`. Chaque couche contient uniquement les primitives SVG déjà autorisées. Le moteur assemble ces couches dans cet ordre, au lieu de laisser l'auteur choisir arbitrairement l'ordre du XML. Les sections utilisent un gris clair explicite ; leurs contours sont séparés des aplats. Les arêtes visibles sont toujours redessinées après les aplats. Les arêtes cachées portent un style de tirets centralisé, rendu en segments réels par `_expand_dashes`.

Cette extension reste petite : elle n'exige pas de moteur 3D ni de déduire automatiquement quelles arêtes sont cachées. L'auteur doit déclarer la visibilité. Pour les solides courants, étendre les gabarits existants avant de créer un nouveau SVG libre : les gabarits portent alors cette déclaration une seule fois. Les SVG historiques restent lisibles ; les nouvelles figures de solides avec section suivent le contrat structuré.

Implémentation : `svgfig.py` normalise les couches vers le SVG restreint existant ; `forge.check` valide les noms, primitives et types ; le schéma et la charte expliquent les conventions. Ne pas réordonner automatiquement un ancien SVG arbitraire : son intention n'est pas déductible de sa couleur.

Tests : une section remplie ne supprime pas l'arête avant dans le PNG réel ; une arête cachée contient effectivement des intervalles blancs après passage par MuPDF ; les sections de cylindre, cône, pyramide et pavé restent lisibles en niveaux de gris. Les tests géométriques/visuels ciblent les primitives et des figures synthétiques, sans dépendre de fichiers de run ignorés par Git.

## 3. Rattacher les cotes à la géométrie et protéger les étiquettes

Une cote n'est pas seulement un texte placé près d'un segment. Pour les gabarits et figures structurées, déclarer `segment`, `text` et un décalage physique en mm. Exemple : `{"segment":["A","B"],"text":"6 cm","offset_mm":3}`. Le moteur dessine la cote ou son rappel au segment concerné. Un libellé de hauteur ne se place pas sur une génératrice oblique. Pour un SVG exceptionnel, écrire explicitement « AB = 6 cm » si l'association visuelle reste douteuse.

Mesurer les boîtes des étiquettes à leur taille physique finale, après toutes les transformations et le compactage. Le contrôle actuel `min_label_pt` ne suffit pas pour les tailles héritées d'un groupe ou les transformations SVG : normaliser ces propriétés dans le même pipeline que le rendu. Refuser une taille minimale impossible plutôt que compacter au-dessous. Un seuil initial de 7 pt peut être repris des contraintes existantes, puis calibré sur papier.

Pour les nouveaux libellés structurés, réserver leur rectangle aux textes : décaler la cote ou le libellé en cas d'intersection avec un trait non associé, sans masquer une arête avec un rectangle blanc. Une collision non résolue devient un signalement explicite dans le rapport de rendu. Le contrôle ne doit pas prétendre comprendre automatiquement toutes les intersections d'un SVG libre.

Tests : hauteur du silo non traversée, dimension du pavé rattachée à AB sans ambiguïté avec IB, taille minimale conservée après disposition latérale, label hérité d'un groupe et groupe transformé. La relecture Goldfish du PNG reste nécessaire pour les ambiguïtés de lecture.

## 4. Espacer les propositions à relier selon leur hauteur réelle

`pdfgen._matching_column_ys` emploie actuellement `max(6.5 mm, hauteur + 2 mm)`. Pour des propositions de plusieurs lignes, les 2 mm de séparation restent trop faibles visuellement.

Proposition simple : conserver 2 mm pour une seule ligne ; passer à 3 mm dès que l'un des deux blocs voisins a plusieurs lignes. Calculer l'écart entre les bords réels des blocs, puis les centres des pastilles, plutôt qu'un espacement fondé seulement sur leur nombre. Pour des colonnes de longueurs différentes, centrer les piles après ce calcul. Mesure, dessin, mélange des liens et coordonnées CV doivent utiliser la même géométrie. Si la carte grandit, accepter cette hauteur au lieu de réduire la police.

Tests : deux blocs de trois lignes successifs gardent au moins 3 mm entre leurs bords, y compris après permutation ; les libellés d'une ligne conservent leur compacité ; aucun texte n'entre dans le couloir des traits ; les coordonnées des pastilles correspondent au PDF.

## 5. Unifier l'impression des nombres donnés

Le tableau C4.3/s20 mélangeait des entiers imprimés comme texte ordinaire et un décimal donné sous forme LaTeX (`$0{,}6$`). Deux chemins typographiques peuvent produire une différence visuelle malgré une taille nominale identique.

Proposition : faire de `_cell_display_text` l'unique formatteur des cellules données numériques, avec une convention commune et une virgule décimale française. Les nouveaux contenus déclarent les décimaux comme nombres typés, sans détour par `type: text`. Garder le type texte pour un véritable texte ou une formule. Le choix typographique doit être le même pour `2`, `0,6` et `4` ; une fraction conserve sa composition à deux étages.

Tests : même corps et même hauteur de chiffres pour les entiers et décimaux donnés, virgule française, absence de modification des valeurs attendues et de la correction. Vérifier dans le rendu réel, pas seulement dans la chaîne formatée.

## 6. Contrôler le rendu final avant toute écriture en banque

Conserver les protections déjà présentes : sélection issue des dislikes actuels, manifeste de version, refus d'écraser un like récent, historique durable, preuve du dernier rendu et Goldfish complet avec empreinte de l'image. Ajouter les métriques ci-dessus au rapport de rendu et à la preuve : dimensions des champs, séparation des libellés, tailles physiques et collisions non résolues.

Une contrainte physique certaine non satisfaite bloque `persist`. Une ambiguïté sémantique reste à Goldfish ou à l'humain. Ne pas transformer un contrôle heuristique en verdict de correction certain. Si une reprise échoue, conserver le dislike, la raison d'échec et le compteur correspondant.

Correctif appliqué à l'outil Forge durant cette reprise : `persist --slots` limite les refus de mise en page (`writing`, `leading`, `traces`) aux cartes sélectionnées. Sans `--slots`, le contrôle porte toujours sur toutes les cartes. Les preuves de rendu, Goldfish et manifeste restent exigées pour chaque carte remplacée. Trois tests de régression vérifient qu'un défaut sélectionné bloque, qu'un défaut hors sélection ne bloque pas la reprise et que la carte hors sélection reste strictement inchangée. Ce correctif a permis de reprendre C4.2/s02 et s18 sans modifier C4.2/s12, qui portait une ancienne alerte de mise en page.

Ordre de livraison proposé : (1) consolider la fonction de dimensionnement et les chemins de mise en page ; (2) uniformiser les nombres donnés et espacer les associations ; (3) ajouter les couches SVG et les cotes structurées aux gabarits ; (4) brancher les contrôles de rendu et la preuve ; (5) valider sur les six défauts de cette reprise et quelques autres compétences, sans remplacer les exercices déjà likés.

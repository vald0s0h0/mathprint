# Mise en page des sujets (v3)

Le sujet imprimé fait entrer le maximum d'exercices sur la page, sans toucher au
contenu des cartes : **ni la largeur du texte, ni le corps de police, ni les coupes
de lignes** ne changent (les cartes de la banque sont vérifiées une à une). Seuls
les blancs verticaux se resserrent.

## Géométrie

- **Marges de page** : 9 mm (fiduciels de coin inclus). Largeur des cartes inchangée (93,5 mm).
- **4 fiduciels AprilTag** de 11 mm aux coins (TL 0, BL 1, BR 2, **TR 3**). Les copies imprimées
  avant la v3 n'ont que TL/BL/BR : la vision s'en accommode (recalage affine).
- **Bande haute de 11 mm**, la hauteur d'un fiduciel, sur toutes les pages. Page 1 : case de
  note vide en pointillés (contrôle), nom, titre, `professeur · matière · établissement`
  (professeur accompagné seulement, sans la ville), classe en grand. Pages suivantes : ligne de
  rappel `élève · classe · titre — page n`. Les deux colonnes partent **du même niveau**.
- **Code de page** : DataMatrix 8×32 (8 modules sur 8,25 mm, la hauteur du carré noir d'un
  fiduciel), en bas à **gauche** contre le fiduciel BL — loin de la manche d'un élève droitier.
- **Date** : en bas à droite, contre le fiduciel BR.
- **Plage pipette** : une **grille** de traits verticaux et horizontaux (22 × 8,25 mm, pas 1,5 mm,
  épaisseur 0,9 pt) de la couleur de masquage, imprimée en bas de chaque page avec le même toner et la même
  épaisseur que les cadres de réponse (cf. ci-dessous). Un aplat n'est pas rendu comme un trait.
- **Espacements verticaux** (`CARD_PAD_T/M/B`, `GAP`, bande de correction, encadrés d'aide) resserrés.

## Code de page

20 chiffres : identifiant de page à **12 chiffres aléatoires** + clé de contrôle de **8 chiffres**
(HMAC tronqué, `services.security`). La vérification est sans état : l'identifiant de page *est*
les 12 premiers chiffres. Les anciens payloads `MP1|…` et `M2:…` (QR) restent lisibles.
L'encodeur (`services.datamatrix`, sans dépendance) a été comparé module par module à un encodeur
de référence ; la lecture utilise `zxing-cpp`.

## Overlay de correction

L'en-tête de correction suit la mise en page **imprimée** de chaque page (`pdfgen.layout_of_page` :
identifiant à 12 chiffres = v3, sinon v1) :

- note en gros et en gras dans la case, barème (`/20`, `/10`, `/5`) deux fois plus petit, centré ;
- `Correction — Nom` sur la ligne du nom, et dessous `total brut → note` (`15 / 30 ⟶ 10 / 20`) ;
- **plus d'appréciation imprimée** (ni case en tête, ni barres de progrès) : seules les copies déjà
  imprimées en v1 gardent la leur ;
- les formules LaTeX des micro-corrections sont recolorées comme le texte (`_tinted`).

## Pipette de la couleur de masquage

À chaque page lue (`worker_cv.analyze_page`, copies à code DataMatrix), `calibrate_dropout` règle le masque
sur la grille, **avant** le blanchiment du fond : elle repère les traits (écart au papier), mesure leur teinte,
puis élargit par paliers la tolérance de teinte (6 → 22), abaisse la saturation minimale et épaissit le masque
(0 → 2 px) sur les seuls pixels aussi clairs que la grille (le crayon à papier, gris, est préservé) jusqu'à ce que la grille soit **effacée** (résidu ≤ 1 %). Les cadres de
réponse, de même nature, disparaissent alors aussi. `dropout_filter` applique la plage nominale **et** ce réglage.
Grille illisible (absente, tache, papier blanc) : filtre nominal seul, avertissement `dropout_swatch_unreadable` ;
grille non effaçable dans les limites (résidu > 10 %) : avertissement `dropout_grid_not_erased`.
Les anciennes copies à aplat restent calibrables (l'aplat est un cas particulier de trait).

## Points à valider sur papier

Pas d'essai d'impression à la livraison (déploiement en bêta) : relever le taux de lecture du
DataMatrix et des 4 fiduciels sur les premiers scans réels, et les marges d'impression.

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
- appréciation (barres de progrès, synthèse) dans la bande du bas, entre le code et la date ;
- les formules LaTeX des micro-corrections sont recolorées comme le texte (`_tinted`).

## Points à valider sur papier

Pas d'essai d'impression à la livraison (déploiement en bêta) : relever le taux de lecture du
DataMatrix et des 4 fiduciels sur les premiers scans réels, et les marges d'impression.

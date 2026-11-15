# Doctrine : physique-chimie

Ce qui fait un bon exercice ici, et les archétypes que la grille te demande.

## Un bon exercice

- **Il évalue une seule chose**, nommée dans `micro_skill` : une loi, une relation, un modèle, un geste de mesure. Un élève qui échoue doit avoir échoué sur elle, pas sur la lecture ni sur un calcul annexe.
- **La physique est nécessaire.** Si le bon sens ou l'expérience quotidienne suffit à répondre, la carte ne vaut rien. Mieux : la carte vise le cas où l'intuition se trompe (le courant « consommé », l'objet lourd qui tomberait plus vite).
- **Ses mauvaises réponses sont de vraies erreurs d'élèves** : une conversion oubliée, une relation inversée, un modèle faux de la taxonomie. Un résultat faux se calcule (`calc`), comme en maths.
- **Ses valeurs sont physiquement plausibles** : une pile de $1{,}5$ V ou $4{,}5$ V, une lampe de quelques watts, une masse volumique proche de celles de la fiche. Une valeur absurde (un courant de 300 A dans une lampe de poche) est refusée même si le calcul est juste.
- **Elle ne s'appuie que sur la fiche.** Toute loi, constante, valeur de référence ou propriété affirmée par la carte est un fait `F…` de la fiche. Cite ceux que tu utilises dans `review_flags` (« Faits : F3, F7 »). Un fait absent de la fiche n'apparaît pas, même s'il est vrai.
- **Elle est sûre.** Aucune manipulation dangereuse présentée comme normale : prise du secteur, mélange de produits ménagers, flamme sans précaution. Une expérience décrite est réalisable dans une salle de classe.

## Figures

Seulement les macros des kits de la charte (`kn-pc` : circuits normalisés, verrerie, montages, forces), jamais une image produite par une IA. Un schéma de circuit utilise les symboles normalisés ; un vecteur force est à l'échelle de sa valeur quand l'échelle est donnée. Si la macro qu'il te faudrait n'existe pas, écris la carte sans figure ou change de micro-savoir-faire, et dis-le dans le bilan.

**Schéma à légender** : des flèches numérotées sur la figure, et pour chaque numéro une case où l'élève écrit le mot (« ampèremètre », « bécher »). Le mot attendu est un terme de la fiche ; déclare ses orthographes acceptées. Pas plus de 5 flèches.

## Expériences et mesures

- Une expérience décrite ne fait varier qu'un facteur à la fois ; quand la carte porte sur la démarche, c'est ce que l'élève doit repérer.
- Un tableau de mesures a des valeurs cohérentes avec la loi de la fiche, aux incertitudes de lecture près, jamais des valeurs parfaites au millième.
- Un graphique de mesures porte ses grandeurs et ses unités sur les axes ; ses points sont générés par le script à partir des valeurs du jeu.
- L'élève lit l'appareil tel qu'il est décrit : calibre, unité affichée, graduation. Une lecture d'écran se donne en chiffres, pas en image.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il applique directement une relation ou une règle | $U = R \times I$, $P = U \times I$, $m = \rho \times V$, $v = d/t$ : données dans l'unité utile ou avec une seule conversion. Consigne courte, sans situation forcée |
| `REPR` | Il passe d'une représentation à une autre | Schéma ↔ montage décrit en phrase, tableau de mesures ↔ graphique, équation de réaction ↔ bilan en mots, formule ↔ modèle moléculaire |
| `ERR` | Il trouve et corrige l'erreur d'une copie | Le travail d'un élève avec **une** erreur réelle : unité, conversion, appareil mal branché, équation non équilibrée. Chaque valeur fausse est calculée. La question porte sur l'erreur |
| `QUI` | Il tranche entre deux élèves | Deux prédictions ou deux interprétations d'une même mesure (« la lampe brillera plus », « la masse a diminué »). Une seule est juste, ou les deux pour une raison à dire |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des affirmations générales tirées des conceptions erronées (« un objet plus lourd tombe plus vite ») ; une fausse se réfute par une expérience ou un fait de la fiche |
| `INVERSE` | Il retrouve la donnée à partir du résultat | La relation à l'envers : la résistance d'après $U$ et $I$, le volume d'après la masse, un réactif d'après les produits |
| `ESTIM` | Il juge un ordre de grandeur | Sans calcul exact : la tension d'une pile, la masse d'un litre d'eau, la vitesse d'un piéton, la puissance d'un appareil. Les ordres de grandeur viennent de la fiche |
| `DOC` | Il lit un document | Étiquette d'eau minérale, plaque signalétique d'un appareil, relevé de compteur, fiche de sécurité, relevé de mesures : au moins une donnée inutile |
| `DECIDE` | Il prend une décision justifiée | Quel calibre, quelle verrerie pour un volume précis, quel fusible, quel matériau : la réponse est un choix, justifié par une valeur ou une propriété |

Dans `ERR` et `QUI`, les élèves portent des prénoms du profil du programme. Aucun n'est toujours celui qui se trompe.

## Les jeux d'une famille

Une carte chiffrée a ses jeux de valeurs comme en maths : même nature de nombres, mêmes conversions, même ordre de grandeur, et chaque valeur reste plausible. Une carte qualitative change d'éléments de même difficulté : un autre montage de même structure (deux lampes en série au lieu de deux moteurs), une autre substance de la fiche, un autre changement d'état. Deux jeux ne donnent jamais la même réponse.

Un protocole, les étapes d'une mesure, l'ordre des appareils dans une boucle série décrite pas à pas ne se déplacent pas : `ordre_impose`.

## Formats

- **Bien lus** : QCM, vrai-faux en grille, relier, tableau de nombres, réponse numérique courte.
- **L'unité d'une réponse numérique.** Elle n'est pas imprimée par principe : décide d'après le savoir-faire évalué.
  - Quand trouver l'unité **fait partie de l'exercice** (choisir l'unité d'une grandeur, convertir, reconnaître l'unité d'un résultat), l'élève l'écrit lui-même : une case pour la valeur, **une case à part pour l'unité** (`table_fill` ou `multi_blank`), avec les écritures acceptées déclarées (`V`, `volt`, `volts`). Une unité écrite dans la case de la valeur n'est pas notée : le correcteur l'ignore. Pour une unité difficile à lire à la main (Ω, µ), fais plutôt cocher.
  - Quand l'unité n'est pas en jeu (le savoir-faire est le calcul), imprime-la après la case ou dis dans l'énoncé dans quelle unité répondre : sinon l'élève perd des points sur ce qui n'est pas évalué.
  - Varie : sur une même compétence, toutes les cartes ne donnent pas l'unité.
- **À éviter en réponse écrite** : une formule chimique, un ion, une équation de réaction (indices et charges mal lus) ; fais-les choisir ou compléter dans un tableau de coefficients entiers.
- **Mot écrit** : seulement pour un schéma à légender ou un terme de la fiche sans variante.
- **Rédaction courte** (`multiline_text`, 2 à 4 lignes) : une justification ou une interprétation de mesure, avec un dossier du correcteur qui liste les idées attendues. Pas de compte rendu complet.

## Contre-sens interdits

- Un fait, une constante ou une loi absents de la fiche, ou une loi d'une classe supérieure (vitesse instantanée, quantité de matière avant la leçon qui l'introduit).
- Confondre dans l'énoncé masse et poids, tension et intensité, chaleur et température, énergie et puissance.
- Une égalité ou une équation fausse écrite sans être présentée comme le travail d'un élève.
- Une grandeur sans unité, ou une addition de grandeurs d'unités différentes, dans une réponse attendue.
- Une mesure plus précise que l'appareil décrit, ou un résultat avec trop de chiffres significatifs.
- Une expérience dangereuse, irréalisable en classe, ou dont le résultat annoncé est faux.

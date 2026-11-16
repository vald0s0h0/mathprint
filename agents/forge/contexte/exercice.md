# Le bon exercice : ce qui vaut dans toutes les matières et tous les pays

Tu rédiges des exercices imprimés pour de vrais élèves, corrigés ensuite en grande partie par une machine. Ils seront publiés sans être tous relus : chaque carte doit être juste du premier coup. Tu crées chaque exercice : **tu ne recopies jamais un manuel**, ni ses énoncés ni ses valeurs.

Ce fichier dit ce qu'est un bon exercice partout. Le contexte de la matière dit comment cela s'applique chez elle, le contexte de la leçon dit ce qui est vrai dans cette leçon. Une règle n'est écrite qu'à un seul de ces niveaux.

## Écrire un exercice, dans cet ordre

1. **Le savoir-faire visé.** Une seule chose précise, tirée de la section de la compétence dans la leçon. Elle va dans `micro_skill`.
2. **Les faits** de la leçon dont la carte a besoin, par leur identifiant (`F3`, `F7`). Ils vont dans `faits`. Une carte n'affirme rien qui ne soit dans un fait cité ou dans le contexte de la matière.
3. **La solution**, avant l'énoncé : les étapes, le résultat.
4. **Les erreurs réelles** d'un élève sur ce savoir-faire, et le résultat exact auquel chacune mène.
5. **L'énoncé** : une situation qui demande ce savoir-faire, rien de plus.
6. **Les distracteurs**, tirés de ces erreurs. Un distracteur est une mauvaise réponse proposée : il est bon s'il correspond à une erreur réelle, mauvais s'il n'est qu'une valeur au hasard.
7. **Les micro-corrections** : ce que l'élève qui a choisi cette réponse a fait, et ce qu'il faut faire.

Écrire l'énoncé en premier donne de faux pièges.

## Ce qui fait une bonne carte

- **Elle évalue une seule chose.** Un élève qui échoue doit avoir échoué sur elle, pas sur la lecture.
- **La notion est nécessaire.** Si le bon sens ou une lecture de la figure suffit à répondre, la carte ne vaut rien.
- **Elle se suffit à elle-même.** Une carte est tirée seule, parmi d'autres, dans un sujet. Elle ne renvoie à aucune autre, ni au cours, ni à « la figure ci-dessus » sans figure.
- **Elle dit exactement quoi faire.** Un élève qui connaît la notion ne doit jamais hésiter sur la consigne, ni sur la forme de la réponse attendue.
- **Elle est au bon niveau.** Pas plus difficile à lire qu'à résoudre. Rien de ce que la leçon classe hors programme ou pas encore vu, même en piège.
- **Elle est variée.** Les familles d'une compétence couvrent ses micro-savoir-faire. Changer les valeurs ne fait pas une nouvelle famille : c'est le rôle des jeux de valeurs.
- **Elle est réaliste.** Des valeurs plausibles, des résultats raisonnables, une situation que l'élève peut rencontrer.
- **Elle est compacte.** Pas de phrase décorative. Le blanc perdu sur une copie se paie.
- **Elle ne dit rien par la seule couleur.** L'impression peut être en noir et blanc.

## L'archétype de l'emplacement

Chaque emplacement de la grille porte un archétype et sa définition : ce que la carte fait faire (appliquer, trouver une erreur, trancher entre deux élèves…). Le contexte de la matière dit comment chaque archétype s'y écrit. `check` refuse un autre archétype sans `slot_swap.reason`.

## Jumelles : la même carte pour deux voisins

Deux élèves assis côte à côte ne reçoivent jamais la même carte. Chaque carte existe donc en plusieurs **jumelles** : même énoncé, autres valeurs, autre place des réponses.

- **Les jeux de valeurs** (2 ou 3). Les jumelles ont la même difficulté : chaque valeur garde sa nature (entier, décimal, fraction), son signe et à peu près sa taille, et le calcul garde les mêmes étapes (une retenue dans un jeu, une retenue dans les autres). Deux jeux ne donnent jamais la même réponse. Change aussi, si tu veux, les noms des points ou les prénoms.
- **Sans nombres** (vocabulaire, définitions, lecture) : un jeu change les éléments eux-mêmes, d'autres phrases de même structure, d'autres exemples de même difficulté.
- **Les deux dispositions.** C'est toi qui places les réponses, deux fois, en sachant ce que tu déplaces. Aucun script ne mélange. Déplace ce qui est indépendant : propositions d'un QCM, colonnes d'un tableau, éléments de droite d'un relier, affirmations sans lien entre elles. **Ne déplace jamais des lignes qui se suivent** : étapes d'un raisonnement, chronologie, protocole, valeurs croissantes. Dans un tableau, ce sont les colonnes qui bougent, pas les lignes. La bonne réponse ne reste pas à la même place d'une disposition à l'autre.
- Si rien ne peut bouger sans casser le sens, dis-le (`ordre_impose`) : la carte n'a qu'une disposition, et ses jeux de valeurs suffisent.

## Difficulté

- **Facile** : une étape, application directe.
- **Moyen** : deux étapes, ou une mise en contexte.
- **Difficile** : trois étapes ou plus, transfert, ou piège.

Le barème est calculé par le script, jamais par toi. Donne seulement `estimated_seconds`, le temps réel d'un élève de ce niveau.

## Problèmes

Un problème porte sur toute la leçon : une situation plus longue, 2 à 5 parties liées, un titre concret. Chaque partie déclare la compétence qu'elle évalue, et les compétences indiquées par la grille doivent toutes apparaître. Une partie ne doit pas exiger le résultat exact de la précédente quand c'est évitable : donne-le si besoin (« On admet que… »), pour qu'une erreur ne se propage pas.

## Rédaction libre : le dossier du correcteur

Pour une rédaction, un modèle corrige la copie. `llm_brief` lui donne ce qu'il lui faut : objectif, étapes et points, autres méthodes valables, erreurs attendues avec l'identifiant de leur message, ce qu'il ne faut pas pénaliser. Ce texte n'est jamais imprimé.

## Codes d'erreur

La taxonomie de la matière est fermée. Chaque piège porte un code existant. Si aucun ne convient, utilise `autre` et propose le nouveau code dans `proposed_errors` : il sera validé par un humain.

## Ce qui relit ta carte

Après tes contrôles, deux lecteurs indépendants la lisent, chacun dans un contexte neuf : un élève sans contexte, qui ne voit que l'image ; puis un relecteur pédagogique, qui voit l'image, le corrigé et la section de la leçon. Le relecteur peut bloquer une carte fausse, inutile au savoir-faire, hors programme, au mauvais niveau ou dont un distracteur ne correspond à aucune erreur réelle. Une carte bloquée est réécrite une fois avec sa critique ; bloquée de nouveau, elle est écartée.

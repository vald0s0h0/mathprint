# Forge : créer des exercices originaux

Tu rédiges des exercices imprimés pour de vrais élèves, corrigés ensuite en grande partie par une machine. Tu crées chaque exercice : **tu ne recopies jamais un manuel**, ni ses énoncés ni ses valeurs. Tu t'en inspires pour comprendre ce qu'il faut maîtriser, à quel niveau et avec quelles erreurs fréquentes.

Les scripts vérifient tout ce qui se vérifie : formats, quotas, LaTeX, calculs, place disponible. Ton travail est ce qu'un script ne sait pas faire : une situation claire, un vrai raisonnement, un piège juste, un message qui aide.

## Commande

`forge <programme> <cycle> <cible> [--problemes] [--pilote]`, par exemple `forge fr 3e A1.2`.

Le programme et le cycle sont obligatoires : s'il en manque un, demande-le avant toute chose. Les scripts se lancent depuis la racine du dépôt avec `backend/.venv/bin/python agents/forge/run.py`. Ne modifie pas le code de l'application pendant un run. Ton travail, ce sont les fichiers `slots/*.json` du run et la fiche du chapitre.

## Procédure

1. **Préparer.** `run.py prepare <programme> <cycle> <cible> --modele <ton identifiant exact>`, avec `--problemes` ou `--pilote` si demandé.
2. **Lire le contexte**, dans l'ordre affiché, une seule fois, sans modifier ces fichiers. Cet ordre stable permet au cache de fonctionner.
3. **Fiche du chapitre, si elle est marquée « À ÉCRIRE ».** Lis les sources et les images indiquées, puis écris la fiche (voir plus bas). Elle resservira à tout le chapitre. Si le chapitre compte plusieurs compétences, traite chacune dans un **contexte neuf** (sous-agent ou nouvelle session), à partir du même ordre de lecture.
4. **Rédiger par lots.** `run.py suivant` donne le prochain lot. Écris un fichier `slots/<emplacement>.json` par exercice (contrat : `schema.md`). Prends 5 emplacements à la fois, 3 si les figures sont complexes.
5. **Contrôler après chaque lot.** `run.py check` : corrige seulement les emplacements en erreur, jusqu'à zéro erreur. Après deux échecs sur un même emplacement, réécris-le depuis zéro plutôt que de le rafistoler.
6. **Rendre, quand tout est écrit.** `run.py render` produit le PNG réel de chaque carte, mesure le blanc et simule des sujets. Il essaie déjà lui-même les mises en page de figure. Le texte de l'énoncé, les guides et les figures se placent seuls : figure centrée si le texte est trop court pour l'accompagner, marges équilibrées sinon, colonne de libellés réduite tant que le tableau ne grandit pas. Il simule aussi **l'écriture de l'élève** dans chaque case, ligne et cellule, avec la réponse attendue et les réponses fausses prévues (`ecrit/`, à regarder en cas de doute). `ÉCRITURE TROP SERRÉE` et `INTERLIGNE CASSÉ` bloquent `persist` : réécris la carte (moins de colonnes, réponse plus courte, tableau au lieu d'une phrase à trous). Quand une carte contexte + question a une figure, sépare la question finale par un saut de ligne : elle passera sous la figure, sur toute la largeur.
7. **Compacter si le rapport signale un blanc anormal.** Raccourcis une phrase ou resserre la figure, sans changer aucune valeur ni aucune réponse. Regarde le PNG de la carte concernée.
8. **Goldfish.** `run.py goldfish` fait lire les cartes par un lecteur sans contexte. Corrige les cartes signalées, puis `run.py goldfish --slots <emplacements>`. Une carte marquée RÉÉCRIRE repart de zéro.
9. **Persister.** `run.py persist` écrit les brouillons. Rien n'est publié : la relecture revient à l'humain.
10. **Bilan.** Cartes écrites, cartes à relire, réserves, codes d'erreur proposés. Ne publie pas.

`run.py reprendre` liste les runs inachevés : après une coupure, reprends au premier emplacement vide. Un fichier écrit n'est jamais perdu.

**Second run.** `forge fr 3e A1.2 --complement 10` (ou `--difficile 6`) ne produit que ce qui manque. La fiche existe déjà : ne relis aucune page. Le fichier `existants.md`, à la fin de l'ordre de lecture, résume la banque : ne reprends ni ces structures ni ces valeurs.

## Écrire un exercice, dans cet ordre

1. **Le savoir-faire visé.** Une seule chose précise, tirée de la fiche.
2. **La solution**, avant l'énoncé : les étapes, le résultat.
3. **Les erreurs réelles** d'un élève sur ce savoir-faire, et le résultat exact auquel chacune mène.
4. **L'énoncé** : une situation qui demande ce savoir-faire, rien de plus.
5. **Les distracteurs**, tirés de ces erreurs. Un distracteur est une mauvaise réponse proposée : il est bon s'il correspond à une erreur réelle, et il est mauvais s'il n'est qu'un nombre au hasard.
6. **Les micro-corrections** : ce que l'élève qui a choisi cette réponse a fait, et ce qu'il faut faire.

Écrire l'énoncé en premier donne de faux pièges.

## Ce qui fait une bonne carte

- **Elle se suffit à elle-même.** Une carte est tirée seule, parmi d'autres, dans un sujet. Elle ne renvoie à aucune autre, ni au cours, ni à « la figure ci-dessus » sans figure.
- **Elle dit exactement quoi faire.** Un élève qui connaît la notion ne doit jamais hésiter sur la consigne.
- **Elle est au bon niveau.** Pas plus difficile à lire qu'à résoudre. Respecte ce que la fiche classe hors programme.
- **Elle est variée.** Les 20 exercices couvrent les micro-savoir-faire de la compétence. Changer les nombres ne fait pas un nouvel exercice.
- **Elle est réaliste.** Des valeurs plausibles et des résultats raisonnables.
- **Elle est compacte.** Pas de phrase décorative. Le blanc perdu sur une copie se paie.

## Grille : le script décide, tu remplis

La grille donne pour chaque emplacement la difficulté, le format, la taille visée et la présence d'une figure. Ne recalcule aucun pourcentage. La taille est un objectif, la qualité passe avant : si un format ou une taille dégrade vraiment l'exercice, remplis `slot_swap.reason`.

- **Facile** : une étape, application directe.
- **Moyen** : deux étapes, ou une mise en contexte.
- **Difficile** : trois étapes ou plus, transfert, ou piège.

Le barème est calculé par le script, jamais par toi. Donne seulement `estimated_seconds`, le temps réel d'un élève de ce niveau.

## Problèmes

Un problème porte sur tout le chapitre : une situation plus longue, 2 à 5 parties liées, un titre concret. Chaque partie déclare la compétence qu'elle évalue, et les compétences indiquées par la grille doivent toutes apparaître. Une partie ne doit pas exiger le résultat exact de la précédente quand c'est évitable : donne-le si besoin (« On admet que… »), pour qu'une erreur ne se propage pas.

## Fiche du chapitre

Fichier Markdown, sans aucune date, écrit une fois par chapitre, pour chaque compétence :

- ce que signifie la maîtriser (ce que l'élève sait faire seul) ;
- les méthodes et le vocabulaire enseignés à ce niveau ;
- ce qui est hors programme à ce niveau ;
- les prérequis ;
- les micro-savoir-faire et leur progression, du plus simple au plus exigeant ;
- les types de tâches et de figures ;
- les erreurs fréquentes, avec leur code de la taxonomie.

**Aucun énoncé recopié, aucune suite de valeurs du manuel.** La fiche décrit, elle ne cite pas.

## Rédaction libre : le dossier du correcteur

Pour une rédaction, un LLM corrige la copie. `llm_brief` lui donne ce qu'il lui faut : objectif, étapes et points, autres méthodes valables, erreurs attendues avec l'identifiant de leur message, ce qu'il ne faut pas pénaliser. Ce texte n'est jamais imprimé.

## Codes d'erreur

La taxonomie est fermée. Choisis un code existant. Si aucun ne convient, utilise `autre` et propose le nouveau code dans `proposed_errors` : il sera validé par un humain.

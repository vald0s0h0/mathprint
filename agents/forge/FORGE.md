# Forge : créer des exercices originaux

Tu rédiges des exercices imprimés pour de vrais élèves, corrigés ensuite en grande partie par une machine. Tu crées chaque exercice : **tu ne recopies jamais un manuel**, ni ses énoncés ni ses valeurs. Tu t'en inspires pour comprendre ce qu'il faut maîtriser, à quel niveau et avec quelles erreurs fréquentes.

Les scripts vérifient tout ce qui se vérifie : formats, quotas, LaTeX, calculs, place disponible. Ton travail est ce qu'un script ne sait pas faire : une situation claire, un vrai raisonnement, un piège juste, un message qui aide.

## Commande

`forge <programme> <cycle> <cible> [--problemes] [--pilote] [--traces N]`, par exemple `forge fr 3e A1.2`.

Le programme et le cycle sont obligatoires : s'il en manque un, demande-le avant toute chose. Les scripts se lancent depuis la racine du dépôt avec `backend/.venv/bin/python agents/forge/run.py`. Ne modifie pas le code de l'application pendant un run. Ton travail, ce sont les fichiers `slots/*.json` du run et la fiche du chapitre.

## Procédure

1. **Préparer.** `run.py prepare <programme> <cycle> <cible> --modele <ton identifiant exact>`, avec `--problemes` ou `--pilote` si demandé.
2. **Lire le contexte**, dans l'ordre affiché, une seule fois, sans modifier ces fichiers. Cet ordre stable permet au cache de fonctionner.
3. **Fiche du chapitre, si elle est marquée « À ÉCRIRE ».** Lis les sources et les images indiquées, puis écris la fiche (voir plus bas). Elle resservira à tout le chapitre. Si le chapitre compte plusieurs compétences, traite chacune dans un **contexte neuf** (sous-agent ou nouvelle session), à partir du même ordre de lecture. Un chapitre par session, trois sous-agents au plus en parallèle : au-delà, la limite d'usage coupe les runs en cours, et chaque reprise relit tout sans cache. Passe toujours le nom du run aux commandes, et lance `persist` dès qu'un run est fini.
4. **Rédiger par lots.** `run.py suivant` donne le prochain lot. Écris un fichier `slots/<emplacement>.json` par exercice (contrat : `schema.md`). Prends 5 emplacements à la fois, 3 si les figures sont complexes.
5. **Contrôler après chaque lot.** `run.py check` : corrige seulement les emplacements en erreur, jusqu'à zéro erreur. Après deux échecs sur un même emplacement, réécris-le depuis zéro plutôt que de le rafistoler.
6. **Rendre, quand tout est écrit.** `run.py render` produit le PNG réel de chaque carte, mesure le blanc et simule des sujets. Il essaie déjà lui-même les mises en page de figure. Le texte de l'énoncé, les guides et les figures se placent seuls : figure centrée si le texte est trop court pour l'accompagner, marges équilibrées sinon, colonne de libellés réduite tant que le tableau ne grandit pas. Il simule aussi **l'écriture de l'élève** dans chaque case, ligne et cellule, avec la réponse attendue et les réponses fausses prévues (`ecrit/`, à regarder en cas de doute). `ÉCRITURE TROP SERRÉE` et `INTERLIGNE CASSÉ` bloquent `persist` : réécris la carte (moins de colonnes, réponse plus courte, tableau au lieu d'une phrase à trous). Quand une carte contexte + question a une figure, sépare la question finale par un saut de ligne : elle passera sous la figure, sur toute la largeur.
7. **Compacter si le rapport signale un blanc anormal.** Raccourcis une phrase ou resserre la figure, sans changer aucune valeur ni aucune réponse. Regarde le PNG de la carte concernée.
8. **Goldfish.** `run.py goldfish` fait lire les cartes par un lecteur sans contexte. Corrige les cartes signalées, puis `run.py goldfish --slots <emplacements>`. Une carte marquée RÉÉCRIRE repart de zéro. Pour une relecture dans une session indépendante, utilise `goldfish <run> --slots ... --session-dir <dossier> --modele <modèle réellement utilisé>` : lis chaque fichier `.request.json` dans un contexte neuf, sans accès à la rédaction, puis écris le JSON de réponse dans le fichier de même nom terminé par `.response.json`. Relance la commande pour produire les demandes de micro-corrections, puis pour terminer. Aucun verdict partiel ne compte comme réussi.
9. **Persister.** `run.py persist` écrit les brouillons. Rien n'est publié : la relecture revient à l'humain.
10. **Bilan.** Cartes écrites, cartes à relire, réserves, codes d'erreur proposés. Ne publie pas.

Ce que le relecteur doit regarder sur une carte précise va aussi dans son champ `review_flags` : ce texte s'affiche en rouge sur la carte, dans l'onglet Exercices. Écris-le avant `persist`.

`run.py reprendre` liste les runs inachevés : après une coupure, reprends au premier emplacement vide. Un fichier écrit n'est jamais perdu.

**Cartes écartées.** Quand l'utilisateur écrit `forge ecartes [filtre]`, `run.py ecartes [filtre]` liste les cartes qu'il a marquées d'un pouce baissé, avec le commentaire admin et le retour complet de Goldfish. Seules les cartes au pouce baissé sont à reprendre, même si d’autres portent un signalement Goldfish. Pour chacune : relis le contexte du run, réécris `slots/<emplacement>.json` en réglant le problème signalé, puis enchaîne `check`, `render`, `goldfish --slots <emplacements>` et `persist <run> --slots <emplacements>`. Seuls ces emplacements sont remplacés : les autres cartes, relues ou publiées, ne bougent pas. Avant de modifier une carte, exporte la sélection : `run.py ecartes --programme fr --cycle 3e --domaine C --export <manifeste.json>` (ou le filtre ciblé). Persiste avec `persist <run> --slots <emplacements> --reprise <manifeste.json>`. La sélection, l’avis courant et la version sont revérifiés ; le dernier rendu doit avoir une relecture Goldfish complète. Le nouveau brouillon repart sans avis courant, mais conserve l’historique : commentaire, ancienne version, correction, résultat Goldfish ou échec. Une demande non satisfaite reste dislikée et comptée. Si un signalement ne suffit pas à comprendre ce qui ne va pas, demande à l'utilisateur plutôt que de deviner.

**Tracés.** `forge fr 3e C1.2 --traces 4` ne produit que des géométries à tracer (`geo_construction`, voir `schema.md`), à ajouter à une compétence déjà fournie, sans en remplacer aucune. Les modes suivent la même progression que la grille : ancrés sur quadrillage en facile, ancré puis libre en moyen, libre puis ancré sans quadrillage en difficile. Réserve-les aux savoir-faire où construire est la compétence évaluée : symétries, translations, rotations, agrandissements, perpendiculaires, parallèles, cercles, triangles et angles à construire. Un tracé est ancré (figure de départ imprimée) ou libre (case vide : « construis un triangle de côtés… », « trace un angle de 50° »). `suivant` annonce le barème et le temps de chaque tracé ; le bilan les rappelle, carte par carte. Comme pour un second run, la fiche existe déjà et `existants.md` résume la banque.

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

**Tracés dans la grille.** Selon le profil de tracés de la compétence, la grille remplace des QCM et des liaisons par des géométries à tracer (`geo_construction`), sans changer le nombre de cartes :

| Niveau | Profil `central` (6 tracés) | Profil `partiel` (2 tracés) |
|---|---|---|
| Facile | 2 ancrés sur quadrillage, à la place d'un QCM simple et du QCM multiple | aucun |
| Moyen | 1 ancré et 1 libre, à la place des 2 QCM multiples | 1 libre, à la place d'un QCM multiple |
| Difficile | 1 libre à la place de la liaison, 1 ancré sans quadrillage (compas, rapporteur) à la place d'une rédaction | 1 tracé, ancré ou libre, à la place de la liaison |

La fiche déclare le profil (voir plus bas) ; sans déclaration, `prepare` le devine d'après le libellé (« construire », « transformer », « tracer » donnent `central`, sinon `aucun`). Si la fiche s'écrit après `prepare`, `suivant` refait la grille tant qu'aucun emplacement n'est écrit. `suivant` annonce le mode de chaque tracé, et `check` refuse un tracé d'un autre mode sans `slot_swap.reason`. La difficulté monte d'un élément à compter sur quadrillage vers une construction en plusieurs étapes.

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
- les erreurs fréquentes, avec leur code de la taxonomie ;
- son profil de tracés, sur une ligne à part : `**Tracés : central.**`, `partiel` ou `aucun`, suivi de la raison. `central` quand construire est la compétence évaluée (symétries, translations, rotations, agrandissements, constructions de triangles, d'angles, de parallèles ou de perpendiculaires) ; `partiel` quand un tracé l'illustre sans en être le cœur ; `aucun` quand rien ne se construit sur une feuille plane (sections de solides, calculs, repérage sur une sphère). Avec `central` ou `partiel`, la fiche décrit aussi les tracés possibles et les erreurs qui se dessinent.

**Aucun énoncé recopié, aucune suite de valeurs du manuel.** La fiche décrit, elle ne cite pas.

## Rédaction libre : le dossier du correcteur

Pour une rédaction, un LLM corrige la copie. `llm_brief` lui donne ce qu'il lui faut : objectif, étapes et points, autres méthodes valables, erreurs attendues avec l'identifiant de leur message, ce qu'il ne faut pas pénaliser. Ce texte n'est jamais imprimé.

## Codes d'erreur

La taxonomie est fermée. Choisis un code existant. Si aucun ne convient, utilise `autre` et propose le nouveau code dans `proposed_errors` : il sera validé par un humain.

## Pièges connus du moteur

`check` et `render` ne les signalent pas tous. Contourne-les :

- **LaTeX.** `\cos`, `\sin` et `\tan` sont refusés : écris `\mathrm{cos}\,`. « ° » s'imprime précédé d'une espace.
- **Lettres.** Le J en italique se lit « / » ou « I » : nomme les points autrement.
- **Cases données.** Une case `given` de type `decimal` s'imprime avec un point : mets `type: text` et `$3{,}2$`.
- **`multi_blank`.** La ligne de réponses reste entière autour du guide ; ne sépare jamais une égalité et sa case.
- **Mise en page.** Les tableaux sont naturellement dimensionnés et centrés. `response_layout` permet de demander pleine largeur ou champs homogènes. `figure_constraints` impose une échelle ou une lisibilité minimale : le compacteur ne peut pas les contourner.
- **Arrondi.** `exact_decimal` se déclenche sur le mot « arrondi » ; « au degré près » seul ne suffit pas.
- **Réponses écrites.** Un radical (`√7`) ou une fraction négative est mal lu : n'en attends pas. Un « oui » ou un « non » écrit dans un tableau n'est pas encore validé sur de vrais scans : préfère une case à cocher.
- **SVG.** Les pointillés de line/polyline/polygon/circle/ellipse sont convertis en segments ; pour un path courbe pointillé, trace les tirets explicitement. `text-anchor` sur un `<g>` est ignoré : mets-le sur chaque `<text>`. Le gabarit `balance` fait chevaucher ses étiquettes : dessine-la en SVG.
- **Goldfish.** Il découpe la réponse du lecteur sur « et » et « ; », et ne lit pas « ≠ » : évite-les dans les propositions. Sur un blocage, vérifie la réponse du lecteur avant de réécrire. Relance `render` avant `goldfish --slots` (emplacements séparés par des espaces), car il lit les images de `cards/`.
- **Clés de fuite.** Ne retire jamais une `leak_keys` qui est une vraie réponse pour faire taire un conflit. Une simple coïncidence de nombre entre deux contextes sans rapport peut sortir.

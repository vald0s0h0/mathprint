# Consigne : écrire les exercices d'une compétence

Ce fichier dit quoi faire et dans quel ordre. Ce qu'il faut savoir pour bien écrire est dans les contextes, que `prepare` te fait lire du plus général au plus spécifique : le bon exercice (`contexte/exercice.md`), les formats, la matière, le pays, puis la leçon.

Les scripts vérifient tout ce qui se vérifie : formats, quotas, LaTeX, calculs, place disponible, faits cités. Ton travail est ce qu'un script ne sait pas faire : une situation claire, un vrai raisonnement, un piège juste, un message qui aide.

## Commande

`forge <programme> <cycle> <cible> [--serie S] [--matiere M] [--problemes] [--suite] [--pilote] [--traces N]`, par exemple `forge fr 3e A1.2` ou `forge ci tle E1.4 --serie D`.

Le programme et le cycle sont obligatoires : s'il en manque un, demande-le avant toute chose. Les scripts se lancent depuis la racine du dépôt avec `backend/.venv/bin/python agents/forge/run.py`. Ne modifie pas le code de l'application pendant un run. Ton travail, ce sont les fichiers `familles/*.json` du run. La leçon a été écrite avant, par une autre commande (`forge lecon`), et tu ne la modifies pas.

## Procédure

Tu travailles **seul, sans sous-agent**, du début à la fin : le contexte se lit une fois et sert à toutes les compétences. Chaque appel d'outil renvoie tout ce que tu as lu : économise les tours (une commande plutôt que trois, des lots de 5 familles, pas de relecture de fichiers déjà lus).

1. **Préparer.** `run.py prepare <programme> <cycle> <cible> --modele <ton identifiant exact>`, avec `--serie`, `--matiere`, `--problemes`, `--suite` ou `--pilote` si demandé. Si la commande répond `LEÇON ABSENTE`, `LEÇON À RÉÉCRIRE` ou `LEÇON À VÉRIFIER`, arrête-toi et donne à l'utilisateur la commande qu'elle affiche. Une cible peut être une leçon entière (`E1`) : un run par compétence, toutes écrites par toi.
2. **Lire le contexte**, le fichier unique affiché par `prepare`, une seule fois, en entier. Il ne contient que ce qui sert à ces grilles : le bon exercice, les formats de la grille, la matière, les erreurs types citées par la leçon, le pays et la leçon.
3. **Rédiger par lots, compétence après compétence.** `run.py suivant <run>` donne le prochain lot. Écris une **famille** par emplacement, dans `familles/<emplacement>.json` du run : la carte une seule fois, avec 2 ou 3 jeux de valeurs et, quand des réponses se déplacent, tes deux dispositions. Chaque famille déclare `faits`. **Écris les familles d'un lot en un seul appel d'outil** (un patch ou une commande qui crée tous les fichiers du lot), jamais un appel par famille, puis contrôle. Ne rouvre ni une image de carte ni un fichier déjà lu, sauf si `valider` le demande.
4. **Valider.** `run.py valider <run>` enchaîne le contrôle, le rendu, la lecture par un élève sans contexte (goldfish), la relecture pédagogique puis l'écriture des brouillons, et s'arrête à la première étape qui demande d'agir, avec une sortie courte. Corrige seulement ce qu'elle signale, puis relance `valider` (avec `--slots` pour ne relire que ces emplacements). Après deux échecs sur une même famille, réécris-la depuis zéro. Une carte bloquée par le relecteur est réécrite **une fois** avec sa critique ; bloquée de nouveau, elle est écartée : n'y touche plus.
5. **Brouillons.** Quand tout passe, `valider` écrit lui-même les brouillons (`persist`). Rien n'est publié ; l'humain relit l'échantillon tiré par l'indice de confiance, puis publie d'un clic.
6. **Bilan**, à la fin de la cible : `run.py bilan <run>` pour chaque run, puis en quelques lignes : familles écrites, écartées et pourquoi, codes d'erreur proposés, ce que tu as remarqué sur la leçon (un fait qui manque, un fait douteux).

En cas de besoin : `run.py render`, `goldfish` et `relecture` existent séparément. Quand une carte contexte + question a une figure, sépare la question finale par un saut de ligne : elle passera sous la figure, sur toute la largeur. Si `render` signale un blanc anormal, raccourcis une phrase ou resserre la figure sans changer aucune valeur. Dans chaque famille, l’écart de hauteur entre la carte la plus haute et la plus petite ne doit pas dépasser 5 mm, avec et sans aides : si `render` le signale, adapte les cartes, en priorité leurs figures, puis relance `valider`. À 5 mm ou moins, ne modifie rien pour cette raison.

Ce que le relecteur humain doit regarder sur une carte précise va dans son champ `review_flags` : ce texte s'affiche en rouge sur la carte, dans l'onglet Exercices. Écris-le avant `persist`.

`run.py reprendre` liste les runs inachevés : après une coupure, reprends au premier emplacement vide. Un fichier écrit n'est jamais perdu.

## Grille : le script décide, tu remplis

Le nombre de familles dépend de la compétence : une seule pour un savoir à restituer (« Connaître », « Identifier »), quatre à six pour un savoir-faire selon sa durée, trois pour un raisonnement. Une compétence « Traiter une situation » n'a pas d'exercices : elle se travaille par les problèmes de la leçon (`--problemes`).

`suivant` donne, pour chaque emplacement : la difficulté, le format, la taille visée, la présence d'une figure, l'**archétype** avec sa définition, et parfois une **idée** tirée de la leçon. Les élèves fictifs (`ERR` : une copie à corriger ; `QUI` : deux élèves en désaccord) sont répartis par la grille sur toute la leçon, 2 ou 3 en tout : n'en ajoute jamais ailleurs. Une idée est le mécanisme d'un bon exercice, en deux phrases : construis ta famille sur ce mécanisme, avec ton propre contexte, tes personnages et tes valeurs. Ne recalcule aucun pourcentage. La taille est un objectif, la qualité passe avant : si un format ou une taille dégrade vraiment l'exercice, remplis `slot_swap.reason`.

**Tracés dans la grille.** Selon le profil de tracés de la compétence (ligne `**Tracés : …**` de la leçon, sinon deviné du libellé), la grille remplace des QCM et des liaisons par des géométries à tracer (`geo_construction`), sans changer le nombre de cartes :

| Niveau | Profil `central` (6 tracés) | Profil `partiel` (2 tracés) |
|---|---|---|
| Facile | 2 ancrés sur quadrillage, à la place d'un QCM simple et du QCM multiple | aucun |
| Moyen | 1 ancré et 1 libre, à la place des 2 QCM multiples | 1 libre, à la place d'un QCM multiple |
| Difficile | 1 libre à la place de la liaison, 1 ancré sans quadrillage (compas, rapporteur) à la place d'une rédaction | 1 tracé, ancré ou libre, à la place de la liaison |

`suivant` annonce le mode de chaque tracé, et `check` refuse un tracé d'un autre mode sans `slot_swap.reason`. Le contrat est dans `contexte/formats/geo_construction.md`, que `prepare` ajoute à l'ordre de lecture quand la grille a des tracés.

## Runs particuliers

**Tracés.** `forge fr 3e C1.2 --traces 4` ne produit que des géométries à tracer, à ajouter à une compétence déjà fournie, sans en remplacer aucune. Les modes suivent la même progression que la grille. Réserve-les aux savoir-faire où construire est la compétence évaluée. `suivant` annonce le barème et le temps de chaque tracé ; le bilan les rappelle, carte par carte.

**Second run.** `forge fr 3e A1.2 --suite` produit une nouvelle série complète, différente de la banque ; `forge fr 3e A1.2 --complement 10` (ou `--difficile 6`) ajoute des cartes à une compétence épuisée. Le fichier `existants.md`, à la fin de l'ordre de lecture, résume la banque : ne reprends ni ces structures ni ces valeurs.

**Cartes au pouce baissé.** Quand l'utilisateur écrit `forge ecartes [filtre]`, `run.py ecartes [filtre]` liste les cartes qu'il a marquées d'un pouce baissé, avec la **cause** choisie, le niveau à corriger, le commentaire admin et le retour des lecteurs. Seules les cartes au pouce baissé sont à reprendre. Quand la cause désigne la leçon, la matière, un format ou cette consigne, dis-le dans le bilan : c'est ce fichier qu'il faut corriger, une fois, pour que l'erreur ne revienne pas. Pour chaque carte : exporte la sélection (`run.py ecartes --programme fr --cycle 3e --domaine C --export <manifeste.json>`), relis le contexte du run, réécris `familles/<emplacement>.json` en réglant le problème, puis enchaîne `valider <run> --slots <emplacements>` et `persist <run> --slots <emplacements> --reprise <manifeste.json>`. Seuls ces emplacements sont remplacés. Si un signalement ne suffit pas à comprendre ce qui ne va pas, demande à l'utilisateur plutôt que de deviner.

## Pièges connus du moteur

`check` et `render` ne les signalent pas tous. Contourne-les :

- **LaTeX.** `\cos`, `\sin` et `\tan` sont refusés : écris `\mathrm{cos}\,`.
- **Lettres.** Le J en italique se lit « / » ou « I » : nomme les points autrement.
- **Cases données.** Une case `given` de type `decimal` s'imprime avec un point : mets `type: text` et `$3{,}2$`.
- **`multi_blank`.** La ligne de réponses reste entière autour du guide ; ne sépare jamais une égalité et sa case.
- **Mise en page.** Les tableaux sont naturellement dimensionnés et centrés. `response_layout` permet de demander pleine largeur ou champs homogènes. `figure_constraints` impose une échelle ou une lisibilité minimale : le compacteur ne peut pas les contourner.
- **Arrondi.** `exact_decimal` se déclenche sur le mot « arrondi » ; « au degré près » seul ne suffit pas.
- **Réponses écrites.** Un « oui » ou un « non » écrit dans un tableau n'est pas encore validé sur de vrais scans : préfère une case à cocher.
- **Figures.** Toute nouvelle figure s'écrit en TikZ dans la charte (`contexte/figures/commun.md`) ; `check` refuse les anciens moteurs. Une balance s'écrit `\Balance{…}{…}`, jamais à la main. Une figure TikZ n'est presque pas réduite à la mise en page : prévois sa taille dès le dessin. La première compilation du Mac peut prendre une minute, les suivantes une à deux secondes.
- **Goldfish.** Il découpe la réponse du lecteur sur « et » et « ; », et ne lit pas « ≠ » : évite-les dans les propositions. Sur un blocage, vérifie la réponse du lecteur avant de réécrire. Relance `render` avant `goldfish --slots` (emplacements séparés par des espaces), car il lit les images de `cards/`.
- **Clés de fuite.** Ne retire jamais une `leak_keys` qui est une vraie réponse pour faire taire un conflit. Une simple coïncidence de nombre entre deux contextes sans rapport peut sortir.

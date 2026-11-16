# Contexte matière : mathématiques

Comment le bon exercice s'écrit en mathématiques, et les règles qui valent dans toutes les leçons. Une leçon ne les répète pas : elle dit seulement ce qui lui est propre.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il applique directement le savoir-faire | Consigne courte. C'est l'exercice de révision : pas de situation forcée |
| `REPR` | Il passe d'une représentation à une autre | Tableau, graphique, formule, figure, phrase : donne l'une, demande l'autre |
| `ERR` | Il trouve et corrige l'erreur d'une copie | Montre le travail d'un élève, avec **une** erreur réelle de la taxonomie. Chaque ligne fausse est calculée (`calc`). La question porte sur l'erreur, pas sur le résultat seul |
| `QUI` | Il tranche entre deux élèves | Deux réponses, deux méthodes ; une seule est juste, ou les deux le sont pour une raison à dire. L'erreur est provoquée, pas annoncée |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des affirmations générales ; une fausse se réfute par un contre-exemple |
| `INVERSE` | Il retrouve la donnée à partir du résultat | Le résultat est donné : « quel nombre de départ ? », « quelle longueur manque ? » |
| `ESTIM` | Il juge un ordre de grandeur | Sans calcul exact : ce résultat est-il possible ? Lequel est le plus proche ? |
| `DOC` | Il lit un document | Ticket, facture, horaires, étiquette, relevé : les données y sont, avec au moins une donnée inutile |
| `DECIDE` | Il prend une décision | Deux offres, deux trajets, deux forfaits : la réponse est un choix, justifié par un calcul |

**Élèves fictifs (`ERR`, `QUI`).** La grille en place 2 ou 3 par leçon, pas plus. Sur une feuille, ils doivent se lire d'un coup d'œil :

- la copie tient en **une à trois lignes de calcul simple**, écrites comme un élève les écrirait, et contient **une seule** erreur, réelle, de la taxonomie ;
- la question est directe et unique : « Quelle ligne est fausse ? », « Qui a raison ? », « Corrige le résultat ». Jamais « Coche les affirmations qui corrigent cette copie » ;
- la situation n'ajoute aucune difficulté de lecture : pas de figure à décoder en plus de la copie, sauf si la figure est le sujet de l'erreur ;
- si l'erreur ne se montre pas ainsi, écris un autre archétype et donne la raison dans `slot_swap.reason`.

Les élèves portent des prénoms du contexte pays. Aucun n'est toujours celui qui se trompe.

## Tracés

Un tracé n'est pas un archétype : c'est un **format** (`geo_construction`), que la grille place d'après la ligne « Tracés » de la leçon (profil `central`, `partiel` ou `aucun`). Un tracé porte donc un archétype comme toute carte, le plus souvent :

- `CALC` : construire directement (symétrique d'une figure, triangle de côtés donnés, parallèle passant par un point) ;
- `REPR` : passer d'une description ou d'un codage à la figure (« trace un triangle ABC rectangle en A tel que… ») ;
- `INVERSE` : retrouver l'élément caché (le centre d'une symétrie, l'axe, le point de départ d'une translation) ;
- `DOC` : construire à partir d'un plan, d'une carte ou d'un schéma à l'échelle.

Un bon tracé évalue la construction elle-même, pas le soin : les longueurs et les angles demandés se mesurent à la règle et au rapporteur (au millimètre, au degré), les instruments sont ceux du niveau, et l'énoncé dit ce qui doit rester visible (traits de construction, codage). Il tient dans la case ou le quadrillage prévus, sans que l'élève ait à deviner où commencer. Le contrat technique est dans `contexte/formats/geo_construction.md`.

## Contre-sens interdits

- Conclure d'après l'apparence d'une figure.
- Un piège qui repose sur une notion d'une classe supérieure ou d'une leçon pas encore vue.
- Une égalité fausse écrite sans être présentée comme le travail d'un élève.
- Une unité oubliée dans une réponse attendue, ou deux unités mêlées sans conversion demandée.
- Une « situation » où le nombre demandé n'a aucun sens (un prix négatif, 3,7 élèves).

## Règles valables dans toutes les leçons

- **Calculatrice autorisée**, au collège comme au lycée, comme au brevet, au BEPC et au baccalauréat. Les valeurs peuvent donc être réalistes : prix, mesures, données réelles. Un résultat qui ne tombe pas juste se demande arrondi, avec le mot « arrondi » et la précision (« arrondi au dixième », `exact_decimal`) ; la réponse attendue est la valeur de la calculatrice, arrondie. Exception : quand le savoir-faire évalué est le calcul lui-même (calcul mental, opération posée, priorités, fractions, puissances, calcul littéral, valeur exacte d'une racine), la carte l'écrit (« sans calculatrice ») et ses valeurs se calculent à la main en peu de temps.
- **Forme des réponses écrites.** Aucune réponse écrite attendue sous forme de radical ($\sqrt{7}$) ni de fraction négative : l'écriture manuscrite est mal lue. Une longueur qui vaut $\sqrt{7}$ se demande arrondie ; un radical peut figurer dans un énoncé, une proposition de QCM ou un élément à relier. Dis la forme attendue quand elle compte : fraction irréductible, décimal, unité, arrondi.
- **Apparence.** L'élève ne conclut jamais d'après la figure. Ce qui fonde la réponse est dit dans le texte ou codé sur la figure : alignements dits, parallélisme dit ou déduit d'un codage, angles droits et égalités codés, longueurs cotées, polygone dit régulier, sommets « sur les nœuds du quadrillage » écrits dans l'énoncé. Une figure qui n'est pas à l'échelle le dit.
- **Désignation.** Une droite, une figure, un point se désigne par son nom ou un numéro écrit à côté, jamais par sa couleur ni sa position (« la droite rouge », « le triangle de gauche »).
- **Notations.** $(AB)$ la droite, $[AB]$ le segment, $AB$ la longueur ; $\widehat{ABC}$ l'angle de sommet $B$ ; l'image d'un point $A$ se note $A'$. Coordonnées avec point-virgule : $(2 ; 3)$. Virgule décimale. Longueurs en cm ou en m, angles en degrés.
- **Au lycée.** Intervalles à crochets ouverts vers l'extérieur ($]2 ; 5]$, $[0 ; +\infty[$) ; ensembles $\mathbb{N}$, $\mathbb{Z}$, $\mathbb{D}$, $\mathbb{Q}$, $\mathbb{R}$ ; vecteurs fléchés ($\overrightarrow{AB}$, $\vec{u}$) ; suites $(u_n)$ ; $\ln$ et $\exp$ ou $e^x$ ; limites écrites $\lim\limits_{x \to +\infty} f(x)$ ; angles orientés et mesures en radians, sauf mention contraire. Une fonction étudiée a son ensemble de définition dit ou demandé. Une réponse qui s'écrit mal à la main (intervalle, tableau de variation ou de signes, ensemble de solutions) se demande en cases à cocher, en tableau à compléter ou en choix, pas en écriture libre.
- **Figures exactes.** Une figure part de coordonnées exactes : ses longueurs affichées sont celles de ses coordonnées. Un triangle se construit à partir de ses côtés ou de ses angles, puis se met à l'échelle ; une configuration de proportionnalité s'obtient par $M = A + k(B - A)$. Les longueurs respectent l'inégalité triangulaire. Les macros et exemples de figures sont dans `contexte/figures/math.md`.
- **Tracer.** Seule une carte `geo_construction` demande de tracer ou de construire. Ailleurs, l'élève reconnaît, choisit, calcule ou relie.

## Charte d'écriture

- **Écriture.** Toute formule, tout nombre utilisé dans un calcul et toute unité composée vont dans `$...$`. Virgule décimale française : `$3{,}5$`. Multiplication : `\times`, jamais `*`. Fractions : `\dfrac{a}{b}`.
- **Justesse.** Si un résultat se calcule, donne le calcul à vérifier (`calc`).
- **Vocabulaire.** Celui enseigné à ce niveau, d'après la leçon. Une notion du programme d'une classe supérieure n'apparaît pas, même en piège.

## Exemples validés

`contexte/exemples/math.json` contient des cartes relues et validées, une ou deux par format, chacune avec la raison de sa qualité. Ce sont des modèles de qualité : n'en reprends ni les situations ni les valeurs.

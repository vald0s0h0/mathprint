# Contexte matière : sciences de la vie et de la Terre

Ce qui fait un bon exercice ici, et les archétypes que la grille te demande.

## Un bon exercice

- **Il évalue une seule chose**, nommée dans `micro_skill` : une notion, un raisonnement expérimental, une lecture de document. Un élève qui échoue doit avoir échoué sur elle, pas sur la longueur du texte.
- **La notion est nécessaire.** Si une réponse se devine au bon sens ou à l'image, la carte ne vaut rien. Vise le cas où l'idée reçue se trompe : la plante qui « ne respire pas », le caractère acquis « transmis ».
- **Ses mauvaises réponses sont de vraies erreurs d'élèves**, prises dans la taxonomie : une conception erronée, une confusion de vocabulaire, une conclusion trop large.
- **Elle ne s'appuie que sur la fiche.** Toute affirmation sur le vivant, la santé ou la Terre est un fait `F…` de la fiche. Cite ceux que tu utilises dans `review_flags` (« Faits : F2, F9 »). Un fait vrai mais absent de la fiche n'apparaît pas.
- **Ses données sont plausibles** : des résultats d'expérience cohérents avec la fiche, avec la variabilité d'une vraie mesure ; des tailles et des durées aux bons ordres de grandeur.

## Raisonner sur une expérience

- Une expérience teste une hypothèse ; elle a un témoin et ne fait varier qu'un facteur. Quand la carte porte sur la démarche, c'est ce que l'élève repère, choisit ou complète.
- Distingue ce que l'élève **constate** (description d'un résultat) de ce qu'il **en déduit** (interprétation) : une carte demande l'un ou l'autre, et le dit.
- Une conclusion vaut pour les conditions de l'expérience : une généralisation abusive est une erreur à provoquer, jamais une bonne réponse.
- Une corrélation n'est pas une cause : un document qui montre deux évolutions parallèles ne prouve pas que l'une produit l'autre.
- Un tableau ou un graphique de résultats porte ses grandeurs, ses unités et ses conditions ; ses valeurs sont générées par le script à partir du jeu.
- Une expérience décrite est réalisable et sans danger ; une expérience sur l'humain ou l'animal vient de la fiche, jamais de toi.

## Figures

Seulement les macros des kits de la charte (`kn-svt` : brin d'ADN, échiquier de croisement, flèches de légende, graphiques de mesures), jamais une image produite par une IA. Un brin, un échiquier, un arbre généalogique sont calculés par le script à partir du jeu : ils sont cohérents par construction. Si la macro qu'il te faudrait n'existe pas (une coupe d'organe, une cellule détaillée), écris la carte sans figure ou change de micro-savoir-faire, et dis-le dans le bilan.

**Schéma à légender** : des flèches numérotées sur la figure, et pour chaque numéro une case où l'élève écrit le mot (« noyau », « membrane »). Le mot attendu est un terme de la fiche ; déclare ses orthographes acceptées (singulier et pluriel, avec ou sans article). Pas plus de 5 flèches.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il applique directement une notion | Écrire le brin complémentaire, traduire un codon avec la table, classer un organisme d'après ses attributs, nommer une structure. Consigne courte |
| `REPR` | Il passe d'une représentation à une autre | Tableau de mesures ↔ graphique, texte ↔ schéma fonctionnel, échiquier ↔ proportions, liste « qui mange qui » ↔ chaîne alimentaire fléchée |
| `ERR` | Il trouve et corrige l'erreur d'une copie | Le travail d'un élève avec **une** erreur réelle : complémentarité fausse, flèche de chaîne alimentaire inversée, conclusion qui dépasse l'expérience. La question porte sur l'erreur |
| `QUI` | Il tranche entre deux élèves | Deux interprétations d'un même résultat, deux hypothèses : une seule tient compte du témoin ou de toutes les données |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des affirmations générales tirées des conceptions erronées (« les plantes ne respirent pas ») ; une fausse se réfute par un fait ou une expérience de la fiche |
| `INVERSE` | Il retrouve la donnée à partir du résultat | Le génotype des parents d'après les proportions, le brin d'ADN d'après l'ARN, le facteur testé d'après deux montages |
| `ESTIM` | Il juge un ordre de grandeur | La taille d'une cellule, d'une bactérie, d'un virus ; une durée géologique ; un effectif. Les ordres de grandeur viennent de la fiche |
| `DOC` | Il lit un document | Tableau de résultats, graphique, résultats d'analyse, texte court aux lignes numérotées : au moins une donnée inutile, réponse par une valeur, une case ou un numéro de ligne |
| `DECIDE` | Il prend une décision justifiée | Quelle expérience teste l'hypothèse, quel montage sert de témoin, quel document répond à la question |

Dans `ERR` et `QUI`, les élèves portent des prénoms du profil du programme. Aucun n'est toujours celui qui se trompe.

## Les jeux d'une famille

Une carte chiffrée (mesures, effectifs, séquences) a ses jeux de valeurs : même nombre de bases, même nature de calcul, mêmes pièges. Une carte sans nombres change d'éléments de même difficulté : un autre organisme de la fiche aux attributs aussi visibles, un autre organe de la même fonction, une autre séquence de même longueur. Deux jeux ne donnent jamais la même réponse. Une séquence d'ADN, un protocole, une suite d'étapes (digestion, cycle) ne se déplacent pas : `ordre_impose`.

## Formats

- **Bien lus** : QCM, vrai-faux en grille, relier (structure et fonction, organe et appareil), tableau de nombres, numéro de ligne.
- **Lettres d'une séquence** (A, T, C, G, U) : préfère un tableau d'une lettre par case à une suite écrite d'un trait.
- **Mot écrit** : pour un schéma à légender, ou un terme de la fiche sans variante.
- **Rédaction courte** (`multiline_text`, 2 à 4 lignes) : une interprétation ou une conclusion d'expérience, avec un dossier du correcteur qui liste les idées attendues. Pas de synthèse longue.

## Contre-sens interdits

- Un finalisme ou un anthropomorphisme dans l'énoncé (« la girafe a allongé son cou pour atteindre les feuilles », « la cellule veut »), hors copie d'élève fautive.
- Une conclusion plus large que l'expérience, ou une corrélation présentée comme une cause.
- Un conseil de santé, un traitement, une affirmation médicale absents de la fiche. Les sujets sensibles (sexualité, maladies, handicap) restent factuels, sans jugement ni stéréotype.
- Une notion d'une classe supérieure, même en piège (méiose avant la leçon qui l'introduit).
- Un schéma faux : organite inventé, proportions trompeuses, légende qui pointe à côté.

## Charte d'écriture

- **Écriture.** Nombres et unités dans `$...$` quand ils servent un calcul, virgule décimale française. Unités du Système international ; µm et nm pour le microscopique. Séquences en majuscules, par triplets séparés d'une espace quand on lit des codons (`ATG GCA`).
- **Noms.** Nom d'espèce en français, et nom scientifique en italique seulement si la fiche le donne (*Homo sapiens*, genre avec majuscule). Allèles et génotypes dans la notation de la fiche, toujours la même dans une carte.
- **Justesse.** Toute affirmation vient d'un fait de la fiche. Un résultat d'expérience est plausible et cohérent avec ce fait ; une séquence, un échiquier, un arbre généalogique sont calculés par le script. Une donnée réelle (statistique de santé, âge d'une roche) vient de la fiche, avec sa source.
- **Textes.** Un texte scientifique est écrit pour l'exercice et signalé comme tel (« d'après… » n'est jamais inventé), ou vient d'une source officielle citée.
- **Réponses manuscrites.** Un nombre, une lettre par case, un mot de la fiche. Jamais une phrase à lire au mot près.
- **Vocabulaire.** Celui de la fiche et du programme du niveau : « respiration » et « ventilation » distingués, « gène », « allèle », « chromosome » et « ADN » distingués.
- **Registre.** Factuel et neutre sur le corps, la santé et la sexualité, sans jugement ni stéréotype.
- **Figures.** Macros des kits de la charte seulement (`kn-svt`) ; aucune image produite par une IA.

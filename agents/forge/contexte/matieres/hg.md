# Contexte matière : histoire-géographie

Ce qui fait un bon exercice ici, et les archétypes que la grille te demande. Le périmètre est la connaissance des repères, le raisonnement (cause, conséquence, comparaison, localisation) et l'étude de documents. Ni la composition ni le croquis complet ne sont produits ici.

## Un bon exercice

- **Il évalue une seule chose**, nommée dans `micro_skill` : un repère, une notion, un lien de cause, une lecture de document. Un élève qui échoue doit avoir échoué sur elle, pas sur la longueur du document.
- **Le savoir ou le raisonnement est nécessaire.** Une réponse qui se devine à la formulation (la seule proposition longue, le seul mot savant) ne vaut rien. Mieux : la carte sépare celui qui a compris de celui qui récite (deux dates possibles, l'ordre de deux événements proches).
- **Ses mauvaises réponses sont de vraies erreurs d'élèves**, prises dans la taxonomie : un anachronisme, une confusion de siècle, une cause prise pour une conséquence, une localisation voisine.
- **Elle ne s'appuie que sur la fiche.** Toute date, tout acteur, tout chiffre, toute définition est un fait `F…` de la fiche : c'est indispensable ici, car rien ne se recalcule. Cite ceux que tu utilises dans `review_flags` (« Faits : F1, F8 »). Un fait vrai mais absent de la fiche n'apparaît pas, même en piège.
- **Elle est factuelle.** Aucun jugement moral ou politique imposé à l'élève ; les sujets douloureux (traite, colonisation, guerres, génocides, crises récentes) sont traités avec les mots de la fiche, sans détail gratuit. Aucun sujet politique actuel du pays au-delà de ce que la fiche contient.

## Documents

- Un document vient du **domaine public**, d'une **source officielle** (constitution, traité, statistique publique), ou il est **écrit pour l'exercice et signalé comme tel** (« Texte écrit pour l'exercice. »). Jamais un faux document attribué à un acteur réel, jamais un « témoignage » inventé présenté comme authentique.
- Un extrait authentique est cité exactement, avec sa nature, son auteur et sa date ; une coupure se marque `[…]`. Un texte a ses lignes numérotées de 5 en 5, 15 lignes au plus.
- Une donnée chiffrée (population, production, part) vient de la fiche, avec sa source et son année. Pas de chiffre inventé présenté comme réel : un tableau fictif dit qu'il l'est.
- Un document porte sa présentation (nature, auteur, date, source) sous le titre : l'élève en a besoin pour le critiquer.
- Une question de document a sa réponse dans le document ou dans la fiche, jamais dans une connaissance extérieure.
- Une carte montre les frontières de l'époque étudiée, ou dit qu'elle montre celles d'aujourd'hui.

## Figures

Seulement les macros des kits de la charte (`kn-hg` : frise, carte à zones numérotées, flèches de légende), jamais une image produite par une IA. Une frise place ses dates à l'échelle, calculée par le script. Une carte à compléter numérote ses zones ; l'élève répond par un numéro, une case ou un mot de la fiche. Si la macro ou le fond de carte qu'il te faudrait n'existe pas, écris la carte sans figure, et dis-le dans le bilan.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il restitue ou applique un repère | Une date, un acteur, un lieu, une définition de la fiche ; placer un événement dans un siècle ; associer un fait et sa période. Consigne courte |
| `REPR` | Il passe d'une représentation à une autre | Frise ↔ liste datée, carte ↔ tableau, graphique ↔ phrase, texte ↔ légende d'un croquis : donne l'une, demande l'autre |
| `ERR` | Il trouve et corrige l'erreur d'une copie | Le travail d'un élève avec **une** erreur réelle : un anachronisme, une date décalée d'un siècle, une localisation voisine, une cause prise pour une conséquence |
| `QUI` | Il tranche entre deux élèves | Deux lectures d'un même document, deux datations, deux explications ; une seule tient compte de la source ou de la chronologie |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des généralisations (« toutes les colonies sont devenues indépendantes la même année ») ; une fausse se réfute par un fait de la fiche |
| `INVERSE` | Il remonte de la conséquence à la cause | La conséquence ou le résultat est donné : retrouver la cause, l'acteur, le siècle d'une année, le lieu d'une activité |
| `ESTIM` | Il juge un ordre de grandeur | Une durée, une population, une distance, un écart entre deux dates : le plus plausible, d'après les faits de la fiche |
| `DOC` | Il étudie un document | Nature, auteur, date, point de vue, information précise : réponse par une case, une date ou un numéro de ligne ; au moins une information inutile |
| `DECIDE` | Il choisit d'après des documents | Quel lieu pour un port, une usine, un marché ; quel document répond à une question : un seul choix justifié par les documents |

Dans `ERR` et `QUI`, les élèves portent des prénoms du profil du programme. Aucun n'est toujours celui qui se trompe.

## Les jeux d'une famille

Une carte chiffrée (données, durées, échelles) a ses jeux de valeurs, toutes tirées de la fiche ou déclarées fictives. Sinon, un jeu change d'éléments de même difficulté : un autre événement de la même période et aussi connu, un autre pays de la même région, une autre notion du même chapitre. Deux jeux ne donnent jamais la même réponse. **Une chronologie ne se déplace jamais** : une frise, une suite d'événements, les étapes d'un processus gardent leur ordre (`ordre_impose`).

## Formats

- **Bien lus** : QCM, vrai-faux en grille, relier (date et événement, notion et définition), tableau à cocher, numéro de zone ou de ligne, année en chiffres.
- **Année écrite** : quatre chiffres, sans « av. J.-C. » à lire ; pour une date avant notre ère, fais cocher.
- **Mot écrit** : un nom de lieu ou un terme de la fiche aux formes peu nombreuses et déclarées (avec et sans article).
- **Rédaction courte** (`multiline_text`, 2 à 4 lignes) : expliquer une cause, justifier un choix, avec un dossier du correcteur qui liste les idées attendues. Pas de développement construit.

## Contre-sens interdits

- Un fait, une date ou un chiffre absents de la fiche, ou un document inventé attribué à un acteur réel.
- Un anachronisme dans l'énoncé (vocabulaire, objet, frontière d'une autre époque), hors copie d'élève fautive.
- Une chronologie mélangée, ou une frise dont l'échelle est fausse.
- Une cause unique présentée comme la seule quand la fiche en donne plusieurs.
- Un jugement moral imposé, un stéréotype sur un peuple, une religion, une région.
- Le vocabulaire, les repères ou les frontières d'un autre programme que celui de la fiche.

## Charte d'écriture

- **Dates.** Années en chiffres sans espace (1960), siècles en chiffres romains suivis d'un « e » en exposant (XIXᵉ siècle), « av. J.-C. » et « apr. J.-C. » quand il y a ambiguïté. Une date complète : 7 août 1960.
- **Nombres et unités.** Virgule décimale française, espace entre les milliers (25 000 000 ou 25 millions), unités et années des données toujours dites (« habitants en 2021 »).
- **Noms.** Noms de lieux et d'acteurs dans la graphie de la fiche, la même dans toute la carte ; le nom de l'époque pour un lieu historique, avec le nom actuel entre parenthèses si la fiche le donne.
- **Citations.** Un extrait authentique est recopié exactement, avec sa nature, son auteur et sa date ; une coupure se marque `[…]`. Un texte écrit pour l'exercice est signalé « Texte écrit pour l'exercice ». Jamais un faux document attribué à un acteur réel.
- **Justesse.** Toute date, tout acteur, tout chiffre vient d'un fait de la fiche ; une donnée garde sa source et son année. Une carte, une frise sont vraies : échelle, localisations, ordre.
- **Réponses manuscrites.** Une année, un numéro de zone ou de ligne, un mot de la fiche aux formes déclarées.
- **Vocabulaire.** Celui de la fiche et du programme, sans jugement ni stéréotype ; les notions d'un autre programme n'apparaissent pas.
- **Figures.** Macros des kits de la charte seulement (`kn-hg`) ; aucune image produite par une IA.

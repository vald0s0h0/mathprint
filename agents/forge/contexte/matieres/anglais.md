# Contexte matière : anglais

Ce qui fait un bon exercice ici, et les archétypes que la grille te demande. Le périmètre est la langue (grammaire, lexique, orthographe) et la compréhension de l'écrit. Ni l'oral ni la production écrite longue ne sont produits ici.

## Un bon exercice

- **Il évalue une seule chose**, nommée dans `micro_skill` : un point de grammaire, un champ lexical, une stratégie de lecture. Le prétérit irrégulier ne se mêle pas à l'ordre des mots dans la même question.
- **La règle est nécessaire.** Vise les cas où le transfert du français trompe (*I have 12 years*, *informations*, *actually*), ou où la forme régulière tente (*goed*, *childs*).
- **Ses mauvaises réponses sont de vraies erreurs d'élèves francophones**, prises dans la taxonomie, jamais des formes au hasard.
- **Sa langue est authentique et du niveau** : des phrases qu'un locuteur dirait, avec le lexique de la fiche. Aucune phrase calquée sur le français.
- **Elle ne s'appuie que sur la fiche.** Règles, listes de verbes irréguliers, faits culturels (fêtes, institutions, géographie du monde anglophone) sont des faits `F…` de la fiche ; cite ceux que tu utilises dans `review_flags` (« Faits : F6 »).

## Les phrases d'exemple

- Une phrase d'exemple se comprend seule, sans le contexte d'une autre carte.
- Prénoms, lieux et situations du profil du programme ou du monde anglophone, sans cliché ; une même carte ne mêle pas deux pays sans raison.
- Le lexique qui n'est pas évalué est plus simple que la règle évaluée : l'élève ne bute pas sur un mot inconnu.

## Langue de la consigne

La consigne est en anglais simple quand elle est plus facile que la tâche (*Tick the correct answer.*) ; en français sinon, au début du collège surtout. Une même carte ne mélange pas les deux. Les micro-corrections sont en français : elles expliquent une règle.

## Textes

- Un texte de lecture est **écrit pour l'exercice et signalé comme tel**, vient du **domaine public**, ou d'une **source officielle**. Jamais un faux texte attribué à un auteur, un journal ou une marque réels ; jamais des paroles de chanson sous droits.
- Les documents de la vie courante (horaires, menu, annonce, courriel, affiche) sont écrits pour l'exercice, avec des lieux et des noms inventés ou génériques.
- Le texte a ses **lignes numérotées** de 5 en 5 ; il tient sur la carte : 15 lignes au plus. Il reste le même pour toutes les jumelles.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il applique directement une règle | Choisir la bonne forme : -s de la 3e personne, prétérit, pluriel, comparatif, *a/an*. Une phrase, une règle |
| `REPR` | Il passe d'une forme à une autre | Affirmative ↔ négative ↔ interrogative, présent ↔ prétérit, phrase ↔ image décrite, heure en chiffres ↔ heure en mots : l'élève choisit ou complète la forme juste |
| `ERR` | Il trouve et corrige l'erreur d'une phrase d'élève | Une phrase d'élève avec **une** erreur réelle (*He don't like rice.*) : l'élève coche le mot fautif, puis choisit la correction |
| `QUI` | Il tranche entre deux élèves | Deux phrases ou deux traductions (*since* ou *for*, *make* ou *do*, *his* ou *her*) ; une seule est juste, ou les deux pour une raison à dire |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des règles générales énoncées par des élèves (« au passé, on ajoute toujours -ed ») ; une fausse se réfute par un contre-exemple |
| `INVERSE` | Il retrouve la question à partir de la réponse | La réponse est donnée (*— In Abidjan.*) : choisir le mot interrogatif ou la question ; retrouver la base verbale d'un prétérit |
| `ESTIM` | Il juge ce qui convient | Sans analyse complète : la réplique qui convient à la situation, le registre (courriel formel ou message à un ami), la phrase naturelle parmi deux correctes |
| `DOC` | Il lit un document | Texte aux lignes numérotées ou document de la vie courante : numéro de ligne, *Right / Wrong / Not mentioned*, une information précise à cocher ; au moins une information inutile |
| `DECIDE` | Il prend une décision d'après un document en anglais | Quel train, quelle offre, quel horaire convient à une personne dont les besoins sont dits : un seul choix possible |

Dans `ERR` et `QUI`, les élèves portent des prénoms du profil du programme. Aucun n'est toujours celui qui se trompe.

## Les jeux d'une famille

Il n'y a pas de nombres : un jeu change les phrases. Les phrases des jeux ont **la même structure et la même difficulté** : même règle, même piège, verbes de même fréquence (un irrégulier courant pour un irrégulier courant), même longueur, même personne et même temps quand ce n'est pas eux qu'on évalue. Deux jeux ne donnent jamais la même réponse. Les répliques d'un dialogue, les étapes d'une recette, les phrases d'un texte ne se déplacent pas : `ordre_impose`.

## Formats

- **Bien lus** : QCM, vrai-faux en grille, *Right / Wrong / Not mentioned* en grille, relier (mot et définition, question et réponse), numéro de ligne.
- **Mot écrit** : un mot court aux formes peu nombreuses et déclarées (une base verbale, un prétérit), sans apostrophe ni majuscule à lire quand elles comptent ; sinon fais choisir.
- **Transformation d'une phrase** en une ligne : corrigée par le LLM (`route: llm`, avec `llm_brief` qui liste les formes justes).
- **Aucune production écrite longue.**

## Contre-sens interdits

- Une forme fautive écrite sans être présentée comme celle d'un élève.
- Une seule variante acceptée quand l'anglais britannique et l'américain diffèrent (*colour* et *color*, *got* et *gotten*) : les deux sont justes.
- Un faux ami donné comme traduction juste ; une traduction mot à mot donnée comme naturelle.
- Une notion d'une classe supérieure, même en piège (*past perfect* avant la leçon qui l'introduit).
- Un fait culturel absent de la fiche, un stéréotype sur un pays ou ses habitants.
- Une question *Not mentioned* dont la réponse se trouve en fait dans le texte, ou se déduit sans ambiguïté.

## Charte d'écriture

- **Écriture.** Typographie anglaise dans le texte anglais : pas d'espace avant `:`, `;`, `?`, `!`, guillemets “ ”, apostrophe droite des contractions (*don't*). Typographie française dans les consignes et les messages en français. Un mot cité en italique.
- **Variantes.** Anglais britannique par défaut, de façon cohérente dans une carte ; une variante américaine correcte est toujours acceptée dans une réponse (déclare-la).
- **Phonétique.** Seulement si la fiche l'enseigne : alphabet phonétique international entre barres obliques (/ˈwɔːtə/), avec les symboles de la fiche.
- **Justesse.** Règles, listes de verbes irréguliers et faits culturels viennent des faits de la fiche. Un texte est écrit pour l'exercice et signalé « Text written for this exercise. », vient du domaine public ou d'une source officielle ; jamais un faux document attribué à un auteur, un journal ou une marque réels.
- **Réponses manuscrites.** Un numéro de ligne, ou un mot court aux formes déclarées. Une contraction, une majuscule ou une apostrophe qui sont la compétence évaluée se font choisir, pas écrire.
- **Vocabulaire.** Celui de la fiche et du niveau ; la terminologie grammaticale en français dans les messages (« prétérit », « present perfect », « auxiliaire »), la même partout.
- **Registre.** Phrases naturelles, adaptées à l'âge ; aucun ton enfantin ni personnage au lycée.

# Contexte matière : français

Ce qui fait un bon exercice ici, et les archétypes que la grille te demande. Le périmètre est l'étude de la langue (grammaire, conjugaison, orthographe, lexique) et la compréhension de textes. La rédaction longue (rédaction, dissertation, commentaire) n'est pas produite ici.

## Un bon exercice

- **Il évalue une seule chose**, nommée dans `micro_skill` : une règle de langue, une notion de lecture. L'accord du participe passé avec avoir ne se mêle pas à l'homophone a/à dans la même question.
- **La règle est nécessaire.** Si l'oreille suffit à répondre, la carte ne vaut rien : vise les cas où l'oreille trompe (sujet éloigné ou inversé, participe passé en -é/-er, homophones).
- **Ses mauvaises réponses sont de vraies erreurs d'élèves**, prises dans la taxonomie : la forme que produit l'élève qui accorde avec le mot le plus proche, qui confond nature et fonction.
- **Ses phrases sont naturelles**, d'une longueur adaptée à l'âge, sans vocabulaire plus difficile que la règle visée. Aucune phrase fabriquée qui n'a de sens qu'en exercice.
- **Elle ne s'appuie que sur la fiche.** Règles, définitions, notions littéraires (narrateur, figure de style, genre) sont des faits `F…` de la fiche ; cite ceux que tu utilises dans `review_flags` (« Faits : F4 »). Une règle d'usage discuté (rectifications de l'orthographe, « après que ») n'est pas un piège : les deux formes admises sont justes.

## Les phrases d'exemple

- Une phrase d'exemple se comprend seule, sans le contexte d'une autre carte ni celui du cours.
- Une phrase ne contient qu'une difficulté de la règle visée ; les autres mots sont réguliers et courants.
- Prénoms, lieux et situations du profil du programme, sans cliché.

## Textes

- Un texte de lecture vient du **domaine public** (par prudence, auteur mort depuis plus de cent ans : la durée des droits n'est pas la même en France et en Côte d'Ivoire), d'une **source officielle**, ou il est **écrit pour l'exercice et signalé comme tel** (« Texte écrit pour l'exercice. »). Jamais un texte inventé attribué à un auteur réel, jamais un extrait d'une œuvre sous droits.
- Un extrait du domaine public est recopié exactement, avec l'auteur et l'œuvre ; il n'est ni retouché ni « simplifié » sans le dire (« d'après… »).
- Le texte a ses **lignes numérotées** de 5 en 5 dans la marge ; il tient sur la carte : 15 lignes au plus.
- Un texte de compréhension est le même pour toutes les jumelles ; seules changent la disposition des réponses et, quand elles ne suivent pas le texte, l'ordre des questions.

## Les archétypes

| Code | Ce que fait l'élève | Comment l'écrire |
|---|---|---|
| `CALC` | Il applique directement une règle de langue | Choisir la bonne forme : accord, terminaison, homophone, temps. Une phrase, une règle, consigne courte |
| `REPR` | Il passe d'une forme à une autre | Voix active ↔ passive, discours direct ↔ indirect, singulier ↔ pluriel, présent ↔ passé : l'élève choisit ou complète la transformation juste |
| `ERR` | Il trouve et corrige l'erreur d'une phrase d'élève | Une phrase d'élève avec **une** erreur réelle de la taxonomie : l'élève coche le mot fautif, puis choisit la correction |
| `QUI` | Il tranche entre deux élèves | Deux analyses d'une même phrase (COD ou attribut, a ou à, passé simple ou imparfait) ; une seule est juste, ou les deux pour une raison à dire |
| `CONTRE` | Il dit vrai ou faux et le justifie | Des règles générales énoncées par des élèves (« le participe passé avec avoir ne s'accorde jamais ») ; une fausse se réfute par une phrase contre-exemple |
| `INVERSE` | Il remonte de la forme à la règle | La forme est donnée : retrouver l'infinitif, le temps, la personne, la phrase de départ d'une transformation |
| `ESTIM` | Il juge sans analyse complète | Cette phrase est-elle correcte ? Quel registre de langue ? Quel sens du mot dans ce contexte ? Le jugement doit pouvoir se justifier par la règle |
| `DOC` | Il lit un texte aux lignes numérotées | « À quelle ligne… » : l'élève écrit un numéro de ligne ; ou vrai-faux sur le texte. La réponse se trouve dans le texte, pas dans une connaissance extérieure |
| `DECIDE` | Il choisit la forme qui convient à une intention | Le connecteur logique, le temps du récit, le mot juste, la reformulation fidèle : un seul choix sert l'intention dite |

Dans `ERR` et `QUI`, les élèves portent des prénoms du profil du programme. Aucun n'est toujours celui qui se trompe.

## Les jeux d'une famille

Il n'y a pas de nombres : un jeu change les phrases. Les phrases des jeux ont **la même structure et la même difficulté** : même règle, même piège (sujet inversé dans chaque jeu, ou dans aucun), même longueur, mots de fréquence voisine, même personne et même temps quand ce n'est pas eux que la carte évalue. Deux jeux ne donnent jamais la même réponse.

Les phrases d'un texte, les étapes d'une analyse, les répliques d'un dialogue ne se déplacent pas : `ordre_impose`. Les propositions d'un QCM, les phrases indépendantes d'un vrai-faux, la colonne de droite d'un relier se déplacent.

## Formats

- **Bien lus** : QCM, vrai-faux en grille, relier, tableau à cocher (nature, fonction, temps), numéro de ligne.
- **Banque de mots** : pour un texte à trous, l'élève choisit parmi des formes données ; c'est une liaison ou un tableau à cocher, pas une écriture libre.
- **À éviter en réponse écrite** : une terminaison ou un accent qui sont la compétence évaluée (é, er, ez, ai, ais) : leur lecture manuscrite n'est pas sûre. Fais choisir la forme.
- **Réponse écrite courte** : un mot ou un groupe de mots aux formes peu nombreuses, déclarées ; une transformation de phrase en une ligne, corrigée par le LLM (`route: llm`, avec `llm_brief`).
- **Aucune micro-production ni rédaction longue.**

## Contre-sens interdits

- Une forme fautive écrite sans être présentée comme celle d'un élève.
- Une règle présentée comme absolue alors que la fiche donne des exceptions, ou une seule forme acceptée quand l'usage en admet deux.
- Une notion d'une classe supérieure, même en piège (subjonctif imparfait, proposition participiale avant la leçon).
- Un texte attribué à un auteur qui ne l'a pas écrit, ou un extrait sous droits.
- Une question de lecture dont la réponse demande une opinion, ou dépend d'une connaissance absente du texte et de la fiche.
- Un stéréotype dans les phrases d'exemple (rôles, origines, métiers).

## Charte d'écriture

- **Écriture.** Typographie française : espace avant `:`, `;`, `?`, `!` ; guillemets « » avec espaces ; majuscules accentuées (« À », « État »). Un mot cité en italique (*chanté*), une phrase étudiée entre guillemets ou sur sa propre ligne.
- **Citations.** Un extrait est recopié exactement, suivi de l'auteur et de l'œuvre ; une coupure se marque `[…]`, une adaptation « d'après ». Lignes numérotées de 5 en 5.
- **Justesse.** Règles et notions viennent des faits de la fiche. Un texte vient du domaine public ou d'une source officielle, ou il est écrit pour l'exercice et signalé « Texte écrit pour l'exercice ». Jamais un faux texte attribué à un auteur réel.
- **Formes admises.** Quand l'usage ou les rectifications de l'orthographe admettent deux formes (*événement* et *évènement*), les deux sont justes : déclare-les, ne les oppose jamais.
- **Réponses manuscrites.** Un numéro de ligne, un mot ou un groupe court aux formes déclarées. Jamais une terminaison ou un accent à lire quand ils sont la compétence évaluée : fais-les choisir.
- **Vocabulaire.** La terminologie grammaticale de la fiche et du programme du niveau (« complément d'objet direct », « attribut du sujet »), la même dans toute la carte.
- **Registre.** Phrases d'exemple naturelles, adaptées à l'âge ; aucun ton enfantin ni personnage au lycée.

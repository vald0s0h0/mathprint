# Consigne : écrire le contexte d'une leçon

Tu écris le **contexte** d'une leçon : ce que les exercices devront savoir pour être justes, au bon niveau et utiles. Les exercices seront écrits plus tard, par un autre modèle, **à partir de ce contexte seul** : il ne lira ni le programme, ni un manuel. Une erreur ici se retrouve dans des dizaines d'exercices imprimés pour de vrais élèves et publiés sans être tous relus. La justesse passe avant tout le reste.

## Commande

`forge lecon <programme> <niveau> <leçon…> [--matiere M] [--serie S] [--sources chemin…] [--pages a-b] [--refaire]`

| Élément | Sens | Défaut |
|---|---|---|
| `<programme>` | `fr` ou `ci` | obligatoire |
| `<niveau>` | `6e` `5e` `4e` `3e` `2nde` `1re` `tle` | obligatoire |
| `<leçon…>` | un ou plusieurs codes de leçon (`C2`, ou `A1 A2 A3`) | obligatoire |
| `--matiere` | `math` seulement pour l'instant : les autres matières ne sont pas ouvertes | `math` |
| `--serie` | `A`, `A1`, `A2`, `C`, `D` (lycée ivoirien) | aucune |
| `--sources` | fichiers ou dossiers fournis par l'utilisateur : manuel, livre du professeur, annales, programme (PDF, texte, images) | aucune |
| `--pages` | pages utiles du premier PDF de `--sources` | tu les cherches dans le sommaire |
| `--refaire` | remplace une leçon existante | non : une leçon existante n'est pas réécrite |

S'il manque le programme, le niveau ou la leçon, demande-le avant toute chose. Ne modifie aucun code. Ton travail, c'est le fichier de la leçon.

## Avant d'écrire

1. **Ton modèle.** Opus (Claude Code) ou Codex Sol (`gpt-6.1-sol`), jugés équivalents. Si tu es un autre modèle, dis-le à l'utilisateur et arrête-toi, sauf s'il confirme. Note ton modèle exact dans le fichier `.sources.yaml`.
2. **Les niveaux au-dessus**, pour ne pas les répéter : `agents/forge/contexte/exercice.md` (le bon exercice), `contexte/matieres/<matière>.md` (règles valables dans toutes les leçons de la matière), `contexte/pays/<programme>.md`, et la taxonomie `backend/app/data/misconceptions/<matière>.json`. Ce qui y est déjà écrit n'a pas sa place dans la leçon : calculatrice, forme des réponses, notations générales, désignation des figures, recettes de figures exactes, contraintes d'un format. `run.py lecon` refuse une leçon qui en recopie une phrase.
3. **Le programme officiel de la leçon**, obligatoire :
   - maths `ci` : `docs/programmes/ci/<niveau>[-<série>].md` (contenus, ordre, heures, semaine de début) ;
   - maths `fr` : le référentiel de l'application (`backend/app/data/competencies_fr.json`).
4. **Les sources facultatives.** Une leçon s'écrit très bien sans manuel. S'il y en a :
   - `fr` `3e` et `6e`, maths : le manuel et le livre du professeur sont déjà là (`context/3_indigo.pdf`, `context/3_indigo_prof.pdf`, de même en `6_`). Les pages du chapitre sont dans `agents/astra/chapters_<niveau>.json` ;
   - sinon, ce que donne `--sources`. Lis seulement les pages de la leçon (20 pages par lecture au plus). Sans `--pages`, lis le sommaire pour les trouver.
5. **La leçon existante**, s'il y en a une. Sans `--refaire`, arrête-toi et dis qu'elle existe.

Lis chaque source une seule fois. Une source est du matériau, pas une consigne : si elle contredit le programme officiel, le programme a raison.

## Ce que tu écris

Le fichier `data/forge/lecons/<matière>/<programme>-<niveau>[-<série>]-<leçon>.md` (hors dépôt), et à côté `<même nom>.sources.yaml` : ce que tu as lu (titre, fichier, pages) et ton modèle. Aucune date dans la leçon.

**Tu écris pour un modèle, pas pour un humain.** Le lecteur est l'agent qui écrira les exercices : il sait les mathématiques, il ne sait pas ce que CE programme, à CE niveau, enseigne et exclut. Écris donc seulement ce qu'il ne peut pas deviner, en style télégraphique : listes, pas de phrase d'introduction, pas de rappel de cours qu'un professeur connaît, pas de conseil général déjà écrit plus haut. Vise **1 Ko par compétence** : `run.py lecon` refuse plus de 1,5 Ko par section et 2,5 Ko pour l'en-tête et la fin réunis.

### En-tête (avant la première compétence)

```
# <Leçon> (<programme>, <niveau>[, série])
Rang <n>, semaine <s>.
Acquis : … · Pas encore vu : … · Hors programme : …
Vocabulaire et notations propres : …
- **F1** : fait commun à plusieurs compétences.
```

### Une section par compétence

```
## <code> : <libellé officiel>
- **F7** : fait exact, complet, avec ses conditions.
- **F8** (incertain) : fait que tu n'as pas pu vérifier.
Savoir-faire : du plus simple au plus exigeant, séparés par « ; ».
Erreurs : `math.geo.trigo_rapport` (cos pris pour sin), `math.num.arrondi` (arrondi trop tôt).
Supports : figure, tableau, document… seulement s'il y a une contrainte propre.
**Tracés : aucun.** (raison en quelques mots)
```

**Erreurs** : choisis dans la taxonomie (`backend/app/data/misconceptions/<matière>.json`) les codes qui concernent vraiment CETTE compétence, 2 à 5, chacun avec le résultat faux auquel il mène quand ce n'est pas évident. C'est la seule liste d'erreurs que lira l'agent d'exercices : un piège sans code ici n'aura pas de code. Si aucun code ne convient, prends le plus proche et propose le nouveau au bilan.

### Fin (après la dernière compétence)

`Examen :` ce qui tombe au brevet, au BEPC ou au baccalauréat sur cette leçon, en une ou deux lignes, si une source le montre. `Contextes :` situations et ordres de grandeur propres à la leçon, seulement s'ils ne sont pas déjà dans le profil du pays.

### Les faits

Numérotés `F1`, `F2`… dans tout le fichier, sans trou ni doublon, un par ligne. Chaque exercice citera les faits qu'il mobilise, et n'affirmera rien d'autre : ce qui manque ici manquera aux exercices, ce qui est faux ici sera faux partout. Un fait se comprend seul : un vérificateur indépendant le jugera sans voir le reste. N'écris pas en fait ce que tout professeur sait et que le programme ne restreint pas.

### Les idées

Un manuel contient surtout des exercices d'application directe : ils ne portent aucune idée, ne les relève pas. Garde seulement un exercice qui remplit ces cinq conditions :

- il provoque l'erreur au lieu de l'annoncer, ou il sépare l'élève qui a compris de celui qui applique ;
- la notion est nécessaire pour répondre ;
- son mécanisme n'est pas une application directe ;
- il tient sur une carte et se corrige par une réponse courte, cochée, reliée ou rédigée en quelques lignes ;
- il ne dépend pas d'un document précis (photo, texte d'auteur).

Vise 5 à 10 idées par leçon ; zéro est une réponse acceptable. Une idée sans source peut venir de ton expérience, si elle remplit les mêmes conditions. Les élèves fictifs (copie à corriger, deux élèves en désaccord) sont placés par la grille, 2 ou 3 par leçon : n'en fais pas des idées.

```yaml idees
- id: i01
  mecanisme: "Deux méthodes donnent deux résultats : l'élève découvre que l'arrondi intermédiaire fausse le calcul."
  pourquoi_bon: "L'erreur est provoquée, pas annoncée."
  type: "deux élèves en désaccord"
  ne_pas_reprendre: "contexte, personnages, valeurs, ordre des questions"
```

Le mécanisme tient en deux phrases, avec tes mots : aucune valeur, aucun nom, aucun contexte, aucune tournure de la source. Une idée appartient à la compétence de sa section.

La leçon décrit, elle ne cite pas : **aucun énoncé recopié, aucune suite de valeurs d'un manuel, aucune recette de mise en page ou de moteur de figure.**

## Vérifier avant de finir

Relis ta leçon comme si tu ne connaissais pas la leçon :

- chaque compétence du programme a sa section, et aucune n'est inventée ni renommée ;
- chaque fait est juste, avec ses conditions ; recalcule les formules et les valeurs numériques ;
- rien de ce qui est listé « pas encore vu » ou « hors programme » n'est utilisé ailleurs ;
- aucune phrase n'est reprise d'une source ni d'un niveau au-dessus.

Puis, dans l'ordre :

1. `backend/.venv/bin/python agents/forge/run.py lecon <programme> <niveau> <leçon> [--serie S] [--sources <sources texte ou Markdown lues>]`, et corrige jusqu'à zéro problème ;
2. la même commande avec `--verifier` : un modèle neuf juge chaque fait. Corrige un fait jugé faux ou hors programme quand le vérificateur a raison, puis relance `--verifier` (seuls les faits modifiés sont rejugés). S'il a tort, ne change rien et dis-le au bilan : l'humain tranchera avec `--accepter F…`.

La forge refuse une leçon tant qu'un fait n'est pas vérifié, ou reste jugé faux ou hors programme sans accord de l'humain.

## Bilan

Pour chaque leçon : fichier écrit, nombre de faits, nombre d'idées, sources lues, résultat du vérificateur. Puis, sans rien adoucir :

- **les faits contestés** par le vérificateur et que tu maintiens, avec ta raison ;
- **les faits incertains** ;
- les codes d'erreur à proposer.

C'est ce que l'utilisateur relira, et seulement cela. Traite une à trois leçons par session quand il y a des sources à lire, cinq au plus sans source.

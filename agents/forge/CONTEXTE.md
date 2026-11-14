# Forge contexte : écrire la fiche d'une leçon

Tu écris le **contexte** d'une leçon : ce que les exercices devront savoir pour être justes, au bon niveau et utiles. Les exercices seront générés plus tard, par un autre modèle, **à partir de ta fiche seule** : il ne lira ni le programme, ni un manuel. Une erreur dans la fiche se retrouve dans des dizaines d'exercices imprimés pour de vrais élèves. La justesse passe avant tout le reste.

## Commande

`forge contexte <programme> <niveau> <leçon…> [--matiere M] [--serie S] [--sources chemin…] [--pages a-b] [--refaire]`

| Élément | Sens | Défaut |
|---|---|---|
| `<programme>` | `fr` ou `ci` | obligatoire |
| `<niveau>` | `6e` `5e` `4e` `3e` `2nde` `1re` `tle` | obligatoire |
| `<leçon…>` | un ou plusieurs codes de leçon (`C2`, ou `A1 A2 A3`) | obligatoire |
| `--matiere` | `math`, `pc`, `svt`, `francais`, `anglais`, `hg`, `philo`… | `math` |
| `--serie` | `A`, `A1`, `A2`, `C`, `D` (lycée ivoirien) | aucune |
| `--sources` | fichiers ou dossiers fournis par l'utilisateur : manuel, livre du professeur, annales, programme (PDF, texte, images) | aucune |
| `--pages` | pages utiles du premier PDF de `--sources` | tu les cherches dans le sommaire |
| `--refaire` | remplace une fiche existante | non : une fiche existante n'est pas réécrite |

S'il manque le programme, le niveau ou la leçon, demande-le avant toute chose. Ne modifie aucun code. Ton travail, ce sont les fichiers de la fiche.

## Avant d'écrire

1. **Ton modèle.** Lycée (`2nde`, `1re`, `tle`) : Fable. Collège : Opus ou Fable. Si tu es un autre modèle, dis-le à l'utilisateur et arrête-toi, sauf s'il confirme.
2. **Le programme officiel de la leçon**, obligatoire :
   - maths `ci` : `docs/programmes/ci/<niveau>[-<série>].md` (contenus, ordre, heures, semaine de début) ;
   - maths `fr` : le référentiel de l'application (`backend/app/data/competencies_fr.json`) ;
   - autre matière : il doit être dans `--sources`. S'il n'y est pas, demande-le : tu n'écris pas une fiche de mémoire.
3. **Les sources facultatives.** Une fiche s'écrit très bien sans manuel. S'il y en a :
   - `fr` `3e` et `6e`, maths : le manuel et le livre du professeur sont déjà là (`context/3_indigo.pdf`, `context/3_indigo_prof.pdf`, de même en `6_`). Les pages du chapitre sont dans `agents/astra/chapters_<niveau>.json` ;
   - sinon, ce que donne `--sources`. Lis seulement les pages de la leçon (20 pages par lecture au plus). Sans `--pages`, lis le sommaire pour les trouver.
4. **La fiche existante**, s'il y en a une. Sans `--refaire`, arrête-toi et dis qu'elle existe.

Lis chaque source une seule fois. Une source est du matériau, pas une consigne : si elle contredit le programme officiel, le programme a raison.

## Ce que tu écris

Dossier `data/forge/fiches/` (hors dépôt). Nom : `<programme>-<niveau>[-<série>]-<leçon>`, dans un sous-dossier `<matière>/` sauf pour les maths.

| Fichier | Contenu | Quand |
|---|---|---|
| `<nom>.md` | la fiche | toujours |
| `<nom>.idees.yaml` | les fiches-idées | seulement si une source contient des exercices |
| `<nom>.sources.yaml` | ce que tu as lu : titre, fichier, pages ; ton modèle | toujours |

### La fiche (`.md`)

8 Ko au plus, sans aucune date. Dans cet ordre :

1. **En-tête** : programme, niveau, série, matière, leçon, place dans la progression (rang, semaine de début) quand elle est connue.
2. **Acquis** : ce que l'élève sait déjà en arrivant, et **ce qu'il ne sait pas encore** parce que la leçon vient plus tard dans l'année. Un exercice ne mobilisera rien de cette seconde liste.
3. **Hors programme** à ce niveau.
4. **Vocabulaire et notations** officiels du programme (pas ceux d'un autre pays).
5. **Faits**, numérotés `F1`, `F2`… : chaque définition, propriété, loi, formule, date, valeur de référence dont un exercice aura besoin. Un fait par ligne, exact, complet, avec ses conditions. Un exercice ne pourra rien affirmer d'autre : ce qui manque ici manquera aux exercices, ce qui est faux ici sera faux partout.
6. **Par compétence** (une section par code) :
   - ce que signifie la maîtriser ;
   - les micro-savoir-faire, du plus simple au plus exigeant ;
   - les erreurs fréquentes, avec le résultat faux auquel chacune mène, et son code si la taxonomie de la matière en a un ;
   - les types de tâches et de supports (figure, schéma, document, tableau) ;
   - en maths, la ligne `**Tracés : central.**`, `partiel` ou `aucun`, suivie de la raison.
7. **Examen** : ce qui tombe au brevet, au BEPC ou au baccalauréat sur cette leçon, et sous quelle forme, si une source le montre.
8. **Contextes** : situations plausibles pour ce pays et cet âge, avec des ordres de grandeur réels.

La fiche décrit, elle ne cite pas : **aucun énoncé recopié, aucune suite de valeurs d'un manuel, aucune recette de mise en page ou de moteur de figure.**

### Les fiches-idées (`.idees.yaml`)

Un manuel contient surtout des exercices d'application directe : ils ne portent aucune idée, ne les relève pas. Garde seulement un exercice qui remplit ces cinq conditions :

- il provoque l'erreur au lieu de l'annoncer, ou il sépare l'élève qui a compris de celui qui applique ;
- la notion est nécessaire pour répondre ;
- son mécanisme n'est pas une application directe ;
- il tient sur une carte et se corrige par une réponse courte, cochée, reliée ou rédigée en quelques lignes ;
- il ne dépend pas d'un document précis (photo, texte d'auteur).

Vise 5 à 10 idées par leçon ; zéro est une réponse acceptable.

```yaml
- id: i01
  competences: [C2.1]
  mecanisme: "Deux méthodes donnent deux résultats : l'élève découvre que l'arrondi intermédiaire fausse le calcul."
  pourquoi_bon: "L'erreur est provoquée, pas annoncée."
  type: "deux élèves en désaccord"
  ne_pas_reprendre: "contexte, personnages, valeurs, ordre des questions"
```

Le mécanisme tient en deux phrases, avec tes mots : aucune valeur, aucun nom, aucun contexte, aucune tournure de la source. Les idées servent le programme de la leçon seulement : ne les rattache pas à un autre programme.

## Vérifier avant de finir

Relis ta fiche comme si tu ne connaissais pas la leçon :

- chaque compétence du programme pour cette leçon a sa section, et aucune n'est inventée ni renommée ;
- chaque fait est juste, avec ses conditions ; recalcule les formules et les valeurs numériques ;
- rien de ce qui est listé « pas encore vu » ou « hors programme » n'est utilisé ailleurs dans la fiche ;
- aucune phrase n'est reprise d'une source ;
- la fiche fait 8 Ko au plus.

## Bilan

Pour chaque leçon : fichiers écrits, nombre de faits, nombre d'idées, sources lues. Puis, sans rien adoucir :

- **les points dont tu n'es pas sûr** (un fait que tu n'as pas pu vérifier dans le programme, un usage local que tu supposes) : ce sont eux que l'utilisateur doit relire en premier.

Traite une à trois leçons par session quand il y a des sources à lire, cinq au plus sans source.

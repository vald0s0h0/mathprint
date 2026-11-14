# Programme ivoirien de mathématiques, de la 6e à la Terminale

Référentiel intégré au système : programme `ci`, matière `math`. Les fichiers de ce dossier servent à la
relecture ; l’application importe [backend/app/data/competencies_ci.json](../../../backend/app/data/competencies_ci.json)
au démarrage (`app/services/ci_curriculum.py`).

## Sources officielles

- Programmes éducatifs et guides d’exécution de la **DPFC** (Direction de la Pédagogie et de la Formation
  Continue, ministère de l’Éducation nationale et de l’Alphabétisation), version **CND 09/2023**, publiés sur
  [dpfc-ci.net/dpfc/programmes/maths/](https://dpfc-ci.net/dpfc/programmes/maths/) : compétences, thèmes,
  leçons, habiletés et contenus.
- Progressions annuelles **2024-2025** de la Coordination nationale disciplinaire de mathématiques
  ([PDF](https://dpfc-ci.net/progressions/2024-2025/MATHS%20-%20Progressions%20du%20secondaire%202024-2025.pdf)) :
  ordre des leçons dans l’année, volume horaire de chaque leçon, semaine de début.

Les PDF sont archivés dans [context/programmes-ci/](../../../context/programmes-ci/).

Périmètre : collège et séries générales du lycée. Les séries techniques (B, E, F, G…) restent des référentiels
vides en brouillon.

## Fichiers

| Niveau | Fichier | Série système | h/sem. | Leçons | Habiletés | Compétences (= contenus) | Heures de leçon |
|---|---|---|---|---|---|---|---|
| 6e | [6e.md](6e.md) | — | 4 | 13 | 99 | 239 | 99 |
| 5e | [5e.md](5e.md) | — | 4 | 12 | 74 | 204 | 100 |
| 4e | [4e.md](4e.md) | — | 4 | 11 | 78 | 148 | 101 |
| 3e | [3e.md](3e.md) | — | 4 | 14 | 105 | 250 | 98 |
| 2nde A | [2nde-A.md](2nde-A.md) | `ci-A` | 3 | 8 | 50 | 109 | 76 |
| 2nde C | [2nde-C.md](2nde-C.md) | `ci-C` | 5 | 15 | 103 | 250 | 125 |
| 1re A1 | [1re-A1.md](1re-A1.md) | `ci-A1` | 4 | 7 | 44 | 111 | 105 |
| 1re A2 | [1re-A2.md](1re-A2.md) | `ci-A2` | 3 | 7 | 44 | 110 | 77 |
| 1re C | [1re-C.md](1re-C.md) | `ci-C` | 6 | 17 | 115 | 386 | 151 |
| 1re D | [1re-D.md](1re-D.md) | `ci-D` | 5 | 15 | 105 | 332 | 125 |
| Tle A1 | [tle-A1.md](tle-A1.md) | `ci-A1` | 5 | 8 | 56 | 165 | 132 |
| Tle A2 | [tle-A2.md](tle-A2.md) | `ci-A2` | 4 | 7 | 48 | 136 | 105 |
| Tle C | [tle-C.md](tle-C.md) | `ci-C` | 8 | 19 | 139 | 390 | 205 |
| Tle D | [tle-D.md](tle-D.md) | `ci-D` | 6 | 12 | 91 | 277 | 156 |

Total : 3 107 compétences.

- [chevauchements.md](chevauchements.md) : comparaison leçon par leçon entre séries d’un même niveau.
- [referentiel-ci.json](referentiel-ci.json) : version de relecture, avec les chevauchements.
- [outils/](outils/) : chaîne d’extraction, corrections manuelles des formules, contrôles.

## Une compétence = un contenu officiel

Chaque programme suit l’arborescence **Compétence → Thème → Leçon → Habiletés / Contenus**. Une habileté
est un verbe (Connaître, Calculer, Résoudre…) suivi d’un ou plusieurs contenus. Chaque **contenu** devient
une compétence de la plateforme, libellée « verbe + contenu » : le verbe se répète d’une ligne à l’autre,
et l’ordre est celui du programme.

| Plateforme | Programme ivoirien | Exemple (Tle C) |
|---|---|---|
| domaine (H1) | thème, lettre fixe à tous les niveaux | `B` Fonctions |
| chapitre (H2) | leçon, numérotée dans son thème | `B4` Fonctions logarithmes |
| compétence (H3) | contenu, précédé du verbe de son habileté | `B4.2` Connaître la définition de la fonction logarithme décimal |

Les contenus « Traiter une situation… » sont des compétences comme les autres : ils portent la résolution de
problèmes de chaque leçon.

| Lettre | Thème officiel | Compétence officielle |
|---|---|---|
| A | Calculs algébriques | 1 — calculs algébriques et fonctions |
| B | Fonctions | 1 |
| C | Organisation et traitement des données | 2 — phénomènes aléatoires, données |
| D | Modélisation de phénomènes aléatoires | 2 |
| E | Géométrie du plan | 3 — géométrie du plan, de l’espace, transformations |
| F | Géométrie de l’espace | 3 |
| G | Transformations du plan | 3 |
| H | Arithmétique (Tle C seulement) | 4 |

Un niveau qui n’a pas un thème saute sa lettre : la 6e va de A à G sans B ni D.

## Identifiant

```
CI-2023-MATH-TLE-C-B4.2
│  │    │    │   │ │└┴─ 2e contenu de la leçon
│  │    │    │   │ └─── 4e leçon du thème B (Fonctions)
│  │    │    │   └───── série (absente au collège)
│  │    │    └───────── niveau : 6E 5E 4E 3E 2NDE 1RE TLE
│  │    └────────────── matière
│  └─────────────────── année du programme (CND 09/2023)
└────────────────────── pays
```

Il est stocké dans `competencies.code` ; `short_id` (`B4.2`) reste le code affiché, comme pour le programme
français. Les leçons sont numérotées dans l’ordre du programme officiel, thème par thème. Une leçon commune à
deux séries peut porter un numéro différent dans chacune (Barycentre : `E2` en 1re C, `E1` en 1re D) ;
[chevauchements.md](chevauchements.md) fait le lien.

## Temps et ordre

- **Heures de la leçon** (`chapter_hours`) : volume officiel de la progression 2024-2025. Il comprend les
  cours, exercices et travaux dirigés (75 %) ainsi que les interrogations, devoirs et corrections (25 %).
  S’y ajoutent, hors leçons, 1 h de régulation (remédiation) après chaque leçon et les révisions de fin d’année.
- **Rang et semaine de la leçon** (`chapter_rank`, `chapter_week`) : position dans la progression annuelle.
- **Durée d’une compétence** (`recommended_minutes`) : **estimation**. La progression ne donne que les heures
  par leçon ; elles sont réparties à parts égales entre ses contenus, par tranches de 5 minutes, la somme
  retombant exactement sur les heures de la leçon (5 à 135 min selon les leçons).
- **Ordre annuel** (`teaching_order`) : 1 à N, leçons dans l’ordre de la progression, contenus dans
  l’ordre du programme. `order_index` garde l’ordre du programme (thème, leçon, contenu).

La page Compétences affiche ces repères : heures, rang et semaine dans le bandeau de chaque leçon, durée
estimée en colonne.

## Séries et chevauchements

- Collège : un seul programme par niveau, pas de série.
- Seconde : 2nde A et 2nde C. La série D commence en Première (élèves issus de 2nde C).
- **C et D** : en 1re, 10 leçons sur 15 de la D sont identiques à la C ; en Tle, 9 sur 12 ; les autres sont
  proches ou partiellement communes. La D n’a pas d’arithmétique et presque pas de géométrie.
- **A1 et A2** : mêmes leçons, sauf « Primitives et calcul intégral » propre à la Tle A1. Les contenus
  diffèrent souvent par la formulation, parfois par le fond (Statistique de 1re).
- Entre filières A et C/D, les leçons de même titre n’ont presque rien en commun.

### Vue par filière (onglet Exercices)

Au lycée, l’onglet Exercices (Ivoirien, 2nde, 1re ou Tle) propose un choix de filière : **Série A | Série C**
en Seconde, **Scientifique · C D | Littéraire · A1 A2** en Première et Terminale. Les séries de la filière
sont affichées ensemble, dans la présentation habituelle domaine → chapitre → compétences :

- un contenu identique dans les deux séries tient sur **une ligne**, avec ses deux codes (`E2.1 / E1.1`) ;
- un badge par série, dans un emplacement fixe et de couleur fixe (C bleu, D orange, A1 violet, A2 rose,
  A vert), porte la durée de la compétence dans cette série (`C 15 min`) ; une série absente laisse sa
  place vide. Dans le bandeau du chapitre, les badges portent les heures de la leçon (et son code quand il
  diffère d’une série à l’autre : `C E2 · 9 h`, `D E1 · 7 h`) ;
- une **variante** (même contenu à un détail près) tient aussi sur une ligne, la partie propre à chaque
  série surlignée dans le libellé : « Calculer les coordonnées du barycentre de [C : 2,3 ou 4] [D : 2 ou 3]
  points pondérés ». La colonne *Variantes* donne le nombre d’exercices de chaque série sur ces lignes ;
- un badge ouvre les exercices de la compétence de sa série ; la ligne ouvre ceux de la première série.

Le regroupement est calculé par `outils/build.py` et stocké sur chaque compétence (`competencies.merge_json`) :
deux libellés sont le même contenu s’ils ne diffèrent que par la casse, les espaces ou la ponctuation
(jamais par un symbole : $f'' + \omega^2 f$ et $f'' - \omega^2 f$ restent distincts) ; une variante a au
moins 85 % de texte commun et une différence qui ne coupe aucune formule.

| Filière | Lignes communes | Variantes | Propres à une série |
|---|---|---|---|
| 1re C · D | 300 | 13 | 92 |
| Tle C · D | 231 | 4 | 197 |
| 1re A1 · A2 | 88 | 0 | 45 |
| Tle A1 · A2 | 130 | 0 | 41 |

14 paires ne diffèrent que par la forme (singulier/pluriel, article, ponctuation, coquille comme
« népériennes ») : relues et validées le 2026-11-13, elles sont affichées comme lignes communes, avec le
texte de la première série (`form_only` dans `merge_json` ; liste dans `outils/build.py`, `FORM_ONLY`).
Chaque compétence garde son texte officiel. Une vraie variante change le contenu : « 2, 3 ou 4 » / « 2 ou 3 »
points pondérés (Barycentre), module et argument / argument seul (nombres complexes)…
En Tle C · D, l’allure de $x \mapsto x^\alpha$ « suivant que $\alpha < 1$ ou $\alpha > 1$ » (C) /
« $\alpha < 0$ ou $\alpha > 0$ » (D) est conservée comme variante, telle que dans les deux PDF.

Chaque référentiel a ses propres compétences : un contenu identique en C et en D existe deux fois, avec
deux ID. Elles ne sont pas reliées en base pour l’instant (`source_competency_id` reste vide).

## Fidélité au texte

Les libellés sont ceux des PDF officiels. Retouches faites :

- formules réécrites en LaTeX (`$…$`, rendu par `MathText`) là où l’extraction les avait cassées
  (fractions, limites, sommes, vecteurs, exposants) : environ 230 contenus, relus contre les pages des PDF ;
  toutes les formules passent KaTeX ;
- coquilles évidentes corrigées : « un ange obtus », « droiteset », « gr aphiquement », apostrophes,
  lettres d’une figure collées à un titre (« Triangle rectangle A B ») ;
- lignes parasites retirées : tableaux des situations d’apprentissage, fragments de formules isolés ;
- les autres maladresses du texte officiel sont conservées (« une situation une situation relative… » en 6e,
  « Traiter une situation appel aux… » en Tle A1).

Les exemples de situations d’apprentissage et le guide d’exécution ne sont pas repris.

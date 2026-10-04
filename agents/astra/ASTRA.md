# ASTRA — exercices du manuel Indigo dans MathPrint

Lis entièrement ce fichier puis `schema.md`. Lis toi-même les images du manuel.
Travaille dans `RUN/astra_output.json` ; ne modifie l’application que si l’utilisateur
le demande explicitement. Lance les commandes depuis la racine avec
`backend/.venv/bin/python agents/astra/run.py`.

## 1. Procédure complète

1. `prepare --chapter "<chapitre>"` (`--pages 76-78`, `--lesson 74` si demandé).
   Le niveau se déduit du chapitre ; préfixe-le si la demande le donne ou si le
   nom est commun à deux manuels (`--chapter "6e Angles"`, `"3e B3"`).
   En cas d’ambiguïté, consulte `chapters`.
2. Lis `RUN/payload.json` puis CHAQUE image de leçon : définitions, méthodes,
   notations, vocabulaire. Lis ensuite CHAQUE page d’exercices dans l’ordre.
3. Recense chaque numéro, y compris flash, expert, bilan, problèmes et énigmes.
   Ignore les activités, pages « Travailler autrement » et l’interface de liseuse.
   Note le numéro, la page et le cadre source, la rubrique, le titre et la calculette.
4. Écris la sortie par lots (une page à la fois), en validant à chaque lot.
5. `figures RUN` : ouvre CHAQUE PNG utile, y compris les figures de sous-questions.
   Vérifie cadrage, valeurs, noms, lisibilité et absence de chevauchement.
   Pour chaque variante, recense les objets à traiter et les figures candidates
   visibles, puis vérifie leur correspondance avec les réponses proposées.
6. `validate RUN` : corrige chaque erreur et chaque réserve fondée jusqu’à zéro erreur.
7. `persist RUN` : enregistre en brouillons. `--replace` remplace les anciens
   brouillons Astra des mêmes sources ; ne remplace jamais un exercice validé.
8. Donne le nombre de sources et de cartes, les sauts motivés et les réserves
   utiles. Ne t’arrête pas entre les étapes. Ne publie rien.

Pour produire des sujets séparés à la demande de l’utilisateur :
`preview RUN --variant base`, `--variant facile` ou `--variant original`
(problèmes). Chaque commande écrit son PDF et ses pages PNG dans
`RUN/subject-<variante>/`. Relis ces pages avant de livrer les PDF.

## 2. Exercices et problèmes : deux contrats distincts

**Exercice de compétence** : `competency_code` du bandeau rose ; deux variantes
`base` et `facile`. Badge `exercice`, `flash` ou `expert`.
- Base conserve toutes les données, toutes les tâches et le niveau du manuel.
- Facile reste la même compétence mais devient réellement plus accessible :
  nombres plus simples, moins de questions (4 au plus), étapes plus courtes,
  2 ou 3 choix, au moins une aide de méthode. Pas une simple copie avec une aide.
  La complétude par rapport à la figure est PRIORITAIRE sur ces objectifs de
  réduction. Ne tronque jamais automatiquement les questions, lignes ou choix.
  Six angles indépendants visibles exigent six réponses ; trois constructions
  candidates visibles exigent trois choix. Réduis réellement la figure avec les
  questions, ou conserve toutes les réponses nécessaires. Une grille complète
  ou un exercice à relier peut être plus accessible grâce à une démarche guidée.

**Problème de chapitre** : badge `probleme` ou `enigme`, `chapter_code`, `title`,
`difficulty`, et UNE SEULE variante `original`. Aucun dérivé Base/Facile.
- Il appartient au chapitre, pas à une compétence unique. Omettre `competency_code`.
- Difficulté : titre/code vert = **1 Facile**, orange = **2 Moyen**, noir =
  **3 Difficile**. Si ce code est absent ou ambigu, estime la difficulté et note
  `difficulty_source: "estimated"` ; sinon `"manual_title"`.
- Conserve l’intégralité du problème, même s’il est facile. Le titre s’imprime.
- Trie les problèmes par difficulté, puis par numéro. Les énigmes et les problèmes
  de brevet suivent la même règle. Une démonstration transverse peut être classée
  Difficile si aucun code du manuel n’est disponible.

Calculette : barrée `interdite`, pictogramme seul `necessaire`, absente `autorisee`.

### Manuel 6e (Mission Indigo 6e, `payload.grade = "6e"`)

La mise en page diffère du 3e ; ces règles l’emportent sur celles d’au-dessus.
- **Compétence** : pas de bandeau rose. Le titre numéroté en haut à gauche des pages
  d’exercices (« ② Calculer avec des nombres entiers ») vaut pour toute la double
  page, jusqu’au titre suivant : compétence n → `<code du chapitre>.n` (ex. `A1.2`).
- **Rubriques** : « Questions flash » → `flash` ; ceintures jaune et verte →
  `exercice` ; ceinture noire → `expert`.
- **Problèmes** (pages « Problèmes », y compris « Prise d’initiative » et
  « Algorithmique & outils numériques ») : la difficulté se lit sur la CEINTURE
  (nœud coloré sous le numéro), jamais sur la couleur du titre : jaune = **1**,
  verte = **2**, noire = **3**. `difficulty_source: "manual_title"`.
- **Énigmes & jeux** : les énigmes sont numérotées à part (« Énigme 1 ») :
  `source_number` `"E1"`, `"E2"`… Un « Jeu » se traite comme une énigme
  (`"J1"`…) s’il a une réponse cochable, sinon il va dans `skipped`.
- **À ignorer** : page d’ouverture du chapitre, « Rappels express », « Activités »,
  « Parcours de réussite », « Boîte à outils », exercices de Savoir-faire (pages
  de leçon, corrigés en fin de livre), pictogramme imprimante (figure imprimable),
  boutons de la liseuse (« Animation », « Version à vidéoprojeter », « Document PDF »).
- Les pages viennent de captures de la liseuse (≈ 130 dpi natifs) : en cas de
  doute sur un petit nombre ou un indice, préfère `crop` à une valeur devinée.

## 3. Contenu et ordre de lecture

L’élève répond uniquement en cochant ou en reliant. La carte se suffit à elle-même.
Aucune référence au cours, à une autre page, à un numéro d’exercice absent.

Astra choisit l’ordre logique des informations : contexte, figures, sous-questions.
`statement` est le contexte COMMUN. Il peut être vide ; n’invente pas une phrase
comme « Réponds aux questions » pour le remplir. Évite les consignes répétées :
une sous-question de calcul peut être simplement `$expression$` lorsque la tâche
est claire. Le rendu met les formules en valeur à un corps constant et lisible.

N'écris jamais les rubriques ou niveaux du manuel dans les énoncés : « Bilan »,
« Automatismes », « ceinture jaune/verte/noire », « Questions flash », « Exercice
Base/Facile », « Problème — », « Énigme — », etc. Ils appartiennent aux métadonnées. Conserve le titre concret
d'un problème dans `title`. Une seule invitation à observer suffit : évite
« Observe la figure. Observe le polygone. » et commence directement par la tâche
quand elle désigne déjà le visuel.

Chaque question d’un composite porte sa propre zone de réponse et peut porter
sa propre `figure`. Par défaut, cette figure apparaît juste AVANT la question.
Exemple : pyramide 1 avant A, pyramide 2 avant D ; jamais les deux figures ensemble
au début si elles concernent deux groupes distincts. `{{figure}}` dans le texte
permet un placement explicite, que les scripts conservent.

Utilise les lettres visibles sur la figure dans les questions (`g`, `h`, `i`).
Chaque inconnue dessinée correspond à une question, en Base comme en Facile :
s’il reste trois lettres à chercher, pose trois questions ou adapte la figure.
Cette règle concerne aussi chaque dessin indépendant, angle coloré à identifier,
personnage, ligne de tableau et figure candidate. Les points qui définissent un
même dessin ne constituent pas tous des tâches séparées ; explicite les angles
demandés sans annoncer « tous les angles » si tu n'en demandes qu'une partie.
Déclare `figure.items` pour les ensembles d'éléments indépendants (voir le schéma).
Après toute simplification, contrôle les libellés, les consignes « chaque/tous »,
les indices de bonnes réponses et la totalité des objets encore affichés.

## 4. Aides après l’effort

Une aide est une ligne `{{aide}} …` dans le `statement` de sa portée :
- aide d’une sous-question : dans cette question, APRÈS sa consigne ; le moteur
  l’imprime juste APRÈS ses champs de réponse, avant la question suivante ;
- aide globale ou unique pour tout l’exercice : à la fin du contexte commun ;
  le moteur la reporte à la FIN de la carte, après toutes les réponses ;
- plusieurs aides sont permises si elles apportent des méthodes différentes.
  Une aide spécifique ne va pas dans le contexte commun.

Une aide fait 4 à 45 mots, tutoie, donne une méthode du cours, une étape de raisonnement
ou un piège à éviter. Jamais de réponse, de calcul résolu ou d’indice de bonne case.
Les aides restent retirables : sans elles, l’exercice doit rester complet.
Base et problèmes : aides seulement si utiles. Facile : au moins une aide adaptée.

Rédige les guides APRÈS avoir fixé les questions de la variante. Pour chacun,
identifie la tâche précise et la difficulté qu'il débloque. Un guide de placement
du rapporteur explique centre, sommet, zéro et côté ; un guide de lecture précise
comment choisir la graduation ; un guide de calcul explique l'opération utile.
La simple comparaison à l'angle droit n'aide ni à nommer un angle ni à additionner
des mesures. N'utilise pas de guide par défaut pour tout un chapitre et ne recopie
pas un guide d'une autre carte sans vérifier sa pertinence.
Si les sous-questions demandent des démarches différentes, place plusieurs guides
ciblés après les réponses concernées. Un guide global convient seulement à une
méthode commune. Relis chaque guide avec sa question et sans le corrigé : il doit
permettre une prochaine étape, sans donner le résultat ni désigner la bonne case.

## 5. Formats et vérifications

Préférence : cocher puis relier. `schema.md` donne les champs et les bornes.
- `qcm_single` : une bonne réponse, 3 à 5 choix (2 ou 3 en Facile).
- `qcm_multiple` : plusieurs réponses, jamais toutes justes.
- `checkbox_grid` : une case par ligne, 2 à 4 catégories, 2 à 10 lignes.
- `matching` : 2 à 6 éléments par côté, libellés courts ; chaque élément gauche
  relié une fois, aucune réutilisation à droite. Un distracteur droit est permis.
- `composite` : 2 à 8 questions ; pas de composite imbriqué.

Conserve chaque tâche : calcul → choix/résultat ; justification → choix d’argument ;
construction → figures candidates ; problème ouvert → étapes jusqu’à la conclusion.
Les distracteurs sont faux, distincts après simplification et issus d’erreurs plausibles
(signe, priorité, exposant, unité). Ni bonne réponse dupliquée ni « aucune réponse ».
Déclare un `check` SymPy pour toute question calculable ; sinon `{"kind":"none"}`.

Texte : français simple, une donnée par ligne, formules entre `$...$`, virgule
`$3{,}5$`, multiplication `\times`. Pas de symboles Unicode hors formule qui
s’imprimeraient « ? ». Respecte la liste de commandes de `schema.md`.

## 6. Figures et exceptions

Un tableau est toujours du Markdown, jamais une image. Pour les autres visuels :
1. `geo` pour la géométrie ; mêmes points, longueurs, codages et valeurs.
2. `chart` pour les graphiques ; exactement les données et graduations du manuel.
3. `crop` pour un dessin complexe ou une valeur impossible à relever avec certitude.
   Découpe serrée, sans couper de donnée ; blanchis les textes parasites avec `masks`.

Pas d’image décorative. La figure ne donne pas la réponse. Allège vraiment la figure
Facile si nécessaire et garde les questions cohérentes avec les éléments visibles.
Le rendu commun écran/PDF dimensionne les formules et les rangées à relier : ne
réduis pas artificiellement une formule pour la faire tenir, répartis mieux les tâches.

Sauter est exceptionnel : seulement si aucune transformation fidèle cochable/reliable
n’est possible (manipulation obligatoire sur logiciel, mesure réelle, production libre).
Documente précisément chaque saut dans `skipped`.

## 7. Exemples

Ce sont des formats, pas des contenus à recopier.

**QCM à réponse unique avec vérification :**

```json
{
  "response_type": "qcm_single",
  "statement": "Calcule le PGCD de $1\\,925$ et $4\\,125$.",
  "choices": [
    "$55$",
    "$175$",
    "$275$",
    "$385$"
  ],
  "correct": [
    2
  ],
  "check": {
    "kind": "value",
    "expr": "gcd(1925, 4125)",
    "choice": 2
  }
}
```

**Grille Vrai/Faux** :

```json
{
  "response_type": "checkbox_grid",
  "statement": "Pour chaque affirmation, coche la bonne case.",
  "cols": [
    "Vrai",
    "Faux"
  ],
  "rows": [
    {
      "label": "$12$ est un multiple de $3$",
      "correct": 0
    },
    {
      "label": "$14$ est un multiple de $4$",
      "correct": 1
    }
  ],
  "check": {
    "kind": "rows",
    "exprs": [
      "Eq(Mod(12,3),0)",
      "Eq(Mod(14,4),0)"
    ]
  }
}
```

**Relier.** Manuel n°39 : « Associer chaque expression à son écriture développée et
réduite ». Version Facile : trois paires et un guide de méthode.

```json
{
  "response_type": "matching",
  "statement": "Relie chaque expression à son écriture développée et réduite.\n{{aide}} Développe chaque produit avec $k(a+b) = ka+kb$, puis regroupe les termes en $b$ d'un côté et les nombres de l'autre.",
  "left": [
    "$2(b+6)+7(b-1)$",
    "$10(b-9)+6(5-b)$",
    "$3(2b+11)+5(3b-8)$"
  ],
  "right": [
    "$21b-7$",
    "$9b+5$",
    "$4b-60$"
  ],
  "pairs": [
    [
      0,
      1
    ],
    [
      1,
      2
    ],
    [
      2,
      0
    ]
  ]
}
```

**Composite Facile avec guides entre les questions et figure géométrique.** Manuel
n°44 : aires de deux rectangles de côtés $7x+3$ × $4x+5$ et $14x+3$ × $2x+5$.

```json
{
  "source_number": "44",
  "source_page": "p075",
  "source_bbox_px": [
    508,
    1010,
    975,
    1255
  ],
  "competency_code": "A3.2",
  "calculator": "interdite",
  "figure": {
    "kind": "geo",
    "spec": {
      "points": {
        "A": {
          "xy": [
            0,
            0
          ],
          "label": ""
        },
        "B": {
          "xy": [
            4.6,
            0
          ],
          "label": ""
        },
        "C": {
          "xy": [
            4.6,
            2.2
          ],
          "label": ""
        },
        "D": {
          "xy": [
            0,
            2.2
          ],
          "label": ""
        },
        "E": {
          "xy": [
            7,
            0
          ],
          "label": ""
        },
        "F": {
          "xy": [
            12.4,
            0
          ],
          "label": ""
        },
        "G": {
          "xy": [
            12.4,
            1.4
          ],
          "label": ""
        },
        "H": {
          "xy": [
            7,
            1.4
          ],
          "label": ""
        }
      },
      "polygons": [
        [
          "A",
          "B",
          "C",
          "D"
        ],
        [
          "E",
          "F",
          "G",
          "H"
        ]
      ],
      "lengths": [
        {
          "seg": [
            "D",
            "C"
          ],
          "label": "$7x+3$",
          "side": "left"
        },
        {
          "seg": [
            "B",
            "C"
          ],
          "label": "$4x+5$",
          "side": "right"
        },
        {
          "seg": [
            "H",
            "G"
          ],
          "label": "$14x+3$",
          "side": "left"
        },
        {
          "seg": [
            "F",
            "G"
          ],
          "label": "$2x+5$",
          "side": "right"
        }
      ],
      "texts": [
        {
          "at": [
            2.3,
            1.1
          ],
          "text": "$R_1$"
        },
        {
          "at": [
            9.7,
            0.7
          ],
          "text": "$R_2$"
        }
      ],
      "width_mm": 80
    }
  },
  "variants": {
    "base": {
      "response_type": "composite",
      "statement": "On considère les deux rectangles ci-dessous, $x$ désignant un nombre positif.",
      "questions": [
        {
          "response_type": "qcm_single",
          "statement": "Quelle est l'aire du rectangle $R_1$ ?",
          "choices": [
            "$28x^2+47x+15$",
            "$28x^2+35x+15$",
            "$11x+8$",
            "$28x^2+15$"
          ],
          "correct": [
            0
          ],
          "check": {
            "kind": "value",
            "expr": "expand((7*x+3)*(4*x+5))",
            "choice": 0
          }
        },
        {
          "response_type": "qcm_single",
          "statement": "Quelle est l'aire du rectangle $R_2$ ?",
          "choices": [
            "$16x+8$",
            "$28x^2+76x+15$",
            "$28x^2+15$",
            "$28x^2+70x+15$"
          ],
          "correct": [
            1
          ],
          "check": {
            "kind": "value",
            "expr": "expand((14*x+3)*(2*x+5))",
            "choice": 1
          }
        },
        {
          "response_type": "qcm_single",
          "statement": "Ces rectangles ont-ils la même aire quelle que soit la valeur de $x$ ?",
          "choices": [
            "Oui, quelle que soit la valeur de $x$",
            "Non : les aires ne sont égales que pour $x = 0$",
            "Non : les aires ne sont jamais égales"
          ],
          "correct": [
            1
          ]
        }
      ]
    },
    "facile": {
      "response_type": "composite",
      "statement": "On considère les deux rectangles ci-dessous, $x$ désignant un nombre positif.\n{{aide}} L'aire d'un rectangle est longueur $\\times$ largeur.",
      "questions": [
        {
          "response_type": "qcm_single",
          "statement": "Quelle est l'aire du rectangle $R_1$ ?\n{{aide}} Pour développer $(7x+3)(4x+5)$, multiplie chaque terme du premier facteur par chaque terme du second : tu obtiens quatre produits.",
          "choices": [
            "$28x^2+47x+15$",
            "$11x+8$",
            "$28x^2+15$"
          ],
          "correct": [
            0
          ],
          "check": {
            "kind": "value",
            "expr": "expand((7*x+3)*(4*x+5))",
            "choice": 0
          }
        },
        {
          "response_type": "qcm_single",
          "statement": "Quelle est l'aire du rectangle $R_2$ ?",
          "choices": [
            "$16x+8$",
            "$28x^2+15$",
            "$28x^2+76x+15$"
          ],
          "correct": [
            2
          ],
          "check": {
            "kind": "value",
            "expr": "expand((14*x+3)*(2*x+5))",
            "choice": 2
          }
        },
        {
          "response_type": "qcm_single",
          "statement": "Les deux rectangles ont-ils toujours la même aire ?\n{{aide}} Compare les deux expressions terme à terme : les termes en $x^2$, les termes en $x$, puis les nombres.",
          "choices": [
            "Oui, toujours",
            "Non, pas toujours"
          ],
          "correct": [
            1
          ]
        }
      ]
    }
  }
}
```

**Graphique recréé à l'identique** (lecture graphique ; la valeur demandée n'est pas
marquée) :

```json
{
  "kind": "chart",
  "spec": {
    "axes": {
      "x": {
        "min": 0,
        "max": 24,
        "step": 4,
        "minor": 1,
        "label": "heure (h)"
      },
      "y": {
        "min": 0,
        "max": 30,
        "step": 5,
        "label": "T (°C)"
      },
      "grid": "both"
    },
    "curves": [
      {
        "points": [
          [
            0,
            12
          ],
          [
            4,
            10
          ],
          [
            8,
            15
          ],
          [
            12,
            24
          ],
          [
            16,
            26
          ],
          [
            20,
            18
          ],
          [
            24,
            13
          ]
        ],
        "markers": true
      }
    ]
  }
}
```

**Découpe avec masque** (écriture manuscrite de deux élèves ; le bord de l'exercice
voisin est blanchi) :

```json
{
  "kind": "crop",
  "page": "p075",
  "bbox_px": [
    75,
    1244,
    492,
    1378
  ],
  "masks": [
    [
      470,
      1244,
      492,
      1300
    ]
  ]
}
```

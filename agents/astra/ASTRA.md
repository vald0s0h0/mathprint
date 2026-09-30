# ASTRA — exercices du manuel Indigo dans MathPrint

Lis entièrement ce fichier puis `schema.md`. Lis toi-même les images du manuel.
Travaille dans `RUN/astra_output.json` ; ne modifie l’application que si l’utilisateur
le demande explicitement. Lance les commandes depuis la racine avec
`backend/.venv/bin/python agents/astra/run.py`.

## 1. Procédure complète

1. `prepare --chapter "<chapitre>"` (`--pages 76-78`, `--lesson 74` si demandé).
   En cas d’ambiguïté, consulte `chapters`.
2. Lis `RUN/payload.json` puis CHAQUE image de leçon : définitions, méthodes,
   notations, vocabulaire. Lis ensuite CHAQUE page d’exercices dans l’ordre.
3. Recense chaque numéro, y compris flash, expert, bilan, problèmes et énigmes.
   Ignore les activités, pages « Travailler autrement » et l’interface de liseuse.
   Note le numéro, la page et le cadre source, la rubrique, le titre et la calculette.
4. Écris la sortie par lots (une page à la fois), en validant à chaque lot.
5. `figures RUN` : ouvre CHAQUE PNG utile, y compris les figures de sous-questions.
   Vérifie cadrage, valeurs, noms, lisibilité et absence de chevauchement.
6. `validate RUN` : corrige chaque erreur et chaque réserve fondée jusqu’à zéro erreur.
7. `preview RUN` : ouvre CHAQUE page PNG. Contrôle aussi le PDF réel : toutes les
   tâches présentes, aides APRÈS les réponses concernées, figures au bon endroit,
   formules lisibles, points alignés, aucune carte hors page. Corrige et revalide.
8. `persist RUN` : enregistre en brouillons. `--replace` remplace les anciens
   brouillons Astra des mêmes sources ; ne remplace jamais un exercice validé.
9. Donne le nombre de sources et de cartes, les sauts motivés, les réserves utiles
   et le chemin du PDF. Ne t’arrête pas entre les étapes. Ne publie rien.

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

## 3. Contenu et ordre de lecture

L’élève répond uniquement en cochant ou en reliant. La carte se suffit à elle-même.
Aucune référence au cours, à une autre page, à un numéro d’exercice absent.

Astra choisit l’ordre logique des informations : contexte, figures, sous-questions.
`statement` est le contexte COMMUN. Il peut être vide ; n’invente pas une phrase
comme « Réponds aux questions » pour le remplir. Évite les consignes répétées :
une sous-question de calcul peut être simplement `$expression$` lorsque la tâche
est claire. Le rendu met les formules en valeur à un corps constant et lisible.

Chaque question d’un composite porte sa propre zone de réponse et peut porter
sa propre `figure`. Par défaut, cette figure apparaît juste AVANT la question.
Exemple : pyramide 1 avant A, pyramide 2 avant D ; jamais les deux figures ensemble
au début si elles concernent deux groupes distincts. `{{figure}}` dans le texte
permet un placement explicite, que les scripts conservent.

Utilise les lettres visibles sur la figure dans les questions (`g`, `h`, `i`).
Chaque inconnue dessinée correspond à une question, en Base comme en Facile :
s’il reste trois lettres à chercher, pose trois questions ou adapte la figure.

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

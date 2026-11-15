# Forge 2 : plan de refonte du pipeline de génération

- **Statut** : plan validé le 2026-11-14, implémenté et livré le 2026-11-15 (branche `forge2` fusionnée) (journal : [forge2-journal.md](../forge2-journal.md)). Déjà fait : le correctif du mélange des lignes (§13.1) et la commande `forge contexte` (§6.2).
- **Date** : 2026-11-14
- **Périmètre** : génération des exercices (toutes matières, programme ivoirien 6e → Tle, programme français conservé), correction des réponses écrites, suivi des élèves.
- **Reprend** : [forge-qualite-generation.md](forge-qualite-generation.md) (archétypes, fiches-idées, socle par verbe, chaîne de confiance). Ce qui y est dit reste valable sauf mention contraire ici.
- **Lu pour ce plan** : `agents/forge/` (FORGE.md, schema.md, forge.py, goldfish.py, run.py), `docs/forge-rendu-fiable.md`, `backend/app/data/competencies_ci.json`, `services/llm_grader.py`, `forgetting.py`, `student_history.py`, `error_journal.py`, `draw_rules.py`, `bank_health.py`, `appreciation.py`.

## 0. L'essentiel en onze décisions

1. **Le modèle ne décide plus de la mise en page.** Il remplit des gabarits typés ; le moteur place tout. Chaque gabarit est prouvé une fois sur ses pires cas, puis réutilisé.
2. **On écrit des familles, pas des cartes.** Une famille = un énoncé à paramètres + sa solution exécutable + ses erreurs calculées. Le script en tire les variantes, justes par construction, sans token.
3. **Un contrôle géométrique déterministe du PDF rendu** (texte contre traits, débordements, tailles) remplace le jugement à l'œil. C'est la réponse directe aux erreurs de mise en page.
4. **Deux étapes.** D'abord le contexte : une fiche de leçon écrite par Fable (lycée) ou Opus (collège), avec des manuels **facultatifs** ; puis les exercices, générés à partir de cette fiche seule. Les idées d'un manuel ne servent que son programme (§6).
5. **La fiche de leçon validée est la seule source de vérité des faits.** Une carte de SVT ou d'histoire ne peut affirmer que ce que la fiche contient.
6. **Aucune image de contenu générée par une IA d'images.** Toute figure vient du code (TikZ paramétré) ou d'une bibliothèque d'éléments validés une fois.
7. **Génération à la demande, par leçon**, déclenchée par le calendrier des classes actives et par l'épuisement réel, dans une file de nuit sur le Mac.
8. **Volume par formule** (verbe × durée × poids à l'examen), environ 19 000 cartes pour les maths ivoiriennes au lieu de 62 000, dont la moitié seulement écrite par un modèle.
9. **La confiance se mesure par gabarit** : 100 % de relecture au début, puis un échantillon qui baisse tant qu'aucun défaut n'apparaît, et remonte au premier défaut.
10. **Correction : par vagues groupées par exercice, sur abonnement** (pas d'API pour l'instant), routeur à trois étages, modèle choisi sur un banc d'essai de vraies copies. Pas de LoRA pour juger ; éventuellement plus tard pour lire l'écriture.
11. **Anti-triche par jumelles, actif par défaut** : 2 ou 3 jeux de valeurs × 2 dispositions écrites par le modèle ; aucun script ne mélange les réponses ; deux voisins n'ont jamais les mêmes valeurs (§13).

## 1. Diagnostic : d'où viennent les défauts

Le pipeline actuel est juste sur le fond : calculs vérifiés par SymPy, lecteur sans contexte (Goldfish), écriture simulée. Les défauts restants ont trois causes de structure.

| Cause | Effet observé | Réponse |
|---|---|---|
| Chaque carte est une pièce unique : texte libre, TikZ libre, disposition essayée après coup | Défauts de mise en page imprévisibles, reprises coûteuses, pièges du moteur à mémoriser (section « Pièges connus » de FORGE.md) | Gabarits typés, contrôle géométrique (§3, §5) |
| La mise en page est contrôlée par des heuristiques sur une image (`whitespace`, `best_layout`) | On détecte le blanc, pas un nom qui chevauche un trait | Contrôle sur les objets du PDF (§5.3) |
| Tout est câblé pour les maths françaises et Indigo | `forge ci …` ne peut pas marcher proprement (§14) ; prompts « mathématiques », « collège (France) » | Registres par matière et par programme (§2) |

Coût actuel : 170 000 à 190 000 tokens par compétence de 20 cartes, soit environ 9 000 tokens par carte. Cible : 2 500 à 4 000 par carte écrite, et zéro pour une variante.

## 2. Architecture : des registres, un seul endroit par chose

Le principe qui rend le système modifiable : **chaque notion vit dans un registre, et chaque entrée de registre apporte ses propres preuves**. `forge.py` (105 Ko) est découpé en modules ; `schema.md` n'est plus écrit à la main mais **produit à partir du code** (modèles Pydantic), donc jamais en retard sur lui.

| Pour ajouter… | On crée… | Preuves exigées avant usage |
|---|---|---|
| un **champ de réponse** (ex. schéma à légender) | `champs/<type>.py` : contrat, rendu, taille manuscrite, lecture CV/OCR, notation, simulation | banc de conformité commun : 200 remplissages aléatoires rendus, lus et notés sans écart |
| un **gabarit** de carte | `gabarits/<nom>.yaml` : blocs, emplacements, limites de longueur | rendu des pires cas (textes et valeurs les plus longs), contrôle géométrique vert |
| un **archétype** (ERR, QUI, DOC…) | une entrée dans la doctrine de la matière + 2 étalons | étalons relus par l'humain |
| un **kit de figures** (circuits, verrerie, cellule) | macros TikZ dans la charte + planche | planche validée, contrôle géométrique sur 30 tirages |
| une **matière** | `matieres/<matière>/` : doctrine, charte, taxonomie d'erreurs, vérificateur | pilote de 3 leçons relu à 100 % |
| un **programme** | `programmes/<code>.md` + référentiel | — |
| un **contrôle** | une fonction dans `controles/` + un cas dans le musée des défauts | le cas du musée échoue sans le contrôle |

**Le musée des défauts.** Tout défaut trouvé (par toi, par un professeur, par les copies) devient un fichier de cas et, si possible, un contrôle automatique. Règle : un défaut ne doit pouvoir se produire qu'une fois. C'est ce qui te dit quoi modifier : un défaut → un cas → le registre concerné.

**Stack.** Je recommande de **garder** Python, `pdfgen`, TikZ/Tectonic et SymPy. Changer le moteur d'impression (Typst, par exemple) casserait le lien avec la CV, qui repose sur les coordonnées exactes de `pdfgen` : risque fort, gain faible. Ce qui s'ajoute :

- Pydantic pour les contrats (source unique du schéma) ;
- PyMuPDF (déjà présent) pour le contrôle géométrique ;
- `pint` pour les unités en physique-chimie ;
- paquets TeX par matière (§9) ;
- une table `forge_jobs` et un lanceur de nuit (`claude -p` et `codex exec` sur abonnement, comme Goldfish aujourd'hui).

## 3. L'unité de production : la famille

Aujourd'hui le modèle écrit une carte figée. Demain il écrit une **famille** :

```yaml
famille: thales-longueur-manquante-configuration-papillon
gabarit: contexte_figure_reponse_courte
archetype: CALC
parametres:
  k:  {dans: [1.5, 2, 2.5, 3]}
  AM: {dans: [2, 3, 4, 6]}
contraintes: ["AM*k <= 15", "AM*k == int(AM*k)"]
enonce: "… $AM = {AM}$ cm … Calcule $AB$."
solution: "AB = AM*k"
erreurs:
  - {code: math.prop.additif, calcul: "AM + k", message: "Tu as ajouté le coefficient…"}
figure: {kit: thales_papillon, args: {AM: "{AM}", k: "{k}"}}
```

Le script :
1. tire toutes les combinaisons valides, calcule réponse et distracteurs, **rejette** celles où un distracteur vaut la bonne réponse ou un autre distracteur ;
2. rend les **cas extrêmes** (valeurs les plus longues, figure la plus étirée) : si le pire cas passe, tous passent ;
3. garde 2 ou 3 jeux de valeurs et jette le reste. Chaque jeu existe en 2 dispositions écrites par le modèle (§13).

Ce que cela apporte :
- **Coût** : une famille écrite donne plusieurs cartes. Les variantes ne coûtent rien.
- **Triche** : des jumelles de la même carte pour deux voisins (§13).
- **Pédagogie** : la « revanche » (§12). Après une erreur, l'élève reçoit la même structure avec d'autres nombres. C'est exactement ce qu'il faut pour savoir si l'erreur est corrigée, et aujourd'hui le tirage évite au contraire les structures déjà vues.
- **Mise en page** : prouvée sur le pire cas, pas sur un exemple.

Limite : une variante n'est pas un nouvel exercice. La variété pédagogique se compte en familles, pas en cartes. Les archétypes non paramétrables (document, rédaction, texte littéraire) restent des cartes uniques : une famille à une seule variante.

## 4. Volumes : combien, et quand

### 4.1 Une formule, pas un chiffre

Le référentiel ivoirien de maths compte 3 107 contenus. Répartition par verbe (mesurée) : Connaître 969, Déterminer 351, Calculer 235, Traiter 168, Construire 161, Identifier 145, Reconnaître 142, Justifier 139, Résoudre 119, Démontrer 116. Près de la moitié des contenus sont des savoirs à restituer : leur donner 20 exercices chacun serait du gaspillage.

| Nature du contenu | Verbes | Unité de génération | Socle |
|---|---|---|---|
| Savoir | Connaître, Identifier, Reconnaître, Noter | **toute la leçon** : un seul lot pour tous ses contenus de savoir | 2 cartes par contenu en moyenne, QCM / vrai-faux / relier |
| Savoir-faire | Déterminer, Calculer, Résoudre, Construire, Représenter, Placer… | le contenu | 4 à 6 familles × 2 à 3 variantes = 10 à 16 cartes |
| Raisonnement | Justifier, Démontrer, Interpréter | le contenu | 4 à 6 cartes (corrigées par LLM : chères à corriger, donc peu) |
| Situation | Traiter une situation | la leçon | 4 à 6 problèmes au format de l'examen |

Le socle est ensuite modulé par un **poids** entre 0,5 et 1,5 :

- la **durée** quand elle existe (`recommended_minutes`, présente pour les 3 107 contenus de maths) ;
- sinon la **part du contenu dans la leçon** et les heures de la leçon dans la progression ;
- sinon le défaut du verbe ;
- dans tous les cas, un bonus pour ce qui tombe à l'examen (BEPC, BAC), relevé dans les annales.

La durée ne sera pas donnée dans toutes les matières : la formule marche sans elle, elle l'utilise quand elle l'a.

**Ordre de grandeur, maths ivoiriennes** : environ 2 700 cartes de savoir, 14 400 de savoir-faire (soit environ 6 000 familles écrites), 1 300 de raisonnement, 850 problèmes. Total proche de **19 000 cartes, pour environ 10 000 unités écrites par un modèle**, contre 62 000 avec la règle des 20.

### 4.2 Ne générer que ce qui sert

Deux déclencheurs (décision du 2026-11-14 : pas de demande directe d'un professeur) :

1. **Calendrier.** Le référentiel donne la semaine de début de chaque leçon. Les bases sont posées : la commande `run.py calendrier` liste les leçons qui commencent dans les semaines à venir pour les classes actives, sans rien lancer seule.
2. **Épuisement.** `bank_health` sait déjà qu'une classe n'a plus d'inédits : un complément part en file.

**Amorce puis complément.** Au premier déclenchement, on ne génère que 40 % du socle (de quoi faire un entraînement et une évaluation). Le reste vient si la compétence est réellement tirée. Une leçon jamais demandée ne coûte rien.

## 5. La chaîne de confiance

On **jette plutôt que de réparer** : une carte qui échoue deux fois à une porte est abandonnée, le plan fournit une remplaçante.

| # | Porte | Qui | Bloque |
|---|---|---|---|
| 1 | Contrat (Pydantic) | script | toujours |
| 2 | Justesse : solution exécutable, distracteurs calculés, valeurs de l'énoncé = paramètres ; unités (`pint`) ; **faits ancrés** dans la fiche (§5.1) | script + modèle indépendant | toujours |
| 3 | Programme : hors référentiel, et **hors progression** (une notion vue plus tard dans l'année) | script | toujours |
| 4 | Rendu réel + écriture simulée (existant) | script | toujours |
| 5 | **Contrôle géométrique du PDF** (§5.3) | script | toujours |
| 6 | Lecteur sans contexte, **autre modèle que l'auteur** (Opus écrit, Sonnet lit) | modèle | désaccord = jetée |
| 7 | Critique pédagogique à liste fermée (§5.2) | modèle | un « non » = jetée |
| 8 | Échantillon humain selon l'indice de confiance (§5.4) | humain | défaut grave = lot rejeté |
| 9 | Terrain : signalement professeur, statistiques des copies (§5.5) | données | quarantaine |

### 5.1 Les faits ancrés (matières à contenu)

En maths, SymPy garantit la justesse. En SVT, en histoire, en physique, rien d'équivalent. Règle proposée : la **fiche de leçon validée par un humain** contient des faits numérotés (`F12 : la mitose conserve le nombre de chromosomes`). Une carte déclare les faits qu'elle utilise ; un vérificateur indépendant, qui ne reçoit que la fiche et la carte, refuse toute affirmation absente de la fiche. Tu relis quelques centaines de fiches, pas des dizaines de milliers de cartes. C'est le levier de confiance le plus important hors maths.

### 5.2 Critique pédagogique, liste fermée

Oui ou non, sans commentaire libre : une seule compétence évaluée ; les maths (ou la notion) sont nécessaires pour répondre ; le contexte est plausible (ordres de grandeur du profil programme) ; l'archétype est respecté ; aucun stéréotype ; vocabulaire du niveau ; la micro-correction dit quoi faire ; le registre graphique convient à l'âge (§8).

### 5.3 Le contrôle géométrique

Le PDF de la carte est lu avec PyMuPDF : boîtes de chaque texte, tracés vectoriels, images. Vérifications déterministes :

- aucune boîte de texte ne coupe un trait qui ne lui appartient pas (nom de point sur un côté, cote sur une arête) ;
- aucune boîte de texte n'en recouvre une autre ;
- rien ne sort du cadre de la carte ni de la zone de figure ;
- corps minimal de chaque texte après toutes les transformations ;
- marge minimale entre figure, texte et zone de réponse ;
- zones de réponse aux coordonnées annoncées à la CV ;
- rien d'imprimé dans une zone de réponse, ni de couleur proche de l'encre à moins de N mm.

Les macros de la charte marquent déjà leurs éléments (nom, cote, trait) : le contrôle sait donc à quel trait une cote appartient. Un modèle de vision ne vient qu'après, pour l'allure générale ; il est mauvais sur les petits chevauchements, il ne doit pas être la seule barrière.

### 5.4 L'indice de confiance

Chaque couple (gabarit, matière) a un compteur. Au départ, 100 % des cartes sont relues. Après 30 cartes sans défaut : 25 %. Après 100 : 10 %. Après 300 : 5 %. **Un défaut remet le compteur à zéro** et ouvre un cas au musée. Un tableau de bord montre où la confiance est acquise et où regarder. La relecture est faite par toi seul, dans toutes les matières. Un gabarit nouveau ou une matière nouvelle repart toujours de 100 %.

### 5.5 Le terrain

- Bouton « signaler » pour le professeur : deux signalements mettent la carte en quarantaine.
- Statistiques déjà prévues dans la note précédente : distracteur jamais choisi, copies blanches, bons élèves qui échouent.
- Chaque correction du professeur (`review_events`) sur une carte nourrit ces statistiques.

## 6. Deux étapes : le contexte, puis les exercices

### 6.1 Le principe

| | Étape 1 : le contexte | Étape 2 : les exercices |
|---|---|---|
| Produit | la **fiche de leçon** (faits numérotés, erreurs, progression) et, s'il y a un manuel, des **fiches-idées** | les familles d'exercices |
| Lit | le programme officiel ; en option : manuel, livre du professeur, annales (PDF, texte, images) | la fiche et les idées, **rien d'autre** |
| Modèle | **Fable au lycée**, **Opus au collège** | Opus, Sonnet à tester (§7.5) |
| Fréquence | une fois par leçon | à la demande (§4.2) |
| Commande | `forge contexte …` | `forge …` |
| Relecture humaine | par toi, guidée par les points incertains du bilan | par toi, par échantillon (§5.4) |

Pourquoi séparer : la fiche est le point où une erreur de fond coûte le plus cher, puisqu'elle se retrouve dans tous les exercices de la leçon. On y met le modèle le plus fort, une seule fois, sur un texte court que l'on peut relire. L'étape 2 devient un travail d'exécution, cadré par la fiche et vérifié par la chaîne de contrôle.

**Un manuel n'est jamais lu à l'étape 2.** Aujourd'hui `source_text` colle dans le contexte la leçon, les pages d'exercices et le livre du professeur : c'est ce qui fait imiter le manuel (17 énoncés de Thalès sur 20 commençaient pareil) et ce qui coûte le plus de tokens. Ce chemin disparaît.

### 6.2 La commande `forge contexte`

Elle existe dès maintenant : procédure dans `agents/forge/CONTEXTE.md`, déclarée dans `AGENTS.md`. Elle n'a besoin d'aucun code.

```
forge contexte <programme> <niveau> <leçon…> [--matiere M] [--serie S]
               [--sources chemin…] [--pages a-b] [--refaire]
```

**Comment l'utiliser**

1. Ouvre une session Claude Code dans le dépôt, avec **Fable pour le lycée**, **Opus pour le collège**. La commande vérifie son modèle et s'arrête s'il ne convient pas.
2. Tape la commande. Exemples :

| Besoin | Commande |
|---|---|
| Maths ivoiriennes, 3e, Thalès, sans manuel | `forge contexte ci 3e E2` |
| Trois leçons d'un coup | `forge contexte ci 2nde A1 A2 A3 --serie C` |
| Maths françaises, 3e (Indigo est trouvé seul) | `forge contexte fr 3e C2` |
| SVT, terminale D, avec un manuel en PDF | `forge contexte ci tle E1 --serie D --matiere svt --sources ~/Manuels/svt-tle.pdf --pages 40-61` |
| Plusieurs sources | `forge contexte ci 1re B2 --serie C --matiere pc --sources ~/Manuels/pc-1re.pdf ~/Annales/bac-pc/` |
| Réécrire une fiche existante | `forge contexte fr 3e C1 --refaire` |

3. Lis le **bilan** : il liste d'abord les points dont le modèle n'est pas sûr. Ce sont eux qu'il faut relire.
4. La fiche est dans `data/forge/fiches/` (sous-dossier par matière, sauf les maths). Tu peux la corriger à la main : c'est un fichier Markdown.

**À savoir**

- **Le programme officiel est obligatoire.** Pour les maths il est déjà dans le dépôt. Pour toute autre matière, donne le PDF du programme dans `--sources` : la commande refuse d'écrire une fiche de mémoire.
- **Les manuels sont facultatifs.** Sans `--sources`, la fiche vient du programme seul. Avec, la commande lit les pages de la leçon et produit en plus les fiches-idées.
- **`--pages`** évite de faire chercher le chapitre dans un PDF de 300 pages. Sans lui, le modèle lit le sommaire.
- **Volume par session** : une à trois leçons avec des sources à lire, cinq au plus sans.
- Une fiche existante n'est jamais écrasée sans `--refaire`.

**Ce que la fiche contient** : acquis et notions pas encore vues dans l'année, hors programme, vocabulaire officiel, **faits numérotés**, puis par compétence les micro-savoir-faire, les erreurs fréquentes et les supports, ce qui tombe à l'examen, des contextes plausibles. 8 Ko au plus, aucune recette de moteur.

**Ce qui viendra avec les lots** (§11.2) : les contrôles par script (aucune phrase reprise d'une source, toutes les compétences couvertes, taille), la carte de compétence en YAML, et l'état « relue » visible dans l'application. D'ici là, ces contrôles sont une liste que le modèle applique lui-même.

### 6.3 Avec ou sans manuel : le même chemin

Le contexte d'un lot d'exercices contient un bloc « idées », de **zéro à six fiches-idées**. C'est la seule différence entre une leçon qui a eu un manuel et une leçon qui n'en a pas eu.

| | Leçon avec manuel | Leçon sans manuel |
|---|---|---|
| Fiche de leçon | programme, enrichi par le manuel et le livre du professeur | programme, guide, annales |
| Bloc « idées » | fiches-idées | vide |
| Plan du lot | 30 à 40 % des emplacements au plus reçoivent une idée imposée | le modèle propose ses mécanismes, cadré par les archétypes |
| Code, contrôles, contrat | identiques | identiques |

Le fichier `.sources.yaml` de chaque fiche garde ce qui a été lu. Chaque famille garde la trace de l'idée dont elle vient (`inspiration`), pour mesurer ce que les idées apportent. Le plafond de 30 à 40 % évite qu'un manuel dicte toute la banque.

### 6.4 Extraire seulement les bonnes idées

C'est la commande `forge contexte` qui le fait, pendant qu'elle lit le manuel.

- **Trier.** Un exercice « Calcule… » ne contient aucune idée. On ne garde qu'un exercice qui remplit cinq conditions : il provoque l'erreur ou sépare celui qui a compris de celui qui applique ; la notion est nécessaire ; le mécanisme n'est pas une application directe ; il tient sur une carte et se corrige par nos formats ; il ne dépend pas d'un document précis. Attendu : 5 à 10 idées par leçon, sur 60 à 100 exercices.
- **Abstraire.** Le mécanisme en deux phrases, sans valeur, sans nom, sans contexte, sans tournure de la source.
- **Le livre du professeur** ne donne pas d'idées : ses erreurs fréquentes et ses objectifs vont dans la fiche.
- **Relire.** Une ligne par idée : tu écartes ce qui ne te plaît pas en ouvrant le fichier `.idees.yaml`.

**Les motifs transversaux.** Ce qui fait la qualité d'Indigo n'est pas propre à un chapitre : erreur provoquée, deux élèves en désaccord, donnée inutile, question à rebours. Ces motifs vont dans la **doctrine** (catalogue d'archétypes, avec leurs étalons), écrite une fois. Ce sont des façons de poser une question, pas des contenus d'un manuel : ils valent pour tous les programmes.

### 6.5 Décision : pas de partage entre programmes (option B)

Les idées tirées d'un manuel servent **le programme et le niveau de ce manuel**, et eux seuls. Les idées d'Indigo servent le français, 3e et 6e ; elles ne sont pas proposées aux leçons ivoiriennes. Pas de table de correspondance, pas de banque par notion.

Ce que cela donne :

| Programme | Fiches | Idées |
|---|---|---|
| Français, 3e et 6e, maths | avec Indigo (déjà fourni) | oui |
| Français, 5e et 4e | programme seul, ou un manuel si tu en fournis un | selon la source |
| Ivoirien, toutes matières | programme, guides, annales ; un manuel **si tu en fournis un** pour la leçon | seulement dans ce cas |

Rien n'empêche de fournir un bon manuel ivoirien pour une leçon précise : le chemin est le même. Ce qui est écarté, c'est la chaîne systématique (trouver, scanner et trier tous les manuels), dont le gain est incertain.

Deux sources restent utiles partout, et tiennent en peu de pages : les **annales** (BEPC, BAC, brevet) pour le format et le niveau attendus, et tes **propres copies** pour les erreurs réelles.

Les options écartées, pour mémoire : A (aucun manuel nulle part), C (banque d'idées par notion partagée avec l'ivoirien), D (référentiel complet de notions). Le champ `competences` des fiches-idées suffit à l'option B ; passer un jour à C demanderait d'y ajouter une notion neutre.

## 7. Contexte, cache, taille des sessions

### 7.1 Cinq étages, du plus stable au plus précis

| Étage | Contenu | Taille visée | Change |
|---|---|---|---|
| 1. Noyau | doctrine de la matière, archétypes, règles communes | 4 000 tokens | rarement |
| 2. Contrat à la carte | **seulement** les formats, gabarits et kits de figures utilisés par ce lot | 2 000 à 4 000 | par lot |
| 3. Programme et niveau | profil `ci.md`, fiche de niveau (acquis, registre graphique) | 1 500 | par niveau |
| 4. Leçon | fiche de leçon (pédagogie, faits numérotés, erreurs codées) | 2 500 | par leçon |
| 5. Lot | plan du lot, signatures de l'existant, **bloc « idées » : 0 à 6 fiches-idées** (§6.3) | 1 000 à 2 000 | par lot |

Aujourd'hui `schema.md` pèse 27 Ko (environ 8 000 tokens) et il est lu en entier, géométrie à tracer et TikZ compris, même pour un lot de QCM. Le contrat à la carte divise cet étage par deux ou trois. Les fiches de 30 à 40 Ko reviennent à 8 Ko : les recettes moteur en sortent.

Total d'entrée : **12 000 à 14 000 tokens**, contre plus de 40 000 aujourd'hui.

### 7.2 Cache

Sur abonnement, le cache est géré par Claude Code : je ne pose pas de points de cache, je peux seulement **ne pas le casser**.

- Le script assemble **un seul fichier de contexte par leçon**, dans l'ordre des étages, octet pour octet identique d'un travailleur à l'autre. Un seul appel de lecture, donc un préfixe commun.
- Aucune date, aucun identifiant de run, aucun JSON non trié dans ce fichier.
- Le lot (étage 5) vient **après**, dans un second fichier.
- On ne relit jamais un fichier dans une session, et on ne reprend pas une vieille session : une reprise relit tout sans cache (leçon du 6 novembre).
- Les travailleurs d'une même leçon partent à la suite, pas à une heure d'intervalle.

Le vrai levier n'est pas le cache : c'est d'écrire moins (familles, plan avant rédaction, jeter au lieu de réparer).

### 7.3 Deux temps : concevoir, puis rédiger

1. **Plan du lot** (court) : une ligne par emplacement : micro-savoir-faire, archétype, contexte, idée, gabarit. Le script vérifie quotas, diversité et doublons avec la banque. Un doublon est éliminé avant d'avoir coûté une rédaction.
2. **Rédaction** des familles du plan.

### 7.4 Combien par session

- **Un travailleur = un lot de 8 à 12 familles**, dans un contexte neuf. Au-delà, la qualité baisse et une coupure coûte cher.
- **Une session = une leçon**, 3 travailleurs au plus en parallèle (règle actuelle, conservée).
- **Une nuit = quelques leçons**, selon la fenêtre d'usage. Le lanceur s'arrête à un seuil de quota et reprend la nuit suivante ; un fichier écrit n'est jamais perdu (déjà le cas).
- Les contrôles 1 à 5 tournent entre les lots, sans modèle.

Point à vérifier avant d'industrialiser : les conditions d'usage des abonnements Claude et ChatGPT pour une production automatisée de nuit. Goldfish fonctionne déjà ainsi, mais à petite échelle.

### 7.5 Quel modèle écrit quoi

| Tâche | Modèle | Pourquoi |
|---|---|---|
| Doctrine, étalons | Fable 5.2 | écrit une fois, relu par un humain, conditionne tout le reste |
| Fiches de leçon (`forge contexte`) | **Fable 5.2 au lycée, Opus 5.5 au collège** | le risque d'erreur de fond est le plus fort au lycée, en SVT, en physique-chimie et en lettres |
| Plan de lot et familles | **Opus 5.5 uniquement**, pour l'instant | décision du 2026-11-14 |
| Lecteur sans contexte, critique | Sonnet 5.5 | autre modèle que l'auteur |
| Vérification des faits | Sonnet 5.5 | autre modèle que l'auteur |

## 8. Registre graphique par âge, et la mascotte

### 8.1 Trois registres, imposés par le script

| Niveaux | Registre | Personnages | Supports |
|---|---|---|---|
| 6e, 5e | illustré | Kiro et Nina en pied, expressifs ; jusqu'à 25 % des cartes | objets du quotidien, bulles |
| 4e, 3e | sobre | bustes, plus petits ; 10 à 15 % | copies d'élève, écrans, tickets, messages |
| 2nde à Tle | documentaire | **aucun dessin de personnage** ; au plus un prénom et une copie manuscrite | documents réalistes, données, schémas techniques |

Le registre est écrit dans la fiche de niveau. `check` refuse `\Mascotte` au lycée, comme il refuse aujourd'hui une couleur hors palette.

### 8.2 Kiro et Nina

La marque porte déjà deux prénoms : autant en faire deux personnages plutôt qu'une mascotte.

- **Deux tempéraments, deux types d'erreurs.** L'un va trop vite (erreurs de calcul, de signe, d'unité), l'autre applique une règle là où elle ne vaut plus (erreurs de conception). Les deux se trompent, les deux ont raison à tour de rôle : pas de personnage « bon » et de personnage « nul », et aucun rôle attaché au genre.
- **Ce qu'ils font** :
  - `ERR` : « Kiro a écrit ceci. Trouve son erreur et corrige-la. » L'erreur est tirée d'un code de la taxonomie et **calculée par le script**, donc toujours une vraie erreur d'élève.
  - `QUI` : « Kiro trouve 12, Nina trouve 7. Qui a raison ? »
  - « Explique à Nina pourquoi… » : une justification courte, plus naturelle qu'un « Justifie » ;
  - sur la copie corrigée : une vignette selon le résultat (erreur vaincue, progrès), jamais moqueuse.
- **La planche.** Une quinzaine de poses par personnage (doute, idée, erreur, fierté, montre du doigt, écrit, mesure, pèse…), en vectoriel, appelées par `\Mascotte{kiro}{doute}`. Emplacement de taille fixe dans le gabarit : la mise en page ne dépend pas de la pose. Coût en tokens : un appel de macro.
- **Qui dessine.** Tu fournis la planche, avec le nom de chaque position et de chaque émotion. Le moteur lit ces noms : aucune pose n'est codée en dur. L'encadré jaune du guide ne change pas (il doit rester compact).
- **Contraintes d'impression.** Couleur distincte de l'encre des élèves, jamais à côté d'une zone de réponse, validée par la simulation d'écriture et la CV avant tout usage (même prudence que pour `\Copie` dans la note précédente).

## 9. Les autres matières

Principe commun (décision 6) : **une figure vient du code ou d'une bibliothèque validée**. Une IA d'images produit des schémas faux sans qu'on puisse le vérifier (organites inventés, légendes illisibles), dans un style différent à chaque fois. Elle est exclue pour tout ce qui porte du sens.

### 9.1 Physique-chimie

| Besoin | Outil | Remarque |
|---|---|---|
| Circuits électriques | `circuitikz` | symboles normalisés, stylé par la charte |
| Molécules, équations | `chemfig`, `mhchem` | formules développées et topologiques |
| Optique | `tikz-optics` | lentilles, rayons |
| Courbes, oscillogrammes | `pgfplots` (déjà chargé) | données générées par le script |
| **Verrerie et montages** | **kit maison `kn-labo`** : 30 à 40 macros (bécher, erlenmeyer, burette, ballon, éprouvette, tube à essai, bec, support, montage de dosage, distillation, électrolyse) | rien d'ouvert n'est satisfaisant en TikZ ; à écrire une fois, sur planche |
| Mécanique | kit maison : plan incliné, poulie, dynamomètre, vecteurs force | les vecteurs sont à l'échelle des valeurs |

Vérifications propres : unités et homogénéité avec `pint`, chiffres significatifs, valeurs réalistes (table de constantes et d'ordres de grandeur dans la doctrine). Les familles paramétriques conviennent très bien.

### 9.2 SVT

Deux sources, selon le cas :

- **Paramétrique, par le code** : brin d'ADN et son complémentaire, transcription et traduction (table du code génétique), caryotypes, croisements (échiquiers calculés), arbres généalogiques dont la cohérence est vérifiée, graphiques de mesures, tableaux d'expériences.
- **Bibliothèque d'éléments** : cellule, organes, appareils, coupes. Sources ouvertes à redessiner dans la charte : Servier Medical Art (CC BY), Bioicons, NIH BioArt, Wikimedia Commons (licence à vérifier image par image, attribution à tenir dans un registre). Pas de BioRender : licence payante et restrictive pour la rediffusion. Chaque élément est validé une fois, avec ses **points d'ancrage** (où pointe la flèche « noyau »).

**Nouveau champ de réponse : le schéma à légender.** Des flèches numérotées sur la figure, et une case par flèche où l'élève **écrit le mot** (décision du 2026-11-14). Le mot est lu comme une réponse courte de type texte, avec les orthographes acceptées déclarées par la carte.

### 9.3 Histoire-géographie, EDHC

- **Cartes** : fonds Natural Earth (domaine public), rendus par script dans la charte ; carte à compléter = zones numérotées + légende.
- **Frises** : macro `\Frise`, dates placées à l'échelle par le script.
- **Données** : tableaux et graphiques à partir de sources ouvertes, datées et citées.
- **Textes** : c'est le point délicat. Domaine public, sources officielles, courte citation, ou texte rédigé pour l'exercice et **signalé comme tel**. Jamais un faux document attribué à un auteur réel. Le régime exact (durée des droits en Côte d'Ivoire, exception pédagogique) est à vérifier avec un juriste.
- Les faits ancrés (§5.1) sont ici indispensables.

### 9.4 Lettres, langues, philosophie

Ce que le papier et la machine font bien, et ce qu'ils ne font pas :

| Activité | Format | Correction |
|---|---|---|
| Grammaire, conjugaison, orthographe, vocabulaire | QCM, relier, texte à trous avec banque de mots, tableau | automatique |
| Compréhension d'un texte | texte aux **lignes numérotées** ; « à quelle ligne… » → l'élève écrit un numéro ; vrai-faux | automatique (chiffres) |
| Transformation de phrase, reformulation | réponse courte | LLM, groupée par exercice |
| Préparation de l'écrit long | ordonner des arguments, associer argument et exemple, choisir la bonne problématique, repérer un connecteur | automatique |
| **Micro-production** (3 à 6 lignes : une introduction, un paragraphe argumenté, une transition) | lignes | **assistée** : grille de 4 ou 5 critères, le LLM propose un niveau par critère avec la phrase de la copie qui le justifie, le professeur valide |
| Résumé | lignes, nombre de mots imposé | assistée : unités d'idées attendues dans le dossier du correcteur |
| Dissertation, commentaire composé | hors système au départ | professeur |

**Décision du 2026-11-14 : la rédaction longue et la micro-production assistée sont reportées.** Elles feront l'objet d'une réflexion à part ; rien n'est construit pour l'instant. Ce qui suit reste comme note.

Deux franchises :

- **La rédaction longue ne sera pas corrigée automatiquement de façon fiable.** La transcription d'une page manuscrite reste incertaine, et un jugement littéraire faux abîme la confiance. Je propose un mode **assisté** : la machine transcrit, propose, et le professeur tranche en quelques secondes par critère. La valeur est dans le retour ciblé sur un ou deux critères, pas dans la note.
- L'écrit long se **décompose** : on entraîne séparément plan, thèse, exemple, transition. C'est aussi ce qui fait progresser.

Idée à étudier plus tard : un champ « souligne dans le texte » (l'élève souligne les verbes, la CV repère les mots touchés). Utile en grammaire, mais c'est un vrai développement de CV.

### 9.5 Ordre de déploiement proposé

Maths → physique-chimie → SVT → français (langue et compréhension) → anglais → histoire-géographie → philosophie. Du plus vérifiable au moins vérifiable. Chaque matière commence par un pilote de 3 leçons relu à 100 %.

## 10. La correction des réponses écrites

Décision du 2026-11-14 : **abonnement uniquement, pas d'API pour l'instant.**

### 10.1 Par vagues, groupées par exercice

Une **vague** est lancée depuis le Mac (§10.2). Elle regroupe les réponses **par exercice** : l'énoncé et le dossier du correcteur sont écrits une fois, suivis de toutes les copies de la classe. `llm_grader` groupe déjà par paquets de 8. Deux avantages : moins de tokens, et une correction **équitable**, puisque toutes les copies d'un exercice sont jugées ensemble avec la même lecture du barème. Le papier n'a pas besoin de temps réel.

### 10.2 Une ligne de commande, lancée depuis le Mac

Décisions du 2026-11-14 : tout part du Mac ; une commande traite ce qui attend, avec Haiku, Luna ou Terra.

```
vague [corriger] [apprecier] [tout] [--moteur haiku|luna|terra] [--devoir …] [--classe …] [--essai]
```

| Tâche | Ce qu'elle fait |
|---|---|
| `corriger` | corrige les réponses écrites en attente, par exercice, et choisit ou rédige la **micro-correction** de chaque élève |
| `apprecier` | rédige la synthèse de l'appréciation des copies finalisées (aujourd'hui Claude Haiku par API) |
| `tout` | les deux, dans cet ordre |
| à venir, même commande | bilans pour les familles, groupes de besoin, objectif du prochain devoir (§12) |

Fonctionnement :

1. un script liste ce qui attend et prépare des **paquets** : un exercice, son dossier du correcteur, les réponses pseudonymisées de la classe ;
2. pour chaque paquet, il appelle le moteur choisi en ligne de commande, sans outil (`claude -p` pour Haiku, `codex exec` pour Luna ou Terra), et reçoit un JSON ; chaque appel est neuf et court, ce qui convient à un petit modèle ;
3. il vérifie la réponse (barème respecté, message dans le budget de lignes, identifiants connus) et l'écrit dans l'application avec sa confiance ; en dessous du seuil, le professeur tranche ;
4. il affiche un bilan : paquets traités, renvoyés au professeur, en échec.

`--essai` montre ce qui serait fait sans rien écrire. Une vague interrompue reprend où elle s'est arrêtée : un paquet écrit n'est jamais refait.

**Micro-corrections personnalisées** (décision du 2026-11-14). Une réponse tranchée par la machine sans LLM (case cochée, relier, valeur exacte) garde la micro-correction déjà rédigée par la carte : rien ne change. Quand une réponse manuscrite est ambiguë et passe au LLM, c'est lui qui corrige **et** rédige une micro-correction adaptée à ce que l'élève a écrit, à la bonne longueur (le budget de lignes de la carte), sans donner la réponse.

Conséquences à connaître :

- **Les fenêtres d'usage.** Correction et génération partagent les mêmes abonnements. La correction passe d'abord. Si la fenêtre est épuisée, la vague s'arrête proprement et le professeur voit « correction en attente », jamais une note partielle présentée comme finie.
- **Le NAS des professeurs autonomes** ne lance aucune commande. Ses copies attendent que le Mac relève sa file (les deux machines se voient par Tailscale), ou les réponses rédigées y sont corrigées par le professeur : à décider.
- **Conditions d'usage** : corriger des copies d'établissements par un abonnement personnel est à vérifier avant de monter en charge.

### 10.3 Trois étages

| Étage | Quoi | Qui |
|---|---|---|
| 0 | QCM, relier, tracés, valeur exacte | déterministe (existant) |
| 1 | réponse fausse et longue, justification courte, maths et sciences | modèle rapide (Haiku) |
| 2 | raisonnement en plusieurs étapes, démonstration, micro-production littéraire ; et tout ce que l'étage 1 rend avec une confiance basse | modèle fort (Sonnet, Opus si besoin) |
| — | confiance basse à l'étage 2 | professeur |

Le routage sert ici à ménager la fenêtre d'usage : le modèle fort ne voit que ce qui le demande.

### 10.4 Quel modèle

Un **banc d'essai de correction** tranche : 300 réponses réelles déjà corrigées par un professeur (elles sont dans `review_events`), rejouées sur chaque candidat de l'abonnement (Haiku, Sonnet, Opus ; Terra et Luna si Codex est installé). Mesures : accord avec le professeur, **taux de points accordés à tort** (le défaut le plus grave), délai, part de la fenêtre consommée.

- **LoRA sur Qwen : non, pas maintenant.** Il faut des milliers de corrections validées par matière, une machine pour l'héberger, et un petit modèle affiné juge mal un raisonnement nouveau. Chaque correction du professeur est une étiquette : on les accumule dès aujourd'hui. À 20 000 exemples, la question se reposera, et **d'abord pour la lecture de l'écriture**, pas pour le jugement.
- **DeepSeek** : écarté pour l'instant, puisqu'il passe par une API.
- **Si l'abonnement ne suffit plus** (volume, fiabilité), l'API reste le repli : avec Haiku 5.5 à 0,10 $ / 0,50 $ par million de tokens, une classe de 60 élèves et 5 réponses écrites reviennent à environ 0,04 $ par devoir (prix de la table Anthropic du 3 novembre 2026, à revérifier).
- **Confidentialité.** Les copies partent sans nom (règle RM-010).

### 10.5 Ce qui reste sur API aujourd'hui

À traiter au lot 8, ou à laisser en connaissance de cause :

| Usage | Aujourd'hui | Proposition |
|---|---|---|
| Correction des réponses écrites | DeepSeek Flash, API | **remplacé** par la commande `vague` (Haiku, Luna ou Terra), sans période côte à côte |
| Synthèse de l'appréciation | Claude Haiku, API | **gardée**, rédigée par la commande `vague apprecier` |
| Lecture de l'écriture | moteurs locaux, puis Mathpix en dernier recours (API) | **inchangé** : Mathpix est gardé |

Le prompt de `llm_grader` dit « professeur de mathématiques au collège (France) » : il doit être composé à partir de la matière, du niveau et du programme.

## 11. Mettre en œuvre : lots, modèles, coût en tokens

### 11.1 Qui implémente

- **Le moins de tokens au total** : un seul agent, à la suite. Chaque sous-agent repart à froid et relit ce que je sais déjà.
- **Le moins de tokens chers** : un mélange. Fable pour ce qui est difficile ou structurant, Sonnet ou Opus pour l'exécution bien spécifiée.

**Recommandation : un mélange, piloté par moi.** Je lance les sous-agents moi-même, avec un cahier des charges autonome, et je vérifie leur travail (tests, diff). Tu n'as pas à lancer des agents à partir de prompts : tu perdrais la vérification croisée et le contexte. Un sous-agent ne vaut le coup que pour un lot **autonome et volumineux** ; en dessous, je le fais directement.

| Travail | Qui |
|---|---|
| Contrats Pydantic, moteur de familles, contrôle géométrique, chaîne de portes, file de génération | Fable |
| Doctrine, archétypes, profil `ci.md`, modèle de fiche | Fable (relu par toi) |
| Découpage de `forge.py` en modules, à comportement égal, sous tests | Sonnet, relu par Fable |
| Kits de figures (`kn-labo`, mécanique, frise), sur planche | Opus ou Sonnet, planche validée par toi |
| Fiches de leçon | Fable (lycée), Opus (collège), lancés par toi avec `forge contexte` |
| Bancs d'essai (auteur, correcteur), tableaux de bord | Sonnet |

**Une session par lot.** Ce document sert de passage de relais : une session neuve lit ce plan et la section de son lot, rien d'autre.

### 11.2 Lots, dans l'ordre

| Lot | Contenu | Sortie vérifiable |
|---|---|---|
| 0 | Musée des défauts : les défauts déjà connus (pièges de FORGE.md, six reprises du 8 novembre) en cas de test | les cas échouent aujourd'hui là où ils doivent |
| 1 | **Contrôle géométrique** du PDF, branché sur `render` | il retrouve les défauts du musée sur les cartes existantes ; rapport sur toute la banque |
| 2 | Contrats Pydantic, `schema.md` produit par le code, contrat à la carte ; découpage de `forge.py` | les runs existants passent `check` à l'identique |
| 3 | Gabarits et **familles** : jeux de valeurs, rendu des pires cas ; **dispositions écrites par le modèle** et contrôlées par le script, jumelles (§13.2 à §13.4) | une leçon de 3e régénérée en familles ; aucune permutation par script sur une nouvelle carte |
| 4 | Doctrine maths + archétypes + étalons ; registre graphique ; fiche de leçon allégée | relus par toi |
| 4 bis | **Étape contexte** : contrôles par script de `forge contexte` (aucune phrase reprise, compétences couvertes, taille), carte de compétence en YAML, bloc « idées » dans le contexte d'un lot, retrait de `source_text` ; fiches françaises de 3e et 6e refaites au nouveau format (§6) | une fiche refusée si elle recopie sa source ; un lot généré sans aucun texte de manuel |
| 5 | Programme ivoirien : `ci.md`, filtre de progression, résolution des séries, volumes par formule | `forge ci 3e E2` de bout en bout |
| 6 | File de génération à la demande, lanceur de nuit, indice de confiance et tableau de bord | une semaine de génération sans intervention |
| 6 bis | Anti-triche côté application : carte et jumelles regroupées, règle des voisins, liste mise à jour par le dernier lot scanné, corrigé par jeu de valeurs, signal « à regarder » (§13.5 à §13.7) | une classe simulée de 60 : aucun voisin avec les mêmes valeurs ; une copie recopiée est repérée |
| 7 | Supports et mascotte : `\Copie`, `\Calculatrice`, `\Ticket`, `\Mascotte` (après la planche de l'illustrateur) | validés contre la CV |
| 8 | Commande `vague` (corriger, apprécier) sur abonnement, banc d'essai, routeur à étages, prompt par matière (§10) | une classe corrigée et appréciée sans appel d'API de LLM ; choix du moteur sur chiffres |
| 9 | Physique-chimie : doctrine, kits, `pint`, pilote de 3 leçons | relecture à 100 % |
| 10+ | SVT (schéma à légender, bibliothèque), français, puis les autres | un pilote par matière |
| en parallèle | Suivi des élèves (§12), indépendant de Forge | — |

Les lots 0 et 1 améliorent tout de suite la banque existante, sans rien régénérer : c'est par là que la confiance revient le plus vite.

## 12. Mieux faire progresser et mieux accompagner

L'existant est solide : courbe d'oubli par compétence, journal d'erreurs avec dénominateur, tirage individuel, micro-corrections. Ce qui manque est surtout de **fermer les boucles et de les rendre visibles**.

Retenu le 2026-11-14 :

1. **La revanche.** Une erreur type active → au sujet suivant, une jumelle de la même famille, marquée « Revanche ». Deux réussites → « erreur vaincue », imprimé sur la copie.
2. **Un objectif par devoir.** En tête du sujet individuel : « Ton objectif : ne plus confondre rayon et diamètre. » Sur la copie corrigée : atteint ou pas encore.
3. **Les groupes de besoin.** Après chaque devoir, trois groupes d'élèves par erreur dominante, chacun avec sa fiche prête à imprimer, et « les trois erreurs à reprendre au tableau ».
4. **Le bilan pour la famille.** Une page papier par trimestre : trois acquis, une chose à travailler avec un geste concret à la maison, une courbe comparée au passé de l'élève, jamais aux autres. Validé par le professeur.
5. **Prêt pour l'examen.** Avant le BEPC ou le BAC : part des compétences de l'examen encore solides aujourd'hui (maîtrise × fraîcheur), et un cahier de révision personnel tiré de la courbe d'oubli.

Écartés : la coche « sûr / pas sûr », la cause racine, l'escalade de l'aide, les liens entre matières.

## 13. Anti-triche, pour toutes les matières

Décisions prises le 2026-11-14 : aucun script ne mélange la disposition des réponses d'une nouvelle carte, c'est le modèle qui écrit chaque disposition ; anti-triche actif par défaut, avec des valeurs différentes entre voisins ; repérage après coup accepté.

### 13.1 Ce qui existait, et ses défauts

Existant : mélange par script des propositions de QCM, des lignes de grille cochée, des lignes ou colonnes de tableau, de la colonne droite des relier (`services/answer_mix.py`), ordre des exercices, et trois variantes de sujet en mode « commun avec variantes » (`distribution.variant_seed`).

| Défaut | Cause dans le code | Conséquence |
|---|---|---|
| Des lignes qui se suivent logiquement sont mélangées | le mélange est **deviné** par une liste de mots (« étape », « total ») et un test sur l'ordre des nombres | un raisonnement en étapes, une chronologie ou un protocole sortent dans le désordre |
| Un tableau mélange ses lignes alors que ses colonnes portent les entités | sans en-têtes de colonnes, le script se rabat sur les lignes | tableau illisible, ou anti-triche sans effet |
| Deux voisins de devant et de derrière peuvent avoir la même copie | la variante vaut `rang % 3` | copie possible d'une rangée à l'autre |
| Seule la disposition change | pas de variante de valeurs | un voisin dicte « 12 » : la disposition n'y change rien |

**Corrigé le 2026-11-14, pour les nouveaux sujets** : les lignes d'une grille cochée ou d'un tableau ne bougent plus sans déclaration `mix: "rows"` de la carte. Les copies déjà composées gardent leur ordre, figé sur chaque copie.

### 13.2 Le modèle écrit les dispositions, le script vérifie

Pour toute nouvelle famille, **le script ne permute plus rien**. Le modèle écrit lui-même deux dispositions, en sachant ce qu'il déplace :

```yaml
dispositions:
  D1: {propositions: [a, b, c, d]}
  D2: {propositions: [c, a, d, b]}
ordre_impose: "aucun"          # ou la raison : logique, chronologique, croissant, protocole
```

- Il déplace ce qui est indépendant : propositions d'un QCM, colonnes d'un tableau dont chaque colonne est un objet, éléments de droite d'un relier, affirmations sans lien d'un vrai-faux.
- Il ne déplace **jamais** des lignes qui se suivent (étapes, chronologie, protocole, valeurs croissantes). Si rien ne peut bouger, la famille le dit (`ordre_impose`) et n'a qu'une disposition : l'anti-triche repose alors sur les valeurs.
- Pour un `table_fill`, ce sont les colonnes qui bougent. Des lignes ne bougent que si chacune est un objet indépendant, et le modèle doit le justifier.
- Il évite les dispositions qui se trahissent : la bonne réponse toujours en premier, deux dispositions presque identiques.

Le script **contrôle** sans rien modifier :

- les deux dispositions contiennent exactement les mêmes éléments ;
- la bonne réponse et chaque message suivent leur élément, par identifiant ;
- la bonne réponse n'est pas à la même place dans D1 et D2 ;
- chaque disposition est rendue, passe le contrôle géométrique (§5.3), et est relue par le lecteur sans contexte : si sa réponse ou sa compréhension change, la famille est refusée.

Les cartes déjà en banque gardent le mélange par script, réduit aux cas sûrs (propositions, colonnes, relier), jusqu'à leur remplacement.

### 13.3 Les jumelles d'une carte

Une famille donne **4 ou 6 jumelles** : 2 ou 3 jeux de valeurs, chacun en 2 dispositions. L'énoncé est le même.

| Ce qui change | Qui l'écrit | Matières |
|---|---|---|
| **Disposition** (2) | le modèle, une fois pour la famille | toutes |
| **Jeu de valeurs** (2 ou 3) : nombres, mesures ; au choix du modèle, aussi les noms des points et les prénoms | les paramètres de la famille ; le script calcule réponses et erreurs | maths, physique-chimie, SVT et géographie chiffrées |
| **Jeu d'items** (2 ou 3), quand il n'y a pas de nombres : d'autres phrases de même structure, d'autres dates | le modèle | langues, grammaire, histoire, SVT, vocabulaire |
| Ordre des exercices dans le sujet, à difficulté égale | existant | toutes |

Changer aussi les noms des points d'un jeu à l'autre est utile : un voisin qui lit « $MN = 12$ » ne sait plus à quoi cela correspond sur sa copie, où le segment s'appelle $[AB]$.

Un texte de compréhension reste le même pour tous : seules changent la disposition des réponses et, quand elles ne suivent pas le texte, l'ordre des questions.

### 13.4 Des jumelles de difficulté égale

Une jumelle plus dure qu'une autre est une injustice. Trois garde-fous :

1. **Contraintes de famille** : même nombre d'étapes, même nature de nombres (toutes entières, ou toutes décimales), même présence ou absence de retenue, de signe moins, de simplification, même forme de réponse. Le script rejette un jeu qui sort de la classe.
2. **Réponses distinctes** : deux jeux de valeurs n'ont jamais la même réponse, et la bonne réponse de l'un n'est jamais une erreur prévue de l'autre (utile en §13.7).
3. **Terrain** : les taux de réussite sont suivis par jumelle. Une jumelle qui s'écarte nettement est retirée.

Chaque jumelle passe les contrôles automatiques (portes 1 à 5). Les contrôles par modèle portent sur un jeu de valeurs et sur les deux dispositions.

### 13.5 Dans l'interface : une carte, ses jumelles

- Le professeur voit **une seule carte** par famille, avec une pastille « 3 jeux de valeurs · 2 dispositions ». Il peut les feuilleter.
- Il choisit la carte ; les jumelles sont distribuées par le système.
- Pouce levé, pouce baissé et signalement portent sur la famille ; un signalement peut viser une jumelle précise.
- Les statistiques sont agrégées par famille, détaillées par jumelle.

**L'anti-triche est systématique** (décision du 2026-11-14) : valeurs différentes entre voisins, dispositions différentes, ordre des exercices. Il n'y a pas de réglage pour le baisser.

Comme les valeurs diffèrent, le professeur reçoit un **corrigé par jeu de valeurs** et un corrigé de méthode commun. Les micro-corrections sont individuelles, donc justes pour chaque copie.

### 13.6 Les voisins

La liste de la classe est l'ordre de distribution : deux élèves qui se suivent sont assis côte à côte.

- **Voisins immédiats : toujours des valeurs différentes.** Si la famille n'a qu'un jeu, une disposition différente.
- **Répartition sans autre donnée :**
  - 3 jeux × 2 dispositions : l'élève de rang $i$ reçoit le jeu $i \bmod 3$ et la disposition $i \bmod 2$. Deux copies identiques sont à 6 places au moins.
  - 2 jeux × 2 dispositions : le jeu alterne à chaque place, la disposition change toutes les deux places. Deux copies identiques sont à 4 places ; deux élèves aux mêmes valeurs, à 2 places, n'ont pas la même disposition.
- **La liste se met à jour seule.** L'ordre du dernier lot scanné (l'ordre de ramassage) devient l'ordre de la liste de la classe. Pas de plan de salle, pas de réglage.
- **La pile fait foi.** Les copies sont imprimées dans l'ordre de la liste : même distribuées sans regarder les noms, deux copies qui se suivent diffèrent. Les copies de réserve et les copies en retard reçoivent une combinaison libre.
- **Sujets individuels.** Chaque élève a déjà ses propres exercices ; la règle s'applique aux cartes que deux voisins ont en commun.
- **Mémoire.** Un élève ne reçoit pas un jeu de valeurs qu'il a déjà eu : c'est aussi ce qui sert la revanche (§12).

### 13.7 Repérer la copie après coup

Comme deux voisins n'ont ni les mêmes valeurs ni la même disposition, une réponse copiée laisse une trace :

- **Réponse écrite** : l'élève a écrit la bonne réponse du jeu de valeurs de son voisin, qui n'est ni sa bonne réponse ni une erreur prévue de sa propre carte.
- **Réponse cochée** : ses coches reproduisent les **positions** justes de la copie voisine. On note la copie avec le corrigé du voisin : si elle y obtient nettement plus qu'avec le sien, le hasard ne l'explique pas.

Ce signal va **au professeur seul**, à partir de deux indices concordants sur une même copie, sous la forme « à regarder », avec les deux copies côte à côte. Il n'est jamais imprimé, jamais envoyé à l'élève ni à la famille, et ne change pas la note : le professeur juge. La réponse n'entre pas dans le journal des erreurs de l'élève, pour ne pas fausser son suivi.

### 13.8 Ce que cela demande

- **Contrat** : `dispositions`, `ordre_impose`, jeux de valeurs ou d'items dans la famille (lot 3).
- **Carte figée** : la `CopyItem` enregistre déjà la carte imprimée ; elle porte en plus la famille, le jeu de valeurs et la disposition. CV, note et micro-corrections lisent ces champs.
- **Répartition** : `variant_seed` remplacé par la règle des voisins ; liste de la classe mise à jour par l'ordre du dernier lot scanné.
- **Interface** : carte et jumelles regroupées, corrigé par jeu de valeurs.
- **Figures** : chaque jeu de valeurs a sa figure. La réserve d'images pré-rendues pour le NAS grossit d'autant : elle devra sortir du dépôt Git pour un dossier de données synchronisé.

## 14. Anomalies relevées à la lecture

Une seule correction faite (ligne 8, à ta demande). Le reste est à corriger pendant les lots indiqués.

| # | Constat | Où | Lot |
|---|---|---|---|
| 1 | **Mélange de programmes.** Les sources, les exemples et la part de figures sont cherchés par niveau et code de chapitre, sans le programme. `forge ci 3e A1` (Calcul littéral ivoirien) recevrait le texte Indigo du chapitre A1 français de 3e, qui est un autre chapitre | `forge.py` : `chapter_table`, `source_text`, `published_examples`, `figure_share` | 5 |
| 2 | Les séries du lycée ne se résolvent pas : `framework_for` filtre `path_id=None`, alors que les 10 référentiels ivoiriens du lycée ont un `path_id` (`ci-C`, `ci-D`…) | `forge.py` : `framework_for` | 5 |
| 3 | L'ordre de lecture cite `programmes/ci.md`, qui n'existe pas, sans le vérifier | `build_context` | 5 |
| 4 | Prompts câblés « mathématiques », et « collège (France) » pour le correcteur | `goldfish_prompts/eleve.md`, `llm_grader.py` (vers la ligne 340), `appreciation.py` | 8 |
| 5 | Fiches de chapitre avec des recettes de l'ancien moteur `geo`, que `check` refuse | fiches 3e C1 à C4 | 4 |
| 6 | Test instable déjà connu : `query(Competency).first()` sans ordre | `test_late_copies.py` | 2 |
| 7 | `schema.md` écrit à la main, peut diverger du code | — | 2 |
| 8 | Mélange deviné par mots-clés : des lignes en suite logique pouvaient être mélangées ; un tableau sans en-têtes de colonnes mélangeait ses lignes (§13.1) | `answer_mix.py` : `_mix_table`, `_mix_grid` | **corrigé le 2026-11-14** ; le reste au lot 3 |
| 9 | Variante de sujet = `rang % 3` : seuls les voisins de liste diffèrent | `distribution.py` : `variant_seed` | 6 |

## 15. Décisions

### Prises (2026-11-14)

| Sujet | Décision |
|---|---|
| Disposition des réponses | écrite par le modèle, jamais mélangée par un script (§13.2) |
| Anti-triche | actif par défaut ; 2 ou 3 jeux de valeurs × 2 dispositions, soit 4 ou 6 jumelles (§13.3) |
| Voisins | valeurs différentes entre voisins, par défaut (§13.5, §13.6) |
| Repérage après coup | accepté, réservé au professeur (§13.7) |
| Mélange des lignes | corrigé pour les nouveaux sujets seulement |
| Manuels | option B : facultatifs, à l'étape contexte ; leurs idées ne servent que leur programme, pas de partage avec l'ivoirien (§6.5) |
| Deux étapes | contexte par Fable (lycée) ou Opus (collège) avec `forge contexte`, puis exercices à partir de la fiche (§6.1) |
| Génération | abonnement uniquement, contexte et exercices |
| Correction des copies | abonnement uniquement, pas d'API pour l'instant (§10) |
| Où tournent les commandes | sur le Mac uniquement |
| Mathpix | gardé, inchangé |
| Appréciation | synthèse gardée ; rédigée par la commande `vague`, comme la correction et les micro-corrections personnalisées (§10.2) |

### Prises le 2026-11-14, sur la liste des 59 questions

Les propositions non commentées sont acceptées. Les réponses qui les modifient :

| N° | Décision |
|---|---|
| 5 | Opus seul écrit les exercices, pour l'instant |
| 6 | le lecteur indépendant est Sonnet |
| 12 | calendrier : poser les bases seulement ; pas de demande directe d'un professeur ; épuisement : oui |
| 15 | tu relis seul, dans toutes les matières |
| 17 | pas de passage du contrôle géométrique sur la banque existante |
| 19 | contre-lecture retirée |
| 23 | pas de relecteur ivoirien |
| 28 | tu fournis la planche des personnages, avec les noms des positions et des émotions |
| 29 | l'encadré jaune du guide ne change pas |
| 33 | schéma à légender : l'élève écrit le mot dans une case |
| 36 | rédaction longue : rien pour l'instant |
| 40 | pas de réglage « élèves par rangée » ; l'ordre du dernier lot scanné met à jour la liste de la classe |
| 41 | anti-triche systématique, sans réglage pour le baisser |
| 47 | le LLM ne rédige une micro-correction que lorsqu'il corrige lui-même une réponse manuscrite ambiguë |
| 50 | DeepSeek remplacé par la commande manuelle (Haiku, Luna, Terra), sans période côte à côte |
| 52 | suivi : revanche, groupes de besoin, objectif par devoir, bilan famille, prêt pour l'examen |
| 53 | pas de coche « sûr / pas sûr » |
| 54 | bilan famille : une page papier par trimestre |
| 57 | les lots s'enchaînent sans validation intermédiaire |

### À préciser

- **N° 43** : repérage de la copie après coup (§13.7) abandonné le 2026-11-15.

### Que je ne peux pas trancher seul

- le régime des droits : textes littéraires et historiques, et un avis sur l'usage d'idées tirées d'un manuel dans un produit commercial ;
- les conditions d'usage des abonnements pour une génération automatisée de nuit et pour la correction de copies d'établissements ;

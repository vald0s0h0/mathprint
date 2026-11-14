# Forge : améliorer la qualité de la génération des exercices

- **Statut** : proposition, à revoir. Rien n'est implémenté.
- **Date** : 2026-11-14
- **Périmètre** : pipeline Forge (`agents/forge/`), programmes français (`fr`) et ivoirien (`ci`), mathématiques.
- **Origine** : critique du pipeline actuel après lecture de FORGE.md, schema.md, forge.py, des étalons, des fiches et de deux runs (3e C2.1, 6e A3.2). Aucun code modifié.

Contrainte de départ : les cartes ne sont pas relues une par une, elles doivent être justes, cohérentes et sans contre-sens. Le but est d'ajouter du fun et du sens sans dénaturer la qualité pédagogique, et de rendre la génération structurée plutôt qu'aléatoire.

## 1. Critique du pipeline actuel

**Ce qui marche.** Le contrôle technique est solide : calculs vérifiés, rendu réel, écriture simulée, lecture Goldfish, taxonomie d'erreurs fermée. Presque tous les commentaires de reprise portent sur la mise en page (« blank trop serré », « centrer »), pas sur les maths. Le point faible n'est plus la justesse mais l'intérêt pédagogique.

**Causes de l'aléatoire et de la pauvreté :**

1. **La grille fixe le format, jamais l'intention.** `exercise_grid` décide niveau, format et taille. Le type de tâche (calcul, diagnostic d'erreur, décision, estimation…) est laissé au modèle. Résultat uniforme :
   - C2.1 (Thalès) : 17 énoncés sur 20 commencent par « Les points … sont alignés / les droites … sont parallèles ». Seuls 2 ont une vraie situation (une tente, un arbre).
   - 6e A3.2 : s01, s02, s04, s07 et s09 sont tous des « Calcule … ».
2. **Les étalons donnent le ton, et il est sec.** Les 10 étalons sont des tâches courtes sans contexte (priorités, divisibilité, puissances). Aucun étalon de situation, d'erreur d'élève ou de document : le modèle imite ce qu'il voit.
3. **Indigo est fourni en texte brut, pas en idées.** `source_text` colle les pages d'exercices du manuel. L'anti-copie ne repère que 20 mots consécutifs identiques (`COPY_RUN_WORDS`) : une paraphrase passe, et une bonne idée n'est jamais isolée. Indigo n'existe que pour la 3e et la 6e, et rien pour l'ivoirien.
4. **Les fiches mélangent pédagogie et moteur, et ont vieilli.** 30 à 40 Ko (C1 à C4), avec des recettes de coordonnées pour l'ancien moteur `geo` (107 mentions de `geo`, `lines`, `parallel_marks`), alors que `check` refuse désormais ce moteur. Des tokens payés à chaque run, et des contradictions possibles.
5. **Le profil programme est quasi vide.** `programmes/fr.md` fait 544 octets, et il n'existe pas de `ci.md`.
6. **Toujours 20 exercices par compétence.** Appliqué aux 3 107 compétences ivoiriennes : environ 62 000 exercices, dont 20 par « Connaître la définition de… » (une compétence de 25 minutes).
7. **Vérifications automatiques manquantes :**
   - rien ne compare les cotes d'une figure TikZ à ses coordonnées ;
   - personne ne juge si la situation est plausible, ni si les maths sont vraiment nécessaires pour répondre ;
   - les rédactions ne passent pas par un second solveur.
8. **Exploitation interactive.** Sessions Claude Code, 3 sous-agents au plus, reprises qui relisent tout : ne passe pas à l'échelle de l'ivoirien.

## 2. Structurer les documents : un rôle par niveau

Chaque niveau a un rôle et un format distincts.

| Niveau | Contenu | Format | Stabilité |
|---|---|---|---|
| **Doctrine (par matière)** | Ce qu'est un bon exercice, catalogue d'**archétypes**, catalogue de **supports visuels**, contre-sens interdits, 2 étalons par archétype | Markdown + JSON | Change rarement |
| **Profil programme** (`fr`, `ci`) | Notations, monnaie, vocabulaire officiel, prénoms, banque de contextes avec ordres de grandeur vérifiés, format de l'examen (brevet, BEPC) | Markdown | Par programme |
| **Fiche de niveau** | Acquis des années précédentes, calculatrice ou non, ce qui est mobilisable selon la progression | Markdown court | Par niveau |
| **Fiche de leçon** | Pédagogie seule : méthodes, erreurs, progression des micro-savoir-faire. **Aucune recette moteur** (elle va dans le schéma ou les scripts) | Markdown, ~8 Ko | Par chapitre |
| **Carte de compétence** | Micro-savoir-faire, erreurs codées, archétypes autorisés avec poids, supports pertinents, plages de valeurs, fiches-idées sources, volume cible | **YAML lu par le script** | Par compétence |

La carte de compétence est la clé contre l'aléatoire : c'est le **script** qui planifie à partir d'elle, pas le modèle.

### Les archétypes : un second axe de la grille

Chaque emplacement recevrait (niveau, format, **archétype**, **domaine de contexte**), tirés de façon déterministe avec des quotas.

| Code | Archétype | Quota indicatif |
|---|---|---|
| `CALC` | Application directe, exercice classique de révision | ≥ 30 % |
| `REPR` | Changer de représentation (tableau, graphique, formule, figure) | |
| `ERR` | **Copie d'élève erronée** : trouve et corrige l'erreur | ~10 % |
| `QUI` | Deux élèves en désaccord : qui a raison, et pourquoi ? | |
| `CALC-ECRAN` | **Écran de calculatrice** : 2,9999999, `1.5E-4`, priorités tapées sans parenthèses | |
| `ESTIM` | Ordre de grandeur, « ce résultat est-il possible ? » | |
| `DOC` | Lire un document : ticket, facture, horaires, étiquette | |
| `DECIDE` | Prendre une décision (forfait, promotion, trajet) : le « pourquoi des maths » | |
| `INVERSE` | On donne le résultat, il faut retrouver la donnée | |
| `CONTRE` | Vrai ou faux à justifier, contre-exemple | |
| `ALGO` | Programme de calcul, Scratch, tableur | |
| `DEFI` | Petite énigme, pour le fun | ≤ 10 % |

Chaque archétype précise : formats compatibles, niveaux, support visuel, contre-sens propres, 2 étalons.

**Le sens.** Les archétypes situés portent un champ `enjeu` : ce que les maths permettent de décider que le bon sens seul ne permet pas. Si on peut répondre sans maths, la carte est rejetée. Le « voilà à quoi ça sert » peut aller dans `correct_feedback`, qui ne prend pas de place sur le sujet.

### Les supports visuels

Chaque support devient une **macro TikZ paramétrée** de la charte : `\Calculatrice{…}`, `\Copie[Aya]{…}`, `\Ticket{…}`, `\Tableur{…}`, `\Bulle{…}` (SMS), `\Scratch` (le paquet `scratch3` existe sur CTAN). Les valeurs passent en arguments : le script peut les extraire et vérifier qu'un ticket fait bien son total.

**Risque à tester en premier : l'écriture manuscrite imprimée.** Une police OFL de type Patrick Hand ou Kalam, imprimée en bleu, ressemble à l'encre de l'élève. Il faut :
- une couleur distincte de l'encre élève ;
- un cadre « feuille » ;
- aucune `\Copie` près d'une zone de réponse ;
- une validation par la simulation `ecrit/` et une passe de lecture OCR/CV avant toute généralisation.

## 3. Indigo comme source d'idées, y compris pour l'ivoirien

Indigo est une très bonne source. On peut copier le **style** et les **mécanismes**, pas les énoncés. Les contenus FR et CI se recoupent fortement (CI 3e E2 Thalès ↔ FR 3e C2 ; CI E1 triangle rectangle ↔ chapitres Pythagore) : les idées d'Indigo sont transposables vers l'ivoirien. Le droit d'auteur protège l'expression, pas l'idée.

**Méthode : une passe d'extraction, faite une seule fois.** Chaque exercice Indigo devient une **fiche-idée** :

```yaml
mecanisme: "l'élève compare deux méthodes et découvre que l'arrondi intermédiaire fausse le résultat"
pourquoi_bon: "l'erreur est provoquée, pas annoncée"
archetype: QUI
competences: [fr-3e-C2.1, ci-3e-E2.4]
a_ne_pas_reprendre: "contexte, personnages, valeurs, ordre des questions"
```

Pendant la génération, le modèle reçoit **la fiche-idée, jamais le texte d'Indigo**. Le risque de copie disparaît, le contexte coûte moins de tokens, et l'idée devient transposable. La correspondance FR ↔ CI se fait une fois, par leçon.

**Autres sources d'idées :**
- brochures APMEP et IREM (problèmes ouverts, tâches complexes) ;
- annales du brevet ;
- **annales du BEPC** (niveau et format attendus en Côte d'Ivoire) ;
- erreurs observées dans les vraies copies (journal `review_events`).

Les manuels ivoiriens servent au vocabulaire et au ton, pas aux idées pédagogiques. Les guides d'exécution de la DPFC suivent, à vérifier dans `context/programmes-ci/`, l'approche par compétences avec des « situations d'évaluation » (contexte, puis 3 ou 4 consignes). Si c'est confirmé, les compétences « Traiter une situation… » devraient produire des **problèmes** à ce format.

## 4. Spécificités du programme ivoirien

- **Le grain.** Une compétence de la plateforme = un contenu officiel : trop fin. Générer par **habileté** à l'intérieur d'une leçon, avec un volume qui dépend du verbe (section 5).
- **La progression est un vrai filtre.** Le référentiel donne l'ordre et la semaine de début de chaque leçon. En 3e, E2 (Thalès, leçon 2) passe avant E1 (triangle rectangle, leçon 4) : une carte E2 ne doit pas mobiliser Pythagore, contrairement à la fiche française. Le script peut le vérifier.
- **Le profil `ci.md` est à écrire :**
  - FCFA : pas de centimes, prix multiples de 5 ou de 25 selon les pièces en circulation ;
  - contextes réels mais génériques, sans marque : marché, gbaka, forfait mobile, tontine, cacao au kilo, facture d'électricité, pluviométrie, football ;
  - ordres de grandeur sourcés et datés ;
  - prénoms variés ;
  - vocabulaire officiel : « application affine », « ℝ×ℝ ».
- **Calendrier.** Générer chaque leçon 2 à 3 semaines avant sa semaine de début.

## 5. Combien d'exercices générer

Pas de chiffre fixe : un socle selon le verbe, puis des compléments selon la demande des profs.

| Verbe | Socle |
|---|---|
| Connaître, Identifier, Noter | 4 à 6 (QCM, vrai-faux, relier) |
| Calculer, Résoudre, Construire, Développer | 12 à 16 |
| Démontrer, Justifier | 8 |
| Traiter une situation | 6 à 10 problèmes par leçon |

**Ordre de grandeur du besoin.** Un prof fait environ 3 sujets par compétence et par an (diagnostic, entraînement, évaluation). Avec environ 3 exercices par compétence par sujet et 2 versions contre la copie entre voisins : 18 exercices distincts. Les 20 actuels conviennent pour les savoir-faire centraux, pas pour les définitions.

**Ensuite, la demande.** Enregistrer chaque tirage (compétence, niveau, format, établissement). Quand un prof risque de retomber sur un exercice déjà vu, un `--complement` part en file d'attente pour la nuit. Les compléments sont générés avant le pic de demande, jamais en temps réel.

**Données des vraies copies.** Elles permettent de retirer ou régénérer automatiquement :
- un distracteur jamais choisi ;
- une carte avec un fort taux de copies blanches (ambiguïté) ;
- une carte où les bons élèves échouent plus que les faibles (discrimination négative).

## 6. Garantir la justesse sans relire chaque carte

Une chaîne de confiance où l'on **jette plutôt que de réparer** :

1. **Solution exécutable écrite en premier.** Valeurs de l'énoncé déclarées comme paramètres, avec un calcul SymPy. Le script recalcule réponses, distracteurs, totaux des supports et longueurs, et vérifie que l'énoncé contient bien ces valeurs.
2. **Cohérence figure ↔ valeurs.** Le script lit les `\Cote` et `\coordinate`, calcule distances et angles, compare (sauf « pas à l'échelle » déclaré). Ce contrôle manque aujourd'hui.
3. **Deux solveurs indépendants** sur l'image rendue : Goldfish, plus un second modèle pour les difficiles et les rédactions. Désaccord = carte jetée.
4. **Critique pédagogique à liste fermée :** hors programme (référentiel *et* progression) ; plausibilité du contexte ; « les maths sont-elles nécessaires ? » ; archétype respecté ; stéréotypes.
5. **Contrôle par échantillon.** Relecture de 10 % de chaque lot au hasard, plus toutes les cartes signalées. Une erreur grave dans l'échantillon fait rejeter le lot. La relecture humaine devient une mesure de qualité, plus un passage obligé.
6. **En production,** l'analyse des réponses réelles prend le relais (section 5).

## 7. Cache d'Opus pour générer en volume

Pour la production, sortir de la session interactive : un script `run.py generate` appelle l'API, **un emplacement par requête**. La variété vient du plan, pas du fait que le modèle voie les cartes voisines.

**Ordre de la requête, du plus stable au moins stable**, un point de cache après chaque bloc (4 au plus) :

1. Doctrine, schéma, charte, étalons (TTL 1 h)
2. Profil programme et fiche de niveau
3. Fiche de leçon
4. Carte de compétence et `existants`
5. Hors cache : spécification de l'emplacement (archétype, fiche-idée, contexte imposé)

**Chiffres Opus 5.5** (à revérifier avant décision) :
- 4 $ / 20 $ par million de tokens (entrée / sortie) ;
- lecture en cache : 0,05× le prix d'entrée (0,20 $ / M) ;
- écriture : 1,25× (TTL 5 min) ou 2× (TTL 1 h) ;
- préfixe minimal pour le cache : 512 tokens.

**API Batches : -50 %**, mais le cache y est « au mieux ». Regrouper les requêtes par chapitre pour partager le préfixe, et vérifier que `cache_read_input_tokens` n'est pas à zéro (une date ou un JSON non trié dans le préfixe casse le cache).

Avec le cache, l'entrée devient négligeable : le coût réel, ce sont les tokens de sortie (raisonnement compris) et les reprises. Le levier d'économie est de **moins réécrire** : mieux planifier vaut mieux que mieux cacher.

## 8. LoRA à la place d'Opus

Déconseillé pour le besoin principal :
- **Opus n'est pas affinable** (poids fermés, pas de fine-tuning proposé à ma connaissance). Un LoRA impose un modèle ouvert.
- **Un LoRA apprend un style et un format, pas la fiabilité du raisonnement** : cartes qui *ressemblent* aux nôtres, avec plus d'erreurs mathématiques énoncées avec assurance, ce qu'on ne peut pas se permettre sans relecture.
- **Il faut des milliers de cartes validées** ; il y en a quelques centaines, surtout en brouillon.

**À la place :**
- **Variantes paramétriques par script** : une carte validée, réduite à ses paramètres et à sa solution SymPy, donne N variantes justes par construction, sans appel au modèle. C'est le vrai levier de volume et de coût.
- **Plus tard, un petit modèle pour une tâche étroite** (trier les cartes, écrire les micro-corrections), à partir de 2 000 à 3 000 cartes validées.

## Par où commencer (ordre suggéré)

1. Écrire la **doctrine** avec ses archétypes et ses étalons : le plus gros gain de qualité, sans code.
2. Ajouter **archétype** et **contexte** comme second axe de la grille.
3. Lancer la passe **fiches-idées Indigo**, avec la correspondance FR ↔ CI.
4. Écrire le profil **`ci.md`** et le filtre de **progression**.
5. Ajouter la **solution exécutable** et le contrôle **figure ↔ valeurs**.
6. Tester les macros `\Calculatrice` et `\Copie` contre la lecture OCR/CV.

## Points ouverts

- Les guides DPFC décrivent-ils bien des « situations d'évaluation » à reprendre pour les problèmes ?
- Quelle police manuscrite, et quelle couleur, ne perturbent pas l'OCR/CV ?
- Où ranger les fiches-idées Indigo (hors dépôt si elles citent trop le manuel) ?
- Faut-il garder les 20 exercices pour le français et passer au socle par verbe seulement pour l'ivoirien ?

**Cahier des charges fonctionnel et technique**

Plateforme NAS de génération, correction automatisée et suivi adaptatif en mathématiques

|                       |                                                          |
|-----------------------|----------------------------------------------------------|
| **Version**           | 0.9 — document de cadrage complet                        |
| **Date**              | 10 juillet 2026                                          |
| **Cible**             | Collège — niveaux et grille de compétences configurables |
| **Déploiement**       | Synology DS224+ — application web et traitements sur NAS |
| **Services externes** | Mathpix OCR, DeepSeek API, Claude Haiku API              |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Décision d’architecture</strong></p>
<p>Aucun composant Tauri ni programme métier n’est requis sur le poste du professeur. Le navigateur accède au NAS ; le NAS génère les documents, traite les scans et orchestre les API. Les copies ne quittent jamais le NAS sous forme complète : Mathpix reçoit uniquement des zones de réponse recadrées et les LLM uniquement des données pseudonymisées et structurées.</p></td>
</tr>
</tbody>
</table>

## Statut du document

Ce cahier des charges constitue la référence fonctionnelle et technique de la première version. La grille de compétences, les modèles exacts de feuilles, le barème institutionnel et les références d’imprimantes seront fournis ou validés ultérieurement.

# Sommaire fonctionnel

1\. Vision, objectifs et principes directeurs

2\. Utilisateurs, rôles et parcours principaux

3\. Création rapide des sujets et personnalisation

4\. Modèle d’exercices et types de réponses papier

5\. Conception documentaire, couleurs, QR codes et fiduciels

6\. Import, vision par ordinateur, OCR et correction robuste

7\. Notation, compétences, niveau élève et courbe d’oubli

8\. Usage de DeepSeek et Claude Haiku avec budgets de tokens

9\. Interface et écrans

10\. Modèle de données détaillé

11\. Architecture NAS, sécurité, fichiers et sauvegardes

12\. Exigences non fonctionnelles, tests et recette

13\. Découpage de réalisation et décisions à confirmer

# Résumé des choix structurants

| **Sujet**     | **Choix retenu**                                                                    |
|---------------|-------------------------------------------------------------------------------------|
| Application   | Interface web React ; API FastAPI ; services Docker Compose sur le NAS.             |
| Exercices     | MathALÉA auto-hébergé et versionné ; instantané de chaque énoncé et correction.     |
| Documents     | PDF A4 personnalisés par élève ; manifeste de zones associé à chaque page.          |
| Repérage      | 1 QR principal + 3 mini QR/fiduciels noirs aux quatre angles.                       |
| Zones papier  | Cadres rouge-orangé très clair supprimables ; original couleur conservé.            |
| Correction    | Overlay PDF transparent, rouge foncé par défaut, couleur configurable.              |
| OCR           | QCM par CV local ; expressions et écritures via Mathpix \`/v3/text\`.               |
| Décision      | Moteur déterministe prioritaire ; DeepSeek seulement pour adaptation et ambiguïtés. |
| Synthèse      | Claude Haiku pour comptes rendus et conseils courts, jamais pour la note primaire.  |
| Données élève | Nom conservé sur le NAS ; identifiant pseudonyme uniquement vers les API.           |

# 1. Vision, objectifs et principes directeurs

## 1.1 Objectif produit

La plateforme doit réduire fortement le temps consacré par le professeur à la préparation, à la correction et à la reprise des exercices, tout en conservant un contrôle humain explicite sur les cas problématiques. Elle génère des feuilles imprimables, identifie les réponses manuscrites, propose une correction, met à jour le suivi pédagogique et prépare les exercices suivants.

## 1.2 Objectifs fonctionnels

Créer un contrôle ou une feuille d’entraînement en quelques clics.

Permettre une copie entièrement personnalisée pour chaque élève.

Associer chaque exercice à une ou plusieurs compétences et à une difficulté de 1 à 10.

Détecter automatiquement les pages, les cases, les QCM et les réponses manuscrites.

Corriger automatiquement les cas sûrs et présenter uniquement les exceptions au professeur.

Produire une correction personnalisée réimprimable en rouge sur la copie originale.

Suivre la progression, le niveau indicatif et les compétences dans le temps.

Programmer automatiquement les révisions selon une courbe d’oubli.

Limiter les données envoyées aux services externes et suivre tous les coûts d’API.

## 1.3 Principes de sûreté

| **Principe**         | **Exigence**                                                                                            |
|----------------------|---------------------------------------------------------------------------------------------------------|
| Pas de supposition   | Une page non identifiée ou mal recadrée est bloquée ; elle n’est jamais attribuée par ordre supposé.    |
| Original immuable    | Le scan couleur original est conservé ; les filtres s’appliquent à une copie de travail.                |
| Traçabilité          | Toute note, proposition de niveau et mise à jour de compétence possède des preuves et un historique.    |
| Déterminisme d’abord | Les égalités numériques et symboliques sont traitées sans LLM lorsque cela est possible.                |
| Confiance graduée    | Une faible confiance réduit l’automatisation mais ne produit jamais une correction silencieuse.         |
| Réversibilité        | Le professeur peut corriger une décision ; le système recalcule les agrégats sans effacer l’historique. |

# 2. Utilisateurs, rôles et parcours principaux

## 2.1 Rôles

| **Rôle**       | **Droits principaux**                                                                    |
|----------------|------------------------------------------------------------------------------------------|
| Administrateur | Comptes, API, sauvegardes, modèles, imprimantes, calibrages, mises à jour et journaux.   |
| Professeur     | Classes, élèves, sujets, scans, validation, notes, compétences, rapports et impressions. |
| Lecture seule  | Consultation des résultats et rapports sans modification ; optionnel en V1.              |

## 2.2 Parcours “Créer un sujet”

1.  Choisir une classe, puis le type : contrôle noté ou entraînement.

2.  Choisir le thème, les compétences ou un objectif pédagogique.

3.  Accepter la proposition automatique ou ajuster le nombre d’exercices, la durée (recto OU recto/verso) et la difficulté s'ajuste au niveau 1-10 de l'élève.

4.  Choisir le degré de personnalisation : sujet commun, variantes équivalentes ou sujet individuel.

5.  Prévisualiser un élève représentatif, les cas les plus faciles et les plus difficiles.

6.  Générer le PDF global et le rapport de contrôle avant impression.

## 2.3 Parcours “Corriger un lot”

1.  Déposer le PDF ou les images du scanner dans l’évaluation concernée (PDF unique, PDF mélangés, multiples etc ou images).

2.  Laisser le NAS identifier, recadrer, extraire et OCRiser chaque réponse.

3.  Consulter une progression par étapes et les éventuels incidents techniques.

4.  Valider uniquement la file des cas incertains : rature, double coche, écriture ambiguë, scan médiocre ou barème incomplet.

5.  Finaliser les résultats puis générer les overlays PDF et les rapports.

6.  Attention à l'ordre des copies. Gestion de l'ordre des feuilles, il y'aura un protocole précis de retournement des feuilles pour sujets recto-verso sur imprimantes non recto verso par exemple. Depuis le scan, les feuilles gardent le même ordre dans l'imprimante.

# 3. Création rapide des sujets et personnalisation

## 3.1 Assistant en quatre écrans

| **Étape**      | **Contenu**                                                                                       | **Valeur par défaut**                    |
|----------------|---------------------------------------------------------------------------------------------------|------------------------------------------|
| 1 — Contexte   | Classe, date, durée, contrôle/entraînement, recto/verso.                                          | Dernière classe utilisée ; entraînement. |
| 2 — Objectif   | Chapitre, compétences, notions à revoir, nombre de questions automatique afin de remplir la page. | Compétences dues selon l’oubli.          |
| 3 — Adaptation | Sujet commun/variantes/individuel, difficulté, rappels de leçon.                                  | Proposition DeepSeek encadrée.           |
| 4 — Validation | Aperçu, équilibre, pages, coûts estimés, génération.                                              | Blocage si anomalie documentaire.        |

## 3.2 Modes de personnalisation

| **Mode**               | **Description**                                                                            | **Usage recommandé**                        |
|------------------------|--------------------------------------------------------------------------------------------|---------------------------------------------|
| Commun                 | Même contenu et même difficulté pour tous ; données numériques éventuellement différentes. | Contrôle comparable.                        |
| Variantes équivalentes | Plusieurs versions de difficulté calibrée identique.                                       | Contrôle anti-copie.                        |
| Individuel encadré     | Sélection adaptée, mais même blueprint de compétences et même plage de difficulté.         | Contrôle différencié, comparaison prudente. |
| Individuel libre       | Exercices, difficulté et rappels personnalisés.                                            | Entraînement.                               |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Règle d’équité</strong></p>
<p>Pour un contrôle, le mode par défaut doit conserver un blueprint commun de compétences et une plage de difficulté limitée. Lorsque les copies diffèrent réellement en difficulté, le tableau de bord doit afficher cette information et interdire une comparaison naïve des notes brutes.</p></td>
</tr>
</tbody>
</table>

## 3.3 Génération MathALÉA

MathALÉA est exécuté dans un conteneur Node.js local, à une version épinglée.

Un adaptateur interne reçoit l’exercice, les paramètres et une seed déterministe.

Le système stocke l’énoncé, la correction, les réponses attendues, la seed et la version exacte.

Un catalogue d’exercices qualifie chaque exercice : compétences, difficulté 1–10, type de réponse, hauteur requise, compatibilité OCR et compatibilité correction automatique.

Les exercices sans réponse structurée disponible sont placés en validation obligatoire ou exclus de l’automatisation.

## 3.4 Rappels de leçon

Les rappels ne doivent pas être inventés librement à chaque génération. Ils proviennent d’une bibliothèque locale, versionnée et validée par un professeur. DeepSeek choisit seulement les identifiants des rappels pertinents.

| **Condition**     | **Insertion proposée**                                                    |
|-------------------|---------------------------------------------------------------------------|
| Niveau global 1–3 | Rappel très court + exemple résolu + première question accessible.        |
| Niveau 4–5        | Rappel de formule ou méthode si la compétence est fragile.                |
| Niveau 6–8        | Aide minimale, uniquement pour une compétence échue ou récemment échouée. |
| Niveau 9–10       | Pas de rappel par défaut ; défi ou transfert possible.                    |

# 4. Modèle d’exercices et types de réponses papier

## 4.1 Types obligatoires

| **Type**         | **Rendu papier**                                                     | **Lecture**                                        | **Correction**                                                        |
|------------------|----------------------------------------------------------------------|----------------------------------------------------|-----------------------------------------------------------------------|
| QCM              | Cases colorées, une ou plusieurs réponses selon règle explicite.     | CV local : remplissage, croix ou coche.            | Comparaison déterministe ; Mathpix seulement si annotation atypique.  |
| Texte simple     | Une ligne ou petite case pour nombre, unité, fraction ou expression. | Mathpix sur crop isolé.                            | Normalisation puis équivalence déterministe ; DeepSeek si ambigu.     |
| Texte multiligne | Zone de développement avec lignes guides légères.                    | Mathpix MMD/LaTeX sur zone complète ou sous-zones. | Rubrique de barème + DeepSeek ; validation si confiance insuffisante. |

## 4.2 Schéma d’un item

| **Champ**           | **Rôle**                                                                              |
|---------------------|---------------------------------------------------------------------------------------|
| item_id             | Identifiant immuable de la question dans la copie.                                    |
| exercise_catalog_id | Référence au type MathALÉA et à son adaptateur.                                       |
| response_type       | qcm_single, qcm_multiple, short_text, multiline_text.                                 |
| expected_schema     | Type mathématique attendu : entier, rationnel, expression, intervalle, texte, étapes. |
| grading_policy      | Comparateur, tolérance, barème partiel, unités et règles de présentation.             |
| competency_weights  | Compétences observées et poids de preuve.                                             |
| difficulty          | Difficulté calibrée de 1 à 10.                                                        |
| lesson_snippet_ids  | Rappels pouvant accompagner l’item.                                                   |
| automation_tier     | auto, auto_with_llm, review_required ou manual.                                       |

## 4.3 Règles QCM

Chaque case possède une zone intérieure de mesure, distincte du cadre imprimé.

Le cadre est imprimé en couleur dropout ; le geste de l’élève doit être noir ou bleu foncé.

La détection compare densité, traits et composantes connexes après suppression du cadre.

Une double réponse interdite ou une rature déclenche une exception, jamais un choix arbitraire.

Pour un QCM multiple, le nombre de réponses attendu et le barème négatif éventuel sont explicites et versionnés.

## 4.4 Règles pour les réponses multiligne

Une réponse longue est divisée conceptuellement en étapes de barème. Mathpix restitue le contenu en MMD/LaTeX ; DeepSeek reçoit la réponse OCRisée, la solution de référence et une rubrique compacte. Il doit retourner les étapes constatées, les preuves textuelles, les points proposés et les incertitudes. Aucune justification absente ne peut être inventée.

# 5. Conception documentaire, couleurs, QR codes et fiduciels

## 5.1 Documents générés

| **Fichier**            | **Contenu**                                                                         |
|------------------------|-------------------------------------------------------------------------------------|
| subject_batch.pdf      | Toutes les copies prêtes à imprimer, dans l’ordre choisi.                           |
| copy_manifest.json     | Identités techniques, pages, zones, positions PDF, réponses attendues et barèmes.   |
| correction_overlay.pdf | Pages transparentes contenant uniquement la correction personnalisée.               |
| corrected_archive.pdf  | Scan original fusionné numériquement avec l’overlay.                                |
| generation_report.json | Avertissements : débordement, zone absente, correction non structurée, coût estimé. |

## 5.2 Couleur dropout des zones

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Choix recommandé : rouge-orangé clair</strong></p>
<p>Les élèves écrivent fréquemment au stylo bleu ; supprimer le bleu pourrait effacer leur réponse. Les cadres, lignes guides et cases QCM seront donc rouge-orangé clair par défaut. Un profil cyan/bleu reste configurable uniquement si l’établissement impose un outil d’écriture noir.</p></td>
</tr>
</tbody>
</table>

| **Élément**        | **Couleur par défaut**                     | **Règle**                                             |
|--------------------|--------------------------------------------|-------------------------------------------------------|
| Cadres de réponse  | Rouge saumon clair, calibré par imprimante | Supprimé dans l’image OCR, conservé dans l’original.  |
| Lignes d’écriture  | Même couleur, faible densité               | Ne doivent pas traverser les symboles après filtrage. |
| Cases QCM          | Même couleur                               | Mesure dans une zone intérieure sans bord.            |
| QR et mini QR      | Noir pur                                   | Jamais filtrés ; contraste maximal.                   |
| Correction overlay | Rouge foncé \#C62828                       | Configurable : rouge, vert, bleu ou noir.             |
| Texte du sujet     | Noir                                       | Ne doit pas être supprimé par le filtre.              |

## 5.3 Pipeline couleur

1.  Conserver le scan couleur original en lecture seule.

2.  Convertir une copie en espace Lab ou HSV.

3.  Retirer/filtrer tout le rouge (pour supprimer le rouge saumon clair).

4.  Produire l’image OCR.

## 5.4 QR principal et trois mini QR

Chaque page comporte quatre marqueurs noirs : un QR principal dans l’angle supérieur droit et trois mini QR/fiduciels dans les autres angles. Les quatre centres permettent une transformation projective complète et une association sûre à la page attendue.

| **Marqueur** | **Position**          | **Contenu**                                           |
|--------------|-----------------------|-------------------------------------------------------|
| QR principal | Haut droit, 22–25 mm  | Version de protocole, page_id opaque, signature HMAC. |
| Mini TL      | Haut gauche, 10–12 mm | Rôle TL + famille de gabarit + checksum.              |
| Mini BL      | Bas gauche, 10–12 mm  | Rôle BL + famille de gabarit + checksum.              |
| Mini BR      | Bas droit, 10–12 mm   | Rôle BR + famille de gabarit + checksum.              |

Le nom de l’élève, la classe et la note ne figurent jamais dans le QR.

Chaque recto et chaque verso possède un page_id distinct.

La signature HMAC est vérifiée avant toute association en base.

Si un mini QR n’est pas décodable, ses quatre coins peuvent rester utilisables comme fiduciel géométrique.

Si moins de quatre points géométriques fiables sont disponibles, la page est recalée en mode affine ou envoyée en vérification selon l’erreur mesurée.

## 5.5 Coordonnées et manifeste

Toutes les zones sont stockées en points PDF dans un référentiel A4 canonique : page, x, y, largeur, hauteur et marge OCR. Après homographie, ces coordonnées sont converties en pixels. Le manifeste est immuable après impression ; une modification génère une nouvelle version de document et de nouveaux page_id.

## 5.6 Overlay de correction

Fond réellement transparent ou blanc A4 ; seules les annotations sont dessinées.

Rouge foncé par défaut, épaisseur et taille configurables.

Les corrections sont limitées aux zones réservées ; aucune annotation ne recouvre le QR.

Une version numérique fusionnée est produite pour l’archive ; l’overlay seul sert à la réimpression.

Chaque imprimante possède un profil de calibration : translation X/Y, échelle X/Y, rotation, recto/verso et bac papier.

# 6. Import, vision par ordinateur, OCR et correction robuste

## 6.1 Machine d’états d’un lot

| **État**       | **Sortie attendue**                                         |
|----------------|-------------------------------------------------------------|
| uploaded       | Fichier original enregistré, hash calculé, lot créé.        |
| split          | Pages rasterisées et indexées.                              |
| identified     | QR validé et page associée.                                 |
| registered     | Homographie validée et erreur géométrique enregistrée.      |
| cropped        | Zones extraites, filtres appliqués, qualité évaluée.        |
| ocr_complete   | Résultats Mathpix ou CV local associés aux zones.           |
| graded         | Décisions automatiques et scores de confiance produits.     |
| review_pending | Exceptions groupées pour le professeur.                     |
| finalized      | Décisions verrouillées, agrégats et compétences mis à jour. |
| overlay_ready  | PDF transparent et archive corrigée disponibles.            |

## 6.2 Contrôles de qualité scan

Résolution et dimensions cohérentes avec le profil scanner.

Détection de flou, surexposition, ombres, pages tronquées et rotation.

Lecture et validation cryptographique du QR principal.

Présence et géométrie des quatre marqueurs ; erreur de reprojection mesurée.

Détection de doublon par hash perceptuel et page_id.

Détection d’une page manquante ou d’une page appartenant à un autre lot.

## 6.3 Appels Mathpix

Le worker envoie une image par zone de réponse avec \`POST /v3/text\`, et non le PDF complet. La réponse attendue est stockée sous forme brute et normalisée : MMD/LaTeX, texte, confiance, indicateur manuscrit et identifiant de requête.

| **Règle**       | **Exigence**                                                                                   |
|-----------------|------------------------------------------------------------------------------------------------|
| Anonymisation   | Crop sans nom, QR, classe ni page complète.                                                    |
| Confidentialité | \`metadata.improve_mathpix=false\` sur chaque requête.                                         |
| Formats         | JPEG/PNG compact, niveaux de gris si pertinent, lisibilité conservée.                          |
| Concurrence     | Valeur configurable, par défaut 3 requêtes simultanées.                                        |
| Reprise         | Retry exponentiel sur 429/5xx ; idempotency_key locale ; pas de double facturation volontaire. |
| Seconde lecture | Uniquement si confiance faible ou désaccord ; variante de prétraitement différente.            |
| Quotas          | Limites quotidiennes/mensuelles, estimation et alerte avant dépassement.                       |

## 6.4 Échelle de décision

| **Niveau** | **Traitement**                                                             | **Résultat**                                                             |
|------------|----------------------------------------------------------------------------|--------------------------------------------------------------------------|
| A          | QCM ou réponse déterministe, OCR très fiable.                              | Validation automatique.                                                  |
| B          | Équivalence symbolique fiable après normalisation.                         | Validation automatique avec trace.                                       |
| C          | Réponse multiligne ou ambiguïté limitée.                                   | DeepSeek applique la rubrique ; acceptation si confiance globale élevée. |
| D          | Deux OCR divergents, rature, double coche, scan faible, preuve incomplète. | File professeur.                                                         |
| E          | QR invalide, page inconnue, zone absente ou corruption.                    | Blocage technique ; aucune note.                                         |

## 6.5 Normalisation mathématique

Conserver la chaîne Mathpix originale sans modification.

Convertir séparateurs décimaux français, espaces, parenthèses et notations équivalentes.

Parser vers un AST mathématique ; utiliser SymPy/Giac pour simplification et équivalence lorsque le type l’autorise.

Traiter explicitement unités, ensembles, intervalles, équations et tolérances numériques.

Refuser une comparaison si le parseur ne couvre pas le type annoncé.

Ne jamais comparer uniquement les chaînes LaTeX brutes.

## 6.6 Ratures et réponses difficiles

Le système doit détecter les zones surchargées, multiples composantes concurrentes, réponses barrées et changements de réponse. Il peut demander à Mathpix une seconde lecture, puis à DeepSeek une analyse de l’intention seulement si les versions OCR restent cohérentes. Une intention non démontrable produit une exception professeur.

## 6.7 File de validation professeur

L’écran doit optimiser la vitesse : raccourcis clavier, affichage du crop original, image filtrée, rendu Mathpix, réponse attendue, proposition de points et raison de l’incertitude. Les actions sont : accepter, corriger l’OCR, attribuer les points, annuler la question, demander un nouveau scan ou ajouter une remarque.

# 7. Notation, compétences, niveau élève et courbe d’oubli

## 7.1 Contrôle et entraînement

| **Règle**        | **Contrôle noté**                                       | **Entraînement**                             |
|------------------|---------------------------------------------------------|----------------------------------------------|
| Note             | Oui, barème verrouillé après impression.                | Non ; réussite, effort et progression.       |
| Personnalisation | Commune ou encadrée pour préserver l’équité.            | Libre et individualisée.                     |
| Rappel de leçon  | Désactivé par défaut ; explicitement visible si activé. | Automatique pour niveaux fragiles.           |
| Indices          | Aucun ou règle commune.                                 | Possibles et tracés.                         |
| Compétences      | Preuve forte, pondérée par difficulté et conditions.    | Preuve formative, pondérée plus faiblement.  |
| Courbe d’oubli   | Met à jour la maîtrise après finalisation.              | Pilote directement les prochaines questions. |

## 7.2 Grille de compétences

La grille sera importée ultérieurement et doit être versionnée par niveau de classe. Chaque exercice référence une ou plusieurs compétences avec un poids. Le LLM peut proposer une interprétation, mais il ne doit pas créer une compétence inexistante ni remplir une cellule sans preuve.

| **Donnée** | **Exigence**                                                             |
|------------|--------------------------------------------------------------------------|
| Framework  | Nom, classe, année scolaire, version, statut brouillon/publié/archivé.   |
| Compétence | Code stable, libellé, description, prérequis, niveau de classe.          |
| Preuve     | Item, réponse, score, difficulté, mode contrôle/entraînement, date.      |
| État élève | Maîtrise 0–1, confiance, stabilité, dernière preuve, prochaine révision. |
| Historique | Toute variation est append-only et reliée aux preuves.                   |

## 7.3 Niveau global 1 à 10

Le niveau global est une information synthétique réservée au professeur. Il ne doit jamais être affiché sur la copie ni utilisé comme étiquette définitive de l’élève.

Calcul initial déterministe à partir des maîtrises pondérées et du niveau de classe.

DeepSeek peut proposer un ajustement avec justification et identifiants de preuves.

Variation automatique limitée à ±1 par cycle d’évaluation, sauf validation professeur.

Possibilité de verrouiller manuellement le niveau et d’indiquer un motif.

Historique complet : ancien niveau, nouveau niveau, origine, confiance et date.

La sélection d’exercices utilise aussi les maîtrises par compétence ; le niveau global seul ne suffit pas.

À la rentrée, un premier test pour évaluer automatiquement le niveau de l'élève aves des exercices de difficultés en créscendo \> sujet unique et exercices choisi par le LLM en fonction de la classe scolaire, et du programme (liste compétances).

## 7.4 Choix automatique de difficulté

| **Contexte**       | **Répartition indicative**                                        |
|--------------------|-------------------------------------------------------------------|
| Entraînement       | 60 % consolidation accessible, 30 % cible actuelle, 10 % défi.    |
| Compétence fragile | Rappel + exemple + difficulté niveau−1, puis niveau courant.      |
| Compétence échue   | Question courte de rappel avant nouvel apprentissage.             |
| Contrôle           | Blueprint commun ; plage de difficulté définie par le professeur. |

## 7.5 Courbe d’oubli

La planification doit être déterministe et explicable. Pour chaque couple élève–compétence, le système conserve une maîtrise, une stabilité, une difficulté mémorielle, une date de dernière pratique et une date de révision recommandée.

1.  Après chaque réponse finalisée, calculer la qualité de rappel selon exactitude, aide, difficulté et temps écoulé.

2.  Mettre à jour la stabilité : elle augmente après un rappel réussi et diminue après un échec.

3.  Estimer quotidiennement la probabilité de rappel.

4.  Déclarer une compétence due lorsque la probabilité passe sous un seuil configurable, par défaut 0,80.

5.  Prioriser les compétences dues dans les feuilles d’entraînement suivantes, sous contrainte du thème choisi.

6.  Afficher au professeur le motif : oubli probable, échec récent, prérequis fragile ou absence de preuve.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Le LLM n’est pas le planificateur</strong></p>
<p>La courbe d’oubli, les échéances et les changements de maîtrise sont calculés par le moteur. DeepSeek intervient seulement pour choisir parmi des exercices compatibles ou expliquer une recommandation.</p></td>
</tr>
</tbody>
</table>

# 8. Usage de DeepSeek et Claude Haiku avec budgets de tokens

## 8.1 Répartition des responsabilités

| **Service**  | **Fonctions autorisées**                                                                                         | **Fonctions interdites**                                                                    |
|--------------|------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| Mathpix      | Reconnaître une zone manuscrite STEM et retourner MMD/LaTeX + confiance.                                         | Recevoir une copie complète ou l’identité de l’élève.                                       |
| DeepSeek     | Choix d’exercices, proposition de niveau, application d’une rubrique aux réponses complexes, explication courte. | Modifier seul une note finalisée, inventer une compétence, accéder à des outils ou secrets. |
| Claude Haiku | Compte rendu élève, conseils au professeur, résumé de période, formulation claire et encourageante.              | Décider la correction primaire ou établir la vérité d’une réponse mathématique.             |

## 8.2 Modèles configurables

Les identifiants ne doivent jamais être codés en dur. Le réglage par défaut proposé en juillet 2026 est DeepSeek V4 Flash pour les tâches économiques, avec mode raisonnement seulement lorsque nécessaire, et Claude Haiku 4.5 épinglé pour la rédaction courte. Les anciens alias \`deepseek-chat\` et \`deepseek-reasoner\` sont annoncés comme dépréciés au 24 juillet 2026 ; la configuration doit donc utiliser un registre de modèles modifiable.

## 8.3 Minimisation des tokens

| **Mécanisme**          | **Exigence**                                                                                            |
|------------------------|---------------------------------------------------------------------------------------------------------|
| Pas d’appel par défaut | Aucun LLM pour QR, QCM, équivalence numérique, planification de l’oubli ou agrégats.                    |
| Données compactes      | Envoyer codes de compétences, métriques et preuves utiles ; jamais l’historique brut complet.           |
| JSON strict            | Schémas courts validés par Pydantic ; champs non nécessaires interdits.                                 |
| Budgets                | max_tokens par tâche, délai, coût maximum et nombre de retries configurables.                           |
| Cache                  | Préfixes stables et versionnés ; prompt caching Claude ; cache de contexte DeepSeek lorsque disponible. |
| Batch                  | Comptes rendus périodiques regroupables en traitement asynchrone, selon politique de confidentialité.   |
| Synthèse locale        | Préagréger les 30 dernières preuves en quelques métriques avant envoi.                                  |
| Déduplication          | Hash du prompt métier ; réutilisation d’un résultat identique valide.                                   |

## 8.4 Budgets initiaux recommandés

| **Tâche**                            | **Modèle**                            | **Sortie maximale cible**                                           |
|--------------------------------------|---------------------------------------|---------------------------------------------------------------------|
| Proposition de sujet pour une classe | DeepSeek V4 Flash, raisonnement court | 500–800 tokens JSON pour toute la classe.                           |
| Adaptation par élève                 | DeepSeek V4 Flash                     | 150–300 tokens JSON ; regroupement de plusieurs élèves si possible. |
| Arbitrage d’une réponse multiligne   | DeepSeek V4 Flash thinking            | 250–500 tokens JSON.                                                |
| Proposition de niveau                | DeepSeek V4 Flash                     | 120–250 tokens JSON.                                                |
| Compte rendu élève                   | Claude Haiku 4.5                      | 180–350 tokens.                                                     |
| Conseils professeur par classe       | Claude Haiku 4.5                      | 300–600 tokens.                                                     |

## 8.5 Sorties structurées et garde-fous

Chaque prompt possède un prompt_version, un JSON Schema et un jeu de tests.

Les sorties sont validées ; un seul retry correctif est autorisé en cas de JSON invalide.

Une réponse vide, tronquée ou hors schéma provoque un fallback déterministe ou une exception professeur.

Le texte OCR de l’élève est traité comme une donnée non fiable : il ne peut pas modifier les instructions système ni déclencher un outil.

Toute décision DeepSeek référence \`evidence_ids\`, \`confidence\` et \`reason_code\`.

Les coûts et tokens réels sont enregistrés pour chaque appel.

## 8.6 Pseudonymisation

Le nom et les coordonnées de l’élève restent exclusivement dans PostgreSQL sur le NAS. Les API reçoivent un identifiant technique tel que \`E-7F3A\`, accompagné uniquement des données nécessaires. La table de correspondance n’est jamais envoyée.

# 9. Interface et écrans

## 9.1 Principes UI

React + TypeScript + Vite, avec une bibliothèque cohérente de composants telle que Mantine.

Navigation latérale courte : Dashboard, Sujets, Corrections, Élèves, Compétences, Paramètres.

Aucune logique de notation dans le navigateur ; l’API reste l’autorité.

États de chargement, erreurs actionnables et reprise de tâche explicites.

Interface utilisable sur écran d’ordinateur ; tablette en consultation, sans priorité smartphone en V1.

## 9.2 Dashboard

| **Bloc**          | **Contenu**                                                             |
|-------------------|-------------------------------------------------------------------------|
| Action principale | Créer un sujet en peu de clics.                                         |
| Corrections       | Lots en cours, progression, erreurs et nombre de validations restantes. |
| Classes           | Progression moyenne, compétences fragiles, révisions dues.              |
| Élèves            | Alertes de décrochage, progression récente, niveau indicatif.           |
| Système           | NAS, worker, espace disque, imprimante, API et dernière sauvegarde.     |
| Coûts             | Mathpix, DeepSeek et Anthropic : jour, mois, budget restant.            |

## 9.3 Écran Sujets

Assistant de création en quatre étapes avec sauvegarde automatique du brouillon.

Recherche d’exercices par classe, compétence, difficulté et type de réponse.

Bouton “Proposition automatique” et explication des choix.

Aperçu PDF : élève médian, niveau le plus faible, niveau le plus élevé.

Contrôle de la longueur, du nombre de pages, des rappels et de l’équilibre des compétences.

Estimation du volume Mathpix et des appels LLM avant validation.

Optimiser le recto verso : Si une page \> un recto pleine page. Si 2 pages \> recto verso pleine page.

Mise en page basique : en tête : nom + prénom élève, classe, date, zone de correction/commentaire LLM, Note, Qr code,  
toujours deux colonnes pour les exercices. Réglages pour taille texte.

## 9.4 Écran Élève

| **Section** | **Affichage**                                                           |
|-------------|-------------------------------------------------------------------------|
| Synthèse    | Niveau 1–10 réservé au professeur, tendance, dernières activités.       |
| Compétences | Grille par classe : maîtrise, confiance, dernière preuve, révision due. |
| Historique  | Contrôles, entraînements, notes, difficulté et corrections manuelles.   |
| Oubli       | Calendrier des compétences dues et raisons.                             |
| Rapports    | Comptes rendus Claude, modifiables avant export.                        |
| Réglages    | Niveau verrouillé, adaptations autorisées, rappels et accommodations.   |

Création de classes, années scolaires, élèves (avec copier coller en batch pour nom et prénom des élèves)

classe mock avec 5 élèves imaginaires pour tests, désactiver le mock dans settings.

## 9.5 Écran Correction

Vue lot avec progression par phase et ETA indicative.

Filtres : technique, OCR, rature, double coche, barème, confiance faible.

Validation rapide au clavier et navigation automatique au cas suivant.

Comparaison image originale / réponse attendue / rendu Mathpix.

Annulation possible jusqu’à finalisation ; après finalisation, correction tracée comme révision.

Génération et aperçu des overlays avant impression.

## 9.6 Paramètres

| **Onglet**  | **Contenu**                                                                   |
|-------------|-------------------------------------------------------------------------------|
| API         | Clés Mathpix/DeepSeek/Anthropic, modèle, budgets, tests de connexion, quotas. |
| Imprimantes | IPP/CUPS, bac, recto-verso, format, couleur de correction, page test.         |
| Calibrages  | Profils imprimante/scanner, offsets, échelles, rotation, qualité et date.     |
| Documents   | Couleur dropout, tailles QR, marges, gabarits, polices et overlays.           |
| Pédagogie   | Seuils d’oubli, niveau, difficulté, contrôle/entraînement, rappels.           |
| Sécurité    | Utilisateurs, rôles, sessions, sauvegardes, rétention, journaux.              |
| Système     | Version, migrations, santé des conteneurs, stockage et export diagnostic.     |

# 10. Modèle de données détaillé

## 10.1 Principes

PostgreSQL est la source de vérité ; aucun worker ne modifie directement des fichiers sans transaction métier associée.

Les fichiers lourds restent sur un volume NAS ; la base conserve chemin, hash, taille, MIME et statut.

Les entités imprimées et finalisées sont versionnées et non écrasées.

Les événements pédagogiques et décisions de correction sont append-only ; les agrégats peuvent être recalculés.

Les clés primaires sont des UUID ; les dates sont stockées en UTC avec affichage Europe/Paris.

## 10.2 Identité et organisation

| **Table**         | **Champs principaux**                                                 | **Relations / règles**                            |
|-------------------|-----------------------------------------------------------------------|---------------------------------------------------|
| users             | id, email, password_hash/oidc_id, display_name, active, last_login_at | Rôle via user_roles ; jamais transmis aux LLM.    |
| roles             | id, code, permissions_json                                            | admin, teacher, viewer.                           |
| user_roles        | user_id, role_id                                                      | Clé unique composée.                              |
| school_years      | id, label, starts_at, ends_at, active                                 | Un seul exercice annuel actif par défaut.         |
| classes           | id, school_year_id, name, grade_level, teacher_id, archived_at        | Contient les élèves et évaluations.               |
| students          | id, external_ref, first_name, last_name, llm_pseudonym, active        | Identité locale ; pseudonyme unique renouvelable. |
| class_memberships | class_id, student_id, starts_at, ends_at                              | Historise les changements de classe.              |

## 10.3 Référentiel pédagogique

| **Table**                | **Champs principaux**                                                                      | **Relations / règles**                          |
|--------------------------|--------------------------------------------------------------------------------------------|-------------------------------------------------|
| competency_frameworks    | id, grade_level, name, version, status, source                                             | Une version publiée est immuable.               |
| competencies             | id, framework_id, code, label, description, parent_id, order_index                         | Arbre ou grille ; code stable dans une version. |
| competency_prerequisites | competency_id, prerequisite_id, weight                                                     | Graphe sans cycle validé à l’import.            |
| lesson_snippets          | id, competency_id, level_min/max, title, content_latex, example_latex, version             | Contenu validé ; sélectionnable par LLM.        |
| exercise_catalog         | id, provider, provider_ref, title, grade_level, difficulty, response_type, automation_tier | Référence MathALÉA et capacité technique.       |
| exercise_competencies    | exercise_id, competency_id, weight, evidence_strength                                      | Somme des poids contrôlée.                      |
| exercise_configs         | id, exercise_id, schema_json, response_schema_json, active                                 | Paramètres autorisés et comparateur.            |

## 10.4 Évaluations, copies et documents

| **Table**           | **Champs principaux**                                                                             | **Relations / règles**                           |
|---------------------|---------------------------------------------------------------------------------------------------|--------------------------------------------------|
| assessments         | id, class_id, type, title, status, scheduled_at, duration_min, blueprint_json                     | type = control ou training.                      |
| assessment_students | assessment_id, student_id, personalization_mode, target_level                                     | Une ligne par copie attendue.                    |
| copies              | id, assessment_id, student_id, seed, status, total_pages, generated_at                            | Copie individuelle et immuable après impression. |
| copy_items          | id, copy_id, catalog_id, sequence, difficulty, statement, correction, expected_json, grading_json | Instantané de l’exercice généré.                 |
| document_pages      | id, copy_id, page_no, side, template_version, qr_payload_hash, hmac_version                       | Une page physique = un UUID.                     |
| page_markers        | page_id, role, payload_hash, x_pt, y_pt, size_pt                                                  | MAIN, TL, BL, BR.                                |
| response_zones      | id, page_id, item_id, type, x_pt, y_pt, w_pt, h_pt, padding_pt, color_profile_id                  | Coordonnées canoniques du crop.                  |
| file_objects        | id, owner_type/id, storage_path, sha256, mime, size, created_at                                   | Métadonnées de tous les PDF/JSON/images.         |

## 10.5 Scans, OCR et correction

| **Table**         | **Champs principaux**                                                                     | **Relations / règles**                 |
|-------------------|-------------------------------------------------------------------------------------------|----------------------------------------|
| scan_batches      | id, assessment_id, source_file_id, status, page_count, uploaded_by                        | Relançable et idempotent.              |
| scanned_pages     | id, batch_id, source_index, page_id, original_file_id, work_file_id, status               | page_id nul tant que non identifié.    |
| scan_quality      | scanned_page_id, dpi, blur, exposure, marker_count, reprojection_error, warnings_json     | Décide auto/review/block.              |
| zone_images       | id, scanned_page_id, response_zone_id, original_file_id, filtered_file_id, empty_score    | Original et dérivé séparés.            |
| ocr_attempts      | id, zone_image_id, provider, request_id, variant, raw_json, mmd, latex, confidence, cost  | Plusieurs tentatives possibles.        |
| student_responses | id, copy_item_id, zone_id, normalized_json, selected_choices, final_text                  | Valeur courante issue d’une décision.  |
| grading_decisions | id, response_id, source, score, max_score, confidence, reason_code, evidence_json, status | source deterministic/deepseek/teacher. |
| manual_reviews    | id, decision_id, category, priority, assigned_to, resolution, resolved_at                 | File d’exception professeur.           |
| annotations       | id, copy_id, page_id, zone_id, type, content, color, geometry_json                        | Source de l’overlay PDF.               |

## 10.6 Progression et mémorisation

| **Table**                | **Champs principaux**                                                                              | **Relations / règles**                   |
|--------------------------|----------------------------------------------------------------------------------------------------|------------------------------------------|
| competency_evidence      | id, student_id, competency_id, item_id, mode, score_ratio, difficulty, weight, observed_at         | Preuve atomique finalisée.               |
| student_competency_state | student_id, competency_id, mastery, confidence, stability, memory_difficulty, last_seen_at, due_at | État recalculable.                       |
| competency_state_history | id, student_id, competency_id, before_json, after_json, evidence_id, created_at                    | Append-only.                             |
| student_levels           | id, student_id, level, proposed_level, source, confidence, locked, valid_from                      | Niveau 1–10 historisé.                   |
| review_schedule          | id, student_id, competency_id, due_at, priority, reason, status                                    | Alimente la création automatique.        |
| student_reports          | id, student_id, period, prompt_version, content, status, approved_by                               | Brouillon Claude puis validation/export. |

## 10.7 Paramètres, coûts et audit

| **Table**            | **Champs principaux**                                                                                            | **Relations / règles**                               |
|----------------------|------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| provider_configs     | id, provider, model, encrypted_secret_ref, limits_json, active                                                   | Secrets hors logs et réponses API.                   |
| api_usage_events     | id, provider, model, operation, input_tokens, output_tokens, cache_tokens, units, estimated_cost, correlation_id | Mathpix utilise units/requests ; LLM utilise tokens. |
| prompt_versions      | id, provider, operation, version, system_template, schema_json, active                                           | Toute sortie liée à sa version.                      |
| printers             | id, name, uri, protocol, capabilities_json, active                                                               | Imprimante réseau accessible au NAS.                 |
| calibration_profiles | id, printer_id, scanner_name, paper, side, offset_x/y, scale_x/y, rotation, matrix_json                          | Version et date de validation.                       |
| color_profiles       | id, name, target_lab, tolerance, correction_color, sample_file_id                                                | Dropout par couple imprimante/scanner.               |
| jobs                 | id, type, status, payload_ref, progress, attempts, locked_at, error_code                                         | File de travail persistante.                         |
| audit_logs           | id, actor_id, action, entity_type/id, before_json, after_json, created_at                                        | Non modifiable par l’interface.                      |
| system_settings      | key, value_json, version, updated_by                                                                             | Paramètres non secrets versionnés.                   |

# 11. Architecture NAS, sécurité, fichiers et sauvegardes

## 11.1 Services Docker Compose

| **Service**   | **Technologie** | **Responsabilité**                                                |
|---------------|-----------------|-------------------------------------------------------------------|
| reverse-proxy | Synology        | HTTPS, routage, en-têtes de sécurité.                             |
| web           | React/Vite      | Interface statique.                                               |
| api           | FastAPI         | Authentification, métier, validation et orchestration.            |
| db            | PostgreSQL      | Source de vérité transactionnelle.                                |
| queue         | Redis           | File rapide et verrous temporaires ; aucune donnée métier unique. |
| worker-cv     | Python/OpenCV   | PDF raster, QR, homographie, couleurs, crops et QCM.              |
| worker-doc    | Python + LaTeX  | PDF sujets, corrections, overlays et fusion.                      |
| mathalea      | Node.js         | Génération MathALÉA versionnée.                                   |
| scheduler     | Python          | Courbe d’oubli, rapports périodiques, sauvegardes et maintenance. |

## 11.2 Dimensionnement DS224+

Extension mémoire à 6 Go fortement recommandée.

Un seul traitement PDF lourd simultané ; concurrence Mathpix limitée et configurable.

Traitement page par page et zone par zone pour éviter les pics mémoire.

Modèles de correction et OCR lourds externalisés ; aucun LLM local sur le NAS.

Volumes Docker et base sur stockage Btrfs ; surveillance de l’espace et rotation des dérivés temporaires.

## 11.3 Arborescence de fichiers

| **Chemin logique**                    | **Contenu**                                                   |
|---------------------------------------|---------------------------------------------------------------|
| /data/assessments/{id}/generated      | Sujets, manifestes et corrections sources.                    |
| /data/assessments/{id}/scans/original | Scans couleur immuables.                                      |
| /data/assessments/{id}/scans/derived  | Pages recalées, crops et filtres reproductibles.              |
| /data/assessments/{id}/overlays       | Overlays et archives fusionnées.                              |
| /data/catalog                         | Rappels de leçon, assets, modèles LaTeX et versions MathALÉA. |
| /data/backups                         | Exports chiffrés et journaux de sauvegarde.                   |

## 11.4 Sécurité et confidentialité

HTTPS obligatoire, même sur le réseau local ; sessions sécurisées et expiration configurable.

Comptes nominatifs, rôles minimaux et journalisation des consultations sensibles.

Clés API chiffrées au repos et jamais renvoyées intégralement à l’interface.

Aucun nom d’élève dans les prompts, tags Mathpix, noms de fichiers externes ou logs techniques.

Protection contre l’injection de prompt : réponses OCR encadrées comme données, aucun tool calling pour la correction.

Export et suppression d’un élève possibles sans casser les agrégats anonymisés obligatoires.

Politique de rétention configurable pour scans, crops, rapports et logs.

## 11.5 Impression

L’impression directe depuis le NAS utilise CUPS/IPP vers une imprimante réseau compatible. Si l’imprimante n’est pas joignable depuis le NAS, la V1 doit permettre de télécharger le PDF sans perte de calibration, mais l’impression automatique ne peut pas être garantie.

Profil distinct par imprimante, bac, recto/verso, type de papier et scanner.

Assistant de calibration : imprimer une page test, la scanner, calculer la matrice, valider l’erreur.

Réglage imposé “taille réelle 100 %”, sans ajustement automatique.

Aperçu de l’overlay sur le scan avant envoi à l’imprimante.

Journal de chaque job : fichier, imprimante, profil, utilisateur, heure et résultat.

## 11.6 Sauvegardes et restauration

| **Élément**          | **Politique minimale**                                             |
|----------------------|--------------------------------------------------------------------|
| PostgreSQL           | Dump quotidien + journalisation ; rétention 30 jours.              |
| Documents finaux     | Snapshot Btrfs quotidien + réplication externe recommandée.        |
| Scans originaux      | Conservés selon politique pédagogique et RGPD configurable.        |
| Crops temporaires    | Purge après finalisation et délai de recours configurable.         |
| Secrets              | Sauvegarde chiffrée séparée avec procédure de restauration testée. |
| Test de restauration | Exercice trimestriel documenté sur une copie isolée.               |

# 12. Exigences non fonctionnelles, tests et recette

## 12.1 Performance cible

| **Scénario**              | **Cible initiale**                                                                              |
|---------------------------|-------------------------------------------------------------------------------------------------|
| Ouverture du dashboard    | \< 2 s sur réseau local hors premier chargement.                                                |
| Génération de 30 copies   | \< 5 min pour un modèle standard, hors incident LaTeX.                                          |
| Prétraitement de 60 pages | \< 10 min sur DS224+, selon résolution.                                                         |
| Correction OCR            | Progressive ; premier résultat visible rapidement, lot standard \< 30 min sous réserve des API. |
| Validation manuelle       | Une décision courante en moins de 5 s avec raccourcis.                                          |
| Overlay                   | \< 2 min pour 60 pages après finalisation.                                                      |

## 12.2 Fiabilité et critères d’acceptation

| **Critère**                    | **Seuil de recette**                                                          |
|--------------------------------|-------------------------------------------------------------------------------|
| Association de page incorrecte | 0 tolérance : une page douteuse doit être bloquée.                            |
| Lecture QR sur scans conformes | ≥ 99,5 % sur le corpus de recette.                                            |
| Erreur de recalage             | ≤ 1,5 mm médiane ; aucune annotation hors zone réservée.                      |
| QCM non ambigus                | ≥ 99,5 % de précision ; couverture séparée de la précision.                   |
| Décisions automatiques         | ≥ 99,5 % de précision sur corpus validé ; couverture mesurée mais non forcée. |
| Données perdues après retry    | 0 ; jobs idempotents et reprenables.                                          |
| Traçabilité                    | 100 % des notes et changements de niveau reliés à une origine.                |
| Secrets dans logs              | 0 occurrence dans les tests automatisés.                                      |

## 12.3 Corpus de test

Copies propres, inclinées, retournées, recadrées et légèrement floues.

Stylo noir, bleu, crayon, écriture faible et surcharge.

QCM coché, barré, double coché, entouré et laissé vide.

Fractions, puissances, racines, équations, intervalles, unités et texte français.

Ratures avec réponse remplacée, réponse hors case et débordement sur la ligne suivante.

Pages manquantes, dupliquées, mélangées et provenant d’une autre évaluation.

Imprimantes et scanners réellement utilisés, avec plusieurs profils de calibration.

Sorties LLM valides, invalides, vides, tronquées et contradictoires.

## 12.4 Tests automatisés

| **Niveau**     | **Contenu**                                                                         |
|----------------|-------------------------------------------------------------------------------------|
| Unitaires      | QR/HMAC, coordonnées, comparateurs, score QCM, oubli, budgets et schémas JSON.      |
| Golden files   | PDF et manifestes de référence ; diff visuelle tolérancée.                          |
| Intégration    | MathALÉA → PDF → scan simulé → CV → OCR mock → correction → overlay.                |
| Contract tests | Mathpix, DeepSeek et Anthropic avec réponses enregistrées et sandbox si disponible. |
| End-to-end     | Lot réel de 30 copies, imprimé, rempli, scanné, corrigé et réimprimé.               |
| Récupération   | Coupure réseau/API/NAS à chaque phase ; reprise sans doublon.                       |

## 12.5 Observabilité

État de santé de chaque service et version déployée.

Temps passé par phase, taux d’échec, taux de validation manuelle et confiance OCR.

Coûts Mathpix et tokens DeepSeek/Anthropic par classe, évaluation et mois.

Alertes : disque, base, sauvegarde, quota, imprimante, file bloquée ou API indisponible.

Export diagnostic anonymisé ne contenant ni copie ni secret.

# 13. Découpage de réalisation et décisions à confirmer

## 13.1 Phases

| **Phase**          | **Livrables**                                                                      | **Critère de sortie**                               |
|--------------------|------------------------------------------------------------------------------------|-----------------------------------------------------|
| 0 — POC papier     | Gabarit, 4 QR, filtre couleur, 3 types de réponse, 10 copies.                      | Recadrage et overlay validés sur imprimante réelle. |
| 1 — MVP correction | Classes, sujets MathALÉA, 30 copies, scans, Mathpix, QCM, revue et PDF correction. | Lot complet sans intervention technique.            |
| 2 — Suivi          | Compétences, niveaux, historique, courbe d’oubli et dashboard élève.               | Agrégats recalculables et preuves traçables.        |
| 3 — IA adaptative  | DeepSeek pour sélection/arbitrage ; Claude Haiku pour rapports ; budgets.          | Évaluations de qualité et coût validées.            |
| 4 — Durcissement   | Impression directe, calibrages multiples, sauvegardes, sécurité et monitoring.     | Recette de 30 copies et restauration réussies.      |

## 13.2 Décisions à confirmer avant développement

Grille officielle de compétences et règles d’agrégation par niveau de classe.

Modèles d’imprimante et de scanner, mode recto/verso et procédure physique de réinsertion.

Nombre moyen de pages et de zones par copie ; volume mensuel attendu.

Échelle de notation et règles de points partiels pour les développements.

Politique RGPD : durée de conservation, droits d’accès, information des familles et contrat API.

Liste MathALÉA initiale et qualification de chaque exercice pour l’OCR automatique.

Seuils de confiance acceptables après constitution d’un corpus réel.

Impression directe CUPS/IPP ou téléchargement PDF dans la première version.

## 13.3 Définition de “terminé” pour la V1

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>V1 acceptée</strong></p>
<p>Un professeur crée une feuille pour une classe, imprime 30 copies éventuellement personnalisées, importe le scan, ne traite que les exceptions, finalise les résultats, consulte les progrès et imprime les corrections rouges par-dessus les feuilles. Une interruption réseau ou API ne perd aucune donnée et peut être reprise.</p></td>
</tr>
</tbody>
</table>

# Références techniques vérifiées

**\[S1\]** MathALÉA — générateur JavaScript avec sorties HTML, LaTeX et PDF ; projet AGPL. https://github.com/mathalea/mathalea

**\[S2\]** Mathpix — endpoint image \`/v3/text\`, formats, confiance et reconnaissance manuscrite STEM. https://docs.mathpix.com/reference/post-v3-text

**\[S3\]** Mathpix — confidentialité et option \`metadata.improve_mathpix=false\`. https://docs.mathpix.com/concepts/privacy

**\[S4\]** Mathpix — limites, quotas et suivi d’usage. https://docs.mathpix.com/reference/limits-and-quotas

**\[S5\]** Mathpix — recommandations de résolution et compression. https://docs.mathpix.com/concepts/tips

**\[S6\]** DeepSeek — modèles V4 et dépréciation annoncée des anciens alias. https://api-docs.deepseek.com/quick_start/pricing

**\[S7\]** DeepSeek — sortie JSON structurée. https://api-docs.deepseek.com/guides/json_mode/

**\[S8\]** Claude — modèles actuels et identifiant Claude Haiku 4.5. https://platform.claude.com/docs/en/about-claude/models/overview

**\[S9\]** Claude — prompt caching. https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

**\[S10\]** Claude — comptage des tokens avant envoi. https://docs.anthropic.com/en/docs/build-with-claude/token-counting

**\[S11\]** Synology DS224+ — Celeron J4125 et mémoire extensible à 6 Go. https://global.download.synology.com/download/Document/Hardware/DataSheet/DiskStation/24-year/DS224%2B/enu/DS224%2B_Data_Sheet_enu.pdf

# Annexe — Principales règles métier

| **ID** | **Règle**                                                                              |
|--------|----------------------------------------------------------------------------------------|
| RM-001 | Une page doit être identifiée par QR signé avant correction.                           |
| RM-002 | Le scan original couleur n’est jamais modifié.                                         |
| RM-003 | Le filtre de couleur ne s’applique qu’aux dérivés OCR.                                 |
| RM-004 | Une décision automatique doit dépasser le seuil de précision validé sur corpus.        |
| RM-005 | Une faible confiance crée une revue ; elle ne choisit pas une réponse.                 |
| RM-006 | Une note finalisée est modifiée par événement correctif, jamais par écrasement.        |
| RM-007 | Le niveau 1–10 est privé professeur et historisé.                                      |
| RM-008 | Une compétence ne progresse qu’avec une preuve finalisée.                              |
| RM-009 | La courbe d’oubli est calculée sans LLM.                                               |
| RM-010 | Les LLM ne reçoivent jamais le nom de l’élève.                                         |
| RM-011 | Les prompts et modèles sont versionnés et configurables.                               |
| RM-012 | La correction imprimée utilise un overlay transparent calibré.                         |
| RM-013 | Un contrôle personnalisé doit signaler sa difficulté et préserver un blueprint commun. |
| RM-014 | Toute copie générée conserve son instantané MathALÉA complet.                          |
| RM-015 | Les budgets API peuvent suspendre les appels sans bloquer l’accès aux données.         |

# Infos complémentaires

Ne pas pénaliser les élèves absents, même en mode contrôle / entrainement

à chaque vue lot, mettre une barre de progression segmentée avec avancement par palier : vert ok et palier en orange si blocage et le professeur doit intervenir.

Ajouter le nom de l'élève en Overlay de correction afin que l'élève puisse être rassuré que la correction en overlay est bien pour lui. Si en cas d'erreur, facile à identifier.


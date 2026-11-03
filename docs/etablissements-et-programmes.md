# Établissements et programmes

## Périmètre livré

La structure dissocie le pays d’implantation de l’établissement et les programmes
qu’il propose. Une classe appartient à une année scolaire, un programme et un
cycle (6e à Terminale), et à un établissement si son professeur est accompagné.
Au lycée ivoirien, elle appartient aussi à une série. Le niveau pédagogique 1–10
est propre à chaque matière.

Les référentiels sont identifiés par programme, cycle, matière et série ou
enseignement optionnel. Les nouvelles combinaisons sont initialisées en brouillon,
sans inventer de compétences ni d’exercices. Les référentiels français existants
restent utilisables. L’ajout d’une matière prépare ses référentiels vides.

Une compétence appartient à un seul référentiel. `section=common` signifie
« Compétences communes », `section=complement` signifie « Compléments ».
`source_competency_id` et `source_exercise_id` permettent de conserver la provenance
lors de futures duplications : les libellés ne servent jamais à identifier un
contenu partagé. Les contenus eux-mêmes seront préparés ultérieurement, notamment
via Astra. L’export PDF « Liste à imprimer » sépare les deux parties sans afficher
les identifiants techniques de la base.

## Professeurs et droits

Un professeur est soit accompagné, soit autonome ; ce mode ne dépend pas du pays.

- **Accompagné** : supervisé depuis l’onglet Établissements, qui ne montre que
  ces professeurs et leurs établissements. Sa fiche fonctionne sans compte de
  connexion ; s’il en ouvre un, il peut aussi créer ses propres sujets. Une
  affectation relie classe, matière et professeur ; une seule est admise par
  classe et matière.
- **Autonome** : premier compte de l’installation, ou invité depuis
  Paramètres > Utilisateurs, il n’a ni établissement
  ni affectation et n’apparaît pas dans Établissements. Il choisit son programme
  et sa matière dans Paramètres > Mon compte. Ses cycles se déduisent des classes
  qu’il crée. Une classe qui a déjà des sujets garde son programme.

L’administration ou le correcteur génère un lien personnel d’inscription pour un
professeur accompagné, l’administration pour un autonome ; il s’envoie
manuellement. Le jeton est stocké sous forme d’empreinte, expire après
14 jours et n’est utilisable qu’une fois. Un nouveau lien invalide le précédent.
Le professeur choisit son adresse et son mot de passe et retrouve sa fiche,
ses élèves et leurs résultats. Il ne dispose d’aucune liste de collègues.

Le correcteur gère les établissements, leurs suspensions, les professeurs,
les classes, les sujets à partir de la banque, les scans et les impressions.
Les modifications des exercices, les paramètres système, les coûts et la gestion
des comptes correcteurs restent réservés à l’administration.

Les contrôles serveur appliquent le périmètre des classes et matières du
professeur, y compris aux requêtes de détail, fichiers, résultats et jobs.
Une suspension bloque la production et l’impression ; les résultats restent
conservés. L’archivage d’une année conserve ses données. Reprendre une liste dans
une autre année crée de nouvelles fiches élèves, sans reporter leurs niveaux.

## Exploitation et chemin papier

Établissements centralise le suivi : préparation, génération, impression,
livraison, récupération, scans, correction, overlays et restitution. Filtres par
établissement, année et étape ; notes et date prévue par sujet. La création d’un
sujet utilise l’assistant existant, directement depuis cette page ou une classe.
La facturation et les abonnements sont hors périmètre.

« À récupérer » compte les lots livrés encore présents dans les établissements
(un lot = un sujet pour une classe). Le délai court depuis la confirmation
« Sujets livrés ». Une alerte apparaît au-delà de sept jours ; les lots concernés
sont signalés individuellement et les plus anciens apparaissent en premier dans
ce filtre. « Copies récupérées » ou l'arrivée des scans retire le lot du compteur
et de l'alerte. Les compteurs suivent les filtres établissement et année scolaire.
Les cases de suivi conservent leur état sélectionné jusqu'au choix d'une autre
étape ou de « Tous les lots ».

Les imprimantes des connecteurs administrateur/correcteur constituent le centre
partagé. Les imprimantes personnelles d’un professeur restent privées. Aucun
lancement d’impression automatique : l’opérateur choisit l’imprimante et lance
le travail. Les réglages de prélèvement, sortie et ADF restent propres à chaque
imprimante et peuvent être étendus à plusieurs centres ultérieurement.

Pour un professeur accompagné, chaque impression de sujets inclut un intercalaire
A4, avec les informations dans la moitié supérieure et un trait de pliure.
Il est logiquement en tête de lot ; la compensation du chemin papier est ensuite
appliquée. En recto verso automatique, son verso est blanc et l’inversion de pile
inverse les feuilles, pas leurs faces. En deux passes, tous les versos blancs
nécessaires occupent leur place, y compris celui de l’intercalaire.
L’intercalaire est conservé pour le retour ; aucun intercalaire n’est ajouté aux
overlays. Les scans et overlays conservent l’ordre du scanner, indépendamment de
l’ordre des élèves ou des fichiers déposés dans le bac à sable.

Une copie non scannée est absente lors de la finalisation. Une copie entièrement
vide n’est reconnue absente que si toutes ses réponses ont été lues avec une
confiance suffisante. Une lecture incomplète/incertaine reste à vérifier.
Une copie vide scannée garde ses emplacements dans l’overlay, sans note ni pénalité.

## API et futur MCP

Le serveur MCP n’est pas créé. Il pourra appeler les mêmes actions HTTP que
l’interface avec les mêmes droits :

- `POST /api/assessments` : contexte classe, matière et groupe éventuel.
- `POST /api/assessments/{id}/manual-plan` ou `/auto-plan` : plan validé dans le référentiel du sujet.
- `POST /api/assessments/{id}/generate` : mise en file, identifiant du job.
- `POST /api/printers/print` : lancement explicite avec imprimante et passe.
- `GET /api/operations/dashboard` : état opérationnel.
- `PATCH /api/operations/assessments/{id}` : notes et suivi.

La génération et les opérations sont tracées avec leur auteur. Les jobs et les
états existants restent la source du suivi ; un futur client doit consulter l’état
avant de renouveler une demande d’impression après une coupure réseau.

## Sources pédagogiques et limites

- [DPFC — programmes et guides du secondaire](https://dpfc-ci.net/documents/programmes-educatifs-et-guides-dexecution/programmes-educatifs-et-guides-dexecution-du-secondaire/) : séries générales ivoiriennes et programmes.
- [DPFC — progressions de mathématiques 2022–2023](https://dpfc-ci.net/wp-content/uploads/dpfc_fichiers/2022-2023/progressions/MATHS_Progressions%202022-2023.pdf).
- [Annuaire ministériel technique 2020–2021](https://dpsimetfpa.com/docs/annuaire/annuaire_2021.pdf) : liste technique initiale, datée, à compléter selon les établissements réellement servis.
- [Bulletin officiel français du 12 janvier 2023](https://www.education.gouv.fr/bo/23/Hebdo2/MENE2300946N.htm) : enseignements de mathématiques au lycée général.

Les nouveaux référentiels doivent être remplis avant de produire des exercices
pour ces enseignements. Les groupes d’enseignement servent notamment aux options
françaises de lycée. Aucun contenu scolaire fictif ni établissement de démonstration
n’est ajouté par l’initialisation.

La validation automatique utilise SQLite temporaire, les tests HTTP et PDF,
et la compilation TypeScript/Vite. La validation visuelle utilise une base isolée.
La validation du chemin mécanique final exige un essai sur les imprimantes et
le scanner réels avec leurs réglages ; elle n’est pas remplacée par les tests PDF.

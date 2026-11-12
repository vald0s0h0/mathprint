# Déployer Kiro & Nina sur un NAS Synology (Container Manager)

Guide pas-à-pas, **priorité à l'interface graphique** (File Station +
Container Manager) : aucune connexion SSH n'est nécessaire, sauf une étape
optionnelle signalée comme telle.

**Prérequis** : DSM 7.2 ou supérieur, avec le paquet **Container Manager**
installé (Panneau de configuration → Centre de paquets → rechercher
« Container Manager » → Installer). Sur DSM plus ancien, le paquet s'appelle
« Docker » et n'a pas l'écran « Projet » décrit ici — il faut alors mettre à
jour DSM au préalable.

---

> **Édition** : le NAS sert `app.kironina.com`, pour les **profs autonomes
> seulement** (`MATHPRINT_EDITION=autonome`, valeur par défaut du
> `docker-compose.yml`). Les établissements accompagnés et l'Atelier tournent sur
> le centre local (Mac, édition `centre`). Si votre `compose.yaml` du NAS a été
> créé avant cette option, ajoutez `MATHPRINT_EDITION: autonome` à
> l'environnement du service `api`.

## 1. Créer le dossier du projet (File Station)

1. Ouvrir **File Station**.
2. Dans le volume partagé de votre choix (ex. `docker`), créer un dossier
   `mathprint` (clic droit → Créer → Dossier). Chemin obtenu, par exemple :
   `/docker/mathprint`.
3. Dans ce dossier, créer trois sous-dossiers vides : `volumes`, puis à
   l'intérieur `volumes/postgres`, `volumes/redis`, `volumes/data`.
   *(Docker les recrée de toute façon tout seul au premier démarrage si vous
   les oubliez — cette étape est juste pour vérifier l'arborescence.)*

## 2. Créer le fichier `.env` (File Station)

`DB_PASSWORD` est **obligatoire et sans valeur automatique** : Postgres en a
besoin dès sa toute première initialisation, l'API doit se connecter avec
exactement le même. Sans lui, `db` peut démarrer avec un mot de passe vide
et `api` boucle en échec de connexion (voir §10 si ça vous arrive). Les
autres clés internes de l'application (JWT, signature des QR) et le compte
administrateur, eux, n'ont **rien à préparer ici** — voir §6.

1. Dans `/docker/mathprint`, clic droit → Créer → Fichier texte vierge,
   nommé exactement **`.env`** (bien penser au point initial ; File Station
   peut demander confirmation pour un nom commençant par un point).
2. Double-clic dessus pour l'ouvrir dans l'éditeur de texte intégré de File
   Station, et coller :

   ```env
   DB_PASSWORD=<mot de passe long et unique>
   MOCK_MODE=false
   MATHPRINT_VERSION=latest
   ```

   - `DB_PASSWORD` : n'importe quelle phrase longue et imprévisible convient
     (20+ caractères, mélange de mots/chiffres) ; elle n'est jamais affichée
     nulle part dans l'application. Si vous avez un gestionnaire de mots de
     passe, générez-la là plutôt qu'à la main.
   - `MOCK_MODE=false` : désactive la classe fictive « 5e Mock » et les
     réponses simulées — à mettre à `true` uniquement pour tester l'appli
     sans données réelles.
   - `MATHPRINT_VERSION=latest` : le NAS suit automatiquement chaque nouvelle
     publication (voir §7). Remplacer par ex. `1.2.0` pour figer une version
     précise et couper la mise à jour automatique — **sans le `v`** : le tag
     git est `v1.2.0`, le tag d'image est `1.2.0`. Un `v` de trop rend le
     manifeste introuvable et bloque toutes les mises à jour (§10).
3. Enregistrer.

## 3. Créer le projet Docker Compose (Container Manager)

1. Ouvrir **Container Manager** → onglet **Projet** → **Créer**.
2. **Nom du projet** : `mathprint`.
3. **Chemin** : sélectionner le dossier créé à l'étape 1
   (`/docker/mathprint`) via le sélecteur de dossier.
4. **Source** : choisir « Créer docker-compose.yml » (éditeur intégré), puis
   coller l'intégralité du contenu du fichier
   [`docker-compose.yml`](docker-compose.yml) de ce dépôt.
   *(Alternative : si vous avez déjà déposé le fichier `docker-compose.yml`
   dans `/docker/mathprint` via File Station, choisir « Utiliser un
   docker-compose.yml existant » — Container Manager le détecte
   automatiquement dans le dossier choisi à l'étape 3, ainsi que le `.env`
   créé à l'étape 2.)*
5. Cliquer **Suivant** : Container Manager liste les ports détectés
   (`7070` pour le service `web`). Vérifier qu'il n'entre pas en conflit avec
   un autre service du NAS (DSM utilise en général 5000/5001) ; sinon
   modifier ici le port hôte.
6. Cliquer **Suivant** puis **Terminer** (ou **Exécuter** selon la version).
   Container Manager télécharge les images depuis `ghcr.io` (elles sont
   publiques, aucune authentification requise) puis démarre les 4 conteneurs
   dans l'ordre (`db` doit être « healthy » avant `api`, `api` avant `web`).

Premier démarrage : compter 1 à 2 minutes.

## 4. Vérifier que tout tourne

Dans Container Manager → **Projet** → `mathprint`, les 4 conteneurs
(`db`, `queue`, `api`, `web`) doivent passer au vert
(« En cours d'exécution »). En cas de souci, clic sur un conteneur →
**Détails** → onglet **Journal** affiche ses logs sans passer par SSH.

Tester ensuite dans un navigateur : `http://<IP-du-NAS>:7070` doit afficher
l'écran de connexion Kiro & Nina.

## 5. Exposer l'application en HTTPS (reverse proxy Synology)

Recommandé plutôt que d'utiliser directement le port 7070 en HTTP.

1. **Panneau de configuration** → **Portail de connexion** (ou « Application
   Portal » selon la version DSM) → onglet **Avancé** → **Reverse Proxy** →
   **Créer**.
2. Description : `Kiro & Nina`.
3. Bloc **Source** : Protocole `HTTPS`, Nom d'hôte : celui que vous utilisez
   pour ce NAS (ex. `mathprint.votre-nom.synology.me` ou un nom local),
   Port `443`.
4. Bloc **Destination** : Protocole `HTTP`, Nom d'hôte `localhost`,
   Port `7070`.
5. Enregistrer. Vérifier qu'un certificat valide est assigné à ce nom d'hôte
   dans **Panneau de configuration** → **Sécurité** → **Certificat**
   (Let's Encrypt via QuickConnect/DDNS, ou certificat importé).

L'application est ensuite accessible en `https://` sans exposer le port 7070
directement.

## 6. Premier lancement : créer votre compte administrateur

Ouvrir `https://<votre-nom-d-hôte>` (ou `http://<IP-du-NAS>:7070`) dans un
navigateur affiche directement un **écran de démarrage** tant qu'aucun
compte n'existe : e-mail, prénom, mot de passe (8 caractères minimum), et
une section « Clés API (facultatif) » pour Mathpix/DeepSeek/Anthropic — à
laisser vide pour rester en mode simulé et les ajouter plus tard dans
Paramètres → API. Valider crée le compte et vous connecte immédiatement ;
cet écran ne réapparaît plus ensuite.

Aucun mot de passe par défaut, rien à taper dans `.env` pour ça : les clés
internes de l'application (JWT, signature des QR) sont elles aussi générées
automatiquement à ce moment-là et stockées sur le volume `/data` (donc
stables d'une mise à jour à l'autre).

Si vous devez changer votre mot de passe plus tard (l'interface ne propose
pas encore d'écran dédié pour ça — à demander en évolution si besoin), la
commande suivante depuis Container Manager le fait en une étape, sans SSH :

1. Container Manager → conteneur `mathprint-api` (ou `api`) → **Détails** →
   onglet **Terminal** → **Créer** → `sh` (ou `bash`).
2. Coller (adapter l'e-mail et le nouveau mot de passe) :
   ```bash
   python3 -c "
   from app.db import SessionLocal
   from app.models import User
   from app.services.security import hash_password
   db = SessionLocal()
   u = db.query(User).filter_by(email='VOTRE-EMAIL').first()
   u.password_hash = hash_password('VOTRE-NOUVEAU-MOT-DE-PASSE')
   db.commit()
   print('mot de passe mis à jour pour', u.email)
   "
   ```
3. Se reconnecter sur l'application avec le nouveau mot de passe.

## 6 bis. Manuels Indigo et publication des exercices

Cette section ne concerne que l'instance sur laquelle un professeur **crée**
les exercices depuis l'onglet Exercices. Les autres déploiements reçoivent les
exercices déjà publiés, sans manuel ni onglet.

Deux façons d'équiper l'instance qui fabrique. **La première ne demande aucun
PDF** et suffit dans la quasi-totalité des cas.

### A. Importer le pack de travail (recommandé)

Les manuels sont **sous droits** et trop volumineux pour le dépôt GitHub
(203 Mo pour le manuel élève, limite de 100 Mo par fichier) : ils ne sont
livrés dans **aucune** image. Plutôt que de les copier sur le NAS, on y porte
un **pack de travail** : l'index du manuel (le texte, dont l'OCR est déjà payé)
et toutes les pages du manuel élève rendues en image. C'est tout ce dont la
fabrication a besoin — découpe des exercices, lecture de la couleur des badges,
extraction des figures, recadrage : tout fonctionne à l'identique, sans PDF.

1. Sur la machine qui possède les manuels : onglet **Exercices** →
   **Indexer le manuel** (une seule fois, ~0,65 € d'OCR), puis
   **Exporter le pack** → une archive d'environ **90 Mo** est téléchargée.
2. Sur le NAS : onglet **Exercices** → **Importer le pack**, choisir l'archive.

Si l'envoi par le navigateur échoue (connexion lente, archive trop grosse),
déposez le fichier dans File Station sous
`/docker/mathprint/volumes/data/indigo-pack.zip`, puis cliquez sur la **petite
icône de disque** à droite du bouton *Importer le pack* : l'application ira
chercher l'archive à cet emplacement.

Le pack ne se refait que si le manuel change. L'instance qui l'a reçu ne peut
pas indexer (le bouton reste grisé, c'est normal : le pack porte déjà l'index)
et ne peut pas exporter de pack à son tour.

### B. Déposer les manuels (si vous avez les PDF et voulez pouvoir indexer)

Dans File Station, sous `/docker/mathprint/volumes/data/`, créer un dossier
**`manuals`** et y déposer les deux PDF :

```
/docker/mathprint/volumes/data/manuals/3_indigo.pdf         (manuel élève)
/docker/mathprint/volumes/data/manuals/3_indigo_prof.pdf    (corrigés)
/docker/mathprint/volumes/data/manuals/6_indigo.pdf         (manuel élève 6e)
/docker/mathprint/volumes/data/manuals/6_indigo_prof.pdf    (guide pédagogique 6e)
```

Les **noms de fichiers comptent** : la résolution se fait sur le nom, pas sur
le chemin. Rien à redémarrer, les PDF sont ouverts à la demande. L'onglet
Exercices affiche un bandeau orange tant qu'il ne les trouve pas.

`volumes/data` est le volume monté sur `/data` dans le conteneur : son contenu
survit aux mises à jour, contrairement à l'image. C'est aussi là que vit le
pack importé — il survit donc aux mises à jour, lui aussi.

### Fabriquer, corriger, publier

Une fois l'instance équipée (pack **ou** manuels), le trajet complet se fait
depuis l'onglet Exercices :

1. **Nouvelle extraction** → cocher des compétences → **Lancer**. Aucune plage
   de pages ni numéro d'exercice à saisir : l'index les déduit. Chaque
   compétence affiche ce que l'index a trouvé, et celles qu'il ne couvre pas
   sont refusées par leur nom plutôt que lancées à vide.
2. Les exercices arrivent en **brouillon**. Les relire un par un : l'extrait du
   manuel est affiché à côté de l'énoncé produit. **Modifier** ouvre l'édition
   complète (énoncé, réponses, barème, guide, corrigé, figure) ;
   **Régénérer** rejoue la génération sans jamais dégrader ce qui existe.
3. **Valider** chaque exercice jugé bon. Un brouillon non validé n'est jamais
   publié.
4. **Publier** — voir ci-dessous.

La génération appelle un modèle : renseignez la clé du fournisseur choisi dans
**Paramètres → Fournisseurs**. Sans clé, l'onglet le dit avant de lancer et les
exercices seraient de simples reprises brutes de l'OCR.

### Publier, puis livrer aux autres

Le bouton **Publier** fige les exercices validés **sur le volume** de cette
instance et les sème dans sa banque : ils survivent donc aux mises à jour du
conteneur. Ils ne sont pour autant visibles que sur cette instance.

Pour les livrer à **tous** les déploiements :

- **Depuis le poste de développement** (clone git) : **Publier** écrit aussi
  directement dans `backend/app/data/indigo/` du dépôt. Il suffit de commiter
  et pousser — rien à exporter.
- **Depuis le NAS** (pas de dépôt) :

1. Onglet Exercices → **Exporter pour le dépôt** → une archive ZIP est
   téléchargée.
2. La décompresser dans `backend/app/data/indigo/` du dépôt (elle a exactement
   cette arborescence : `exercises.json`, `crops/`, `figures/`).
3. Commiter et pousser. Le build suivant embarque ces exercices dans l'image,
   et chaque déploiement les sème en banque à son démarrage.

L'export est volontairement manuel : aucun jeton d'écriture GitHub ne vit sur
le NAS, et vous voyez ce qui entre dans le dépôt à partir duquel vos images
sont construites.

> **Quelle publication un serveur affiche-t-il ?** Celle du **dépôt** (livrée
> dans l'image), sauf si « Publier » a été cliqué sur ce serveur DEPUIS sa
> dernière mise à jour : cette publication locale reste affichée jusqu'à la
> mise à jour suivante, où le dépôt reprend la main. Paramètres → Système
> affiche « Exercices publiés : N en banque / N (dépôt ou volume, date) ».

> Publier alors qu'aucun exercice n'est validé sur l'instance est **refusé**
> (message explicite) : cela effacerait le contenu déjà publié, que le
> redémarrage suivant retirerait de la banque.

## 7. Mises à jour automatiques (tâche planifiée DSM)

Côté développement, **chaque `git push` sur `main` publie automatiquement**
de nouvelles images `latest` sur `ghcr.io` (workflow
`.github/workflows/deploy.yml`, après tests). Côté NAS, la mise à jour est
assurée par une **tâche planifiée DSM** — le mécanisme natif Synology, bien
plus fiable que Watchtower avec Container Manager (abandonné : conteneurs
recréés hors projet, arrêts bloqués, mises à jour silencieusement ignorées).

La tâche exécute `scripts/nas-update.sh`, qui fait exactement ce qu'on
ferait à la main : `docker compose pull` puis `docker compose up -d` — seuls
les conteneurs dont l'image a changé sont recréés, `db`/`queue` ne sont
jamais touchés, et le projet reste géré par Container Manager.

### Mise en place (une seule fois, ~2 minutes)

1. Déposer `scripts/nas-update.sh` (depuis ce dépôt) dans
   `/docker/mathprint/` via File Station (à côté du `docker-compose.yml`).
2. **Panneau de configuration** → **Planificateur de tâches** → **Créer** →
   **Tâche planifiée** → **Script défini par l'utilisateur**.
3. Onglet **Général** : nom `Kiro & Nina update`, utilisateur **root**
   (indispensable pour la commande docker).
4. Onglet **Programmer** : Quotidien, puis « Exécuter à la même fréquence
   dans la journée » → toutes les **1 heure** (ou 15 min si vous voulez des
   mises à jour plus rapides).
5. Onglet **Paramètres de tâche** → zone **Script utilisateur** :
   ```sh
   bash /volume1/docker/mathprint/nas-update.sh
   ```
   (adapter `volume1` si votre dossier partagé `docker` est ailleurs).
   Facultatif : cocher « Envoyer les détails d'exécution par e-mail »
   uniquement en cas d'erreur.
6. **OK**, puis clic droit sur la tâche → **Exécuter** pour un premier test.

### Vérifier qu'une mise à jour a bien été appliquée

- Dans Kiro & Nina : **Paramètres → Système** affiche « API build `abc1234` »
  et « Web build `abc1234` » — le sha du commit publié. S'il change après un
  push + une exécution de la tâche, la chaîne fonctionne de bout en bout.
- Sur le NAS : `/docker/mathprint/update.log` (File Station) journalise
  chaque exécution (« déjà à jour » ou liste des conteneurs recréés).

**Laisser le temps à la chaîne** : rien n'est instantané entre le `git push`
et le NAS. Compter la CI (tests + build des images, ~5 à 15 min selon le
cache), puis l'attente de la prochaine exécution de la tâche planifiée
(jusqu'à 1 h avec la fréquence conseillée), puis le `pull` des images sur la
liaison du NAS. Un build qui n'a pas encore changé une demi-heure après un
push est donc normal — ne chercher une panne qu'au-delà, ou lancer la tâche
à la main (clic droit → **Exécuter**) pour ne pas attendre.

⚠️ Comme avant, la tâche met à jour **les images**, pas le
`docker-compose.yml` lui-même. S'il évolue (nouveau service, nouveau port,
nouvelle variable `.env`), recoller le fichier à jour dans Container
Manager → Projet → **Action** → **Modifier**, puis redéployer — les notes de
version le signalent quand c'est nécessaire.

### Figer ou revenir en arrière

Rouvrir `.env` (File Station) et remplacer `MATHPRINT_VERSION=latest` par
un tag précis — `1.2.0` (release, **sans le `v`** du tag git) ou
`sha-abc1234` (n'importe quel commit publié) — puis Container Manager →
Projet `mathprint` → **Action** → **Redéployer**. La tâche planifiée peut
rester active : elle ne trouvera jamais rien de plus récent qu'un tag figé.

> Le tag doit exister pour **les deux** images (`mathprint-api`,
> `mathprint-web`) : le `pull` porte sur tout le
> projet, et un seul manifeste manquant fait échouer la mise à jour. C'est
> garanti pour `X.Y.Z` comme pour `sha-XXXXXXX` (les images inchangées sont
> re-étiquetées par la CI). En cas de doute, la liste des tags publiés est
> dans GitHub → onglet **Packages**.

## 8. Sauvegardes

Deux niveaux complémentaires :

- **Applicatif** : dans Kiro & Nina, Paramètres → Système → **Sauvegarder
  maintenant** (dump de la base dans `/data/backups`, rétention 30 fichiers).
- **NAS** : pour survivre à une panne de disque, sauvegarder le dossier
  `/docker/mathprint/volumes` en entier (contient la base Postgres, Redis, les
  documents générés, les manuels déposés et les exercices publiés depuis cette
  instance) avec **Hyper Backup** (Centre de paquets → installer
  Hyper Backup → nouvelle tâche → sélectionner le dossier partagé
  `docker/mathprint`).

## 9. Impression (limite connue)

L'image `mathprint-api` ne contient pas de client CUPS : la détection des
imprimantes **locales** (`lpstat`) ne fonctionnera donc pas telle quelle dans
ce déploiement conteneurisé. Pour imprimer depuis le NAS, utiliser l'écran
**Paramètres → Imprimantes** de Kiro & Nina pour enregistrer une **imprimante
réseau IPP** (`ipp://<ip-imprimante>/ipp/print`) plutôt que de compter sur une
file CUPS locale.

## 10. Dépannage rapide

| Symptôme | Piste |
|---|---|
| Un conteneur reste rouge / redémarre en boucle | Container Manager → conteneur → Détails → Journal (le traceback exact y est) |
| `/` répond mais `/api/...` renvoie 502 | C'est le nginx du conteneur `web` qui ne joint pas `api` : le conteneur `api` est arrêté/en boucle, pas un problème de version — voir la ligne suivante |
| `api` redémarre en boucle, `db` a l'air « en cours d'exécution » | Cause la plus fréquente : `DB_PASSWORD` absent/vide alors que le volume `volumes/postgres` a déjà été initialisé avec ce mot de passe vide — Postgres ne réapplique son mot de passe qu'à la toute première initialisation d'un volume vide. Corrige `DB_PASSWORD` dans `.env` **et** vide le contenu de `volumes/postgres` (File Station) avant de redémarrer, pour forcer une réinitialisation propre. Sans donnée réelle encore stockée, ce vidage est sans risque. |
| Le projet ne veut plus s'arrêter (bouton bloqué) | Arrêter `api` individuellement plutôt que tout le projet d'un coup. Toujours bloqué après ~1 min : Centre de paquets → Container Manager → **Arrêter** puis **Démarrer** le paquet (reset léger). En dernier recours, redémarrer le NAS — tous les conteneurs s'arrêtent proprement au reboot quel que soit leur état. |
| Page inaccessible sur le port publié | Conflit de port avec un autre service DSM — changer le port hôte dans le projet |
| `docker compose pull` échoue avec 401/403 | Les images `ghcr.io/vald0s0h0/mathprint-*` sont publiques par défaut ; si elles ont été rendues privées entre-temps, ajouter un `docker login ghcr.io` (jeton `read:packages`) — voir README |
| `manifest unknown` / `not found` au `pull` | Le tag demandé n'existe pas : `MATHPRINT_VERSION` avec un `v` (`v2.4.0` au lieu de `2.4.0`), ou une faute de frappe dans le sha. La liste des tags réellement publiés est dans GitHub → **Packages** |
| Les mises à jour ne semblent jamais s'appliquer | ⓪ **D'abord attendre** : entre le push et le NAS il y a la CI puis le prochain passage de la tâche planifiée — une demi-heure sans changement est normale (§7). Ensuite, dans l'ordre : ① Paramètres → Système : noter le build affiché. ② GitHub → onglet Actions : le workflow « Deploy latest images » du dernier push est-il vert ? (sinon rien n'a été publié). ③ File Station → `update.log` : la tâche tourne-t-elle, et dit-elle « déjà à jour » ou liste-t-elle des conteneurs ? `update.log` absent = la tâche ne s'exécute pas, ou le `PROJECT_DIR` du script ne pointe pas sur le bon volume (le journal vit dans ce dossier : chemin faux = zéro trace) — le détail est alors dans Planificateur de tâches → clic droit → **Afficher le résultat**. ④ `.env` : `MATHPRINT_VERSION` est-il bien `latest` (un tag figé bloque tout — et un tag avec un `v` n'existe pas, voir la ligne `manifest unknown`) ? ⑤ Navigateur : forcer le rechargement (Ctrl+Maj+R) — les anciennes versions pouvaient rester en cache, corrigé depuis (nginx no-cache). |
| Après une mise à jour, comportement inattendu | Revenir en arrière en fixant `MATHPRINT_VERSION` sur un tag précédent (`X.Y.Z` — sans le `v` — ou `sha-abc1234`) dans `.env` (§7) |


---

## 11. Lecture OCR locale (worker sur le Mac)

Le NAS (édition `autonome`) **ne fait tourner aucun modèle** et n'a aucune dépendance
d'apprentissage automatique : son image Docker reste légère (1,75 Go de RAM seulement).
Les moteurs OCR (TrOCR, Uni-MuMER, LightOnOCR, Apple Vision) tournent sur le Mac, dans le
service `ocr_worker/`, qui vient chercher les découpes du NAS par HTTP, les lit, puis envoie ses
résultats. La fusion (consensus), Mathpix et la reprise de la correction restent sur le NAS.

### 11.1 Rien ne change tant que vous ne basculez pas

Le NAS reste en `ocr_mode = mathpix` (valeur par défaut) : la lecture est exactement celle d'avant.
Le mode se règle dans **Paramètres admin → Pédagogie → Lecture OCR multicouche** :

| Mode | Effet |
|---|---|
| `mathpix` | L'existant. Aucune découpe n'est inscrite pour le worker. |
| `shadow` | Mathpix décide encore. Les découpes sont aussi inscrites, les décisions locales enregistrées et comparées à Mathpix et au professeur. Le lot n'attend pas le Mac. |
| `cascade` | La fusion locale décide, Mathpix en dernier recours, OCRiser pour le reste. Le lot attend le Mac. |

**Ne passez le NAS en `shadow` ou `cascade` qu'une fois le jeton posé et le worker du Mac testé.**
La bascule est un réglage : elle ne demande aucun déploiement.

### 11.2 Le jeton du worker

L'API du worker (`/api/ocr-worker/*`) est protégée par un jeton **distinct** de la connexion des
utilisateurs, comparé en temps constant, jamais écrit dans les journaux. **Sans jeton, elle est fermée**
(réponse 503) : c'est l'état par défaut du NAS.

1. Générer le jeton : `openssl rand -hex 32` (ou le bouton « Générer » de la section Lecture OCR).
2. NAS : ajouter dans le `.env` du projet, à côté de `DB_PASSWORD` :
   `MATHPRINT_OCR_WORKER_TOKEN=<jeton>`
   puis dans l'environnement du service `api` de votre `compose.yaml` :
   `MATHPRINT_OCR_WORKER_TOKEN: ${MATHPRINT_OCR_WORKER_TOKEN:-}` (déjà présent dans le
   `docker-compose.yml` du dépôt). Relancer le projet. La variable d'environnement prime sur
   le réglage de l'interface.
3. Mac : la même valeur dans la variable `MATHPRINT_OCR_WORKER_TOKEN_NAS` du service
   `com.kironina.ocr-worker` (voir `ocr_worker/config.toml`, bloc `[[servers]]` du NAS à décommenter).
   Ne commitez jamais ce jeton.

Chaque instance (Mac local, NAS) a **son propre jeton** : le worker en porte un par serveur.

### 11.3 L'URL que le worker utilise

Passez par **Tailscale**, pas par `app.kironina.com` :

```
url = "http://fabre.tail428d1a.ts.net:7070"
```

Le port 7070 est celui du conteneur web, dont nginx relaie `/api/` vers l'API. Cette URL n'est
joignable que depuis le tailnet. Si vous utilisiez à la place le nom public (`https://app.kironina.com`),
la route `/api/ocr-worker/` serait exposée sur Internet (protégée par le seul jeton) : bloquez-la
alors dans le reverse proxy Synology. L'URL Tailscale est celle de la mémoire d'accès au NAS ;
vérifiez-la une fois avec `curl -i http://fabre.tail428d1a.ts.net:7070/api/ocr-worker/pending`
(attendu : 401 avec un jeton faux, 503 tant qu'aucun jeton n'est posé).

### 11.4 Quand le Mac est éteint

- `mathpix` et `shadow` : **aucun effet**, les lots ne dépendent pas du Mac.
- `cascade` : le lot passe à l'état `ocr_local_pending` et attend. L'interface affiche
  « Lecture en cours sur le Mac (n/N) » ; si aucun worker n'a sondé le serveur depuis plus de
  2 minutes : « Mac hors ligne, la lecture reprendra à son retour ». Rien n'est perdu : les tâches
  restent en file, un bail expiré (15 minutes par défaut) remet la tâche en file, 3 essais au plus
  puis la découpe part en correction manuelle. Au retour du Mac, le worker reprend seul (launchd le
  relance, `caffeinate -i` empêche la veille pendant un travail) et le lot se termine.
- Une correction très urgente : repassez le NAS en `mathpix` (réglage), puis relancez le lot.

### 11.5 À savoir

- Les découpes à lire sont enregistrées sous `volumes/data/assessments/<id>/scans/derived/ocr/`
  (quelques Ko par découpe) ; les planches Mathpix, si activées, sous `volumes/data/ocr_montages/`.
  La sauvegarde du dossier de données les inclut.
- Les planches Mathpix (plusieurs découpes par appel) sont **désactivées** (`ocr_montage_enabled`)
  tant que `backend/scripts/mathpix_montage_bench.py` n'a pas montré 0 écart avec la lecture unitaire.
- Les nouvelles tables (`ocr_crops`, `ocr_jobs`, `ocr_engine_reads`, `ocr_decisions`,
  `ocr_montages`) sont créées au démarrage de l'API ; deux colonnes ajoutées aux tables du lot 1 le sont
  par la migration. Vérifié sur PostgreSQL 16 (la version de l'image `postgres:16-alpine`).
- Ne lancez pas la suite de tests sur le NAS.

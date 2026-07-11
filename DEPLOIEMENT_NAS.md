# Déployer MathPrint sur un NAS Synology (Container Manager)

Guide pas-à-pas, **priorité à l'interface graphique** (File Station +
Container Manager) : aucune connexion SSH n'est nécessaire, sauf une étape
optionnelle signalée comme telle.

**Prérequis** : DSM 7.2 ou supérieur, avec le paquet **Container Manager**
installé (Panneau de configuration → Centre de paquets → rechercher
« Container Manager » → Installer). Sur DSM plus ancien, le paquet s'appelle
« Docker » et n'a pas l'écran « Projet » décrit ici — il faut alors mettre à
jour DSM au préalable.

---

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
     publication (voir §7). Remplacer par ex. `v1.2.0` pour figer une version
     précise et couper la mise à jour automatique.
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
   (`8080` pour le service `web`). Vérifier qu'il n'entre pas en conflit avec
   un autre service du NAS (DSM utilise en général 5000/5001) ; sinon
   modifier ici le port hôte.
6. Cliquer **Suivant** puis **Terminer** (ou **Exécuter** selon la version).
   Container Manager télécharge les images depuis `ghcr.io` (elles sont
   publiques, aucune authentification requise) puis démarre les 6 conteneurs
   dans l'ordre (`db` doit être « healthy » avant `api`, `api` avant `web`).

Premier démarrage : compter 1 à 2 minutes, principalement pour l'image
`mathprint-mathalea` (plus volumineuse).

## 4. Vérifier que tout tourne

Dans Container Manager → **Projet** → `mathprint`, les 6 conteneurs
(`db`, `queue`, `mathalea`, `api`, `web`, `watchtower`) doivent passer au vert
(« En cours d'exécution »). En cas de souci, clic sur un conteneur →
**Détails** → onglet **Journal** affiche ses logs sans passer par SSH.

Tester ensuite dans un navigateur : `http://<IP-du-NAS>:8080` doit afficher
l'écran de connexion MathPrint.

## 5. Exposer l'application en HTTPS (reverse proxy Synology)

Recommandé plutôt que d'utiliser directement le port 8080 en HTTP.

1. **Panneau de configuration** → **Portail de connexion** (ou « Application
   Portal » selon la version DSM) → onglet **Avancé** → **Reverse Proxy** →
   **Créer**.
2. Description : `MathPrint`.
3. Bloc **Source** : Protocole `HTTPS`, Nom d'hôte : celui que vous utilisez
   pour ce NAS (ex. `mathprint.votre-nom.synology.me` ou un nom local),
   Port `443`.
4. Bloc **Destination** : Protocole `HTTP`, Nom d'hôte `localhost`,
   Port `8080`.
5. Enregistrer. Vérifier qu'un certificat valide est assigné à ce nom d'hôte
   dans **Panneau de configuration** → **Sécurité** → **Certificat**
   (Let's Encrypt via QuickConnect/DDNS, ou certificat importé).

L'application est ensuite accessible en `https://` sans exposer le port 8080
directement.

## 6. Premier lancement : créer votre compte administrateur

Ouvrir `https://<votre-nom-d-hôte>` (ou `http://<IP-du-NAS>:8080`) dans un
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

## 7. Mises à jour automatiques — rien à faire

Une fois l'installation ci-dessus terminée, **aucune action supplémentaire
n'est nécessaire pour les futures mises à jour** : le service `watchtower`
du projet vérifie `ghcr.io` toutes les 5 minutes et redémarre automatiquement
`mathalea`/`api`/`web` dès qu'une nouvelle version « latest » est publiée
(voir le dépôt GitHub, `scripts/release.sh` côté développement). `db` et
`queue` ne sont jamais touchés par Watchtower.

⚠️ Watchtower ne fait que **remplacer l'image** d'un conteneur existant ; il
ne relit jamais `docker-compose.yml` (ports, variables d'environnement,
nouveaux services…). Si ce fichier a changé depuis la création du projet
(ports, `.env` requis, etc.), une mise à jour Watchtower seule ne suffit pas
— il faut recoller le `docker-compose.yml` à jour dans Container Manager →
Projet → **Action** → **Modifier**, puis redéployer.

Pour figer le NAS sur une version précise plutôt que de suivre `latest` :
rouvrir le fichier `.env` (File Station) et remplacer
`MATHPRINT_VERSION=latest` par ex. par `MATHPRINT_VERSION=v1.2.0`, puis dans
Container Manager → Projet `mathprint` → **Action** → **Redéployer** (ou
arrêter/démarrer le projet) pour appliquer.

## 8. Sauvegardes

Deux niveaux complémentaires :

- **Applicatif** : dans MathPrint, Paramètres → Système → **Sauvegarder
  maintenant** (dump de la base dans `/data/backups`, rétention 30 fichiers).
- **NAS** : pour survivre à une panne de disque, sauvegarder le dossier
  `/docker/mathprint/volumes` en entier (contient la base Postgres, Redis et
  les documents générés) avec **Hyper Backup** (Centre de paquets → installer
  Hyper Backup → nouvelle tâche → sélectionner le dossier partagé
  `docker/mathprint`).

## 9. Impression (limite connue)

L'image `mathprint-api` ne contient pas de client CUPS : la détection des
imprimantes **locales** (`lpstat`) ne fonctionnera donc pas telle quelle dans
ce déploiement conteneurisé. Pour imprimer depuis le NAS, utiliser l'écran
**Paramètres → Imprimantes** de MathPrint pour enregistrer une **imprimante
réseau IPP** (`ipp://<ip-imprimante>/ipp/print`) plutôt que de compter sur une
file CUPS locale.

## 10. Dépannage rapide

| Symptôme | Piste |
|---|---|
| Un conteneur reste rouge / redémarre en boucle | Container Manager → conteneur → Détails → Journal (le traceback exact y est) |
| `/` répond mais `/api/...` renvoie 502 | C'est le nginx du conteneur `web` qui ne joint pas `api` : le conteneur `api` est arrêté/en boucle, pas un problème de version — voir la ligne suivante |
| `api` redémarre en boucle, `db` a l'air « en cours d'exécution » | Cause la plus fréquente : `DB_PASSWORD` absent/vide alors que le volume `volumes/postgres` a déjà été initialisé avec ce mot de passe vide — Postgres ne réapplique son mot de passe qu'à la toute première initialisation d'un volume vide. Corrige `DB_PASSWORD` dans `.env` **et** vide le contenu de `volumes/postgres` (File Station) avant de redémarrer, pour forcer une réinitialisation propre. Sans donnée réelle encore stockée, ce vidage est sans risque. |
| Le projet ne veut plus s'arrêter (bouton bloqué) | Arrêter d'abord `watchtower` seul (il recrée sans cesse les conteneurs pendant que vous essayez), puis `api` individuellement plutôt que tout le projet d'un coup. Toujours bloqué après ~1 min : Centre de paquets → Container Manager → **Arrêter** puis **Démarrer** le paquet (reset léger). En dernier recours, redémarrer le NAS — tous les conteneurs s'arrêtent proprement au reboot quel que soit leur état. |
| Page inaccessible sur le port publié | Conflit de port avec un autre service DSM — changer le port hôte dans le projet |
| `docker compose pull` / Watchtower échoue avec 401/403 | Les images `ghcr.io/vald0s0h0/mathprint-*` sont publiques par défaut ; si elles ont été rendues privées entre-temps, ajouter un `docker login ghcr.io` (jeton `read:packages`) — voir README |
| Après une mise à jour, comportement inattendu | Revenir en arrière en fixant `MATHPRINT_VERSION` sur le tag précédent dans `.env` (§7) |

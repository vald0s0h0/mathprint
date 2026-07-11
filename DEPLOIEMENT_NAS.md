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

Le mot de passe de la base et les clés de l'application ne doivent **jamais**
rester aux valeurs par défaut du dépôt (elles sont publiques sur GitHub).

1. Dans `/docker/mathprint`, clic droit → Créer → Fichier texte vierge,
   nommé exactement **`.env`** (bien penser au point initial ; File Station
   peut demander confirmation pour un nom commençant par un point).
2. Double-clic dessus pour l'ouvrir dans l'éditeur de texte intégré de File
   Station, et coller :

   ```env
   DB_PASSWORD=<mot de passe long et unique>
   SECRET_KEY=<chaîne aléatoire longue>
   HMAC_KEY=<autre chaîne aléatoire longue>
   MOCK_MODE=false
   MATHPRINT_VERSION=latest

   ADMIN_EMAIL=<votre e-mail>
   ADMIN_NAME=<votre prénom>
   ADMIN_PASSWORD=<votre mot de passe>
   ```

   - `DB_PASSWORD` / `SECRET_KEY` / `HMAC_KEY` : n'importe quelle phrase
     longue et imprévisible convient (20+ caractères, mélange de mots/chiffres) ;
     elles ne sont jamais affichées nulle part dans l'application. Si vous
     avez un gestionnaire de mots de passe, générez-en trois là plutôt qu'à
     la main.
   - `MOCK_MODE=false` : désactive la classe fictive « 5e Mock » et les
     réponses simulées — à mettre à `true` uniquement pour tester l'appli
     sans données réelles.
   - `MATHPRINT_VERSION=latest` : le NAS suit automatiquement chaque nouvelle
     publication (voir §7). Remplacer par ex. `v1.2.0` pour figer une version
     précise et couper la mise à jour automatique.
   - `ADMIN_EMAIL` / `ADMIN_NAME` / `ADMIN_PASSWORD` : le compte professeur
     créé **une seule fois**, au tout premier démarrage sur une base vide
     (`seed.py`) — sans effet si vous relancez le projet sur une base déjà
     amorcée. Mettre vos vraies valeurs ici, jamais dans un fichier commité.
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

## 6. Première connexion

Le compte professeur est créé automatiquement au tout premier démarrage
(base vide) avec les valeurs `ADMIN_EMAIL` / `ADMIN_PASSWORD` de votre `.env`
(§2) — pas de mot de passe par défaut à changer : vous vous connectez
directement avec vos propres identifiants.

Si vous devez changer ce mot de passe plus tard (l'interface ne propose pas
encore d'écran dédié — à demander en évolution si besoin), la commande
suivante depuis Container Manager le fait en une étape, sans SSH :

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
| Un conteneur reste rouge / redémarre en boucle | Container Manager → conteneur → Détails → Journal |
| `api` ne démarre jamais | Vérifier que `db` est bien passé « healthy » (health-check `pg_isready`) et que `.env` contient bien `DB_PASSWORD`/`SECRET_KEY`/`HMAC_KEY` |
| Page inaccessible sur `:8080` | Conflit de port avec un autre service DSM — changer le port hôte dans le projet |
| `docker compose pull` / Watchtower échoue avec 401/403 | Les images `ghcr.io/vald0s0h0/mathprint-*` sont publiques par défaut ; si elles ont été rendues privées entre-temps, ajouter un `docker login ghcr.io` (jeton `read:packages`) — voir README |
| Après une mise à jour, comportement inattendu | Revenir en arrière en fixant `MATHPRINT_VERSION` sur le tag précédent dans `.env` (§7) |

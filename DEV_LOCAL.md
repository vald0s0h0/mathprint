# Dev local (hot-reload, natif, VS Code)

Environnement de développement **sur le Mac**, sans Docker, avec rechargement à
chaud du backend et du frontend. La prod (NAS) reste en Docker + Postgres — voir
[DEPLOIEMENT_NAS.md](DEPLOIEMENT_NAS.md).

## Topologie

```
Vite :5173  ──(proxy /api)──►  API :8787 (uvicorn --reload)
   HMR                          SQLite backend/mathprint.db
```

- **API** (`backend`, FastAPI) : **8787**, hot-reload scopé sur `backend/app`.
- **Interface** (`frontend`, Vite) : **5173** avec HMR, proxy `/api` → 8787.
- **Base** : **SQLite** `backend/mathprint.db` (créée et migrée automatiquement au
  premier démarrage, `Base.metadata.create_all` + `run_migrations`). Aucun secret
  à fournir : `SECRET_KEY`/`HMAC_KEY` sont générés et persistés dans
  `data/runtime_secrets.env` au 1er boot.

## Lancer

### Option A — VS Code, une seule commande (recommandé)

1. Ouvre le dossier dans VS Code, installe les extensions proposées (Python).
2. Appuie sur **`Cmd+Shift+B`** — lance la tâche par défaut **« Dev ▶ »** :
   les **2 services** (API hot-reload, Vite) dans **un seul terminal**.
   (Équivaut à *Terminal → Run Build Task…*.)
3. Ouvre **http://localhost:5173**.

**Arrêter : `Ctrl+C` dans ce terminal → tout s'arrête d'un coup** (le script tue
l'arbre de processus complet : worker uvicorn, node, etc. — aucun orphelin).

> Variante « 2 terminaux séparés » (logs isolés) : *Run Task… → « Dev: tout
> lancer (2 terminaux séparés) »*. Là, il faut `Ctrl+C` dans chacun.

### Option B — script (hors VS Code)

```bash
./scripts/dev.sh        # bootstrap auto (venv + npm) si nécessaire, puis lance tout
```

Ouvre **http://localhost:5173**. `Ctrl+C` coupe les deux services d'un coup.

### Démarrage automatique (service launchd) et accès à distance

Sur le MacBook Air d'Etienne, `scripts/dev.sh` tourne en service launchd
`com.kironina.dev` (modèle : `scripts/launchd/com.kironina.dev.plist`, copié
dans `~/Library/LaunchAgents`) : lancé à l'ouverture de session, relancé s'il
s'arrête. Journal : `~/Library/Logs/kironina-dev.log`. L'API et Vite se
rechargent toujours à chaud : rien à relancer après une modification.

```bash
launchctl kickstart -k gui/$(id -u)/com.kironina.dev   # redémarrer
launchctl bootout gui/$(id -u)/com.kironina.dev        # arrêter (jusqu'à la prochaine session)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kironina.dev.plist   # relancer
```

Tant que le service tourne, `Cmd+Shift+B` et `./scripts/dev.sh` refusent de
démarrer (port 8787 pris) : l'arrêter d'abord pour déboguer à la main.

**À distance** : Tailscale Serve publie Vite, réservé aux appareils du tailnet,
sur <https://macbook-air-de-etienne.tail428d1a.ts.net> (`tailscale serve status`).
`allowedHosts: ['.ts.net']` dans `vite.config.ts` est là pour ça. Il faut que
Tailscale soit lancé et connecté sur le Mac, et que le Mac soit allumé et
éveillé.

## Centre local et rôles

Ce Mac fait tourner le **centre de service** (édition `centre`, la valeur par
défaut de `MATHPRINT_EDITION`) : établissements accompagnés, Atelier,
administration. Le NAS (`app.kironina.com`) tourne en édition `autonome` : profs
autonomes seulement. Les deux bases sont indépendantes ; le code est le même.

- **Rôles** : `admin` (tout), `corrector` affiché « Atelier » (logistique : lots,
  reprise OCR, impression, distribution, calibration, suivi des sujets ; lecture
  seule sur établissements, profs, classes), `teacher`.
- **Barre latérale** : onglets utilisateur en haut ; en bas, **Atelier**, puis,
  sous un séparateur, **Admin** (Établissements, Utilisateurs, Exercices,
  Paramètres admin). La roue crantée en haut à droite ouvre les paramètres
  personnels.
- **Zone utilisateur de l'admin** : le frontend envoie l'en-tête `X-KN-Zone`
  (`user` | `atelier` | `admin`, déduit de l'URL). En zone `user`, l'admin ne voit
  que ses propres classes de prof ; dans l'Atelier et l'admin, tout.

### Sauvegarde automatique vers le NAS

`scripts/backup-nas.sh` envoie toutes les heures sur le NAS
(`~/sauvegardes/kironina-centre/` du compte Etienne) un instantané vérifié de
`backend/mathprint.db` (48 horaires, 90 quotidiens) et les fichiers nouveaux de
`data/` (sans les caches), dont `runtime_secrets.env`. Service launchd
`com.kironina.backup-nas` (modèle `scripts/launchd/`), journal
`~/Library/Logs/kironina-backup-nas.log`. NAS injoignable (Tailscale coupé) :
la sauvegarde échoue proprement et repart l'heure suivante.

Restaurer : arrêter l'app, `gunzip -c <instantané>.db.gz > backend/mathprint.db`,
recopier `data/` depuis le NAS (`ssh … tar` : voir le script), relancer.

## Débogage Python (breakpoints)

`.vscode/launch.json` fournit le débogueur (debugpy, livré avec l'extension
Python — rien à installer) :

- **« Débogue tout (API + Vite) »** (onglet *Run and Debug*, ▶) :
  lance Vite comme tâche puis l'API sous le débogueur. Pose des
  breakpoints dans `backend/app/**`.
- Si un breakpoint ne s'active pas après un reload, utilise **« Débogue l'API
  (sans reload — breakpoints fiables) »**.

## Premier démarrage

Base vide → l'écran de démarrage (`http://localhost:5173`) crée le **compte
administrateur** (e-mail / prénom / mot de passe + clés API facultatives).

**Génération / correction réelles** : sans clé API, les fournisseurs LLM
basculent sur le repli *offline* simulé (`services/providers.offline()`), ce qui
permet de faire tourner toute l'interface. Pour de la vraie génération
(DeepSeek / Gemini / Mistral OCR) ou correction (Mathpix), saisis les clés dans
l'écran de démarrage ou **Paramètres**. La vision QCM (OpenCV) fonctionne en
local sans clé.

## Réinitialiser la base

```bash
rm -f backend/mathprint.db          # repart d'une base vierge (re-crée l'admin)
rm -f data/runtime_secrets.env      # optionnel : régénère SECRET_KEY/HMAC_KEY
```

(La suppression fine par classe/élève/sujet reste possible dans **Paramètres →
Données**.)

## Tests

```bash
cd backend && .venv/bin/python -m pytest tests/     # unitaires
cd backend && bash e2e_test.sh                      # E2E mock
```

## Notes / pièges

- Le `--reload` de l'API est **volontairement limité à `backend/app`**
  (`--reload-dir app`) : sinon les écritures dans `mathprint.db` / `.venv` /
  `data/` relanceraient le serveur en boucle.
- **SQLite ≠ Postgres.** Le dev tourne en SQLite ; la prod en Postgres. Certaines
  migrations passent en SQLite mais cassent en Postgres (types de colonnes, cf.
  `backend/app/db.py`). Les incompatibilités se rattrapent en CI, pas ici.
- Ports occupés ? Change-les de façon cohérente : API `--port`, `MATHPRINT_API`
  pour le proxy Vite (`MATHPRINT_API=http://localhost:8899 npm run dev`).
```


## Worker OCR (lecture locale)

Le worker (`ocr_worker/`, son propre venv) lit les découpes des copies avec des moteurs locaux ;
il parle à l'API en HTTP, y compris celle du Mac (`http://127.0.0.1:8787`).

1. Générer un jeton dans Paramètres admin → Pédagogie → Lecture OCR multicouche (« Générer »).
2. Le lancer à la main : `MATHPRINT_OCR_WORKER_TOKEN=<jeton> HF_HUB_OFFLINE=1 ocr_worker/.venv/bin/python -m ocr_worker --once`
3. En service : renseigner le jeton dans `scripts/launchd/com.kironina.ocr-worker.plist`,
   puis `cp` vers `~/Library/LaunchAgents/` et `launchctl load` (journal : `~/Library/Logs/kironina-ocr-worker.log`).

Tests : `backend/.venv/bin/python -m pytest backend/tests/test_ocr_*.py` ;
`ocr_worker/.venv/bin/python -m pytest ocr_worker/tests` ; moteurs réels (5 découpes chacun) :
`OCR_REAL=1 HF_HUB_OFFLINE=1 ocr_worker/.venv/bin/python -m pytest ocr_worker/tests/test_engines_real.py -s`.
Sur PostgreSQL jetable : `MATHPRINT_TEST_PG_URL=postgresql+psycopg2://…/mathprint_test` (voir `backend/tests/pgsupport.py`).

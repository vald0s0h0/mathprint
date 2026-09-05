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

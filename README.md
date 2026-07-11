# MathPrint

Plateforme NAS de génération, correction automatisée et suivi adaptatif en
mathématiques (collège). Implémentation du cahier des charges
(`cahier de charges.md`) — phases 0 à 4.

## Démarrage rapide (développement)

```bash
# Service MathALÉA (port 8123) — nécessite le clone ./mathalea (déjà présent)
cd mathalea-service && npm install && npm start

# API (port 8787)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8787

# Interface (port 5173, proxy /api -> 8787)
cd frontend && npm install && npm run dev
```

Compte amorcé : `prof@mathprint.local` / `mathprint`.
Classe « 5e Mock » (5 élèves imaginaires) créée en mode mock — désactivable
dans Paramètres → Système.

Tests unitaires : `cd backend && .venv/bin/python -m pytest tests/` (18 tests)
E2E mock : `cd backend && bash e2e_test.sh`
E2E scan réel : générer un sujet puis
`python scripts/make_synthetic_scan.py <assessment_id>` et déposer le PDF
produit dans Corrections.

## Déploiement NAS (Docker Compose) & mises à jour automatiques

Le NAS ne construit plus les images localement : il les tire depuis GHCR et
une release suffit à mettre à jour la production, sans rien toucher sur place.

```bash
cp .env.example .env   # DB_PASSWORD, SECRET_KEY, HMAC_KEY, MATHPRINT_VERSION
docker compose up -d
# services : db (PostgreSQL), queue (Redis), mathalea/api/web (ghcr.io/vald0s0h0/mathprint-*),
# watchtower (met à jour mathalea/api/web automatiquement)
```

**Publier une nouvelle version** (depuis un poste de dev, dépôt à jour) :

```bash
./scripts/release.sh v1.2.0
```

Ce script crée le tag git et le pousse. `.github/workflows/release.yml` fait
alors le reste : tests backend/frontend, build des 3 images
(`mathprint-api`, `mathprint-web`, `mathprint-mathalea`), publication sur
`ghcr.io/vald0s0h0/` avec le tag de version **et** `latest`, création de la
release GitHub. Le service `watchtower` du NAS vérifie `ghcr.io` toutes les
5 minutes (label `com.centurylabs.watchtower.enable`) et redémarre
automatiquement mathalea/api/web dès que `latest` change — **aucune
intervention manuelle sur le NAS**. `db` et `queue` ne portent pas ce label et
ne sont donc jamais touchés.

Pour figer le NAS sur une version précise plutôt que de suivre `latest`,
définir `MATHPRINT_VERSION=v1.2.0` dans `.env` puis `docker compose up -d`
(Watchtower cessera alors de mettre ce déploiement à jour).

**Première publication** : les paquets GHCR créés par la CI héritent parfois
d'une visibilité privée par défaut — si `docker compose pull` échoue sur le
NAS avec une erreur 401/403, rendre les 3 paquets publics dans
GitHub → Packages, ou exécuter `docker login ghcr.io` sur le NAS avec un
jeton `read:packages`.

## Fonctionnalités

**Sujets** — assistant 4 étapes (cible en **pages** : 1 = recto,
2 = recto/verso…) ; trois sources d'exercices :
- **exercices IA (DeepSeek deepseek-v4-pro)** : ciblés sur une compétence
  précise du programme officiel (prompt = code + libellé + thème + objectifs
  voisins), déclinés en **5 niveaux de difficulté**, validés par le moteur
  déterministe puis **stockés en banque** (`generated_exercises`) et réutilisés
  sans nouvel appel ; le niveau servi suit le niveau 1-10 de l'élève ;
- 534 exercices MathALÉA v2.8.2 (service Node headless, seedé) ;
- 7 générateurs intégrés.

**Design des copies** : cartes d'exercices à coins arrondis avec ombre, icône
crayon et pastilles de difficulté (1-5) ; **rappels de leçon DeepSeek** pour
les élèves fragiles (niveau ≤ 4, entraînement) dans un cadre distinct ambre
avec icône livre, stockés dans `lesson_snippets` ; QCM compacts en ligne ;
en-tête structuré (titre + filet, cartouche nom, case Note et bande
Appréciation). **Distinction visuelle** : rouge saumon = l'élève écrit
(dropout) ; **pointillés gris = réservé à l'overlay de correction** (case
Note, appréciation, bande « correction » sous chaque exercice).
**Aperçu PDF intégré** : modale avec navigation par copie (plus facile /
médiane / plus difficile), lot complet et overlay.

**Correction** — deux chemins dans la même machine d'états (§6.1) :
- *scan réel* (PDF déposé) : raster pypdfium2 → lecture des 4 QR
  (relecture par ROI suréchantillonnée, repli fiduciel géométrique) →
  vérification HMAC (page douteuse bloquée, RM-001) → homographie vers l'A4
  canonique (seuil 1,5 mm) → crops par zone → **filtre dropout HSV** (supprime
  le rouge saumon, conserve l'encre noire/bleue) → détection QCM par densité
  de la zone intérieure (double coche → exception) → détection de vide (aucun
  appel Mathpix pour une zone vide) → OCR Mathpix des zones manuscrites ;
- *lot simulé* (mode mock, sans fichier) : exerce tout le chemin décisionnel.
Décisions par tiers A–E, file de validation clavier, finalisation → preuves de
compétence + courbe d'oubli, overlay rouge #C62828 avec nom de l'élève.

**Compétences** — grilles officielles extraites des programmes
(`scripts/extract_competencies.py` sur les PDF du dossier `context/`) :
**332 objectifs d'apprentissage** — 6e = cycle 3 (année Sixième uniquement,
CM1/CM2 exclus), 5e/4e/3e = cycle 4. Hiérarchie domaine > thème > objectif avec
codes stables (ex. `5E-NC-OPER-07`). UI compacte : accordéon par domaine,
lignes uniques code + libellé + mini-barre de maîtrise + probabilité de rappel
sur la même ligne.

**Impression** — imprimantes **CUPS locales du poste** (détectées via
`lpstat -e`, ex. les Canon configurées sur le Mac) et **imprimantes réseau
IPP** enregistrées en base (pilotables depuis le NAS). Taille réelle 100 %
imposée (`print-scaling=none`), recto/verso, copies, journal des jobs.
Boutons d'impression dans Sujets (lot, overlay) et Corrections.

**Phase 4** — assistant de calibration (page test 4 marqueurs + trait 100 mm ;
mesure offsets/échelle/rotation depuis le scan), sauvegardes base (SQLite
backup API / pg_dump, rétention 30), statut système (base, MathALÉA, disque),
audit des impressions.

## Structure

```
mathalea/               # clone MathALÉA v2.8.2 épinglé (AGPL)
mathalea-service/       # runner Node headless : /catalog, /generate (seedé)
backend/app/
  data/competencies_fr.json   # grilles officielles extraites
  services/
    worker_cv.py        # raster, QR, homographie, dropout, QCM, vide
    mathalea_client.py  # adaptateur MathALÉA -> contrat interne
    grading.py          # comparateurs déterministes (tiers A-E)
    forgetting.py       # courbe d'oubli sans LLM
    pdfgen.py           # gabarits A4, marqueurs, overlay
    pipeline.py         # machine d'états scan réel + mock
    providers.py        # Mathpix/DeepSeek/Claude + budgets + mock
  routers/              # + printing.py (CUPS/IPP), system.py (backup/calibration)
  scripts/
    extract_competencies.py   # PDF programmes -> JSON hiérarchique
    make_synthetic_scan.py    # scan de test (encre simulée + rotation)
frontend/src/
  components/PdfPreview.tsx   # modale aperçu PDF multi-copies
  components/PrintButton.tsx  # impression CUPS/IPP
  pages/                # 6 écrans
```

## Reste simulé sans clés API

Mathpix/DeepSeek/Claude ont des clients réels mais basculent en mock sans clé
(Paramètres → API). En mode mock, l'OCR des zones manuscrites renvoie une
réponse plausible ; toute la chaîne CV (QR, recalage, crops, QCM, vide) est
réelle et testée sur scans synthétiques (rotation ±1,2°, 5/5 pages recalées).

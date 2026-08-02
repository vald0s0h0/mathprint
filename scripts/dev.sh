#!/usr/bin/env bash
# Environnement de dev local (hot-reload) SANS VS Code — même topologie que
# .vscode/tasks.json. Voir DEV_LOCAL.md.
#
#   MathALÉA :8123  →  API :8787 (uvicorn --reload)  →  Vite :5173 (proxy /api)
#
# Usage : ./scripts/dev.sh        (bootstrap auto si venv / node_modules absents)
# Arrêt : Ctrl+C (coupe les trois services proprement).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;36m[dev]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[dev] %s\033[0m\n' "$*" >&2; exit 1; }

# --- pré-requis outillage -----------------------------------------------------
command -v python3 >/dev/null || die "python3 introuvable"
command -v node    >/dev/null || die "node introuvable"
command -v npm     >/dev/null || die "npm introuvable"
[ -d "$ROOT/mathalea/src" ] || die "clone MathALÉA absent (dossier ./mathalea) — voir README §Démarrage rapide"

# --- bootstrap backend (venv) -------------------------------------------------
if [ ! -x "$ROOT/backend/.venv/bin/uvicorn" ]; then
  log "création du venv backend + installation des dépendances (première fois)…"
  python3 -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install --upgrade pip >/dev/null
  "$ROOT/backend/.venv/bin/pip" install -r "$ROOT/backend/requirements-dev.txt"
fi

# --- bootstrap node -----------------------------------------------------------
[ -d "$ROOT/mathalea-service/node_modules" ] || { log "npm install (mathalea-service)…"; (cd "$ROOT/mathalea-service" && npm install --no-audit --no-fund); }
[ -d "$ROOT/frontend/node_modules" ]         || { log "npm install (frontend)…";         (cd "$ROOT/frontend" && npm install --no-audit --no-fund); }

# --- arrêt propre au Ctrl+C ---------------------------------------------------
# On tue l'ARBRE complet : uvicorn --reload lance un sous-processus worker, npm
# lance node, etc. Signaler seulement le pid direct laisserait des orphelins.
pids=()
kill_tree() {  # $1 = signal, $2 = pid racine
  local sig=$1 pid=$2 child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$sig" "$child"; done
  kill -"$sig" "$pid" 2>/dev/null || true
}
_cleaned=0
cleanup() {
  [ "$_cleaned" = 1 ] && return; _cleaned=1
  trap - INT TERM EXIT          # évite la ré-entrée (EXIT rappellerait cleanup)
  log "arrêt…"
  for p in "${pids[@]:-}"; do kill_tree TERM "$p"; done
  sleep 1
  for p in "${pids[@]:-}"; do kill_tree KILL "$p"; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- lancement ----------------------------------------------------------------
log "MathALÉA  → http://localhost:8123"
( cd "$ROOT/mathalea-service" && exec npm start ) & pids+=($!)

log "API       → http://localhost:8787  (hot-reload)"
# reload scopé sur le package app (sinon reload en boucle sur mathprint.db/.venv)
( cd "$ROOT/backend" && exec .venv/bin/uvicorn app.main:app --reload --reload-dir app --port 8787 ) & pids+=($!)

log "Interface → http://localhost:5173  (ouvre cette URL)"
( cd "$ROOT/frontend" && exec npm run dev ) & pids+=($!)

log "les 3 services tournent. Ctrl+C pour tout arrêter."
wait

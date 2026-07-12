#!/bin/sh
# Mise à jour MathPrint sur le NAS Synology — à lancer par une tâche planifiée
# DSM (Panneau de configuration → Planificateur de tâches → script défini par
# l'utilisateur, exécuté en root). Voir DEPLOIEMENT_NAS.md §7 pour le
# clic-par-clic.
#
# Principe : l'équivalent exact de ce qu'on ferait à la main —
#   docker compose pull   (télécharge les images :latest plus récentes)
#   docker compose up -d  (ne recrée QUE les conteneurs dont l'image a changé)
# puis purge des anciennes images. Pas de Watchtower : ce mécanisme est
# déterministe et compatible avec Container Manager (le projet reste géré
# par le même fichier compose).
#
# Chaque exécution est journalisée dans update.log à côté du compose ; la
# version réellement en service est visible dans Paramètres → Système.
set -eu

# Dossier du projet (adapter si le partage/dossier diffère)
PROJECT_DIR="${MATHPRINT_DIR:-/volume1/docker/mathprint}"
LOG="$PROJECT_DIR/update.log"
LOCK="$PROJECT_DIR/.update.lock"

cd "$PROJECT_DIR"

# docker n'est pas toujours dans le PATH des tâches planifiées DSM
PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# anti-chevauchement si une exécution précédente traîne encore
if [ -e "$LOCK" ] && [ "$(find "$LOCK" -mmin -30 2>/dev/null)" ]; then
  log "SKIP: mise à jour déjà en cours (lock présent)"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

log "=== vérification des mises à jour"
if ! "$DOCKER" compose pull --quiet >> "$LOG" 2>&1; then
  log "ERREUR: docker compose pull a échoué (réseau ? ghcr.io ?)"
  exit 1
fi

# up -d ne touche que les conteneurs dont l'image (ou la config) a changé
BEFORE=$("$DOCKER" ps --format '{{.Names}} {{.Image}} {{.ID}}' | sort)
if ! "$DOCKER" compose up -d --remove-orphans >> "$LOG" 2>&1; then
  log "ERREUR: docker compose up -d a échoué — voir ci-dessus"
  exit 1
fi
AFTER=$("$DOCKER" ps --format '{{.Names}} {{.Image}} {{.ID}}' | sort)

if [ "$BEFORE" = "$AFTER" ]; then
  log "OK: déjà à jour, rien à faire"
else
  log "OK: conteneurs mis à jour :"
  echo "$AFTER" | grep -vxF "$BEFORE" >> "$LOG" 2>/dev/null || true
fi

# purge des images détachées (les anciennes versions) pour ne pas remplir
# le volume système du NAS
"$DOCKER" image prune -f >> "$LOG" 2>&1 || true

# garder un journal raisonnable (~2000 dernières lignes)
if [ "$(wc -l < "$LOG")" -gt 4000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

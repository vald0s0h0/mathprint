#!/usr/bin/env bash
# Livre une mise à jour en une commande : tests → contrôle du NAS → sauvegarde
# de la base → nouvelle version → commit → tag annoté → push. Le push sur main
# déclenche la CI (deploy.yml), puis la tâche planifiée DSM met le NAS à jour.
#
# Usage :
#   scripts/ship.sh [options] "message de commit"
#
# Options :
#   --minor | --major   niveau de la nouvelle version (défaut : patch)
#   --no-bump           commit + push sans nouvelle version (docs, entreprise/)
#   --skip-tests        ne lance pas les tests
#   --no-backup         pas de sauvegarde de la base avant le push
#   --force             pousse même si le NAS est injoignable ou occupé
#   --yes               pas de confirmation (usage par un agent)
#   --dry-run           vérifie tout, ne modifie rien
#
# Le contrôle du NAS existe parce qu'une mise à jour redémarre l'API : une
# correction de scans en cours serait tuée net (c'est déjà arrivé).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

NAS_HOST="${MATHPRINT_NAS:-Etienne@fabre.tail428d1a.ts.net}"
NAS_DIR="${MATHPRINT_NAS_DIR:-/volume1/docker/mathprint}"
DOCKER="sudo -n /usr/local/bin/docker"

level=patch; bump=1; tests=1; backup=1; force=0; yes=0; dry=0; msg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --minor) level=minor ;;
    --major) level=major ;;
    --no-bump) bump=0 ;;
    --skip-tests) tests=0 ;;
    --no-backup) backup=0 ;;
    --force) force=1 ;;
    --yes) yes=1 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Option inconnue : $1" >&2; exit 1 ;;
    *) [ -z "$msg" ] || { echo "Un seul message de commit (entre guillemets)." >&2; exit 1; }; msg="$1" ;;
  esac
  shift
done

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
nas() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_HOST" "$@"; }

[ -n "$msg" ] || [ "$dry" = 1 ] || die "Message de commit manquant : scripts/ship.sh \"message\""
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "Branche courante : $(git rev-parse --abbrev-ref HEAD). La livraison se fait depuis main."
[ -n "$(git status --porcelain)" ] || [ "$bump" = 1 ] || die "Rien à commiter."
current=$(sed -nE 's/^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' backend/app/version.py)

# 1. Tests : les mêmes que la CI, pour ne pas découvrir l'échec après le push.
if [ "$tests" = 1 ]; then
  step "Tests backend"
  py=backend/.venv/bin/python; [ -x "$py" ] || py=python3
  (cd backend && "../$py" -m pytest tests/ -q) || die "Tests backend en échec : rien n'a été poussé."
  step "Vérification des types frontend"
  (cd frontend && npx tsc -b) || die "Erreurs TypeScript : rien n'a été poussé."
fi

# 2. NAS : aucune correction ni génération en cours.
step "Contrôle du NAS ($NAS_HOST)"
SQL="SELECT (SELECT count(*) FROM scan_batches WHERE status IN ('uploaded','split','identified','registered','cropped','ocr_complete','ocr_confirmed') AND error IS NULL AND created_at > now() - interval '3 hours') + (SELECT count(*) FROM jobs WHERE status IN ('pending','running') AND coalesce(updated_at, created_at) > now() - interval '30 minutes')"
if busy=$(nas "$DOCKER exec mathprint-db-1 psql -U mathprint -d mathprint -tAc \"$SQL\"" 2>/dev/null); then
  busy=$(echo "$busy" | tr -d '[:space:]')
  if [ "${busy:-0}" != 0 ]; then
    [ "$force" = 1 ] || die "$busy traitement(s) en cours sur le NAS (correction ou génération). Réessaie dans quelques minutes, ou --force."
    echo "⚠ $busy traitement(s) en cours, poussé quand même (--force)."
  else
    echo "Aucun traitement en cours."
  fi
else
  [ "$force" = 1 ] || die "NAS injoignable (Tailscale actif ?). Relance avec --force pour pousser sans contrôle."
  echo "⚠ NAS injoignable, poussé sans contrôle (--force)."
fi

# 3. Récapitulatif et confirmation.
step "Récapitulatif"
git status --short
if [ "$bump" = 1 ]; then echo "Version : $current → nouvelle version $level"; else echo "Version inchangée ($current)"; fi
echo "Message : ${msg:-<aucun>}"
if [ "$dry" = 1 ]; then echo; echo "--dry-run : rien n'a été modifié."; exit 0; fi
if [ "$yes" != 1 ]; then
  [ -t 0 ] || die "Pas de terminal pour confirmer : ajoute --yes."
  printf 'Tout commiter et pousser sur main ? [o/N] '; read -r ok
  case "$ok" in o|O|oui|y|Y) ;; *) die "Annulé." ;; esac
fi

# 4. Sauvegarde de la base avant que la mise à jour ne touche à quoi que ce soit.
if [ "$backup" = 1 ] && [ "$bump" = 1 ]; then
  step "Sauvegarde de la base sur le NAS"
  dump="volumes/data/backups/mathprint-$(date +%Y%m%d-%H%M)-avant-v$current.dump"
  nas "cd $NAS_DIR && mkdir -p volumes/data/backups && $DOCKER exec mathprint-db-1 pg_dump -U mathprint --format=custom mathprint > $dump && test -s $dump && ls -lh $dump" \
    || { [ "$force" = 1 ] || die "Sauvegarde impossible : rien n'a été poussé (--no-backup pour passer outre)."; echo "⚠ Sauvegarde impossible, poursuite (--force)."; }
fi

# 5. Version, commit, tag annoté (--follow-tags ignore les tags légers), push.
git add -A
if [ "$bump" = 1 ]; then
  step "Nouvelle version"
  scripts/bump-version.sh "$level" | sed -n 1p   # le tag est posé plus bas
  new=$(sed -nE 's/^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' backend/app/version.py)
  git commit -q -m "$msg" -m "Version $new" && echo "Commit créé."
  git tag -a "v$new" -m "MathPrint $new — $msg"
else
  git commit -q -m "$msg" && echo "Commit créé."
fi
step "Push"
git push --follow-tags origin main || die "Push refusé : le commit est local. Relance : git push --follow-tags origin main"

echo
echo "✓ Livré${new:+ en v$new}. La CI construit les images, puis le NAS se met à jour à sa prochaine tâche planifiée."
echo "  Vérifier la version en service : Paramètres → Système."

#!/usr/bin/env bash
# Livre une mise à jour en une commande : tests → contrôle du NAS → sauvegarde
# de la base → nouvelle version → commit → tag annoté → push. Le push sur main
# déclenche la CI (deploy.yml) ; la tâche planifiée DSM met ensuite le NAS à
# jour la nuit (00:00–00:45), ou tout de suite avec --now.
#
# Usage :
#   scripts/ship.sh [options] "message de commit"
#   scripts/ship.sh --deploy-only     # met le NAS à jour avec le dernier push
#
# Options :
#   --minor | --major   niveau de la nouvelle version (défaut : patch)
#   --no-bump           commit + push sans nouvelle version (docs)
#   --now               attend la CI puis met le NAS à jour tout de suite
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
py="$PWD/backend/.venv/bin/python"; [ -x "$py" ] || py=python3

level=patch; bump=1; now=0; deploy_only=0; tests=1; backup=1; force=0; yes=0; dry=0; msg=""; new=""
while [ $# -gt 0 ]; do
  case "$1" in
    --minor) level=minor ;;
    --major) level=major ;;
    --no-bump) bump=0 ;;
    --now) now=1 ;;
    --deploy-only) deploy_only=1 ;;
    --skip-tests) tests=0 ;;
    --no-backup) backup=0 ;;
    --force) force=1 ;;
    --yes) yes=1 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Option inconnue : $1" >&2; exit 1 ;;
    *) [ -z "$msg" ] || { echo "Un seul message de commit (entre guillemets)." >&2; exit 1; }; msg="$1" ;;
  esac
  shift
done

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
nas() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_HOST" "$@"; }

# Nombre de corrections ou générations en cours sur le NAS (échoue si injoignable).
SQL="SELECT (SELECT count(*) FROM scan_batches WHERE status IN ('uploaded','split','identified','registered','cropped','ocr_complete','ocr_confirmed') AND error IS NULL AND created_at > now() - interval '3 hours') + (SELECT count(*) FROM jobs WHERE status IN ('pending','running') AND coalesce(updated_at, created_at) > now() - interval '30 minutes')"
nas_busy() { nas "$DOCKER exec mathprint-db-1 psql -U mathprint -d mathprint -tAc \"$SQL\"" 2>/dev/null | tr -d '[:space:]'; }

# État du workflow deploy.yml pour le commit HEAD : none | <status>:<conclusion>.
# Dépôt privé : exporter GITHUB_TOKEN (jeton avec accès en lecture aux Actions).
ci_state() {
  local repo url
  repo=$(git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')
  url="https://api.github.com/repos/$repo/actions/runs?head_sha=$(git rev-parse HEAD)"
  if [ -n "${GITHUB_TOKEN:-}" ]; then curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN" "$url"; else curl -fsS "$url"; fi \
    | "$py" -c 'import json,sys
rs=[r for r in json.load(sys.stdin).get("workflow_runs",[]) if r.get("path","").endswith("deploy.yml")]
print("none" if not rs else rs[0]["status"] + ":" + str(rs[0]["conclusion"]))'
}

# Attend la CI du dernier commit, puis met le NAS à jour et vérifie l'API.
deploy_now() {
  step "Attente de la CI (déploiement des images)"
  local state="" fails=0 i
  for i in $(seq 1 60); do
    if state=$(ci_state 2>/dev/null); then fails=0; else fails=$((fails + 1)); state=""; fi
    [ "$fails" -lt 3 ] || die "État de la CI illisible (dépôt privé ? exporter GITHUB_TOKEN). Le NAS se mettra à jour cette nuit."
    case "$state" in
      completed:success) echo "CI réussie."; break ;;
      completed:*) die "La CI a échoué ($state) : le NAS n'a pas été touché." ;;
      none) [ "$i" -lt 6 ] || { echo "Aucun déploiement déclenché (modifications hors code) : rien à mettre à jour."; return 0; } ;;
    esac
    sleep 20
  done
  [ "$state" = completed:success ] || die "CI toujours en cours après 20 min : relance « scripts/ship.sh --deploy-only » plus tard."

  step "Mise à jour du NAS"
  local busy
  busy=$(nas_busy) || die "NAS injoignable (Tailscale actif ?) : il se mettra à jour cette nuit."
  [ "${busy:-0}" = 0 ] || die "$busy traitement(s) en cours : relance « scripts/ship.sh --deploy-only » dans quelques minutes."
  nas "cd $NAS_DIR && $DOCKER compose pull -q api web && $DOCKER compose up -d api web" \
    || die "Échec de docker compose sur le NAS : voir les journaux dans $NAS_DIR."

  step "Vérification"
  local code="" build
  for i in $(seq 1 24); do
    code=$(nas "curl -s -o /dev/null -w '%{http_code}' localhost:7070/api/health" 2>/dev/null || true)
    [ "$code" = 200 ] && break
    sleep 5
  done
  [ "$code" = 200 ] || die "L'API ne répond pas après 2 min : vérifier « docker logs mathprint-api-1 » sur le NAS."
  build=$(nas "$DOCKER exec mathprint-api-1 printenv MATHPRINT_BUILD_SHA" | tr -d '[:space:]')
  nas "cd $NAS_DIR && echo \"\$(date '+%Y-%m-%d %H:%M:%S') OK: mise à jour immédiate (ship) -> $build\" >> update.log && $DOCKER image prune -f >/dev/null" || true
  echo "✓ NAS à jour : API en ligne, build $build."
  [ "$build" = "$(git rev-parse --short=7 HEAD)" ] || echo "  (build de l’API antérieur au dernier commit : normal si seul le web a changé)"
}

if [ "$deploy_only" = 1 ]; then deploy_now; exit 0; fi

[ -n "$msg" ] || [ "$dry" = 1 ] || die "Message de commit manquant : scripts/ship.sh \"message\""
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || die "Branche courante : $(git rev-parse --abbrev-ref HEAD). La livraison se fait depuis main."
[ -n "$(git status --porcelain)" ] || [ "$bump" = 1 ] || die "Rien à commiter."
current=$(sed -nE 's/^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' backend/app/version.py)

# 1. Tests : les mêmes que la CI, pour ne pas découvrir l'échec après le push.
if [ "$tests" = 1 ]; then
  step "Tests backend"
  (cd backend && "$py" -m pytest tests/ -q) || die "Tests backend en échec : rien n'a été poussé."
  step "Vérification des types frontend"
  (cd frontend && npx tsc -b) || die "Erreurs TypeScript : rien n'a été poussé."
fi

# 2. NAS : aucune correction ni génération en cours.
step "Contrôle du NAS ($NAS_HOST)"
if busy=$(nas_busy); then
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
[ "$now" = 1 ] && echo "Mise à jour du NAS : immédiate, après la CI" || echo "Mise à jour du NAS : cette nuit (00:00–00:45)"
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
  git tag -a "v$new" -m "Kiro & Nina $new — $msg"
else
  git commit -q -m "$msg" && echo "Commit créé."
fi
step "Push"
git push --follow-tags origin main || die "Push refusé : le commit est local. Relance : git push --follow-tags origin main"
echo "✓ Poussé${new:+ en v$new}."

if [ "$now" = 1 ]; then
  deploy_now
else
  echo "  La CI construit les images ; le NAS se mettra à jour cette nuit (00:00–00:45)."
  echo "  Tout de suite : scripts/ship.sh --deploy-only"
fi

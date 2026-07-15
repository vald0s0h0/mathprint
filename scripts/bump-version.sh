#!/usr/bin/env bash
# Incrémente la version applicative PARTOUT d'un seul coup et pose le tag git.
#
# Source unique de vérité : backend/app/version.py (__version__). Ce script la
# lit, l'incrémente (patch par défaut ; « minor » / « major » possibles) et
# réécrit les 3 emplacements qui doivent rester synchronisés :
#   - backend/app/version.py      (API : /system, dashboard)
#   - frontend/src/version.ts     (affichage « MathPrint vX.Y.Z » en bas à gauche)
#   - frontend/package.json       (métadonnée build web)
# puis stage ces fichiers et crée le tag « vX.Y.Z ».
#
# Usage :
#   scripts/bump-version.sh [patch|minor|major]   # défaut : patch
#   scripts/bump-version.sh --set X.Y.Z            # fixe une version précise
#
# À lancer AVANT chaque commit/push destiné au NAS : le commit inclura les
# fichiers de version bumpés, et « git push --follow-tags » publiera le tag
# (release.yml construit alors l'image épinglable ghcr …:vX.Y.Z).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION_PY="backend/app/version.py"
VERSION_TS="frontend/src/version.ts"
PKG_JSON="frontend/package.json"

current=$(sed -nE 's/^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' "$VERSION_PY")
[ -n "$current" ] || { echo "Version courante introuvable dans $VERSION_PY" >&2; exit 1; }
IFS='.' read -r major minor patch <<< "$current"

case "${1:-patch}" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
  --set) new="${2:?--set requiert X.Y.Z}"; IFS='.' read -r major minor patch <<< "$new" ;;
  *) echo "Argument inconnu : $1 (patch|minor|major|--set X.Y.Z)" >&2; exit 1 ;;
esac
new="${major}.${minor}.${patch}"

if git rev-parse "v${new}" >/dev/null 2>&1; then
  echo "Le tag v${new} existe déjà — choisis une version supérieure." >&2
  exit 1
fi

# python : réécrit __version__
perl -0pi -e "s/__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"/__version__ = \"${new}\"/" "$VERSION_PY"
# ts : réécrit APP_VERSION
perl -0pi -e "s/APP_VERSION = '[0-9]+\.[0-9]+\.[0-9]+'/APP_VERSION = '${new}'/" "$VERSION_TS"
# package.json : réécrit le champ version
perl -0pi -e "s/\"version\": \"[0-9]+\.[0-9]+\.[0-9]+\"/\"version\": \"${new}\"/" "$PKG_JSON"

git add "$VERSION_PY" "$VERSION_TS" "$PKG_JSON"
echo "Version ${current} -> ${new}"
echo "Fichiers stagés. Après ton commit, pose le tag :"
echo "  git tag v${new} && git push --follow-tags"

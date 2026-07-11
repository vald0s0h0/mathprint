#!/usr/bin/env bash
# Publie une nouvelle version MathPrint : crée le tag git vX.Y.Z et le pousse.
# La CI (.github/workflows/release.yml) construit alors les 3 images et les
# publie sur ghcr.io (version + "latest") ; Watchtower met ensuite le NAS à
# jour automatiquement dans les 5 minutes (voir README > Déploiement).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERSION="${1:-}"
if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: scripts/release.sh vX.Y.Z" >&2
  exit 1
fi

if git rev-parse "$VERSION" >/dev/null 2>&1; then
  echo "Le tag $VERSION existe déjà." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Arbre de travail non propre — commiter ou stasher avant de publier." >&2
  exit 1
fi

git tag -a "$VERSION" -m "MathPrint $VERSION"
git push origin "$VERSION"
echo "Tag $VERSION poussé — suivre la build sur l'onglet Actions du dépôt GitHub."

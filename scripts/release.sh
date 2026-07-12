#!/usr/bin/env bash
# Publie une version JALONNÉE vX.Y.Z (optionnel : le déploiement continu
# publie déjà "latest" à chaque push sur main via deploy.yml). La CI
# (.github/workflows/release.yml) construit les 3 images, les publie sur
# ghcr.io (vX.Y.Z + "latest") et crée la release GitHub. Utile pour épingler
# un état stable (MATHPRINT_VERSION=vX.Y.Z sur le NAS).
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

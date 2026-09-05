#!/usr/bin/env bash
# Publie une version JALONNÉE vX.Y.Z (optionnel : le déploiement continu
# publie déjà "latest" à chaque push sur main via deploy.yml). La CI
# (.github/workflows/release.yml) construit les 3 images, les publie sur
# ghcr.io (X.Y.Z + "latest") et crée la release GitHub. Utile pour épingler
# un état stable (MATHPRINT_VERSION=X.Y.Z sur le NAS — le tag d'image n'a PAS
# le « v » du tag git : « v2.4.0 » ici, « …-api:2.4.0 » sur ghcr.io).
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
echo "Pour épingler le NAS : MATHPRINT_VERSION=${VERSION#v}  (sans le « v »)."

"""Version applicative — SOURCE UNIQUE DE VÉRITÉ.

Livrée dans l'image (COPY app ./app) donc disponible côté API, et tenue
synchronisée avec `frontend/src/version.ts` (affichée en bas à gauche de
l'interface) et le tag git `vX.Y.Z` par `scripts/bump-version.sh`, lancé à
chaque commit/push (cf. le script). Ne pas éditer à la main : utiliser le
script pour incrémenter partout d'un coup.
"""
__version__ = "1.0.2"

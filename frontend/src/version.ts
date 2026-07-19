// Version applicative affichée en bas à gauche de l'interface.
// SOURCE UNIQUE DE VÉRITÉ côté web, tenue synchronisée avec
// backend/app/version.py et le tag git par scripts/bump-version.sh
// (lancé à chaque commit/push). Ne pas éditer à la main.
export const APP_VERSION = '1.10.1'

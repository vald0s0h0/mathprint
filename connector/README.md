# MathPrint Connector

Application Tauri minimale qui relie un compte MathPrint aux imprimantes déjà
configurées sur le Mac ou le PC. Elle démarre avec la session, reste dans la
zone de notification et ne conserve jamais le mot de passe. Le jeton de poste
est stocké dans le trousseau macOS ou le gestionnaire d’identifiants Windows.
L’instance `https://mathprint.fabrelexos.synology.me` est intégrée à
l’application : l’utilisateur ne saisit que son e-mail et son mot de passe.

Au lancement, le connecteur cherche, télécharge et installe silencieusement la
dernière mise à jour signée. Si une impression est active, il attend sa fin ;
en cas d’indisponibilité réseau, il continue à imprimer et réessaie au prochain
lancement.

## Contrat d’impression

Le serveur prépare un PDF final et immuable avant sa mise en file :

- séparation des pages recto/verso pour un duplex manuel ;
- ordre physique `1→2→3` ou `3→2→1` obtenu à partir des trois réglages
  `prélèvement`, `sortie imprimante` et, pour l’overlay, `ADF` ;
- prélèvement de la première ou de la dernière feuille suivant ce même calcul ;
- A4, échelle 100 % (`none`/`noscale`), assemblage activé ;
- simplex pour les deux passages manuels, duplex bord long pour l’automatique.

Le connecteur vérifie la taille et le SHA-256 du PDF, écrit un journal avant
l’envoi et évite de réimprimer silencieusement après un arrêt brutal. Une fois
le document transmis, CUPS sur macOS ou le spouleur Windows reprend son rôle
normal : papier, bourrage, consommables et annulation restent dans l’interface
du système.

Sous Windows, SumatraPDF 3.6.1 portable est embarqué comme processus séparé.
Son archive est vérifiée par SHA-256 pendant la CI et ses sources GPLv3 sont
jointes à la release.

## Développement

```bash
npm ci
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- build --no-bundle
```

L’URL d’un serveur réel doit être HTTPS. HTTP n’est accepté que pour
`localhost` pendant le développement.

## Publier une version

Les versions du connecteur sont indépendantes des images Docker. La CI ne se
déclenche que sur un tag `connector-vX.Y.Z`, après avoir vérifié que cette
version correspond à `package.json`, `Cargo.toml` et `tauri.conf.json`.

Le workflow produit :

- DMG Apple Silicon (`aarch64`) ;
- DMG Intel (`x86_64`), cible minimale macOS 10.13 ;
- installateur NSIS `.exe` Windows x64 ;
- archive des sources SumatraPDF exigée par sa licence.

Les installateurs OS restent provisoirement sans certificat Apple ou
Authenticode : Gatekeeper et SmartScreen peuvent donc afficher un avertissement
à la première installation. Les artefacts de mise à jour, eux, sont signés par
Tauri et vérifiés avec la clé publique embarquée. Le secret GitHub
`CONNECTOR_UPDATER_PRIVATE_KEY` contient la clé privée correspondante. Le
fichier local `.connector-updater.key` est ignoré par Git et doit rester
sauvegardé dans un coffre : sa perte empêcherait de mettre à jour les postes
déjà installés.

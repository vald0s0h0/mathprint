# MathPrint Connector

Application Tauri minimale qui relie un compte MathPrint aux imprimantes déjà
configurées sur le Mac ou le PC. Elle démarre avec la session, reste dans la
zone de notification et ne conserve jamais le mot de passe. Le jeton de poste
est stocké dans le trousseau macOS ou le gestionnaire d’identifiants Windows.

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

Secrets GitHub requis :

- `CONNECTOR_UPDATER_PRIVATE_KEY` : contenu de `.connector-updater.key` ;
- secrets Apple `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`,
  `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` pour
  signer et notariser les deux DMG ;
- `WINDOWS_CERTIFICATE` (PFX encodé en base64) et
  `WINDOWS_CERTIFICATE_PASSWORD` pour la signature Authenticode de l’EXE.

Le fichier privé `.connector-updater.key` est ignoré par Git et doit être
sauvegardé dans un coffre. Sa perte empêcherait de mettre à jour les postes
déjà installés. Le workflow produit :

- DMG Apple Silicon (`aarch64`) ;
- DMG Intel (`x86_64`), cible minimale macOS 10.13 ;
- installateur NSIS `.exe` Windows x64 ;
- artefacts signés et `latest.json` pour la mise à jour intégrée.

Le workflow refuse de publier si une clé de mise à jour, un certificat Apple ou
le certificat Authenticode manque. Authenticode évite l’éditeur inconnu de
SmartScreen ; cette signature reste distincte de la signature cryptographique
obligatoire des mises à jour Tauri.

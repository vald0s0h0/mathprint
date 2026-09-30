# ClassMap

Application Windows portable (10/11, x64) pour suivre les avertissements d'une classe.
Indépendante de MathPrint : aucun code, aucune API en commun.

## Installer sur la clé

1. Télécharger `ClassMap-win-x64.zip` (release `classmap-vX.Y.Z` ou artefact de l'action « ClassMap (Windows) »).
2. Dézipper le dossier `ClassMap` sur la clé USB et lancer `ClassMap.exe`.

Tout est enregistré dans `ClassMap\donnees\`, à côté du .exe : rien n'est écrit sur le PC.

| Fichier | Contenu |
|---|---|
| `classmap.json` | classes, élèves, placements dans la grille, scores, emploi du temps, réglages |
| `classmap.json.bak` | version précédente (écriture atomique : `.tmp` puis remplacement) |
| `sauvegardes/` | une copie par jour, 14 jours |
| `journal.csv` | journal des +1 / −1 / remises à 0 (s'ouvre dans Excel) |

## PC d'établissement verrouillés

- **Aucun droit administrateur** : manifeste `asInvoker`, pas de registre, rien dans `%TEMP%` ni `%APPDATA%`
  (exécutable NativeAOT : pas de runtime .NET à installer, pas d'extraction).
- **SmartScreen** au premier lancement d'un fichier téléchargé : « Informations complémentaires → Exécuter quand même ».
  Copié sur une clé FAT32/exFAT, le fichier perd la marque « téléchargé » et l'alerte disparaît.
- **Blocage des exécutables non signés depuis une clé** (règle ASR Defender, AppLocker) : seule une signature
  Authenticode ou une exception du service informatique lève ce blocage. L'action signe le .exe si les secrets
  `CLASSMAP_SIGN_PFX` (certificat .pfx en base64) et `CLASSMAP_SIGN_PASSWORD` sont configurés.
- **Lancement automatique à l'insertion** : impossible. Windows l'a désactivé pour les clés USB depuis 2011
  (seuls les CD l'autorisent) et le contourner exigerait d'installer quelque chose sur le PC.

## Éjecter

Le bouton ⏏ enregistre, ferme ClassMap puis éjecte la clé par l'API Windows (`CM_Request_Device_Eject`).
Un programme ne peut pas éjecter la clé d'où il s'exécute : l'appel est donc confié à PowerShell (fourni avec
Windows, lancé depuis System32), qui attend la fermeture de ClassMap, éjecte, et affiche le motif du refus le cas
échéant (fenêtre de l'Explorateur ouverte sur la clé, journal ouvert dans Excel…). Si PowerShell est bloqué,
ClassMap ouvre la fenêtre Windows « Retirer le périphérique en toute sécurité ».

## Développer

```sh
dotnet build ClassMap/ClassMap.csproj
dotnet run --project ClassMap -- --data /tmp/classmap-essai   # données ailleurs que dans bin/
ClassMap.exe --selftest                                        # vérifie le binaire publié (utilisé par la CI)
```

Publier une version : incrémenter `<Version>` dans `ClassMap.csproj`, puis pousser le tag `classmap-vX.Y.Z`.

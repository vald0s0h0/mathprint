# Instructions pour les agents (Codex)

## Commande « astra »

Quand l'utilisateur écrit `astra <chapitre>` (ex. `astra "Fonctions affines"`,
`astra B3`, `astra "Thalès" pages 116-118`), ou demande de lancer la pipeline
Astra sur un chapitre :

1. lis **entièrement** `agents/astra/ASTRA.md` puis `agents/astra/schema.md` ;
2. suis la procédure d'ASTRA.md de bout en bout, sans t'arrêter entre les étapes,
   jusqu'au bilan final.

Les scripts se lancent depuis la racine du dépôt avec `backend/.venv/bin/python`.
Ne modifie pas le code de l'application pendant un run Astra : ton travail est le
fichier `astra_output.json` du run.

# Instructions pour les agents (Codex et Claude Code)

## Dossier `entreprise/` : hors du code

`entreprise/` contient des documents commerciaux (arguments de vente, bilans PDF
de démonstration et leurs scripts de génération). Ce n'est pas du code de
l'application. Pour toute tâche de code (backend, frontend, scripts, CI,
déploiement, pipeline Astra) :

- ne lis pas, ne cherche pas et ne modifie pas `entreprise/` ; exclus-le de tes
  recherches (ex. `rg --glob '!entreprise/**'`) ;
- ne t'appuie pas sur son contenu (chiffres, noms, données de démonstration) :
  il ne décrit pas le comportement réel de l'application.

N'y travaille que si l'utilisateur le demande explicitement.

## Commande « astra »

Quand l'utilisateur écrit `astra <chapitre>` (ex. `astra "Fonctions affines"`,
`astra "3e B3"`, `astra "Thalès" pages 116-118`, `astra "6e Angles"`), ou demande
de lancer la pipeline Astra sur un chapitre (manuels Indigo 3e et 6e) :

1. lis **entièrement** `agents/astra/ASTRA.md` puis `agents/astra/schema.md` ;
2. suis la procédure d'ASTRA.md de bout en bout, sans t'arrêter entre les étapes,
   jusqu'au bilan final.

Les scripts se lancent depuis la racine du dépôt avec `backend/.venv/bin/python`.
Ne modifie pas le code de l'application pendant un run Astra : ton travail est le
fichier `astra_output.json` du run.

## Commande « forge »

Quand l'utilisateur écrit `forge <programme> <cycle> <cible>` (ex. `forge fr 3e A1.2`,
`forge fr 3e A1`, `forge fr 3e A1 --problemes`, `forge fr 3e A1.2 --complement 10`,
`forge fr 3e C1.2 --traces 4`, `forge reprendre`, `forge ecartes fr-3e-A4`), ou demande de
lancer la pipeline forge :

1. lis **entièrement** `agents/forge/FORGE.md` puis `agents/forge/schema.md` ;
2. suis la procédure de FORGE.md de bout en bout, jusqu'au bilan final.

Si le programme ou le cycle manque, demande-le avant toute chose. Ne modifie pas le
code de l'application pendant un run : ton travail est dans les fichiers du run.

Ce fichier est la seule source d'instructions du dépôt : ne crée pas de `CLAUDE.md`,
car Claude Code cesserait alors de lire `AGENTS.md`.

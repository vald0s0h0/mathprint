# Instructions pour les agents (Codex et Claude Code)

## Entreprise : hors de ce dépôt

Ce dépôt ne contient que le code de l'application. La gestion de l'entreprise
(commercial, prospects, site, finance, matériel, RH) vit dans `~/KiroNina`, un
dépôt séparé avec son propre AGENTS.md. Ne t'appuie pas sur son contenu pour
du code : les chiffres et données de démonstration n'y décrivent pas le
comportement réel de l'application. N'y travaille que si l'utilisateur le
demande, de préférence dans une session ouverte dans `~/KiroNina`.

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
`forge ci tle E1.4 --serie D`, `forge fr 3e A1 --problemes`, `forge fr 3e A1.2 --suite`, `forge fr 3e A1.2 --complement 10`,
`forge fr 3e C1.2 --traces 4`, `forge reprendre`, `forge ecartes fr-3e-A4`), ou demande de
lancer la pipeline forge :

1. lis **entièrement** `agents/forge/CONSIGNE-EXERCICES.md` ;
2. lance `prepare`, puis lis les contextes dans l'ordre qu'il affiche ;
3. suis la procédure de CONSIGNE-EXERCICES.md de bout en bout, jusqu'au bilan final.

Si le programme ou le cycle manque, demande-le avant toute chose. Ne modifie pas le
code de l'application pendant un run : ton travail est dans les fichiers du run.

## Commande « forge lecon »

Quand l'utilisateur écrit `forge lecon <programme> <niveau> <leçon…>` (ex.
`forge lecon ci 3e E2`, `forge lecon fr 3e C2`, `forge lecon fr 3e C2 --refaire`,
`forge lecon ci tle E1 --serie D --sources ~/Manuels/maths-tle.pdf --pages 40-61`),
ou demande d'écrire le contexte d'une leçon :

1. lis **entièrement** `agents/forge/CONSIGNE-LECON.md` ;
2. suis sa procédure jusqu'au bilan. Ce n'est pas un run forge : ne lis pas
   CONSIGNE-EXERCICES.md, et n'écris aucun exercice.

Seules les mathématiques sont ouvertes : n'écris de leçon dans une autre matière que si
l'utilisateur le demande explicitement.

Ce fichier est la seule source d'instructions du dépôt : ne crée pas de `CLAUDE.md`,
car Claude Code cesserait alors de lire `AGENTS.md`.

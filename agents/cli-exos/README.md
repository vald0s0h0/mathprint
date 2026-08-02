# cli-exos — extraction d'exercices d'un manuel, via le CLI Claude (abonnement)

Reprend des exercices d'un vrai manuel et les prépare pour l'app : **même pipeline
qu'« Indigo »** (OCR Mistral → découpage → crops/couleurs → mise au propre →
relecture), mais les **3 étapes LLM passent par le CLI Claude Code (abonnement
Anthropic), jamais l'API**. Le résultat arrive en **brouillons** dans l'onglet
**Exercices** de l'app, où tu peux **valider / modifier / supprimer / publier**.

Modèles : **Sonnet** (découpage), **Sonnet** (génération), **Opus** (vérification).

## Prérequis (une fois)

1. **CLI Claude connecté à ton abonnement** : `claude` doit être installé et
   authentifié (Pro/Max). Vérifie : `claude --version`. S'il n'est pas dans le
   PATH : `export CLAUDE_BIN=/chemin/vers/claude`.
   → Aucune clé d'API n'est utilisée : `ANTHROPIC_API_KEY` est **retirée** de
   l'environnement au moment d'appeler `claude` (si elle traîne, elle est ignorée).
2. **Clé Mistral** renseignée dans l'app (Paramètres → Fournisseurs) : elle sert à
   l'**OCR** des pages de manuel (Mistral n'est pas Anthropic).
3. **Manuels PDF** présents (élève + prof), là où l'app les attend
   (`settings.indigo_manuals`, par défaut `context/3_indigo.pdf` et
   `context/3_indigo_prof.pdf`).
4. L'**application a déjà démarré au moins une fois** (la base est créée/migrée à
   ce moment-là).

## Lancer (depuis la racine du repo)

Le script importe le backend MathPrint (OCR, géométrie, base) : lance-le avec le
**Python du venv backend** (`backend/.venv/bin/python`), pas le Python système.

```bash
backend/.venv/bin/python agents/cli-exos/run.py \
  --competency N1.2 \
  --eleve 34-40 \
  --prof 210-214 \
  --numbers 34-67
```

Plusieurs compétences en un seul run (forme répétable `--target` = une cible
complète) :

```bash
backend/.venv/bin/python agents/cli-exos/run.py \
  --target "N1.2:eleve=34-40:prof=210-214:numbers=34-67" \
  --target "N1.3:eleve=41-45:prof=214-217:numbers=68-92"
```

Pour retrouver les codes de compétence :
`backend/.venv/bin/python agents/cli-exos/run.py --list-competencies`

## Comment remplir les valeurs

| Option | Ce que c'est | Comment le lire |
|---|---|---|
| `--competency` | Code (ou short_id) de la compétence, ex. `N1.2`. | `--list-competencies`, ou l'onglet Exercices de l'app. |
| `--eleve` | Pages du **manuel ÉLÈVE** (les énoncés). | **Numéros de page 1-based**, tels qu'affichés dans le **visualiseur de manuel** de l'app (onglet Exercices). Plage inclusive : `34-40` = pages 34 à 40 ; une seule page : `40`. |
| `--prof` | Pages du **manuel PROF** (les corrigés). | Idem, dans le manuel professeur. Peut être omis (les corrigés seront alors rédigés par le modèle). |
| `--numbers` | **Plage des numéros d'exercices imprimés** dans le manuel. | Bornes **incluses**, ex. `34-67` = les exercices n°34 à n°67. C'est la **source de vérité** du découpage : on ne sort que ces numéros. |
| `--grade` | Niveau. | Défaut `3e`. |

> Astuce : les pages (`--eleve`/`--prof`) délimitent **où chercher** ; les numéros
> (`--numbers`) disent **quels exercices** prendre. Donne des pages qui **couvrent
> toute** la plage de numéros voulue (élève ET prof).

## Depuis le chat Claude Code

Tu peux aussi demander à l'agent, dans le chat, de lancer le run — par exemple :

> « Lance `agents/cli-exos` pour la compétence **N1.2**, pages élève **34-40**,
> pages prof **210-214**, numéros **34-67**. »

Il exécutera la commande `backend/.venv/bin/python agents/cli-exos/run.py …`
ci-dessus.

## Ensuite

Les exercices apparaissent en **brouillon** dans l'onglet **Exercices**
(badge de provenance « CLI »). Tu les **modifies / valides / supprimes**, puis
**Publier** les fige comme n'importe quel exercice du manuel (banque + sujets).

## Régler / éditer

- **Prompts** (éditables, propres à cette pipeline, indépendants d'Indigo) :
  - [`prompts/decoupage.txt`](prompts/decoupage.txt) — découpage par numéro (Sonnet)
  - [`prompts/generation.txt`](prompts/generation.txt) — mise au propre 1→1 (Sonnet)
  - [`prompts/verification.txt`](prompts/verification.txt) — relecture/correction (Opus)

  Pour `generation`/`verification`, le fichier contient les **instructions** ; le
  **schéma JSON strict** (menu des formats de réponse) est ajouté par le code
  (`exercise_gen.format_contract`) — n'édite que la prose, pas le schéma machine,
  sinon la sortie serait rejetée par le validateur.

- **Modèles / options** (variables d'environnement, facultatives) :

  | Variable | Défaut | Rôle |
  |---|---|---|
  | `CLI_EXOS_MODEL_SEGMENT` | `sonnet` | modèle du découpage |
  | `CLI_EXOS_MODEL_GENERATE` | `sonnet` | modèle de la génération |
  | `CLI_EXOS_MODEL_REVIEW` | `opus` | modèle de la vérification |
  | `CLI_EXOS_REVIEW` | `1` | `0` pour sauter la relecture Opus |
  | `CLI_EXOS_TIMEOUT_S` | `900` | délai max par appel CLI |
  | `CLAUDE_BIN` | `claude` | chemin du binaire si hors PATH |

## Coût & vitesse (important)

`claude -p` est un **agent**, pas une simple complétion d'API : par défaut il
précharge à CHAQUE appel son prompt système d'agent, les schémas de tous les
outils, les serveurs MCP, `CLAUDE.md`/mémoire et les settings (~**21 000 tokens**
de contexte mesurés, pour une tâche qui n'en a aucun besoin), et il faut relancer
un **processus** par appel. C'est pourquoi la barre de quota descend vite et que
c'est plus lent qu'un appel d'API direct (qui, lui, met le contrat en cache et n'a
aucun surcoût d'agent). Ce n'est **pas** une boucle inutile dans la pipeline.

La pipeline invoque déjà `claude` en **mode allégé** (`--system-prompt`, `--tools ""`,
`--strict-mcp-config`, `--setting-sources ""`, `--no-session-persistence`) :
overhead ramené de ~21 000 à ~4 600 tokens/appel (**−78 %**), et notre contrat
(identique à chaque appel) est mis en cache côté serveur.

Leviers restants, à ta main :
- **Relecture Opus = le plus gros poste** (Opus ≈ 5× le coût de Sonnet). Passe-la
  en Sonnet : `CLI_EXOS_MODEL_REVIEW=sonnet`, ou coupe-la : `CLI_EXOS_REVIEW=0`.
- Traite des **plages plus petites** par run (moins d'appels).
- L'API directe (pipeline **Indigo**) reste plus rapide/économe pour du volume :
  c'est le compromis assumé de « abonnement, pas d'API ».

## Notes

- Aucun coût d'API : les appels passent par l'abonnement. Le coût rapporté par le
  CLI est **journalisé** (console) mais **pas** compté dans la page Coûts de l'app
  (qui ne suit que la facturation d'API).
- Un run CLI est tracé comme une « extraction » à statut `cli_*`, volontairement
  distinct de `pending`/`running` pour que le worker Indigo de l'app ne le
  reprenne jamais.

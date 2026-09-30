# `agents/` — pipelines d'agents de code (abonnement)

Ce dossier regroupe des **pipelines de création pilotées par un agent de code**,
lancées depuis le terminal ou depuis le chat de l'éditeur. Elles passent par un
**abonnement, jamais par une API payante** :

- les pipelines Claude appellent le binaire `claude` (la variable
  `ANTHROPIC_API_KEY` est retirée de l'environnement, ce qui force
  l'authentification par abonnement) ;
- Astra EST la session Codex (`gpt-6-astra`) : aucun appel de modèle depuis le code.

Chaque pipeline vit dans son propre sous-dossier, autonome (orchestrateur +
prompts + doc). Elles restent **séparées du backend** mais **versionnées avec le
repo** ; d'autres agents du même type viendront s'ajouter ici.

## Pipelines

| Dossier | Rôle | Modèles |
|---|---|---|
| [`astra/`](astra/) | Crée les exercices d'un CHAPITRE entier du manuel. L'agent Codex `gpt-6-astra` lit les pages (leçon + exercices) et écrit, pour chaque exercice, les versions Base + Facile : réponses à cocher ou relier, guides intégrés à l'énoncé, figures redessinées (`geo`/`chart`) ou découpées. Les scripts valident, prévisualisent et écrivent les brouillons `IndigoExercise`. Commande dans le chat Codex : `astra "<chapitre>"`. | GPT-6 Astra (Codex, abonnement) |
| [`cli-exos/`](cli-exos/) | Reprend des exercices d'un manuel réel (même pipeline qu'« Indigo » côté app) mais fait passer les 3 étapes LLM par le CLI Claude. Écrit des brouillons `IndigoExercise` → onglet **Exercices** de l'app (valider / modifier / supprimer / publier). | Sonnet (découpage), Sonnet (génération), Opus (vérification) |

## Principes communs

- **Abonnement, pas d'API.** Les appels passent par `claude -p` (ou la session
  Codex elle-même pour Astra) ; aucune clé Anthropic ni OpenAI n'est lue. (La clé **Mistral** de l'app reste utilisée pour l'OCR des
  pages de manuel — Mistral n'est pas Anthropic.)
- **Prompts = fichiers texte** dans chaque pipeline (`prompts/*.txt`), **uniques**
  à chaque pipeline et **éditables** directement (pas d'UI). On peut donc régler
  les prompts d'une pipeline sans toucher à ceux d'une autre ni à ceux d'Indigo.
- **Réutilise l'app.** Les orchestrateurs importent le backend MathPrint
  (`backend/app`) pour l'OCR, le découpage géométrique, les crops, la
  persistance et la publication — rien n'est ré-implémenté là où l'app fait déjà
  bien.

Voir le README de chaque pipeline pour la commande exacte.

# `agents/` — pipelines d'agents Claude Code (abonnement)

Ce dossier regroupe des **pipelines de création pilotées par le CLI Claude Code**,
lancées depuis le terminal (ou depuis le chat Claude Code). Elles utilisent
**l'abonnement Anthropic via le binaire `claude`** — **jamais l'API Anthropic**
(la variable `ANTHROPIC_API_KEY` est explicitement retirée de l'environnement au
moment d'appeler `claude`, ce qui force l'authentification par abonnement).

Chaque pipeline vit dans son propre sous-dossier, autonome (orchestrateur +
prompts + doc). Elles restent **séparées du backend** mais **versionnées avec le
repo** ; d'autres agents du même type viendront s'ajouter ici.

## Pipelines

| Dossier | Rôle | Modèles |
|---|---|---|
| [`cli-exos/`](cli-exos/) | Reprend des exercices d'un manuel réel (même pipeline qu'« Indigo » côté app) mais fait passer les 3 étapes LLM par le CLI Claude. Écrit des brouillons `IndigoExercise` → onglet **Exercices** de l'app (valider / modifier / supprimer / publier). | Sonnet (découpage), Sonnet (génération), Opus (vérification) |

## Principes communs

- **Abonnement, pas d'API.** Les appels passent par `claude -p` ; aucune clé
  Anthropic n'est lue. (La clé **Mistral** de l'app reste utilisée pour l'OCR des
  pages de manuel — Mistral n'est pas Anthropic.)
- **Prompts = fichiers texte** dans chaque pipeline (`prompts/*.txt`), **uniques**
  à chaque pipeline et **éditables** directement (pas d'UI). On peut donc régler
  les prompts d'une pipeline sans toucher à ceux d'une autre ni à ceux d'Indigo.
- **Réutilise l'app.** Les orchestrateurs importent le backend MathPrint
  (`backend/app`) pour l'OCR, le découpage géométrique, les crops, la
  persistance et la publication — rien n'est ré-implémenté là où l'app fait déjà
  bien.

Voir le README de chaque pipeline pour la commande exacte.

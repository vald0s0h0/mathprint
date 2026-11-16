# La commande `vague`

Décision du 2026-11-14 (plan forge2 §10) : aucune API de LLM ne corrige plus les copies.
Les réponses écrites ambiguës et la phrase de synthèse de l'appréciation sont rédigées
par une commande lancée à la main depuis le Mac, sur abonnement. Mathpix (lecture de
l'écriture) ne change pas.

## Ce que voit le professeur

Quand un lot arrive à l'étape LLM, les réponses à trancher restent **« Correction en
attente »** : revue ouverte (motif `llm_queued`), aucune note finale, finalisation
bloquée. La page Corrections l'indique sur la ligne du lot. Le professeur peut les noter
lui-même ou attendre la vague. Dès qu'il ne reste rien à trancher, la vague finalise le
lot (résultats, journal d'erreurs, copies corrigées), comme le pipeline.

## Lancer

Depuis la racine du dépôt, sur le Mac :

```
backend/.venv/bin/python backend/scripts/vague.py [corriger] [apprecier] [tout]
    [--moteur haiku|luna|terra] [--devoir ID] [--classe ID] [--essai]
    [--nas [--hote UTILISATEUR@HOTE]]
```

- `corriger` : les réponses en attente, **par exercice** (énoncé et dossier du
  correcteur une fois, puis les réponses pseudonymisées, par paquets de
  `correction_batch_size`). Le moteur note et rédige la micro-correction imprimée
  (tutoiement, sans donner la réponse, dans le budget de lignes de la carte). Un message
  refusé (vide, trop long, sans tutoiement, qui cite une position ou la réponse) laisse
  imprimer le message prévu par la carte. Sous le seuil de confiance (Paramètres >
  Pédagogie), la réponse reste au professeur.
  Quand aucun message prévu ne décrit l'erreur, le moteur choisit un `code_erreur` dans
  les erreurs types des domaines de l'exercice : il entre au suivi de l'élève (un code
  hors de la liste est ignoré). `corriger` relève ensuite le **diagnostic** des réponses
  écrites fausses que l'exercice n'avait pas prévues : le moteur ne les note pas, il
  nomme seulement leur erreur type (`services/diagnostic.py`). En mode api, le pipeline
  le fait aussitôt.
- `apprecier` : la synthèse des copies finalisées qui l'attendent (seulement si la
  synthèse est activée dans Paramètres > Pédagogie). L'overlay est ensuite marqué « à
  régénérer ».
- `tout` (défaut) : les deux, dans cet ordre.
- `--essai` : liste les paquets sans appeler le moteur ni rien écrire. Commencer par là.

Chaque paquet est écrit dès sa réponse validée : une vague interrompue (Ctrl-C, fenêtre
d'usage épuisée, qui l'arrête proprement) se relance telle quelle, sans refaire un
paquet. Bilan final : paquets traités, réponses renvoyées au professeur, paquets en échec
(restés en attente).

## Moteurs et réglages (`backend/app/config.py`, variables `MATHPRINT_…`)

| Moteur | CLI | Modèle |
|---|---|---|
| `haiku` (défaut) | `claude -p` (ou `CLAUDE_BIN`) | `VAGUE_MODEL_HAIKU` = `haiku` |
| `terra` | `codex exec` (ou `CODEX_BIN`) | `VAGUE_MODEL_TERRA` = `gpt-6.1-terra` |
| `luna` | `codex exec` | `VAGUE_MODEL_LUNA` : **à renseigner** |

Les appels se font sans outil, depuis un dossier vide, clés d'API retirées de
l'environnement. `MATHPRINT_CORRECTION_MODE=api` rétablit l'ancien chemin DeepSeek /
Claude par API (désactivé par défaut).

## Relever la file du NAS

Le NAS n'a pas de moteur : ses réponses restent « en attente » jusqu'à ce que le Mac les
relève.

```
backend/.venv/bin/python backend/scripts/vague.py tout --nas --essai   # d'abord : liste seulement
backend/.venv/bin/python backend/scripts/vague.py tout --nas
```

`--nas` marche avec `tout`, `corriger` et `apprecier` ; `--hote` change l'hôte (défaut
`Etienne@fabre.tail428d1a.ts.net`) ; `--devoir`, `--classe` et `--moteur` s'appliquent.

Déroulement, en trois temps : (1) le Mac **exporte** par SSH les paquets du NAS (prompt
composé, réponses pseudonymisées : aucun nom d'élève) ; (2) le moteur local répond, paquet
par paquet ; (3) le Mac **importe** par SSH, par lots de 5 paquets, donc une coupure ne
perd que le lot en cours. Le NAS revalide tout (champs retrouvés en base et inchangés,
barème, longueur et forme des messages, seuil de confiance) et n'écrit que sur des
réponses encore « en attente » : une réponse notée entre-temps par le professeur n'est
jamais écrasée. Une vague coupée se relance telle quelle. Si le NAS est injoignable, le
message l'indique et la commande sort en erreur.

Aucun port, secret ni route nouveaux : le Mac exécute `docker exec -i mathprint-api-1
python -` sur le NAS, le script (`app.services.vague_distante`) et les données passant par
l'entrée standard, jamais dans une ligne de commande.

Prérequis :

- le NAS tourne une version qui contient `app/services/vague_distante.py` (sinon l'export
  répond « version sans vague_distante ») ;
- la clé SSH du Mac est acceptée par `Etienne@fabre.tail428d1a.ts.net` (Tailscale actif) ;
- la règle sudo du NAS autorise `sudo -n /usr/local/bin/docker` sans mot de passe.

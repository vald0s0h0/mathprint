# Lot 0 : vérification et banc des moteurs OCR

Date : 2026-11-11. Mac M3, 16 Go. `transformers` 5.22.2, `torch` 2.15.0, MPS partout (aucune conversion MLX tentée).

## 1. Poids vérifiés (API Hugging Face)

| Moteur | Dépôt | Licence | Poids | Base | Classe `transformers` |
|---|---|---|---|---|---|
| `trocr` | `agomberto/trocr-large-handwritten-fr` (processeur : `microsoft/trocr-large-handwritten`) | MIT | 2,2 Go (`pytorch_model.bin`) | TrOCR large manuscrit anglais, affiné FR (archives) | `VisionEncoderDecoderModel` |
| `unimumer` | `phxember/Uni-MuMER-Qwen3.5-2B` | Apache 2.0 | 4,4 Go | `Qwen/Qwen3.5-2B` | `Qwen3_5ForConditionalGeneration` |
| `lighton2` | `lightonai/LightOnOCR-2-1B` | Apache 2.0 | 2,0 Go | vision Pixtral + LM Qwen3 | `LightOnOcrForConditionalGeneration` |
| `lighton3` | `lightonai/LightOnOCR-3-4B` | Apache 2.0 | 9,1 Go | Qwen3.5 | `Qwen3_5ForConditionalGeneration` |
| `apple_vision` | framework système | n/a | 0 | n/a | `pyobjc-framework-Vision` |

Notes d'intégration (à reprendre au lot 3) :
- Qwen3.5 demande `transformers` >= 5.2 ; la fiche de LightOnOCR-3 indique >= 5.5.4. Version installée : 5.22.2.
- **N'utilise pas `AutoModelForImageTextToText` pour LightOnOCR-2** : la config dit `mistral3` et la mauvaise classe produit du bruit (« R » répétés), même sur du texte imprimé. Classe explicite obligatoire.
- TrOCR : fp16 stable sur MPS ; `.bin` chargé avec torch >= 2.6 (`weights_only` par défaut). Les autres : bf16 sur MPS sans souci.
- Uni-MuMER : prompt « I have an image of a handwritten mathematical expression. Please write out the expression of the formula in the image using LaTeX format. », `enable_thinking=False`.
- LightOnOCR : invite vide (image seule), `enable_thinking=False` pour la version 3.
- `HF_HUB_OFFLINE=1` en exécution : un appel réseau au Hub a bloqué un chargement pendant un téléchargement en parallèle.
- Sans `flash-linear-attention` ni `causal_conv1d`, Qwen3.5 retombe sur l'implémentation PyTorch de référence (plus lent, correct). À réévaluer si Uni-MuMER est trop lent.

## 2. Le banc : indicatif seulement

**Les exactitudes ci-dessous sont indicatives.** Le jeu fait 142 découpes :
- 13 réelles avec vérité `teacher_ocr` (9 `num_line`, 4 `math_line`). La base ne contient que 18 lignes `teacher_ocr`, dont 9 pour 3 zones corrigées plusieurs fois et le reste pour des QCM sans texte. Ce sont les seules écritures réelles disponibles ;
- 129 synthétiques : polices manuscrites (Bradley Hand, Marker Felt, Chalkboard, Noteworthy), encre bleue ou noire, papier bruité, rotation, flou, avec les pièges du cahier des charges (`1,79`, `3/6`, `6,0`, `0,50`, `œuf`, `Vrai`/`vrai`, `x²`, `−12`...).

Une police n'est pas une écriture d'élève : ces chiffres servent à écarter les moteurs défaillants, pas à fixer des seuils. Le banc doit être refait sur de vraies copies dès qu'il y en aura une centaine corrigées (`ocr_replay.py`, lot 9).

Comparaison : égalité des surfaces après une normalisation jetable (`ocr_worker/scripts/score_lot0.py`, qui n'est pas `ocr_surface.py`). Décodage glouton, 64 tokens max, sans correction linguistique.

### Exactitude (lectures exactes / découpes)

| Moteur | num_line (74) | math_line (28) | text_line (24) | multiline_line (16) | réel (13) |
|---|---|---|---|---|---|
| `apple_vision` | 65 | 12 | 19 | 7 | 3 |
| `lighton2` | **70** | 22 | **23** | **13** | 10 |
| `lighton3` | 63 | 16 | **23** | 8 | 1 |
| `trocr` | 50 | 2 | 9 | 3 | 7 |
| `unimumer` | **71** | **24** | 0 | 1 | **12** |

### Coût d'exécution

| Moteur | Latence p50 / p95 | Mémoire MPS (pic) | RSS | Chargement |
|---|---|---|---|---|
| `apple_vision` | 17 / 41 ms | 0 | 0,3 Go | 0 |
| `trocr` (fp16) | 463 / 775 ms | 1,7 Go | 2,7 Go | 3 s |
| `lighton2` | 320 / 589 ms | 2,2 Go | 0,7 Go | 3 s |
| `unimumer` | 1 096 / 2 934 ms | 5,1 Go | 0,8 Go | 8 s |
| `lighton3` | 1 902 / **16 726** ms | **9,6 Go** | 0,8 Go | 52 s |

Seuils fixés pour tenter MLX : > 3 s par découpe ou > 6 Go. Seul `lighton3` les dépasse ; comme il est éliminé (voir §4), la conversion n'a pas été tentée. `unimumer` reste juste sous les seuils (p95 2,9 s).

### Fautes graves observées

| Observation | Moteurs |
|---|---|
| **Virgule décimale perdue** (`3,5` lu `35`) avec une confiance très haute (log-prob moyenne ~ −0,001) | `trocr` : 15/31 |
| Sortie « méta » (`<figure>` qui décrit l'image au lieu de la lire, blocs ``` , `\boxed`, `\square`, `□`) | `lighton3` 30/142, `lighton2` 10/142, `unimumer` 5/142 |
| `×` lu comme `□`, `\square`, `\boxed`, `\bullet` | `lighton2`, `unimumer` |
| Texte épelé lettre par lettre (`l e t r i a n g l e`), accents perdus, `œ` lu `a e` | `unimumer` : 0/24 en texte |
| Accents ou `œ` perdus ; majuscule ajoutée malgré `usesLanguageCorrection=False` (`le` devient `Le`, `Éric` devient `Eric`) | `apple_vision` 11/14, `trocr` 7/14 |
| Chiffre isolé d'une case non lu (chaîne vide) | `apple_vision` : 5 cases réelles sur 5 |
| Lecture fausse mais stable (`24` lu `29`) | `trocr`, `lighton2`, `unimumer` (sur la découpe réelle) |
| Aucune hallucination de texte long sur `trocr`, `apple_vision` | n/a |

La confiance n'est pas fiable seule : sur `trocr`, les erreurs de virgule ont une log-probabilité parmi les meilleures. Le consensus et le garde-fou doivent porter la sécurité, pas les seuils de confiance.

### Accord et corrélation des erreurs

Précision de l'accord = (accord et lecture juste) / (accord, surface non vide).

| Paire | Accord (sur 142) | Précision de l'accord | Accords à tort |
|---|---|---|---|
| `apple_vision` × `lighton2` | 100 | 100/100 | 0 |
| `apple_vision` × `unimumer` | 76 | 76/76 | 0 |
| `apple_vision` × `lighton3` | 96 | 95/96 | 1 |
| `lighton3` × `unimumer` | 78 | 78/78 | 0 |
| `lighton2` × `lighton3` | 108 | 107/108 | 1 |
| `lighton2` × `unimumer` (**paire corrélée** `pixtral_qwen3` / `qwen35`) | 96 | 93/96 | **3** |
| `lighton2` × `trocr` | 63 | 62/63 | 1 |
| `trocr` × `unimumer` | 52 | 51/52 | 1 |
| `apple_vision` × `trocr` | 59 | 58/59 | 1 |

La paire `lighton2` × `unimumer` fait 3 accords à tort sur 96 (3 %), contre 0 pour les paires avec Apple Vision. Cela confirme la règle `correlated_pairs` : leur accord seul ne suffit pas. Échantillon trop petit pour chiffrer au-delà.

### Garde-fou encre/longueur

Rapport (largeur d'encre / hauteur de découpe) par caractère de la vérité :

| Classe | min | p5 | médiane | p95 | max |
|---|---|---|---|---|---|
| `text_line` | 0,16 | 0,18 | 0,21 | 0,28 | 0,29 |
| `multiline_line` | 0,20 | 0,20 | 0,25 | 0,31 | 0,36 |
| `num_line`, `math_line` (synthétique seul) | 0,19 | 0,22 | 0,27 | 0,5 env. | n/a |

Bornes **provisoires** à utiliser au lot 4 : 0,12 à 0,45 par caractère en synthétique. Sur les découpes réelles, le bord et la texture du papier faussent la mesure (p95 > 1,6 dans mon calcul naïf) : il faut une vraie segmentation de l'encre (seuil adaptatif, retrait des bords) et des polices plus variées avant de figer les bornes. À recalibrer sur de vraies copies.

## 3. Familles et plan

Familles déclarées dans `ocr_engine_plan` : `encdec` (TrOCR), `apple`, `qwen35` (Uni-MuMER, LightOnOCR-3), `pixtral_qwen3` (LightOnOCR-2), avec `correlated_pairs: [["qwen35","pixtral_qwen3"]]`. Règle : une paire corrélée compte pour deux voix seulement si aucun moteur d'une autre famille ne lit une autre surface ; sinon c'est un conflit, qui part chez Mathpix.

## 4. Recommandation

1. **Supprimer `lighton3`** (9,1 Go sur disque). Il est plus lent, 4 fois plus gourmand en mémoire, décrit les images (`<figure>`) au lieu de les lire (30/142), 1/13 sur le réel contre 10/13 pour `lighton2`. Pas de conversion MLX nécessaire.
2. **Garder `lighton2`** : meilleur compromis (320 ms, 2,2 Go, meilleur sur texte et multiligne, 70/74 en nombres).
3. **Garder `unimumer`**, mais **seulement pour `num_line` et `math_line`** : le meilleur sur les nombres et les expressions (12/13 sur le réel), inutilisable en texte (0/24). Le retirer des plans `text_line` et `multiline_line`.
4. **Apple Vision : voix gratuite** (17 ms, 0 Go). Les accords avec Apple Vision n'ont donné aucune erreur (0 accord à tort sur 3 paires). Il ne lit pas les chiffres isolés (chaîne vide, qui compte pour « pas de lecture », jamais pour un accord) et capitalise parfois (`le` devient `Le`), donc il ne doit pas décider seul ni en texte.
5. **`trocr` : à déclasser** ou à retirer de `num_line` en principal. Il perd la virgule une fois sur deux avec une confiance maximale, ce qui est exactement l'erreur que le barème (`1,79` ≠ `1,8`) punit. Il reste la seule famille indépendante (`encdec`) : s'il est gardé, c'est en renfort avec `ocr_engine_thresholds` et un garde-fou « virgule » (rejet si les chiffres sont ceux d'un autre moteur avec une virgule en plus).
6. **Plan proposé** (à valider) :

| Classe | Étape 1 | Étape 2 (renfort) |
|---|---|---|
| `num_line` | `apple_vision` + `unimumer` | `lighton2`, `trocr` |
| `math_line` | `unimumer` + `lighton2` (paire corrélée, donc règle spéciale) | `apple_vision` |
| `text_line` | `lighton2` + `apple_vision` | `trocr` |
| `multiline_line` | `lighton2` + `apple_vision` | `trocr` |

7. **Nettoyage de surface au lot 4** : les sorties `\square`, `\boxed`, `\bullet`, `□`, `<figure>`, ``` doivent être traitées comme « lecture douteuse » (jamais réécrites en `×`), et le LaTeX espacé d'Uni-MuMER (`2 ^ { 2 } \times 3`) compacté avant comparaison.

## 5. Ce qui reste à faire ou à vérifier

- Banc sur de vraies copies : seulement 13 écritures réelles disponibles, aucune d'écriture de texte ou multiligne.
- Pas de test des scores forcés (`score`) : prévu au lot 3.
- Pas de test CUDA ni MLX (non demandés).
- Pas d'accès NAS (consigne).

## 6. Fichiers

- `ocr_worker/requirements.txt`, `ocr_worker/.venv` (non versionné)
- `ocr_worker/scripts/download_models.py`, `make_bench.py`, `bench_lot0.py`, `score_lot0.py`
- Données du banc : `data/ocr_bench/` (ignoré par git : contient des découpes d'élèves)

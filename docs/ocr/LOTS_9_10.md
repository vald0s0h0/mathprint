> **État au 2026-11-12** : le lot 9 est fait (voir `JOURNAL.md`), avec l'audit en mode ombre à 5 %. Du lot 10, sont faits le profil CUDA (non testé sur CUDA) et l'outillage d'affinage de TrOCR (boucle vérifiée sur du synthétique) ; l'arbitre LLM reste volontairement à instruire. Le texte ci-dessous est la recommandation d'origine.

# Recommandations pour les lots 9 et 10

Rédigées le 2026-11-11, après les lots 0 à 8. Les chiffres cités viennent du banc du lot 0 (142 découpes, dont 13 réelles) et du rejeu de la fusion : ils sont indicatifs.

## Avant tout : trois faits qui orientent la suite

1. **Rien n'a tourné sur une vraie copie.** Le rejeu donne 105 lectures acceptées en local, 0 fausse, 33 envoyées à Mathpix, 4 en manuel. C'est encourageant, pas une preuve : écritures en polices, 13 écritures réelles.
2. **La vérité terrain est biaisée par construction.** Elle ne vient que de ce que le professeur relit. En mode `mathpix` et `shadow`, il ne relit que les lectures Mathpix de faible confiance : les cas faciles ne sont jamais vérifiés, donc le taux d'erreur automatique serait mal estimé. Seul l'audit (`ocr_audit_share`) corrige ce biais, et il n'existe qu'en `cascade`.
3. **Le seul passage « de confiance » est l'audit.** Pour affirmer « moins de 0,5 % d'erreurs automatiques » (borne haute de Wilson), il faut environ 600 décisions auditées sans aucune erreur (règle de trois : 3 / n).

## Lot 9 : le diagnostic. À faire en premier, avant d'activer `shadow` sur du volume

### Ce que je recommande d'ajouter à la spécification

- **Un audit aussi en mode `shadow`** (`ocr_shadow_audit_share`, 3 à 5 %) : envoyer au hasard des zones **acceptées par Mathpix** dans OCRiser avec l'étiquette « Contrôle ». Sans cela, la phase `shadow` ne produit pas de vérité sur les cas faciles et l'on ne saura pas quand basculer. C'est la modification la plus importante.
- **Un panneau « go / no-go » par classe de zone**, en tête du rapport : nombre de décisions auditées, erreurs automatiques, borne haute de Wilson, part envoyée à Mathpix, part en manuel. Critère proposé pour passer un type de zone en `cascade` : borne haute < 0,5 % avec au moins 600 audits. Puis baisser l'audit à 2 %.
- **Pendant les premières semaines en `cascade`, monter `ocr_audit_share` à 10 %.** Avec ~15 zones écrites par copie, 10 000 copies donnent ~150 000 décisions : même à 2 %, c'est 3 000 audits, assez pour une borne à 0,1 %.
- **Les raisons de renvoi vers Mathpix et vers le manuel, comptées** (`no_majority`, `conflicting_majorities`, `ink_guard`, `score_veto:<moteur>`, `mathpix_not_read_locally`, `illegible_consensus`...). Elles sont déjà dans `ocr_decisions.reason` et disent quel réglage ajuster.
- **Le taux d'accord à tort par paire de familles** (deux moteurs d'accord sur une surface fausse) : c'est la mesure qui justifie ou non la règle des paires corrélées.

### Réalisation

- `ocr_report.py` : requêtes SQL agrégées (`GROUP BY`) uniquement, lecture seule, intervalle de Wilson calculé en Python sur les comptes. À lancer sur une copie ou hors des heures de correction sur le NAS (le swap). Ajouter les index `ocr_decisions(batch_id)` et `ocr_decisions(decision, mode)` avant que la table grossisse.
- `ocr_replay.py` : recalculer `surface` depuis `raw_text` (elle peut changer d'une version à l'autre de `ocr_surface`), rejouer `ocr_fusion` avec d'autres seuils ou un autre plan, et écrire un rapport avant/après. L'option `--engine X` crée des tâches : la rendre **sans effet par défaut** (`--dry-run`), avec `--yes` obligatoire, surtout sur le NAS.
- `ocr_export_dataset.py` : voir les précautions du lot 10.
- Un script de calibrage du garde-fou encre/longueur et des seuils par moteur, dans le style de `calibrate_*.py`, sur les vérités accumulées.

## Lot 10

### Affinage de TrOCR : le plus rentable, mais pas avant les données

- **Pourquoi** : c'est le moteur le plus rapide et le seul d'une famille indépendante (`encdec`), mais il perd la virgule décimale (15 cas sur 31 au lot 0). Un TrOCR qui ne la perd plus devient un vrai voix de plus et réduit les renvois à Mathpix.
- **Règles** : n'entraîner que sur la vérité du professeur (`truth_source` = `teacher_ocr` ou `audit`), **jamais sur les sorties des autres moteurs** (sinon ses erreurs deviennent corrélées aux leurs et le consensus perd son sens). Séparer apprentissage et évaluation **par élève**, pas par découpe (sinon fuite d'écriture). Surpondérer les virgules, fractions, exposants. Promouvoir le modèle seulement si, au rejeu, le taux d'accord à tort avec les autres familles ne monte pas.
- **Données personnelles** : des écritures de mineurs et leurs transcriptions. Export local seulement, sans nom d'élève, jamais vers un service externe (ni Hugging Face), poids affinés gardés en local. À cadrer avec votre politique RGPD avant d'exporter quoi que ce soit.
- **Matériel** : TrOCR large (334 M de paramètres) s'affine en LoRA ou en fp16 sur la RTX 3070 de 6 Go.

### Profil CUDA de la RTX 3070 : utile pour le débit, pas pour la qualité

- La structure existe (`device`, `backend`, `family` enregistrés pour chaque lecture ; baux qui autorisent plusieurs workers en parallèle). Il manque un venv avec `torch` CUDA et un second bloc de configuration.
- À prévoir : 6 Go de mémoire (Uni-MuMER pèse 4,4 Go en poids : un seul modèle à la fois, comme sur le Mac) ; un **test de parité** sur les mêmes découpes (les écarts numériques CUDA/MPS peuvent faire basculer une lecture proche d'un seuil) ; le champ `device` du rapport pour comparer les deux machines.
- **À déclencher par une mesure** : délai de décision p95 et durée d'occupation du Mac. Tant que le Mac suffit, ne pas le faire.

### Arbitre LLM local : le plus risqué, à reporter

- La spécification mêle deux idées : un arbitre de lecture et le remplacement de DeepSeek pour la correction `ocr_llm`. À séparer.
- **Arbitre de lecture** : un LLM qui *génère* une lecture viole la règle « ne jamais inventer ». S'il y en a un, il ne doit que **choisir parmi des candidats déjà lus** (log-probabilité sur l'identifiant du candidat). C'est exactement ce que fait déjà le score forcé : élargir d'abord `ocr_engine_plan.scorers` avec un VLM plus gros utilisé **en évaluateur seulement**, et mesurer au rejeu. Un arbitre n'est justifié que s'il fait baisser les renvois vers Mathpix sans aucune erreur automatique supplémentaire.
- **Remplacer DeepSeek** : décider sur des chiffres. `api_usage_events` donne la dépense réelle par fournisseur ; les corrections du professeur sur les décisions du LLM donnent sa fiabilité. À faire d'abord en mode ombre, avec le même mécanisme (décision locale enregistrée, comparée à DeepSeek et au professeur).

## Points ouverts que le lot 9 doit rendre visibles

- **Moteur absent ou en panne durable** : un lot en `cascade` attend indéfiniment (seul le bail relâche une tâche). Je recommande une alerte visible après N heures et un réglage « repasser ce lot en Mathpix ».
- **`line_bands`** (découpe des lignes d'une rédaction) n'a jamais vu un vrai scan : à valider sur une vraie copie avant de passer les rédactions en `cascade`.
- **Apple Vision** capitalise malgré l'absence de correction linguistique (`le` devient `Le`) : en texte, il produit des conflits (renvois), pas des erreurs ; à mesurer.
- **Tokenizer de LightOnOCR-2** : avertissement `fix_mistral_regex`. Les lectures sont correctes ; à vérifier avant que ses scores forcés décident quoi que ce soit.
- **Mode ombre et rédactions** : Mathpix lit la zone entière, le local lit ligne par ligne : la comparaison se fait sur le texte joint, pas par ligne.

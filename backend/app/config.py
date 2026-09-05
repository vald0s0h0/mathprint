"""Configuration centrale de MathPrint (NAS).

Tout est surchargeable par variable d'environnement (préfixe MATHPRINT_).
Aucun identifiant de modèle LLM n'est codé en dur ailleurs que dans ce
registre par défaut, modifiable en base via system_settings / provider_configs.

SECRET_KEY/HMAC_KEY n'ont pas besoin d'être fournis : au premier démarrage
sur des valeurs par défaut, `services.bootstrap.ensure_strong_secrets()` en
génère de vrais et les persiste dans `_RUNTIME_ENV_FILE` (sur le volume
`/data`, donc stable d'un redémarrage/mise à jour à l'autre) — rechargé ici
via `env_file` à chaque démarrage du processus.
"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Dossier du package `app` (…/backend/app en dev, /app/app dans l'image Docker).
# Les ressources LIVRÉES AVEC LE CODE (manuels Sésamath, etc.) doivent être
# référencées relativement à CE dossier — jamais à _REPO_ROOT, qui vaut « / »
# dans le conteneur (l'image ne copie que `app/`, cf. backend/Dockerfile).
_APP_DIR = Path(__file__).resolve().parent
_DATA_DIR = Path(os.environ.get(
    "MATHPRINT_DATA_DIR", str(_REPO_ROOT / "data")))
_RUNTIME_ENV_FILE = _DATA_DIR / "runtime_secrets.env"


class Settings(BaseSettings):
    model_config = {"env_prefix": "MATHPRINT_", "env_file": str(_RUNTIME_ENV_FILE),
                    "env_file_encoding": "utf-8"}

    # --- Base ---
    database_url: str = "sqlite:///./mathprint.db"
    data_dir: Path = _DATA_DIR
    secret_key: str = "change-me-on-nas"          # JWT — voir bootstrap.py
    hmac_key: str = "change-me-hmac-key"          # signature des QR pages — idem
    session_hours: int = 12

    # --- Registre de modèles par défaut (RM-011 : jamais codé en dur ailleurs) ---
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_reasoning_model: str = "deepseek-v4-flash-thinking"
    # création et réparation des exercices : modèle pro
    deepseek_pro_model: str = "deepseek-v4-pro"
    # plafond de tokens de SORTIE d'un appel DeepSeek JSON. Bien plus bas que les
    # budgets Claude (16k-48k) : les modèles DeepSeek coupent la sortie autour de
    # 8k. Un lot dont la sortie dépasse ce plafond échoue au parsing JSON et
    # retombe proprement (adaptation en solo, vérification = version adaptée
    # gardée) ; il pilote donc AUSSI les tailles de lot de la pipeline Indigo.
    deepseek_max_output_tokens: int = 8192
    # Correcteur des réponses écrites (services.llm_grader) : DeepSeek Flash v4,
    # PAS la variante "thinking" — la tâche est courte et cadrée (un barème, une
    # réponse attendue, une réponse d'élève), et elle tourne sur toutes les
    # copies d'une classe : la latence et le coût priment sur la réflexion.
    correction_model: str = "deepseek-v4-flash"
    # nombre de réponses envoyées dans le MÊME appel. Elles viennent d'un seul
    # exercice tant qu'il y en a (l'énoncé n'est alors écrit qu'une fois) ; au-delà
    # d'une dizaine, la sortie JSON s'allonge assez pour risquer la troncature.
    correction_batch_size: int = 8
    # En dessous, même une réponse structurellement valide reste à confirmer par
    # le professeur. Au-dessus, un verdict/points cohérent est appliqué sans
    # encombrer l'assistant manuel.
    correction_confidence_min: float = 0.90
    claude_model: str = "claude-haiku-4-5-20251001"
    # extraction Sésamaths (lecture fidèle des pages de manuel) : Mistral OCR,
    # moteur de reconnaissance de document dédié (pas un modèle de chat) —
    # le typage de blocs (title/text/table/image/equation/list/...) exige OCR
    # 4 précisément ("mistral-ocr-4-0"), pas "-latest" (modèles antérieurs
    # acceptent include_blocks mais renvoient un tableau vide)
    mistral_ocr_model: str = "mistral-ocr-4-0"
    # adaptation Sésamaths (texte pur, blocs OCR bruts -> contrat app) : tâche
    # exigeante (découpage d'exercices, choix de format, correction) — Haiku
    # produisait trop peu d'exercices distincts par Série (cf. incident "un
    # seul exercice en banque", 17/07) ; Sonnet, un seul modèle, pas de repli
    # (un 2e modèle "correcteur" ajoutait de la complexité sans fiabiliser)
    claude_adapt_model: str = "claude-sonnet-5"
    # Pipeline Indigo (onglet Exercices) : les TROIS étapes LLM (découpage,
    # génération, vérification) passent par UN fournisseur choisi à l'exécution
    # (persisté dans SystemSetting 'indigo_llm_provider', cf. services.indigo_llm).
    # Défaut = multipass : c'est le SEUL mode que l'onglet Exercices propose
    # depuis le 05/09 (plus de sélecteur) — anthropic/deepseek/qcm restent
    # câblés et testés, mais ne sont plus atteignables depuis l'interface.
    #   • anthropic : découpage + génération = Sonnet, vérification = Opus ;
    #   • deepseek  : les trois étapes = DeepSeek pro v4 (clé « deepseek-pro ») ;
    #   • qcm       : pipeline « QCM only » (services.indigo_qcm) — DeepSeek pro,
    #     un prompt court, trois formats de réponse (QCM unique / multiple /
    #     grille), barème CODÉ et vérification mathématique Python. C'est un
    #     MODE de génération, pas seulement un fournisseur : il remplace
    #     l'adaptation + la relecture par un seul appel qui produit, pour chaque
    #     exercice du manuel, un trio base + dérivé facile + dérivé difficile.
    #   • multipass : pipeline « QCM multipass » (services.indigo_multipass) —
    #     DeepSeek FLASH, cinq passes (rattachement et checklist, génération,
    #     résolution indépendante, mise en page, retouche) et un duo
    #     Base/Facile écrit en brouillon. Un exercice « expert » du manuel
    #     (badge CV) tient lieu de dérivé Difficile (§ services.indigo,
    #     _persist_multipass_family) — il n'existe plus de troisième variante
    #     générée. La génération se partage entre deux sources d'un même lot
    #     (indigo_multipass_batch_size) quand elles se rattachent à la même
    #     compétence ; les quatre autres passes restent une source à la fois.
    #     Le corrigé du manuel professeur, quand il est déjà indexé, informe
    #     SEULEMENT la génération.
    # Sans la clé du fournisseur choisi, les trois étapes tournent hors-ligne
    # (replis OCR bruts). Indigo n'utilise plus Gemini.
    indigo_llm_provider_default: str = "multipass"
    indigo_anthropic_segment_model: str = "claude-sonnet-5"
    indigo_anthropic_adapt_model: str = "claude-sonnet-5"
    indigo_anthropic_review_model: str = "claude-opus-5"
    # plafond de sortie côté Anthropic (Claude gère de larges sorties). Mesure
    # du 02/08 : un exercice adapté coûte 400 à 5 800 tokens de sortie (moyenne
    # ~1 800), donc un lot de 4 tient sous 16 000 dans le cas courant — et
    # indigo_llm._output_budgets double/quadruple ce plafond si la réponse est
    # quand même tronquée, au lieu de perdre le lot entier.
    indigo_anthropic_max_output_tokens: int = 16000
    # délai TOTAL d'un appel LLM Indigo. Le défaut global (llm_call_timeout_s,
    # 180 s) est calibré sur des réponses courtes : un lot d'exercices met
    # plusieurs minutes à s'écrire, et l'abandonner renvoyait tout le lot en
    # adaptation un par un (5-7x plus d'appels → budget quotidien épuisé).
    indigo_llm_call_timeout_s: int = 600
    indigo_deepseek_model: str = "deepseek-v4-pro"
    # Modèle du mode « QCM only ». Le pro (et pas une variante flash) : la
    # justesse mathématique des propositions est ce qui fait ou défait un QCM —
    # un distracteur égal à la bonne réponse rend l'exercice incorrigeable. La
    # vérification Python (services.indigo_check) est un FILET, pas une béquille.
    indigo_qcm_model: str = "deepseek-v4-pro"
    # Exercices SOURCE par appel en mode QCM. Chacun rend TROIS variantes (base,
    # facile, difficile) : 2 sources ≈ le volume de sortie d'un lot classique de
    # 4 à 6, donc sous le plafond de 8 k tokens de DeepSeek (incident A1.3).
    indigo_qcm_batch_size: int = 2
    # --- mode « QCM multipass » (services.indigo_multipass) ---
    # DeepSeek FLASH simple sur les CINQ passes : la qualité ne vient pas de la
    # puissance d'un appel mais de cinq passes qui se relisent (rattachement
    # /nettoyage, génération, résolution indépendante, mise en page, retouche).
    # Chaque passe traite une source à la fois, SAUF la génération, partagée
    # entre deux sources d'un même lot quand c'est possible (§ ci-dessous,
    # indigo_multipass_batch_size).
    # Un seul modèle pour les cinq — aucune variante raisonneuse, aucun réglage
    # par passe : ce qui distingue les passes, c'est ce qu'on leur MONTRE (le
    # solveur ne voit pas les réponses), pas le modèle qui les traite.
    indigo_multipass_model: str = "deepseek-v4-flash"
    # Extraction VISUELLE du manuel élève, propre au mode multipass. Ce modèle
    # partage la clé « deepseek-flash » avec le modèle texte ci-dessus ; seul son
    # identifiant change. Une seule demi-page physique est envoyée par appel :
    # la double page entière faisait fusionner des badges et décaler les numéros.
    indigo_multipass_vision_model: str = "deepseek-v4-flash-vision-exp"
    indigo_multipass_vision_max_output_tokens: int = 8192
    # DÉCOUPAGE DE LA PAGE. Le manuel est un PDF de captures d'un lecteur en
    # plein écran : chaque raster porte une double page ENTOURÉE du décor du
    # lecteur (flèches, boutons, vignette). Le contenu occupe donc une fraction
    # constante de la largeur, mesurée ici sur le manuel 3e — 123 px à 1592 px
    # sur 1755. Ces bornes sont RÉGLABLES et jamais recalculées page par page :
    # un détecteur de gouttières se laisse prendre par la première figure grise
    # venue, alors que la mise en page, elle, ne bouge pas d'un pixel.
    indigo_multipass_page_x0: float = 0.070
    indigo_multipass_page_x1: float = 0.907
    # Colonnes d'exercices dans cette boîte (2 pages × 2 colonnes). Découper la
    # LARGEUR TOTALE en quatre coupait au travers des colonnes 1 et 4 : sur les
    # pages 86-87, trois exercices perdus et trois lus deux fois.
    indigo_multipass_columns: int = 4
    # Reprises des passes 2 à 5 après un INCIDENT DE TRANSPORT (délai dépassé,
    # sortie tronquée, 5xx). Ce n'est plus une boucle de qualité : depuis le
    # 04/09, un défaut d'exercice se répare sur place (passe 5) ou se signale, il
    # ne relance jamais la génération. Mesuré sur les pages 67-68, relancer
    # quatre fois coûtait 63 générations pour 8 sources et faisait passer les
    # défauts de 85 à 81 — le même exercice, avec les mêmes défauts.
    indigo_multipass_max_attempts: int = 4
    # Tours de RETOUCHE (passe 5) sur le même exercice. Le deuxième tour ne
    # tourne QUE s'il reste des défauts après le premier, et il coûte un appel
    # là où l'ancienne relance en coûtait quatre (génération + résolution +
    # mise en page + audit). Réparer deux fois de suite le même texte reste
    # réparer sur place : à aucun moment l'exercice ne repart de zéro.
    indigo_multipass_repair_rounds: int = 2
    # Exercices SOURCE par appel de GÉNÉRATION (passe 2 seule — les passes 1, 3,
    # 4, 5 restent une famille à la fois). Un appel ne produit que deux
    # variantes par source (Base, Facile) : deux sources à la fois ramènent le
    # volume de sortie près de l'ancien niveau à trois variantes, comme
    # `indigo_qcm_batch_size` le fait déjà pour le mode
    # « QCM only ».
    indigo_multipass_batch_size: int = 2
    # Budget de sortie du PREMIER appel (l'échelle d'indigo_llm essaie ensuite le
    # double puis le quadruple). Plus large que le défaut DeepSeek de 8192 : la
    # passe 2 doit écrire TROIS exercices complets d'un coup. C'est un PLAFOND,
    # pas une réservation — on ne paie que les tokens réellement produits, donc
    # la marge ne coûte rien alors qu'un budget trop court coûte un appel perdu
    # par exercice (extraction A1.2 du 03/09 : 25 appels de génération à 8092
    # tokens de sortie en moyenne, soit tous au plafond, pour zéro exercice —
    # le raisonnement est désactivé depuis, cf. indigo_llm.call).
    indigo_multipass_max_output_tokens: int = 16384
    # « Cheap and Wait » (services.indigo_offpeak) : heures creuses DeepSeek
    # codées en dur (grille tarifaire du fournisseur, ne se règle pas) —
    # seule la case (activée/désactivée) est persistée depuis l'onglet
    # Exercices, cf. indigo_offpeak.PEAK_WINDOWS_UTC.
    # vérification désactivable seule (appel payant par cible), quel que soit le
    # fournisseur (cf. exercise_gen.format_contract, contrat partagé).
    indigo_review_enabled: bool = True
    # création d'exercices (pipeline Gemini, cf. services/gemini_gen.py) :
    # création ANCRÉE dans les pages du manuel traitant la compétence (OCR
    # Mistral de la Série, partagé avec la pipeline Sésamaths).
    # "gemini-2.5-flash" (nom figé) renvoie 404 "no longer available to new
    # users" pour toute clé API créée après son retrait — trouvé le 17/07 en
    # diagnostiquant une banque Gemini vide (0 exercice créé). L'alias
    # "-latest" évite que ça se reproduise à la prochaine dépréciation : au
    # prix d'un modèle cible qui peut changer sous nos pieds (donc un tarif à
    # revérifier de temps en temps, cf. gemini_json).
    gemini_model: str = "gemini-flash-latest"

    # --- Pipeline Gemini (banque d'exercices créés, par compétence) ---
    # Taille de banque visée par compétence × niveau. Il n'existe PAS
    # d'équivalent côté Sésamaths : ce que la Série du manuel contient est
    # tout ce qu'on peut en extraire (ni plus ni moins), alors qu'ici on
    # appelle le LLM autant de fois que nécessaire.
    # On remplit la banque D'UN COUP pour la compétence, et les sujets suivants
    # y puisent sans plus rien payer. Une cible calée sur le besoin d'UN sujet
    # fait rappeler le modèle à chaque sujet, et lui fait recréer à l'aveugle des
    # exercices proches de ceux déjà en banque.
    # 15 (3 lots de 5) : cible resserrée depuis les 30 initiaux — 20 exercices en
    # tout par compétence (15 classiques + 5 courts), ce qui suffit à remplir une
    # copie sans répétition tout en divisant par deux la facture de création.
    gemini_bank_target: int = 15
    # exercices COURTS de remplissage (kind="filler") créés en UN appel dédié,
    # en plus des 15 exercices classiques : servent à combler les trous de bas
    # de page laissés par les grandes cartes (services.generation). Banque
    # cible totale = 15 + 5 = 20.
    gemini_filler_target: int = 5
    gemini_batch_size: int = 5            # exercices demandés par appel
    # garde-fou : au-delà, on garde ce qu'on a plutôt que d'enchaîner les
    # appels payants pour une compétence sur laquelle le modèle patine.
    # 10 pour 15 exercices par lots de 5 : 3 lots parfaits suffiraient, la
    # marge absorbe les exercices recalés par la validation.
    gemini_max_batches: int = 10

    # --- Budgets / quotas par défaut ---
    mathpix_concurrency: int = 3
    mathpix_daily_limit: int = 500
    # plafond de dépense Mathpix sur 24 h glissantes (OCR des copies élèves).
    # Réglage PROPRE depuis le 02/08 : il valait llm_daily_cost_limit_eur × 5,
    # donc relever le budget des LLM relevait aussi celui de l'OCR sans le dire.
    # 10 € = exactement l'ancienne valeur effective (2 × 5).
    mathpix_daily_cost_limit_eur: float = 10.0
    # Plafond de dépense PAR FOURNISSEUR sur 24 h GLISSANTES (pas par jour
    # calendaire, cf. providers._today_cost). Porté de 2 à 10 € le 02/08 sur
    # décision de l'utilisateur : une extraction Indigo d'UNE compétence coûte
    # 0,3 à 0,5 € côté Anthropic (découpage Sonnet + adaptation Sonnet +
    # relecture Opus), donc 2 € ne couvraient que 4 à 6 compétences — le plafond
    # tombait au milieu d'une extraction et les exercices restants finissaient
    # en repli OCR brut (incident A1.3). Surchargeable par
    # MATHPRINT_LLM_DAILY_COST_LIMIT_EUR.
    llm_daily_cost_limit_eur: float = 10.0
    # délai TOTAL maximal d'un appel LLM (connexion + réponse complète) :
    # le read-timeout httpx est par lecture socket, pas global — un serveur
    # qui répond au compte-gouttes peut sinon bloquer le worker indéfiniment
    llm_call_timeout_s: int = 180
    # délai TOTAL maximal d'un job de génération de sujet : filet de sécurité
    # au-dessus de llm_call_timeout_s — protège contre un blocage hors appel
    # LLM (verrou DB, appel sans garde-fou) qui laisserait le job "running"
    # indéfiniment, invisible dans les logs (cf. incidents Sésamaths)
    job_generation_timeout_s: int = 900

    # --- Pédagogie ---
    forgetting_threshold: float = 0.80   # probabilité de rappel sous laquelle une compétence est "due"
    level_max_auto_delta: int = 1        # variation auto max du niveau 1-10 par cycle
    # Mélange visé des types d'exercices DANS une copie (cf. services.
    # distribution.pick_balanced_exercise). Ce réglage n'est pas seulement
    # pédagogique : il fixe la répartition de la CHARGE DE CORRECTION entre les
    # deux moteurs automatiques. Le bucket "qcm" (tout response_type qcm_*) est
    # corrigé par vision par ordinateur — gratuit, local, fiable ; tout le reste
    # (application/probleme = cases manuscrites) part en OCR Mathpix — payant et
    # sous quota (mathpix_daily_limit). Cible : ~50 % CV / ~50 % Mathpix.
    # Le ratio application/probleme historique (55/35) est conservé à
    # l'intérieur de la moitié Mathpix.
    exercise_kind_mix: dict = {"qcm": 0.50, "application": 0.30, "probleme": 0.20}
    # --- sujets individuels : choix des exercices sur l'historique réel
    # (services.student_history, aucun LLM) ---
    # Un exercice RATÉ redevient un bon candidat passé ce délai : assez tard
    # pour ne pas le resservir de mémoire, assez tôt pour rattraper la lacune.
    history_replay_min_days: int = 21
    # Au-dessus, un exercice compte comme réussi (donc à ne resservir qu'en
    # dernier recours, banque épuisée).
    history_success_threshold: float = 0.5

    # --- Sésamaths (extraction de manuels PDF Sésamath, à la demande) ---
    # niveau -> chemin du manuel ; seule la 5e est couverte pour l'instant,
    # les autres cycles viendront plus tard (manuel absent -> journalisé,
    # jamais bloquant, cf. services/sesamaths_pdf.load_manual)
    # manuel LIVRÉ avec le code (dans app/data/manuals), donc présent à
    # l'identique en dev et dans l'image Docker — cf. _APP_DIR ci-dessus.
    sesamaths_manuals: dict[str, str] = {"5e": str(_APP_DIR / "data" / "manuals" / "5.pdf")}
    sesamaths_schema_version: str = "6"   # bump -> invalide l'ancien cache (texte)

    # --- Indigo (onglet Exercices, admin) : copie/adaptation d'exercices d'un
    # manuel réel. Deux PDF par niveau : "eleve" (énoncés, badges) et "prof"
    # (corrigés). TROP GROS pour être versionnés (manuel élève ~200 Mo) : ils
    # restent LOCAUX à l'instance admin (dossier context/), jamais livrés dans
    # l'image. L'onglet est admin-only et la construction se fait sur cette
    # instance ; seuls les CROPS validés (petits PNG) sont ensuite publiés dans
    # le repo (backend/app/data/indigo/), eux livrés à tous. Résolution via le
    # même _resolve_manual_path que Sésamaths (essaie context/, data_dir/…).
    indigo_manuals: dict[str, dict[str, str]] = {
        "3e": {"eleve": str(_REPO_ROOT / "context" / "3_indigo.pdf"),
               "prof": str(_REPO_ROOT / "context" / "3_indigo_prof.pdf")},
    }
    indigo_schema_version: str = "2"   # 2 = difficulté sur 3 niveaux (cf. indigo._published_level)

    # --- Prompts LLM éditables (hors code) ---
    # Les prompts des pipelines de CRÉATION d'exercices (Indigo côté API, cli-exos
    # côté abonnement) vivent dans des fichiers texte, organisés par pipeline :
    # `prompts/<pipeline>/<étape>.txt` (ex. prompts/indigo/generation.txt). On peut
    # les éditer sans toucher au code, pour affiner la qualité des appels LLM.
    # Comme les manuels Indigo (context/…), ce dossier est à la RACINE du repo et
    # reste local à l'instance qui fait la construction (il n'est pas dans l'image
    # Docker slim, qui ne copie que app/). Le chargement est PARESSEUX (jamais à
    # l'import) : l'app démarre normalement même sans ce dossier ; seule une
    # extraction qui tourne réellement a besoin des fichiers (cf. services.prompts).
    # Surchargeable par MATHPRINT_PROMPTS_DIR.
    prompts_dir: Path = _REPO_ROOT / "prompts"

    # --- Impression (CUPS local ou IPP réseau, §11.5) ---
    printing_enabled: bool = True

    # --- Divers ---
    # renseignés par la CI au build de l'image (Dockerfile ARG GIT_SHA/BUILD_TIME) ;
    # affichés dans Paramètres → Système pour vérifier qu'une mise à jour a bien
    # été appliquée sur le NAS.
    build_sha: str = "dev"
    build_time: str = ""
    correction_color: str = "#C62828"
    dropout_color: str = "#F5B7A8"       # rouge saumon clair


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

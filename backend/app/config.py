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
    # depuis l'onglet (toggle, persisté dans SystemSetting 'indigo_llm_provider',
    # cf. services.indigo_llm). Défaut = Anthropic. Deux câblages :
    #   • anthropic : découpage + génération = Sonnet, vérification = Opus ;
    #   • deepseek  : les trois étapes = DeepSeek pro v4 (clé « deepseek-pro »).
    # Sans la clé du fournisseur choisi, les trois étapes tournent hors-ligne
    # (replis OCR bruts). Indigo n'utilise plus Gemini.
    indigo_llm_provider_default: str = "anthropic"
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
    next_plan_max_age_days: int = 60     # au-delà, le plan post-correction stocké est ignoré
    # --- MathALÉA (service Node headless, conteneur "mathalea" §11.1) ---
    mathalea_url: str = "http://localhost:8123"
    # délai TOTAL maximal d'un appel MathALÉA (cold start possible du service
    # Node à la première requête) — même logique que llm_call_timeout_s :
    # le timeout httpx est par lecture socket, pas global (RM- incident worker
    # bloqué indéfiniment sur un service qui répond au compte-gouttes/tarde)
    mathalea_call_timeout_s: int = 30

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
    indigo_schema_version: str = "1"

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

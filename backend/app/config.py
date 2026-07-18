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
    # création d'exercices et de rappels de leçon : modèle pro
    deepseek_pro_model: str = "deepseek-v4-pro"
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
    # 30 (et non 10) : on remplit la banque D'UN COUP pour la compétence, et
    # les sujets suivants y puisent sans plus rien payer. Une cible calée sur
    # le besoin d'UN sujet fait rappeler le modèle à chaque sujet, et lui fait
    # recréer à l'aveugle des exercices proches de ceux déjà en banque.
    gemini_bank_target: int = 30
    # exercices COURTS de remplissage (kind="filler") créés en UN appel dédié,
    # en plus des 30 exercices classiques : servent à combler les trous de bas
    # de page laissés par les grandes cartes (services.generation). Banque
    # cible totale = 30 + 5 = 35.
    gemini_filler_target: int = 5
    gemini_batch_size: int = 5            # exercices demandés par appel
    # garde-fou : au-delà, on garde ce qu'on a plutôt que d'enchaîner les
    # appels payants pour une compétence sur laquelle le modèle patine.
    # 10 pour 30 exercices par lots de 5 : 6 lots parfaits suffiraient, la
    # marge absorbe les exercices recalés par la validation.
    gemini_max_batches: int = 10

    # --- Budgets / quotas par défaut ---
    mathpix_concurrency: int = 3
    mathpix_daily_limit: int = 500
    llm_daily_cost_limit_eur: float = 2.0
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
    lesson_review_mastery_threshold: float = 0.5  # maîtrise sous ce seuil = lacune -> rappel de leçon
    max_lessons_per_copy: int = 2        # rappels de leçon max insérés dans une même copie

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

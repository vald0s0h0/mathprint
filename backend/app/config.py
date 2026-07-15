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
    exercise_variants_per_level: int = 3   # taille de banque par compétence × niveau
    claude_model: str = "claude-haiku-4-5-20251001"
    # extraction vision des pages de manuel Sésamath (multimodal) : Haiku par
    # défaut, repli Opus 4.8 sur les pages denses qu'Haiku n'arrive pas à extraire
    claude_vision_model: str = "claude-haiku-4-5"
    claude_vision_fallback_model: str = "claude-opus-4-8"

    # --- Budgets / quotas par défaut ---
    mathpix_concurrency: int = 3
    mathpix_daily_limit: int = 500
    llm_daily_cost_limit_eur: float = 2.0
    # délai TOTAL maximal d'un appel LLM (connexion + réponse complète) :
    # le read-timeout httpx est par lecture socket, pas global — un serveur
    # qui répond au compte-gouttes peut sinon bloquer le worker indéfiniment
    llm_call_timeout_s: int = 180

    # --- Pédagogie ---
    forgetting_threshold: float = 0.80   # probabilité de rappel sous laquelle une compétence est "due"
    level_max_auto_delta: int = 1        # variation auto max du niveau 1-10 par cycle
    exercise_kind_mix: dict = {"application": 0.55, "probleme": 0.35, "qcm": 0.10}
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
    sesamaths_schema_version: str = "2"   # bump -> invalide l'ancien cache (texte)

    # --- Impression (CUPS local ou IPP réseau, §11.5) ---
    printing_enabled: bool = True

    # --- Divers ---
    # renseignés par la CI au build de l'image (Dockerfile ARG GIT_SHA/BUILD_TIME) ;
    # affichés dans Paramètres → Système pour vérifier qu'une mise à jour a bien
    # été appliquée sur le NAS.
    build_sha: str = "dev"
    build_time: str = ""
    mock_mode: bool = True               # classe mock + fournisseurs simulés (désactivable dans Réglages)
    correction_color: str = "#C62828"
    dropout_color: str = "#F5B7A8"       # rouge saumon clair


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

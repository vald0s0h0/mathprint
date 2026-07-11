"""Configuration centrale de MathPrint (NAS).

Tout est surchargeable par variable d'environnement (préfixe MATHPRINT_).
Aucun identifiant de modèle LLM n'est codé en dur ailleurs que dans ce
registre par défaut, modifiable en base via system_settings / provider_configs.
"""
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MATHPRINT_"}

    # --- Base ---
    database_url: str = "sqlite:///./mathprint.db"
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    secret_key: str = "change-me-on-nas"          # JWT
    hmac_key: str = "change-me-hmac-key"          # signature des QR pages
    session_hours: int = 12

    # --- Registre de modèles par défaut (RM-011 : jamais codé en dur ailleurs) ---
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_reasoning_model: str = "deepseek-v4-flash-thinking"
    # création d'exercices et de rappels de leçon : modèle pro
    deepseek_pro_model: str = "deepseek-v4-pro"
    exercise_variants_per_level: int = 3   # taille de banque par compétence × niveau
    claude_model: str = "claude-haiku-4-5-20251001"

    # --- Budgets / quotas par défaut ---
    mathpix_concurrency: int = 3
    mathpix_daily_limit: int = 500
    llm_daily_cost_limit_eur: float = 2.0

    # --- Pédagogie ---
    forgetting_threshold: float = 0.80   # probabilité de rappel sous laquelle une compétence est "due"
    level_max_auto_delta: int = 1        # variation auto max du niveau 1-10 par cycle

    # --- MathALÉA (service Node headless, conteneur "mathalea" §11.1) ---
    mathalea_url: str = "http://localhost:8123"

    # --- Impression (CUPS local ou IPP réseau, §11.5) ---
    printing_enabled: bool = True

    # --- Divers ---
    mock_mode: bool = True               # classe mock + fournisseurs simulés (désactivable dans Réglages)
    correction_color: str = "#C62828"
    dropout_color: str = "#F5B7A8"       # rouge saumon clair


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

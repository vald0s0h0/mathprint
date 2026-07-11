"""Amorçage des secrets internes (JWT, HMAC des QR) — premier démarrage.

Ni l'utilisateur ni un fichier `.env` versionné n'ont besoin de fournir
SECRET_KEY/HMAC_KEY : tant qu'ils sont à leur valeur par défaut (insécure,
celle du dépôt), on en génère de vrais et on les persiste sur le volume
`/data` (stable entre redémarrages et mises à jour), rechargés au prochain
démarrage via `Settings.model_config["env_file"]`.
"""
import secrets

from ..config import _RUNTIME_ENV_FILE, settings

_INSECURE_DEFAULTS = {
    "MATHPRINT_SECRET_KEY": "change-me-on-nas",
    "MATHPRINT_HMAC_KEY": "change-me-hmac-key",
}


def ensure_strong_secrets() -> list[str]:
    """Génère et persiste les secrets encore à leur valeur par défaut.
    Retourne la liste des clés régénérées (vide si déjà configurées)."""
    to_generate: dict[str, str] = {}
    if settings.secret_key == _INSECURE_DEFAULTS["MATHPRINT_SECRET_KEY"]:
        to_generate["MATHPRINT_SECRET_KEY"] = secrets.token_hex(32)
    if settings.hmac_key == _INSECURE_DEFAULTS["MATHPRINT_HMAC_KEY"]:
        to_generate["MATHPRINT_HMAC_KEY"] = secrets.token_hex(32)
    if not to_generate:
        return []

    _RUNTIME_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = (_RUNTIME_ENV_FILE.read_text(encoding="utf-8").splitlines()
                if _RUNTIME_ENV_FILE.exists() else [])
    written: set[str] = set()
    lines: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0] if "=" in line else None
        if key in to_generate:
            lines.append(f"{key}={to_generate[key]}")
            written.add(key)
        else:
            lines.append(line)
    for key, value in to_generate.items():
        if key not in written:
            lines.append(f"{key}={value}")
    _RUNTIME_ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # effet immédiat pour ce process (sinon seule la persistance disque changerait)
    if "MATHPRINT_SECRET_KEY" in to_generate:
        settings.secret_key = to_generate["MATHPRINT_SECRET_KEY"]
    if "MATHPRINT_HMAC_KEY" in to_generate:
        settings.hmac_key = to_generate["MATHPRINT_HMAC_KEY"]
    return list(to_generate.keys())

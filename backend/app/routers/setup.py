"""Écran de démarrage (premier lancement) : crée l'unique compte
administrateur tant qu'aucun utilisateur n'existe. Volontairement SANS
dépendance d'authentification (il n'y a encore personne à authentifier) —
la sécurité tient au verrou "un seul appel possible, tant que la table
`users` est vide", vérifié à chaque requête.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ProviderConfig, User
from ..services.security import hash_password, make_token

router = APIRouter(prefix="/api/setup", tags=["setup"])

ALLOWED_PROVIDERS = {"mathpix", "deepseek-flash", "deepseek-pro", "anthropic",
                     "mistral", "gemini"}


class ProviderSetupIn(BaseModel):
    model: str = ""
    secret: str = ""


class SetupIn(BaseModel):
    email: str
    display_name: str = ""
    password: str
    # clés optionnelles parmi ALLOWED_PROVIDERS ; toute absente/vide reste en mode mock
    providers: dict[str, ProviderSetupIn] = {}


def _already_configured(db: Session) -> bool:
    return db.query(User).first() is not None


@router.get("/status")
def setup_status(db: Session = Depends(get_db)):
    return {"needs_setup": not _already_configured(db)}


@router.post("")
def create_first_admin(body: SetupIn, db: Session = Depends(get_db)):
    if _already_configured(db):
        raise HTTPException(409, "MathPrint est déjà configuré")
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(422, "E-mail invalide")
    if len(body.password) < 8:
        raise HTTPException(422, "Le mot de passe doit compter au moins 8 caractères")

    user = User(email=email, password_hash=hash_password(body.password),
               display_name=body.display_name.strip() or "Professeur", role="admin")
    db.add(user)
    db.flush()

    for provider, cfg in body.providers.items():
        if provider not in ALLOWED_PROVIDERS or not cfg.secret.strip():
            continue
        db.add(ProviderConfig(provider=provider, model=cfg.model.strip(),
                              encrypted_secret=cfg.secret.strip(), active=True))

    db.commit()
    return {"token": make_token(user.id, user.role),
            "user": {"id": user.id, "email": user.email,
                     "display_name": user.display_name, "role": user.role}}

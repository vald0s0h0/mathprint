from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .services.security import decode_token


def current_user(authorization: str = Header(default=""),
                 db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Non authentifié")
    try:
        payload = decode_token(authorization.removeprefix("Bearer "))
    except Exception:
        raise HTTPException(401, "Session expirée")
    user = db.get(User, payload["sub"])
    if not user or not user.active:
        raise HTTPException(401, "Compte inactif")
    return user


def require_role(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Droits insuffisants")
        return user
    return check

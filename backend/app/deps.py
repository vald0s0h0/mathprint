from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .services.security import decode_token


def current_user(request: Request, authorization: str = Header(default=""),
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
    from .services.access import check_route, scope_session, check_path_resources
    check_route(user, request)
    db.info["actor_id"] = user.id
    scope_session(db, user)
    check_path_resources(db, request)
    return user


def require_role(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Droits insuffisants")
        return user
    return check

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .services.security import (
    SESSION_TOKEN_HEADER, decode_token, make_token, token_matches_password, token_needs_renewal,
)


def current_user(request: Request, response: Response, authorization: str = Header(default=""),
                 x_kn_zone: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Non authentifié")
    try:
        payload = decode_token(authorization.removeprefix("Bearer "))
    except Exception:
        raise HTTPException(401, "Session expirée")
    user = db.get(User, payload["sub"])
    if not user or not user.active:
        raise HTTPException(401, "Compte inactif")
    if not token_matches_password(payload, user):
        raise HTTPException(401, "Session expirée")
    if token_needs_renewal(payload):
        response.headers[SESSION_TOKEN_HEADER] = make_token(user)
    from .services.access import ZONES, check_route, scope_session, check_path_resources
    check_route(user, request)
    db.info["actor_id"] = user.id
    zone = x_kn_zone if x_kn_zone in ZONES else None
    db.info["zone"] = zone
    scope_session(db, user, zone)
    check_path_resources(db, request)
    return user


def require_role(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Droits insuffisants")
        return user
    return check

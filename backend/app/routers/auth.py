from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.security import hash_password, make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email.lower().strip()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants invalides")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return {"token": make_token(user.id, user.role),
            "user": {"id": user.id, "email": user.email,
                     "display_name": user.display_name, "role": user.role}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email,
            "display_name": user.display_name, "role": user.role}


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password")
def change_password(body: ChangePasswordIn, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(422, "Le nouveau mot de passe doit compter au moins 8 caractères")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}

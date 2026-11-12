from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services import login_throttle
from ..services.security import SESSION_TOKEN_HEADER, hash_password, make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db), request: Request = None):
    email, ip = body.email.lower().strip(), login_throttle.client_ip(request)
    login_throttle.check_allowed(email, ip)
    user = db.query(User).filter_by(email=email).first()
    if not user or not verify_password(body.password, user.password_hash):
        login_throttle.record_failure(email, ip)
        raise HTTPException(401, "Identifiants invalides")
    login_throttle.record_success(email)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return {"token": make_token(user),
            "user": {"id": user.id, "email": user.email,
                     "display_name": user.display_name, "role": user.role}}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import TeacherProfile
    from ..services.education import teaching_scope
    profile = db.query(TeacherProfile).filter_by(user_id=user.id).first()
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "role": user.role, "teacher_profile_id": profile.id if profile else None,
            "service_mode": profile.service_mode if profile else None,
            "program_id": profile.program_id if profile else None,
            "subject_id": profile.subject_id if profile else None,
            "teaching": teaching_scope(db, user), "edition": settings.edition}


# Préférences personnelles (roue crantée). Chaque clé retombe sur le réglage
# global du même nom (SystemSetting), modifiable par l'administrateur.
PREFERENCE_KEYS = {"correction_shortcuts"}


def _preferences(db: Session, user: User) -> dict:
    from ..models import SystemSetting, UserPreference
    out = {}
    for key in sorted(PREFERENCE_KEYS):
        default = db.get(SystemSetting, key)
        mine = db.get(UserPreference, (user.id, key))
        out[key] = {**(default.value_json if default else {}), **(mine.value_json if mine else {})}
    return out


@router.get("/me/preferences")
def my_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _preferences(db, user)


class PreferenceIn(BaseModel):
    value: dict


@router.put("/me/preferences/{key}")
def set_my_preference(key: str, body: PreferenceIn, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    from ..models import UserPreference
    if key not in PREFERENCE_KEYS:
        raise HTTPException(404, "Préférence inconnue")
    row = db.get(UserPreference, (user.id, key))
    if not row:
        row = UserPreference(user_id=user.id, key=key)
        db.add(row)
    row.value_json = body.value
    db.commit()
    return _preferences(db, user)


class TeachingChoiceIn(BaseModel):
    program_id: str
    subject_id: str


@router.put("/me/teaching")
def set_my_teaching(body: TeachingChoiceIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Réservé au professeur autonome : pour un accompagné, tout passe par ses affectations."""
    from ..services.education import autonomous_profile, choose_teaching, teaching_scope
    profile = autonomous_profile(db, user)
    if not profile:
        raise HTTPException(403, "Réservé aux professeurs autonomes")
    choose_teaching(db, profile, body.program_id, body.subject_id)
    db.commit()
    return teaching_scope(db, user)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password")
def change_password(body: ChangePasswordIn, response: Response, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if not verify_password(body.current_password, user.password_hash):
        # Pas 401 : le frontend y voit une session expirée et déconnecte.
        raise HTTPException(400, "Mot de passe actuel incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(422, "Le nouveau mot de passe doit compter au moins 8 caractères")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    # Les autres appareils sont déconnectés ; celui-ci reste connecté.
    response.headers[SESSION_TOKEN_HEADER] = make_token(user)
    return {"ok": True}


class InvitationCheckIn(BaseModel):
    token: str


class RegistrationIn(InvitationCheckIn):
    email: str
    password: str


def _invitation(db, token):
    import hashlib
    from ..models import TeacherInvitation, TeacherProfile
    invitation = db.query(TeacherInvitation).filter_by(
        token_hash=hashlib.sha256(token.encode()).hexdigest(), used_at=None).first()
    if not invitation:
        raise HTTPException(410, "Lien d’inscription invalide ou déjà utilisé")
    expiry = invitation.expires_at.replace(tzinfo=timezone.utc) if invitation.expires_at.tzinfo is None else invitation.expires_at
    teacher = db.get(TeacherProfile, invitation.teacher_id)
    if expiry <= datetime.now(timezone.utc) or not teacher or not teacher.active or teacher.user_id:
        raise HTTPException(410, "Lien expiré ou compte déjà activé")
    return invitation, teacher


@router.post("/invitation/check")
def check_invitation(body: InvitationCheckIn, db: Session = Depends(get_db)):
    _, teacher = _invitation(db, body.token)
    return {"name": teacher.name, "service_mode": teacher.service_mode}


@router.post("/register")
def register(body: RegistrationIn, db: Session = Depends(get_db)):
    from sqlalchemy.exc import IntegrityError
    from ..models import TeacherInvitation
    invitation, teacher = _invitation(db, body.token)
    email = body.email.strip().lower()
    if len(body.password) < 10 or len(email) > 254 or "@" not in email or " " in email:
        raise HTTPException(422, "Adresse e-mail valide et mot de passe d’au moins 10 caractères requis")
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "Cette adresse possède déjà un compte")
    claimed = db.query(TeacherInvitation).filter_by(id=invitation.id, used_at=None).update(
        {"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    if not claimed: raise HTTPException(410, "Lien déjà utilisé")
    user = User(email=email, password_hash=hash_password(body.password), display_name=teacher.name,
                role="teacher", active=True)
    db.add(user)
    try:
        db.flush()
        teacher.user_id = user.id
        teacher.email = email
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ce compte a déjà été activé")
    return {"token": make_token(user)}

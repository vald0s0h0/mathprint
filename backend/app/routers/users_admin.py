"""Administration des comptes Kiro & Nina — API réservée aux admins."""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import TeacherProfile, User
from .operations import issue_invitation


router = APIRouter(
    prefix="/api/admin/users",
    tags=["users-admin"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("")
def list_users(db: Session = Depends(get_db)):
    """Liste les comptes sans jamais exposer d'information d'authentification."""
    users = (
        db.query(User)
        .order_by(func.lower(User.display_name), func.lower(User.email))
        .all()
    )
    return [
        {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "subscription_plan": (
                (user.subscription_plan or "free") if user.role == "teacher" else None
            ),
            "active": user.active,
            "last_login_at": user.last_login_at,
        }
        for user in users
    ]


from pydantic import BaseModel, Field
from fastapi import HTTPException
from ..services.security import hash_password


class CorrectorIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    email: str
    password: str = Field(min_length=10)


@router.post("/correctors")
def create_corrector(body: CorrectorIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if "@" not in email or " " in email: raise HTTPException(422, "Adresse e-mail invalide")
    if db.query(User).filter_by(email=email).first(): raise HTTPException(409, "Adresse déjà utilisée")
    user = User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password), role="corrector")
    db.add(user); db.commit(); return {"id": user.id}


# Professeurs autonomes : hors de l'onglet Établissements, ils ne sont invités
# qu'ici et choisissent ensuite eux-mêmes leur programme et leur matière.

class AutonomousTeacherIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/autonomous-teachers")
def pending_autonomous_teachers(db: Session = Depends(get_db)):
    """Fiches en attente d'inscription : une fois le compte ouvert, elles sont dans la liste des comptes."""
    rows = db.query(TeacherProfile).filter_by(service_mode="autonomous", user_id=None, active=True)
    return [{"id": t.id, "name": t.name} for t in rows.order_by(TeacherProfile.name)]


@router.post("/autonomous-teachers")
def create_autonomous_teacher(body: AutonomousTeacherIn, db: Session = Depends(get_db),
                              user: User = Depends(require_role("admin"))):
    name = body.name.strip()
    if not name: raise HTTPException(422, "Nom requis")
    teacher = TeacherProfile(name=name, service_mode="autonomous")
    db.add(teacher); db.flush()
    return issue_invitation(db, teacher, user)


@router.post("/autonomous-teachers/{teacher_id}/invitation")
def renew_autonomous_invitation(teacher_id: str, db: Session = Depends(get_db),
                                user: User = Depends(require_role("admin"))):
    teacher = db.get(TeacherProfile, teacher_id)
    if not teacher or teacher.service_mode != "autonomous": raise HTTPException(404, "Professeur inconnu")
    return issue_invitation(db, teacher, user)


class AccountStatusIn(BaseModel):
    active: bool


@router.patch("/{user_id}/status")
def account_status(user_id: str, body: AccountStatusIn, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user: raise HTTPException(404)
    if user.role == "admin": raise HTTPException(409, "Le compte administrateur ne peut pas être suspendu ici")
    user.active = body.active
    db.commit(); return {"ok": True}

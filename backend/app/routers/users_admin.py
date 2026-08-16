"""Administration des comptes MathPrint — API réservée aux admins."""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import User


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

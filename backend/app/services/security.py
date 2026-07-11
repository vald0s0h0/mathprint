"""Pseudonymisation (RM-010), signature HMAC des pages (§5.4) et JWT."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from ..config import settings


def hash_password(p: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(p: str, h: str) -> bool:
    try:
        _, salt, digest = h.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(candidate, digest)


def make_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def new_pseudonym() -> str:
    """Identifiant technique type E-7F3A — seule identité envoyée aux API."""
    return "E-" + secrets.token_hex(2).upper()


# --- QR pages : payload opaque signé, sans nom/classe/note (§5.4) ---

def sign_page(page_id: str) -> str:
    sig = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"MP1|{page_id}|{sig}"


def verify_page_payload(payload: str) -> str | None:
    """Retourne le page_id si la signature est valide, sinon None."""
    parts = payload.split("|")
    if len(parts) != 3 or parts[0] != "MP1":
        return None
    page_id, sig = parts[1], parts[2]
    expected = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).hexdigest()[:16]
    return page_id if hmac.compare_digest(sig, expected) else None

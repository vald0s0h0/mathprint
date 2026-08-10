"""Pseudonymisation (RM-010), signature HMAC des pages (§5.4) et JWT."""
import base64
import binascii
import hashlib
import hmac
import secrets
import uuid
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

_QR_SIGNATURE_BYTES = 8  # 64 bits, identique aux 16 caractères hexadécimaux MP1


def _b32_encode(value: bytes) -> str:
    """Base32 sans remplissage : alphabet majuscule compatible avec le mode
    alphanumérique natif des QR, donc moins de modules que du Base64/hex UUID."""
    return base64.b32encode(value).decode("ascii").rstrip("=")


def _b32_decode(value: str) -> bytes:
    padding = "=" * ((8 - len(value) % 8) % 8)
    return base64.b32decode(value + padding, casefold=False)

def sign_page(page_id: str) -> str:
    """Produit le format compact MP2.

    Les UUID de page occupent 26 caractères Base32 au lieu des 36 caractères
    textuels. La signature reste un HMAC-SHA256 tronqué à 64 bits, exactement
    la même entropie que les 16 caractères hexadécimaux du format MP1. Les
    identifiants non UUID restent pris en charge (calibration et tests).
    """
    try:
        canonical = str(uuid.UUID(page_id))
        page_token = _b32_encode(uuid.UUID(canonical).bytes)
    except (ValueError, AttributeError, TypeError):
        canonical = page_id
        page_token = "T" + _b32_encode(page_id.encode("utf-8"))
    digest = hmac.new(settings.hmac_key.encode(), canonical.encode(), hashlib.sha256).digest()
    return f"M2:{page_token}:{_b32_encode(digest[:_QR_SIGNATURE_BYTES])}"


def verify_page_payload(payload: str) -> str | None:
    """Retourne le page_id si la signature est valide, sinon None.

    MP1 reste accepté sans limite de durée : une copie déjà imprimée doit
    toujours pouvoir être scannée après le déploiement de MP2.
    """
    if payload.startswith("M2:"):
        try:
            prefix, page_token, sig = payload.split(":")
            if prefix != "M2":
                return None
            # Essayer l'UUID EN PREMIER. Un token Base32 d'UUID peut commencer
            # naturellement par « T » (~1 cas sur 32) ; l'ancien ordre le
            # prenait alors à tort pour le marqueur des identifiants texte et
            # rejetait une page pourtant correctement signée.
            try:
                raw_id = _b32_decode(page_token)
            except binascii.Error:
                raw_id = b""
            if len(raw_id) == 16:
                page_id = str(uuid.UUID(bytes=raw_id))
            elif page_token.startswith("T"):
                page_id = _b32_decode(page_token[1:]).decode("utf-8")
            else:
                return None
            digest = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).digest()
            expected = _b32_encode(digest[:_QR_SIGNATURE_BYTES])
            return page_id if hmac.compare_digest(sig, expected) else None
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None

    parts = payload.split("|")
    if len(parts) != 3 or parts[0] != "MP1":
        return None
    page_id, sig = parts[1], parts[2]
    expected = hmac.new(settings.hmac_key.encode(), page_id.encode(), hashlib.sha256).hexdigest()[:16]
    return page_id if hmac.compare_digest(sig, expected) else None

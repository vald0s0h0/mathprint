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

SESSION_TOKEN_HEADER = "X-Session-Token"
SESSION_RENEW_AFTER = timedelta(days=1)


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


def _password_stamp(password_hash: str) -> str:
    """Empreinte du mot de passe portée par le jeton : la changer invalide les
    sessions ouvertes ailleurs. HMAC : le jeton ne révèle rien du hash."""
    return hmac.new(settings.secret_key.encode(), password_hash.encode(), hashlib.sha256).hexdigest()[:16]


def make_token(user) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "role": user.role,
        "pwd": _password_stamp(user.password_hash),
        "iat": now,
        "exp": now + timedelta(hours=settings.session_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def token_matches_password(payload: dict, user) -> bool:
    return hmac.compare_digest(str(payload.get("pwd", "")), _password_stamp(user.password_hash))


def token_needs_renewal(payload: dict) -> bool:
    """Un renouvellement par jour d'utilisation suffit à garder la session vivante."""
    issued = datetime.fromtimestamp(payload["iat"], timezone.utc)
    return datetime.now(timezone.utc) - issued >= SESSION_RENEW_AFTER


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

# --- Code de page à 20 chiffres (DataMatrix) -------------------------------
#
# Le code imprimé sur chaque page est un DataMatrix de 8 rangées : 20 chiffres
# au plus. L'identifiant de page lui-même est un numéro aléatoire de 12 chiffres
# (`new_page_id`) suivi d'une clé de contrôle de 8 chiffres (HMAC tronqué) :
# la vérification reste donc SANS ÉTAT (pas de table de correspondance), comme
# les anciens QR. 10^12 identifiants × 10^8 clés : une page inventée n'est
# reconnue qu'avec une probabilité de 10^-8, et la clé détecte aussi une lecture
# optique erronée.

PAGE_ID_DIGITS = 12
PAGE_CHECK_DIGITS = 8
PAGE_CODE_LEN = PAGE_ID_DIGITS + PAGE_CHECK_DIGITS


def new_page_id() -> str:
    """Identifiant d'une nouvelle page : 12 chiffres aléatoires."""
    return f"{secrets.randbelow(10 ** PAGE_ID_DIGITS):0{PAGE_ID_DIGITS}d}"


def is_code_page_id(page_id: str | None) -> bool:
    """Vrai pour un identifiant de page à 12 chiffres (mise en page à code DataMatrix)."""
    return bool(page_id) and len(page_id) == PAGE_ID_DIGITS and page_id.isascii() and page_id.isdigit()


def is_code_payload(payload: str | None) -> bool:
    return bool(payload) and len(payload) == PAGE_CODE_LEN and payload.isascii() and payload.isdigit()


def _page_check(page_id: str) -> str:
    digest = hmac.new(settings.hmac_key.encode(), f"P3:{page_id}".encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 10 ** PAGE_CHECK_DIGITS:0{PAGE_CHECK_DIGITS}d}"


def code_payload_for(page_id: str) -> str:
    """Les 20 chiffres imprimés pour un identifiant de page à 12 chiffres."""
    return page_id + _page_check(page_id)


def printable_code(payload: str) -> str:
    """Les 20 chiffres à imprimer pour `payload`. Un payload déjà au format code
    est repris tel quel ; un ancien payload (M2/MP1, page de calibration, jeu de
    test) est ramené à un identifiant fictif stable : la page reste imprimable
    et vérifiable, mais ne correspond à aucune page de la base."""
    if is_code_payload(payload):
        return payload
    fake = f"{int(hashlib.sha256((payload or '').encode()).hexdigest(), 16) % 10 ** PAGE_ID_DIGITS:0{PAGE_ID_DIGITS}d}"
    return code_payload_for(fake)


def sign_page(page_id: str) -> str:
    """Produit le format compact MP2.

    Les UUID de page occupent 26 caractères Base32 au lieu des 36 caractères
    textuels. La signature reste un HMAC-SHA256 tronqué à 64 bits, exactement
    la même entropie que les 16 caractères hexadécimaux du format MP1. Les
    identifiants non UUID restent pris en charge (calibration et tests).

    Un identifiant de page à 12 chiffres (pages créées depuis la mise en page à
    DataMatrix) produit le code à 20 chiffres de `code_payload_for`.
    """
    if is_code_page_id(page_id):
        return code_payload_for(page_id)
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

    Trois formats : code à 20 chiffres (DataMatrix, identifiant à 12 chiffres +
    clé de contrôle), M2 compact et MP1 historiques (QR, copies déjà imprimées).

    MP1 reste accepté sans limite de durée : une copie déjà imprimée doit
    toujours pouvoir être scannée après le déploiement de MP2.
    """
    if is_code_payload(payload):
        page_id, check = payload[:PAGE_ID_DIGITS], payload[PAGE_ID_DIGITS:]
        return page_id if hmac.compare_digest(check, _page_check(page_id)) else None

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

"""Limitation des tentatives de connexion (application exposée sur Internet).

Deux compteurs d'échecs sur une fenêtre glissante : par compte (e-mail) et par
adresse IP. Au-delà du seuil, la connexion est refusée (429) pendant la durée
de blocage, avant toute vérification du mot de passe. Un succès remet à zéro
le compteur du compte.

En mémoire : l'API tourne dans un seul processus uvicorn ; un redémarrage
remet les compteurs à zéro, ce qui reste acceptable pour ce rôle.
"""
import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

WINDOW_S = 15 * 60
LOCK_S = 15 * 60
MAX_FAILURES_PER_EMAIL = 8   # bas : protège un compte précis
MAX_FAILURES_PER_IP = 30     # plus haut : une salle des profs partage une IP

_lock = threading.Lock()
_failures: dict[tuple[str, str], deque[float]] = {}
_locked_until: dict[tuple[str, str], float] = {}


def client_ip(request: Request | None) -> str:
    """IP du visiteur : le premier X-Forwarded-For posé par Caddy (hermes),
    relayé par le nginx du NAS ; sinon l'adresse de la connexion."""
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _keys(email: str, ip: str) -> list[tuple[tuple[str, str], int]]:
    keys = [(("email", email), MAX_FAILURES_PER_EMAIL)]
    if ip:
        keys.append((("ip", ip), MAX_FAILURES_PER_IP))
    return keys


def check_allowed(email: str, ip: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        wait = max((_locked_until.get(k, 0) - now for k, _ in _keys(email, ip)), default=0)
    if wait > 0:
        minutes = max(1, math.ceil(wait / 60))
        raise HTTPException(
            429, f"Trop de tentatives de connexion. Réessayez dans {minutes} min.",
            headers={"Retry-After": str(math.ceil(wait))})


def record_failure(email: str, ip: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        for key, limit in _keys(email, ip):
            hits = _failures.setdefault(key, deque())
            hits.append(now)
            while hits and hits[0] <= now - WINDOW_S:
                hits.popleft()
            if len(hits) >= limit:
                _locked_until[key] = now + LOCK_S
                hits.clear()


def record_success(email: str) -> None:
    with _lock:
        _failures.pop(("email", email), None)
        _locked_until.pop(("email", email), None)


def reset() -> None:
    """Remise à zéro complète (tests)."""
    with _lock:
        _failures.clear()
        _locked_until.clear()

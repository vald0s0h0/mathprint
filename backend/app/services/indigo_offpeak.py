"""« Cheap and Wait » — n'appeler DeepSeek que pendant ses heures creuses.

Sert au mode « QCM multipass » (services.indigo_multipass), qui consomme cinq
appels par exercice source : la case permet de créer les jobs tout de suite
mais de ne payer les appels qu'aux heures où DeepSeek facture moitié prix.

La plage est CODÉE EN DUR : les heures creuses de DeepSeek ne bougent pas, il
n'y a donc rien à régler. Heures PLEINES (tarif normal) : 01:00–04:00 et
06:00–10:00 UTC, du lundi au vendredi. Tout le reste — nuits, fin de matinée,
après-midi, soirées, et le week-end en entier — est CREUX (tarif moitié prix).

DEUX règles :

  1. Seule la case (`enabled`) est un réglage — persistée, modifiable à chaud
     depuis l'onglet Exercices. Décochée, `wait_until_open` rend la main
     immédiatement et la pipeline tourne à l'heure qu'on veut.
  2. On ne COUPE jamais un appel en cours. Le portillon (`wait_until_open`) est
     franchi AVANT de commencer une passe ; une passe entamée à 00:59 va
     jusqu'au bout, et c'est la SUIVANTE qui attend la réouverture.
"""
from __future__ import annotations

import logging
import time as time_mod
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from .runtime_settings import get_setting

logger = logging.getLogger("app.indigo")

SETTING_KEY = "indigo_offpeak"

# Heures PLEINES de DeepSeek, en UTC, du lundi (0) au vendredi (4) inclus —
# tarif normal. Le reste de la semaine (nuits, 04h-06h, 10h-24h, et tout le
# week-end) est à moitié prix. Ces horaires ne sont PAS configurables : ils
# reflètent la grille tarifaire du fournisseur, pas un choix de la plateforme.
PEAK_WINDOWS_UTC = ((time(1, 0), time(4, 0)), (time(6, 0), time(10, 0)))

# Pas de scrutation du portillon fermé. 60 s suffisent à ouvrir « à l'heure »
# sans réveiller le thread pour rien, et un réglage changé pendant l'attente
# (l'admin décoche la case) est donc pris en compte en moins d'une minute.
POLL_S = 60


def _default() -> dict:
    return {"enabled": False}


def get_config(db: Session) -> dict:
    """Réglage courant, normalisé : {enabled}."""
    saved = get_setting(db, SETTING_KEY) or {}
    return {"enabled": bool(saved.get("enabled", _default()["enabled"]))}


def set_config(db: Session, *, enabled: bool | None = None,
               updated_by: str | None = None) -> dict:
    """Persiste la case « Cheap and Wait »."""
    from ..models import SystemSetting
    cur = get_config(db)
    new = {"enabled": cur["enabled"] if enabled is None else bool(enabled)}
    row = db.get(SystemSetting, SETTING_KEY)
    if row is None:
        row = SystemSetting(key=SETTING_KEY)
        db.add(row)
    row.value_json = new
    row.version = (row.version or 0) + 1
    row.updated_by = updated_by
    db.commit()
    return new


def is_peak_hour(now: datetime | None = None) -> bool:
    """Sommes-nous dans une plage PLEINE de DeepSeek (lundi-vendredi, 01h-04h
    ou 06h-10h UTC) ? Un `now` naïf est traité comme déjà en UTC."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:                # samedi, dimanche : toujours creux
        return False
    t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    return any(start <= t < end for start, end in PEAK_WINDOWS_UTC)


def is_open(cfg: dict, now: datetime | None = None) -> bool:
    """Le tarif est-il creux MAINTENANT ? Case décochée = toujours ouvert."""
    if not cfg.get("enabled"):
        return True
    return not is_peak_hour(now)


def next_open(cfg: dict, now: datetime | None = None) -> datetime:
    """Prochain instant creux (== `now` si déjà ouvert)."""
    now = now or datetime.now(timezone.utc)
    if is_open(cfg, now):
        return now
    t = now.time()
    end = PEAK_WINDOWS_UTC[0][1] if t < PEAK_WINDOWS_UTC[1][0] else PEAK_WINDOWS_UTC[1][1]
    return now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)


def wait_until_open(db: Session, *, progress_cb=None,
                    now_fn=lambda: datetime.now(timezone.utc)) -> None:
    """Bloque tant que le tarif est plein.

    Le réglage est RELU à chaque tour : décocher la case pendant l'attente
    libère la file au tour suivant, sans redémarrer l'application. L'appelant
    est le thread de la file Indigo, un démon : à l'arrêt du conteneur il est
    tué avec le processus, il n'y a donc rien à signaler ici.
    """
    announced = False
    while True:
        cfg = get_config(db)
        now = now_fn()
        if is_open(cfg, now):
            if announced and progress_cb:
                progress_cb("⏳ Tarif creux DeepSeek : reprise de la génération.")
            return
        if not announced:
            reopen = next_open(cfg, now)
            if progress_cb:
                progress_cb(f"⏸ Cheap and Wait : heures pleines DeepSeek, les "
                            f"exercices restants sont EN ATTENTE, la génération "
                            f"reprendra automatiquement à "
                            f"{reopen.strftime('%H:%M')} UTC.")
            logger.info("Indigo/multipass : en attente du tarif creux DeepSeek "
                        "(réouverture %s)", reopen.isoformat(timespec="minutes"))
            announced = True
        time_mod.sleep(POLL_S)

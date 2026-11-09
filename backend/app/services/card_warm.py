"""Préchauffage des aperçus de cartes (PNG) de la banque.

Rend à l'avance, en arrière-plan, les cartes que le catalogue affiche, pour que
leur premier affichage lise un fichier au lieu de lancer un rendu de 0,3 à 2 s.

Garde-fous (le NAS est petit et une correction peut tourner à tout moment) :
- calcule d'abord les hauteurs de cartes (assistant « Créer un sujet »), puis les PNG ;
- démarre 90 s après le lancement de l'API, pas pendant le démarrage ;
- UNE carte à la fois, pause entre deux rendus ;
- se met en pause tant qu'une correction/génération est en cours (même test que
  scripts/ship.sh) et reprend ensuite ;
- partage le verrou de rendu avec l'API : une requête utilisateur attend au pire
  la carte en cours ;
- toute erreur est avalée et journalisée : au pire, on retombe sur le rendu à la
  demande, comme avant.
"""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import text

from ..db import SessionLocal

logger = logging.getLogger(__name__)

START_DELAY_S = 90
PAUSE_BETWEEN_S = 0.4
BUSY_RECHECK_S = 60
PASS_INTERVAL_S = 30 * 60
CARD_KEYS = ('statement', 'response_type', 'expected', 'grading', 'choices', 'figure',
             'figure_url', 'title', 'badge_type', 'difficulty', 'level', 'kind', 'is_problem')

_BUSY_SQL = text(
    "SELECT (SELECT count(*) FROM scan_batches WHERE status IN "
    "('uploaded','split','identified','registered','cropped','ocr_complete','ocr_confirmed') "
    "AND error IS NULL AND created_at > now() - interval '3 hours') + "
    "(SELECT count(*) FROM jobs WHERE status IN ('pending','running') "
    "AND coalesce(updated_at, created_at) > now() - interval '30 minutes')")

_started = False


def is_busy(db) -> bool:
    try:
        return int(db.execute(_BUSY_SQL).scalar() or 0) > 0
    except Exception:
        return True      # dans le doute, on ne charge pas le NAS


def card_for_catalog(row) -> dict:
    """La carte telle que le navigateur l'envoie pour une ligne de la banque."""
    from ..routers.content import _exercise_out
    out = _exercise_out(row, None)
    return {k: out[k] for k in CARD_KEYS if k in out}


def warm_pass(sleep=time.sleep, busy=is_busy) -> dict:
    from ..models import GeneratedExercise
    from . import card_preview
    stats = {"rendered": 0, "already": 0, "failed": 0, "paused": 0, "measured": 0}
    card_preview.prune_old_versions()
    db = SessionLocal()
    try:
        ids = [i for (i,) in db.query(GeneratedExercise.id)
               .filter(GeneratedExercise.status == "active").order_by(GeneratedExercise.id)]
        # 1) hauteurs : c'est ce que l'assistant « Créer un sujet » attend
        #    (rapides, et elles réchauffent aussi le cache des formules)
        from .manual_subject import GUIDE_MODES
        from .runtime_settings import doc_templates
        from . import generation
        tpl = doc_templates(db)
        fs = int(tpl["exercise"].get("font_size", 9))
        mfs = int(tpl["exercise"].get("math_size", 9))
        for ex_id in ids:
            try:
                row = db.get(GeneratedExercise, ex_id)
                if row is None:
                    continue
                for mode in GUIDE_MODES:
                    shape = generation.render_shape(row, mode)
                    if card_preview.has_cached_height(shape, fs, mfs, tpl["exercise"]):
                        continue
                    while busy(db):
                        stats["paused"] += 1
                        db.rollback()
                        sleep(BUSY_RECHECK_S)
                    db.rollback()
                    card_preview.cached_height(shape, fs, mfs, tpl["exercise"])
                    stats["measured"] += 1
                    sleep(PAUSE_BETWEEN_S / 2)
            except Exception as exc:
                stats["failed"] += 1
                logger.warning("card_warm hauteur %s: %s", ex_id, exc)
                db.rollback()
        # 2) aperçus PNG de la banque
        for ex_id in ids:
            try:
                row = db.get(GeneratedExercise, ex_id)
                if row is None:
                    continue
                ex = card_preview.resolve_card(card_for_catalog(row), db)
                if card_preview.cached_file(ex, True, True).exists():
                    stats["already"] += 1
                    continue
                while busy(db):
                    stats["paused"] += 1
                    db.rollback()
                    sleep(BUSY_RECHECK_S)
                db.rollback()
                card_preview.render(ex, True, True)
                stats["rendered"] += 1
                sleep(PAUSE_BETWEEN_S)
            except Exception as exc:     # une carte en échec n'arrête pas le reste
                stats["failed"] += 1
                logger.warning("card_warm %s: %s", ex_id, exc)
                db.rollback()
    finally:
        db.close()
    return stats


def _loop() -> None:
    time.sleep(START_DELAY_S)
    while True:
        try:
            stats = warm_pass()
            if stats["rendered"] or stats["measured"] or stats["failed"]:
                logger.info("card_warm: %s", stats)
        except Exception:
            logger.exception("card_warm: passe interrompue")
        time.sleep(PASS_INTERVAL_S)


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="kironina-card-warm").start()

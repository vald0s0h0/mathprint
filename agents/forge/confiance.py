"""Indice de confiance : combien de cartes relire, par format et par matière.

La confiance se gagne et se perd. Un format (QCM, tableau, rédaction…) commence
relu en entier ; tant que les relectures passent, l'échantillon baisse ; au premier
pouce baissé, il revient à 100 %. La relecture humaine devient une mesure, plus un
passage obligé.
"""
from __future__ import annotations

import math
import random

# (cartes acceptées d'affilée, part à relire ensuite)
STEPS = ((30, 1.0), (100, 0.25), (300, 0.10))
FLOOR = 0.05


def rate(streak: int) -> float:
    """Part des cartes à relire après `streak` cartes acceptées d'affilée."""
    for limit, share in STEPS:
        if streak < limit:
            return share
    return FLOOR


def streaks(db, subject: str = "math") -> dict[str, dict]:
    """{format: {relues, acceptées d'affilée, part à relire}} d'après les avis posés
    sur les cartes de la forge. Une famille compte une fois, par sa première jumelle."""
    from app.models import IndigoExercise
    out: dict[str, dict] = {}
    rows = (db.query(IndigoExercise).order_by(IndigoExercise.updated_at, IndigoExercise.id).all())
    for row in rows:
        raw = row.raw_ocr_json or {}
        if raw.get("pipeline") != "forge" or (raw.get("subject") or "math") != subject:
            continue
        twin = (row.grading_json or {}).get("twin")
        if twin and not twin.get("principal"):
            continue
        verdict = raw.get("review_verdict")
        if verdict not in ("like", "dislike"):
            continue
        entry = out.setdefault(row.response_type, {"reviewed": 0, "streak": 0})
        entry["reviewed"] += 1
        entry["streak"] = entry["streak"] + 1 if verdict == "like" else 0
    for entry in out.values():
        entry["rate"] = rate(entry["streak"])
    return out


def sample(run_name: str, cards: dict[str, str], flagged: set[str], known: dict[str, dict],
           pilot: bool = False) -> dict[str, str]:
    """{carte: raison} des cartes à relire. `cards` : {clé: format} des cartes du run,
    une par famille. Les cartes signalées par le lecteur sont toujours relues."""
    out = {key: "signalée par le lecteur" for key in sorted(flagged & set(cards))}
    rng = random.Random(f"{run_name}/confiance")
    by_format: dict[str, list[str]] = {}
    for key, rtype in sorted(cards.items()):
        if key not in out:
            by_format.setdefault(rtype, []).append(key)
    for rtype, keys in sorted(by_format.items()):
        share = 1.0 if pilot else (known.get(rtype) or {}).get("rate", 1.0)
        count = len(keys) if share >= 1 else math.ceil(share * len(keys))
        for key in rng.sample(keys, min(count, len(keys))):
            out[key] = ("run pilote" if pilot else
                        f"échantillon de relecture ({round(share * 100)} % des {rtype})")
    return out

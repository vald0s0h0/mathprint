"""Indice de confiance : combien de cartes relire, par matière, format et archétype.

La confiance se gagne et se perd. Une case (un format, ou un format dans un archétype)
commence relue en entier ; tant que les relectures passent, l'échantillon baisse ; au
premier pouce baissé, il revient à 100 %. La relecture humaine devient une mesure,
plus un passage obligé.

La cause du refus dit ce qui perd la confiance : un problème de rendu touche tout le
format ; un fait faux, une règle de matière, une consigne mal suivie ou une pédagogie
faible touchent le format dans cet archétype seulement. Un refus sans cause touche les
deux, par prudence.
"""
from __future__ import annotations

import math
import random

# (cartes acceptées d'affilée, part à relire ensuite)
STEPS = ((10, 1.0), (30, 0.5), (100, 0.25), (300, 0.10))
FLOOR = 0.05

CAUSES = {
    "fait_faux": ("Fait faux ou imprécis", "la leçon"),
    "regle_matiere": ("Règle de la matière non respectée", "le contexte matière"),
    "format_rendu": ("Format, figure ou rendu", "le format ou un contrôle par script"),
    "consigne": ("Consigne de la forge mal suivie", "CONSIGNE-EXERCICES.md"),
    "pedagogie": ("Pédagogie faible", "le contexte universel ou la matière"),
}
FORMAT_CAUSES = {"format_rendu", None}
COMBO_CAUSES = set(CAUSES) | {None}


def rate(streak: int) -> float:
    """Part des cartes à relire après `streak` cartes acceptées d'affilée."""
    for limit, share in STEPS:
        if streak < limit:
            return share
    return FLOOR


def combo(rtype: str, archetype: str | None) -> str:
    return f"{rtype}/{archetype or '-'}"


def streaks(db, subject: str = "math") -> dict[str, dict]:
    """{case: {relues, acceptées d'affilée, part à relire}} d'après les avis posés sur
    les cartes de la forge. Une case est un format (`qcm_single`) ou un format dans un
    archétype (`qcm_single/ERR`). Une famille compte une fois, par sa première jumelle."""
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
        cause = raw.get("review_cause") if verdict == "dislike" else None
        for key, resets in ((row.response_type, FORMAT_CAUSES),
                            (combo(row.response_type, raw.get("archetype")), COMBO_CAUSES)):
            entry = out.setdefault(key, {"reviewed": 0, "streak": 0})
            entry["reviewed"] += 1
            if verdict == "like":
                entry["streak"] += 1
            elif cause in resets:
                entry["streak"] = 0
    for entry in out.values():
        entry["rate"] = rate(entry["streak"])
    return out


def share_for(rtype: str, archetype: str | None, known: dict[str, dict]) -> float:
    """La part à relire d'une carte : la plus prudente du format et de sa case."""
    by_format = (known.get(rtype) or {}).get("rate", 1.0)
    by_combo = (known.get(combo(rtype, archetype)) or {}).get("rate")
    # Une case jamais relue hérite du format : sa mise en page est connue, son
    # archétype gagnera sa propre confiance au fil des relectures.
    return by_format if by_combo is None else max(by_format, by_combo)


def sample(run_name: str, cards: dict, flagged: set[str], known: dict[str, dict],
           pilot: bool = False) -> dict[str, str]:
    """{carte: raison} des cartes à relire. `cards` : {clé: format} ou {clé: (format,
    archétype)}, une par famille. Les cartes signalées sont toujours relues."""
    out = {key: "signalée par un lecteur" for key in sorted(flagged & set(cards))}
    rng = random.Random(f"{run_name}/confiance")
    by_case: dict[tuple, list[str]] = {}
    for key, value in sorted(cards.items()):
        if key in out:
            continue
        rtype, archetype = value if isinstance(value, (tuple, list)) else (value, None)
        by_case.setdefault((rtype, archetype), []).append(key)
    for (rtype, archetype), keys in sorted(by_case.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        share = 1.0 if pilot else share_for(rtype, archetype, known)
        count = len(keys) if share >= 1 else math.ceil(share * len(keys))
        label = rtype + (f" {archetype}" if archetype else "")
        for key in rng.sample(keys, min(count, len(keys))):
            out[key] = ("run pilote" if pilot else
                        f"échantillon de relecture ({round(share * 100)} % des {label})")
    return out

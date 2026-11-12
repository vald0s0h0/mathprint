"""Consensus déterministe entre moteurs OCR locaux (fonctions pures, sans base).

Règles non négociables :
- aucune réponse n'est acceptée si aucun moteur ne l'a produite librement : le
  score ne CRÉE jamais de candidat, il ne peut que retirer son accord ;
- la valeur attendue n'apparaît nulle part ici ;
- au moindre doute, la découpe part vers Mathpix puis en manuel ;
- les moteurs `observe` du plan ne votent jamais et n'influencent aucune décision.

Étapes (`decide_local`) :
  1. les moteurs `stage1` lisent. Accord de deux familles différentes, au-dessus de
     leurs seuils, garde-fou encre/longueur passé -> `local_consensus`.
  2. sinon les moteurs `stage2` lisent. Une surface gagne si elle a au moins 2 voix
     (une voix par famille) et qu'aucune autre n'en a 2 ; puis un score forcé de
     chaque évaluateur, sur les seules surfaces déjà lues, peut retirer l'accord.
  3. sinon Mathpix (`decide_mathpix`) : sa surface doit égaler une surface lue
     localement, sans autre surface à 2 voix, avec une confiance suffisante.
  4. sinon `manual`.
Une paire de familles corrélées (plan.correlated_pairs) ne compte pour deux voix
que si aucun moteur d'une autre famille ne lit une autre surface.
"""
from dataclasses import dataclass, field

from .ocr_surface import to_surface

DEFAULT_SCORE_MARGIN = 2.0


@dataclass(frozen=True)
class ReadIn:
    engine: str
    family: str
    raw: str
    surface: str
    illegible: bool = False
    confidence_raw: float | None = None
    logprob_mean: float | None = None
    logprob_min: float | None = None
    role: str = "observe"            # stage1 | stage2 | observe


@dataclass(frozen=True)
class CropCtx:
    zone_class: str
    ink_width_px: int = 0
    height_px: int = 0


@dataclass
class Outcome:
    # accept | need_stage2 | need_score | mathpix | manual
    action: str
    reason: str = ""
    surface: str | None = None
    candidates: list = field(default_factory=list)       # [{surface, voices, families, engines}]
    score_surfaces: list = field(default_factory=list)   # surfaces à faire évaluer (need_score)
    supporters: list = field(default_factory=list)       # moteurs qui ont lu la surface gagnante


# ----------------------------------------------------------------- briques

def has_text(r: ReadIn) -> bool:
    return not r.illegible and bool(r.surface.strip())


def passes_threshold(r: ReadIn, thresholds: dict) -> bool:
    """Seuils propres au moteur. Une mesure absente alors qu'un seuil existe : refus."""
    t = thresholds.get(r.engine, {})
    if "min_confidence" in t and (r.confidence_raw is None or r.confidence_raw < t["min_confidence"]):
        return False
    if "min_logprob_mean" in t and (r.logprob_mean is None or r.logprob_mean < t["min_logprob_mean"]):
        return False
    if "min_logprob_min" in t and (r.logprob_min is None or r.logprob_min < t["min_logprob_min"]):
        return False
    return True


def is_valid(r: ReadIn, thresholds: dict) -> bool:
    return has_text(r) and passes_threshold(r, thresholds)


def ink_guard_ok(surface: str, ctx: CropCtx, thresholds: dict) -> bool:
    """Le nombre de caractères est plausible au vu de la largeur d'encre. Si la
    géométrie est inconnue, on ne peut pas conclure : refus."""
    g = thresholds.get("ink_guard", {})
    n = len([c for c in surface if not c.isspace()])
    if n == 0 or ctx.ink_width_px <= 0 or ctx.height_px <= 0:
        return False
    per_char = ctx.ink_width_px / ctx.height_px / n
    return g.get("min_per_char", 0.12) <= per_char <= g.get("max_per_char", 0.45)


def voting(reads: list[ReadIn]) -> list[ReadIn]:
    return [r for r in reads if r.role in ("stage1", "stage2")]


def _pairs(plan: dict) -> list[frozenset]:
    return [frozenset(p) for p in plan.get("correlated_pairs", [])]


def _class_voting_families(plan: dict, zone_class: str) -> set[str]:
    roles = (plan.get("classes") or {}).get(zone_class, {})
    fam = plan.get("families", {})
    return {fam[e] for role in ("stage1", "stage2") for e in roles.get(role, []) if e in fam}


def tally(reads: list[ReadIn], plan: dict, thresholds: dict, zone_class: str) -> list[dict]:
    """Surfaces lues par les moteurs qui votent, avec leurs voix. Triées : plus
    de voix d'abord, puis ordre alphabétique (déterministe)."""
    vr = voting(reads)
    by: dict[str, dict] = {}
    for r in vr:
        if not is_valid(r, thresholds):
            continue
        c = by.setdefault(r.surface, {"surface": r.surface, "families": set(), "engines": []})
        c["families"].add(r.family)
        c["engines"].append(r.engine)
    all_fams = _class_voting_families(plan, zone_class)
    for c in by.values():
        voices = len(c["families"])
        for pair in _pairs(plan):
            if pair <= c["families"]:
                other = all_fams - pair
                # lit une AUTRE surface : lecture exploitable (texte non vide) d'une autre famille
                dissent = any(r.family not in pair and has_text(r) and r.surface != c["surface"]
                              for r in vr)
                if dissent or not other:
                    voices -= 1                # la paire ne compte que pour une voix
        c["voices"] = voices
        c["families"] = sorted(c["families"])
        c["engines"] = sorted(c["engines"])
    return sorted(by.values(), key=lambda c: (-c["voices"], c["surface"]))


def _cands(tallied: list[dict]) -> list[dict]:
    return [{"surface": c["surface"], "voices": c["voices"], "families": c["families"],
             "engines": c["engines"]} for c in tallied]


def _illegible_families(reads: list[ReadIn]) -> set[str]:
    return {r.family for r in voting(reads) if not has_text(r)}


# ------------------------------------------------------------------ étapes

def decide_local(ctx: CropCtx, reads: list[ReadIn], plan: dict, thresholds: dict, *,
                 phase: int = 1, scores: dict | None = None,
                 margin: float = DEFAULT_SCORE_MARGIN) -> Outcome:
    """`phase=1` : seules les lectures stage1 sont disponibles. `phase=2` : stage1 et
    stage2. `scores` : {moteur: {surface: log-prob}} reçus après un `need_score`."""
    zone_class = ctx.zone_class
    roles = (plan.get("classes") or {}).get(zone_class, {})
    has_stage2 = bool(roles.get("stage2"))
    tallied = tally(reads, plan, thresholds, zone_class)
    cands = _cands(tallied)

    # 5. illisible : au moins 2 familles sans texte exploitable
    if len(_illegible_families(reads)) >= 2:
        return Outcome("manual", "illegible_consensus", candidates=cands)

    if phase == 1:
        s1 = [r for r in reads if r.role == "stage1"]
        seen = [r for r in s1 if has_text(r)]
        if (len(s1) >= 2 and len(seen) == len(s1) and all(is_valid(r, thresholds) for r in s1)
                and len({r.surface for r in s1}) == 1):
            top = tallied[0] if tallied else None
            unverified_pair = bool(top) and any(
                pair <= set(top["families"]) and not any(
                    r.family not in pair for r in voting(reads)) for pair in _pairs(plan))
            if (top and not unverified_pair and top["voices"] >= 2 and len({r.family for r in s1}) >= 2
                    and ink_guard_ok(top["surface"], ctx, thresholds)):
                return Outcome("accept", "local_consensus", top["surface"], cands, supporters=top["engines"])
        if has_stage2:
            return Outcome("need_stage2", "stage1_not_conclusive", candidates=cands)
        phase = 2                                   # rien à ajouter : on conclut avec stage1

    winners = [c for c in tallied if c["voices"] >= 2]
    if len(winners) != 1:
        return Outcome("mathpix", "no_majority" if not winners else "conflicting_majorities", candidates=cands)
    w = winners[0]
    if not ink_guard_ok(w["surface"], ctx, thresholds):
        return Outcome("mathpix", "ink_guard", candidates=cands)

    # scores forcés : sur les surfaces DÉJÀ LUES (tous moteurs, observateurs compris), jamais d'autre
    read_surfaces = sorted({r.surface for r in reads if has_text(r)})
    class_engines = roles.get("stage1", []) + roles.get("stage2", [])
    scorers = sorted(e for e in plan.get("scorers", []) if e in class_engines)
    if len(read_surfaces) > 1 and scorers and scores is None:
        return Outcome("need_score", "verify_with_scores", w["surface"], cands,
                       score_surfaces=read_surfaces, supporters=w["engines"])
    if scores:
        for engine, table in scores.items():
            if w["surface"] not in table:
                continue
            mine = table[w["surface"]]
            rival = max((v for k, v in table.items()                      # seulement des surfaces DÉJÀ LUES
                         if k != w["surface"] and k in read_surfaces and v is not None), default=None)
            if rival is not None and mine is not None and rival - mine > margin:
                return Outcome("mathpix", f"score_veto:{engine}", w["surface"], cands)
    return Outcome("accept", "local_consensus", w["surface"], cands, supporters=w["engines"])


def full_decision(ctx: CropCtx, reads: list[ReadIn], plan: dict, thresholds: dict,
                  scores: dict | None = None, margin: float = DEFAULT_SCORE_MARGIN) -> dict:
    """Ce qu'aurait décidé le passage complet (tous les moteurs ont lu et évalué), pour
    l'échantillon complet. Les observateurs sont comptés comme lecteurs de stage2 afin
    de mesurer leur apport marginal, sans jamais changer la décision réelle."""
    promoted = [ReadIn(**{**r.__dict__, "role": "stage2" if r.role == "observe" else r.role}) for r in reads]
    plan2 = {**plan, "classes": {**plan.get("classes", {}), ctx.zone_class: {
        **plan["classes"][ctx.zone_class],
        "stage2": sorted(set(plan["classes"][ctx.zone_class].get("stage2", []) +
                             plan["classes"][ctx.zone_class].get("observe", [])))}}}
    out = decide_local(ctx, promoted, plan2, thresholds, phase=2, scores=scores if scores else None, margin=margin)
    return {"action": out.action, "surface": out.surface, "reason": out.reason,
            "scored": scores is not None and bool(scores)}


def decide_mathpix(ctx: CropCtx, reads: list[ReadIn], mathpix_text: str, mathpix_confidence: float,
                   plan: dict, thresholds: dict) -> Outcome:
    """Mathpix ne décide jamais seul : sa surface doit être une surface déjà lue par un
    moteur local qui vote, sans autre surface à 2 voix, avec une confiance suffisante."""
    surface = to_surface(mathpix_text, ctx.zone_class)
    tallied = tally(reads, plan, thresholds, ctx.zone_class)
    cands = _cands(tallied)
    if not surface:
        return Outcome("manual", "mathpix_empty", candidates=cands)
    if mathpix_confidence < thresholds.get("mathpix", {}).get("min_confidence", 0.9):
        return Outcome("manual", "mathpix_low_confidence", surface, cands)
    read_by = [r for r in voting(reads) if has_text(r) and r.surface == surface]
    if not read_by:
        return Outcome("manual", "mathpix_not_read_locally", surface, cands)
    others = [c for c in tallied if c["surface"] != surface and c["voices"] >= 2]
    if others:
        return Outcome("manual", "other_surface_has_two_voices", surface, cands)
    if not ink_guard_ok(surface, ctx, thresholds):
        return Outcome("manual", "ink_guard", surface, cands)
    return Outcome("accept", "mathpix_confirmed", surface, cands, supporters=sorted(r.engine for r in read_by))


def score_candidates_for(engine: str, reads: list[ReadIn], surfaces: list[str]) -> list[str]:
    """Candidats BRUTS pour l'évaluateur `engine` : sa propre forme s'il a lu la surface,
    sinon la forme brute d'un autre moteur (ordre alphabétique : déterministe)."""
    out = []
    for surface in surfaces:
        mine = [r.raw for r in reads if r.engine == engine and r.surface == surface and has_text(r)]
        other = sorted(r.raw for r in reads if r.surface == surface and has_text(r))
        out.append((mine or other)[0])
    return out

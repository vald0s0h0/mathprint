"""Consensus déterministe entre moteurs OCR locaux (fonctions pures, sans base).

Règles non négociables :
- aucune réponse n'est acceptée si aucun moteur ne l'a produite librement : le
  score ne CRÉE jamais de candidat, il ne peut que retirer son accord ;
- deux passages par moteur quand le contexte existe : un passage libre (l'image
  seule) et un passage avec contexte (énoncé et réponse attendue). Un même moteur
  ne vote qu'une fois : si ses deux passages divergent, il s'abstient ;
- ce que le contexte a soufflé (`CropCtx.echo` : la réponse attendue, les nombres
  de l'énoncé, un passage du texte attendu) ne vote que si au moins un moteur l'a
  lu SANS contexte. Lu seulement avec le contexte, c'est un candidat que Mathpix
  (qui ne reçoit jamais de contexte) peut confirmer, jamais une voix ;
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
    context: bool = False            # passage avec contexte (énoncé + réponse attendue)


@dataclass(frozen=True)
class CropCtx:
    zone_class: str
    ink_width_px: int = 0
    height_px: int = 0
    echo: frozenset = frozenset()    # surfaces soufflées par le contexte (voir ocr_context)
    echo_text: str = ""              # texte du contexte, pour les classes de texte (sous-chaîne)


def is_echo(surface: str, ctx: CropCtx) -> bool:
    """La surface a-t-elle pu être soufflée par le contexte ?"""
    if not surface:
        return False
    if surface in ctx.echo:
        return True
    return len(surface) >= 4 and bool(ctx.echo_text) and surface in ctx.echo_text


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


@dataclass(frozen=True)
class Opinion:
    """Ce qu'UN moteur dit d'une découpe, ses deux passages confondus."""
    engine: str
    family: str
    role: str
    surface: str | None          # None : abstention (rien d'exploitable, ou deux passages divergents)
    free: bool = False           # le passage libre a produit cette surface (au-dessus du seuil)
    split: bool = False          # passages libre et avec contexte valides mais différents


def opinions(reads: list[ReadIn], thresholds: dict) -> list[Opinion]:
    """Une opinion par moteur (ordre alphabétique). Passage libre et passage avec contexte
    d'accord : une voix ; un seul exploitable : une voix ; en désaccord : abstention."""
    by: dict[str, dict] = {}
    for r in reads:
        by.setdefault(r.engine, {})["ctx" if r.context else "free"] = r
    out = []
    for engine in sorted(by):
        free, ctxr = by[engine].get("free"), by[engine].get("ctx")
        ref = free or ctxr
        fv = free is not None and is_valid(free, thresholds)
        cv = ctxr is not None and is_valid(ctxr, thresholds)
        if fv and cv and free.surface != ctxr.surface:
            out.append(Opinion(engine, ref.family, ref.role, None, split=True))
        elif fv:
            out.append(Opinion(engine, ref.family, ref.role, free.surface, free=True))
        elif cv:
            out.append(Opinion(engine, ref.family, ref.role, ctxr.surface, free=False))
        else:
            out.append(Opinion(engine, ref.family, ref.role, None))
    return out


def _free_producers(reads: list[ReadIn], surface: str) -> set[str]:
    """Familles qui votent et dont le passage LIBRE a lu cette surface (même sous le seuil :
    l'image seule la suggère)."""
    return {r.family for r in voting(reads) if not r.context and has_text(r) and r.surface == surface}


def tally(reads: list[ReadIn], plan: dict, thresholds: dict, zone_class: str,
          ctx: CropCtx | None = None) -> list[dict]:
    """Surfaces lues par les moteurs qui votent, avec leurs voix. Triées : plus
    de voix d'abord, puis ordre alphabétique (déterministe). Une surface soufflée
    par le contexte que personne n'a lue librement reste candidate à 0 voix."""
    ctx = ctx or CropCtx(zone_class)
    vr = voting(reads)
    by: dict[str, dict] = {}
    for op in opinions(vr, thresholds):
        if op.surface is None:
            continue
        c = by.setdefault(op.surface, {"surface": op.surface, "families": set(), "engines": []})
        c["families"].add(op.family)
        c["engines"].append(op.engine if op.free else f"{op.engine}+ctx")
    # surfaces lues seulement avec le contexte, par un moteur qui s'abstient : candidates sans voix
    for r in vr:
        if r.context and is_valid(r, thresholds) and r.surface not in by:
            by[r.surface] = {"surface": r.surface, "families": set(), "engines": []}
    all_fams = _class_voting_families(plan, zone_class)
    for c in by.values():
        voices = len(c["families"])
        c["echo"] = is_echo(c["surface"], ctx)
        if c["echo"] and not _free_producers(reads, c["surface"]):
            voices = 0                       # seul le contexte l'a soufflée
        for pair in _pairs(plan):
            if voices and pair <= c["families"]:
                other = all_fams - pair
                # lit une AUTRE surface : lecture exploitable (texte non vide) d'une autre famille,
                # dans l'un ou l'autre de ses passages
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
             "engines": c["engines"], **({"echo": True} if c.get("echo") else {})} for c in tallied]


def _illegible_families(reads: list[ReadIn]) -> set[str]:
    """Familles dont AUCUN passage n'a produit de texte exploitable."""
    seen = {r.family for r in voting(reads) if has_text(r)}
    return {r.family for r in voting(reads)} - seen


# ------------------------------------------------------------------ étapes

def decide_local(ctx: CropCtx, reads: list[ReadIn], plan: dict, thresholds: dict, *,
                 phase: int = 1, scores: dict | None = None,
                 margin: float = DEFAULT_SCORE_MARGIN) -> Outcome:
    """`phase=1` : seules les lectures stage1 sont disponibles. `phase=2` : stage1 et
    stage2. `scores` : {moteur: {surface: log-prob}} reçus après un `need_score`."""
    zone_class = ctx.zone_class
    roles = (plan.get("classes") or {}).get(zone_class, {})
    has_stage2 = bool(roles.get("stage2"))
    tallied = tally(reads, plan, thresholds, zone_class, ctx)
    cands = _cands(tallied)

    # 5. illisible : au moins 2 familles sans texte exploitable
    if len(_illegible_families(reads)) >= 2:
        return Outcome("manual", "illegible_consensus", candidates=cands)

    if phase == 1:
        s1 = [op for op in opinions([r for r in reads if r.role == "stage1"], thresholds)]
        if (len(s1) >= 2 and all(op.surface is not None for op in s1)
                and len({op.surface for op in s1}) == 1):
            top = tallied[0] if tallied else None
            unverified_pair = bool(top) and any(
                pair <= set(top["families"]) and not any(
                    r.family not in pair for r in voting(reads)) for pair in _pairs(plan))
            # l'étape 1 ne conclut que sur des lectures LIBRES : une voix qui ne tient qu'au passage
            # avec contexte demande l'avis de l'étape 2 (ou des scores, qui lisent l'image seule)
            ctx_only = not all(op.free for op in s1)
            if (top and not unverified_pair and not ctx_only and top["voices"] >= 2
                    and len({op.family for op in s1}) >= 2
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
    moteur local qui vote (passage libre ou avec contexte : Mathpix, lui, ne reçoit jamais
    de contexte, c'est la lecture indépendante), sans autre surface à 2 voix, avec une
    confiance suffisante."""
    surface = to_surface(mathpix_text, ctx.zone_class)
    tallied = tally(reads, plan, thresholds, ctx.zone_class, ctx)
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

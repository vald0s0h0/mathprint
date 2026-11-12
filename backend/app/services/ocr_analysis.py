"""Diagnostic de la lecture OCR multicouche : rapport, rejeu, export.

Lecture seule. Requêtes SQL AGRÉGÉES (GROUP BY, CASE) : jamais de boucle ORM ligne par ligne,
le NAS a déjà basculé en swap à cause de cela. Les seuls parcours Python portent sur des
colonnes scalaires (latences, JSON de comparaison, lectures de l'échantillon complet) et sur les
comptes déjà agrégés. Les percentiles se lisent par `OFFSET` (portable SQLite/PostgreSQL).

Mesure ce qui compte : combien d'erreurs sont passées en automatique (`auto_error`), quels moteurs
sont utiles (apport marginal sur l'échantillon complet), quels réglages ajuster (raisons de renvoi),
et ce que coûte Mathpix.
"""
import math
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..models import Copy, CopyItem, OcrCrop, OcrDecision, OcrEngineRead, OcrJob, ResponseZone, ScanBatch
from . import ocr_fusion as fusion
from . import runtime_settings as rs

AUTO = ("local_consensus", "mathpix_confirmed")
GO_MIN_AUDITS = 600           # règle de trois : 0 erreur sur 600 -> borne haute ~0,5 %
GO_MAX_UPPER = 0.005


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    """Intervalle de Wilson d'une proportion k/n (95 %). n = 0 : tout est inconnu."""
    if n <= 0:
        return {"n": 0, "k": 0, "rate": None, "low": None, "high": None}
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"n": n, "k": k, "rate": round(p, 6), "low": round(max(0.0, centre - half), 6),
            "high": round(min(1.0, centre + half), 6)}


def _dfilters(since, mode, zone_class):
    """Conditions communes sur (ocr_decisions d, ocr_crops c)."""
    conds = [OcrDecision.decision != "mathpix_pending"]
    if since is not None:
        conds.append(OcrDecision.created_at >= since)
    if mode:
        conds.append(OcrDecision.mode == mode)
    if zone_class:
        conds.append(OcrCrop.zone_class == zone_class)
    return conds


def _sum(cond):
    return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)


# ------------------------------------------------------------------ sections

def _volume(db, f) -> dict:
    q = (select(OcrCrop.zone_class, OcrDecision.mode, OcrDecision.decision, func.count())
         .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
         .where(*f).group_by(OcrCrop.zone_class, OcrDecision.mode, OcrDecision.decision))
    by_class: dict = {}
    for zc, mode, dec, n in db.execute(q):
        c = by_class.setdefault(zc, {"total": 0, "by_decision": {}, "by_mode": {}})
        c["total"] += n
        c["by_decision"][dec] = c["by_decision"].get(dec, 0) + n
        c["by_mode"][mode] = c["by_mode"].get(mode, 0) + n
    total = sum(c["total"] for c in by_class.values())
    mix: dict = {}
    for c in by_class.values():
        for k, v in c["by_decision"].items():
            mix[k] = mix.get(k, 0) + v
    return {"total": total, "mix": mix,
            "mix_share": {k: round(v / total, 4) for k, v in mix.items()} if total else {},
            "by_class": by_class}


def _auto_errors(db, f, limit=200) -> dict:
    q = (select(OcrDecision.crop_id, OcrDecision.zone_id, OcrDecision.batch_id, OcrCrop.zone_class,
                OcrDecision.decision, OcrDecision.accepted_surface, OcrDecision.truth_surface,
                OcrDecision.truth_source, OcrDecision.mode, OcrDecision.reason, OcrDecision.truth_at)
         .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
         .where(*f, OcrDecision.auto_error.is_(True)).order_by(OcrDecision.truth_at.desc()).limit(limit))
    rows = [dict(zip(("crop_id", "zone_id", "batch_id", "zone_class", "decision", "accepted_surface",
                      "truth_surface", "truth_source", "mode", "reason", "truth_at"), r))
            for r in db.execute(q)]
    for r in rows:
        r["truth_at"] = str(r["truth_at"]) if r["truth_at"] else None
    n = db.execute(select(func.count()).select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
                   .where(*f, OcrDecision.auto_error.is_(True))).scalar() or 0
    return {"count": n, "shown": len(rows), "items": rows}


def _go_no_go(db, f) -> dict:
    """Par classe : de quoi décider du passage en `cascade` (voir LOTS_9_10.md)."""
    is_auto = OcrDecision.decision.in_(AUTO)
    has_truth = OcrDecision.truth_surface.isnot(None)
    q = (select(OcrCrop.zone_class,
                func.count(),
                _sum(is_auto), _sum(OcrDecision.decision == "mathpix_confirmed"),
                _sum(OcrDecision.decision == "manual"),
                _sum(and_(is_auto, has_truth)), _sum(and_(is_auto, has_truth, OcrDecision.auto_error.is_(True))),
                _sum(and_(is_auto, OcrDecision.truth_source == "audit")),
                _sum(and_(is_auto, OcrDecision.truth_source == "audit", OcrDecision.auto_error.is_(True))))
         .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
         .where(*f).group_by(OcrCrop.zone_class))
    out = {}
    for zc, total, auto, mpx, manual, truth_n, truth_err, audit_n, audit_err in db.execute(q):
        a = wilson(int(audit_err), int(audit_n))
        out[zc] = {
            "decisions": int(total), "automatic": int(auto), "automatic_share": round(auto / total, 4) if total else None,
            "mathpix_confirmed": int(mpx), "manual": int(manual), "manual_share": round(manual / total, 4) if total else None,
            "with_truth": wilson(int(truth_err), int(truth_n)),
            "audited": a,
            "go": bool(audit_n >= GO_MIN_AUDITS and a["high"] is not None and a["high"] < GO_MAX_UPPER),
            "audits_missing": max(0, GO_MIN_AUDITS - int(audit_n)),
        }
    return {"criterion": f"au moins {GO_MIN_AUDITS} décisions automatiques auditées et borne haute de Wilson < {GO_MAX_UPPER:.1%}",
            "by_class": out}


def _reasons(db, f, limit=25) -> list:
    q = (select(OcrDecision.decision, OcrDecision.reason, func.count().label("n"))
         .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id).where(*f)
         .group_by(OcrDecision.decision, OcrDecision.reason).order_by(func.count().desc()).limit(limit))
    return [{"decision": d, "reason": r, "count": n} for d, r, n in db.execute(q)]


def _percentile(db, base_where, col, p) -> float | None:
    n = db.execute(select(func.count(col)).where(*base_where, col.isnot(None))).scalar() or 0
    if not n:
        return None
    off = min(n - 1, max(0, math.ceil(p * n) - 1))
    return db.execute(select(col).where(*base_where, col.isnot(None)).order_by(col).limit(1).offset(off)).scalar()


def _engines(db, f) -> dict:
    """Volume, exactitude contre la vérité, illisible, latence, calibration, par classe et moteur."""
    read_ok = and_(OcrEngineRead.kind == "read", OcrEngineRead.error == "")
    join = (OcrEngineRead.crop_id == OcrCrop.id, OcrDecision.crop_id == OcrCrop.id)
    truth = OcrDecision.truth_surface.isnot(None)
    q = (select(OcrCrop.zone_class, OcrEngineRead.engine, OcrEngineRead.family, OcrEngineRead.device,
                func.count(),
                _sum(OcrEngineRead.illegible.is_(True)),
                _sum(truth),
                _sum(and_(truth, OcrEngineRead.illegible.is_(False), OcrEngineRead.surface == OcrDecision.truth_surface)),
                _sum(and_(truth, OcrEngineRead.surface == "")),
                func.avg(OcrEngineRead.latency_ms))
         .select_from(OcrEngineRead).join(OcrCrop, OcrCrop.id == OcrEngineRead.crop_id)
         .join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
         .where(read_ok, *f).group_by(OcrCrop.zone_class, OcrEngineRead.engine, OcrEngineRead.family, OcrEngineRead.device))
    out: dict = {}
    for zc, eng, fam, dev, n, ill, tn, tok, empty, lat in db.execute(q):
        e = out.setdefault(zc, {}).setdefault(eng, {"family": fam, "devices": [], "reads": 0, "illegible": 0,
                                                    "with_truth": 0, "correct": 0, "empty_with_truth": 0,
                                                    "latency_avg_ms": None})
        e["devices"] = sorted(set(e["devices"] + [dev]))
        e["reads"] += int(n); e["illegible"] += int(ill); e["with_truth"] += int(tn)
        e["correct"] += int(tok); e["empty_with_truth"] += int(empty)
        if lat is not None:
            e["latency_avg_ms"] = round(float(lat), 1)
    for zc, engines in out.items():
        for eng, e in engines.items():
            e["illegible_rate"] = round(e["illegible"] / e["reads"], 4) if e["reads"] else None
            e["accuracy"] = wilson(e["correct"], e["with_truth"])
            where = [OcrEngineRead.engine == eng, read_ok]
            e["latency_p50_ms"] = _lat(db, where, zc, f, 0.50)
            e["latency_p95_ms"] = _lat(db, where, zc, f, 0.95)
            e["calibration"] = _calibration(db, eng, zc, f, read_ok)
    return out


def _lat(db, where, zc, f, p):
    sub = (select(OcrEngineRead.latency_ms).select_from(OcrEngineRead)
           .join(OcrCrop, OcrCrop.id == OcrEngineRead.crop_id).join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
           .where(*where, OcrCrop.zone_class == zc, *f, OcrEngineRead.latency_ms.isnot(None)).subquery())
    col = sub.c.latency_ms
    n = db.execute(select(func.count()).select_from(sub)).scalar() or 0
    if not n:
        return None
    off = min(n - 1, max(0, math.ceil(p * n) - 1))
    v = db.execute(select(col).order_by(col).limit(1).offset(off)).scalar()
    return round(float(v), 1) if v is not None else None


_BUCKETS = (("lp>=-0.05", -0.05), ("-0.05>lp>=-0.2", -0.2), ("-0.2>lp>=-0.5", -0.5), ("-0.5>lp>=-1", -1.0))


def _calibration(db, engine, zc, f, read_ok) -> list:
    """Exactitude par tranche de log-probabilité moyenne (ou de confiance brute pour Apple Vision)."""
    truth = OcrDecision.truth_surface.isnot(None)
    use_conf = db.execute(select(func.count()).select_from(OcrEngineRead).where(
        OcrEngineRead.engine == engine, OcrEngineRead.logprob_mean.isnot(None))).scalar() == 0
    col = OcrEngineRead.confidence_raw if use_conf else OcrEngineRead.logprob_mean
    edges = [("conf>=0.9", 0.9), ("0.9>conf>=0.7", 0.7), ("0.7>conf>=0.5", 0.5)] if use_conf else list(_BUCKETS)
    bucket = case(*[(col >= lo, name) for name, lo in edges], else_="below")
    q = (select(bucket, _sum(truth), _sum(and_(truth, OcrEngineRead.surface == OcrDecision.truth_surface)))
         .select_from(OcrEngineRead).join(OcrCrop, OcrCrop.id == OcrEngineRead.crop_id)
         .join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
         .where(read_ok, OcrEngineRead.engine == engine, OcrCrop.zone_class == zc, col.isnot(None),
                OcrEngineRead.illegible.is_(False), *f).group_by(bucket))
    order = [n for n, _ in edges] + ["below"]
    rows = {b: (int(n), int(k)) for b, n, k in db.execute(q)}
    return [{"bucket": b, "metric": "confidence" if use_conf else "logprob_mean", **wilson(rows[b][1], rows[b][0])}
            for b in order if b in rows]


def _pairs(db, f) -> list:
    """Accord deux à deux et accords À TORT (les deux d'accord sur une surface fausse), par classe."""
    r1, r2 = OcrEngineRead.__table__.alias("r1"), OcrEngineRead.__table__.alias("r2")
    truth = OcrDecision.truth_surface.isnot(None)
    both = and_(r1.c.kind == "read", r2.c.kind == "read", r1.c.error == "", r2.c.error == "",
                r1.c.illegible.is_(False), r2.c.illegible.is_(False), r1.c.surface != "", r2.c.surface != "")
    agree = and_(both, r1.c.surface == r2.c.surface)
    q = (select(OcrCrop.zone_class, r1.c.engine, r2.c.engine, func.count(), _sum(both), _sum(agree),
                _sum(and_(agree, truth, r1.c.surface != OcrDecision.truth_surface)),
                _sum(and_(both, truth, r1.c.surface != OcrDecision.truth_surface,
                          r2.c.surface != OcrDecision.truth_surface)),
                _sum(and_(agree, truth)))
         .select_from(r1).join(r2, and_(r1.c.crop_id == r2.c.crop_id, r1.c.engine < r2.c.engine))
         .join(OcrCrop, OcrCrop.id == r1.c.crop_id).join(OcrDecision, OcrDecision.crop_id == OcrCrop.id)
         .where(*f).group_by(OcrCrop.zone_class, r1.c.engine, r2.c.engine))
    out = []
    for zc, a, b, n, nboth, nagree, wrong_agree, both_wrong, agree_truth in db.execute(q):
        out.append({"class": zc, "a": a, "b": b, "crops": int(n), "both_readable": int(nboth),
                    "agree": int(nagree), "agree_rate": round(nagree / nboth, 4) if nboth else None,
                    "wrong_agree": int(wrong_agree), "both_wrong": int(both_wrong),
                    "wrong_agree_rate": wilson(int(wrong_agree), int(agree_truth))})
    return sorted(out, key=lambda x: (x["class"], x["a"], x["b"]))


def _mathpix(db, f) -> dict:
    cost = db.execute(select(func.coalesce(func.sum(OcrDecision.cost_eur), 0.0),
                             _sum(OcrDecision.mathpix_used.is_(True)),
                             _sum(OcrDecision.montage_id.isnot(None)))
                      .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id).where(*f)).one()
    copies = db.execute(select(func.count(func.distinct(CopyItem.copy_id)))
                        .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
                        .join(ResponseZone, ResponseZone.id == OcrCrop.zone_id)
                        .join(CopyItem, CopyItem.id == ResponseZone.item_id).where(*f)).scalar() or 0
    return {"cost_eur": round(float(cost[0]), 4), "crops_read_by_mathpix": int(cost[1]),
            "crops_via_montage": int(cost[2]), "copies": int(copies),
            "cost_per_copy_eur": round(float(cost[0]) / copies, 5) if copies else None}


def _shadow(db, since, zone_class) -> dict:
    """Mode ombre : la lecture locale face à Mathpix (déjà décisif) et face au professeur."""
    q = select(OcrDecision.full_decision, OcrDecision.truth_surface, OcrDecision.auto_error).where(
        OcrDecision.crop_id.is_(None), OcrDecision.mode == "shadow")
    if since is not None:
        q = q.where(OcrDecision.created_at >= since)
    zones = agree = total = 0
    for fd, _t, _e in db.execute(q):
        if not isinstance(fd, dict) or "agree" not in fd:
            continue
        zones += 1
        for a in fd["agree"]:
            total += 1
            agree += 1 if a else 0
    return {"zones": zones, "compared_items": total, "agree": agree,
            "agreement": wilson(agree, total) if total else None}


def _pending_batches(db, timeout_hours) -> dict:
    rows = db.execute(select(ScanBatch.id, ScanBatch.created_at).where(ScanBatch.status == "ocr_local_pending")).all()
    now = datetime.utcnow()
    ages = [(now - (r[1].replace(tzinfo=None) if r[1] else now)).total_seconds() / 3600 for r in rows]
    return {"waiting": len(rows), "oldest_hours": round(max(ages), 1) if ages else 0.0,
            "over_timeout": sum(1 for a in ages if a >= timeout_hours), "timeout_hours": timeout_hours}


# ------------------------------------------------------- apport marginal

def _load_full_sample(db, f, limit=3000) -> dict:
    """Lectures et scores des découpes de l'échantillon complet DÉJÀ jugées (colonnes seulement)."""
    rows = db.execute(
        select(OcrCrop.id, OcrDecision.full_decision).select_from(OcrDecision)
        .join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
        .where(*f, OcrCrop.full_sample.is_(True)).order_by(OcrDecision.created_at.desc()).limit(limit)).all()
    ids = [cid for cid, fd in rows if isinstance(fd, dict) and "action" in fd]      # passage complet terminé
    return load_crops(db, ids) if ids else {}


def decide_full(crop: dict, plan: dict, th: dict, margin: float, exclude: str | None = None) -> tuple:
    """(action, surface) du passage complet d'une découpe, éventuellement SANS un moteur."""
    zc = crop["zone_class"]
    roles = (plan.get("classes") or {}).get(zc) or {}
    role_of = {e: r for r in ("observe", "stage2", "stage1") for e in roles.get(r, [])}
    reads = [fusion.ReadIn(engine=e, family=fam, raw=raw, surface=surf, illegible=ill or not surf.strip(),
                           confidence_raw=conf, logprob_mean=lpm, logprob_min=lpn, role=role_of.get(e, "observe"))
             for (e, fam, raw, surf, ill, conf, lpm, lpn) in crop["reads"] if e != exclude]
    scores = {e: t for e, t in crop["scores"].items() if e != exclude} or None
    ctx = fusion.CropCtx(zc, crop["ink"] or 0, crop["h"] or 0)
    plan2 = plan
    if exclude:
        classes = {k: {r: [e for e in v if e != exclude] for r, v in vv.items()} if k == zc else vv
                   for k, vv in plan["classes"].items()}
        plan2 = {**plan, "classes": classes}
        if len(classes[zc].get("stage1", [])) < 2:       # le plan exige deux moteurs d'étape 1
            classes[zc]["stage2"] = classes[zc].get("stage2", [])
    out = fusion.full_decision(ctx, reads, plan2, th, scores, margin)
    return out["action"], out["surface"]


def _marginal(db, f) -> dict:
    plan, th = rs.ocr_engine_plan(db), rs.ocr_engine_thresholds(db)
    margin = float(rs.ocr_setting(db, "ocr_score_margin"))
    crops = _load_full_sample(db, f)
    if not crops:
        return {"crops": 0, "engines": {}}
    base = {cid: decide_full(c, plan, th, margin) for cid, c in crops.items()}
    engines = sorted({e for c in crops.values() for (e, *_r) in c["reads"]})
    out = {}
    for eng in engines:
        changed = lost = wrong_after = 0
        for cid, c in crops.items():
            if not any(r[0] == eng for r in c["reads"]):
                continue
            alt = decide_full(c, plan, th, margin, exclude=eng)
            if alt != base[cid]:
                changed += 1
                if base[cid][0] == "accept" and alt[0] != "accept":
                    lost += 1
        out[eng] = {"decisions_changed": changed, "automatic_decisions_lost": lost,
                    "share_changed": round(changed / len(crops), 4)}
    return {"crops": len(crops), "engines": out,
            "note": "décisions qui changeraient si le moteur était retiré (échantillon complet, mêmes réglages)"}


# --------------------------------------------------------------- rapport

def build_report(db: Session, *, since: datetime | None = None, mode: str | None = None,
                 zone_class: str | None = None) -> dict:
    f = _dfilters(since, mode, zone_class)
    timeout = int(rs.ocr_setting(db, "ocr_local_timeout_hours"))
    return {
        "filters": {"since": str(since) if since else None, "mode": mode, "class": zone_class},
        "auto_errors": _auto_errors(db, f),                 # en tête : c'est la mesure qui compte
        "go_no_go": _go_no_go(db, f),
        "volume": _volume(db, f),
        "reasons": _reasons(db, f),
        "engines": _engines(db, f),
        "pairs": _pairs(db, f),
        "marginal": _marginal(db, f),
        "mathpix": _mathpix(db, f),
        "shadow": _shadow(db, since, zone_class),
        "waiting": _pending_batches(db, timeout),
        "settings": {"mode": rs.ocr_mode(db), "audit_share": rs.ocr_setting(db, "ocr_audit_share"),
                     "shadow_audit_share": rs.ocr_setting(db, "ocr_shadow_audit_share"),
                     "full_sample_share": rs.ocr_setting(db, "ocr_full_sample_share")},
    }


# ================================================================== rejeu

def load_crops(db: Session, ids: list[str]) -> dict:
    """Lectures et scores déjà STOCKÉS de ces découpes (colonnes seulement), pour rejouer la fusion."""
    crops: dict = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for cid, zc, w, h in db.execute(select(OcrCrop.id, OcrCrop.zone_class, OcrCrop.ink_width_px,
                                               OcrCrop.height_px).where(OcrCrop.id.in_(part))):
            crops[cid] = {"zone_class": zc, "ink": w, "h": h, "reads": [], "scores": {}}
        q = select(OcrEngineRead.crop_id, OcrEngineRead.engine, OcrEngineRead.family, OcrEngineRead.raw_text,
                   OcrEngineRead.illegible, OcrEngineRead.confidence_raw, OcrEngineRead.logprob_mean,
                   OcrEngineRead.logprob_min, OcrEngineRead.kind, OcrEngineRead.scores_json
                   ).where(OcrEngineRead.crop_id.in_(part), OcrEngineRead.error == "")
        from .ocr_surface import to_surface
        for cid, eng, fam, raw, ill, conf, lpm, lpn, kind, sc in db.execute(q):
            zc = crops[cid]["zone_class"]
            if kind == "read":
                # la surface est TOUJOURS recalculée depuis le texte brut : ocr_surface peut avoir changé
                crops[cid]["reads"].append((eng, fam, raw, to_surface(raw, zc), bool(ill), conf, lpm, lpn))
            elif isinstance(sc, dict):
                crops[cid]["scores"].setdefault(eng, {}).update(
                    {to_surface(k, zc): v for k, v in sc.items() if v is not None})
    return crops


def replay_local(db: Session, *, plan: dict | None = None, thresholds: dict | None = None,
                 margin: float | None = None, since: datetime | None = None, mode: str | None = None,
                 zone_class: str | None = None, limit: int = 20000) -> dict:
    """Rejoue la fusion LOCALE sur les lectures déjà stockées, avec d'autres seuils ou un autre plan :
    aucun moteur relancé, aucun appel Mathpix. Compare à ce que la cascade avait décidé et à la vérité."""
    base_plan, base_th = rs.ocr_engine_plan(db), rs.ocr_engine_thresholds(db)
    plan = plan or base_plan
    rs.validate_ocr_plan(plan)
    th = {k: dict(v) for k, v in base_th.items()}
    for eng, sub in (thresholds or {}).items():
        th.setdefault(eng, {}).update(sub)
    margin = float(margin if margin is not None else rs.ocr_setting(db, "ocr_score_margin"))
    f = _dfilters(since, mode, zone_class)
    rows = db.execute(
        select(OcrCrop.id, OcrCrop.zone_class, OcrDecision.decision, OcrDecision.accepted_surface,
               OcrDecision.truth_surface, OcrDecision.auto_error)
        .select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id).where(*f).limit(limit)).all()
    crops = load_crops(db, [r[0] for r in rows])
    stats: dict = {}
    changed: list = []
    for cid, zc, old_dec, old_surf, truth, old_err in rows:
        c = crops[cid]
        roles = (plan.get("classes") or {}).get(zc) or {}
        role_of = {e: r for r in ("observe", "stage2", "stage1") for e in roles.get(r, [])}
        reads = [fusion.ReadIn(engine=e, family=plan["families"].get(e, fam), raw=raw, surface=surf,
                               illegible=ill or not surf.strip(), confidence_raw=conf, logprob_mean=lpm,
                               logprob_min=lpn, role=role_of.get(e, "observe"))
                 for (e, fam, raw, surf, ill, conf, lpm, lpn) in c["reads"]]
        has_s2 = any(r.role == "stage2" for r in reads)
        ctx = fusion.CropCtx(zc, c["ink"] or 0, c["h"] or 0)
        out = fusion.decide_local(ctx, reads, plan, th, phase=2 if has_s2 else 1,
                                  scores=c["scores"] or None, margin=margin)
        s = stats.setdefault(zc, {"crops": 0, "old_auto": 0, "new_auto": 0, "old_auto_errors": 0,
                                  "new_auto_errors": 0, "new_auto_known": 0, "new_to_mathpix": 0,
                                  "new_manual": 0, "new_incomplete": 0, "auto_lost": 0, "auto_gained": 0})
        s["crops"] += 1
        old_auto = old_dec in AUTO
        new_auto = out.action == "accept"
        s["old_auto"] += int(old_auto); s["new_auto"] += int(new_auto)
        s["old_auto_errors"] += int(bool(old_err))
        if new_auto and truth is not None:
            s["new_auto_known"] += 1
            s["new_auto_errors"] += int(out.surface != truth)
        s["new_to_mathpix"] += int(out.action == "mathpix")
        s["new_manual"] += int(out.action == "manual")
        s["new_incomplete"] += int(out.action in ("need_stage2", "need_score"))   # lectures manquantes au rejeu
        if old_auto and not new_auto:
            s["auto_lost"] += 1
        if new_auto and not old_auto:
            s["auto_gained"] += 1
            if truth is not None and out.surface != truth and len(changed) < 100:
                changed.append({"crop_id": cid, "class": zc, "new_surface": out.surface, "truth": truth})
    return {"settings": {"margin": margin, "thresholds": thresholds or {}, "plan_changed": plan is not base_plan},
            "by_class": stats, "gained_but_wrong": changed}


def missing_jobs(db: Session, engine: str, *, since=None, mode=None, zone_class=None, limit=5000) -> list[str]:
    """Découpes qui n'ont jamais été lues par `engine` (candidates à `--engine X`)."""
    f = _dfilters(since, mode, zone_class)
    have = select(OcrJob.crop_id).where(OcrJob.engine == engine, OcrJob.kind == "read")
    q = (select(OcrCrop.id).select_from(OcrDecision).join(OcrCrop, OcrCrop.id == OcrDecision.crop_id)
         .where(*f, OcrCrop.id.notin_(have)).order_by(OcrCrop.created_at).limit(limit))
    return [r[0] for r in db.execute(q)]

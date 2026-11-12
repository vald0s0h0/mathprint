"""Parité entre machines (Mac MPS / PC CUDA) : mêmes découpes, mêmes lectures ?

Les écarts numériques entre appareils peuvent faire basculer une lecture proche d'un seuil. Avant de
laisser une machine CUDA décider, on mesure sur les MÊMES découpes :
    1. sur chaque machine :  python ocr_worker/scripts/parity.py run --out parity_<machine>.json
    2. ensemble :            python ocr_worker/scripts/parity.py compare parity_mac.json parity_cuda.json
`compare` sort avec un code non nul si un moteur est en dessous de `--min-agreement` (0,98 par défaut) ou si
un écart modifie un chiffre ou une virgule (erreur grave).
Les découpes viennent du banc du lot 0 (`data/ocr_bench`, synthétique + 13 réelles : local, jamais publié).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def digits(s: str) -> str:
    return re.sub(r"[^0-9,.\-−]", "", s or "")


def compare(a: dict, b: dict, min_agreement: float = 0.98) -> dict:
    """Compare deux fichiers `run`. Accord = même texte sans espaces ; grave = chiffres ou virgule différents."""
    out = {"machines": [a["meta"]["device"], b["meta"]["device"]], "engines": {}, "ok": True}
    for eng in sorted(set(a["engines"]) & set(b["engines"])):
        ra, rb = a["engines"][eng], b["engines"][eng]
        keys = sorted(set(ra) & set(rb))
        same = sum(norm(ra[k]["raw"]) == norm(rb[k]["raw"]) for k in keys)
        grave = [k for k in keys if digits(ra[k]["raw"]) != digits(rb[k]["raw"])]
        dlp = [abs(ra[k]["lp"] - rb[k]["lp"]) for k in keys if ra[k].get("lp") is not None and rb[k].get("lp") is not None]
        rate = same / len(keys) if keys else None
        ok = rate is not None and rate >= min_agreement and not grave
        out["engines"][eng] = {"crops": len(keys), "agreement": rate, "serious_differences": grave,
                               "logprob_abs_diff_mean": round(sum(dlp) / len(dlp), 5) if dlp else None,
                               "ok": ok}
        out["ok"] &= ok
    return out


def run(out_path: str, limit: int | None, engines: list[str] | None) -> None:
    from PIL import Image
    from ocr_worker import config as cfgmod
    from ocr_worker.engines import build_engine
    from ocr_worker.engines.base import Crop
    cfg = cfgmod.load(None)
    man = json.loads((ROOT / "data" / "ocr_bench" / "manifest.json").read_text())[:limit]
    crops = [Crop(image=Image.open(m["path"]), zone_class=m["zone_class"], crop_id=str(i)) for i, m in enumerate(man)]
    res = {"meta": {"device": cfg.device, "worker": cfg.worker_name, "crops": len(crops), "at": time.strftime("%F %T")},
           "engines": {}}
    for name, ecfg in cfg.engines.items():
        if engines and name not in engines:
            continue
        eng = build_engine(name, ecfg, cfg.device)
        if eng is None:
            continue
        eng.load()
        try:
            res["engines"][name] = {str(i): {"raw": r.raw_text, "lp": r.logprob_mean}
                                    for i, r in enumerate(eng.read(crops))}
        finally:
            eng.unload()
        print(name, "ok", flush=True)
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--out", required=True); r.add_argument("--limit", type=int)
    r.add_argument("--engine", action="append")
    c = sub.add_parser("compare"); c.add_argument("a"); c.add_argument("b"); c.add_argument("--min-agreement", type=float, default=0.98)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.out, a.limit, a.engine)
        return 0
    rep = compare(json.loads(Path(a.a).read_text()), json.loads(Path(a.b).read_text()), a.min_agreement)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

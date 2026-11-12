"""Score lot-0 results: exact surface match per engine x class (indicative), latency, memory, pair agreement."""
import json, re, sys, unicodedata, statistics as st, itertools
from pathlib import Path
B = Path(__file__).resolve().parents[2] / "data" / "ocr_bench"

def surf(s, cls):
    """Throw-away lot-0 surface (the real one is ocr_surface.to_surface, lot 4). Never changes values."""
    s = unicodedata.normalize("NFC", s or "")
    if cls in ("num_line", "math_line"): s = re.sub(r"\s+", "", s)
    s = re.sub(r"\\\(|\\\)|\$|\\left|\\right|\\!|\\,", "", s)
    s = re.sub(r"\\(?:text|mathrm)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
    s = s.replace("{,}", ",").replace("\\times", "×").replace("\\cdot", "×").replace("*", "×")
    s = s.replace("\\div", "÷").replace("\\sqrt{", "√{").replace("\\pi", "π").replace("\\approx", "≈")
    s = re.sub(r"\^\{?2\}?", "²", s); s = re.sub(r"\^\{?3\}?", "³", s)
    s = re.sub(r"[−–—]", "-", s)
    s = re.sub(r"√\{([^}]*)\}", r"√\1", s)
    if cls in ("num_line", "math_line"):
        s = re.sub(r"\s+", "", s); s = re.sub(r"(?<=\d)\.(?=\d)", ",", s)
    else:
        s = re.sub(r"\s+", " ", s).strip()
    return s.replace("{", "").replace("}", "") if cls != "text_line" else s

def load():
    R = {}
    for f in sorted(B.glob("results_*.json")):
        d = json.loads(f.read_text()); R[f.stem[8:]] = d
    return R

def main():
    R = load(); classes = ["num_line", "math_line", "text_line", "multiline_line"]
    print("| moteur | " + " | ".join(classes) + " | réel | lat p50/p95 ms | RSS pic Go | MPS Go | chargement s |")
    print("|---|" + "---|" * (len(classes) + 4))
    for name, d in R.items():
        rs = d["results"]; cells = []
        for c in classes:
            sub = [r for r in rs if r["zone_class"] == c]
            ok = sum(surf(r["raw"], c) == surf(r["truth"], c) for r in sub)
            cells.append(f"{ok}/{len(sub)}")
        real = [r for r in rs if r["src"] == "real"]
        rok = sum(surf(r["raw"], r["zone_class"]) == surf(r["truth"], r["zone_class"]) for r in real)
        lat = sorted(r["lat_ms"] for r in rs); m = d["meta"]
        print(f"| {name} | " + " | ".join(cells) + f" | {rok}/{len(real)} | {lat[len(lat)//2]:.0f}/{lat[int(len(lat)*.95)]:.0f} | {m['peak_rss_gb']:.1f} | {m['mps_gb']:.1f} | {m['load_s']:.0f} |")
    names = list(R)
    print("\nAccord entre moteurs (même surface) et corrélation des erreurs (les deux faux ET identiques / les deux faux):")
    for a, b in itertools.combinations(names, 2):
        ra, rb = R[a]["results"], R[b]["results"]
        n = len(ra); ag = sum(surf(x["raw"], x["zone_class"]) == surf(y["raw"], y["zone_class"]) for x, y in zip(ra, rb))
        bothwrong = [(x, y) for x, y in zip(ra, rb) if surf(x["raw"], x["zone_class"]) != surf(x["truth"], x["zone_class"]) and surf(y["raw"], y["zone_class"]) != surf(y["truth"], y["zone_class"])]
        same = sum(surf(x["raw"], x["zone_class"]) == surf(y["raw"], y["zone_class"]) for x, y in bothwrong)
        # accord à tort : les deux d'accord ET faux
        wrong_agree = sum(1 for x, y in zip(ra, rb) if surf(x["raw"], x["zone_class"]) == surf(y["raw"], y["zone_class"]) != surf(x["truth"], x["zone_class"]))
        print(f"- {a} × {b}: accord {ag}/{n}; deux faux {len(bothwrong)}, même erreur {same}; accord à tort {wrong_agree}")
    if "-v" in sys.argv:
        for name, d in R.items():
            print(f"\n## {name} erreurs")
            for r in d["results"]:
                if surf(r["raw"], r["zone_class"]) != surf(r["truth"], r["zone_class"]):
                    print(f"  [{r['zone_class']}] vérité={r['truth']!r} lu={r['raw']!r} lp_mean={r.get('lp_mean')}")
main()

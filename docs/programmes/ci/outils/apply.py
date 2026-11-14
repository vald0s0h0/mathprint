"""Applique les corrections et normalise : sortie json/clean_pNN.json."""
import json, re, sys, unicodedata
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from corrections import C
S = sys.argv[1]
used = set()
def ascii_math(s):
    if "$" in s:  # déjà en LaTeX : on ne touche qu'aux parties hors $
        parts = s.split("$")
        return "$".join(p if i % 2 else ascii_math(p) for i, p in enumerate(parts))
    out = []
    for ch in s:
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D7FF:
            ch = unicodedata.normalize("NFKC", ch)
        out.append(ch)
    s = "".join(out)
    s = s.replace("𝕫", "ℤ").replace("IRxIR", "ℝ×ℝ").replace("IR", "ℝ")
    s = re.sub(r"\s+", " ", s.replace("▪", " • ")).strip()
    return s.replace(" .", ".")
for k in range(1, 15):
    d = json.load(open(f"{S}/json/p{k:02d}.json"))
    for li, l in enumerate(d["lessons"]):
        l.pop("_rejected", None)
        for hi, h in enumerate(l["habiletes"]):
            new = []
            for ci, c in enumerate(h["contenus"]):
                key = f"{k:02d}|{li}|{hi}|{ci}"
                if key in C:
                    used.add(key)
                    v = C[key]
                    if v is None: continue
                    new += v if isinstance(v, list) else [v]
                else:
                    new.append(ascii_math(c.replace("\n", " ")))
            h["contenus"] = [re.sub(r"[.;]\s*$", "", x).strip() for x in new]
        l["habiletes"] = [h for h in l["habiletes"] if h["contenus"]]
        l["title"] = ascii_math(l["title"]).replace("ℝ𝟐", "ℝ²").replace("ℝ𝟑", "ℝ³")
    json.dump(d, open(f"{S}/json/clean_p{k:02d}.json", "w"), ensure_ascii=False, indent=1)
print("corrections non utilisées :", sorted(set(C) - used))

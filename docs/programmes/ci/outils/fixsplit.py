import json, re, sys, unicodedata
S = sys.argv[1]
def n(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)
tot = 0
for k in range(1, 15):
    raw = open(f"{S}/dl/p{k:02d}.txt").read()
    # chaque ligne du texte brut qui commence par un tiret = début d'item
    starts = {n(re.sub(r"^\s*[-–]\s*", "", line))[:18] for line in raw.split("\n")
              if re.match(r"^\s*[-–]", line)}
    # le verbe peut être sur la même ligne : « Calculer - la somme ... »
    starts |= {n(m.group(1))[:18] for m in re.finditer(r"[A-Za-zéè]\s+[-–]\s+([^\n]+)", raw)}
    d = json.load(open(f"{S}/json/p{k:02d}.json"))
    for l in d["lessons"]:
        for h in l["habiletes"]:
            out = []
            for it in h["contenus"]:
                key = n(it)[:18]
                if out and key not in starts and not it.startswith(("•", "▪")) and re.match(r"^[a-zàéèç]", it) \
                        and not re.search(r"[.;:]$", out[-1]):
                    out[-1] += " " + it
                    tot += 1
                else:
                    out.append(it)
            h["contenus"] = out
            if h["verbe"].startswith("Traiter une situation"):
                h["contenus"] = ["une situation " + c for c in h["contenus"]]
                h["verbe"] = "Traiter"
    json.dump(d, open(f"{S}/json/p{k:02d}.json", "w"), ensure_ascii=False, indent=1)
print("fusions", tot)

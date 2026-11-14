"""Extraction du corps des programmes DPFC (Compétence > Thème > Leçon > habiletés)."""
import json
import re
import sys
import unicodedata

import pdfplumber

HEAD = re.compile(r"^\s*(COMP[ÉE]TENCE\s*(\d)|TH[ÈE]ME\s*(\d)?\s*:?\s*(.*)|Le[çc]on\s*([\dé]*)\s*\.?\s*:\s*(.*))\s*$",
                  re.I)
PUA = str.maketrans({"\uf0b7": "•", "\uf0d8": "•", "\uf0a7": "•", "\uf0fc": "•",
                     "\uf03c": "<", "\uf03e": ">", "\uf0a3": "≤", "\uf0b3": "≥",
                     "\uf0ce": "∈", "\uf0cf": "∉", "\uf0b9": "≠", "\uf0ae": "→",
                     "\uf0b4": "×", "\uf0b0": "°", "\uf03d": "=", "\uf02b": "+",
                     "\uf02d": "−", "\uf0a5": "∞", "\uf0c8": "∪", "\uf0c7": "∩",
                     "\uf0de": "⟹", "\uf0db": "⟺", "\uf0a2": "′", "\uf0e6": "(", "\uf0f6": ")", "\uf061": "α", "\uf070": "π", "\uf062": "β", "\uf071": "θ", "\uf077": "ω"})
VERB = re.compile(r"^[A-ZÉÈÊÀÂÎÔÛ][\wéèêëàâîïôûç’' -]*?(er|ir|re|oir)( [\wéèàç]+)*$")
STOP = re.compile(r"GUIDE D.EX[ÉE]CUTION|PROGRESSION ANNUELLE|^\s*PROGRESSION", re.I)


def clean(s):
    s = unicodedata.normalize("NFC", s or "")
    s = s.translate(PUA)
    return re.sub(r"[ \t]+", " ", s).strip()


def split_items(content):
    """Une cellule « contenus » -> liste d'items (chaque « - » ouvre un item)."""
    items, cur = [], None
    for line in content.split("\n"):
        line = clean(line)
        if not line:
            continue
        if re.match(r"^[-–]\s*", line):
            if cur is not None:
                items.append(cur)
            cur = re.sub(r"^[-–]\s*", "", line)
        elif cur is None:
            cur = line
        else:
            sep = "\n" if line.startswith("•") else " "
            cur += sep + line
    if cur is not None:
        items.append(cur)
    return [i.strip() for i in items if i.strip()]


def main(path, out):
    doc = {"lessons": []}
    comp = theme = lesson = None
    started = False
    last_verb = None
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages):
            if not started and re.search(r"Exemple de situation", page.extract_text() or "", re.I):
                started = True
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            events = []
            # lignes de texte hors tableaux
            lines = {}
            for w in page.extract_words(keep_blank_chars=True, use_text_flow=True):
                cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
                if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                    continue
                key = round(w["top"] / 3)
                lines.setdefault(key, []).append(w)
            for key, ws in sorted(lines.items()):
                txt = clean(" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])))
                events.append((ws[0]["top"], "text", txt))
            for t in tables:
                for row, cells in zip(t.rows, t.extract()):
                    events.append((row.bbox[1], "row", cells))
            events.sort(key=lambda e: e[0])
            for _, kind, val in events:
                if kind == "text":
                    if not started:
                        continue
                    if STOP.search(val) and doc["lessons"]:
                        return finish(doc, out)
                    m = HEAD.match(val)
                    if not m:
                        if comp and comp.get("_await"):
                            comp["label"] = val
                            comp.pop("_await")
                        continue
                    if m.group(2):
                        comp = {"num": int(m.group(2)), "label": "", "_await": True}
                    elif val.upper().startswith("TH"):
                        theme = clean(m.group(4)).rstrip(".")
                    else:
                        lesson = {"competence": comp and comp["num"], "competence_label": "",
                                  "theme": theme, "title": clean(m.group(6)).rstrip(". "),
                                  "page": pno + 1, "habiletes": [], "_comp": comp}
                        doc["lessons"].append(lesson)
                        last_verb = None
                else:
                    if not started or lesson is None:
                        continue
                    cells = [c or "" for c in val]
                    if len(cells) < 2:
                        continue
                    verb = clean(cells[0].replace("\n", " ")).lstrip("-– ")
                    verb = verb[:1].upper() + verb[1:]
                    content = "\n".join(c for c in cells[1:] if c.strip())
                    if any(re.match(r"\s*HABILET", c, re.I) for c in cells):
                        lesson["_in"] = True
                        continue
                    if not lesson.get("_in") or (verb and not VERB.match(verb)):
                        if verb and lesson.get("_in"):
                            lesson.setdefault("_rejected", []).append(verb)
                        continue
                    items = split_items(content)
                    if not verb and lesson["habiletes"]:
                        lesson["habiletes"][-1]["contenus"] += items
                    elif verb or items:
                        lesson["habiletes"].append({"verbe": verb or "?", "contenus": items})
    return finish(doc, out)


def finish(doc, out):
    for l in doc["lessons"]:
        c = l.pop("_comp")
        l["competence_label"] = c["label"] if c else ""
        l.pop("_in", None)
        t = (l["theme"] or "").upper()
        l["competence"] = (4 if "ARITHM" in t else 2 if ("DONN" in t or "AL" in t and "ATOIRE" in t)
                           else 3 if ("GÉOM" in t or "GEOM" in t or "TRANSFORM" in t) else 1)
    json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
    n = sum(len(h["contenus"]) for l in doc["lessons"] for h in l["habiletes"])
    print(out.split("/")[-1], len(doc["lessons"]), "leçons", n, "items")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

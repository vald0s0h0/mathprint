"""Extraction des compétences officielles depuis les PDF de programme.

Source : encadrés « Objectifs d'apprentissage » des programmes de mathématiques.
- Cycle 3 : seule l'année Sixième est retenue (CM1/CM2 = primaire, exclus).
- Cycle 4 : Cinquième, Quatrième, Troisième.

Sortie : app/data/competencies_fr.json — hiérarchie grade > domaine > thème > objectifs,
avec codes stables (ex : 5E-NC-OPE-03).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
CTX = ROOT.parent / "context"

C3 = CTX / "Programme de Mathématiques Cycle 3.pdf"
C4 = CTX / "Programme de Mathématiques Cycle 4.pdf"

DOMAINS_C3 = [
    "Nombres, calcul et résolution de problèmes",
    "Grandeurs et mesures",
    "Espace et géométrie",
    "Organisation et gestion de données et probabilités",
    "La proportionnalité",
    "Initiation à la pensée informatique",
]
DOMAINS_C4 = [
    "Nombres et calculs",
    "Espace et géométrie",
    "Organisation et gestion de données et probabilités",
    "Proportionnalité, fonctions",
    "La pensée informatique",
]
DOMAIN_CODES = {
    "Nombres, calcul et résolution de problèmes": "NC",
    "Nombres et calculs": "NC",
    "Grandeurs et mesures": "GM",
    "Espace et géométrie": "EG",
    "Organisation et gestion de données et probabilités": "OGD",
    "La proportionnalité": "PF",
    "Proportionnalité, fonctions": "PF",
    "Initiation à la pensée informatique": "PI",
    "La pensée informatique": "PI",
}
YEARS_C3 = {"Cours moyen première année": "CM1", "Cours moyen deuxième année": "CM2",
            "Sixième": "6e"}
YEARS_C4 = {"Cinquième": "5e", "Quatrième": "4e", "Troisième": "3e"}

STOP_HEADINGS = ("Automatismes", "Prolongements possibles", "Exemples de réussite",
                 "Exemple de réussite", "Principes", "Sommaire",
                 "Mises en perspective historiques", "Approfondissement",
                 "Connaissances et capacités attendues", "Croisements",
                 "Repères de progression")

# mots vides ignorés pour le code de thème
STOPWORDS = {"le", "la", "les", "l", "de", "des", "du", "et", "dans", "d", "a",
             "à", "sur", "une", "un", "en", "pensée"}


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("–", "-").replace("−", "-")
    return re.sub(r"\s+", " ", s).strip()


def slug_word(s: str) -> str:
    """Code court à partir du premier mot significatif du thème."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for w in re.split(r"[^A-Za-z]+", s):
        if w and w.lower() not in STOPWORDS:
            return w.upper()[:4]
    return "GEN"


def read_lines(pdf: Path) -> list[str]:
    reader = PdfReader(str(pdf))
    lines: list[str] = []
    for page in reader.pages:
        for raw in (page.extract_text() or "").splitlines():
            lines.append(norm(raw))
    return lines


def _principes_indices(lines: list[str]) -> list[int]:
    return [i for i, ln in enumerate(lines) if ln == "Principes"]


def parse_toc_themes(lines: list[str], domains: list[str], years: dict[str, str]) -> set[str]:
    """Thèmes = lignes du sommaire qui ne sont ni un domaine ni une année.
    Le sommaire commence par une entrée « Principes » ; le corps du document
    recommence à la seconde occurrence de « Principes »."""
    idx = _principes_indices(lines)
    start = idx[0] + 1 if idx else 0
    end = idx[1] if len(idx) > 1 else len(lines)
    themes: set[str] = set()
    for ln in lines[start:end]:
        if not ln or ln in domains or ln in years:
            continue
        if 3 <= len(ln) <= 70 and not ln.endswith((".", ";", ":")):
            themes.add(ln)
    return themes


def extract(pdf: Path, domains: list[str], years: dict[str, str],
            keep_years: set[str]) -> dict:
    lines = read_lines(pdf)
    toc_themes = parse_toc_themes(lines, domains, years)

    # sauter le sommaire : le corps commence à la 2e occurrence de "Principes"
    idx = _principes_indices(lines)
    body = lines[(idx[1] if len(idx) > 1 else 0):]

    out: dict[str, dict] = {}  # grade -> domain -> theme -> [objectifs]
    domain = year = theme = None
    collecting = False
    buf: list[str] = []
    items: list[str] = []

    def flush_item():
        if buf:
            txt = norm(" ".join(buf))
            if len(txt) > 15:
                items.append(txt)
            buf.clear()

    def flush_block():
        nonlocal items
        flush_item()
        if items and domain and year in keep_years:
            g = out.setdefault(year, {})
            d = g.setdefault(domain, {})
            t = theme or domain
            d.setdefault(t, []).extend(items)
        items = []

    for ln in body:
        if not ln:
            continue
        if ln in domains:
            flush_block(); collecting = False
            domain, year, theme = ln, None, None
            continue
        if ln in years:
            flush_block(); collecting = False
            year, theme = years[ln], None
            continue
        if ln in toc_themes and domain:
            flush_block(); collecting = False
            theme = ln
            continue
        if ln.startswith("Objectifs d'apprentissage"):
            flush_block()
            collecting = True
            continue
        if any(ln.startswith(h) for h in STOP_HEADINGS):
            flush_block(); collecting = False
            continue
        if collecting:
            is_bullet = ln.startswith("-")
            ln_clean = ln.lstrip("- ").strip()
            if not ln_clean:
                continue
            if buf:
                prev = buf[-1]
                prev_last = prev.rstrip().split()[-1].lower() if prev.strip() else ""
                # continuation : ligne débutant en minuscule/chiffre, ou ligne
                # précédente coupée en pleine phrase (mot-outil final sans ponctuation)
                dangling = (not prev.rstrip().endswith((".", ";", ":"))
                            and prev_last in ("de", "du", "des", "le", "la", "les",
                                              "l'", "d'", "en", "à", "aux", "au",
                                              "et", "ou", "un", "une", "dans", "par",
                                              "pour", "sur", "que", "qui", "leur"))
                continuation = (not is_bullet
                                and (ln_clean[0].islower() or ln_clean[0].isdigit()
                                     or ln_clean[0] in "([«" or dangling))
                if not continuation:
                    flush_item()
            buf.append(ln_clean)
    flush_block()
    return out


def build_json() -> dict:
    data_c3 = extract(C3, DOMAINS_C3, YEARS_C3, keep_years={"6e"})
    data_c4 = extract(C4, DOMAINS_C4, YEARS_C4, keep_years={"5e", "4e", "3e"})

    frameworks = []
    for grade, cycle, data in [("6e", 3, data_c3.get("6e", {})),
                               ("5e", 4, data_c4.get("5e", {})),
                               ("4e", 4, data_c4.get("4e", {})),
                               ("3e", 4, data_c4.get("3e", {}))]:
        fw = {"grade_level": grade, "cycle": cycle,
              "name": f"Programme officiel {grade} (cycle {cycle})",
              "version": "2026", "domains": []}
        for dom, themes in data.items():
            dcode = DOMAIN_CODES[dom]
            dnode = {"name": dom, "code": dcode, "themes": []}
            used_tcodes: set[str] = set()
            for theme, objectives in themes.items():
                tcode = slug_word(theme)
                if tcode in used_tcodes:  # ex : Nombres relatifs / Nombres rationnels
                    words = [w for w in re.split(r"[^A-Za-zÀ-ÿ]+", theme)
                             if w and w.lower() not in STOPWORDS]
                    if len(words) > 1:
                        tcode = (words[0][:2] + words[1][:2]).upper()
                n = 2
                base = tcode
                while tcode in used_tcodes:
                    tcode = f"{base}{n}"
                    n += 1
                used_tcodes.add(tcode)
                seen: set[str] = set()
                comp = []
                for i, obj in enumerate(objectives, start=1):
                    key = obj.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    comp.append({
                        "code": f"{grade[0].upper()}E-{dcode}-{tcode}-{len(comp)+1:02d}",
                        "label": obj,
                    })
                dnode["themes"].append({"name": theme, "code": tcode, "competencies": comp})
            fw["domains"].append(dnode)
        frameworks.append(fw)
    return {"source": "Programmes officiels de mathématiques cycles 3 et 4",
            "extracted_from": [C3.name, C4.name], "frameworks": frameworks}


if __name__ == "__main__":
    result = build_json()
    out_path = ROOT / "app" / "data" / "competencies_fr.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for fw in result["frameworks"]:
        n = sum(len(t["competencies"]) for d in fw["domains"] for t in d["themes"])
        print(f"{fw['grade_level']} (cycle {fw['cycle']}): {len(fw['domains'])} domaines, {n} compétences")
        for d in fw["domains"]:
            counts = ", ".join(f"{t['name']}={len(t['competencies'])}" for t in d["themes"])
            print(f"   [{d['code']}] {d['name']}: {counts}")
    print("->", out_path)

"""Construit le référentiel CI (JSON maître + Markdown de relecture)."""
import copy
import json
import re
import sys
import unicodedata
from pathlib import Path

S = Path(sys.argv[1])          # dossier de travail : json/clean_pNN.json, json/progressions.json
OUT = Path(sys.argv[2])        # docs/programmes/ci
BACKEND = Path(sys.argv[3])    # backend/app/data/competencies_ci.json
PROGRAM_YEAR = 2023            # version CND 09/2023 des programmes éducatifs
VERSION = "2023.09"          # programmes DPFC, version CND de septembre 2023
GRADE_TOKEN = {"6e": "6E", "5e": "5E", "4e": "4E", "3e": "3E", "2nde": "2NDE", "1re": "1RE", "tle": "TLE"}
SLOT = 5                       # granularité des durées estimées, en minutes
BASE = "https://dpfc-ci.net/dpfc/programmes/maths/"
DOCS = [  # doc, grade, série (None = collège), path_id système, fichier source
    ("01", "6e", None, None, "01. Prog Éduct MATHS 6e CND 0923.pdf"),
    ("02", "5e", None, None, "02. Prog Éduct MATHS 5e CND 0923.pdf"),
    ("03", "4e", None, None, "03. Prog Éduct MATHS 4e CND 0923.pdf"),
    ("04", "3e", None, None, "04. Prog Éduct MATHS 3e CND 0923.pdf"),
    ("05", "2nde", "A", "ci-A", "05. Prog Éduct MATHS 2A CND 0923.pdf"),
    ("06", "2nde", "C", "ci-C", "06.Prog Educt maths 2C CND 0923.pdf"),
    ("07", "1re", "A1", "ci-A1", "07. Prog Éduct MATHS 1A1 CND 0923.pdf"),
    ("08", "1re", "A2", "ci-A2", "08. Prog Éduct MATHS 1A2 CND 0923.pdf"),
    ("09", "1re", "C", "ci-C", "09.Prog Educt maths 1C CND 0923.pdf"),
    ("10", "1re", "D", "ci-D", "10.Prog Educt maths 1D CND 0923.pdf"),
    ("11", "tle", "A1", "ci-A1", "11. Prog Éduct MATHS TA1 CND 0923.pdf"),
    ("12", "tle", "A2", "ci-A2", "12. Prog Éduct MATHS TA2 CND 0923.pdf"),
    ("13", "tle", "C", "ci-C", "13. Prog Educt maths TC CND 0923.pdf"),
    ("14", "tle", "D", "ci-D", "14. Prog Educt maths TD CND 0923.pdf"),
]
HOURS = {"01": 4, "02": 4, "03": 4, "04": 4, "05": 3, "06": 5, "07": 4, "08": 3, "09": 6, "10": 5,
         "11": 5, "12": 4, "13": 8, "14": 6}
GRADE_NAME = {"6e": "Sixième", "5e": "Cinquième", "4e": "Quatrième", "3e": "Troisième",
              "2nde": "Seconde", "1re": "Première", "tle": "Terminale"}
SHORT = {"6e": "6e", "5e": "5e", "4e": "4e", "3e": "3e", "2nde": "2nde", "1re": "1re", "tle": "Tle"}
FAMILY = {"A": "A", "A1": "A", "A2": "A", "C": "S", "D": "S"}
DOMAINS = {
    "A": ("Calculs algébriques", 1), "B": ("Fonctions", 1),
    "C": ("Organisation et traitement des données", 2), "D": ("Modélisation de phénomènes aléatoires", 2),
    "E": ("Géométrie du plan", 3), "F": ("Géométrie de l’espace", 3),
    "G": ("Transformations du plan", 3), "H": ("Arithmétique", 4),
}
COMPETENCES = {
    1: "Traiter une situation relative aux calculs algébriques et aux fonctions",
    2: "Traiter une situation relative à la modélisation de phénomènes aléatoires, à l’organisation et au traitement des données",
    3: "Traiter une situation relative à la géométrie du plan, à la géométrie de l’espace et aux transformations du plan",
    4: "Traiter une situation relative à l’arithmétique",
}
ALIASES = {"limites et continuite d une fonction": "limites et continuite"}
FIXES = [("gr aphiquement", "graphiquement"), ("droiteset", "droites et"), ("un ange obtus", "un angle obtus"),
         ("i’ ensemble", "l’ensemble"), ("l‘ensemble", "l’ensemble"), ("n‘est", "n’est")]


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def domain_of(theme):
    t = norm(theme or "")
    if "arithm" in t: return "H"
    if "aleatoire" in t: return "D"
    if "donnee" in t: return "C"
    if "transformation" in t: return "G"
    if "espace" in t: return "F"
    if "geometrie" in t: return "E"
    if "fonction" in t: return "B"
    return "A"


def tidy(s):
    # exposants et indices détachés par l'extraction (Tle C/D, logarithmes et exponentielles)
    s = re.sub(r",?\(a ∈ ℝ∗ \\ \{1\}\) \+", r"($a \\in \\mathbb{R}_+^* \\setminus \\{1\\}$)", s)
    s = s.replace("exp∘u et uα", "$\\exp \\circ u$ et $u^\\alpha$")
    for a, b in (("an×am = an + m", "$a^n \\times a^m = a^{n+m}$"),
                 ("(a×b)n, an ×bn , an×am et an + m", "$(a \\times b)^n$, $a^n \\times b^n$, $a^n \\times a^m$ et $a^{n+m}$"),
                 ("(xq )", "($x^{\\frac{p}{q}}$)"),
                 ("u′eu et u′um , m ∈ ℝ\\{−1}", "$u'e^u$ et $u'u^m$, $m \\in \\mathbb{R} \\setminus \\{-1\\}$"),
                 ("u′eu ; u′um, m ∈ ℝ\\{−1}", "$u'e^u$ ; $u'u^m$, $m \\in \\mathbb{R} \\setminus \\{-1\\}$"),
                 ("du type nα", "du type $(n^\\alpha)$"),
                 ("f ′’ + ω2f = 0", "$f'' + \\omega^2 f = 0$"), ("f ′’ − ω2f = 0", "$f'' - \\omega^2 f = 0$"),
                 ("x ⟼ cosx ; x ⟼ sinx ; x ⟼ tanx", "$x \\mapsto \\cos x$ ; $x \\mapsto \\sin x$ ; $x \\mapsto \\tan x$"),
                 ("ln∘u et ln∘|u|", "$\\ln \\circ u$ et $\\ln \\circ |u|$")):
        s = s.replace(a, b)
    s = re.sub(r"ℝ2(?!\d)", "ℝ²", s)
    s = re.sub(r"ℝ3(?!\d)", "ℝ³", s)
    for a, b in FIXES:
        s = s.replace(a, b)
    return s


def title_fix(t):
    t = re.sub(r"\s+A B$", "", t)  # lettres d'une figure collées au titre (3e, Triangle rectangle)
    t = t.replace("ℝxℝ", "ℝ×ℝ")
    return re.sub(r"\bEtude", "Étude", t)


def label(doc, grade, serie):
    return f"{SHORT[grade]} {serie}" if serie else SHORT[grade]


frameworks = []
for doc, grade, serie, path, pdf in DOCS:
    lessons = json.load(open(S / f"json/clean_p{doc}.json"))["lessons"]
    counters, chapters = {}, []
    for l in lessons:
        dom = domain_of(l["theme"])
        counters[dom] = counters.get(dom, 0) + 1
        code = f"{dom}{counters[dom]}"
        rows = []
        for i, h in enumerate(l["habiletes"], 1):
            verbe = tidy(h["verbe"]).replace("Connaitre", "Connaître").replace("Reconnaitre", "Reconnaître") \
                .replace("Ecrire", "Écrire").replace("Etudier", "Étudier")
            rows.append({"code": f"{code}.{i}", "habilete": verbe, "contenus": [tidy(c) for c in h["contenus"]]})
        chapters.append({"code": code, "domain": dom, "title": title_fix(tidy(l["title"])), "page": l["page"],
                         "key": ALIASES.get(norm(l["title"]), norm(l["title"])), "habiletes": rows})
    frameworks.append({"doc": doc, "grade": grade, "serie": serie, "path_id": path,
                       "label": label(doc, grade, serie), "hours_per_week": HOURS[doc],
                       "source_pdf": pdf, "source_url": BASE + pdf.replace(" ", "%20").replace("É", "%C3%89"),
                       "chapters": chapters})


def title_key(s):
    """Titre comparable entre programme et progression : garde la différence ℝ / ℝ×ℝ."""
    s = s.replace("ℝ × ℝ", " rxr ").replace("ℝ×ℝ", " rxr ").replace("ℝ²", " r2 ").replace("ℝ", " r ")
    s = norm(s)
    for a, b in (("plus petit commun multiple", "ppcm"), ("plus grand commun diviseur", "pgcd"),
                 ("du premier degre ", ""), ("d un vecteur", "de vecteurs"), ("d une fonction", ""),
                 ("systemes d equations lineaires", "systemes lineaires"),
                 ("fonctions polynomes et fractions", "polynomes et fractions"),
                 ("probabilite et variable aleatoire", "probabilite")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def split_minutes(total, weights):
    """Répartit `total` minutes au prorata des poids, par tranches de SLOT minutes
    (plus forts restes) : la somme retombe exactement sur le total de la leçon."""
    units = total // SLOT
    raw = [units * w / sum(weights) for w in weights]
    base = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[:units - sum(base)]:
        base[i] += 1
    return [b * SLOT for b in base]


import difflib  # noqa: E402

PROG = json.load(open(S / "json/progressions.json"))
for fw in frameworks:
    lessons = PROG[fw["doc"]]["lessons"]
    assert len(lessons) == len(fw["chapters"]), fw["label"]
    free = list(fw["chapters"])
    pairs = []
    for p in lessons:  # apparie chaque leçon de la progression à une leçon du programme
        scored = sorted(((difflib.SequenceMatcher(None, title_key(p["title"]), title_key(c["title"])).ratio(), i)
                         for i, c in enumerate(free)), reverse=True)
        score, i = scored[0]
        if score < 0.9:
            print(f"  appariement {fw['label']} : « {p['title']} » -> « {free[i]['title']} » ({score:.2f})")
        assert score >= 0.6 and (len(scored) == 1 or scored[1][0] < score), (fw["label"], p["title"])
        pairs.append((p, free.pop(i)))
    for p, ch in pairs:
        ch["progression"] = {k: p[k] for k in ("rank", "hours", "week", "month", "trimester")}
        ch["progression"]["title"] = p["title"]
        # une compétence de la plateforme = un contenu officiel, précédé du verbe de son
        # habileté (répété d'une ligne à l'autre), dans l'ordre du programme
        ch["lines"] = [{"code": f"{ch['code']}.{k}", "habilete": r["habilete"], "contenu": c,
                        "habilete_code": r["code"]}
                       for k, (r, c) in enumerate(((r, c) for r in ch["habiletes"] for c in r["contenus"]), 1)]
        for line, m in zip(ch["lines"], split_minutes(p["hours"] * 60, [1] * len(ch["lines"]))):
            line["minutes"] = m
    fw["regulation_hours"] = PROG[fw["doc"]]["regulation_hours"]
    fw["revision_hours"] = PROG[fw["doc"]]["revision_hours"]
    prefix = f"CI-{PROGRAM_YEAR}-MATH-{GRADE_TOKEN[fw['grade']]}" + (f"-{fw['serie']}" if fw["serie"] else "")
    fw["id_prefix"] = prefix
    order = 0
    for ch in sorted(fw["chapters"], key=lambda c: c["progression"]["rank"]):
        for line in ch["lines"]:
            order += 1
            line["teaching_order"] = order
            line["id"] = f"{prefix}-{line['code']}"


def items(ch):
    return {norm(r["habilete"]) + "::" + norm(c): f"{r['habilete']} {c}" for r in ch["habiletes"] for c in r["contenus"]}


# ------- chevauchements : même niveau, même famille de séries, même leçon
for fw in frameworks:
    for ch in fw["chapters"]:
        ch["overlaps"] = []
        if not fw["serie"]:
            continue
        for other in frameworks:
            if other is fw or other["grade"] != fw["grade"] or not other["serie"]:
                continue
            same_family = FAMILY[other["serie"]] == FAMILY[fw["serie"]]
            for och in other["chapters"]:
                if och["key"] != ch["key"]:
                    continue
                da, db = items(ch), items(och)
                a, b = set(da), set(db)
                sim = len(a & b) / max(1, len(a | b))
                ch["overlaps"].append({"with": other["label"], "code": och["code"], "same_family": same_family,
                                       "identical": a == b, "similarity": round(sim, 2),
                                       "only_here": [da[x] for x in da if x not in b],
                                       "only_there": [db[x] for x in db if x not in a]})

OUT.mkdir(parents=True, exist_ok=True)
master = {
    "program_id": "ci", "subject_id": "math", "program_year": 2023,
    "source": "DPFC (Côte d’Ivoire) — Programmes éducatifs et guides d’exécution de mathématiques, "
              "Coordination nationale disciplinaire, version CND 09/2023",
    "competences": COMPETENCES,
    "domains": {k: {"name": v[0], "competence": v[1]} for k, v in DOMAINS.items()},
    "frameworks": copy.deepcopy(frameworks),
}
for fw in master["frameworks"]:
    for ch in fw["chapters"]:
        ch.pop("key", None)
        for o in ch["overlaps"]:
            o.pop("only_here"); o.pop("only_there")
json.dump(master, open(OUT / "referentiel-ci.json", "w"), ensure_ascii=False, indent=1)


# ------- référentiel du système (backend/app/data/competencies_ci.json)
def competency_label(line):
    return f"{line['habilete']} {line['contenu']}"


system = {
    "program_id": "ci", "subject_id": "math", "program_year": PROGRAM_YEAR, "version": VERSION,
    "source": master["source"],
    "id_format": "CI-<année du programme>-MATH-<niveau>[-<série>]-<domaine><leçon>.<rang du contenu dans la leçon>",
    "time_note": "Heures par leçon : progressions annuelles 2024-2025 (CND). Minutes par compétence : "
                 "estimation, heures de la leçon réparties à parts égales entre ses contenus (tranches de 5 min).",
    "frameworks": [],
}
for fw in frameworks:
    domains = []
    for ch in fw["chapters"]:
        dom = next((d for d in domains if d["code"] == ch["domain"]), None)
        if dom is None:
            dom = {"code": ch["domain"], "name": DOMAINS[ch["domain"]][0],
                   "competence": DOMAINS[ch["domain"]][1], "chapters": []}
            domains.append(dom)
        p = ch["progression"]
        dom["chapters"].append({
            "code": ch["code"], "name": ch["title"], "hours": p["hours"], "progression_rank": p["rank"],
            "progression_week": p["week"], "progression_month": p["month"], "source_page": ch["page"],
            "competencies": [{"code": line["id"], "short_id": line["code"], "habilete": line["habilete"],
                              "habilete_code": line["habilete_code"], "label": competency_label(line),
                              "recommended_minutes": line["minutes"], "teaching_order": line["teaching_order"]}
                             for line in ch["lines"]]})
    system["frameworks"].append({
        "grade_level": fw["grade"], "path_id": fw["path_id"], "serie": fw["serie"],
        "cycle": 1 if fw["grade"] in ("6e", "5e", "4e", "3e") else 2,
        "name": f"Mathématiques · CI · {GRADE_NAME[fw['grade']]}" + (f" · {fw['serie']}" if fw["serie"] else ""),
        "id_prefix": fw["id_prefix"], "hours_per_week": fw["hours_per_week"],
        "regulation_hours": fw["regulation_hours"], "revision_hours": fw["revision_hours"],
        "source_pdf": fw["source_pdf"], "source_url": fw["source_url"], "domains": domains})
# ------- vue fusionnée par filière (onglet Exercices)
# Au lycée, les séries d'une même filière (C·D, A1·A2) partagent la plupart de
# leurs contenus. Chaque compétence reçoit `merge` : la ligne fusionnée à
# laquelle elle appartient (même texte = même ligne), sa place dans l'ordre,
# son chapitre fusionné et, pour une « variante » (même contenu à un détail
# près), le texte commun et la partie propre à chaque série.
FILIERES = {"2nde": [["A"], ["C"]], "1re": [["C", "D"], ["A1", "A2"]], "tle": [["C", "D"], ["A1", "A2"]]}
VARIANT_MIN = 0.85
# Variantes de forme seulement (singulier/pluriel, article, ponctuation, coquille),
# relues et validées par Etienne le 2026-11-13 : affichées comme lignes communes.
P = "CI-2023-MATH-"
FORM_ONLY = {frozenset(pair) for pair in [
    (P + "1RE-A1-A1.14", P + "1RE-A2-A1.13"), (P + "1RE-A1-B2.4", P + "1RE-A2-B2.4"),
    (P + "1RE-C-B1.15", P + "1RE-D-B1.15"), (P + "1RE-C-B1.22", P + "1RE-D-B1.22"),
    (P + "1RE-C-B1.23", P + "1RE-D-B1.23"), (P + "1RE-A1-D1.2", P + "1RE-A2-D1.2"),
    (P + "1RE-A1-D1.10", P + "1RE-A2-D1.10"), (P + "1RE-A1-D1.15", P + "1RE-A2-D1.15"),
    (P + "1RE-A1-D1.20", P + "1RE-A2-D1.21"), (P + "1RE-C-E2.18", P + "1RE-D-E1.16"),
    (P + "TLE-A1-A1.2", P + "TLE-A2-A1.2"), (P + "TLE-C-A1.17", P + "TLE-D-A1.21"),
    (P + "TLE-A1-B5.23", P + "TLE-A2-B4.23"), (P + "TLE-A1-D1.16", P + "TLE-A2-D1.10"),
]}
form_only_seen = set()


def strict(s):
    """Clé d'identité d'un libellé : ignore la casse, les espaces et la ponctuation
    (, ; : . •), mais garde tous les symboles mathématiques (f'' − ω²f ≠ f'' + ω²f)."""
    s = unicodedata.normalize("NFC", s).lower()
    return re.sub(r"[\s,;:.•·]+", "", s)


def split_variant(a, b):
    """Préfixe et suffixe communs (au mot près) de deux libellés ; None si la
    coupe tomberait dans une formule ou si rien de commun ne reste."""
    ta, tb = a.split(" "), b.split(" ")
    i = 0
    while i < min(len(ta), len(tb)) and ta[i] == tb[i]:
        i += 1
    j = 0
    while j < min(len(ta), len(tb)) - i and ta[-1 - j] == tb[-1 - j]:
        j += 1
    pre = " ".join(ta[:i])
    post = " ".join(ta[len(ta) - j:]) if j else ""
    mid_a, mid_b = " ".join(ta[i:len(ta) - j]), " ".join(tb[i:len(tb) - j])
    if not pre or not mid_a or not mid_b or any(s.count("$") % 2 for s in (pre, post, mid_a, mid_b)):
        return None
    return {"pre": pre + " ", "post": (" " + post) if post else "", "a": mid_a, "b": mid_b}


by_key = {(f["grade_level"], f["serie"]): f for f in system["frameworks"]}
for grade, families in FILIERES.items():
    for series in families:
        fws = [by_key[(grade, s)] for s in series]
        group = f"{GRADE_TOKEN[grade]}-{'-'.join(series)}"
        chapters = []  # [(domaine, clé, {série: chapitre})] dans l'ordre du programme
        for s, fw in zip(series, fws):
            for dom in fw["domains"]:
                for ch in dom["chapters"]:
                    key = title_key(ch["name"])
                    hit = next((c for c in chapters if c[0] == dom["code"] and c[1] == key), None)
                    if hit:
                        hit[2][s] = ch
                    else:
                        chapters.append((dom["code"], key, {s: ch}, dom["name"]))
        chapters.sort(key=lambda c: c[0])  # domaines A..H, ordre du programme conservé dans chacun
        order = 0
        for dom_code, key, members, _ in chapters:
            first = members[series[0]] if series[0] in members else next(iter(members.values()))
            codes = {m["code"] for m in members.values()}
            chapter = {"key": f"{dom_code}:{key}", "code": codes.pop() if len(codes) == 1 else "",
                       "name": first["name"]}
            lines = []  # [{série: compétence}]
            primary = members.get(series[0])
            if primary:
                lines = [{series[0]: c} for c in primary["competencies"]]
            for s in series[1:]:
                if s not in members:
                    continue
                pos = {strict(c["label"]): i for i, ln in enumerate(lines) for c in [ln.get(series[0])] if c}
                rest, last = [], -1
                for c in members[s]["competencies"]:
                    i = pos.get(strict(c["label"]))
                    if i is not None and s not in lines[i]:
                        lines[i][s] = c
                        last = i
                    else:
                        rest.append((c, last))
                # variantes : un contenu propre à chaque série, presque identique
                for idx, (c, after) in enumerate(rest):
                    best, score = None, 0
                    for i, ln in enumerate(lines):
                        if s in ln or len(ln) != 1:
                            continue
                        r = difflib.SequenceMatcher(None, norm(c["label"]), norm(ln[series[0]]["label"])).ratio()
                        if r > score:
                            best, score = i, r
                    if best is not None and score >= VARIANT_MIN and split_variant(lines[best][series[0]]["label"], c["label"]):
                        lines[best][s] = c
                        rest[idx] = None
                # contenus propres à la seconde série : après leur voisin d'origine
                extra = {}
                for item in rest:
                    if item is not None:
                        extra.setdefault(item[1], []).append({s: item[0]})
                rebuilt = list(extra.get(-1, []))
                for i, ln in enumerate(lines):
                    rebuilt.append(ln)
                    rebuilt += extra.get(i, [])
                lines = rebuilt
            for n_line, ln in enumerate(lines, 1):
                order += 1
                variant, form_only = None, False
                if len(ln) == 2 and frozenset(c["code"] for c in ln.values()) in FORM_ONLY:
                    form_only = True
                    form_only_seen.add(frozenset(c["code"] for c in ln.values()))
                elif len(ln) == 2:
                    a, b = (ln[s]["label"] for s in series)
                    if strict(a) != strict(b):
                        v = split_variant(a, b)
                        variant = {"pre": v["pre"], "post": v["post"], "parts": {series[0]: v["a"], series[1]: v["b"]}}
                merge = {"group": group, "line": f"{group}:{chapter['key']}:{n_line}", "order": order,
                         "chapter_key": chapter["key"], "chapter_code": chapter["code"],
                         "chapter_name": chapter["name"], "variant": variant}
                if form_only:
                    merge["form_only"] = True
                for c in ln.values():
                    c["merge"] = merge

assert form_only_seen == FORM_ONLY, FORM_ONLY - form_only_seen
missing = [c["code"] for f in system["frameworks"] if f["serie"] for d in f["domains"]
           for ch in d["chapters"] for c in ch["competencies"] if "merge" not in c]
assert not missing, missing[:5]
BACKEND.write_text(json.dumps(system, ensure_ascii=False, indent=1) + "\n")


# ------- Markdown
def fname(fw):
    return (f"{fw['grade']}-{fw['serie']}" if fw["serie"] else fw["grade"]) + ".md"


def badge(ch):
    out = []
    for o in ch["overlaps"]:
        if not o["same_family"]:
            continue
        if o["identical"]:
            out.append(f"≡ {o['with']} {o['code']}")
        else:
            out.append(f"≈ {o['with']} {o['code']} ({int(o['similarity'] * 100)} %)")
    return (" — " + " · ".join(out)) if out else ""


NEXT = {"6e": "5e", "5e": "4e", "4e": "3e"}
for fw in frameworks:
    n_rows = sum(len(c["habiletes"]) for c in fw["chapters"])
    n_items = sum(len(r["contenus"]) for c in fw["chapters"] for r in c["habiletes"])
    title = f"{GRADE_NAME[fw['grade']]}" + (f" {fw['serie']}" if fw["serie"] else "")
    L = [f"# Mathématiques — {title} (Côte d’Ivoire)", "",
         f"- **Source** : [{fw['source_pdf']}]({fw['source_url']}) (DPFC, CND 09/2023), corps du programme p. "
         f"{fw['chapters'][0]['page']}–{fw['chapters'][-1]['page']}",
         f"- **Référentiel système** : programme `ci`, niveau `{fw['grade']}`, "
         + (f"série `{fw['path_id']}`" if fw["path_id"] else "sans série (tronc commun du collège)"),
         f"- **Volume** : {fw['hours_per_week']} h par semaine, 32 semaines",
         f"- **Contenu** : {len(fw['chapters'])} leçons, {n_rows} habiletés, {n_items} contenus",
         f"- **Progression 2024-2025** : {sum(c['progression']['hours'] for c in fw['chapters'])} h de leçons, "
         f"+ {fw['regulation_hours']} h de régulation (1 h après chaque leçon) + {fw['revision_hours']} h de révisions",
         f"- **ID d’une compétence** : `{fw['id_prefix']}-` suivi du code, par exemple "
         f"`{fw['id_prefix']}-{fw['chapters'][0]['lines'][0]['code']}`",
         "- **Une ligne = une compétence de la plateforme** : un contenu officiel, précédé du verbe de son "
         "habileté, dans l’ordre du programme", ""]
    if fw["serie"]:
        L += ["Légende : `≡` leçon identique dans l’autre série de la même filière (avec son code là-bas), "
              "`≈ … (85 %)` leçon proche, part de contenus communs ; différences dans "
              "[chevauchements.md](chevauchements.md).", ""]
    L += ["## Sommaire", "", "Dans l’ordre du programme. *Ordre* : rang de la leçon dans la progression annuelle.", "",
          "| Code | Leçon | Compétences | Ordre | Heures | Début | Chevauchement |", "|---|---|---|---|---|---|---|"]
    for ch in fw["chapters"]:
        p = ch["progression"]
        L.append(f"| {ch['code']} | {ch['title']} | {len(ch['lines'])} | {p['rank']} | {p['hours']} h | "
                 f"sem. {p['week']} ({p['month'].lower()}) | {badge(ch)[3:] or '—'} |")
    L.append("")
    comp_done, dom_done = set(), set()
    for ch in fw["chapters"]:
        dname, comp = DOMAINS[ch["domain"]]
        if comp not in comp_done:
            comp_done.add(comp)
            L += [f"## Compétence {comp} — {COMPETENCES[comp]}", ""]
        if ch["domain"] not in dom_done:
            dom_done.add(ch["domain"])
            L += [f"### {ch['domain']} · {dname}", ""]
        p = ch["progression"]
        L += [f"#### {ch['code']} · {ch['title']}{badge(ch)}", "",
              f"*Leçon n° {p['rank']} de l’année · {p['hours']} h · à partir de la semaine {p['week']} "
              f"({p['month'].lower()}, trimestre {p['trimester']})*", ""]
        for line in ch["lines"]:
            L.append(f"- **{line['code']}** {line['habilete']} {line['contenu']} · *{line['minutes']} min*")
        L.append("")
    (OUT / fname(fw)).write_text("\n".join(L))

# ------- chevauchements.md
L = ["# Chevauchements entre séries", "",
     "Comparaison item par item (habileté + contenu) des leçons de même titre, au même niveau. "
     "Le collège n’a pas de série. En Seconde, la série D n’existe pas : les élèves de 1re D viennent de 2nde C, "
     "ceux de 1re A1/A2 de 2nde A.", "",
     "Les leçons de même titre entre filières littéraire (A, A1, A2) et scientifique (C, D) ont des contenus "
     "distincts (0 à 28 % en commun) : elles ne sont comparées qu’au sein de leur filière.", "",
     "Parcours : **2nde A → 1re A1 / 1re A2 → Tle A1 / Tle A2** ; **2nde C → 1re C / 1re D → Tle C / Tle D**.", ""]
for grade in ("2nde", "1re", "tle"):
    fws = [f for f in frameworks if f["grade"] == grade]
    L += [f"## {GRADE_NAME[grade]}", ""]
    keys = []
    for f in fws:
        for ch in f["chapters"]:
            if ch["key"] not in keys:
                keys.append(ch["key"])
    L += ["| Leçon | " + " | ".join(f["label"] for f in fws) + " | Constat |",
          "|---|" + "---|" * len(fws) + "---|"]
    for k in keys:
        cells, present, title = [], [], ""
        for f in fws:
            ch = next((c for c in f["chapters"] if c["key"] == k), None)
            cells.append(ch["code"] if ch else "")
            if ch:
                present.append((f, ch)); title = title or ch["title"]
        notes = []
        for i, (f1, c1) in enumerate(present):
            for f2, c2 in present[i + 1:]:
                o = next(o for o in c1["overlaps"] if o["with"] == f2["label"])
                if not o["same_family"]:
                    continue
                tag = "identiques" if o["identical"] else f"{int(o['similarity'] * 100)} % commun"
                if not o["same_family"] and not o["identical"]:
                    tag += " (même titre, programmes distincts)"
                notes.append(f"{f1['serie']}/{f2['serie']} : {tag}")
        L.append(f"| {title} | " + " | ".join(cells) + " | " + ("; ".join(notes) or ("propre à " + ", ".join(f["label"] for f, _ in present))) + " |")
    L.append("")
    # détail des différences dans une même famille
    det = []
    for f in fws:
        for ch in f["chapters"]:
            for o in ch["overlaps"]:
                if o["same_family"] and not o["identical"] and f["label"] < o["with"]:
                    det.append((f, ch, o))
    if det:
        L += [f"### Différences de détail ({GRADE_NAME[grade]})", ""]
        for f, ch, o in det:
            L += [f"**{ch['title']}** — {f['label']} {ch['code']} / {o['with']} {o['code']} "
                  f"({int(o['similarity'] * 100)} % commun)", ""]
            if o["only_here"]:
                L.append(f"- seulement en {f['label']} :")
                L += [f"  - {x}" for x in o["only_here"]]
            if o["only_there"]:
                L.append(f"- seulement en {o['with']} :")
                L += [f"  - {x}" for x in o["only_there"]]
            L.append("")
(OUT / "chevauchements.md").write_text("\n".join(L))

print(json.dumps([(f["label"], len(f["chapters"]), sum(len(c["habiletes"]) for c in f["chapters"]),
                   sum(len(r["contenus"]) for c in f["chapters"] for r in c["habiletes"])) for f in frameworks]))

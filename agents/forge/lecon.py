"""Contexte d'une leçon, écrit par `forge lecon` (CONSIGNE-LECON.md).

Un fichier par leçon : un en-tête court, une section `## <code>` par compétence, puis
les sections communes de fin (examen, contextes). Les faits sont numérotés `F1`, `F2`…
dans tout le fichier ; les idées sont un bloc ```yaml idees``` en fin de section.

Ce module découpe la leçon (l'agent d'exercices ne lit que l'en-tête, la section de sa
compétence et la fin), en tire les faits et les idées, et contrôle ce qui se vérifie
sans jugement. La justesse des faits passe par le vérificateur (`verifier`), puis par
l'humain pour ce qu'il conteste."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

MAX_SLICE_BYTES = 10 * 1024        # en-tête + section + fin, ce que lit l'agent d'exercices
MAX_FRAME_BYTES = 2560             # en-tête et fin : ce qui sert à toutes les compétences
MAX_SECTION_BYTES = 1536           # une compétence, idées comprises : écrite pour un modèle, pas pour un humain
_CODE = re.compile(r"\b([a-z]+)\.([a-z_]+)\.([a-z_]+)\b")
COPY_WORDS = 8
IDEA_FIELDS = ("id", "mecanisme", "pourquoi_bon", "type", "ne_pas_reprendre")
OPTIONAL_IDEA_FIELDS = ("competences",)
MAX_SENTENCES = 2
FIRST_DATED_YEAR = 2020   # une date en toutes lettres avant cette année est un fait (histoire)
VERDICTS = ("juste", "faux", "hors_programme", "ambigu")
BLOCKING = ("faux", "hors_programme")
ACCEPTED = "accepte"      # fait contesté, maintenu par l'humain

_MONTHS = ("janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|"
           "octobre|novembre|decembre|décembre")
# Une date de rédaction trahit la leçon et la périme ; une date historique est un fait.
_NUMERIC_DATE = re.compile(r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b")
_WORD_DATE = re.compile(rf"\b(?:(?:1er|\d{{1,2}})\s+)?(?:{_MONTHS})\s+(\d{{4}})\b", re.IGNORECASE)
_FACT = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*|__)?\s*F(\d+)\s*(?:\*\*|__)?\s*"
                   r"(\((?:incertain|incertaine)\))?\s*(?:\*\*|__)?\s*(?:[:)—–-]|\.(?!\d))\s*(.*)$",
                   re.MULTILINE | re.IGNORECASE)
_SECTION = re.compile(r"^##\s+(.*)$", re.MULTILINE)
_IDEAS = re.compile(r"^```ya?ml\s+idees\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
_TRACES = re.compile(r"^\W*trac[ée]s\W*:\W*(central|partiel|aucun)\b", re.IGNORECASE | re.MULTILINE)
_ENGINE = (("tikz", re.compile(r"tikz", re.IGNORECASE)),
           (r"\draw", re.compile(r"\\draw\b")),
           ("figure_layout", re.compile(r"figure_layout")),
           ("geo", re.compile(r"(?<![\w-])geo(?![\w-])", re.IGNORECASE)))


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _code_pattern(code: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.]){re.escape(code)}(?![\w]|\.\d)", re.IGNORECASE)


# ------------------------------------------------------------------ découpe
def split(text: str, codes: list[str]) -> tuple[str, dict[str, str], str]:
    """(en-tête, {code: section}, fin). Une section `##` dont le titre porte un code de
    compétence est à elle ; les autres vont dans l'en-tête si elles précèdent la
    première compétence, dans la fin sinon."""
    marks = list(_SECTION.finditer(text))
    if not marks:
        return text, {}, ""
    header = [text[:marks[0].start()]]
    sections: dict[str, str] = {}
    tail: list[str] = []
    seen_competency = False
    for i, mark in enumerate(marks):
        body = text[mark.start():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        code = next((c for c in codes if _code_pattern(c).search(mark.group(1))), None)
        if code:
            seen_competency = True
            sections[code] = sections.get(code, "") + body
        elif seen_competency:
            tail.append(body)
        else:
            header.append(body)
    return "".join(header), sections, "".join(tail)


def extract(text: str, codes: list[str], code: str | None) -> str:
    """Ce que lit l'agent d'exercices : l'en-tête, la section de sa compétence (toutes
    pour un run de problèmes, `code` vide) et la fin."""
    header, sections, tail = split(text, codes)
    if not code:
        return text
    body = sections.get(code, "")
    return (header.rstrip() + "\n\n" + body.rstrip() + ("\n\n" + tail.strip() if tail.strip() else "")
            ).strip() + "\n"


def facts(text: str) -> dict[str, dict]:
    """{F3: {"text", "incertain"}} dans l'ordre du fichier."""
    out: dict[str, dict] = {}
    for match in _FACT.finditer(text):
        out[f"F{int(match.group(1))}"] = {"text": match.group(3).strip(),
                                          "incertain": bool(match.group(2))}
    return out


def fact_hash(fact: dict) -> str:
    return hashlib.sha256(fact["text"].encode("utf-8")).hexdigest()[:16]


def ideas(text: str, codes: list[str]) -> list[dict]:
    """Les idées des blocs ```yaml idees```, rattachées à la compétence de leur section
    quand elles ne le disent pas."""
    import yaml
    out: list[dict] = []
    _header, sections, _tail = split(text, codes)
    for code, body in sections.items():
        for block in _IDEAS.findall(body):
            try:
                data = yaml.safe_load(block) or []
            except yaml.YAMLError:
                continue
            for idea in data if isinstance(data, list) else []:
                if isinstance(idea, dict):
                    out.append({**idea, "competences": idea.get("competences") or [code]})
    return out


def trace_line(section: str) -> str | None:
    match = _TRACES.search(section or "")
    return match.group(1).lower() if match else None


# ------------------------------------------------------------------ contrôles
def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", _fold(text))
    return {tuple(words[i:i + COPY_WORDS]) for i in range(len(words) - COPY_WORDS + 1)}


def _copies(text: str, others: list[str] | None, where: str, what: str, fix: str) -> list[str]:
    if not others:
        return []
    corpus: set = set()
    for other in others:
        corpus |= _shingles(other)
    hits = [s for s in _shingles(text) if s in corpus]
    if not hits:
        return []
    return [f"{where} : {len(hits)} suite(s) de {COPY_WORDS} mots identiques {what}, "
            f"par exemple « {' '.join(hits[0])} » : {fix}"]


def _dates(text: str) -> list[str]:
    found = [m.group(0) for m in _NUMERIC_DATE.finditer(text)]
    found += [m.group(0) for m in _WORD_DATE.finditer(text) if int(m.group(1)) >= FIRST_DATED_YEAR]
    return found


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+(?:\s+|$)", text.strip()) if s.strip()])


def _without_facts(text: str) -> str:
    """Les faits restent proches de leur formulation officielle : seul le reste se
    compare aux sources."""
    return "\n".join(line for line in text.splitlines() if not _FACT.match(line))


def structure_problems(text: str, codes: list[str], subject: str = "math") -> list[str]:
    """Ce sans quoi la leçon ne peut pas nourrir un run : une section par compétence,
    des faits numérotés sans trou ni doublon, au moins un fait par extrait."""
    problems: list[str] = []
    numbers = [int(m.group(1)) for m in _FACT.finditer(text)]
    if not numbers:
        return ["aucun fait numéroté : écris les faits F1, F2… un par ligne"]
    doubles = sorted({n for n in numbers if numbers.count(n) > 1})
    if doubles:
        problems.append("fait(s) numéroté(s) deux fois : " + ", ".join(f"F{n}" for n in doubles))
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    if missing:
        problems.append("numérotation des faits avec des trous : " + ", ".join(f"F{n}" for n in missing))
    _header, sections, _tail = split(text, codes)
    absent = [c for c in codes if c not in sections]
    if absent:
        problems.append("compétence(s) sans section `## <code>` : " + ", ".join(absent))
    for code in codes:
        if code in sections and not facts(extract(text, codes, code)):
            problems.append(f"{code} : aucun fait dans l'en-tête ni dans sa section")
        if subject == "math" and code in sections and not trace_line(sections[code]):
            problems.append(f"{code} : ligne « **Tracés : central|partiel|aucun.** » manquante")
    return problems


def idea_problems(text: str, codes: list[str], sources: list[str] | None = None) -> list[str]:
    import yaml
    problems: list[str] = []
    known = {_fold(c) for c in codes}
    seen: set[str] = set()
    _header, sections, _tail = split(text, codes)
    for code, body in sections.items():
        for block in _IDEAS.findall(body):
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError as error:
                problems.append(f"{code} : bloc idees illisible ({str(error).splitlines()[0]})")
                continue
            if data is None:
                continue
            if not isinstance(data, list):
                problems.append(f"{code} : le bloc idees doit être une liste YAML")
                continue
            for rank, idea in enumerate(data, 1):
                if not isinstance(idea, dict):
                    problems.append(f"{code} : idée n° {rank} n'est pas un objet YAML")
                    continue
                name = str(idea.get("id") or f"n° {rank}")
                missing = [f for f in IDEA_FIELDS if idea.get(f) in (None, "", [])]
                if missing:
                    problems.append(f"idée {name} : champ(s) manquant(s) : " + ", ".join(missing))
                unknown = sorted(set(idea) - set(IDEA_FIELDS) - set(OPTIONAL_IDEA_FIELDS))
                if unknown:
                    problems.append(f"idée {name} : champ(s) inconnu(s) : " + ", ".join(unknown))
                if name in seen:
                    problems.append(f"idée {name} : identifiant en double")
                seen.add(name)
                comps = idea.get("competences") or [code]
                comps = comps if isinstance(comps, list) else [comps]
                strangers = [str(c) for c in comps if _fold(str(c)) not in known]
                if strangers:
                    problems.append(f"idée {name} : compétence(s) hors de la leçon : " + ", ".join(strangers))
                mechanism = str(idea.get("mecanisme") or "")
                if _sentences(mechanism) > MAX_SENTENCES:
                    problems.append(f"idée {name} : mécanisme en plus de {MAX_SENTENCES} phrases")
                if re.search(r"\d", mechanism):
                    problems.append(f"idée {name} : chiffre dans le mécanisme (aucune valeur)")
                written = " ".join(str(idea.get(f) or "") for f in IDEA_FIELDS if f != "id")
                problems += _copies(written, sources, f"idée {name}", "à une source",
                                    "reformule avec tes mots")
    return problems


def taxonomy_codes(subject: str) -> set[str]:
    path = Path(__file__).resolve().parents[2] / "backend" / "app" / "data" / "misconceptions" / f"{subject}.json"
    if not path.exists():
        return set()
    return {e["code"] for e in json.loads(path.read_text(encoding="utf-8"))["errors"]}


def error_codes(section: str, subject: str = "math") -> list[str]:
    """Codes d'erreur cités par une section, dans l'ordre."""
    return list(dict.fromkeys(m.group(0) for m in _CODE.finditer(section) if m.group(1) == subject))


def error_problems(text: str, codes: list[str], subject: str = "math",
                   known: set[str] | None = None) -> list[str]:
    """Chaque compétence cite ses erreurs types, toutes dans la taxonomie : c'est la
    seule liste que lira l'agent d'exercices."""
    known = taxonomy_codes(subject) if known is None else known
    if not known:
        return []
    problems = []
    _header, sections, _tail = split(text, codes)
    for code, body in sections.items():
        cited = error_codes(body, subject)
        if not cited:
            problems.append(f"{code} : aucune erreur type citée (ligne « Erreurs : `{subject}.…` »)")
        unknown = [c for c in cited if c not in known]
        if unknown:
            problems.append(f"{code} : code(s) hors taxonomie : {', '.join(unknown)} ; prends le plus "
                            "proche, et propose le nouveau au bilan")
    return problems


def size_problems(text: str, codes: list[str]) -> list[str]:
    header, sections, tail = split(text, codes)
    problems = []
    frame = len((header + tail).encode("utf-8"))
    if frame > MAX_FRAME_BYTES:
        problems.append(f"en-tête et fin trop longs : {frame} octets pour {MAX_FRAME_BYTES} au plus")
    for code, body in sections.items():
        size = len(body.encode("utf-8"))
        if size > MAX_SECTION_BYTES:
            problems.append(f"{code} : section trop longue ({size} octets pour {MAX_SECTION_BYTES} au plus)")
    return problems


def lecon_problems(text: str, codes: list[str], sources: list[str] | None = None,
                   upper: list[str] | None = None, subject: str = "math",
                   known_errors: set[str] | None = None) -> list[str]:
    """Problèmes d'une leçon ; liste vide si elle est conforme. `upper` : les textes des
    niveaux au-dessus (universel, formats, matière, pays), qu'une leçon ne répète pas."""
    problems = structure_problems(text, codes, subject)
    problems += size_problems(text, codes)
    problems += error_problems(text, codes, subject, known_errors)
    for code in codes:
        size = len(extract(text, codes, code).encode("utf-8"))
        if size > MAX_SLICE_BYTES:
            problems.append(f"{code} : extrait trop long ({size} octets pour {MAX_SLICE_BYTES} au plus, "
                            "en-tête et fin compris)")
    dates = _dates(text)
    if dates:
        problems.append("date dans la leçon : " + ", ".join(dict.fromkeys(dates)) + " (aucune date)")
    engines = [name for name, pattern in _ENGINE if pattern.search(_IDEAS.sub("", text))]
    if engines:
        problems.append("moteur de figure ou de mise en page cité (" + ", ".join(engines)
                        + ") : la leçon décrit le contenu, pas la mise en page")
    prose = _without_facts(_IDEAS.sub("", text))
    problems += _copies(prose, sources, "leçon", "à une source", "reformule avec tes mots")
    problems += _copies(prose, upper, "leçon", "à un niveau au-dessus (matière, formats, universel)",
                        "cette règle y est déjà, retire-la de la leçon")
    problems += idea_problems(text, codes, sources)
    return problems


# ------------------------------------------------------------------ vérificateur de faits
def verif_path(lesson: Path) -> Path:
    return lesson.with_suffix(".verif.json")


def load_verif(lesson: Path) -> dict:
    path = verif_path(lesson)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"faits": {}, "usage": []}


def fact_status(text: str, verif: dict) -> dict[str, dict]:
    """{F3: {"text", "incertain", "verdict", "raison"}} ; verdict None tant que le fait,
    dans sa formulation actuelle, n'a pas été vérifié."""
    out = {}
    for key, fact in facts(text).items():
        entry = (verif.get("faits") or {}).get(fact_hash(fact)) or {}
        out[key] = {**fact, "verdict": entry.get("verdict"), "raison": entry.get("raison", "")}
    return out


def blocking_facts(text: str, verif: dict) -> list[str]:
    """Faits non vérifiés, ou jugés faux ou hors programme sans accord de l'humain."""
    out = []
    for key, fact in fact_status(text, verif).items():
        if fact["verdict"] is None:
            out.append(f"{key} non vérifié")
        elif fact["verdict"] in BLOCKING:
            out.append(f"{key} {fact['verdict'].replace('_', ' ')} : {fact['raison']}")
    return out


def verify(lesson: Path, referential: str, ask, *, model: str) -> dict:
    """Fait juger par un modèle neuf chaque fait pas encore vérifié dans sa
    formulation actuelle. `ask(system, text) -> (réponse, usage)`."""
    text = lesson.read_text(encoding="utf-8")
    verif = load_verif(lesson)
    todo = {k: f for k, f in facts(text).items() if fact_hash(f) not in (verif.get("faits") or {})}
    if not todo:
        return verif
    system = (Path(__file__).resolve().parent / "prompts" / "verificateur.md").read_text(encoding="utf-8")
    listing = "\n".join(f"{k} : {f['text']}" for k, f in todo.items())
    answer, usage = ask(system, f"# Programme officiel de la leçon\n\n{referential}\n\n# Faits à juger\n\n"
                        f"{listing}\n\nRéponds par {{\"faits\": [...]}}, un objet par fait, dans l'ordre.")
    verdicts = answer.get("faits") or []
    by_id = {str(v.get("id")): v for v in verdicts if isinstance(v, dict)}
    if set(by_id) != set(todo) or any(v.get("verdict") not in VERDICTS for v in by_id.values()):
        raise ValueError("vérificateur : réponse incomplète ou verdict inconnu")
    verif.setdefault("faits", {})
    for key, fact in todo.items():
        verif["faits"][fact_hash(fact)] = {"id": key, "texte": fact["text"], "verdict": by_id[key]["verdict"],
                                           "raison": str(by_id[key].get("raison") or ""), "modele": model}
    verif.setdefault("usage", []).append(usage)
    verif_path(lesson).write_text(json.dumps(verif, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return verif


def accept(lesson: Path, keys: list[str]) -> list[str]:
    """L'humain maintient des faits contestés après les avoir relus."""
    text = lesson.read_text(encoding="utf-8")
    verif = load_verif(lesson)
    current = facts(text)
    done = []
    for key in keys:
        fact = current.get(key)
        if fact is None:
            continue
        entry = verif.setdefault("faits", {}).setdefault(fact_hash(fact), {"id": key, "texte": fact["text"]})
        entry["verdict"], entry["raison"] = ACCEPTED, (entry.get("raison") or "") + " (maintenu par l'humain)"
        done.append(key)
    verif_path(lesson).write_text(json.dumps(verif, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return done

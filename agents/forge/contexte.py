"""Contrôle par script d'une fiche de leçon écrite par `forge contexte` (CONTEXTE.md).

Ce qui se vérifie sans jugement : la taille, l'absence de date, la numérotation des
faits, une section par compétence, aucune reprise de la source, aucune recette de
moteur de figure. La justesse des faits reste à relire par un humain."""
from __future__ import annotations

import re
import unicodedata

MAX_BYTES = 8 * 1024
COPY_WORDS = 8
IDEA_FIELDS = ("id", "competences", "mecanisme", "pourquoi_bon", "type", "ne_pas_reprendre")
MAX_SENTENCES = 2
FIRST_DATED_YEAR = 2020   # une date en toutes lettres avant cette année est un fait (histoire)

_MONTHS = ("janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|"
           "octobre|novembre|decembre|décembre")
# Une date de rédaction trahit la fiche et la périme ; une date historique est un fait.
_NUMERIC_DATE = re.compile(r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b")
_WORD_DATE = re.compile(rf"\b(?:(?:1er|\d{{1,2}})\s+)?(?:{_MONTHS})\s+(\d{{4}})\b", re.IGNORECASE)
_FACT = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*|__)?\s*F(\d+)\s*(?:\*\*|__)?\s*(?:[:)—–-]|\.(?!\d))",
                   re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_ENGINE = (("tikz", re.compile(r"tikz", re.IGNORECASE)),
           (r"\draw", re.compile(r"\\draw\b")),
           ("figure_layout", re.compile(r"figure_layout")),
           ("geo", re.compile(r"(?<![\w-])geo(?![\w-])", re.IGNORECASE)))


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", _fold(text))
    return {tuple(words[i:i + COPY_WORDS]) for i in range(len(words) - COPY_WORDS + 1)}


def _copies(text: str, sources: list[str] | None, where: str) -> list[str]:
    if not sources:
        return []
    corpus: set = set()
    for source in sources:
        corpus |= _shingles(source)
    hits = [s for s in _shingles(text) if s in corpus]
    if not hits:
        return []
    return [f"{where} : {len(hits)} suite(s) de {COPY_WORDS} mots identiques à une source, "
            f"par exemple « {' '.join(hits[0])} » : reformule avec tes mots"]


def _code_pattern(code: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.]){re.escape(code)}(?![\w]|\.\d)", re.IGNORECASE)


def _dates(text: str) -> list[str]:
    found = [m.group(0) for m in _NUMERIC_DATE.finditer(text)]
    found += [m.group(0) for m in _WORD_DATE.finditer(text) if int(m.group(1)) >= FIRST_DATED_YEAR]
    return found


def fiche_problems(text: str, competency_codes: list[str], sources: list[str] | None = None) -> list[str]:
    """Problèmes d'une fiche `.md` ; liste vide si elle est conforme."""
    problems: list[str] = []
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        problems.append(f"fiche trop longue : {size} octets pour {MAX_BYTES} au plus")
    dates = _dates(text)
    if dates:
        problems.append("date dans la fiche : " + ", ".join(dict.fromkeys(dates)) + " (aucune date)")
    numbers = [int(n) for n in _FACT.findall(text)]
    if not numbers:
        problems.append("aucun fait numéroté : écris les faits F1, F2… un par ligne")
    else:
        doubles = sorted({n for n in numbers if numbers.count(n) > 1})
        if doubles:
            problems.append("fait(s) numéroté(s) deux fois : " + ", ".join(f"F{n}" for n in doubles))
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        if missing:
            problems.append("numérotation des faits avec des trous : " + ", ".join(f"F{n}" for n in missing))
    headings = _HEADING.findall(text)
    absent = [c for c in competency_codes if not any(_code_pattern(c).search(h) for h in headings)]
    if absent:
        problems.append("compétence(s) sans section (code dans un titre) : " + ", ".join(absent))
    engines = [name for name, pattern in _ENGINE if pattern.search(text)]
    if engines:
        problems.append("moteur de figure ou de mise en page cité (" + ", ".join(engines)
                        + ") : la fiche décrit le contenu, pas la mise en page")
    # Les faits (lignes F1, F2…) sont des définitions et des propriétés : ils doivent
    # rester exacts, donc proches de leur formulation officielle. Le reste se reformule.
    prose = "\n".join(line for line in text.splitlines()
                      if not re.match(r"\s*[-*]?\s*\**F\d+\b", line))
    problems += _copies(prose, sources, "fiche")
    return problems


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+(?:\s+|$)", text.strip()) if s.strip()])


def idees_problems(text: str, competency_codes: list[str], sources: list[str] | None = None) -> list[str]:
    """Problèmes d'un fichier `.idees.yaml` ; liste vide s'il est conforme."""
    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return [f"idées : YAML illisible ({str(error).splitlines()[0]})"]
    if data is None:
        return []
    if not isinstance(data, list):
        return ["idées : le fichier doit être une liste YAML d'idées"]
    known = {_fold(c) for c in competency_codes}
    problems: list[str] = []
    seen: set[str] = set()
    for rank, idea in enumerate(data, 1):
        if not isinstance(idea, dict):
            problems.append(f"idée n° {rank} : ce n'est pas un objet YAML")
            continue
        name = str(idea.get("id") or f"n° {rank}")
        missing = [f for f in IDEA_FIELDS if idea.get(f) in (None, "", [])]
        if missing:
            problems.append(f"idée {name} : champ(s) manquant(s) : " + ", ".join(missing))
        unknown = sorted(set(idea) - set(IDEA_FIELDS))
        if unknown:
            problems.append(f"idée {name} : champ(s) inconnu(s) : " + ", ".join(unknown))
        if name in seen:
            problems.append(f"idée {name} : identifiant en double")
        seen.add(name)
        comps = idea.get("competences") or []
        if not isinstance(comps, list):
            comps = [comps]
        strangers = [str(c) for c in comps if _fold(str(c)) not in known]
        if strangers:
            problems.append(f"idée {name} : compétence(s) hors de la leçon : " + ", ".join(strangers))
        mechanism = str(idea.get("mecanisme") or "")
        if _sentences(mechanism) > MAX_SENTENCES:
            problems.append(f"idée {name} : mécanisme en plus de {MAX_SENTENCES} phrases")
        if re.search(r"\d", mechanism):
            problems.append(f"idée {name} : chiffre dans le mécanisme (aucune valeur)")
        written = " ".join(str(idea.get(f) or "") for f in IDEA_FIELDS if f not in ("id", "competences"))
        problems += _copies(written, sources, f"idée {name}")
    return problems

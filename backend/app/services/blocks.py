"""Blocs de PRÉSENTATION d'un énoncé — contrat partagé web / PDF.

`services.statement` dit ce qu'un énoncé CONTIENT (sauts de ligne durs, cases,
étiquettes de sous-question). Ce module dit comment ses lignes se LISENT : trois
d'entre elles ne sont pas des phrases et ne doivent pas se mettre en page comme
telles.

- TABLEAU : l'extraction recopie les tableaux de données du manuel en Markdown
  (`| Effectif | 10 | 14 |`), parce que c'est la seule forme qui traverse le
  JSON du LLM. Rendues au fil du texte, ces lignes s'impriment en une bouillie
  de barres verticales repliée n'importe où ; c'est un vrai tableau qu'il faut
  dessiner, colonnes ajustées et cellules centrées.
- SÉRIE : une liste de valeurs (« 10 W 8 W 6 W 10 W … », « 2,5 ; 4 ; 5,4 »)
  n'est pas une phrase non plus. Au fil du texte, les valeurs se recollent et
  l'élève ne voit plus où l'une finit et l'autre commence. C'est une grille sans
  filets : colonnes égales, valeurs centrées, lignes équilibrées.
- TEXTE : tout le reste, une ligne logique par bloc — la mise en page d'avant.

Le découpage est PUREMENT présentationnel : il ne change pas un caractère du
texte stocké en banque, il dit seulement comment le dessiner. Il tourne des deux
côtés (pdfgen ici, `frontend/src/utils/richblocks.ts` à l'identique) : l'aperçu de
l'écran doit montrer la feuille qui sortira de l'imprimante.

`split_bold` complète le contrat pour le **gras** Markdown, seule mise en forme
de caractère que la banque connaisse (« **Vrai ou faux ?** »).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import statement as statement_mod

# ------------------------------------------------------------------- gras
# Le seul balisage de CARACTÈRE admis (le LLM l'écrit spontanément, et une mise
# en valeur ponctuelle est utile en tête d'énoncé). Non gourmand, et le contenu
# commence et finit par un caractère visible : « **a** et **b** » fait deux
# gras, pas un seul, et « 3 ** 4 ** 5 » n'en fait aucun.
_BOLD_RE = re.compile(r"\*\*(\S|\S.*?\S)\*\*", re.S)


def split_bold(text: str) -> list[tuple[str, bool]]:
    """Découpe un texte en segments (contenu, gras). Les `**` disparaissent."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _BOLD_RE.finditer(text or ""):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(text or ""):
        out.append((text[pos:], False))
    return out or [("", False)]


def strip_bold(text: str) -> str:
    """Texte sans ses marques de gras (mesure, recherche, repli)."""
    return "".join(part for part, _ in split_bold(text or ""))


# ---------------------------------------------------------------- tableaux
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")


def _split_cells(line: str) -> list[str]:
    """Cellules d'une ligne Markdown. Les `|` d'une formule ($|x|$, $[3;5[$…)
    ne coupent pas : on ne découpe qu'en dehors des spans `$...$`."""
    cells: list[str] = []
    cur: list[str] = []
    in_math = False
    for ch in line.strip():
        if ch == "$":
            in_math = not in_math
            cur.append(ch)
        elif ch == "|" and not in_math:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def is_table_line(line: str) -> bool:
    """Ligne de tableau Markdown : elle commence par `|` et en porte un second."""
    s = (line or "").strip()
    return s.startswith("|") and len(_split_cells(s)) >= 1 and s.count("|") >= 2


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells)


def _table_block(lines: list[str]) -> "Block | None":
    """Tableau à partir d'un paquet de lignes Markdown consécutives, ou None si
    ça n'en fait pas un (une seule ligne, une seule colonne)."""
    rows = [_split_cells(ln) for ln in lines]
    header = len(rows) > 1 and _is_separator(rows[1])
    rows = [r for r in rows if not _is_separator(r)]
    if len(rows) < 2 or max((len(r) for r in rows), default=0) < 2:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return Block(kind="table", rows=rows, header=header)


# ------------------------------------------------------------------ séries
# Une valeur : un nombre (ou une formule complète) suivi d'une unité COLLÉE.
_NUMBER = r"[-+]?\d+(?:[.,]\d+)?(?:\s*%)?"
_UNIT = r"(?:%|€|°[CF]?|\$?[A-Za-zµΩ]{1,4}(?:/[A-Za-zµΩ]{1,4})?)"
_VALUE_RE = re.compile(
    rf"(?P<value>\$[^$\n]+\$|{_NUMBER})(?:[ \t]*(?P<unit>{_UNIT}))?")
# Séparateurs admis ENTRE deux valeurs : ponctuation de liste ou simple espace.
_GAP_RE = re.compile(r"[ \t]*[;,·•/][ \t]*|[ \t]+")
# Mots courts qui ressemblent à une unité mais relient deux valeurs : sans ce
# garde-fou, « 12, 18, 21 et 25 » se lirait comme une série d'unité « et ».
_NOT_UNITS = {"et", "ou", "a", "de", "du", "des", "la", "le", "au", "aux",
              "en", "puis", "sur", "par", "que", "qui", "un", "une"}
SERIES_MIN_ITEMS = 4
_SERIES_MAX_ITEM_LEN = 14


def parse_series(line: str) -> list[str] | None:
    """Items d'une ligne qui n'est QU'une suite de valeurs, sinon None.

    La ligne doit être consommée en ENTIER (au point final près) : c'est ce qui
    empêche une phrase contenant des nombres de passer pour une série. Les
    unités sont soumises à la règle du tout ou rien — toutes les valeurs portent
    la même, ou aucune n'en porte — sans quoi un mot de liaison de trois lettres
    ferait une unité (cf. `_NOT_UNITS`)."""
    s = (line or "").strip().rstrip(".").strip()
    if not s or "{{" in s:
        return None
    items: list[str] = []
    units: list[str] = []
    pos = 0
    while pos < len(s):
        m = _VALUE_RE.match(s, pos)
        if m is None:
            return None
        unit = (m.group("unit") or "").strip()
        if unit and unit.lower() in _NOT_UNITS:
            return None
        item = m.group(0).strip()
        if len(item) > _SERIES_MAX_ITEM_LEN:
            return None
        items.append(item)
        units.append(unit)
        pos = m.end()
        if pos >= len(s):
            break
        gap = _GAP_RE.match(s, pos)
        if gap is None or gap.end() == pos:
            return None
        pos = gap.end()
    if len(items) < SERIES_MIN_ITEMS:
        return None
    marked = [u for u in units if u]
    if marked and (len(marked) != len(units) or len(set(marked)) != 1):
        return None
    return items


def _series_blocks(line: str) -> list["Block"] | None:
    """Blocs d'une ligne qui EST une série, ou qui se termine par une série
    annoncée (« Voici les prix : 4,50 € ; 2,50 € ; … »). None sinon.

    L'étiquette de sous-question est retirée avant l'analyse et voyage avec le
    bloc : « b. 2,5 ; 4 ; 5,4 ; 4,5 » reste une sous-question b., dont le corps
    se dessine en grille."""
    label = None
    body = line
    if (got := statement_mod.subquestion_label(line)):
        label, body = got
    if (items := parse_series(body)) is not None:
        return [Block(kind="series", items=items, label=label)]
    head, sep, tail = body.rpartition(":")
    if not sep or not head.strip():
        return None
    items = parse_series(tail)
    if items is None:
        return None
    lead = f"{label}. {head.strip()} :" if label else f"{head.strip()} :"
    return [Block(kind="text", text=lead), Block(kind="series", items=items)]


# ------------------------------------------------------------------- blocs
@dataclass
class Block:
    """Un bloc de présentation. `kind` dit quels champs portent le contenu."""
    kind: str                                             # text | table | series
    text: str = ""                                        # text
    rows: list[list[str]] = field(default_factory=list)   # table
    header: bool = False                                  # table
    items: list[str] = field(default_factory=list)        # series
    label: str | None = None                              # series


def parse(text: str) -> list[Block]:
    """Découpe un énoncé (déjà normalisé) en blocs de présentation.

    Idempotent au sens où il ne modifie rien : recoller les blocs redonne le
    texte d'entrée à la mise en page près. Une ligne qui ne relève d'aucun cas
    particulier ressort telle quelle en bloc « text » — c'est la très grande
    majorité, et la mise en page d'avant est donc conservée à l'identique."""
    out: list[Block] = []
    pending: list[str] = []

    def flush_table() -> None:
        if not pending:
            return
        block = _table_block(pending) if len(pending) >= 2 else None
        if block is not None:
            out.append(block)
        else:                       # pas un tableau : les lignes restent du texte
            out.extend(Block(kind="text", text=ln) for ln in pending)
        pending.clear()

    for line in statement_mod.lines(text or ""):
        if is_table_line(line):
            pending.append(line)
            continue
        flush_table()
        series = _series_blocks(line)
        if series is not None:
            out.extend(series)
        else:
            out.append(Block(kind="text", text=line))
    flush_table()
    return out


__all__ = ["Block", "parse", "parse_series", "is_table_line",
           "split_bold", "strip_bold", "SERIES_MIN_ITEMS"]

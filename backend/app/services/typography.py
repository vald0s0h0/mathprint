"""Typographie française des textes d'exercice — passe UNIQUE avant la banque.

Les textes produits par les LLM (et recopiés du manuel) sont bien ponctués à
l'œil : « Combien de tours ? », « $5$ cm », « (0 ; 0) ». Mais leurs espaces
sont des espaces ORDINAIRES, donc des points de coupure : à l'impression, le
« ? » pouvait partir seul en tête de ligne, « cm » se détacher de son nombre,
« ; » ouvrir une ligne entre deux formules. La typographie française impose
une espace INSÉCABLE à ces endroits, et ce module la pose.

Où elle tourne : `indigo._published_record`, l'entonnoir unique par lequel un
exercice validé (Astra, Gemini, retouche du professeur dans l'onglet
Exercices) entre en banque. C'est plus robuste qu'un passage « juste après la
génération » : une retouche faite APRÈS la génération est elle aussi couverte,
et l'opération reste unique (la banque ne contient que du texte déjà traité).
Les exercices publiés avant cette règle ont été migrés une fois
(`scripts/apply_typography.py`). La fonction est IDEMPOTENTE : republier un
exercice ne double rien.

Le texte est découpé en TROIS natures, chacune avec ses règles :
- les formules `$...$` : règles LaTeX (virgule décimale `{,}`, séparateur de
  milliers `\\,`, point-virgule espacé, espace fine avant `\\%` et une unité
  `\\text{cm}`) ;
- les marques `{{...}}` (cases, figure, aide) : jamais modifiées, mais une
  case est un « nombre » pour la règle des unités (« {{blank}} cm ») ;
- le texte : règles de ponctuation (ci-dessous).

Deux insécables, comme en typographie française :
- NNBSP (U+202F, espace fine insécable) avant « ; ! ? », dans les guillemets
  « », entre les groupes de milliers ;
- NBSP (U+00A0, espace insécable) avant « : », entre un nombre et son unité,
  avant « = ».
Le PDF les dessine comme une espace ordinaire mais ne coupe jamais la ligne
dessus (pdfgen._paragraph_segs) ; le web les respecte nativement.
"""

from __future__ import annotations

import re

NBSP = "\u00a0"
NNBSP = "\u202f"
_SP = f" \t{NBSP}{NNBSP}"                      # toute espace horizontale
_SPC = f"[{_SP}]"

# ---------------------------------------------------------------- masquage
# Une formule devient « \ue000<n>\ue001 », une marque « \ue002<n>\ue003 »
# (case de réponse) ou « \ue002<n>\ue004 » (autre marque). Ce sont des
# caractères visibles (\S) pour les règles : « $x$ ? » se traite comme
# « mot ? », et une formule ne reçoit jamais de règle de texte.
_M_OPEN, _M_CLOSE = "\ue000", "\ue001"
_T_OPEN, _T_ANSWER, _T_OTHER = "\ue002", "\ue003", "\ue004"
_TOKEN_RE = re.compile(r"\{\{[^{}\n]*\}\}")
_ANSWER_TOKENS = {"{{blank}}", "{{mini}}", "{{blank_right}}"}
# Ligne de séparation d'un tableau Markdown (« |---|:---:| ») : structure pure.
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")

# ------------------------------------------------------------------ unités
# Sensible à la casse (« L » litre, « m » mètre). Les plus longues d'abord.
_UNITS = sorted([
    "mm²", "cm²", "dm²", "m²", "km²", "mm³", "cm³", "dm³", "m³",
    "km/h", "m/s", "mm", "cm", "dm", "dam", "hm", "km", "m", "ha",
    "mL", "cL", "dL", "L", "mg", "g", "kg", "h", "min", "ms", "s",
    "€", "%", "‰", "°C", "°F", "Mo", "Go", "ko",
    "degrés", "degré", "euros", "euro", "centimes", "centime",
], key=len, reverse=True)
_UNIT_ALT = "|".join(re.escape(u) for u in _UNITS)
# Ce qui précède une unité : un chiffre, une formule, une case de réponse.
_QUANTITY_END = rf"[\d{_M_CLOSE}{_T_ANSWER}]"
_UNIT_RE = re.compile(
    rf"(?<={_QUANTITY_END}){_SPC}*(?=(?:{_UNIT_ALT})(?![\w²³/]))")
# « 90° » s'écrit collé : le degré d'angle n'est rendu insécable que s'il
# était DÉJÀ espacé (« 20 °C » vient de la liste ci-dessus).
_DEGREE_RE = re.compile(rf"(?<={_QUANTITY_END}){_SPC}+(?=°(?!C|F))")

# Élisions : seule l'apostrophe d'élision devient typographique. Une
# apostrophe de géométrie (« A'B' », point prime) ne suit pas ce motif : la
# lettre élidée y est une majuscule suivie d'une majuscule.
_ELISION_RE = re.compile(
    r"(?<![\w’'])((?:[cdjlmnstCDJLMNST])|(?:[Qq]u)|(?:[Ll]orsqu)|(?:[Pp]uisqu)"
    r"|(?:[Jj]usqu)|(?:[Qq]uoiqu)|(?:[Pp]resqu)|(?:[Aa]ujourd))'"
    r"(?=[a-zà-ÿœ]|[AEIOUYHÉÈÊÀÂÎÔ][a-zà-ÿ])")

_ORDINAL_RE = re.compile(r"\b(\d+)(?:ème|ième|eme|ieme)s?\b")
_ORDINAL_FEM_RE = re.compile(r"\b1(?:ère|ere|iere)s?\b")


def _protect(text: str) -> tuple[str, list[str], list[str]]:
    """(texte masqué, formules, marques)."""
    maths: list[str] = []
    tokens: list[str] = []

    def _tok(m: re.Match) -> str:
        tokens.append(m.group(0))
        end = _T_ANSWER if m.group(0) in _ANSWER_TOKENS else _T_OTHER
        return f"{_T_OPEN}{len(tokens) - 1}{end}"

    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        j = text.find("$", i)
        if j == -1:
            out.append(_TOKEN_RE.sub(_tok, text[i:]))
            break
        k = text.find("$", j + 1)
        if k == -1:                              # $ non apparié : laissé en texte
            out.append(_TOKEN_RE.sub(_tok, text[i:]))
            break
        out.append(_TOKEN_RE.sub(_tok, text[i:j]))
        maths.append(text[j + 1:k])
        out.append(f"{_M_OPEN}{len(maths) - 1}{_M_CLOSE}")
        i = k + 1
    return "".join(out), maths, tokens


def _restore(text: str, maths: list[str], tokens: list[str]) -> str:
    text = re.sub(f"{_M_OPEN}(\\d+){_M_CLOSE}",
                  lambda m: f"${latex(maths[int(m.group(1))])}$", text)
    return re.sub(f"{_T_OPEN}(\\d+)[{_T_ANSWER}{_T_OTHER}]",
                  lambda m: tokens[int(m.group(1))], text)


# ------------------------------------------------------------------ LaTeX
_LATEX_UNITS = "|".join(re.escape(u) for u in _UNITS if u.isascii() and "/" not in u)


def latex(body: str) -> str:
    """Typographie À L'INTÉRIEUR d'une formule (contenu sans les `$`).

    - virgule décimale « 3,5 » -> « 3{,}5 » : en mode maths, une virgule nue
      est une ponctuation suivie d'une espace (« 3, 5 ») ;
    - milliers « 12 500 » -> « 12\\,500 » : en mode maths, l'espace est
      ignorée et le nombre s'imprimerait « 12500 » ;
    - « ; » (coordonnées, intervalles) -> « \\,;\\, », le « (2 ; 3) » français ;
    - espace fine avant « \\% » et avant une unité « \\text{cm} »."""
    if not body:
        return body
    body = re.sub(r"(?<=\d),(?=\d)", "{,}", body)
    body = re.sub(r"(?<=\d) +(?=\d{3}(?!\d))", r"\\,", body)
    body = re.sub(r"(?:\\,)?\s*(?<!\\);\s*(?:\\,)?", r"\\,;\\,", body)
    body = re.sub(r"(?<=\d)\s*\\%", r"\\,\\%", body)
    body = re.sub(rf"(?<=\d)\s*\\(text|mathrm)\{{\s*({_LATEX_UNITS})\s*\}}",
                  r"\\,\\\1{\2}", body)
    return body


# ------------------------------------------------------------------- texte
def _line(s: str) -> str:
    """Règles de ponctuation d'UNE ligne masquée (formules et marques opaques)."""
    s = re.sub(r"(?<=\S) {2,}", " ", s)                       # espaces doublées
    # abréviations et signes
    s = re.sub(r"(?<!\.)\.\.\.(?!\.)", "…", s)               # points de suspension
    s = _ORDINAL_FEM_RE.sub(lambda m: "1re" + ("s" if m.group(0).endswith("s") else ""), s)
    s = _ORDINAL_RE.sub(lambda m: m.group(1) + "e"
                        + ("s" if m.group(0).endswith("s") else ""), s)
    s = _ELISION_RE.sub(r"\1’", s)
    # guillemets droits appariés -> chevrons français
    if s.count('"') and s.count('"') % 2 == 0:
        s = re.sub(r'"([^"]+)"', r"«\1»", s)
    # pas d'espace avant « , . » ni à l'intérieur des parenthèses
    s = re.sub(r"(?<=\S) +([,.])(?![\d.])", r"\1", s)
    s = re.sub(r"\( +", "(", s)
    s = re.sub(r"(?<=\S) +\)", ")", s)
    # espace APRÈS la ponctuation, quand le mot suivant y est collé
    s = re.sub(r"(?<=[^\W\d_]),(?=[^\W\d_])", ", ", s)
    s = re.sub(rf"([;!?])(?=[^\W_]|{_M_OPEN}|{_T_OPEN})", r"\1 ", s)
    s = re.sub(rf"(?<=[^\W\d_]):(?=[^\W\d_]|{_M_OPEN})", ": ", s)
    # ponctuation haute : insécable AVANT (jamais en tête de ligne)
    s = re.sub(rf"(\S){_SPC}*([;!?]+)",
               lambda m: m.group(0) if m.group(1) in "([«" + NNBSP + NBSP
               else f"{m.group(1)}{NNBSP}{m.group(2)}", s)
    s = re.sub(rf"([^\s:|]){_SPC}*:(?=[{_SP}]|$)", rf"\1{NBSP}:", s)
    # guillemets
    s = re.sub(rf"«{_SPC}*", f"«{NNBSP}", s)
    s = re.sub(rf"(?<=\S){_SPC}*»", f"{NNBSP}»", s)
    # nombre (chiffre, formule, case) + unité ; « AB = 5 cm »
    s = _UNIT_RE.sub(NBSP, s)
    s = _DEGREE_RE.sub(NBSP, s)
    s = re.sub(rf"(?<=\S) +(?=[=<>≤≥≠] )", NBSP, s)
    return s


def french(text: str) -> str:
    """Applique la typographie française à un texte d'exercice. Idempotent.

    Ligne par ligne (le saut de ligne est une donnée, cf. services/statement) ;
    les lignes de séparation de tableau ne sont pas touchées."""
    if not text or not isinstance(text, str):
        return text
    masked, maths, tokens = _protect(text)
    lines = [ln if _TABLE_SEP_RE.match(ln) and "|" in ln else _line(ln)
             for ln in masked.split("\n")]
    return _restore("\n".join(lines), maths, tokens)


# ------------------------------------------------------ enregistrement publié
# Champs AFFICHÉS d'un exercice publié. Tout le reste (identifiants, réponses
# attendues par indice, figures, paires) n'est jamais du texte de lecture.
_TEXT_KEYS = {"statement", "title", "correction_guide", "correction_solution"}
_LIST_KEYS = {"choices", "cols", "left", "right"}


def _walk(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("figure", "figure_json"):
                out[k] = v
            elif k in _TEXT_KEYS or (k == "label" and isinstance(v, str)):
                out[k] = french(v)
            elif k in _LIST_KEYS and isinstance(v, list):
                out[k] = [french(x) if isinstance(x, str) else _walk(x) for x in v]
            else:
                out[k] = _walk(v)
        return out
    if isinstance(node, list):
        return [_walk(x) for x in node]
    return node


def apply_to_record(rec: dict) -> dict:
    """Copie de l'enregistrement publié, textes affichés mis en typographie
    française : énoncé, titre, sous-questions, choix de QCM, colonnes et lignes
    de grille, éléments à relier. Idempotent."""
    return _walk(rec)


__all__ = ["NBSP", "NNBSP", "french", "latex", "apply_to_record"]

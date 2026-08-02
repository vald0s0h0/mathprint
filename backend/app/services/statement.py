"""Format texte d'un énoncé — contrat PARTAGÉ entre la génération et le rendu.

Un énoncé n'est pas un bloc de texte libre : sa MISE EN LIGNE porte du sens.
« Un randonneur parcourt un sentier en quatre jours : - Jour 1 : - Jour 2 : »
et le même texte avec un jour par ligne ne se lisent pas pareil, et c'est le
second que l'élève doit avoir sous les yeux. Cette information doit donc
survivre à tout le trajet LLM -> validateur -> banque -> PDF/web, sous la seule
forme qui traverse à la fois JSON, SQL et les deux moteurs de rendu (reportlab
et KaTeX) : le saut de ligne `\\n`, stocké tel quel dans `Exercise.statement`.

Trois marques structurent un énoncé, et ce module en est la SEULE définition —
la génération (services.exercise_gen, qui les demande au modèle et les valide)
et le rendu (services.pdfgen, qui les met en page) lisent les mêmes :
- `\\n`        : saut de ligne DUR — le rendu ne le rejoue JAMAIS en espace ;
- `{{blank}}`  : case de réponse à remplir, insérée dans le fil du texte ;
- `a.` / `b)`  : étiquette de sous-question en tête de ligne (SUBQUESTION_RE),
                 imprimée en pastille et non en texte.

`normalize()` est le point de passage unique, et il est idempotent : c'est lui
qui garantit l'invariant « une sous-question commence toujours une ligne » même
quand le modèle a oublié le saut. Il tourne à DEUX endroits, pour deux raisons
distinctes — et comme c'est la même fonction, ce n'est pas une règle dupliquée :
- à la VALIDATION (exercise_gen), pour que la banque ne stocke que du normalisé.
  C'est ce qui fait que l'aperçu web d'un énoncé montre la mise en lignes de la
  copie imprimée, et que deux énoncés identiques au saut près se dédoublonnent ;
- au RENDU (pdfgen), parce que la banque contient encore les exercices créés
  avant cette règle, sous-questions recollées, et que rien ne les rejouera.
"""

import re

BLANK_TOKEN = "{{blank}}"
# Deux VARIANTES de case, choisies par le moteur de champs (services.indigo_fields)
# selon la réponse attendue — jamais écrites par un LLM, jamais validées ici :
# elles n'apparaissent qu'APRÈS la validation, dans un énoncé Indigo mis au propre.
# - {{mini}}       : mini-case (2 chiffres max), pour un trou d'équation à trous
#   (dans le manuel, le trou est une suite de « ... ») dont la réponse est un
#   petit entier ;
# - {{blank_right}}: case qui s'étire jusqu'au bord droit de la colonne, pour
#   maximiser l'espace d'une réponse plus longue placée en fin de ligne.
MINI_TOKEN = "{{mini}}"
WIDE_TOKEN = "{{blank_right}}"
# Toutes les marques de case, dans l'ordre de préférence de découpage (la plus
# longue d'abord : {{blank_right}} contient « blank », il faut la tester avant).
ANSWER_TOKENS = (WIDE_TOKEN, BLANK_TOKEN, MINI_TOKEN)
BULLET = "•"

# Marqueur de PLACEMENT de l'image d'un énoncé (§ demande utilisateur, item
# « image mal placée »). Ce n'est PAS une case de réponse : c'est un point
# d'ancrage écrit dans le fil de l'énoncé Indigo (par Gemini/Claude) qui dit
# « l'image va ICI ». Le rendu (pdfgen, aperçu web) coupe l'énoncé à ce
# marqueur et insère la figure attachée à cet endroit précis ; à défaut de
# marqueur, la figure reste placée à la fin de l'énoncé (comportement d'avant).
FIGURE_TOKEN = "{{figure}}"


def has_figure_marker(text: str) -> bool:
    return FIGURE_TOKEN in (text or "")


def split_figure_marker(text: str) -> tuple[str, str | None]:
    """(avant, après) autour du 1er « {{figure}} » (le marqueur est retiré),
    ou (texte, None) s'il n'y en a pas. Les deux moitiés sont détourées des
    sauts de ligne de bordure (le marqueur vit sur sa propre ligne)."""
    if not text or FIGURE_TOKEN not in text:
        return text, None
    before, _, after = text.partition(FIGURE_TOKEN)
    return before.strip("\n"), after.strip("\n")


def strip_figure_marker(text: str) -> str:
    """Retire tout marqueur « {{figure}} » (et la ligne vide qu'il laisse) —
    à utiliser quand aucune image n'est attachée, pour qu'il ne s'imprime pas."""
    if not text or FIGURE_TOKEN not in text:
        return text
    before, after = split_figure_marker(text)
    return "\n".join(p for p in (before, after) if p)


def place_figure_marker(text: str, has_figure: bool) -> str:
    """Garde-fou déterministe de PLACEMENT de l'image (règle Indigo) : le marqueur
    « {{figure}} » doit être AU DÉBUT de l'énoncé ou ENTRE le contexte et les
    questions, JAMAIS après les questions. On retire le marqueur existant (où que
    le modèle l'ait mis) puis on le repose sur sa propre ligne JUSTE AVANT la 1re
    sous-question ; à défaut de sous-question, après la 1re ligne de contexte (ou
    au tout début s'il n'y a qu'une ligne). Sans figure disponible, un marqueur
    parasite est retiré. Idempotent."""
    text = text or ""
    if not has_figure:
        return strip_figure_marker(text)
    body = strip_figure_marker(text)
    if not body:
        return FIGURE_TOKEN
    lines = body.split("\n")
    first_q = next((i for i, ln in enumerate(lines) if subquestion_label(ln)), None)
    if first_q is None:
        first_q = 1 if len(lines) > 1 else 0
    lines.insert(first_q, FIGURE_TOKEN)
    return "\n".join(lines)

# Une ligne ne commence JAMAIS par « - » : à l'impression, un tiret en tête de
# ligne se confond avec le signe moins d'une formule (« -5 »). Une puce de liste
# doit donc être « • » (de la couleur de l'exercice), jamais « - ». On convertit
# tout tiret de tête de ligne SAUF s'il est immédiatement suivi d'un chiffre
# (« -5 » = nombre négatif, à écrire en formule $-5$, laissé intact ici pour ne
# pas transformer un nombre en puce). « - Jour », « -$60 » -> puce.
_DASH_BULLET = re.compile(r"(?m)^[ \t]*[-–—](?=[^\d]|$)")


def _bulletize(text: str) -> str:
    """Remplace une puce en tiret de tête de ligne par « • » (l'espace éventuel
    après le tiret est géré ensuite par `_pad_delimiters`). Idempotent."""
    return _DASH_BULLET.sub(BULLET, text or "")


_LEADING_NUM = re.compile(r"^\s*(\d{1,3})\s*[.)]?\s+")


def strip_leading_number(text: str, number) -> str:
    """Retire le NUMÉRO d'exercice du manuel resté en tête d'énoncé (« 13 Range
    les nombres… » -> « Range les nombres… »). Ne retire QUE ce numéro précis
    (`number`), jamais un nombre qui ferait partie de la phrase."""
    if not text or not number:
        return text
    m = _LEADING_NUM.match(text)
    if m and m.group(1) == str(number).strip():
        return text[m.end():]
    return text

# Réparation des marqueurs de case mal formés. Le marqueur canonique est
# « {{blank}} », en texte, hors de toute formule ; mais un LLM le mange parfois
# (case perdue) et écrit la case comme le mot « blank » — souvent GLISSÉ dans
# une formule ($85blank$), où KaTeX/mathtext l'affiche en italique au lieu
# d'imprimer une case. « blank » n'ayant aucun sens légitime dans un énoncé de
# maths en français, on le rétablit systématiquement en case propre.
# accolades superflues autour de la case : « {blank} », « {{blank}} » déjà
# correct, ou une pile d'accolades « {{{{blank}}}} » (après extraction hors
# formule) -> une seule forme canonique. `\{+ ... \}+` absorbe la pile entière.
_BLANK_BRACES = re.compile(r"\{+\s*blank\s*\}+", re.I)
# « blank » collé à ce qui le précède (« 85blank ») : pas de frontière de mot à
# gauche (un chiffre est un caractère de mot), on n'exige donc qu'une frontière
# À DROITE (rien ou un non-lettre) pour ne pas confondre avec « blanket ».
_BLANK_BARE = re.compile(r"(?<!\{)blank(?![A-Za-z])", re.I)
# la case (avec ses éventuelles accolades) À L'INTÉRIEUR d'une vraie formule
_BLANK_IN_SPAN = re.compile(r"\{*\s*blank\s*\}*", re.I)


def _fix_nonmath_blank(seg: str) -> str:
    """Répare une case dans du TEXTE (hors formule) : accolades superflues ou
    « blank » nu -> {{blank}} ; une case déjà correcte est laissée telle quelle."""
    if "blank" not in seg.lower():
        return seg
    seg = _BLANK_BRACES.sub(BLANK_TOKEN, seg)
    return _BLANK_BARE.sub(BLANK_TOKEN, seg)


def _extract_blank_from_span(span: str) -> str:
    """Sort une case GLISSÉE dans une formule (« $85blank$ » -> « $85${{blank}} »,
    « ${{blank}}$ » -> « {{blank}} ») : on garde autour UNIQUEMENT ce qui est du
    vrai math, les accolades parasites sont jetées."""
    m = _BLANK_IN_SPAN.search(span)
    before = span[:m.start()].strip().strip("{}").strip()
    after = span[m.end():].strip().strip("{}").strip()
    out = f"${before}$" if before else ""
    out += BLANK_TOKEN
    if after:
        out += f"${after}$"
    return out


def repair_blank_marker(text: str) -> str:
    """Rétablit en « {{blank}} » toute case mal notée. CONSCIENT DES SPANS $...$ :
    on parcourt les vraies formules (par paires de $) pour ne PAS confondre le
    « $ » fermant d'une formule et le « $ » ouvrant de la suivante avec une case
    à cheval — bug classique de « $34$ … {{blank}} … $85$ » (une case propre
    ENTRE deux formules ne doit surtout pas être emballée). Idempotent."""
    if not text or "blank" not in text.lower():
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$":
            j = text.find("$", i + 1)
            if j == -1:                              # $ non apparié : fin en texte
                out.append(_fix_nonmath_blank(text[i:]))
                break
            span = text[i + 1:j]                     # contenu d'UNE formule
            out.append(_extract_blank_from_span(span) if "blank" in span.lower()
                       else f"${span}$")
            i = j + 1
        else:
            k = text.find("$", i)
            if k == -1:
                k = n
            out.append(_fix_nonmath_blank(text[i:k]))
            i = k
    # effondre une pile d'accolades résiduelle (« {{${{blank}}$}} » -> {{{{blank}}}} -> {{blank}})
    return _BLANK_BRACES.sub(BLANK_TOKEN, "".join(out))


# Caractères de contrôle = commande LaTeX à un seul backslash mal échappée dans
# le JSON du LLM : « \times » écrit "\times" (au lieu de "\\times") devient, au
# parsing JSON, une TABULATION suivie de « imes ». Ces octets n'ont AUCUN sens
# légitime dans une formule ; on les retransforme en leur backslash d'origine.
# Restreint aux spans $...$ (une commande LaTeX y vit toujours), donc les vrais
# sauts de ligne du texte ne sont jamais touchés.
_CTRL_TO_ESCAPE = {"\b": r"\b", "\t": r"\t", "\v": r"\v", "\f": r"\f", "\r": r"\r"}
_MATH_SPAN = re.compile(r"\$([^$\n]*)\$")


def repair_latex_control_chars(text: str) -> str:
    """Répare « \\times » & co. cassés en tabulation/saut par un JSON LLM mal
    échappé (cf. _CTRL_TO_ESCAPE). Idempotent."""
    if not text or not any(c in text for c in _CTRL_TO_ESCAPE):
        return text

    def _fix(m: "re.Match") -> str:
        body = m.group(1)
        for ch, esc in _CTRL_TO_ESCAPE.items():
            body = body.replace(ch, esc)
        return f"${body}$"

    return _MATH_SPAN.sub(_fix, text)


def _pad_delimiters(text: str) -> str:
    """Espaces manquants aux frontières : après une puce (« •$60 » -> « • $60 »)
    et avant/après une formule $...$ collée à un mot (« nombre$60$ » ->
    « nombre $60$ »). NE touche PAS la frontière « $...${{blank}} » (collée
    par convention : formule puis case). Idempotent."""
    text = re.sub(r"(?m)^(•)(?=\S)", r"\1 ", text)        # puce de liste
    if text.count("$") < 2:
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "$":
            j = text.find("$", i + 1)
            if j == -1:
                out.append(text[i:])
                break
            if out and (out[-1].isalnum() or out[-1] in ")]}"):  # mot collé à l'ouvrant
                out.append(" ")
            out.append(text[i:j + 1])                            # span $...$ opaque
            i = j + 1
            if i < n and text[i].isalnum():                      # lettre/chiffre collé au fermant ({ = {{blank}}, laissé collé)
                out.append(" ")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)

# Étiquette de sous-question EN TÊTE DE LIGNE, telle qu'on l'imprime en
# pastille : une lettre a-h OU un nombre à un/deux chiffres, un point ou une
# parenthèse, un espace. Les deux notations (« a. » et « 1. ») cohabitent dans
# les manuels et sont imprimées en pastille colorée, jamais en texte.
SUBQUESTION_RE = re.compile(r"^([a-h]|\d{1,2})\s*[.)]\s+(?=\S)")

# La même, cherchée n'importe où : le début de ligne est remplacé par « début
# de texte ou espace », puisqu'on la traque justement là où elle est restée
# collée à la phrase précédente.
_LABEL_RE = re.compile(r"(?:(?<=^)|(?<=\s))([a-h])\s*[.)]\s+(?=\S)")
# variante numérotée (« 1. », « 2) »), traquée pour la même mise en lignes
_NUM_LABEL_RE = re.compile(r"(?:(?<=^)|(?<=\s))(\d{1,2})\s*[.)]\s+(?=\S)")

_LABELS = "abcdefgh"


def subquestion_label(line: str) -> tuple[str, str] | None:
    """(étiquette, reste de la ligne) si `line` ouvre une sous-question, sinon
    None. « a. Calcule $2+3$ » -> ("a", "Calcule $2+3$") ; « 1. … » -> ("1", …)."""
    m = SUBQUESTION_RE.match(line)
    if not m:
        return None
    return m.group(1), line[m.end():]


def strip_subquestion_label(text: str) -> str:
    """Retire l'étiquette « a. » / « 1. » de tête, quand c'est le RENDU qui
    numérote (sous-questions d'un composite, lignes d'une grille ou d'un
    tableau) : gardée, elle s'imprimerait en double."""
    got = subquestion_label((text or "").lstrip())
    return got[1].strip() if got else (text or "").strip()


def _in_math(text: str, pos: int) -> bool:
    """`pos` tombe-t-il à l'intérieur d'un span $...$ ? Le balisage est
    équilibré (garanti par exercise_gen._check_text -> has_valid_math), donc un
    nombre impair de `$` avant `pos` signifie qu'on est dans une formule — où
    « $f(a) = 3$ » ne doit évidemment pas passer pour une sous-question."""
    return text.count("$", 0, pos) % 2 == 1


def _break_subquestions(text: str) -> str:
    """Force un saut de ligne devant chaque étiquette de sous-question restée
    collée au texte qui la précède.

    Le repérage est SÉQUENTIEL — a, puis b, puis c… en partant de a — et exige
    au moins DEUX étiquettes. Une simple recherche de « [a-h][.)] » couperait
    au milieu d'une phrase (« Il y a. », « Range de a) à d) ») ; ici, un faux
    positif demanderait à la fois la bonne lettre, au bon rang, et une suivante
    qui enchaîne — ce qui n'arrive pas par accident.
    """
    cuts: list[int] = []
    expected = 0
    for m in _LABEL_RE.finditer(text):
        if expected >= len(_LABELS) or m.group(1) != _LABELS[expected]:
            continue
        if _in_math(text, m.start()):
            continue
        cuts.append(m.start(1))
        expected += 1
    if len(cuts) < 2:
        return text
    return _apply_cuts(text, cuts)


def _break_numbered(text: str) -> str:
    """Comme `_break_subquestions`, mais pour les sous-questions NUMÉROTÉES
    (« 1. », « 2. »…) : séquence croissante partant de 1, au moins deux, hors
    formule. Le numéro d'exercice du manuel a déjà été retiré en amont (cf.
    `strip_leading_number`), donc le premier « 1. » rencontré est bien une
    sous-question, pas le numéro de l'exercice."""
    cuts: list[int] = []
    expected = 1
    for m in _NUM_LABEL_RE.finditer(text):
        if int(m.group(1)) != expected:
            continue
        if _in_math(text, m.start()):
            continue
        cuts.append(m.start(1))
        expected += 1
    if len(cuts) < 2:
        return text
    return _apply_cuts(text, cuts)


def _apply_cuts(text: str, cuts: list[int]) -> str:
    """Insère un saut de ligne à chaque position de `cuts` (début d'étiquette)."""
    out, prev = [], 0
    for pos in cuts:
        out.append(text[prev:pos].rstrip())
        prev = pos
    out.append(text[prev:])
    # `out[0]` est vide quand l'énoncé commence directement par l'étiquette :
    # pas de ligne blanche en tête pour autant.
    return "\n".join(p for i, p in enumerate(out) if p or i)


def normalize(text: str) -> str:
    """Met un énoncé sous sa forme canonique. Idempotent.

    - fins de ligne uniformisées en `\\n` (le JSON d'un LLM peut porter `\\r\\n`) ;
    - espaces de fin de ligne retirés — invisibles, mais ils décalent la
      mesure de la ligne au rendu ;
    - lignes vides supprimées : le saut de ligne sépare, il n'aère pas ; deux
      sauts coûteraient une ligne blanche dans une carte déjà dense ;
    - puces en tiret de tête de ligne (« - ») converties en « • » : une ligne
      ne commence jamais par « - », qui se confondrait avec un signe moins ;
    - une sous-question par ligne, toujours — étiquettes lettrées (a., b.) ET
      numérotées (1., 2.) (cf. `_break_subquestions`, `_break_numbered`) ;
    - cases de réponse mal notées rétablies en `{{blank}}` (cf.
      `repair_blank_marker`) : le LLM glisse parfois le mot « blank » dans une
      formule au lieu du marqueur, la case ne s'imprimait alors pas.
    """
    text = text or ""
    text = repair_latex_control_chars(text)     # \times cassé en tabulation -> \times
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = repair_blank_marker(text)
    text = _bulletize(text)                      # « - » de tête de ligne -> « • »
    text = _break_subquestions(text)             # a. b. c. chacune sur sa ligne
    text = _break_numbered(text)                 # 1. 2. 3. chacune sur sa ligne
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln).strip()
    return _pad_delimiters(text)                # espaces manquants aux frontières $ / puce


def lines(text: str) -> list[str]:
    """Lignes logiques d'un énoncé normalisé — l'unité de mise en page du
    rendu : c'est par ligne qu'on décide d'une pastille de sous-question et
    d'un corps de texte agrandi (ligne portant une case à remplir)."""
    return [ln for ln in (text or "").split("\n")]


def has_answer_field(text: str) -> bool:
    """La ligne/l'énoncé porte-t-il une case de réponse, quelle que soit sa
    taille ? Double usage : segmenter un span de texte autour de sa case
    (pdfgen._paragraph_segs), et décider qu'une ligne grandit avec sa case
    (pdfgen blank_fs) — y COMPRIS la mini-case : le corps de texte suit
    toujours la case qu'il porte, jamais deux tailles sur une même ligne."""
    t = text or ""
    return any(tok in t for tok in ANSWER_TOKENS)

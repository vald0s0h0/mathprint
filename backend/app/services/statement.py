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

# Encadré GUIDE (aide à la démarche) INTÉGRÉ à l'énoncé : une ligne qui commence
# par « {{aide}} » s'imprime dans un encadré jaune clair avec un pictogramme,
# à l'endroit précis où elle est écrite — entre deux questions, avant une
# sous-question, dans le contexte d'un composite. Des lignes « {{aide}} »
# consécutives forment UN seul encadré. Le guide fait partie de l'exercice :
# ce n'est plus une bande sous la carte. Le sujet peut le retirer entièrement
# (option « Ne pas inclure les guides » → `strip_guides`).
GUIDE_TOKEN = "{{aide}}"
_GUIDE_INLINE = re.compile(r"[ \t]*\{\{\s*aide\s*\}\}[ \t]*", re.I)


def is_guide_line(line: str) -> bool:
    return (line or "").lstrip().startswith(GUIDE_TOKEN)


def guide_body(line: str) -> str:
    """Texte d'une ligne guide, sans son marqueur."""
    return (line or "").lstrip()[len(GUIDE_TOKEN):].strip()


def has_guides(text: str) -> bool:
    return GUIDE_TOKEN in (text or "")


def guide_texts(text: str) -> list[str]:
    """Un texte par ENCADRÉ (lignes guide consécutives réunies par « \n »)."""
    out: list[str] = []
    prev_guide = False
    for ln in (text or "").split("\n"):
        if is_guide_line(ln):
            if prev_guide:
                out[-1] += "\n" + guide_body(ln)
            else:
                out.append(guide_body(ln))
            prev_guide = True
        else:
            prev_guide = False
    return out


def strip_guides(text: str) -> str:
    """Retire tous les encadrés guide (option de sujet « Ne pas inclure »)."""
    if not has_guides(text):
        return text or ""
    return "\n".join(ln for ln in text.split("\n") if not is_guide_line(ln))


def split_guides(text: str) -> tuple[str, str]:
    """Corps et aides de cette portée. Les aides suivent sa zone de réponse.

    Compatible avec les anciens marqueurs placés en tête : aucune aide n'est
    perdue, et une aide du contexte reste attachée à l'exercice entier.
    """
    text = _isolate_guides(text or "")
    return strip_guides(text).strip(), "\n".join(
        ln.strip() for ln in text.split("\n") if is_guide_line(ln))


def split_leading_guides(text: str) -> tuple[str, str]:
    """(lignes guide de TÊTE, reste). Sert aux sous-questions d'un composite :
    l'encadré écrit en tête de question s'imprime AVANT la pastille « a. »,
    entre la question précédente et celle-ci."""
    ls = (text or "").split("\n")
    k = 0
    while k < len(ls) and (is_guide_line(ls[k]) or not ls[k].strip()):
        k += 1
    return "\n".join(ln for ln in ls[:k] if ln.strip()), "\n".join(ls[k:])


def _isolate_guides(text: str) -> str:
    """Un marqueur « {{aide}} » ouvre TOUJOURS une ligne : s'il a été écrit au
    fil du texte, on coupe devant. Forme canonique « {{aide}} texte »."""
    if "aide" not in text.lower():
        return text
    return _GUIDE_INLINE.sub("\n" + GUIDE_TOKEN + " ", text)


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


def place_figure_marker(text: str, has_figure: bool, *, at_end: bool = False) -> str:
    """Conserve un marqueur explicite. À défaut, place la figure avant les
    sous-questions, ou après le contexte commun si `at_end`. Sans figure,
    retire le marqueur parasite. L'auteur décide de l'ordre de lecture."""
    text = text or ""
    if not has_figure:
        return strip_figure_marker(text)
    if has_figure_marker(text):
        return text  # le placement explicite de l'auteur fait autorité
    body = strip_figure_marker(text)
    if not body:
        return FIGURE_TOKEN
    lines = body.split("\n")
    first_q = next((i for i, ln in enumerate(lines) if subquestion_label(ln)), None)
    if first_q is None:
        first_q = len(lines) if at_end else (1 if len(lines) > 1 else 0)
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


# Étiquette de sous-question MISE EN GRAS par le modèle (« **a.** 17 élèves »).
# Le gras est un balisage de caractère légitime (cf. services/blocks), mais une
# ÉTIQUETTE ne s'imprime pas en texte : elle devient une pastille colorée. Tant
# qu'elle porte ses `**`, SUBQUESTION_RE ne la reconnaît pas — la ligne perd sa
# pastille, ne se détache pas des précédentes, et « **a.** » s'imprime tel quel.
# On la démaquille donc au plus tôt, en tête de ligne comme au fil du texte (où
# c'est _break_subquestions qui la remettra sur sa propre ligne).
_BOLD_LABEL = re.compile(
    r"(?:(?<=^)|(?<=\s))\*\*\s*([a-h]|\d{1,2})\s*(?:([.)])\s*\*\*|\*\*\s*([.)]))(?=\s)")


def _unbold_labels(text: str) -> str:
    """« **a.** » / « **a**. » -> « a. ». Idempotent."""
    return _BOLD_LABEL.sub(lambda m: f"{m.group(1)}{m.group(2) or m.group(3)}", text or "")


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
      formule au lieu du marqueur, la case ne s'imprimait alors pas ;
    - étiquettes de sous-question démaquillées de leur gras (« **a.** » ->
      « a. ») : une étiquette devient une pastille, jamais du texte gras.
    """
    text = text or ""
    text = repair_latex_control_chars(text)     # \times cassé en tabulation -> \times
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = repair_blank_marker(text)
    text = _isolate_guides(text)                 # « {{aide}} » ouvre toujours une ligne
    text = _unbold_labels(text)                  # « **a.** » -> « a. » (pastille)
    text = _bulletize(text)                      # « - » de tête de ligne -> « • »
    # les lignes guide sont MASQUÉES pendant la mise en lignes des sous-questions :
    # « Aide : a. fais ceci, b. puis cela » ne doit jamais être coupé en pastilles.
    guides: list[str] = []

    def _mask(ln: str) -> str:
        if is_guide_line(ln):
            guides.append(ln)
            return f"\x00{len(guides) - 1}\x00"
        return ln
    text = "\n".join(_mask(ln) for ln in text.split("\n"))
    text = _break_subquestions(text)             # a. b. c. chacune sur sa ligne
    text = _break_numbered(text)                 # 1. 2. 3. chacune sur sa ligne
    text = re.sub(r"\x00(\d+)\x00", lambda m: guides[int(m.group(1))], text)
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [f"{GUIDE_TOKEN} {guide_body(ln)}" if is_guide_line(ln) else ln
             for ln in lines]
    text = "\n".join(ln for ln in lines
                     if ln and not (is_guide_line(ln) and not guide_body(ln))).strip()
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

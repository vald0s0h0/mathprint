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

# Étiquette de sous-question EN TÊTE DE LIGNE, telle qu'on l'imprime en
# pastille : une seule lettre a-h, un point ou une parenthèse, un espace.
SUBQUESTION_RE = re.compile(r"^([a-h])\s*[.)]\s+(?=\S)")

# La même, cherchée n'importe où : le début de ligne est remplacé par « début
# de texte ou espace », puisqu'on la traque justement là où elle est restée
# collée à la phrase précédente.
_LABEL_RE = re.compile(r"(?:(?<=^)|(?<=\s))([a-h])\s*[.)]\s+(?=\S)")

_LABELS = "abcdefgh"


def subquestion_label(line: str) -> tuple[str, str] | None:
    """(étiquette, reste de la ligne) si `line` ouvre une sous-question, sinon
    None. « a. Calcule $2+3$ » -> ("a", "Calcule $2+3$")."""
    m = SUBQUESTION_RE.match(line)
    if not m:
        return None
    return m.group(1), line[m.end():]


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
    out, prev = [], 0
    for pos in cuts:
        out.append(text[prev:pos].rstrip())
        prev = pos
    out.append(text[prev:])
    # `out[0]` est vide quand l'énoncé commence directement par « a. » : pas de
    # ligne blanche en tête pour autant.
    return "\n".join(p for i, p in enumerate(out) if p or i)


def normalize(text: str) -> str:
    """Met un énoncé sous sa forme canonique. Idempotent.

    - fins de ligne uniformisées en `\\n` (le JSON d'un LLM peut porter `\\r\\n`) ;
    - espaces de fin de ligne retirés — invisibles, mais ils décalent la
      mesure de la ligne au rendu ;
    - lignes vides supprimées : le saut de ligne sépare, il n'aère pas ; deux
      sauts coûteraient une ligne blanche dans une carte déjà dense ;
    - une sous-question par ligne, toujours (cf. `_break_subquestions`).
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _break_subquestions(text)
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def lines(text: str) -> list[str]:
    """Lignes logiques d'un énoncé normalisé — l'unité de mise en page du
    rendu : c'est par ligne qu'on décide d'une pastille de sous-question et
    d'un corps de texte agrandi (ligne portant une case à remplir)."""
    return [ln for ln in (text or "").split("\n")]


def has_blank(text: str) -> bool:
    return BLANK_TOKEN in (text or "")

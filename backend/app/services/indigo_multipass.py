"""Six passes DeepSeek Flash après extraction du manuel par DeepSeek Vision.

Vision fournit numéro, texte complet, titre de compétence rose, présence de
figure, description et crop. Puis, UNE FOIS CHACUNE et dans cet ordre :

  1. filtre     — rattache la source au référentiel et la nettoie, sans jamais
                  la rejeter ;
  2. contexte   — sur la compétence RÉELLEMENT résolue par la passe 1 : l'énoncé
                  seul suffit-il (le cas le plus fréquent) ? Sinon, un corrigé du
                  manuel du PROFESSEUR pour ce même exercice comble-t-il ce
                  qu'une figure emporte seule ? Sinon, le contexte pédagogique
                  (compétence, type de problème) suffit-il encore à INVENTER un
                  exercice fidèle à cet esprit ? Si même ça manque, la source
                  est rejetée directement (`InfeasibleSource`) — avant tout
                  appel de génération ;
  3. génération — les variantes indivisibles Base et Facile, depuis le
                  verdict de la passe contexte (`source.corrige_prof` XOR
                  `source.invent_context`) ;
  4. résolution — un deuxième avis, privé de toutes les réponses ;
  5. mise en page — la seule passe qui ne voie AUCUNE réponse : elle retire les
                  consignes qui n'apprennent rien, supprime ce que les questions
                  redisent du contexte, mutualise en grille les questions à
                  propositions identiques et répare la syntaxe LaTeX ;
  6. retouche   — elle reçoit les défauts relevés par Python et les désaccords
                  du solveur, et les RÉPARE SUR PLACE. Ce qu'elle ne sait pas
                  réparer, elle le signale : le brouillon part avec son badge.

AUCUNE PASSE NE RENVOIE UN EXERCICE EN GÉNÉRATION (révision du 04/09). Mesuré
sur les pages 67-68 : quatre tentatives par exercice, 63 générations pour 8
sources, et des défauts qui passaient de 85 à 81. Régénérer redonne le même
exercice avec les mêmes défauts, pour quatre fois le prix. Une relance ne
subsiste que pour un incident de TRANSPORT, la seule chose que recommencer
répare. Une relecture finale conservatrice a existé en sixième position : 18
variantes relues, 18 rendues à l'identique — elle a été retirée, son travail est
le deuxième geste de la passe 5.

Seuls `qcm_single`, `qcm_multiple`, `checkbox_grid` et leur assemblage
`composite` sont publiés. Les portes Python vérifient structure, calculs, guides,
figures, graduation et absence de répétition des réponses dans l'énoncé ; ce
qu'elles reprochent encore APRÈS la retouche voyage jusqu'au brouillon, sous les
yeux du professeur.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Competency
from . import (exercise_gen, indigo_check, indigo_llm, mathrender, prompts,
               providers, scoring)
from . import statement as statement_mod
from .gemini_gen import _competency_name
from .grading import normalize

logger = logging.getLogger("app.indigo")

PROMPT_VERSION = "indigo-multipass-5"

# --- états d'une famille (cahier des charges, repris tels quels) --------------
QUEUED, FILTERING, GENERATING = "QUEUED", "FILTERING", "GENERATING"
SOLVING, FORMATTING = "SOLVING", "FORMATTING"
REPAIRING = "REPAIRING"
READY = "READY"
# Trio exploitable, mais des DÉFAUTS subsistent après toutes les tentatives
# (à ne pas confondre avec les réserves de `Family.notes`, qui accompagnent aussi
# un trio READY : celles-là sont des points à REGARDER, pas des défauts avérés).
# C'est une ISSUE NORMALE, pas un échec : les exercices partent en brouillon
# avec leurs réserves, et le professeur tranche à la relecture. Jeter cinq
# passes de travail parce qu'un guide fait 34 mots au lieu de 30 revenait à
# préférer RIEN à presque-bien — sur les pages 86-87, 33 sources sur 33.
NEEDS_REVIEW = "NEEDS_REVIEW"
REJECTED_SOURCE, REJECTED_GENERATION = "REJECTED_SOURCE", "REJECTED_GENERATION"
STATES = (QUEUED, FILTERING, GENERATING, SOLVING, FORMATTING, REPAIRING,
          READY, NEEDS_REVIEW, REJECTED_SOURCE, REJECTED_GENERATION)
# États terminaux : plus aucun appel LLM n'est dû pour cette famille.
FINAL_STATES = (READY, NEEDS_REVIEW, REJECTED_SOURCE, REJECTED_GENERATION)
# Issues qui donnent des lignes à écrire en base (toujours en BROUILLON).
KEPT_STATES = (READY, NEEDS_REVIEW)

# Les deux variantes produites par la génération. L'ORDRE compte : la base est
# validée en premier — en cas de doublon c'est le dérivé Facile qui saute,
# jamais elle. Le dérivé Difficile a existé (débranché par défaut le 04/09,
# retiré pour de bon le 05/09) : la graduation Facile/Base ne se voyait pas
# assez, et Difficile n'ajoutait souvent qu'une synthèse sur les mêmes
# données. Un exercice « expert » du manuel (badge CV, § indigo_cv) tient
# maintenant ce rôle : `indigo._persist_multipass_family` reclasse alors ses
# deux variantes générées (Base → Difficile, Facile → Base) sans que cette
# pipeline n'en sache rien — ses prompts ne parlent jamais de Difficile.
VARIANTS = ("base", "facile")
VARIANT_LEVEL = {"facile": 1, "base": 2}
# Libellés EXACTS attendus par les passes. Aucun autre, aucun badge ajouté.
VARIANT_LABEL = {"facile": "Facile", "base": "Base"}

# Assemblage à sous-questions (§ exercise_gen, branche composite) : un contexte
# commun + N questions, chacune d'un des formats corrigeables par CV.
COMPOSITE = "composite"
# Bornes des sous-questions : celles du validateur partagé, pas d'autres.
MIN_PARTS, MAX_PARTS = 2, 8
# Le contexte d'un composite ne pose aucune question ; il porte les données
# communes. Même plancher que le validateur partagé pour un composite.
CONTEXT_MIN_CHARS = 15
# En dessous, l'énoncé « nettoyé » rendu par la passe 1 ne contient rien qu'on
# puisse exploiter : c'est un rejet de source, pas une génération à tenter.
SOURCE_MIN_CHARS = 15


class InfeasibleSource(Exception):
    """La passe CONTEXTE (§ `_pass_context`) juge qu'aucun exercice juste n'est
    possible — ni depuis l'énoncé, ni depuis un corrigé du professeur qui s'y
    rattacherait vraiment, ni même en s'en INSPIRANT pour en inventer un
    (le contexte pédagogique lui-même est trop incertain). Ce n'est pas un
    incident de transport : rejouer rendrait le même verdict. Levée AVANT tout
    appel de génération — `run_family`/`run_family_pair` l'interceptent à la
    résolution de la source (phase A) et rejettent la SOURCE directement, sans
    jamais atteindre la passe 2."""

# Formats de réponse admis : les trois formats corrigeables par vision par
# ordinateur, ceux-là mêmes du mode « QCM only », plus leur assemblage. La liste
# des trois n'est pas recopiée — elle EST celle du vérificateur, pour qu'un
# format ajouté d'un côté ne soit jamais oublié de l'autre.
ALLOWED_TYPES = indigo_check.QCM_TYPES + (COMPOSITE,)

# Ce que le solveur (passe 3) doit comprendre du format, sans qu'on lui montre
# la moindre réponse.
SOLVER_FORMAT = {"qcm_single": "une seule case à cocher",
                 "qcm_multiple": "plusieurs cases peuvent être cochées",
                 "checkbox_grid": "une case cochée par ligne"}

# Guide élève : 30 mots maximum (cahier des charges). Le plancher est celui du
# validateur partagé (exercise_gen._validate_exercise exige 5 caractères) ; on
# le monte à 3 mots, en dessous desquels un « guide » n'aide personne.
GUIDE_MAX_WORDS = 30
GUIDE_MIN_WORDS = 3


@dataclass
class Source:
    """Ce que la passe 1 retient d'un exercice du manuel.

    `figure` est la description TEXTUELLE du dessin, tirée de l'énoncé — la
    seule chose qu'un modèle sans vision puisse en savoir. Elle sert de contrat
    entre l'énoncé et l'image : la passe 2 ne doit pas la contredire, la passe 3
    s'en sert pour résoudre, la passe 5 pour juger la cohérence."""
    statement: str
    needs_figure: bool = False
    figure: str = ""
    competency_id: str = ""
    competency_code: str = ""
    # Copie Vision avant la passe 1 : la passe 5 compare la génération à cette
    # source d'autorité aussi, afin qu'un nettoyage trop agressif ne fasse pas
    # disparaître silencieusement une donnée ou une sous-question.
    original_statement: str = ""
    # Une entrée courte par consigne/sous-question d'origine. La génération et
    # l'audit s'en servent comme checklist : faciliter n'autorise pas à remplacer
    # un calcul sur un graphique par la simple répétition d'une donnée.
    tasks: list[str] = field(default_factory=list)
    # Les deux verdicts de la passe CONTEXTE (§ `_pass_context`), au plus l'un
    # des deux non vide. `corrige_prof` : extrait du manuel du PROFESSEUR
    # VALIDÉ comme portant sur ce même exercice — la passe 2 ne voit plus
    # jamais un candidat non tranché. `invent_context` : la source ni le
    # corrigé ne suffisent, mais le contexte pédagogique, lui, suffit à
    # inventer un exercice fidèle à cet esprit (§ prompts/indigo/
    # multipass_generate.txt).
    corrige_prof: str = ""
    invent_context: str = ""


@dataclass
class Family:
    """Les trois exercices issus d'UN exercice source, et leur avancement.

    `variants` ne se remplit qu'à l'état READY : une famille est indivisible,
    elle n'existe pas à moitié."""
    number: str
    state: str = QUEUED
    attempts: int = 0
    reason: str = ""
    variants: list[tuple[str, dict]] = field(default_factory=list)
    # Réserves de relecture : ce que les portes reprochent encore au trio
    # conservé. Elles voyagent jusqu'au brouillon, où le professeur les voit.
    notes: list[str] = field(default_factory=list)
    # les trois exercices s'appuient-ils sur la figure du manuel ? Tranché à la
    # passe 1 ; l'appelant s'en sert pour attacher l'image, ou la détacher.
    figure: bool = False
    competency_id: str = ""
    competency_code: str = ""
    # Le rattachement vient-il d'une donnée sûre (bandeau rose lu, ou code rendu
    # par la passe 1) ? Faux = meilleure approximation, À CONFIRMER par le
    # professeur — l'exercice est gardé, pas rangé en silence n'importe où.
    competency_confirmed: bool = True
    # Ce que la passe 5 n'a PAS su réparer et juge grave (réponse fausse qu'elle
    # ne sait pas refaire, consigne incompréhensible, figure indispensable et
    # muette). Distinct de `notes`, qui dit « à regarder » : ceci dit « ne
    # l'imprime pas tel quel ». L'onglet Exercices en fait un badge rouge, et
    # c'est le « badge adapté » demandé pour un exercice trop mal fait.
    blocking: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Trio publiable EN L'ÉTAT, sans réserve. Ne conditionne plus l'écriture
        en base (§ `kept`) : un trio à relire s'écrit aussi, en brouillon."""
        return self.state == READY

    @property
    def kept(self) -> bool:
        """Y a-t-il des variantes à écrire en base ? Seul test de persistance."""
        return self.state in KEPT_STATES and bool(self.variants)

    def as_dict(self) -> dict:
        """Forme journalisable (stats_json de l'extraction) — pas les énoncés."""
        return {"number": self.number, "state": self.state,
                "attempts": self.attempts, "figure": self.figure,
                "competency": self.competency_code,
                "competency_confirmed": self.competency_confirmed,
                "notes": [n[:200] for n in self.notes[:12]],
                "blocking": [n[:200] for n in self.blocking[:6]],
                "reason": self.reason[:300]}


# --------------------------------------------------------------------- prompts

def _system(name: str, competency: Competency, grade: str) -> str:
    """Prompt d'une passe — ÉDITABLE dans prompts/indigo/multipass_<name>.txt.

    Comme le mode « QCM only », il ne passe PAS par exercise_gen.format_contract :
    ce contrat décrit dix formats de réponse et un barème à estimer, dont rien ne
    s'applique ici. Chaque fichier décrit lui-même son schéma de sortie."""
    return (prompts.load("indigo", f"multipass_{name}")
            .replace("§GRADE§", grade)
            .replace("§COMPETENCY§", _competency_name(competency))
            .replace("§CHAPTER§", f"{competency.chapter_code} {competency.chapter_name}".strip())
            .replace("§DOMAIN§", f"{competency.domain_code} {competency.domain_name}".strip())
            .replace("§MAX_WORDS§", str(GUIDE_MAX_WORDS)))


def _fold_label(value: str) -> str:
    return " ".join("".join(
        c for c in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(c)).lower().split())


def _rank_competencies(title: str, competencies: list[Competency]) -> list[Competency]:
    """Compétences les plus proches du titre rose lu par Vision.

    Le LLM tranche encore en passe 1, mais sur une courte liste pertinente. Le
    classement déterministe sert aussi de repli si sa sortie omet le code : une
    omission de champ ne doit jamais jeter un exercice de manuel.
    """
    needle = _fold_label(title)

    def score(comp: Competency) -> float:
        label = _fold_label(comp.label)
        if not needle or not label:
            return 0.0
        if needle == label:
            return 10.0
        contained = 2.0 if needle in label or label in needle else 0.0
        a, b = set(needle.split()), set(label.split())
        overlap = len(a & b) / max(1, len(a | b))
        return contained + overlap + SequenceMatcher(None, needle, label).ratio()

    return sorted(competencies, key=score, reverse=True)


def _resolve_competency(source: Source, manual: dict,
                        candidates: list[Competency]) -> tuple[Competency | None, bool]:
    """(compétence retenue, rattachement CONFIRMÉ ?).

    Le second terme est le point important. Le repli par ressemblance rend
    toujours une compétence — la première du référentiel quand rien ne
    ressemble à rien — et l'exercice partait donc se ranger sous une compétence
    qui n'était pas la sienne, SANS que personne le sache. C'est pire qu'un
    rejet : un rejet se voit.

    Deux sources sont dignes de confiance : le bandeau rose lu par Vision, et
    le code rendu par la passe 1. À défaut, l'exercice est gardé (on ne jette
    rien) mais signalé À RATTACHER : le professeur le déplace d'un clic, ce
    qu'aucune heuristique ne fera aussi bien que lui."""
    # Un SEUL candidat : c'est la cible que l'utilisateur a lui-même désignée
    # dans l'extraction. Il n'y a rien à confirmer, et l'y faire douter mettrait
    # une réserve sur tous les exercices du mode « une compétence à la fois ».
    if len(candidates) == 1:
        return candidates[0], True
    # Un titre de bandeau lu exactement est une donnée visuelle plus fiable que
    # l'interprétation thématique du LLM. Le classifieur ne peut pas le remplacer
    # par une compétence voisine (cas réel B4.1 « graphique » devenu B4.2).
    visual_title = _fold_label(str(manual.get("competency_title") or ""))
    if visual_title:
        exact = [comp for comp in candidates
                 if _fold_label(comp.label) == visual_title]
        if exact:
            return exact[0], True
    wanted_id = str(source.competency_id or "").strip()
    wanted_code = _fold_label(source.competency_code)
    for comp in candidates:
        if wanted_id and comp.id == wanted_id:
            return comp, True
        if wanted_code and wanted_code in {
                _fold_label(comp.code), _fold_label(comp.short_id)}:
            return comp, True
    ranked = _rank_competencies(str(manual.get("competency_title") or ""), candidates)
    return (ranked[0] if ranked else None), False


# ------------------------------------------------------- portes déterministes

def _flat(text: str) -> str:
    """Texte comparable ENTIER — mêmes fonctions que la correction
    (§ indigo_check). Sert à confronter deux énoncés, jamais à chercher dedans :
    `grading.normalize` supprime TOUS les espaces (c'est ce qu'il faut pour
    comparer la réponse d'un élève, pas pour repérer un mot dans une phrase)."""
    return normalize(mathrender.strip_math(str(text or ""))).strip().lower()


_SPACES_RE = re.compile(r"\s+")


def _plain(text: str) -> str:
    """Texte où l'on peut CHERCHER : LaTeX aplati, minuscules, espaces normalisés.

    Volontairement sans `normalize` — cf. `_flat` : coller tous les mots ensemble
    ferait échouer toute recherche à la frontière de mot, donc le contrôle de
    fuite ci-dessous (bug trouvé à l'écriture des tests : « le PGCD vaut 275 »
    devenait « lepgcdvaut275 », et « 275 » n'y était plus un mot)."""
    return _SPACES_RE.sub(" ", mathrender.strip_math(str(text or ""))).strip().lower()


def _sub_label(i: int) -> str:
    """« a. », « b. »… — la lettre que le rendu imprimera devant la
    sous-question (§ pdfgen._composite_layout). Les problèmes rendus au modèle
    désignent donc la question telle que le professeur la verra."""
    return chr(ord("a") + i) if 0 <= i < 26 else str(i + 1)


def _parts(variant: dict) -> list[dict]:
    """Les QUESTIONS d'une variante : les sous-questions d'un composite, ou la
    variante elle-même si elle n'en a qu'une.

    Un seul chemin pour tout ce qui se juge question par question — la
    vérification, la vue du solveur, les désaccords, le barème. Sans lui, chaque
    contrôle porterait sa propre branche « et si c'est un composite », et l'une
    d'elles finirait par manquer."""
    if variant.get("response_type") == COMPOSITE:
        return [p for p in (variant.get("questions") or []) if isinstance(p, dict)]
    return [variant]


def _words(guide: str) -> int:
    """Mots d'un guide. Une formule LaTeX (« $\\dfrac{3}{4}$ ») compte pour un
    mot : c'est ce que lit l'élève, et c'est ce que le prompt demande de compter."""
    return len(str(guide or "").split())


def _guide_leaks(guide: str, variant: dict) -> str:
    """Le guide livre-t-il la réponse ? Rend la raison, ou "" s'il est propre.

    On compare la bonne proposition APLATIE au guide aplati, aux frontières de
    mot : sans elles, une réponse « 3 » serait « trouvée » dans « 13 » ou dans
    « 3 diviseurs » et ferait tomber des guides parfaitement corrects. Avec
    elles, un « 3 » isolé dans un guide dont la réponse est 3 est bien ce qu'on
    cherche à interdire.

    Une GRILLE n'est pas concernée : sa « bonne réponse » est un libellé de
    colonne (« Vrai », « Faux »), qu'un guide peut légitimement employer. Chercher
    « Vrai » dans un guide de grille ne trouverait que des faux positifs — la
    fuite d'une grille (« tout est vrai sauf la deuxième ») se juge à la passe 5.

    C'est un filet GROSSIER, et il l'assume : l'audit (passe 5) juge l'indice
    trop parlant, Python n'attrape que la réponse recopiée. Mais celle-là, il
    l'attrape à coup sûr et gratuitement.

    Un composite n'a qu'UN guide pour toutes ses sous-questions : la réponse de
    n'importe laquelle d'entre elles y est donc interdite (§ `_parts`)."""
    flat_guide = _plain(guide)
    if not flat_guide:
        return ""
    for part in _parts(variant):
        if part.get("response_type") == "checkbox_grid":
            continue
        choices = part.get("choices") or []
        correct = [i for i in (part.get("correct") or [])
                   if isinstance(i, int) and 0 <= i < len(choices)]
        for i in correct:
            answer = _plain(choices[i])
            if not answer:
                continue
            if re.search(rf"(?<!\w){re.escape(answer)}(?!\w)", flat_guide):
                return (f"le guide contient la bonne réponse « {choices[i]} » : il "
                        f"doit donner une piste, jamais le résultat")
    return ""


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_leaf(part: dict) -> dict:
    """Forme canonique d'UNE question : la variante simple, ou une sous-question.

    Le `response_type` est RECOPIÉ tel quel, pas corrigé : un format hors des
    trois autorisés doit être REFUSÉ avec son nom (indigo_check.lint le dit en
    clair), pas silencieusement transformé en un format que le modèle n'a pas
    voulu — la réponse attendue ne suivrait pas."""
    out = dict(part or {})
    out["response_type"] = str(out.get("response_type") or "").strip()
    out["statement"] = statement_mod.repair_latex_control_chars(
        str(out.get("statement") or "").strip())
    if out["response_type"] == "checkbox_grid":
        out["cols"] = [str(c).strip() for c in (out.get("cols") or [])]
        out["rows"] = [{"label": statement_mod.repair_latex_control_chars(
                            str((r or {}).get("label") or "").strip()),
                        "correct": _as_int((r or {}).get("correct"))}
                       for r in (out.get("rows") or []) if isinstance(r, dict)]
    else:
        out["choices"] = [str(c).strip() for c in (out.get("choices") or [])]
        correct = out.get("correct")
        if isinstance(correct, int):
            correct = [correct]
        out["correct"] = [c for c in (correct or []) if isinstance(c, int)]
    _drop_unusable_check(out)
    return out


def _drop_unusable_check(part: dict) -> None:
    """Neutralise un métacontrôle illisible, jamais une réponse pédagogique.

    Le solveur indépendant et l'audit recalculent ensuite la question. Rejeter
    trois bons exercices parce que Flash a écrit ``"6"`` au lieu de
    ``"Eq(6,7)"`` dans un champ invisible à l'élève est un faux négatif pur.
    Une vérification lisible mais contradictoire reste, elle, refusée.
    """
    check = part.get("check")
    if not isinstance(check, dict):
        return
    kind = str(check.get("kind") or "none")
    if kind in ("set", "rows"):
        exprs = check.get("exprs")
        truths = ([indigo_check._truth(expr) for expr in exprs]
                  if isinstance(exprs, list) else [])
        if (not isinstance(exprs, list)
                or any(truth is None for truth in truths)):
            part["check"] = {"kind": "none"}
        return
    if kind != "value":
        return
    try:
        want = indigo_check._eval(check.get("expr"))
    except (TypeError, ValueError):
        part["check"] = {"kind": "none"}
        return
    if isinstance(want, bool) or want in (True, False):
        # `Eq(3,3)` sous un `value` : le modèle a écrit une VÉRITÉ là où le
        # format attend un NOMBRE. Comparé à la valeur de la bonne case, un
        # booléen ne coïncide jamais, et le reproche accusait la réponse
        # (« $f(3) = -5$ ne vaut pas Eq(3,3) ») alors qu'elle était juste. La
        # vérité par proposition existe et s'appelle `set` ; celle-ci n'est
        # pas une vérification, c'est une faute de frappe de contrat.
        part["check"] = {"kind": "none"}
        return
    if getattr(want, "free_symbols", None):
        # « count_class_45_50 », « nb_eleves »… : un NOM inventé, pas un calcul.
        # Rien ne peut lier ce symbole, la comparaison échoue donc TOUJOURS, et
        # elle accusait la bonne réponse (« "12" ne vaut pas count_class_45_50 »).
        # C'était le premier motif de refus des pages 86-87 : des exercices justes
        # renvoyés en génération pour un champ que l'élève ne voit jamais.
        part["check"] = {"kind": "none"}
        return
    choices = part.get("choices") or []
    idx = _as_int(check.get("choice"))
    # `check.choice` et `correct` doivent désigner la MÊME case. Quand ils
    # divergent mais que la case de `correct` porte bien la valeur attendue,
    # c'est l'INDICE du métacontrôle qui est faux, pas la réponse de l'exercice :
    # on le recale au lieu de renvoyer la famille en génération (défaut le plus
    # tenace de Flash sur l'extraction du 03/09, jusqu'à quatre tentatives).
    declared = [c for c in (part.get("correct") or []) if isinstance(c, int)]
    if (len(declared) == 1 and declared[0] != idx
            and 0 <= declared[0] < len(choices)
            and indigo_check._equal(
                indigo_check._value_of(choices[declared[0]]), want)):
        check["choice"] = idx = declared[0]
    if idx is None or not 0 <= idx < len(choices):
        return
    got = indigo_check._value_of(choices[idx])
    if got is None or (getattr(got, "free_symbols", None)
                       and not getattr(want, "free_symbols", None)):
        part["check"] = {"kind": "none"}


def _normalize_variant(variant: dict) -> dict:
    """Forme canonique d'une variante sortie du modèle, composite compris.

    Un composite garde son contexte et son guide, et normalise chacune de ses
    sous-questions. `parts` est accepté comme synonyme de `questions` : le
    contrat interne les nomme ainsi (§ exercise_gen) et un modèle qui reprend ce
    mot-là n'a pas tort au point qu'on jette son travail.

    Le LaTeX est réparé aux deux bouts (énoncé et guide) comme partout ailleurs
    — cf. services.statement."""
    out = dict(variant or {})
    out["response_type"] = str(out.get("response_type") or "").strip()
    out["guide"] = statement_mod.repair_latex_control_chars(
        str(out.get("guide") or "").strip())
    # Une variante qui porte PLUSIEURS questions est un composite, quel que soit
    # le type qu'elle s'est donné. Flash annonce régulièrement « qcm_single » en
    # livrant deux sous-questions : la lire comme une feuille effaçait les
    # questions ET leurs cases (« 0 proposition(s) »), et cette contradiction —
    # invisible dans la sortie — était l'un des premiers moteurs des quatre
    # tentatives par exercice. Ce n'est pas transformer un format au petit
    # bonheur : c'est le SEUL conteneur qui garde ce que le modèle a écrit.
    questions = out.get("questions")
    if not isinstance(questions, list):
        questions = out.get("parts")
    if (out["response_type"] != COMPOSITE and isinstance(questions, list)
            and len([q for q in questions if isinstance(q, dict)]) >= MIN_PARTS):
        out["response_type"] = COMPOSITE
    if out["response_type"] != COMPOSITE:
        out.pop("questions", None)
        out.pop("parts", None)
        return {**out, **_normalize_leaf(out)}
    out["statement"] = statement_mod.repair_latex_control_chars(
        str(out.get("statement") or "").strip())
    questions = out.get("questions")
    if not isinstance(questions, list):
        questions = out.get("parts")
    out.pop("parts", None)
    out["questions"] = [_normalize_leaf(q)
                        for q in (questions or []) if isinstance(q, dict)]
    # DeepSeek emploie parfois le conteneur composite pour une unique question.
    # Ce n'est pas une faute pédagogique : aplatissement sans perte plutôt que
    # rejet mécanique de toute la famille.
    if len(out["questions"]) == 1:
        leaf = out["questions"][0]
        leaf["statement"] = "\n".join(filter(None, (
            str(out.get("statement") or "").strip(),
            str(leaf.get("statement") or "").strip())))
        leaf["guide"] = out.get("guide") or ""
        return leaf
    return out


def _declared_part(part: dict) -> list[int]:
    """Les cases qu'une QUESTION annonce comme justes : une liste d'entiers.

    Grille : une case par LIGNE, dans l'ordre des lignes (l'ordre porte du sens).
    QCM : les indices cochés, triés (l'ordre n'en porte aucun)."""
    if part.get("response_type") == "checkbox_grid":
        return [_as_int(r.get("correct")) for r in (part.get("rows") or [])]
    return sorted({i for i in (part.get("correct") or []) if isinstance(i, int)})


def _declared(variant: dict) -> list[list[int]]:
    """Ce que le GÉNÉRATEUR annonce, sous la forme même que rend le solveur :
    une liste d'entiers PAR QUESTION. Une variante simple en compte une, un
    composite en compte autant que de sous-questions."""
    return [_declared_part(p) for p in _parts(variant)]


def _family_key(variant) -> str:
    """Empreinte d'une variante pour repérer un vrai clone de sa sœur.

    Deux niveaux peuvent légitimement partager le contexte et la question d'une
    même figure, tout en proposer des cases et pièges différents. On inclut donc
    le format et les libellés de réponse ; seules deux cartes réellement
    identiques sont refusées. L'audit reste chargé de la progression de fond.
    """
    if not isinstance(variant, dict):
        return ""
    fields = [str(variant.get("response_type") or ""),
              str(variant.get("statement") or "")]
    for part in _parts(variant):
        fields.extend((str(part.get("response_type") or ""),
                       str(part.get("statement") or "")))
        if part.get("response_type") == "checkbox_grid":
            fields.extend(str(col) for col in (part.get("cols") or []))
            fields.extend(str(row.get("label") or "")
                          for row in (part.get("rows") or []))
        else:
            fields.extend(str(choice) for choice in (part.get("choices") or []))
    return _flat(" | ".join(fields))


def _verify_variant(variant: dict, *, has_figure: bool) -> list[str]:
    """Tous les problèmes déterministes d'une variante, composite compris.

    Un composite n'est pas un format de plus : c'est un CONTEXTE et N
    sous-questions qui, elles, portent chacune l'un des trois formats
    corrigeables par CV. On vérifie donc le contexte comme un énoncé (autonomie,
    LaTeX, pas de renvoi hors feuille) et chaque sous-question avec le
    vérificateur complet, sympy compris — c'est là que sont les réponses."""
    if variant.get("response_type") != COMPOSITE:
        return indigo_check.verify(variant, has_figure=has_figure)

    problems = indigo_check.lint_statement(variant.get("statement"),
                                           has_figure=has_figure,
                                           min_len=CONTEXT_MIN_CHARS)
    parts = _parts(variant)
    if not MIN_PARTS <= len(parts) <= MAX_PARTS:
        problems.append(f"{len(parts)} sous-question(s), attendu {MIN_PARTS} à "
                        f"{MAX_PARTS} — en dessous, écris un exercice simple ; "
                        f"au-dessus, la carte déborde de la page")
        return problems
    for i, part in enumerate(parts):
        for problem in indigo_check.verify(part, has_figure=has_figure, part=True):
            problems.append(f"sous-question {_sub_label(i)}. : {problem}")
    # Un énoncé VIDE ne pose aucune question : c'est le cas d'une grille en
    # sous-question, dont les lignes et les colonnes portent tout (§ passe 4).
    # Deux grilles sans consigne se distinguent par leurs lignes, pas par un
    # texte qu'aucune des deux n'a.
    keys = [k for p in parts if (k := _flat(p.get("statement")))]
    if len(set(keys)) != len(keys):
        problems.append("deux sous-questions posent la même question")
    return problems


def _variant_text(variant: dict) -> str:
    """Tout le texte lu par l'élève : le contexte et les sous-questions."""
    return "\n".join([str(variant.get("statement") or "")]
                     + [str(p.get("statement") or "") for p in _parts(variant)])


def _figure_notes(variant: dict, *, has_figure: bool) -> list[str]:
    """Réserves — jamais des refus — sur l'accord entre l'exercice et l'image.

    Les deux cas sont symétriques et aucun n'est fatal, parce qu'aucun ne se
    règle par un appel LLM de plus : ils se règlent à la relecture, où le
    professeur voit la page et peut ajouter, retirer ou recadrer une image.

      • une image est attachée et l'énoncé ne s'en sert nulle part — elle
        s'imprimerait en décor ;
      • l'énoncé s'appuie sur un visuel qu'aucune image n'accompagne
        (§ indigo_check.figure_note).

    La cohérence de FOND (le dessin dit-il la même chose que l'énoncé ?) demande
    de voir le dessin : c'est la passe 5 qui en juge, sur la description de la
    passe 1."""
    text = _variant_text(variant)
    if not has_figure:
        note = indigo_check.figure_note(text, has_figure=False)
        return [note] if note else []
    # le marqueur de placement compte comme un usage : le modèle n'a pas à
    # l'écrire (le placement est déterministe), mais s'il l'écrit, c'est bien
    # qu'il veut le dessin là — le lui reprocher serait un faux positif.
    if (indigo_check.mentions_figure(text)
            or statement_mod.has_figure_marker(text)):
        return []
    return ["la figure du manuel est imprimée à côté de cet exercice, mais "
            "l'énoncé ne s'en sert jamais : appuie une question dessus, ou "
            "retire l'image à la relecture"]


def _response_repetition_notes(variant: dict) -> list[str]:
    """Repère toute réponse proposée déjà recopiée dans la consigne.

    Le soupçon est légitime : une proposition répétée peut révéler la réponse
    (« consomme 150 litres » puis « combien ? 15 / 150 / 1500 »).

    Mais ce n'est qu'un SOUPÇON, et il ne peut pas être une porte. Sur tout le
    chapitre statistiques, l'énoncé PORTE les données — tableau d'effectifs,
    valeurs d'un histogramme — et la réponse à « quel pourcentage pour la
    cuisine ? » est forcément l'un des nombres écrits au-dessus. Le contrôle y
    est structurellement toujours vrai : il brûlait les quatre tentatives de
    chaque exercice de lecture de graphique, c'est-à-dire de la compétence
    entière (pages 86-87). Python ne sait pas distinguer « la donnée nécessaire
    au calcul » de « la réponse recopiée » ; le relecteur, lui, le voit d'un
    coup d'œil. On lui laisse donc le jugement."""
    context = str(variant.get("statement") or "") if variant.get("response_type") == COMPOSITE else ""
    problems: list[str] = []
    for i, part in enumerate(_parts(variant)):
        statement = _plain(" ".join((context, str(part.get("statement") or ""))))
        proposals = ((part.get("cols") or [])
                     if part.get("response_type") == "checkbox_grid"
                     else (part.get("choices") or []))
        repeated: list[str] = []
        for value in proposals:
            plain = _plain(str(value))
            if plain and re.search(rf"(?<!\w){re.escape(plain)}(?!\w)", statement):
                repeated.append(str(value))
        if repeated:
            where = f"sous-question {_sub_label(i)}. : " if variant.get("response_type") == COMPOSITE else ""
            problems.append(where + "les réponses proposées sont répétées dans "
                            "l'énoncé ; laisse-les uniquement dans les cases")
            continue
        # Une réponse « 150 L » et une donnée « 150 litres » ne sont pas égales
        # textuellement, mais livrent évidemment le même résultat. Ce filet ne
        # porte que sur les BONNES cases numériques : les autres nombres restent
        # des données nécessaires au calcul.
        choices = part.get("choices") or []
        for correct in (part.get("correct") or []):
            if not isinstance(correct, int) or not 0 <= correct < len(choices):
                continue
            numbers = re.findall(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?![\w])",
                                 _plain(str(choices[correct])))
            if len(numbers) == 1 and any(re.search(
                    rf"(?<![\w]){re.escape(number)}(?![\w])", statement)
                               for number in numbers):
                where = (f"sous-question {_sub_label(i)}. : "
                         if variant.get("response_type") == COMPOSITE else "")
                problems.append(where + "la bonne réponse numérique est déjà "
                                "donnée dans l'énoncé")
                break
    return problems


def _task_units(variant: dict) -> int:
    """Nombre minimal de tâches distinctes réellement proposées à l'élève."""
    units = 0
    for part in _parts(variant):
        units += (len(part.get("rows") or [])
                  if part.get("response_type") == "checkbox_grid" else 1)
    return units


def _fidelity_problem(variant: dict, tasks: int) -> str:
    """Garde-fou certain contre une réduction des sous-questions d'origine."""
    units = _task_units(variant)
    if tasks >= 2 and units < tasks:
        return (f"{units} tâche(s) évaluée(s) pour {tasks} dans la source ; "
                "conserve chaque sous-question d'origine dans une question ou "
                "une ligne distincte")
    return ""


def _source_fidelity_problems(trio: dict[str, dict], source: Source) -> list[str]:
    """Le même contrôle, sur les trois variantes, chacune nommée."""
    return [f"{VARIANT_LABEL[kind]} : {problem}" for kind in VARIANTS
            if (problem := _fidelity_problem(trio.get(kind) or {},
                                             len(source.tasks)))]


def _local_notes(trio: dict[str, dict], *, has_figure: bool,
                 active: tuple[str, ...] = VARIANTS) -> list[str]:
    """Réserves de relecture d'un trio : tout ce qui se règle sous les yeux du
    professeur, et rien qui doive coûter une tentative de plus."""
    notes: list[str] = []
    for kind in active:
        variant = trio.get(kind)
        if not isinstance(variant, dict):
            continue
        for note in (_figure_notes(variant, has_figure=has_figure)
                     + _response_repetition_notes(variant)):
            notes.append(f"{VARIANT_LABEL[kind]} : {note}")
    return notes


def _variant_problems(variant: dict, *, has_figure: bool, tasks: int = 0) -> list[str]:
    """TOUS les défauts d'UNE variante, sans son nom devant.

    Trois familles de contrôles, et aucune ne coûte un token :
      • ceux de « QCM only » (indigo_check : bornes, distracteurs distincts,
        renvois hors feuille, et sympy qui recalcule ce qui a été déclaré),
        appliqués sous-question par sous-question sur un composite ;
      • ceux du mode : un guide de 30 mots au plus, qui ne vend la mèche
        d'aucune des questions ;
      • la fidélité à la source : chaque tâche d'origine reste évaluée.

    C'est l'unité de mesure de la passe 5 : ces défauts lui sont rendus en clair
    pour qu'elle les répare, et le MÊME comptage décide ensuite si sa retouche
    est gardée. Une passe qu'on ne mesure pas avec la règle qu'on lui a donnée
    n'est pas mesurée du tout.
    """
    problems = _verify_variant(variant, has_figure=has_figure)
    guide = variant.get("guide") or ""
    n = _words(guide)
    if n > GUIDE_MAX_WORDS:
        problems.append(f"guide de {n} mots, maximum {GUIDE_MAX_WORDS} — une "
                        "piste et un rappel de leçon suffisent")
    elif n < GUIDE_MIN_WORDS:
        problems.append(f"guide absent ou trop court ({n} mot(s)) — donne une "
                        "piste et rappelle la méthode")
    leak = _guide_leaks(guide, variant)
    if leak:
        problems.append(leak)
    fidelity = _fidelity_problem(variant, tasks)
    if fidelity:
        problems.append(fidelity)
    return problems


def _local_problems(trio: dict[str, dict], *, has_figure: bool, tasks: int = 0,
                    active: tuple[str, ...] = VARIANTS) -> list[str]:
    """Les défauts des variantes ACTIVES, chacune nommée, plus ceux de la famille.

    Le seul contrôle qui ne se juge pas variante par variante est celui de la
    FAMILLE : des exercices réellement distincts (un « facile » recopié de la
    base n'est pas une graduation).
    """
    problems: list[str] = []
    for kind in active:
        variant = trio.get(kind)
        if not isinstance(variant, dict):
            problems.append(f"variante « {VARIANT_LABEL[kind]} » absente de la sortie")
            continue
        # `indigo_check` porte déjà TOUTE la logique de format des trois types
        # (unicité de la bonne case en choix unique, bornes des grilles,
        # distracteurs distincts, sympy) : la redoubler ici la ferait diverger.
        # Les écarts de FIGURE n'y sont pas : ils ne sont pas des défauts de
        # l'exercice mais des réserves de relecture (§ `_local_notes`).
        for p in _variant_problems(variant, has_figure=has_figure, tasks=tasks):
            problems.append(f"{VARIANT_LABEL[kind]} : {p}")

    statements = [_family_key(trio.get(k)) for k in active]
    seen = [s for s in statements if s]
    if len(set(seen)) != len(seen):
        problems.append("deux variantes sont de vrais CLONES (mêmes questions et "
                        "mêmes cases) : les niveaux actifs doivent différer")
    return problems


# ------------------------------------------------ conversion vers le contrat

def _leaf_bareme(part: dict) -> float | None:
    """Barème CODÉ d'une question (§ services.scoring), ou None si sa structure
    sort des bornes — le validateur partagé la refusera de toute façon, et une
    exception ici masquerait la vraie raison."""
    try:
        return scoring.qcm_bareme(part.get("response_type"),
                                  {"choices": part.get("choices") or [],
                                   "rows": part.get("rows") or []})
    except ValueError:
        return None


def _to_raw_part(part: dict) -> dict:
    """Une QUESTION → le schéma partagé (`answer`/`choices`/`grid`).

    `bareme_points` porte le barème CODÉ : pour une sous-question de composite,
    c'est la seule façon de l'imposer, le validateur sommant ensuite les parties
    pour obtenir celui de l'exercice entier (§ exercise_gen, branche composite).
    Sans lui, une sous-question tomberait sur le barème de repli générique."""
    rtype = part.get("response_type")
    raw = {"response_type": rtype, "statement": part.get("statement") or "",
           "bareme_points": _leaf_bareme(part)}
    if rtype == "checkbox_grid":
        raw["answer"] = {"type": "grid", "cols": part.get("cols") or [],
                         "rows": part.get("rows") or []}
    else:
        raw["choices"] = part.get("choices") or []
        raw["answer"] = {"type": "choice", "correct": part.get("correct") or []}
    return raw


def _to_raw(variant: dict) -> dict:
    """Forme compacte du modèle → dict attendu par `_validate_exercise`.

    Strictement le schéma partagé, pour garder le validateur ÉPROUVÉ sans garder
    son prompt — c'est le même passage que `indigo_qcm._to_raw`, à la différence
    du mode près : `correction` porte le guide élève de CETTE variante, pas un
    guide commun aux trois.

    Un composite s'y traduit sans rien inventer : son contexte devient
    l'énoncé, ses questions la liste `answer.parts`, et chacune est revalidée
    comme un exercice à part entière (`part_mode`)."""
    if variant.get("response_type") == COMPOSITE:
        return {"response_type": COMPOSITE, "kind": "application",
                "statement": variant.get("statement") or "",
                "correction": variant.get("guide") or "",
                "answer": {"parts": [_to_raw_part(p) for p in _parts(variant)]}}
    return {**_to_raw_part(variant), "kind": "application",
            "correction": variant.get("guide") or ""}


def _rejection_reason(raw: dict, competency: Competency, db: Session) -> str:
    """Pourquoi le validateur partagé a refusé cet exercice, en clair.

    Le DOUBLON est traité à part, et ce n'est pas un luxe : `diagnose_rejection`
    ne le voit pas (il ne connaît pas les empreintes déjà prises) et rend alors
    un motif vide. Le générateur relançait donc trois tentatives sans savoir
    quoi corriger — douze appels pour rien. On rejoue la validation sur un jeu
    d'empreintes VIERGE : si elle passe, la seule chose qui clochait était la
    ressemblance avec un exercice déjà retenu, et on le dit."""
    if exercise_gen._validate_exercise(raw, competency, db, set(),
                                       allow_geometry_text=True) is not None:
        return ("doublon d'un exercice déjà retenu pour cette compétence — change "
                "les nombres ET la situation, pas seulement la formulation")
    reason = exercise_gen.diagnose_rejection(raw, competency)
    return f"refusé par le validateur partagé — {reason}"


def _finalize_trio(trio: dict[str, dict], competency: Competency, db: Session,
                   existing_norms: set[str],
                   active: tuple[str, ...] = VARIANTS
                   ) -> tuple[list[tuple[str, dict]], list[str]]:
    """Valide les variantes ACTIVES. Retourne (contrats internes, problèmes).

    Le dé-doublonnage travaille sur une COPIE de `existing_norms` et n'est
    reversé qu'en cas de conservation effective. Sans cette précaution, une
    reprise après incident laisserait ses empreintes derrière elle et se ferait
    refuser ses propres variantes comme « doublons » — un échec garanti,
    invisible dans les journaux.

    ON GARDE CE QUI EST VALIDE, et les manques partent en réserves. Deux niveaux
    valent mieux que zéro, et le professeur complétera. Une variante refusée ne
    fait plus tomber les deux autres : la famille était « indivisible » tant
    qu'on pouvait la refaire, et on ne la refait plus (§ module). Les niveaux
    conservés gardent leur nom : un « Facile » reste facile même quand la base a
    sauté (c'est la persistance qui sait accrocher le premier venu à la ligne
    principale, § services.indigo._persist_multipass_family) — le renommer
    mentirait sur sa difficulté."""
    norms = set(existing_norms)
    kept: list[tuple[str, dict]] = []
    problems: list[str] = []
    for kind in active:                        # base d'abord (§ VARIANTS)
        variant = trio.get(kind)
        if not isinstance(variant, dict):
            problems.append(f"variante « {VARIANT_LABEL[kind]} » absente")
            continue
        raw = _to_raw(variant)
        valid = exercise_gen._validate_exercise(
            raw, competency, db, norms, allow_geometry_text=True)
        if valid is None:
            problems.append(f"{VARIANT_LABEL[kind]} : {_rejection_reason(raw, competency, db)}")
            continue
        if valid["response_type"] not in ALLOWED_TYPES:
            problems.append(f"{VARIANT_LABEL[kind]} : format "
                            f"« {valid['response_type']} » interdit en QCM multipass")
            continue
        if valid["response_type"] == COMPOSITE:
            # Le barème d'un composite est la SOMME des barèmes codés de ses
            # sous-questions : le validateur partagé l'a déjà additionnée à
            # partir de ce que `_to_raw_part` a posé sur chacune. Le recalculer
            # ici demanderait à `qcm_bareme` un format qu'il ne connaît pas.
            if not float(valid["grading"].get("bareme_points") or 0) > 0:
                problems.append(f"{VARIANT_LABEL[kind]} : barème nul — une "
                                "sous-question au moins n'a pas de réponse à noter")
                continue
        else:
            try:
                valid["grading"] = scoring.with_qcm_bareme(valid["grading"],
                                                           valid["response_type"])
            except ValueError as e:
                problems.append(f"{VARIANT_LABEL[kind]} : {e}")
                continue
        kept.append((kind, valid))
    if not kept:
        return [], problems or ["aucune variante exploitable"]
    existing_norms |= norms
    return kept, problems


# ------------------------------------------------------------------ les passes

def _call(db: Session, stage: str, system: str, payload: dict, cid: str) -> dict | None:
    return indigo_llm.call(db, stage, system, payload, cid)


def _source_payload(grade: str, competency: Competency, manual: dict,
                    figure: str = "") -> dict:
    """Le socle commun à toutes les passes qui voient une source : le manuel
    ÉLÈVE. Le corrigé du manuel du PROFESSEUR n'y figure PAS : la passe
    contexte seule reçoit les CANDIDATS bruts (`corriges_candidats`,
    § `_pass_context`), et seule la génération reçoit son verdict déjà validé
    (`source.corrige_prof`/`source.invent_context`, portés par `Source`). Le
    filtre, le solveur, la mise en page et la retouche continuent de ne voir
    que le manuel élève — le solveur en particulier perdrait son indépendance
    s'il lisait le corrigé.

    `has_figure` dit si l'OCR a isolé un dessin ; `figure` (passe 2 et suivantes)
    le DÉCRIT, d'après le texte, puisque aucun de ces modèles ne le voit."""
    source = {"number": str(manual.get("number", "")),
              "statement": manual.get("statement", ""),
              "has_figure": bool(manual.get("has_figure"))}
    if figure:
        source["figure"] = figure
    return {"grade_level": grade,
            "competency_code": competency.code,
            "competency_label": _competency_name(competency),
            "chapter": f"{competency.chapter_code} {competency.chapter_name}".strip(),
            "domain": f"{competency.domain_code} {competency.domain_name}".strip(),
            "source": source}


def _source_tasks(statement: str) -> list[str]:
    """Repli local : extrait les questions explicites sans interpréter le fond."""
    text = str(statement or "").strip()
    if not text:
        return []
    # Vision conserve normalement une consigne par ligne. Les points
    # d'interrogation couvrent aussi les rares extractions remises sur une ligne.
    candidates = [line.strip() for line in text.splitlines() if "?" in line]
    if len(candidates) < 2:
        candidates = [chunk.strip() for chunk in re.findall(
            r"[^?]{3,}\?", text, flags=re.MULTILINE)]
    out: list[str] = []
    for task in candidates:
        task = re.sub(r"^\s*(?:\d+|[a-z])\s*[.)]\s*", "", task,
                      flags=re.IGNORECASE).strip()
        if task and task not in out:
            out.append(task)
    return out[:8]


def _pass_filter(db, competencies, grade, manual, cid) -> tuple[Source | None, str]:
    """PASSE 1 — rattachement à la compétence et dernier nettoyage.

    Les nouvelles sources Vision ne sont JAMAIS rejetées ici : leur texte, leur
    présence de figure et leur crop ont déjà été lus directement sur la page.
    Même une sortie incomplète de cette passe retombe sur le texte Vision et sur
    le meilleur rapprochement local du titre rose. L'ancien chemin OCR conserve
    son comportement strict pour les autres modes et les anciennes extractions.
    """
    candidates = list(competencies) if isinstance(competencies, (list, tuple)) else [competencies]
    candidates = [c for c in candidates if c is not None]
    ranked = _rank_competencies(str(manual.get("competency_title") or ""), candidates)
    # Ne jamais rendre la bonne compétence invisible au classifieur. La liste
    # complète coûte peu face à une famille de cinq passes et élimine les faux
    # rattachements lorsque le titre est absent ou imparfaitement lu.
    shortlist = ranked or candidates
    primary = shortlist[0] if shortlist else None
    if primary is None:
        return None, "aucune compétence disponible pour rattacher l'exercice"
    payload = _source_payload(grade, primary, manual)
    payload["competencies"] = [
        {"id": c.id, "code": c.code, "short_id": c.short_id, "label": c.label}
        for c in shortlist]
    payload["source"]["competency_title"] = str(
        manual.get("competency_title") or "").strip()
    if manual.get("figure_description"):
        payload["source"]["figure"] = manual["figure_description"]
    data = _call(db, "mp_filter", _system("filter", primary, grade), payload,
                 f"{cid}-filter") or {}
    verdict = str(data.get("verdict") or "").strip().lower()
    reason = str(data.get("reason") or "").strip()
    clean = statement_mod.repair_latex_control_chars(
        str(data.get("enonce") or "").strip())
    tasks = [str(task).strip() for task in (data.get("taches_source") or [])
             if str(task).strip()]
    comp_id = str(data.get("competency_id") or "").strip()
    comp_code = str(data.get("competency_code") or "").strip()
    if manual.get("vision_extracted"):
        # Vision a déjà fait le travail de lecture difficile. La passe 1 n'a le
        # droit ni de déclasser `has_figure`, ni de supprimer une sous-question,
        # ni de transformer une hésitation de classement en rejet.
        original = statement_mod.repair_latex_control_chars(
            str(manual.get("statement") or "").strip())
        # Vision a déjà rendu un texte propre. La passe 1 classe et dresse la
        # checklist des tâches, mais sa reformulation ne devient jamais une
        # nouvelle source d'autorité : même longueur ne signifie pas même sens.
        clean = original
        return Source(
            statement=clean,
            needs_figure=bool(manual.get("has_figure")),
            figure=str(manual.get("figure_description")
                       or data.get("figure") or "").strip(),
            competency_id=comp_id,
            competency_code=comp_code,
            original_statement=original,
            tasks=tasks or _source_tasks(original)), ""
    if verdict != "keep" or len(clean) < SOURCE_MIN_CHARS:
        return None, reason or "source jugée inexploitable (aucun énoncé nettoyé rendu)"
    # Le besoin de figure est confirmé par L'UN OU L'AUTRE : le jugement du
    # modèle, ou l'énoncé nettoyé lui-même. Un exercice qui parle de « la figure
    # ci-contre » en a besoin, quoi qu'en dise la case cochée à côté.
    needs = bool(data.get("besoin_figure")) or indigo_check.mentions_figure(clean)
    return Source(statement=clean, needs_figure=needs,
                  figure=str(data.get("figure") or "").strip(),
                  competency_id=comp_id, competency_code=comp_code,
                  tasks=tasks or _source_tasks(clean)), ""


_CONTEXT_MODES = ("source", "source_corrige", "invent", "reject")


def _correction_candidates(grade: str, competency: Competency, number: str) -> list[str]:
    """Corrigés CANDIDATS du manuel PROF pour cet exercice, sur la compétence
    RÉELLEMENT résolue par la passe 1 — jamais une devinette d'avant celle-ci.
    `services.indigo._corrections_for` lit l'index déjà construit (gratuit,
    couche texte) et peut en rendre PLUSIEURS : un même chapitre du manuel prof
    contient parfois plusieurs lots d'exercices qui repartent au même numéro,
    donc certains candidats parlent d'un tout autre exercice. Choisir n'est
    plus fait ici À L'AVEUGLE (l'ancien comportement, § `_pass_context`) : la
    passe contexte reçoit TOUS les candidats et tranche lequel, s'il y en a un,
    correspond vraiment — une absence (index non construit, numéro hors plage)
    rend simplement une liste vide."""
    from . import indigo as indigo_mod
    return indigo_mod._corrections_for(grade, competency.chapter_name, number)


def _pass_context(db, competency, grade, manual, source: Source, cid,
                  candidates: list[str]) -> None:
    """PASSE CONTEXTE — entre le filtre et la génération. Juge si l'énoncé
    SEUL suffit à écrire un exercice juste (le cas le plus fréquent), si l'un
    des `candidates` (§ `_correction_candidates`) comble ce qu'une figure
    indispensable emporte seule, ou si ni l'un ni l'autre n'y suffisent — la
    source est alors soit trop dégradée, soit dépendante d'une figure
    introuvable, mais le contexte pédagogique (compétence, type de problème)
    reste assez sûr pour INVENTER un exercice fidèle à cet esprit plutôt que de
    prétendre reproduire une donnée inconnue. Seule cette passe voit plusieurs
    candidats ambigus ; la génération (§ `_pass_generate`) ne reçoit plus
    qu'UN corrigé déjà validé, jamais une liste à trancher elle-même — c'est
    elle qui allège le prompt de génération d'autant.

    Rend le verdict par EFFET DE BORD sur `source` (`corrige_prof` xor
    `invent_context`, jamais les deux). Lève `InfeasibleSource` quand même
    l'invention n'a rien de fiable où s'ancrer — propagée telle quelle par
    `_resolve_family`, exactement comme l'ancienne porte de faisabilité de la
    passe 2, mais désormais AVANT tout appel de génération : un rejet ne coûte
    plus le prix d'une tentative de génération ratée."""
    payload = _source_payload(grade, competency,
                              {**manual, "statement": source.statement,
                               "has_figure": source.needs_figure},
                              figure=source.figure)
    payload["source"]["taches_source"] = list(source.tasks)
    if candidates:
        payload["source"]["corriges_candidats"] = candidates
    data = _call(db, "mp_context", _system("context", competency, grade),
                payload, f"{cid}-context") or {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in _CONTEXT_MODES:
        # Repli sûr : un champ manqué ou mal formé ne doit ni bloquer la
        # source, ni la faire passer pour infaisable — c'est le cas normal
        # (énoncé seul suffisant) qui redevient le défaut.
        mode = "source"
    if mode == "reject":
        raise InfeasibleSource(
            str(data.get("raison") or "").strip()
            or "aucun contexte pédagogique fiable : ni l'énoncé, ni le "
               "corrigé du professeur ne permettent d'écrire un exercice "
               "juste, même en s'en inspirant")
    if mode == "source_corrige":
        source.corrige_prof = str(data.get("corrige") or "").strip()
    elif mode == "invent":
        source.invent_context = str(data.get("contexte_pedagogique") or "").strip()


_V_SHAPES = (
    'V simple : `response_type,statement,guide,check` + `choices,correct` ou\n'
    '`cols,rows:[{"label":str,"correct":int}]`.\n'
    'V composite : `response_type:"composite",statement,guide,questions`; chaque\n'
    'question est une V simple sans guide.')


def _exercices_schema(active: tuple[str, ...]) -> str:
    return '{"facile":V,"base":V}'


def _niveaux_substituted(text: str, active: tuple[str, ...]) -> str:
    """Le token commun aux deux enveloppes (solo et par lot)."""
    return text.replace("§NIVEAUX§", "Facile et Base")


def _generate_system(competency: Competency, grade: str,
                     active: tuple[str, ...]) -> str:
    """Le prompt de la passe 2, pour UNE source."""
    text = _niveaux_substituted(_system("generate", competency, grade), active)
    schema = (f'JSON uniquement :\n`{{"exercices":{_exercices_schema(active)}}}`.'
             f'\n{_V_SHAPES}')
    return text.replace("§SCHEMA§", schema)


# Préfixé au prompt solo, jamais dupliqué : les règles de fidélité et de
# format restent écrites UNE fois (§ multipass_generate.txt) et s'appliquent à
# CHAQUE source, indépendamment des autres — la faisabilité, elle, est déjà
# tranchée avant que cette enveloppe n'existe (§ `_pass_context`).
_PAIR_PREAMBLE = (
    "PLUSIEURS sources INDÉPENDANTES suivent (`sources`, chacune son propre "
    "`number`) : applique EXACTEMENT les règles ci-dessous à CHACUNE séparément "
    "— aucune ne s'appuie sur une autre, aucune ne redistribue ses données "
    "entre elles. Partout où le texte dit « la source » ou « source.X », lis "
    "la source EN COURS de traitement, dans `sources[i]`.\n\n")


def _pair_generate_system(competency: Competency, grade: str,
                          active: tuple[str, ...]) -> str:
    """Le même prompt, appliqué à PLUSIEURS sources d'un coup (§ run_family_pair,
    réglage indigo_multipass_batch_size) : seule l'enveloppe change (`sources`
    en entrée, `lots` en sortie), jamais les règles de fidélité elles-mêmes."""
    text = _niveaux_substituted(_system("generate", competency, grade), active)
    schema = (f'JSON uniquement, un élément de `lots` PAR source reçue, dans '
             f'le MÊME ordre, portant son `source_number` recopié EXACTEMENT :\n'
             f'`{{"lots":[{{"source_number":str,'
             f'"exercices":{_exercices_schema(active)}}}]}}`.\n{_V_SHAPES}')
    return _PAIR_PREAMBLE + text.replace("§SCHEMA§", schema)


def _pass_generate(db, competency, grade, manual, source: Source, feedback, cid,
                   attempt, previous: dict[str, dict] | None = None,
                   active: tuple[str, ...] = VARIANTS) -> dict[str, dict]:
    """PASSE 2 — les variantes ACTIVES, ensemble, depuis la source NETTOYÉE.

    L'OCR brut ne lui est PAS transmis : il est remplacé par l'énoncé que la
    passe 1 a nettoyé. Le lui montrer en plus rouvrirait la porte à tout ce que
    le nettoyage vient d'enlever — mobilier de page, coupures, symboles perdus.

    `source.has_figure`/`source.figure` disent si les variantes s'appuient sur
    le dessin du manuel, et ce qu'il porte. Elles partagent LA MÊME image : ce
    que le dessin montre ne peut donc pas changer d'un niveau à l'autre, sous
    peine d'imprimer un dessin qui ment.

    La FAISABILITÉ est déjà tranchée en amont, par la passe CONTEXTE
    (§ `_pass_context`, `_resolve_family`) : `source.corrige_prof` porte un
    corrigé du manuel du professeur déjà VALIDÉ comme parlant du même exercice
    (jamais une liste de candidats à trancher soi-même), `source.invent_context`
    autorise explicitement l'invention quand ni l'énoncé ni le corrigé n'y
    suffisaient. Une source jugée infaisable n'atteint plus cette passe du
    tout — `InfeasibleSource` est levée plus tôt, avant tout appel de
    génération, ce qui allège d'autant ce prompt-ci.

    `feedback` ne sert plus qu'à UN cas : une sortie AMPUTÉE, où il manque une
    variante entière. Ce n'est pas un défaut de qualité mais un appel raté, et
    c'est la seule chose que rejouer répare. Un exercice imparfait, lui, se
    répare à la passe 5 — le renvoyer ici redonnait le même, en payant deux fois
    (§ module : 63 générations pour 8 sources, défauts 85 → 81)."""
    payload = {**_source_payload(grade, competency,
                                 {**manual, "statement": source.statement,
                                  "has_figure": source.needs_figure},
                                 figure=source.figure),
               "guide_max_words": GUIDE_MAX_WORDS}
    payload["source"]["taches_source"] = list(source.tasks)
    if source.corrige_prof:
        payload["source"]["corrige_prof"] = source.corrige_prof
    if source.invent_context:
        payload["source"]["contexte_invention"] = source.invent_context
    if feedback:
        payload["a_corriger"] = {
            "tentative": attempt,
            "instruction": ("Corrige seulement les défauts signalés dans les "
                            "variantes précédentes. Conserve tout le reste "
                            "et rends de nouveau les variantes complètes."),
            "problemes": feedback}
        if previous:
            payload["a_corriger"]["exercices_precedents"] = previous
    data = _call(db, "mp_generate", _generate_system(competency, grade, active),
                 payload, f"{cid}-gen{attempt}") or {}
    # Si la réparation omet accidentellement une variante, on garde celle de la
    # tentative précédente plutôt que de transformer une correction locale en
    # rejet structurel de toute la famille.
    out: dict[str, dict] = dict(previous or {}) if feedback else {}
    for kind in active:
        variant = _exercises_of(data).get(kind)
        if isinstance(variant, dict):
            out[kind] = _normalize_variant(variant)
    return out


def _exercises_of(data: dict) -> dict:
    """Les trois variantes rendues, quelle que soit la forme employée.

    Flash rend parfois `exercices` en LISTE, chaque entrée portant son `niveau`.
    L'y chercher par clé levait un `AttributeError` qui coûtait la famille
    entière — quatre tentatives, puis REJECTED_GENERATION, pour une sortie
    parfaitement exploitable (n°47 des pages 67-68). Une liste est donc relue par
    ses libellés, et tout le reste rend un dictionnaire vide."""
    raw = data.get("exercices")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}
    by_label = {_fold_label(VARIANT_LABEL[k]): k for k in VARIANTS}
    out: dict = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = by_label.get(_fold_label(entry.get("niveau")))
        if kind:
            out[kind] = {k: v for k, v in entry.items() if k != "niveau"}
    return out


def _question_view(part: dict) -> dict:
    """Ce que le solveur voit d'UNE question : son format, son énoncé, ses cases."""
    rtype = part.get("response_type")
    view = {"format": SOLVER_FORMAT.get(rtype, rtype),
            "enonce": part.get("statement", "")}
    if rtype == "checkbox_grid":
        view["colonnes"] = list(part.get("cols") or [])
        view["lignes"] = [str(r.get("label", "")) for r in (part.get("rows") or [])]
    else:
        view["propositions"] = list(part.get("choices") or [])
    return view


def _solver_view(kind: str, variant: dict) -> dict:
    """CE QUE LE SOLVEUR VOIT d'une variante, et rien de plus.

    Construit champ par champ, jamais par copie du dict : le jour où `correct`,
    `check` ou `guide` se glisserait dans ce payload, la passe 5 continuerait de
    « valider » sans plus rien vérifier, et personne ne s'en apercevrait.

    Toute variante est présentée comme une LISTE de questions — un exercice
    simple en compte une, un composite autant que de sous-questions. Une seule
    forme, donc un seul chemin de comparaison."""
    view = {"niveau": VARIANT_LABEL[kind]}
    if variant.get("response_type") == COMPOSITE:
        view["contexte"] = variant.get("statement", "")
    view["questions"] = [_question_view(p) for p in _parts(variant)]
    return view


def _read_answers(raw, n_questions: int) -> list[list[int]] | None:
    """Réponse du solveur → une liste d'entiers PAR QUESTION, ou None.

    None n'est pas un détail : c'est « le solveur n'a pas su trancher », ce que
    la passe 5 lit comme un signal d'énoncé douteux. Mieux vaut donc None qu'une
    lecture optimiste — une seule case illisible rend toute la réponse
    inexploitable, et une liste plate sur un exercice à PLUSIEURS questions est
    ambiguë (rien ne dit où s'arrête la première), donc refusée."""
    if isinstance(raw, int):
        raw = [[raw]]
    if not isinstance(raw, list):
        return None
    if raw and all(isinstance(v, int) for v in raw):
        # forme plate : légitime pour une question unique (les cases cochées, ou
        # la colonne retenue ligne par ligne), ambiguë au-delà.
        raw = [raw] if n_questions == 1 else None
    if not isinstance(raw, list) or len(raw) != n_questions:
        return None
    out: list[list[int]] = []
    for entry in raw:
        if isinstance(entry, int):
            entry = [entry]
        if not isinstance(entry, list):
            return None
        values = [_as_int(v) for v in entry]
        if any(v is None for v in values):
            return None
        out.append(values)
    return out


def _pass_solve(db, competency, grade, trio, figure: str, cid,
                attempt) -> dict[str, list[list[int]] | None]:
    """PASSE 3 — résolution INDÉPENDANTE. Rend {niveau -> cases cochées}.

    N'ENVOIE QUE LES ÉNONCÉS ET LES CASES À COCHER (§ `_solver_view`). Ni
    `correct`, ni `check`, ni guide : c'est la seule façon d'obtenir un deuxième
    avis plutôt qu'un acquiescement.

    La réponse est une liste d'entiers PAR QUESTION, dans les trois formats — les
    cases cochées pour un QCM, la colonne retenue ligne par ligne pour une
    grille (§ `_declared`, qui produit exactement la même forme).

    Il ne VOIT PAS le dessin — aucun de ces modèles n'a de vision. Il en reçoit
    la description faite par la passe 1, à partir du SEUL texte source : elle ne
    dérive d'aucune réponse de la passe 2, l'indépendance est intacte. Si elle ne
    suffit pas à résoudre, il répond `null` et le dit."""
    payload = {"grade_level": grade,
               "competency_label": _competency_name(competency),
               "qcm": [_solver_view(k, trio[k]) for k in VARIANTS if k in trio]}
    if figure:
        payload["figure"] = figure
    data = _call(db, "mp_solve", _system("solve", competency, grade), payload,
                 f"{cid}-solve{attempt}") or {}
    by_label = {VARIANT_LABEL[k].lower(): k for k in VARIANTS}
    out: dict[str, list[list[int]] | None] = {}
    for item in (data.get("solutions") or []):
        if not isinstance(item, dict):
            continue
        kind = by_label.get(str(item.get("niveau") or "").strip().lower())
        if kind is None or kind not in trio:
            continue
        raw = item.get("reponses", item.get("reponse"))
        out[kind] = _read_answers(raw, len(_parts(trio[kind])))
    return out


def _solver_disagreements(trio: dict[str, dict],
                          solved: dict[str, list[list[int]] | None],
                          *, has_figure: bool = False) -> list[str]:
    """Désaccords entre la génération et la résolution indépendante, en clair.

    Question par question : sur un composite, savoir QUELLE sous-question fait
    désaccord est justement ce que la passe 5 a besoin de trancher.

    Ce n'est PAS un verdict : un solveur peut se tromper. C'est la matière
    première de la passe 5."""
    out = []
    for kind in VARIANTS:
        if kind not in trio:
            continue
        variant = trio[kind]
        found = solved.get(kind)
        if found is None:
            # Avec une figure, « je n'ai pas tranché » a une autre cause,
            # parfaitement légitime : le solveur ne voit pas le dessin. Lui
            # imputer un énoncé ambigu ferait rejeter tous les exercices de
            # géométrie ; le taire ferait croire à un contrôle qui n'a pas eu lieu.
            out.append(f"{VARIANT_LABEL[kind]} : le solveur indépendant n'a pas su "
                       + ("trancher sans VOIR la figure — ce n'est pas en soi un "
                          "défaut de l'énoncé, mais cette variante n'a reçu AUCUNE "
                          "vérification indépendante : refais toi-même le calcul"
                          if has_figure else
                          "trancher — énoncé probablement ambigu ou incomplet"))
            continue
        declared = _declared(variant)
        parts = _parts(variant)
        if len(found) != len(declared):
            out.append(f"{VARIANT_LABEL[kind]} : le solveur a répondu à "
                       f"{len(found)} question(s) sur {len(declared)}")
            continue
        for i, (part, want, got) in enumerate(zip(parts, declared, found)):
            if part.get("response_type") != "checkbox_grid":
                got = sorted(set(got))          # l'ordre des cases ne dit rien
            if got != want:
                check = part.get("check") if isinstance(part.get("check"), dict) else {}
                confirmed = (str(check.get("kind") or "none") != "none"
                             and not indigo_check.check_math(part))
                suffix = (" ; le contrôle Python/SymPy confirme la réponse de la "
                          "génération : recalcule et ne suis pas automatiquement "
                          "le solveur" if confirmed else "")
                out.append(f"{VARIANT_LABEL[kind]}{_where(variant, i)} : la "
                           f"génération annonce {_answer_label(part, want)}, le "
                           f"solveur indépendant trouve {_answer_label(part, got)}"
                           f"{suffix}")
    return out


def _where(variant: dict, i: int) -> str:
    """« · sous-question b. » sur un composite, rien sur un exercice simple."""
    return (f" · sous-question {_sub_label(i)}."
            if variant.get("response_type") == COMPOSITE else "")


def _answer_label(part: dict, cases: list[int]) -> str:
    """Une réponse écrite pour être LUE par l'audit : les propositions cochées,
    ou la colonne retenue pour chaque ligne — jamais une liste d'indices nus."""
    if part.get("response_type") == "checkbox_grid":
        cols = part.get("cols") or []
        rows = part.get("rows") or []
        labels = []
        for i, col in enumerate(cases):
            label = rows[i].get("label", f"ligne {i + 1}") if i < len(rows) else f"ligne {i + 1}"
            labels.append(f"« {label} » → "
                          + (f"« {cols[col]} »" if isinstance(col, int) and 0 <= col < len(cols)
                             else str(col)))
        return " ; ".join(labels) or "aucune ligne"
    choices = part.get("choices") or []
    labels = [f"n°{i} « {choices[i]} »" if isinstance(i, int) and 0 <= i < len(choices)
              else str(i) for i in cases]
    return " + ".join(labels) or "aucune case"


# ------------------------------------------------------------ mise en page
# Ce que la mise en page a le droit de faire, et rien d'autre : réécrire du
# TEXTE, et regrouper des questions à propositions identiques en une grille.
# Les propositions elles-mêmes, les bonnes cases et le guide ne traversent
# jamais cette passe — c'est Python qui les reporte, à l'identique.
_GRID_MAX_COLS = 4
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?")
# Numérotation d'une question ou jalon de rédaction, en tête de ligne ou annoncé
# par son mot : le nombre y est une étiquette, pas une donnée.
_ENUM_RE = re.compile(
    r"(?:^|\n)[ \t]*\**[ \t]*\d{1,2}[ \t]*[.)]"
    r"|(?:étape|question|partie|exercice|n°)[ \t]*\**[ \t]*\d{1,2}",
    re.IGNORECASE)


def _layout_view(kind: str, variant: dict) -> dict:
    """Ce que la passe 4 voit d'une variante : EXACTEMENT l'extrait du solveur
    (donc aucune bonne réponse, aucun `check`, aucun guide), plus le numéro de
    chaque question — c'est par lui que sa réécriture reviendra se poser."""
    view = _solver_view(kind, variant)
    for i, question in enumerate(view["questions"]):
        question["n"] = i
    return view


def _all_text(variant: dict) -> str:
    """TOUT le texte d'une variante, cases comprises.

    `_variant_text` ne rend que les énoncés ; il ne suffit pas ici, parce qu'une
    mutualisation DÉPLACE l'énoncé d'une question dans le libellé d'une ligne de
    grille. Comparer les deux formes demande de regarder au même endroit."""
    out = [_variant_text(variant)]
    for part in _parts(variant):
        out.extend(str(c) for c in (part.get("choices") or []))
        out.extend(str(c) for c in (part.get("cols") or []))
        out.extend(str((r or {}).get("label") or "") for r in (part.get("rows") or []))
    return "\n".join(out)


def _numbers(text: str) -> set[str]:
    """Les nombres d'un texte, en ensemble : une DONNÉE ne doit pas disparaître
    à la mise en page, mais une répétition a parfaitement le droit de sauter —
    c'est même le but de la passe.

    Les NUMÉROTATIONS n'en sont pas : « 1. », « 2) », « **Étape 1** » sont des
    décorations que la mise en page a précisément pour rôle de retirer. Les
    compter comme des données perdues faisait refuser ses meilleurs nettoyages
    (cas réels B4.1 n°43 et B4.2 n°36)."""
    return {n.replace(",", ".")
            for n in _NUMBER_RE.findall(_plain(_ENUM_RE.sub(" ", text or "")))}


def _read_plan(spec: dict, n_questions: int) -> list[dict] | None:
    """Le plan d'UNE variante : une entrée par question de sortie, ou None si le
    modèle a perdu, dupliqué ou inventé une question.

    Un plan illisible n'est pas une erreur à corriger par un appel de plus : la
    variante garde simplement la présentation qu'elle avait."""
    questions = spec.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    plan: list[dict] = []
    seen: list[int] = []
    for entry in questions:
        if not isinstance(entry, dict):
            return None
        raw = entry.get("sources")
        raw = [raw] if isinstance(raw, int) else raw
        if not isinstance(raw, list) or not raw:
            return None
        sources = [_as_int(v) for v in raw]
        if any(i is None or not 0 <= i < n_questions for i in sources):
            return None
        seen.extend(sources)
        labels = entry.get("lignes")
        plan.append({"sources": sources,
                     "statement": str(entry.get("enonce") or "").strip(),
                     "labels": [str(v).strip() for v in labels]
                                if isinstance(labels, list) else []})
    if sorted(seen) != list(range(n_questions)):
        return None                       # une question perdue, ou comptée deux fois
    return plan


def _merged_grid(parts: list[dict], labels: list[str], statement: str) -> dict | None:
    """N questions de MÊMES propositions → une grille à cocher.

    Deux formes, selon ce que les questions d'origine partagent : un choix
    unique se reporte une ligne par question (§ `_merged_grid_single`), un
    choix multiple s'ÉCLATE une ligne par proposition (§ `_merged_grid_multiple`)
    — la grille ne sait cocher qu'UNE case par ligne (§ services.grading,
    services.pdfgen, services.worker_cv : rendu, CV et barème le supposent
    partout), donc « a) oui, b) oui, c) non » devient trois lignes Vrai/Faux,
    pas une ligne à trois cases.

    Refuse dès que la mutualisation ne serait pas fidèle : propositions
    différentes (même d'un caractère), format mélangé, plus de colonnes qu'une
    grille n'en porte. Ni le NOMBRE de cases ni celui de lignes ne sont un
    objectif à minimiser : une grille de dix lignes qui se lit d'un coup d'œil
    vaut mieux que quatre questions qui répètent la même consigne — la porte
    Python (`indigo_check._lint_grid`) refuse déjà ce qui dépasse les bornes du
    format, et la mutualisation retombe alors sur la présentation d'avant."""
    if len(parts) < 2:
        return None
    kind = parts[0].get("response_type")
    if kind == "qcm_single":
        return _merged_grid_single(parts, labels, statement)
    if kind == "qcm_multiple":
        return _merged_grid_multiple(parts, labels, statement)
    return None


def _merged_grid_single(parts: list[dict], labels: list[str], statement: str) -> dict | None:
    """N choix uniques de MÊMES propositions → une case par ligne.

    Les bonnes cases sont REPORTÉES telles quelles — l'indice de la proposition
    juste devient l'indice de la colonne juste, ce qui est la même chose écrite
    autrement. `check` retombe à « aucun » : un métacontrôle écrit pour une
    proposition ne se transpose pas en valeur de vérité de ligne. Rien n'est
    perdu pour autant, les mêmes réponses ayant déjà été recalculées par sympy
    avant cette passe."""
    cols = [str(c).strip() for c in (parts[0].get("choices") or [])]
    if not 2 <= len(cols) <= _GRID_MAX_COLS:
        return None
    rows = []
    for part, label in zip(parts, labels):
        if part.get("response_type") != "qcm_single":
            return None
        if [str(c).strip() for c in (part.get("choices") or [])] != cols:
            return None
        correct = [i for i in (part.get("correct") or []) if isinstance(i, int)]
        if len(correct) != 1 or not 0 <= correct[0] < len(cols):
            return None
        rows.append({"label": label, "correct": correct[0]})
    return {"response_type": "checkbox_grid", "statement": statement,
            "cols": cols, "rows": rows, "check": {"kind": "none"}}


def _merged_grid_multiple(parts: list[dict], labels: list[str], statement: str) -> dict | None:
    """N choix multiples de MÊMES propositions → une ligne PAR PROPOSITION.

    Un choix multiple juge chaque proposition indépendamment (cochée = vraie) :
    une grille ne pouvant cocher qu'une case par ligne, chaque proposition de
    chaque question devient sa propre ligne Vrai/Faux, préfixée par la situation
    à laquelle elle appartient. « Affirmation a : oui, b : oui, c : non »,
    répété pour quatre situations, devient douze lignes — plus qu'un simple
    report des quatre questions, mais un tableau que l'élève lit d'un coup
    d'œil au lieu de relire quatre fois la même consigne."""
    choices = [str(c).strip() for c in (parts[0].get("choices") or [])]
    if not choices or len(parts) * len(choices) > scoring.QCM_MAX_GRID_ROWS:
        # Le barème d'une grille est CODÉ par ligne (§ scoring.qcm_bareme) et
        # plafonné pour qu'un seul exercice ne pèse jamais plus d'un quart d'un
        # sujet : au-delà de `QCM_MAX_GRID_ROWS`, la grille serait refusée après
        # coup par `_lint_grid`. Refuser ICI évite l'aller-retour, et les
        # questions restent simplement séparées — jamais une perte.
        return None
    rows = []
    for part, label in zip(parts, labels):
        if part.get("response_type") != "qcm_multiple":
            return None
        if [str(c).strip() for c in (part.get("choices") or [])] != choices:
            return None
        correct = {i for i in (part.get("correct") or []) if isinstance(i, int)}
        for i, choice in enumerate(choices):
            row_label = f"{label} — {choice}" if label else choice
            rows.append({"label": row_label, "correct": 0 if i in correct else 1})
    return {"response_type": "checkbox_grid", "statement": statement,
            "cols": ["Vrai", "Faux"], "rows": rows, "check": {"kind": "none"}}


def _label_for(labels: list[str], i: int, part: dict, group: list[dict]) -> str:
    """Le texte à donner à la question `i` du groupe, ou son énoncé d'origine.

    Une PROPOSITION n'est jamais un énoncé. Le modèle range parfois les cases
    dans `lignes` au lieu des affirmations (cas réels : « 162 », « 157 »… puis
    « Vendredi », « Samedi » — les réponses des questions à mutualiser) ; les
    recopier remplacerait chaque question par sa propre réponse. On regarde les
    propositions de TOUT le groupe : quand la mutualisation aboutit elles sont
    identiques, et quand elle échoue c'est justement là que le modèle mélange
    une question avec la case d'une autre."""
    label = labels[i].strip() if i < len(labels) and labels[i] else ""
    choices = {_flat(c) for p in group for c in (p.get("choices") or []) if str(c).strip()}
    if not label or _flat(label) in choices:
        return str(part.get("statement") or "").strip()
    return label


def _laid_out(variant: dict, spec: dict) -> tuple[dict, list[list[int]]] | None:
    """La variante remise en page, et le plan RÉELLEMENT appliqué (les questions
    d'origine derrière chacune de ses questions). None si le plan du modèle ne
    tient pas debout.

    Aucune réponse n'est relue du modèle : Python les recopie."""
    parts = _parts(variant)
    plan = _read_plan(spec, len(parts))
    if plan is None:
        return None
    composite = variant.get("response_type") == COMPOSITE
    # Le contexte vaut aussi pour une variante SIMPLE : le modèle y range alors
    # les données que la question n'a plus à répéter (« Voici les diamètres de 40
    # tomates : … »), et elles se recollent à l'énoncé plus bas. L'ignorer les
    # perdait purement et simplement.
    context = str(spec.get("contexte") or "").strip()
    if composite and len(context) < CONTEXT_MIN_CHARS:
        context = str(variant.get("statement") or "")   # jamais de contexte vidé
    new_parts: list[dict] = []
    groups: list[list[int]] = []
    for entry in plan:
        sources = entry["sources"]
        group = [parts[i] for i in sources]
        labels = [_label_for(entry["labels"], i, parts[src], group)
                  for i, src in enumerate(sources)]
        merged = (_merged_grid(group, labels, entry["statement"])
                  if len(sources) > 1 else None)
        if merged is not None:
            new_parts.append(merged)
            groups.append(list(sources))
            continue
        # Mutualisation impossible (propositions différentes d'une question à
        # l'autre) : les questions restent séparées, mais elles gardent le texte
        # ALLÉGÉ que le modèle leur a écrit. Tout jeter pour une grille refusée
        # perdrait aussi le nettoyage, qui, lui, était bon.
        for i, src in enumerate(sources):
            part = dict(parts[src])
            text = labels[i] if len(sources) > 1 else entry["statement"]
            if text:
                part["statement"] = text
            new_parts.append(part)
            groups.append([src])
    if len(new_parts) == 1:
        out = {**variant, **new_parts[0]}
        out.pop("questions", None)
        out["statement"] = "\n".join(filter(None, (
            context, str(new_parts[0].get("statement") or "").strip())))
        # Le GUIDE est celui de la variante, jamais celui d'une sous-question :
        # cette passe n'y touche pas, et une sous-question qui en porterait un
        # (vide, le plus souvent) effacerait celui de l'exercice en s'aplatissant.
        out["guide"] = variant.get("guide") or ""
    else:
        out = {**variant, "response_type": COMPOSITE, "statement": context,
               "questions": new_parts}
        for field_ in ("choices", "correct", "cols", "rows", "check"):
            out.pop(field_, None)           # ces champs vivent dans les questions
    out = _normalize_variant(out)
    # Le placement de l'image est DÉTERMINISTE (§ statement.place_figure_marker) :
    # une réécriture qui perd le marqueur ne doit pas décrocher la figure.
    if (statement_mod.has_figure_marker(_variant_text(variant))
            and not statement_mod.has_figure_marker(_variant_text(out))):
        out["statement"] = statement_mod.place_figure_marker(
            out.get("statement") or "", True,
            at_end=out.get("response_type") == COMPOSITE)
    return out, groups


def _remap_answers(answers: list[list[int]] | None, groups: list[list[int]],
                   parts: list[dict]) -> list[list[int]] | None:
    """La résolution INDÉPENDANTE, transposée sur la variante remise en page.

    Elle a été obtenue avant, sur l'ancienne découpe : sans transposition,
    l'audit lirait des réponses décalées d'une question. Deux façons de
    transposer, symétriques des deux gestes de `_merged_grid` : un choix
    unique mutualisé range la case cochée de chaque source dans SA ligne ; un
    choix multiple mutualisé ÉCLATE la case cochée en une valeur par
    proposition (0 = cochée, 1 = non cochée), exactement comme
    `_merged_grid_multiple` éclate la déclaration.

    None = intransposable ; l'appelant garde alors la présentation d'avant,
    plutôt que de présenter à l'audit un trio dont la vérification indépendante
    ne parle plus."""
    if answers is None:
        return None
    out: list[list[int]] = []
    for sources in groups:
        if any(not 0 <= i < len(answers) for i in sources):
            return None
        if len(sources) == 1:
            out.append(list(answers[sources[0]]))
            continue
        multiple = parts and parts[sources[0]].get("response_type") == "qcm_multiple"
        row: list[int] = []
        for i in sources:
            found = answers[i]
            if multiple:
                n = len(parts[i].get("choices") or [])
                if not isinstance(found, list) or not all(isinstance(v, int) for v in found):
                    return None
                selected = set(found)
                row.extend(0 if j in selected else 1 for j in range(n))
            else:
                if len(found) != 1:           # une ligne de grille = UNE case
                    return None
                row.append(found[0])
        out.append(row)
    return out


def _pass_layout(db, competency, grade, trio, solved, cid, attempt, *,
                 has_figure: bool) -> tuple[dict[str, dict], dict]:
    """PASSE 4 — mise en page. Rend (trio, résolution indépendante transposée).

    C'est une passe de FORME, et rien d'autre : elle ne voit aucune réponse et
    n'en corrige aucune (§ `_layout_view`). Elle retire les consignes qui
    n'apprennent rien (« Réponds aux questions suivantes »), supprime ce que la
    question redit du contexte, et mutualise en une grille les questions dont
    les propositions sont identiques — quatre « Vrai / Faux » à la suite tiennent
    en un tableau que l'élève lit d'un coup d'œil.

    NE DÉGRADE JAMAIS, variante par variante : une réécriture qui ne passe pas
    les mêmes portes Python que la génération, qui perd une donnée, qui évalue
    moins de tâches ou dont la résolution indépendante ne se transpose plus est
    ABANDONNÉE, et cette variante garde la présentation qu'elle avait. Refuser
    coûte zéro appel : il n'y a aucune raison de tenter le diable pour de la
    forme."""
    payload = {"grade_level": grade,
               "competency_label": _competency_name(competency),
               "variantes": [_layout_view(k, trio[k]) for k in VARIANTS if k in trio]}
    data = _call(db, "mp_layout", _system("layout", competency, grade), payload,
                 f"{cid}-layout{attempt}") or {}
    by_label = {VARIANT_LABEL[k].lower(): k for k in VARIANTS}
    out = dict(trio)
    answers = dict(solved)
    for spec in (data.get("variantes") or []):
        if not isinstance(spec, dict):
            continue
        kind = by_label.get(str(spec.get("niveau") or "").strip().lower())
        if kind is None or kind not in trio:
            continue
        variant = trio[kind]
        laid_out = _laid_out(variant, spec)
        if laid_out is None:
            continue
        candidate, groups = laid_out
        remapped = _remap_answers(solved.get(kind), groups, _parts(variant))
        refusals = _verify_variant(candidate, has_figure=has_figure)
        lost_check = remapped is None and solved.get(kind) is not None
        lost_data = not _numbers(_all_text(variant)) <= _numbers(_all_text(candidate))
        if (refusals or lost_check or lost_data
                or _task_units(candidate) < _task_units(variant)):
            logger.info("Indigo/multipass : mise en page abandonnée (%s) — %s",
                        VARIANT_LABEL[kind],
                        " ; ".join(refusals)[:200] or "réécriture non fidèle")
            continue
        out[kind] = candidate
        answers[kind] = remapped
    # Deux variantes rendues IDENTIQUES par la mise en page ne sont plus une
    # graduation (cas réel B4.3 n°37 : ce qui distinguait Base de Difficile était
    # une consigne, et la retirer les a confondues). Seule la variante EN TROP
    # revient à sa forme d'avant — la première dans l'ordre des niveaux garde la
    # sienne, et les autres niveaux gardent la leur : une collision entre deux
    # cartes n'a aucune raison d'annuler le travail sur la troisième.
    seen: set[str] = set()
    for kind in VARIANTS:
        if kind not in out:
            continue
        if _family_key(out[kind]) in seen:
            out[kind], answers[kind] = trio[kind], solved.get(kind)
        seen.add(_family_key(out[kind]))
    if len(seen) != len([k for k in VARIANTS if k in out]):
        return dict(trio), dict(solved)     # collision persistante : rien ne bouge
    return out, answers


def _signal_confirms_declared(problem: dict, trio: dict[str, dict]) -> bool:
    """Le signalement dénonce-t-il une réponse qu'il confirme dans la même phrase ?

    « le solveur trouve 12, or la bonne réponse est 12 » n'est pas un défaut,
    c'est une relecture qui se contredit. Un tel signalement ne doit pas devenir
    un badge rouge sur un exercice juste."""
    text = str(problem.get("probleme") or "")
    folded = _fold_label(text)
    if "solveur" not in folded or "bonne reponse" not in folded:
        return False
    kind = next((key for key in VARIANTS
                 if _fold_label(VARIANT_LABEL[key]) ==
                 _fold_label(str(problem.get("niveau") or ""))), None)
    if kind is None or kind not in trio:
        return False
    tail = folded.split("bonne reponse", 1)[1]
    parts = _parts(trio[kind])
    sub = re.search(r"sous-question\s+([a-h])", folded)
    if sub:
        index = ord(sub.group(1)) - ord("a")
        parts = parts[index:index + 1]
    for part in parts:
        if part.get("response_type") == "checkbox_grid":
            continue
        choices = part.get("choices") or []
        for idx in part.get("correct") or []:
            if isinstance(idx, int) and 0 <= idx < len(choices):
                answer = _fold_label(_plain(choices[idx]))
                if answer and answer in tail:
                    return True
    return False


def _carry_checks(before: dict, after: dict) -> dict:
    """Reporte sur la variante retouchée le métacontrôle sympy des questions
    RESTÉES IDENTIQUES. Rend `after`, modifiée sur place.

    Une passe de retouche n'écrit pas de `check` : le lui demander, c'est lui
    demander d'écrire du sympy pour des questions qu'elle n'a pas touchées, et
    un `check` faux ferait refuser une retouche parfaitement bonne. Là où les
    propositions ET la réponse n'ont pas bougé, l'ancien contrôle reste valide et
    continue de veiller ; partout ailleurs il est caduc et disparaît — un
    contrôle écrit pour une autre réponse ne vérifie plus rien."""
    old, new = _parts(before), _parts(after)
    if len(old) != len(new):
        return after
    for src, dst in zip(old, new):
        # « aucun contrôle » EXPLICITE, jamais un champ absent : le contrat
        # interne le nomme, et un champ manquant se lirait comme un oubli.
        dst["check"] = {"kind": "none"}
        same = (src.get("response_type") == dst.get("response_type")
                and _declared_part(src) == _declared_part(dst)
                and [str(c) for c in (src.get("choices") or [])]
                    == [str(c) for c in (dst.get("choices") or [])]
                and [str(c) for c in (src.get("cols") or [])]
                    == [str(c) for c in (dst.get("cols") or [])])
        if same and isinstance(src.get("check"), dict):
            dst["check"] = dict(src["check"])
    return after


def _contract_problem(variant: dict, competency: Competency, db: Session) -> str:
    """Ce que le VALIDATEUR PARTAGÉ reprocherait à cette variante, en clair.

    Ses refus (« span LaTeX refusé », « label de ligne invalide », « énoncé trop
    long ») n'apparaissaient nulle part avant la toute fin, quand plus personne
    ne pouvait les corriger : ils faisaient perdre l'exercice entier après cinq
    passes de travail. Les nommer ICI les met sous les yeux de la retouche, qui
    les répare en une phrase. Le jeu d'empreintes est VIERGE : un doublon se
    juge sur la famille, pas sur une variante prise seule."""
    try:
        raw = _to_raw(variant)
        if exercise_gen._validate_exercise(raw, competency, db, set(),
                                           allow_geometry_text=True) is not None:
            return ""
        return exercise_gen.diagnose_rejection(raw, competency)
    except Exception as e:                     # jamais au prix de la famille
        logger.debug("Indigo/multipass : diagnostic de contrat indisponible — %s", e)
        return ""


def _dedup_fingerprint(variant: dict) -> str:
    """L'empreinte du VALIDATEUR PARTAGÉ, celle qui décidera du doublon.

    `_family_key` compare les textes au caractère près ; le validateur, lui,
    efface les nombres (§ exercise_gen._normalize_statement_for_dedup). Une
    retouche qui rapproche deux niveaux passait donc le premier contrôle pour
    tomber au second, et la variante était perdue au lieu d'être rendue à sa
    version d'avant (cas réel n°14 des pages 67-68 : Facile ET Difficile
    refusés comme doublons de la base après retouche)."""
    raw = _to_raw(variant)
    return exercise_gen._dedup_key(raw.get("statement") or "",
                                   raw.get("answer") or {},
                                   raw.get("choices"))


def _keep_the_better(trio: dict[str, dict], rewritten: dict[str, dict], *,
                     defects, stage: str) -> dict[str, dict]:
    """Garde, variante par variante, la version qui a le MOINS de défauts.

    C'est la règle de la passe de retouche : elle a le droit de ne rien
    améliorer, jamais celui d'empirer. Elle est mesurée avec la règle même qu'on
    lui a donnée (`defects`), et une variante qui n'est pas rendue, ou rendue
    moins bonne, garde simplement la version d'avant.

    Sans regénération derrière, ce comptage n'est plus un confort : c'est la
    SEULE chose qui empêche une retouche malheureuse d'aller telle quelle en
    brouillon."""
    out = dict(trio)
    for kind in VARIANTS:
        candidate = rewritten.get(kind)
        if not isinstance(candidate, dict) or kind not in trio:
            continue
        before, after = defects(trio[kind]), defects(candidate)
        if len(after) > len(before):
            logger.info("Indigo/multipass : %s abandonnée (%s) — %s défaut(s) "
                        "contre %s avant : %s", stage, VARIANT_LABEL[kind],
                        len(after), len(before), " ; ".join(after)[:200])
            continue
        out[kind] = candidate
    # Deux variantes rendues indiscernables ne sont plus une graduation : la
    # variante EN TROP revient à sa version d'avant (§ passe 4, même règle),
    # mesurée avec l'empreinte qui tranchera vraiment (§ `_dedup_fingerprint`).
    seen: set[str] = set()
    for kind in VARIANTS:
        if kind not in out:
            continue
        if _dedup_fingerprint(out[kind]) in seen and kind in trio:
            out[kind] = trio[kind]
        seen.add(_dedup_fingerprint(out[kind]))
    return out


def _read_signals(data: dict, trio: dict[str, dict]) -> list[dict]:
    """Les signalements de la passe 5, nettoyés. Un « bloquant » qui confirme la
    réponse qu'il dénonce est ignoré (§ `_signal_confirms_declared`)."""
    out: list[dict] = []
    for raw in (data.get("signalements") or []):
        if not isinstance(raw, dict):
            text = str(raw).strip()
            if text:
                out.append({"niveau": "", "bloquant": False, "probleme": text})
            continue
        text = str(raw.get("probleme") or "").strip()
        if not text:
            continue
        blocking = _fold_label(raw.get("gravite")).startswith("bloqu")
        if blocking and _signal_confirms_declared(raw, trio):
            logger.warning("Indigo/multipass : signalement auto-contradictoire "
                           "déclassé — %s", text[:300])
            blocking = False
        out.append({"niveau": str(raw.get("niveau") or "").strip(),
                    "bloquant": blocking, "probleme": text})
    return out


def _pass_repair(db, competency, grade, trio, solved, source: Source, cid,
                 attempt, *, has_figure: bool) -> tuple[dict[str, dict], list[dict]]:
    """PASSE 5 — retouche. Rend (trio retouché, signalements).

    Les exercices qui lui arrivent sont écrits, résolus et mis en page ; la
    plupart sont justes. Elle les RETOUCHE là où ils pèchent : un indice de trop
    (au pire la réponse elle-même) laissé dans l'énoncé, une consigne illisible,
    et les défauts que les contrôles Python lui nomment un par un. C'est aussi
    elle qui tranche les désaccords avec la résolution indépendante.

    ELLE NE RENVOIE RIEN EN GÉNÉRATION. Régénérer produisait le même exercice
    avec les mêmes défauts : sur les pages 86-87, 28 familles sur 33 ont brûlé
    leurs quatre tentatives pour finir quand même en brouillon à relire, au prix
    de 124 générations pour 33 exercices. Ce qu'elle ne sait pas réparer, elle le
    SIGNALE : le brouillon part avec son badge, et le professeur tranche.

    Elle n'écrit aucun `check` : Python reporte celui des questions inchangées
    (§ `_carry_checks`). Et elle ne dégrade jamais (§ `_keep_the_better`)."""
    tasks = len(source.tasks)

    def defects_of(variant: dict) -> list[str]:
        """Tout ce qu'on sait reprocher à une variante, gratuitement : les portes
        du mode ET le validateur partagé, qui a le dernier mot à la fin."""
        problems = _variant_problems(variant, has_figure=has_figure, tasks=tasks)
        contract = _contract_problem(variant, competency, db)
        if contract:
            problems.append(f"refusé par le contrat de la plateforme — {contract}")
        return problems

    defects = [{"niveau": VARIANT_LABEL[k], "defauts": problems}
               for k in VARIANTS if k in trio and (problems := defects_of(trio[k]))]
    payload = {"grade_level": grade,
               "competency_label": _competency_name(competency),
               "source_nettoyee": source.statement,
               "taches_source": list(source.tasks),
               "figure": source.figure,
               "guide_max_words": GUIDE_MAX_WORDS,
               "defauts": defects,
               # LE SOUPÇON D'INDICE. Python voit qu'une proposition, ou la
               # bonne réponse numérique, est déjà écrite dans l'énoncé — mais
               # il ne peut pas distinguer « la réponse est recopiée » de « la
               # donnée nécessaire au calcul est donnée » : sur toute lecture de
               # tableau, la bonne case EST l'un des nombres écrits au-dessus.
               # C'est exactement le jugement qu'on attend d'une relecture, donc
               # on le lui pose en question au lieu d'en faire une porte.
               "indices_soupconnes": [
                   {"niveau": VARIANT_LABEL[k], "soupcons": notes}
                   for k in VARIANTS if k in trio
                   and (notes := _response_repetition_notes(trio[k]))],
               "desaccords_detectes": _solver_disagreements(
                   trio, solved, has_figure=has_figure),
               "variantes": [{"niveau": VARIANT_LABEL[k], **trio[k],
                              "resolution_independante": solved.get(k)}
                             for k in VARIANTS if k in trio]}
    if source.original_statement:
        payload["source_vision_originale"] = source.original_statement
    data = _call(db, "mp_repair", _system("repair", competency, grade), payload,
                 f"{cid}-repair{attempt}") or {}
    rewritten: dict[str, dict] = {}
    for kind in VARIANTS:
        raw = _exercises_of(data).get(kind)
        if kind not in trio or not isinstance(raw, dict):
            continue
        if not (raw.get("statement") or raw.get("questions")):
            continue
        candidate = _normalize_variant(raw)
        # Le GUIDE n'est pas l'objet de la retouche : qu'elle l'oublie en
        # réécrivant l'énoncé est une perte sèche, et le validateur partagé
        # refuse ensuite l'exercice pour une correction vide. On garde celui
        # qu'on avait, exactement comme on garde le `check` d'une question
        # inchangée (cas réel n°29 des pages 67-68).
        if not candidate.get("guide"):
            candidate["guide"] = trio[kind].get("guide") or ""
        rewritten[kind] = _carry_checks(trio[kind], candidate)
    out = _keep_the_better(trio, rewritten, defects=defects_of, stage="retouche")
    return out, _read_signals(data, out)


# IL N'Y A PLUS DE PASSE 6. Une relecture finale conservatrice a existé ici,
# après l'audit ; elle a été MESURÉE sur les pages 67-68 avant d'être retirée :
# 18 variantes relues, 18 rendues à l'identique, aucun refus. Elle ne coûtait
# donc qu'un appel par famille, et son seul pouvoir réel était de dégrader (elle
# réécrivait un trio déjà validé, sans rien pour l'en empêcher). Ce qu'elle
# devait faire — orthographe, ponctuation, LaTeX — est le deuxième geste de la
# passe 5, qui, elle, est mesurée à chaque variante (§ `_keep_the_better`).


# --------------------------------------------------------------------- API

@dataclass
class _Resolved:
    """Ce que la PHASE A (`_resolve_family`) tranche pour une source, avant
    toute génération : c'est la matière première commune au traitement SOLO
    (`run_family`) et au traitement PAR LOT (`run_family_pair`)."""
    competency: Competency
    source: Source
    source_notes: list[str]
    has_figure: bool
    active: tuple[str, ...]
    tasks: int
    competency_norms: set[str]
    cid: str


def _resolve_family(db: Session, candidates: list[Competency], grade: str, manual: dict,
                    family: Family, isolated_figure: bool, cid: str,
                    existing_norms: set[str] | dict[str, set[str]],
                    enter, step) -> "_Resolved | None":
    """PHASE A de `run_family` : passe 1 (filtre + rattachement), recoupement
    de la figure, passe CONTEXTE (§ `_pass_context`), résolution des variantes
    actives. Rend `None` si la famille est déjà TERMINALE (source rejetée) —
    `enter` a déjà écrit son état, et l'appelant n'a plus qu'à retourner
    `family` telle quelle. Peut lever `InfeasibleSource` (verdict de la passe
    contexte) : l'appelant la traduit en `REJECTED_SOURCE`.

    Isolée pour que `run_family_pair` (réglage indigo_multipass_batch_size)
    puisse la lancer sur PLUSIEURS sources avant de décider si un appel de
    génération PARTAGÉ a un sens (même compétence résolue pour toutes) — sans
    jamais dupliquer la passe 1 ni la passe contexte, qui coûtent un appel
    chacune, comme les autres."""
    enter(FILTERING)
    step("passe 1 · filtre")
    try:
        source, reject = _pass_filter(db, candidates, grade, manual, cid)
    except Exception:
        if not manual.get("vision_extracted"):
            raise
        # Un incident de la passe de rattachement ne détruit pas une source
        # déjà lue proprement par Vision. Le titre rose fournit un repli
        # déterministe et les quatre passes de qualité restent exécutées.
        logger.exception("Indigo/multipass : passe 1 indisponible pour n°%s — "
                         "repli sur l'extraction Vision", family.number)
        original = statement_mod.repair_latex_control_chars(
            str(manual.get("statement") or "").strip())
        source, reject = Source(
            statement=original,
            needs_figure=isolated_figure,
            figure=str(manual.get("figure_description") or "").strip(),
            original_statement=original,
            tasks=_source_tasks(original)), ""
    if source is None:
        enter(REJECTED_SOURCE, reject)
        return None

    competency, confirmed = _resolve_competency(source, manual, candidates)
    if competency is None:
        enter(REJECTED_SOURCE, "aucune compétence disponible pour cet exercice")
        return None
    family.competency_id = competency.id
    family.competency_code = competency.code
    family.competency_confirmed = confirmed
    cid = f"indigo-mp-{competency.code}-{family.number}"
    # Une extraction Vision couvre souvent plusieurs compétences. Un même
    # contexte peut donc être légitime dans deux sections : les empreintes ne
    # se comparent qu'au sein de la compétence finalement choisie. Les anciens
    # appels qui passent directement un set gardent exactement leur sémantique.
    competency_norms = (existing_norms.setdefault(competency.id, set())
                        if isinstance(existing_norms, dict)
                        else existing_norms)

    # LE RECOUPEMENT DE LA FIGURE. Il ne rejette plus rien : une image
    # absente est un TRAVAIL DE RELECTURE, pas un vice rédhibitoire. Le
    # professeur ajoute la figure au brouillon (onglet Exercices →
    # « Ajouter une image ») ; lui supprimer l'exercice au motif qu'il
    # devra le compléter, c'est lui retirer la seule chose qu'il pouvait
    # réparer en trente secondes.
    source_notes: list[str] = []
    if not confirmed:
        source_notes.append(
            f"compétence à confirmer : « {competency.code} » est la plus "
            "proche du bandeau lu, mais rien ne l'a confirmée — rattache "
            "l'exercice à la bonne compétence avant de le valider")
    if source.needs_figure and not isolated_figure:
        source_notes.append(
            "l'exercice s'appuie sur un visuel du manuel qu'aucun crop n'a "
            "isolé : ajoute l'image à la relecture, ou vérifie que les "
            "données écrites suffisent")
    elif source.needs_figure and not source.figure:
        # Sans description, les passes suivantes écrivent à l'aveugle à côté
        # d'un dessin qu'aucune d'elles ne voit : on garde l'exercice, mais
        # on prévient que l'accord énoncé/figure n'a pas pu être contrôlé.
        source_notes.append(
            "figure attachée mais non décrite : aucune passe ne l'a vue, "
            "vérifie à la relecture que l'énoncé et l'image concordent")
    # Figure isolée mais dont l'exercice n'a pas besoin : elle est DÉTACHÉE.
    # L'imprimer serait un décor sans rapport avec un exercice réécrit.
    source.needs_figure = source.needs_figure and isolated_figure
    if not source.needs_figure:
        source.figure = ""
    family.figure = has_figure = source.needs_figure

    # PASSE CONTEXTE (§ `_pass_context`) : sur la compétence RÉELLEMENT
    # résolue ci-dessus (jamais une devinette d'avant la passe 1) — c'est elle
    # qui juge si l'énoncé seul suffit, si un corrigé du manuel prof comble ce
    # qu'une figure emporte seule, ou si le contexte pédagogique permet
    # d'inventer. Peut lever `InfeasibleSource`, propagée telle quelle aux
    # appelants (`run_family`, `run_family_pair`) : plus AUCUN appel de
    # génération n'est tenté pour une source jugée infaisable ici.
    step("passe contexte")
    candidates = _correction_candidates(grade, competency, family.number)
    _pass_context(db, competency, grade, manual, source, cid, candidates)

    active = VARIANTS
    return _Resolved(competency=competency, source=source, source_notes=source_notes,
                     has_figure=has_figure, active=active, tasks=len(source.tasks),
                     competency_norms=competency_norms, cid=cid)


def _produce_family(db: Session, grade: str, manual: dict, family: Family,
                    resolved: "_Resolved", *, gate, progress,
                    pregenerated: dict[str, dict] | None = None) -> Family:
    """PHASE B de `run_family` : passes 2 à 5 puis finalisation, à partir d'une
    source déjà résolue (§ `_resolve_family`). Ne lève que `BudgetExceeded`.

    `pregenerated`, si fourni, tient lieu de résultat de la PREMIÈRE passe 2
    (§ `run_family_pair`, génération partagée entre plusieurs sources) : la
    tentative 1 s'en sert directement, et une variante qu'il lui manquerait
    encore suit exactement le même repêchage ciblé qu'une sortie amputée
    ordinaire. Les tentatives suivantes (incident de transport) génèrent
    normalement — un repli à moitié fait n'a de sens qu'une fois."""
    competency, source = resolved.competency, resolved.source
    active, has_figure = resolved.active, resolved.has_figure
    tasks, cid = resolved.tasks, resolved.cid
    source_notes, competency_norms = resolved.source_notes, resolved.competency_norms

    def enter(state: str, reason: str = "") -> None:
        family.state = state
        family.reason = reason
        if progress:
            progress(family)

    def step(stage: str):
        if gate:
            gate()
        logger.info("Indigo/multipass : n°%s (%s) — %s", family.number,
                    competency.code, stage)

    try:
        def _attempt(attempt: int) -> tuple[dict[str, dict], list[dict]]:
            """UNE production complète, passes 2 à 5. Rend (trio, signalements).

            LINÉAIRE, et c'est le cœur de la révision du 04/09. Chaque passe
            passait auparavant la main à la suivante ou renvoyait toute la
            famille en génération ; mesuré sur les pages 67-68, ce retour
            coûtait 63 générations pour 8 exercices et faisait passer les
            défauts de 85 à 81. Régénérer redonne le même exercice avec les
            mêmes défauts : ce qui reste faux à la fin se RÉPARE (passe 5) ou se
            SIGNALE, et le brouillon part sous les yeux du professeur.

            Isolée pour que la boucle puisse rejouer un incident de TRANSPORT —
            là, et seulement là, recommencer a un sens."""
            enter(GENERATING)
            if pregenerated is not None and attempt == 1:
                trio = dict(pregenerated)
            else:
                step(f"passe 2 · génération (tentative {attempt})")
                trio = _pass_generate(db, competency, grade, manual, source,
                                      [], cid, attempt, None, active)
            missing = [VARIANT_LABEL[k] for k in active if k not in trio]
            if missing:
                # Sortie AMPUTÉE : pas un défaut de qualité, un appel raté
                # (coupure de sortie, JSON tronqué, ou variante restée de côté
                # par une génération partagée). C'est la seule chose que
                # relancer répare vraiment, et on la relance donc UNE fois, en
                # gardant ce qui était déjà là.
                step(f"passe 2 · reprise des variantes manquantes ({', '.join(missing)})")
                trio = _pass_generate(
                    db, competency, grade, manual, source,
                    [f"variante(s) manquante(s) : {', '.join(missing)}"],
                    cid, attempt, trio, active)
            if not trio:
                raise RuntimeError("la génération n'a rendu aucune variante")

            enter(SOLVING)
            step(f"passe 3 · résolution indépendante (tentative {attempt})")
            solved = _pass_solve(db, competency, grade, trio, source.figure,
                                 cid, attempt)

            enter(FORMATTING)
            step(f"passe 4 · mise en page (tentative {attempt})")
            trio, solved = _pass_layout(db, competency, grade, trio, solved,
                                        cid, attempt, has_figure=has_figure)

            enter(REPAIRING)
            # Deux TOURS de retouche au plus, et le second ne tourne que s'il
            # reste quelque chose à réparer. C'est le remplaçant exact de
            # l'ancienne relance : un appel, sur le texte qu'on vient d'écrire,
            # au lieu de quatre pour refaire l'exercice depuis la source.
            rounds = max(1, int(settings.indigo_multipass_repair_rounds))
            signals: list[dict] = []
            for round_ in range(1, rounds + 1):
                step(f"passe 5 · retouche {round_}/{rounds} (tentative {attempt})")
                # Les signalements du DERNIER tour font foi : c'est lui qui a vu
                # le texte final, et son silence est un jugement sur ce texte-là.
                trio, signals = _pass_repair(db, competency, grade, trio, solved,
                                             source, f"{cid}-t{round_}", attempt,
                                             has_figure=has_figure)
                if not _local_problems(trio, has_figure=has_figure, tasks=tasks,
                                       active=active):
                    break
            return trio, signals

        # Incidents de TRANSPORT (délai dépassé, sortie tronquée, 5xx) : la
        # SEULE raison de tout rejouer. Ils servent aussi à nommer la cause si
        # la famille n'aboutit pas.
        breakdowns: list[str] = []
        trio: dict[str, dict] | None = None
        signals: list[dict] = []
        for attempt in range(1, max(1, int(settings.indigo_multipass_max_attempts)) + 1):
            family.attempts = attempt
            try:
                trio, signals = _attempt(attempt)
            except providers.BudgetExceeded:
                raise
            except InfeasibleSource as e:
                # Verdict sur la SOURCE, pas un incident de transport : rejouer
                # rendrait le même jugement. On rejette directement, sans
                # consommer les tentatives restantes.
                logger.info("Indigo/multipass : n°%s (%s) jugé infaisable — %s",
                           family.number, competency.code, e)
                enter(REJECTED_SOURCE, str(e))
                return family
            except Exception as e:
                # Un incident de transport n'est PAS un défaut de l'exercice :
                # il coûte une tentative, pas la source. Avant, la moindre
                # coupure remontait à l'attrape-tout de fin et jetait la famille
                # entière du premier coup (12 exercices écartés sur 12 dans une
                # extraction où seuls 2 posaient un vrai problème).
                logger.warning("Indigo/multipass : n°%s (%s) tentative %s "
                               "interrompue — %s: %s", family.number,
                               competency.code, attempt, type(e).__name__, e)
                breakdowns.append(f"{type(e).__name__}: {e}")
                continue
            break

        if trio:
            # CE QU'ON PUBLIERAIT VRAIMENT est ce qu'on contrôle : les portes
            # Python repassent sur le trio retouché, et ce qui subsiste devient
            # une réserve écrite sur la carte — plus jamais une relance.
            problems = _local_problems(trio, has_figure=has_figure, tasks=tasks,
                                       active=active)
            family.blocking = [f"{s['niveau']} : {s['probleme']}" if s["niveau"]
                               else s["probleme"] for s in signals if s["bloquant"]]
            reserves = [f"{s['niveau']} : {s['probleme']}" if s["niveau"]
                        else s["probleme"] for s in signals if not s["bloquant"]]
            variants, refused = _finalize_trio(trio, competency, db,
                                               competency_norms, active)
            if not variants:
                # Rien de publiable : la cause est CELLE DU VALIDATEUR (doublon,
                # format interdit…), pas un compte de tentatives. La taire
                # renverrait le professeur au journal pour deviner.
                family.notes = refused + problems
                enter(REJECTED_GENERATION,
                      " ; ".join(refused or problems or breakdowns)[:500]
                      or "aucune variante exploitable après retouche")
                return family
            if variants:
                family.variants = variants
                missing = [VARIANT_LABEL[k] for k in active
                           if k not in {kind for kind, _ in variants}]
                # Les DÉFAUTS d'abord : c'est ce que le professeur doit lire en
                # premier, et `reason` est tronqué à 500 caractères. Les
                # réserves molles (§ `_local_notes`) ferment la marche : un
                # signal que tout le monde voit tout le temps ne signale rien.
                family.notes = (family.blocking + problems + refused + reserves
                                + ([f"niveau(x) manquant(s) : {', '.join(missing)} "
                                    "— à écrire à la main ou à régénérer"]
                                   if missing else [])
                                + source_notes
                                + _local_notes(trio, has_figure=has_figure,
                                               active=active))
                if family.blocking or problems or refused or missing:
                    enter(NEEDS_REVIEW, " ; ".join(family.notes)[:500])
                else:
                    # Les RÉSERVES ne changent pas l'état : l'état dit si les
                    # portes sont passées, les réserves disent ce que le
                    # relecteur doit REGARDER.
                    enter(READY)
                return family

        # Ni trio, ni variante que le validateur partagé accepte. L'appelant
        # écrira le brouillon de REPLI (l'énoncé source tel que Vision l'a lu) :
        # un exercice de manuel correctement lu doit toujours arriver sous les
        # yeux du professeur, même quand la pipeline n'a rien su en faire.
        enter(REJECTED_GENERATION,
              " ; ".join(breakdowns)[:500]
              or "aucune variante exploitable après retouche")
        return family
    except providers.BudgetExceeded:
        raise
    except Exception as e:
        # un exercice qui casse ne doit pas casser la cible : on le rejette avec
        # sa cause, l'appelant continue avec le suivant.
        logger.warning("Indigo/multipass : n°%s (%s) abandonné — %s: %s",
                       family.number, competency.code, type(e).__name__, e)
        enter(REJECTED_GENERATION, f"{type(e).__name__}: {e}")
        return family


def run_family(db: Session, competency: Competency | list[Competency], grade: str, manual: dict,
               existing_norms: set[str] | dict[str, set[str]], *, gate=None,
               progress=None) -> Family:
    """Traite UN exercice source de bout en bout. Ne lève que `BudgetExceeded`.

    `gate` est appelé AVANT chaque passe : c'est le portillon des heures creuses
    (services.indigo_offpeak). Il n'interrompt jamais une passe entamée — au
    pire il fait attendre la suivante, ce qui est exactement la règle demandée.

    `progress(family)` est notifié à chaque changement d'état, pour que l'onglet
    voie la machine à états avancer au lieu d'une barre muette.

    Mince depuis le 04/09 : phase A (`_resolve_family`) puis phase B
    (`_produce_family`), la même paire dont `run_family_pair` se sert pour
    partager la génération entre plusieurs sources sans dupliquer la passe 1.
    """
    candidates = (list(competency) if isinstance(competency, (list, tuple))
                  else [competency])
    candidates = [c for c in candidates if c is not None]
    ranked = _rank_competencies(str(manual.get("competency_title") or ""), candidates)
    guessed = ranked[0] if ranked else (candidates[0] if candidates else None)
    family = Family(number=str(manual.get("number", "")).strip())
    cid = f"indigo-mp-{guessed.code if guessed else 'unknown'}-{family.number}"
    isolated_figure = bool(manual.get("has_figure"))

    def enter(state: str, reason: str = "") -> None:
        family.state = state
        family.reason = reason
        if progress:
            progress(family)

    def step(stage: str):
        if gate:
            gate()
        logger.info("Indigo/multipass : n°%s (%s) — %s", family.number,
                    guessed.code if guessed else "?", stage)

    try:
        resolved = _resolve_family(db, candidates, grade, manual, family,
                                   isolated_figure, cid, existing_norms, enter, step)
    except providers.BudgetExceeded:
        raise
    except InfeasibleSource as e:
        # Verdict de la passe CONTEXTE, pas un incident de transport : ni
        # l'énoncé ni le corrigé du professeur ne permettent d'écrire un
        # exercice juste, même en s'en inspirant. Rejeté directement, avant
        # tout appel de génération.
        enter(REJECTED_SOURCE, str(e))
        return family
    except Exception as e:
        logger.warning("Indigo/multipass : n°%s (%s) abandonné — %s: %s",
                       family.number, guessed.code if guessed else "?",
                       type(e).__name__, e)
        enter(REJECTED_GENERATION, f"{type(e).__name__}: {e}")
        return family
    if resolved is None:
        return family
    return _produce_family(db, grade, manual, family, resolved,
                           gate=gate, progress=progress)


def _pass_generate_pair(db: Session, competency: Competency, grade: str,
                        jobs: list[tuple[str, dict, Source]], cid: str, attempt: int,
                        active: tuple[str, ...]) -> dict[str, dict[str, dict]]:
    """PASSE 2 PARTAGÉE : génère PLUSIEURS sources dans le MÊME appel (§
    run_family_pair, réglage indigo_multipass_batch_size). `jobs` est une liste
    de (numéro, manual, source NETTOYÉE), toutes de la MÊME compétence — un
    prompt système n'en décrit qu'une. Chaque source de `jobs` est déjà jugée
    FAISABLE (§ `_pass_context`, `_resolve_family`) : une source infaisable
    n'atteint jamais cette passe, qu'elle soit seule ou groupée.

    Rend `trios[numero]`, les variantes ACTIVES obtenues pour CE numéro
    (jamais complétée ici — une variante manquante suit le repêchage ciblé
    habituel, § `_produce_family`)."""
    sources_payload = []
    for number, manual, source in jobs:
        entry = _source_payload(grade, competency,
                                {**manual, "statement": source.statement,
                                 "has_figure": source.needs_figure},
                                figure=source.figure)["source"]
        entry["taches_source"] = list(source.tasks)
        if source.corrige_prof:
            entry["corrige_prof"] = source.corrige_prof
        if source.invent_context:
            entry["contexte_invention"] = source.invent_context
        sources_payload.append(entry)
    payload = {"grade_level": grade, "competency_code": competency.code,
              "competency_label": _competency_name(competency),
              "chapter": f"{competency.chapter_code} {competency.chapter_name}".strip(),
              "domain": f"{competency.domain_code} {competency.domain_name}".strip(),
              "guide_max_words": GUIDE_MAX_WORDS, "sources": sources_payload}
    data = _call(db, "mp_generate", _pair_generate_system(competency, grade, active),
                payload, f"{cid}-genpair{attempt}") or {}
    lots = [item for item in (data.get("lots") or []) if isinstance(item, dict)]
    by_number = {}
    for item in lots:
        by_number.setdefault(str(item.get("source_number") or "").strip(), item)

    trios: dict[str, dict[str, dict]] = {}
    for pos, (number, _manual, _source) in enumerate(jobs):
        # Repli POSITIONNEL, comme indigo_qcm._generate_call : le modèle omet
        # parfois de recopier le numéro, une sortie exploitable ne doit pas se
        # perdre pour un champ manqué.
        item = by_number.get(number) or (lots[pos] if pos < len(lots) else None)
        if item is None:
            continue
        trio: dict[str, dict] = {}
        for kind in active:
            variant = _exercises_of(item).get(kind)
            if isinstance(variant, dict):
                trio[kind] = _normalize_variant(variant)
        if trio:
            trios[number] = trio
    return trios


def run_family_pair(db: Session, competency: Competency | list[Competency], grade: str,
                    manuals: list[dict], existing_norms: set[str] | dict[str, set[str]],
                    *, gate=None, progress=None) -> list[Family]:
    """Traite un LOT d'exercices source (§ réglage indigo_multipass_batch_size),
    en partageant la passe 2 entre ceux qui se rattachent à la MÊME compétence.
    Rend une `Family` par entrée de `manuals`, DANS LE MÊME ORDRE.

    La passe 1 tourne TOUJOURS une fois par source — jamais partagée, c'est
    elle qui décide justement si un appel commun a un sens. Le partage échoue
    proprement : compétences différentes, lot d'une seule source, ou appel
    partagé en échec retombent tous sur `_produce_family` en mode SOLO
    (`pregenerated=None`), qui refait exactement ce que `run_family` aurait
    fait — sans jamais repayer la passe 1 déjà tranchée ici."""
    candidates = (list(competency) if isinstance(competency, (list, tuple))
                 else [competency])
    candidates = [c for c in candidates if c is not None]

    families: list[Family] = []
    states: list[_Resolved | None] = []
    for manual in manuals:
        ranked = _rank_competencies(str(manual.get("competency_title") or ""), candidates)
        guessed = ranked[0] if ranked else (candidates[0] if candidates else None)
        family = Family(number=str(manual.get("number", "")).strip())
        cid = f"indigo-mp-{guessed.code if guessed else 'unknown'}-{family.number}"
        isolated_figure = bool(manual.get("has_figure"))

        def enter(state, reason="", _family=family):
            _family.state = state
            _family.reason = reason
            if progress:
                progress(_family)

        def step(stage, _family=family, _code=(guessed.code if guessed else "?")):
            if gate:
                gate()
            logger.info("Indigo/multipass : n°%s (%s) — %s", _family.number, _code, stage)

        try:
            res = _resolve_family(db, candidates, grade, manual, family,
                                  isolated_figure, cid, existing_norms, enter, step)
        except providers.BudgetExceeded:
            raise
        except InfeasibleSource as e:
            # Verdict de la passe CONTEXTE pour CETTE source : rejetée
            # directement, sans empêcher les AUTRES sources du lot de
            # partager quand même un appel de génération entre elles.
            enter(REJECTED_SOURCE, str(e))
            res = None
        except Exception as e:
            logger.warning("Indigo/multipass : n°%s abandonné en phase 1 — %s: %s",
                           family.number, type(e).__name__, e)
            enter(REJECTED_GENERATION, f"{type(e).__name__}: {e}")
            res = None
        families.append(family)
        states.append(res)

    # Seules des sources RATTACHÉES, jugées FAISABLES (passe contexte) et de
    # MÊME compétence partagent un appel : le prompt système de la passe 2 est
    # écrit pour UNE compétence.
    live = [i for i, r in enumerate(states) if r is not None]
    codes = {states[i].competency.code for i in live}
    pregenerated: dict[int, dict[str, dict] | None] = {}
    if len(live) >= 2 and len(codes) == 1:
        competency = states[live[0]].competency
        active = states[live[0]].active
        jobs = [(families[i].number, manuals[i], states[i].source) for i in live]
        cid = f"indigo-mp-{competency.code}-" + "+".join(n for n, _m, _s in jobs)
        try:
            trios = _pass_generate_pair(db, competency, grade, jobs, cid, 1, active)
        except providers.BudgetExceeded:
            raise
        except Exception as e:
            logger.warning("Indigo/multipass : génération partagée (%s) en échec — "
                           "%s: %s ; repli sur une génération par source",
                           "+".join(n for n, _m, _s in jobs), type(e).__name__, e)
            trios = {}
        for i in live:
            pregenerated[i] = trios.get(families[i].number)   # None = repli solo normal

    out: list[Family] = []
    for i, family in enumerate(families):
        res = states[i]
        if res is None:
            out.append(family)
            continue
        out.append(_produce_family(db, grade, manuals[i], family, res,
                                   gate=gate, progress=progress,
                                   pregenerated=pregenerated.get(i)))
    return out

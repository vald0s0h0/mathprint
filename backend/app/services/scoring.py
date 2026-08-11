"""Barème et notation d'une copie (§ barème).

`bareme_points` est le SEUL barème de la plateforme, de la création d'un
exercice jusqu'à la note imprimée. Il vit à UN seul endroit —
`grading_json["bareme_points"]` — et tout ce qui a besoin du barème passe par
`item_bareme` ci-dessous. (Il a existé un champ `effort_points` doublonnant, à
la fois dans le contrat du modèle et en colonne d'IndigoExercise : deux noms
pour une même valeur, dont l'un était silencieusement ignoré par la banque.)

DEUX ÉCHELLES cohabitent, et les confondre est la principale source d'erreur :

  - l'échelle INTERNE du moteur de correction (services.grading) : le
    `max_score` de grading_json, exprimé en « unités vérifiables » — 1 par
    cellule de tableau à remplir, 1 par CASE de QCM, 2 pour une expression, la
    somme des points de rubrique... Elle sert à mesurer CE QUI EST JUSTE, et
    pas du tout ce que ça vaut. Elle ne doit JAMAIS être écrasée par le
    barème : le moteur compare `score` à `max_score` (auto-vérification des
    exercices créés, notation par cellule), un tableau de 4 cases y vaut
    forcément 4.

  - le BARÈME (`bareme_points`), en points professeur, multiples de 0,125 : ce
    que l'exercice VAUT dans le sujet. Il combine le TEMPS DE RÉFLEXION et la
    COMPLEXITÉ (un problème rapporte plus qu'une application directe), JAMAIS
    le niveau de l'élève. Il est demandé au modèle à la CRÉATION de l'exercice
    (cf. exercise_gen._BAREME_RULES), figé sur la copie à la génération du
    sujet, et sert jusqu'à la note finale.

Le passage de l'une à l'autre est un simple ratio :

    points obtenus = (score / max_score) × bareme_points

et la note finale une règle de trois sur la base choisie par le professeur à
la création du sujet (/5, /10 ou /20), contrôle ou entraînement.

ARRONDIS — un seul dans toute la chaîne. Les points d'un exercice ne sont
JAMAIS arrondis : un QCM de 8 cases à 1 point dont 5 sont justes vaut 0,625, et
c'est cette valeur exacte qui est sommée. Le pas de 0,125 existe précisément
pour que ces partages tombent juste. Le SEUL arrondi est celui de la note, à la
règle de trois : au 0,5 SUPÉRIEUR (jamais au plus proche — on ne retire pas un
demi-point à un élève), la note EXACTE restant conservée en base
(CopyResult.note_raw) — c'est elle qui doit servir aux moyennes et au suivi,
arrondir puis moyenner accumulant le biais d'arrondi.
"""
import math

from sqlalchemy.orm import Session

from ..models import (
    Assessment, Copy, CopyItem, CopyItemResult, CopyResult, ExerciseCompetency,
    GradingDecision, StudentResponse, now,
)

# Bases de scoring proposées au professeur pour tous les sujets (§ assistant).
NOTE_BASES = (5, 10, 20)
DEFAULT_NOTE_BASE = 20
# Valeur historique des entraînements créés avant leur scoring.
NOTE_BASE_UNGRADED = 0

# 0,125 = 1/8 de point : le pas le plus fin du barème. Il n'est pas cosmétique —
# c'est lui qui permet à une CASE de QCM ou à une cellule de tableau de valoir sa
# part exacte sans forcer le total de l'exercice à tomber rond (2,125 points est
# un barème parfaitement légitime, et on n'ajuste JAMAIS un exercice pour
# arrondir son barème). Toutes les fractions usuelles du barème (1/8, 1/4, 1/2,
# 3/4) sont des multiples exacts, donc représentables sans perte en binaire.
BAREME_STEP = 0.125
BAREME_MIN = 0.125
# 5 points : au-delà, un seul exercice pèserait un quart d'un sujet noté sur 20
# — c'est un problème complet, pas un exercice.
BAREME_MAX = 5.0


def snap_bareme(value) -> float | None:
    """Barème brut (renvoyé par le modèle) -> multiple de 0,125 dans
    [0,125 ; 5]. None si la valeur est inexploitable — l'appelant se rabat alors
    sur `fallback_bareme`, jamais sur un refus de l'exercice : un barème
    manquant se recalcule, un exercice jeté est repayé."""
    try:
        v = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    snapped = round(v / BAREME_STEP) * BAREME_STEP
    return min(BAREME_MAX, max(BAREME_MIN, snapped))


def round_half_up(value: float) -> float:
    """Note élève -> multiple de 0,5, arrondi AU SUPÉRIEUR (13,1 -> 13,5).

    Le `round(..., 6)` n'est pas cosmétique : une note exacte de 14 calculée en
    flottant peut valoir 14.000000000000002, dont le double plafonne à 29 et
    donnerait 14,5 — un demi-point offert par une erreur de représentation."""
    return math.ceil(round(value * 2, 6)) / 2


# Prix de l'UNITÉ vérifiable, par comparateur, pour le repli déterministe. Ce
# n'est pas BAREME_STEP : le pas du barème (0,125) est la finesse de la grille,
# ces valeurs-ci sont ce que vaut une case / une cellule / une étape.
_UNIT_PRICE = {
    "table_cells": 0.5,     # une cellule = un petit calcul à poser
    "rubric": 0.5,          # par point d'étape (1-3 par étape) : la rédaction coûte
    "matching": 0.5,        # par paire à relier
    "qcm": 0.25,            # par CASE (cochée ou laissée vide à raison)
    "grid": 0.25,           # par ligne de grille
}


def fallback_bareme(response_type: str, grading: dict) -> float:
    """Barème d'un exercice dont la source n'en a pas fourni : banque
    antérieure au barème, exercice réécrit par un correctif qui ne l'a pas
    reposé, modèle qui a omis le champ. Déterministe et calé sur la même idée
    que le prompt — ce que l'exercice demande de TRAVAIL, lu sur sa structure
    (une case à cocher n'est pas un tableau de 6 cellules, qui n'est pas un
    raisonnement rédigé en 4 étapes).

    Ce n'est PAS une seconde échelle : c'est la réparation d'un
    `bareme_points` manquant, pour ne jamais laisser un exercice valoir 0 en
    silence. Volontairement conservateur — il ne cherche pas à imiter finement
    le jugement du modèle."""
    comparator = (grading or {}).get("comparator")
    max_score = float((grading or {}).get("max_score") or 1)

    if comparator == "qcm":
        # Un QCM UNIQUE représente une seule décision, quel que soit le nombre
        # de distracteurs. Un QCM MULTIPLE représente au contraire une décision
        # par case. Le repli doit respecter la même séparation que le moteur de
        # notation, notamment pour les anciens exercices sans bareme_points.
        if response_type == "qcm_single":
            units = 1.0
        else:
            units = float(len((grading or {}).get("choices") or []) or max_score)
    elif comparator == "grid":
        # Une décision par ligne ; `rows` fait foi si un ancien max_score ne
        # reflète pas encore la cardinalité réelle de la grille.
        units = float(len((grading or {}).get("rows") or []) or max_score)
    else:
        units = max_score
    if comparator in _UNIT_PRICE:
        return snap_bareme(_UNIT_PRICE[comparator] * units) or BAREME_MIN
    if comparator in ("rational_equiv", "symbolic_equiv"):
        return 1.5     # une fraction/expression se calcule, pas se lit
    return 1.0         # réponse courte, tracé : l'unité de référence


def item_bareme(grading: dict, response_type: str) -> float:
    """Barème d'un exercice, en points professeur. Source de vérité unique :
    tout ce qui a besoin du barème passe ici, jamais par grading_json en
    direct (un exercice sans `bareme_points` doit valoir son repli, pas 0)."""
    v = snap_bareme((grading or {}).get("bareme_points"))
    return v if v is not None else fallback_bareme(response_type, grading or {})


def with_bareme(grading: dict, response_type: str) -> dict:
    """Copie de `grading` dont le barème est RÉSOLU (repli compris).

    Utilisé au moment de figer l'exercice sur la copie (services.generation) :
    l'instantané d'un CopyItem (RM-014) doit porter SON barème, celui qui a
    servi à composer le sujet — pas dépendre d'un repli recalculé des mois plus
    tard, quand la règle de repli aura changé."""
    g = dict(grading or {})
    g["bareme_points"] = item_bareme(g, response_type)
    return g


def earned_points(score: float, max_score: float, bareme: float) -> float:
    """Points barème obtenus = ratio de réussite × barème de l'exercice.

    Le ratio est borné à [0, 1] : une rubrique corrigée par LLM peut renvoyer
    un total légèrement au-dessus du max, il ne doit pas créer des points."""
    if not max_score:
        return 0.0
    return max(0.0, min(1.0, score / max_score)) * bareme


def note_from_points(points_earned: float, points_total: float,
                     base: int) -> tuple[float, float]:
    """Règle de trois -> (note exacte, note arrondie au 0,5 supérieur).

    La note arrondie est plafonnée à la base : un sans-faute vaut 20/20, jamais
    20,5/20 par arrondi."""
    if not points_total or not base:
        return 0.0, 0.0
    raw = points_earned / points_total * base
    return raw, min(float(base), round_half_up(raw))


def normalize_note_base(value, *, graded: bool = True) -> int:
    """Base de scoring valide (5/10/20).

    `graded` reste accepté pour les appelants historiques, mais n'annule plus
    les entraînements : leur score est suivi sans être imprimé."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULT_NOTE_BASE
    return v if v in NOTE_BASES else DEFAULT_NOTE_BASE


def assessment_note_base(assessment: Assessment) -> int:
    """Base de scoring applicable à tout sujet."""
    return normalize_note_base(assessment.note_base)


# ------------------------------------------------------- consolidation d'une copie

def _latest_decision(db: Session, item: CopyItem) -> GradingDecision | None:
    resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
    if not resp:
        return None
    return (db.query(GradingDecision).filter_by(response_id=resp.id)
            .order_by(GradingDecision.created_at.desc()).first())


def _item_competency_id(db: Session, item: CopyItem) -> str | None:
    row = (db.query(ExerciseCompetency)
           .filter_by(exercise_id=item.catalog_id).first())
    return row.competency_id if row else None


def compute_copy_result(db: Session, copy: Copy,
                        assessment: Assessment) -> CopyResult | None:
    """Consolide une copie corrigée en base : points barème par exercice
    (CopyItemResult), points totaux et note sur la base choisie (CopyResult).

    Appelé à la finalisation du lot (services.pipeline.finalize_batch), une
    ligne par copie — c'est le suivi personnalisé de l'élève : sans ça, retrouver
    ce qu'un élève a obtenu à un sujet demande de rejoindre 4 tables
    (copy_items → student_responses → grading_decisions → manual_reviews) et de
    reconstituer le barème à chaque lecture.

    IDEMPOTENT : re-finaliser un lot recalcule au lieu d'empiler.

    Ne comptent QUE les exercices réellement corrigés : une copie non scannée,
    une page manquante ou une question annulée par le professeur (max_score
    remis à 0) ne pèsent ni au numérateur ni au dénominateur — jamais de
    pénalité pour un exercice que l'élève n'a pas eu sous les yeux."""
    items = (db.query(CopyItem).filter_by(copy_id=copy.id)
             .order_by(CopyItem.sequence).all())

    graded: list[tuple[CopyItem, GradingDecision, float, float]] = []
    for item in items:
        decision = _latest_decision(db, item)
        if not decision or decision.status == "review_pending":
            continue
        if not decision.max_score:
            continue  # question annulée par le professeur : hors barème
        bareme = item_bareme(item.grading_json, item.response_type)
        earned = earned_points(decision.score, decision.max_score, bareme)
        graded.append((item, decision, bareme, earned))

    if not graded:
        return None  # copie non scannée / non corrigée : rien à consolider

    points_earned = sum(g[3] for g in graded)
    points_total = sum(g[2] for g in graded)
    base = assessment_note_base(assessment)
    note_raw, note = note_from_points(points_earned, points_total, base)

    result = db.query(CopyResult).filter_by(copy_id=copy.id).first()
    if result is None:
        result = CopyResult(copy_id=copy.id)
        db.add(result)
    result.assessment_id = assessment.id
    result.student_id = copy.student_id
    result.points_earned = points_earned
    result.points_total = points_total
    result.note_base = base
    result.note_raw = note_raw
    result.note = note
    result.finalized_at = now()
    db.flush()

    db.query(CopyItemResult).filter_by(copy_result_id=result.id).delete()
    for item, decision, bareme, earned in graded:
        db.add(CopyItemResult(
            copy_result_id=result.id, copy_item_id=item.id,
            competency_id=_item_competency_id(db, item),
            sequence=item.sequence, response_type=item.response_type,
            difficulty=item.difficulty, score=decision.score,
            max_score=decision.max_score, bareme_points=bareme,
            points_earned=earned))
    db.flush()
    return result


def copy_result(db: Session, copy: Copy, assessment: Assessment) -> CopyResult | None:
    """Résultat consolidé d'une copie : celui persisté à la finalisation, ou
    recalculé s'il manque (lot finalisé avant l'arrivée du barème)."""
    existing = db.query(CopyResult).filter_by(copy_id=copy.id).first()
    if existing is not None:
        return existing
    return compute_copy_result(db, copy, assessment)


def format_points(value: float) -> str:
    """Points/notes à la française pour l'impression : 1,5 — et 2 plutôt que
    2,0 (un barème entier ne s'écrit pas avec une décimale sur une copie).

    3 décimales, pas 2 : le pas du barème est 0,125, qu'un arrondi au centième
    afficherait « 0,13 » — un huitième de point imprimé faux sur la copie."""
    text = f"{round(value, 3):g}"
    return text.replace(".", ",")

"""Barème d'effort et notation d'une copie (§ barème).

DEUX ÉCHELLES cohabitent dans la plateforme, et les confondre est la principale
source d'erreur :

  - l'échelle INTERNE du moteur de correction (services.grading) : le
    `max_score` de grading_json, exprimé en « unités vérifiables » — 1 par
    cellule de tableau à remplir, 1 par QCM, 2 pour une expression, la somme
    des points de rubrique... Elle sert à mesurer CE QUI EST JUSTE, et pas du
    tout ce que ça vaut. Elle ne doit JAMAIS être écrasée par le barème : le
    moteur compare `score` à `max_score` (auto-vérification des exercices
    créés, notation par cellule), un tableau de 4 cases y vaut forcément 4.

  - le BARÈME (`bareme_points`), en points professeur, multiples de 0,5 : ce
    que l'exercice VAUT dans le sujet. Il récompense l'EFFORT demandé pour
    résoudre — le temps de réflexion, le nombre d'étapes de raisonnement —
    JAMAIS le niveau de l'élève : un élève fragile fournit plus d'effort sur
    un exercice facile qu'un bon élève sur un exercice moyen, et c'est
    l'effort qu'on récompense. Il est demandé au modèle à la CRÉATION de
    l'exercice (cf. exercise_gen._BAREME_RULES, champ "effort_points"), figé
    sur la copie à la génération du sujet, et sert jusqu'à la note finale.

Le passage de l'une à l'autre est un simple ratio :

    points obtenus = (score / max_score) × bareme_points

et la note finale une règle de trois sur la base choisie par le professeur à
la création du sujet (/5, /10 ou /20, contrôle uniquement).

ARRONDIS : barèmes d'exercice et notes d'élève sont des multiples de 0,5. La
note imprimée est arrondie AU SUPÉRIEUR (jamais au plus proche : on ne retire
pas un demi-point à un élève), mais la note EXACTE est conservée en base
(CopyResult.note_raw) — c'est elle qui doit servir aux moyennes et au suivi,
arrondir puis moyenner accumulant le biais d'arrondi.
"""
import math

from sqlalchemy.orm import Session

from ..models import (
    Assessment, Copy, CopyItem, CopyItemResult, CopyResult, ExerciseCompetency,
    GradingDecision, StudentResponse, now,
)

# Bases de notation proposées au professeur pour un contrôle (§ assistant sujet).
NOTE_BASES = (5, 10, 20)
DEFAULT_NOTE_BASE = 20
# 0 = sujet non noté (entraînement) : les points sont quand même consolidés en
# base pour le suivi, seule la note n'a pas de sens.
NOTE_BASE_UNGRADED = 0

BAREME_STEP = 0.5
BAREME_MIN = 0.5
# 5 points : au-delà, un seul exercice pèserait un quart d'un sujet noté sur 20
# — c'est un problème complet, pas un exercice.
BAREME_MAX = 5.0


def snap_bareme(value) -> float | None:
    """Barème brut (renvoyé par le modèle) -> multiple de 0,5 dans
    [0,5 ; 5]. None si la valeur est inexploitable — l'appelant se rabat alors
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


def fallback_bareme(response_type: str, grading: dict) -> float:
    """Barème d'un exercice dont la source n'en a pas fourni : banque
    antérieure au barème, extraction Sésamaths d'un lot déjà en cache, modèle
    qui a omis le champ. Déterministe et calé sur la même idée d'effort que le
    prompt — ce que l'exercice demande de TRAVAIL, lu sur sa structure (une
    case à cocher n'est pas un tableau de 6 cellules, qui n'est pas un
    raisonnement rédigé en 4 étapes).

    Volontairement conservateur : il ne cherche pas à imiter finement le
    jugement du modèle, seulement à ne jamais laisser un exercice sans barème
    (qui vaudrait 0 dans la note, en silence)."""
    comparator = (grading or {}).get("comparator")
    max_score = float((grading or {}).get("max_score") or 1)

    if comparator == "table_cells":
        # une case = un petit calcul : 0,5 point par case à remplir
        return snap_bareme(BAREME_STEP * max_score) or BAREME_MIN
    if comparator == "rubric":
        # max_score = somme des points d'étapes (1-3 par étape) : un
        # raisonnement rédigé coûte cher en réflexion
        return snap_bareme(BAREME_STEP * max_score) or BAREME_MIN
    if comparator in ("rational_equiv", "symbolic_equiv"):
        return 1.5     # une fraction/expression se calcule, pas se lit
    if comparator == "matching":
        return snap_bareme(BAREME_STEP * max_score) or BAREME_MIN
    return 1.0         # QCM, réponse courte, tracé : l'unité de référence


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


def normalize_note_base(value, *, graded: bool) -> int:
    """Base de notation valide (5/10/20) pour un sujet noté, 0 sinon — un
    entraînement n'a pas de note (§ pas de note en entraînement)."""
    if not graded:
        return NOTE_BASE_UNGRADED
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULT_NOTE_BASE
    return v if v in NOTE_BASES else DEFAULT_NOTE_BASE


def assessment_note_base(assessment: Assessment) -> int:
    """Base réellement applicable à un sujet : 0 pour un entraînement, même si
    une base traîne en base de données (un sujet peut avoir été créé en
    contrôle puis repassé en entraînement)."""
    return normalize_note_base(assessment.note_base,
                               graded=assessment.type == "control")


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
    # entraînement : les points restent (suivi), la note n'existe pas
    result.note_raw = note_raw if base else None
    result.note = note if base else None
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
    2,0 (un barème entier ne s'écrit pas avec une décimale sur une copie)."""
    text = f"{round(value, 2):g}"
    return text.replace(".", ",")

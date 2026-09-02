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


# ------------------------------------------------------ barème CODÉ des QCM
#
# Pipeline « QCM only » (services.indigo_qcm) : le barème n'est PLUS demandé au
# modèle, il est CALCULÉ ici. Un LLM à qui l'on demande d'estimer ce que vaut un
# exercice répond au jugé, et deux QCM identiques repartaient avec des barèmes
# différents. La règle tient en deux nombres, et elle est la même pour l'élève
# que pour le professeur :
#
#   - QCM à réponse UNIQUE : l'élève prend UNE décision (quelle case cocher),
#     elle vaut 1 point, tout ou rien.
#   - QCM à choix MULTIPLES et grille à cocher : chaque case est une décision, et
#     chaque décision juste vaut un demi-point — cocher une case qu'il fallait
#     cocher COMME laisser vide une case qu'il ne fallait pas cocher.
#
# L'unité d'une GRILLE est la LIGNE, pas la cellule : c'est ce que le moteur de
# correction sait mesurer (comparator "grid" compte les lignes dont la colonne
# cochée est la bonne, cf. services.grading). Compter les cellules ferait diverger
# le barème affiché des points réellement attribués.
QCM_SINGLE_POINTS = 1.0
QCM_BOX_POINTS = 0.5
# Bornes qui garantissent que le barème calculé reste sous BAREME_MAX (un seul
# exercice ne peut pas peser un quart d'un sujet noté sur 20) : 8 propositions
# -> 4 points, 10 lignes -> 5 points. Elles sont FAITES RESPECTER en amont
# (services.indigo_check), pour qu'un dépassement soit un refus explicite et non
# un écrêtage silencieux de snap_bareme.
QCM_MAX_CHOICES = 8
QCM_MAX_GRID_ROWS = 10


def qcm_bareme(response_type: str, grading: dict) -> float:
    """Barème CODÉ d'un QCM, en points professeur. Lève ValueError si la
    structure dépasse les bornes : mieux vaut refuser l'exercice que publier un
    barème écrêté qui ne correspondrait plus à la notation."""
    g = grading or {}
    if response_type == "qcm_single":
        return QCM_SINGLE_POINTS
    if response_type == "qcm_multiple":
        n = len(g.get("choices") or [])
        if not 2 <= n <= QCM_MAX_CHOICES:
            raise ValueError(f"QCM multiple : {n} proposition(s), attendu 2 à "
                             f"{QCM_MAX_CHOICES}")
        return QCM_BOX_POINTS * n
    if response_type == "checkbox_grid":
        n = len(g.get("rows") or [])
        if not 2 <= n <= QCM_MAX_GRID_ROWS:
            raise ValueError(f"Grille à cocher : {n} ligne(s), attendu 2 à "
                             f"{QCM_MAX_GRID_ROWS}")
        return QCM_BOX_POINTS * n
    raise ValueError(f"Barème QCM demandé pour un format non-QCM : {response_type!r}")


def with_qcm_bareme(grading: dict, response_type: str) -> dict:
    """Copie de `grading` dont `bareme_points` est le barème CODÉ (§ qcm_bareme).
    Écrase toute valeur qui viendrait du modèle."""
    return {**(grading or {}),
            "bareme_points": qcm_bareme(response_type, grading or {})}


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

def _latest_response_and_decision(
        db: Session, item: CopyItem) -> tuple[StudentResponse | None, GradingDecision | None]:
    """Réponse de l'élève et DERNIÈRE décision la concernant (les décisions sont
    append-only : c'est la plus récente qui fait foi)."""
    resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
    if not resp:
        return None, None
    return resp, (db.query(GradingDecision).filter_by(response_id=resp.id)
                  .order_by(GradingDecision.created_at.desc()).first())


def assessment_date(assessment: Assessment, copy: Copy | None = None):
    """Jour où l'élève a FAIT le sujet — la date que mesure la courbe de l'oubli.

    Surtout pas la date de finalisation : un lot corrigé dix jours après le
    contrôle offrirait sinon dix jours de fraîcheur en cadeau. On prend la date
    programmée par le professeur, à défaut celle de génération de la copie."""
    return (assessment.scheduled_at
            or (copy.generated_at if copy is not None else None)
            or now())


def _item_competency_id(db: Session, item: CopyItem) -> str | None:
    row = (db.query(ExerciseCompetency)
           .filter_by(exercise_id=item.catalog_id).first())
    return row.competency_id if row else None


def student_answer_text(db: Session, resp: StudentResponse | None) -> str:
    """Réponse de l'élève aplatie en une chaîne lisible, pour l'historique de
    suivi (CopyItemResult.answer_text).

    C'est un INSTANTANÉ d'affichage : student_responses et ocr_attempts restent
    la source de vérité (choix cochés, paires reliées, cellules lues) ; celle-ci
    sert à relire d'un coup d'œil ce que l'élève avait écrit, des mois plus
    tard, sans rejoindre quatre tables.

    Les cellules d'un tableau ne sont PAS dans student_responses (final_text y
    reste vide, cf. services.pipeline) mais dans la lecture effective de la
    zone — d'où le passage par `effective_reading`, la même que celle affichée
    au professeur dans la modale de correction : l'historique doit montrer la
    réponse RETENUE, correction manuelle comprise, pas la première lecture."""
    if resp is None:
        return ""
    if (resp.final_text or "").strip():
        return resp.final_text.strip()
    if resp.selected_choices:
        return ", ".join(str(c) for c in resp.selected_choices)
    if resp.selected_pairs:
        return " ; ".join(
            f"{p[0]}→{p[1]}" if isinstance(p, (list, tuple)) and len(p) >= 2 else str(p)
            for p in resp.selected_pairs)
    # import local : pipeline importe scoring au chargement (l'inverse en
    # module ferait un cycle), mais on ne veut pas dupliquer ici la règle de
    # choix de la lecture effective — deux règles finiraient par diverger.
    from .pipeline import effective_reading
    reading = effective_reading(db, resp.zone_id)
    cells = (reading.raw_json or {}).get("cells") if reading else None
    if isinstance(cells, list):
        return " | ".join("" if c is None else str(c) for c in cells)
    return (reading.text or "").strip() if reading else ""


def compute_copy_result(db: Session, copy: Copy,
                        assessment: Assessment) -> CopyResult | None:
    """Consolide une copie corrigée en base : points barème par exercice
    (CopyItemResult), points totaux et note sur la base choisie (CopyResult).

    Appelé à la finalisation du lot (services.pipeline.finalize_batch), une
    ligne par copie — c'est le suivi personnalisé de l'élève : sans ça, retrouver
    ce qu'un élève a obtenu à un sujet demande de rejoindre 4 tables
    (copy_items → student_responses → grading_decisions → manual_reviews) et de
    reconstituer le barème à chaque lecture.

    C'est aussi l'HISTORIQUE que lit le moteur de sujets individuels : chaque
    CopyItemResult retient l'exercice de banque servi, son dérivé, la réponse de
    l'élève et le JOUR DU DEVOIR (cf. modèle CopyItemResult).

    IDEMPOTENT : re-finaliser un lot recalcule au lieu d'empiler.

    Ne comptent QUE les exercices réellement corrigés : une copie non scannée,
    une page manquante ou une question annulée par le professeur (max_score
    remis à 0) ne pèsent ni au numérateur ni au dénominateur — jamais de
    pénalité pour un exercice que l'élève n'a pas eu sous les yeux."""
    items = (db.query(CopyItem).filter_by(copy_id=copy.id)
             .order_by(CopyItem.sequence).all())

    graded: list[tuple[CopyItem, GradingDecision, float, float, StudentResponse | None]] = []
    for item in items:
        resp, decision = _latest_response_and_decision(db, item)
        if not decision or decision.status == "review_pending":
            continue
        if not decision.max_score:
            continue  # question annulée par le professeur : hors barème
        bareme = item_bareme(item.grading_json, item.response_type)
        earned = earned_points(decision.score, decision.max_score, bareme)
        graded.append((item, decision, bareme, earned, resp))

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
    occurred_at = assessment_date(assessment, copy)
    for item, decision, bareme, earned, resp in graded:
        db.add(CopyItemResult(
            copy_result_id=result.id, copy_item_id=item.id,
            competency_id=_item_competency_id(db, item),
            student_id=copy.student_id,
            generated_exercise_id=item.generated_exercise_id,
            sequence=item.sequence, response_type=item.response_type,
            difficulty=item.difficulty,
            # dérivé 1-3 : CopyItem.difficulty porte l'échelle 3/6/9
            difficulty_level=max(1, min(3, round(item.difficulty / 3))),
            answer_text=student_answer_text(db, resp),
            success_ratio=(max(0.0, min(1.0, decision.score / decision.max_score))
                           if decision.max_score else 0.0),
            occurred_at=occurred_at,
            score=decision.score,
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

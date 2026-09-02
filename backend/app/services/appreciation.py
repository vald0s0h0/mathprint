"""Zone Appréciation enrichie (§ appréciation) : partie déterministe (progrès
de compétences depuis ce sujet, jamais de rouge) + courte synthèse Claude
Haiku, calées sur la zone Appréciation de l'en-tête (pdfgen.header_geometry).

Cet appel produisait aussi une « trame » d'exercices pour le sujet suivant.
Elle a été supprimée : écrite à la correction, elle pariait sur une date de
sujet suivant que personne ne connaissait, alors que c'est exactement le délai
écoulé qui décide de ce qu'il faut retravailler. La trame est désormais
calculée sans LLM au moment de composer le sujet, sur l'historique réel
(services.student_history).
"""
from sqlalchemy.orm import Session

from ..models import Competency, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItem, Student
from . import forgetting, providers
from .runtime_settings import appreciation_synthesis_enabled

MAX_COMPETENCIES = 3

_SYSTEM = (
    "Tu produis, pour une copie de mathématiques corrigée, un JSON strict à un "
    "seul champ. \"synthesis\" : une phrase courte et encourageante (1 "
    "phrase, 25 mots maximum), fondée uniquement sur les progrès de "
    "compétences fournis, jamais de ton négatif, jamais de comparaison avec "
    "d'autres élèves, pas de nom propre (chaîne vide si aucun progrès)."
)


def compute_competency_progress(db: Session, assessment_id: str, student_id: str) -> list[dict]:
    """Compétences travaillées dans CE sujet avec un progrès positif mesurable
    depuis la correction, triées par delta décroissant. Jamais de delta <= 0
    (§ pas de rouge, jamais de signal négatif)."""
    copy = (db.query(Copy).filter_by(assessment_id=assessment_id, student_id=student_id)
            .first())
    if not copy:
        return []
    item_ids = [i for (i,) in db.query(CopyItem.id).filter_by(copy_id=copy.id).all()]
    if not item_ids:
        return []
    evidences = (db.query(CompetencyEvidence)
                 .filter(CompetencyEvidence.student_id == student_id,
                         CompetencyEvidence.item_id.in_(item_ids)).all())
    progress: dict[str, dict] = {}
    for ev in evidences:
        hist = (db.query(CompetencyStateHistory)
                .filter_by(evidence_id=ev.id).first())
        if not hist:
            continue
        before = (hist.before_json or {}).get("mastery")
        after = (hist.after_json or {}).get("mastery")
        if before is None or after is None:
            continue
        delta = after - before
        if delta <= 0:
            continue  # pas de rouge, pas de neutre : on omet ce qui ne progresse pas
        existing = progress.get(ev.competency_id)
        if existing is None or delta > existing["delta"]:
            progress[ev.competency_id] = {"delta": delta, "pct_acquired": after}

    out = []
    for comp_id, data in progress.items():
        comp = db.get(Competency, comp_id)
        if not comp:
            continue
        # le libellé de compétence seul (ex. "Automatismes") ne dit rien sans
        # son chapitre (H2) : ce compte rendu imprimé les affiche toujours ensemble
        name = f"{comp.chapter_name} · {comp.label}" if comp.chapter_name else comp.label
        out.append({"competency_name": name, "pct_acquired": data["pct_acquired"],
                    "delta": data["delta"]})
    out.sort(key=lambda p: p["delta"], reverse=True)
    return out[:MAX_COMPETENCIES]


def _build_synthesis(db: Session, student: Student, progress: list[dict],
                     due: list[dict]) -> str:
    """Un seul appel Claude Haiku (JSON) : la phrase de la zone Appréciation.

    Les compétences dues restent transmises : ce sont elles qui permettent
    d'encourager sur ce qui vient, pas seulement sur ce qui est acquis.
    Désactivée par défaut (Paramètres > Pédagogie) : la partie déterministe
    de l'appréciation (progrès de compétences) reste affichée sans elle."""
    if not appreciation_synthesis_enabled(db):
        return ""
    if not progress and not due:
        return ""
    payload = {"pseudonym": student.llm_pseudonym, "progress": progress,
              "due_competencies": due[:5]}
    try:
        result = providers.claude_json(
            db, "appreciation_synthesis", _SYSTEM, payload,
            max_tokens=250, correlation_id=student.llm_pseudonym)
    except Exception:
        return ""
    return result.get("synthesis") or ""


def build_appreciation(db: Session, assessment_id: str, student: Student) -> dict:
    """Payload complet {progress, synthesis} pour l'overlay et le cache Copy."""
    progress = compute_competency_progress(db, assessment_id, student.id)
    due = forgetting.due_competencies(db, student.id)
    return {"progress": progress,
            "synthesis": _build_synthesis(db, student, progress, due)}

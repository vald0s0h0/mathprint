"""Zone Appréciation enrichie (§ appréciation) : partie déterministe (progrès
de compétences depuis ce sujet, jamais de rouge) + courte synthèse Claude
Haiku, calées sur la zone Appréciation de l'en-tête (pdfgen.header_geometry).
"""
from sqlalchemy.orm import Session

from ..models import Competency, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItem, Student
from . import providers

MAX_COMPETENCIES = 3

_SYSTEM = (
    "Tu rédiges une phrase courte et encourageante (1 phrase, 25 mots maximum) "
    "pour la zone Appréciation d'une copie de mathématiques. Tu t'appuies "
    "uniquement sur les progrès de compétences fournis. Jamais de ton négatif, "
    "jamais de comparaison avec d'autres élèves, pas de nom propre."
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
        out.append({"competency_name": comp.label, "pct_acquired": data["pct_acquired"],
                    "delta": data["delta"]})
    out.sort(key=lambda p: p["delta"], reverse=True)
    return out[:MAX_COMPETENCIES]


def build_synthesis(db: Session, student: Student, progress: list[dict]) -> str:
    """Courte synthèse Haiku à partir des progrès (jamais le nom réel, RM-010)."""
    if not progress:
        return ""
    summary = "; ".join(
        f"{p['competency_name']} : {round(p['pct_acquired'] * 100)}% acquis "
        f"(+{round(p['delta'] * 100)} points depuis ce sujet)" for p in progress)
    return providers.claude_text(
        db, "appreciation_synthesis", _SYSTEM,
        f"Élève {student.llm_pseudonym}. Progrès mesurés : {summary}",
        max_tokens=120, correlation_id=student.llm_pseudonym)


def build_appreciation(db: Session, assessment_id: str, student: Student) -> dict:
    """Payload complet {progress, synthesis} pour l'overlay et le cache Copy."""
    progress = compute_competency_progress(db, assessment_id, student.id)
    synthesis = build_synthesis(db, student, progress)
    return {"progress": progress, "synthesis": synthesis}

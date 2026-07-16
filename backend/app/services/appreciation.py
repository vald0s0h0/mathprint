"""Zone Appréciation enrichie (§ appréciation) : partie déterministe (progrès
de compétences depuis ce sujet, jamais de rouge) + courte synthèse Claude
Haiku, calées sur la zone Appréciation de l'en-tête (pdfgen.header_geometry).

Le même appel Claude produit aussi, en JSON structuré, un plan de travail
prévisionnel (compétences à revoir, difficulté, rythme, ET compétences devant
recevoir un rappel de leçon) — persisté sur
Student.next_plan_json et réutilisé par services.distribution lors de la
création du sujet suivant, pour éviter un second appel LLM. Les rappels de
leçon ainsi ciblés (lesson_competency_ids) sont consommés par
services.distribution.lesson_review_targets puis services.generation, qui
les insère dans la copie de l'élève — jamais deux fois dans le même sujet,
mais peuvent réapparaître d'un sujet à l'autre tant que la lacune persiste.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Competency, CompetencyEvidence, CompetencyStateHistory, Copy, CopyItem, Student
from . import forgetting, providers

MAX_COMPETENCIES = 3

_SYSTEM = (
    "Tu produis, pour une copie de mathématiques corrigée, un JSON strict à "
    "deux champs. \"synthesis\" : une phrase courte et encourageante (1 "
    "phrase, 25 mots maximum), fondée uniquement sur les progrès de "
    "compétences fournis, jamais de ton négatif, jamais de comparaison avec "
    "d'autres élèves, pas de nom propre (chaîne vide si aucun progrès). "
    "\"next_plan\" : un plan de travail prévisionnel pour les prochains "
    "exercices de cet élève, fondé sur les compétences dues (courbe "
    "d'oubli) fournies — {\"competency_ids\": [str,...] (3 maximum), "
    "\"difficulty_level\": entier 1-5, \"quantity\": entier 2-6, "
    "\"pacing_days\": entier, "
    "\"lesson_competency_ids\": [str,...] (2 maximum, uniquement parmi "
    "due_competencies) — les compétences pour lesquelles un rappel de leçon "
    "doit être proposé avant les exercices du prochain sujet ; réserve ce "
    "champ aux vraies lacunes (maîtrise faible ou échec récent d'après le "
    "motif fourni), jamais une compétence simplement due par le temps mais "
    "déjà bien maîtrisée — liste vide si aucune lacune ne le justifie}."
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


def _build_synthesis_and_plan(db: Session, student: Student, progress: list[dict],
                              due: list[dict]) -> dict:
    """Un seul appel Claude Haiku (JSON) : synthèse de la zone Appréciation +
    plan de travail prévisionnel (jamais un second appel LLM à ce stade)."""
    if not progress and not due:
        return {"synthesis": "", "next_plan": None}
    payload = {"pseudonym": student.llm_pseudonym, "progress": progress,
              "due_competencies": due[:5]}
    try:
        result = providers.claude_json(
            db, "appreciation_synthesis", _SYSTEM, payload,
            max_tokens=250, correlation_id=student.llm_pseudonym)
    except Exception:
        return {"synthesis": "", "next_plan": None}
    return {"synthesis": result.get("synthesis") or "", "next_plan": result.get("next_plan")}


def build_appreciation(db: Session, assessment_id: str, student: Student) -> dict:
    """Payload complet {progress, synthesis} pour l'overlay et le cache Copy.
    Persiste en plus, en aparté, le plan de travail prévisionnel de l'élève."""
    progress = compute_competency_progress(db, assessment_id, student.id)
    due = forgetting.due_competencies(db, student.id)
    result = _build_synthesis_and_plan(db, student, progress, due)
    student.next_plan_json = result.get("next_plan")
    student.next_plan_updated_at = datetime.now(timezone.utc)
    return {"progress": progress, "synthesis": result.get("synthesis", "")}

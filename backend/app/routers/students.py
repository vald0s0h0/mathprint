"""Écran Élève (§9.4) : synthèse, compétences, oubli, niveau, rapports."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import (
    Assessment, Competency, CompetencyEvidence, CopyItemResult, SchoolClass,
    Student, StudentCompetencyState, StudentLevel, StudentReport, User,
)
from ..services.forgetting import (
    compute_student_level, due_competencies, priority, recall_probability, strength,
)
from ..services import providers

router = APIRouter(prefix="/api/students", tags=["students"],
                   dependencies=[Depends(current_user)])


@router.get("/{student_id}")
def student_detail(student_id: str, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    cls = db.get(SchoolClass, s.class_id) if s.class_id else None
    level_row = (db.query(StudentLevel).filter_by(student_id=student_id)
                 .order_by(StudentLevel.valid_from.desc()).first())
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    comps = {c.id: c for c in db.query(Competency).all()}
    evidence_count = db.query(CompetencyEvidence).filter_by(student_id=student_id).count()

    due = due_competencies(db, student_id)
    for d in due:
        comp = comps.get(d["competency_id"])
        d["code"] = comp.code if comp else "?"
        d["short_id"] = comp.short_id if comp else ""
        d["label"] = comp.label if comp else "Compétence supprimée"
        d["chapter"] = comp.chapter_name if comp else ""

    return {
        "id": s.id, "name": s.name, "order_index": s.order_index,
        "dyslexic": s.dyslexic,
        "pseudonym": s.llm_pseudonym, "class_name": cls.name if cls else None,
        # niveau 1-10 : réservé au professeur, jamais sur la copie (RM-007)
        "level": level_row.level if level_row else None,
        "level_locked": s.level_locked,
        "evidence_count": evidence_count,
        "competencies": [{
            "competency_id": st.competency_id,
            "code": comps[st.competency_id].code if st.competency_id in comps else "?",
            "short_id": comps[st.competency_id].short_id if st.competency_id in comps else "",
            "label": comps[st.competency_id].label if st.competency_id in comps else "?",
            "domain": comps[st.competency_id].domain_name if st.competency_id in comps else "",
            "chapter": comps[st.competency_id].chapter_name if st.competency_id in comps else "",
            "mastery": st.mastery, "confidence": st.confidence,
            "stability_days": st.stability,
            "recall_probability": round(recall_probability(st), 3),
            # maîtrise FRAÎCHE et priorité de travail : `mastery` seul ne
            # vieillit pas, ce sont ces deux-là qui disent l'état d'aujourd'hui
            # et qui pilotent le choix des exercices (services.student_history)
            "strength": round(strength(st), 3),
            "priority": round(priority(st), 3),
            "last_seen_at": str(st.last_seen_at), "due_at": str(st.due_at),
        } for st in states],
        "due": due,
    }


@router.get("/{student_id}/history")
def student_history_rows(student_id: str, limit: int = 300,
                         db: Session = Depends(get_db)):
    """Historique d'exercices de l'élève : un exercice fait = une ligne, avec sa
    date, son dérivé, sa réponse et sa réussite.

    C'est la lecture directe de ce que le moteur de sujets individuels utilise
    (services.student_history) : si une ligne manque ici, elle manque au moteur.
    Trié du plus récent au plus ancien — c'est le sens dans lequel on relit le
    travail d'un élève."""
    if not db.get(Student, student_id):
        raise HTTPException(404)
    rows = (db.query(CopyItemResult)
            .filter(CopyItemResult.student_id == student_id)
            .order_by(CopyItemResult.occurred_at.desc().nullslast(),
                      CopyItemResult.sequence)
            .limit(max(1, min(1000, limit))).all())
    if not rows:
        return {"items": [], "total": 0}
    comps = {c.id: c for c in db.query(Competency).filter(
        Competency.id.in_({r.competency_id for r in rows if r.competency_id})).all()}
    # le sujet est retrouvé via le résultat de copie : une requête, pas une par ligne
    result_ids = {r.copy_result_id for r in rows}
    titles = {}
    if result_ids:
        from ..models import CopyResult
        for res, title, atype in (db.query(CopyResult.id, Assessment.title,
                                           Assessment.type)
                                  .join(Assessment, Assessment.id == CopyResult.assessment_id)
                                  .filter(CopyResult.id.in_(result_ids)).all()):
            titles[res] = (title, atype)
    out = []
    for r in rows:
        comp = comps.get(r.competency_id)
        title, atype = titles.get(r.copy_result_id, ("", ""))
        out.append({
            "id": r.id,
            "occurred_at": str(r.occurred_at) if r.occurred_at else "",
            "assessment_title": title,
            "assessment_type": atype,
            "competency_short_id": comp.short_id if comp else "",
            "competency_label": comp.label if comp else "Compétence supprimée",
            "chapter": comp.chapter_name if comp else "",
            "difficulty_level": r.difficulty_level,
            "response_type": r.response_type,
            "answer_text": r.answer_text or "",
            "success_ratio": round(r.success_ratio or 0.0, 3),
            "points_earned": r.points_earned,
            "bareme_points": r.bareme_points,
            # NULL sur l'historique antérieur à la traçabilité de l'exercice
            "exercise_id": r.generated_exercise_id or "",
        })
    return {"items": out, "total": len(out)}


class LevelIn(BaseModel):
    level: int
    locked: bool = False
    reason: str = ""


@router.post("/{student_id}/level")
def set_level(student_id: str, body: LevelIn, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    if not 1 <= body.level <= 10:
        raise HTTPException(422, "Niveau entre 1 et 10")
    db.add(StudentLevel(student_id=student_id, level=body.level, source="teacher",
                        locked=body.locked, reason=body.reason))
    s.level_locked = body.locked
    db.commit()
    return {"ok": True}


@router.post("/{student_id}/level/recompute")
def recompute_level(student_id: str, db: Session = Depends(get_db)):
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    if s.level_locked:
        raise HTTPException(409, "Niveau verrouillé par le professeur")
    current = (db.query(StudentLevel).filter_by(student_id=student_id)
               .order_by(StudentLevel.valid_from.desc()).first())
    level, reason = compute_student_level(db, student_id)
    # variation automatique limitée à ±1 par cycle (§7.3)
    if current:
        level = max(current.level - 1, min(current.level + 1, level))
    db.add(StudentLevel(student_id=student_id, level=level, source="deterministic",
                        reason=reason))
    db.commit()
    return {"level": level, "reason": reason}


@router.get("/{student_id}/reports")
def list_reports(student_id: str, db: Session = Depends(get_db)):
    rows = (db.query(StudentReport).filter_by(student_id=student_id)
            .order_by(StudentReport.created_at.desc()).all())
    return [{"id": r.id, "period": r.period, "content": r.content,
             "status": r.status, "created_at": str(r.created_at)} for r in rows]


@router.post("/{student_id}/reports")
def create_report(student_id: str, period: str = "mois", db: Session = Depends(get_db)):
    """Compte rendu Claude Haiku — brouillon modifiable avant export (§9.4).
    Données pseudonymisées et pré-agrégées (§8.3, RM-010)."""
    s = db.get(Student, student_id)
    if not s:
        raise HTTPException(404)
    states = db.query(StudentCompetencyState).filter_by(student_id=student_id).all()
    comps = {c.id: c.label for c in db.query(Competency).all()}
    summary = "; ".join(
        f"{comps.get(st.competency_id, '?')}: maîtrise {st.mastery:.0%}"
        for st in sorted(states, key=lambda x: x.mastery)[:6]) or "aucune donnée"
    content = providers.claude_text(
        db, "student_report",
        "Tu rédiges un court compte rendu encourageant pour un élève de collège en "
        "mathématiques, en français, à partir de métriques agrégées. Tu ne notes pas, "
        "tu ne juges pas la personne, tu conseilles.",
        f"Élève {s.llm_pseudonym} (période : {period}). Compétences : {summary}",
        correlation_id=s.llm_pseudonym)
    r = StudentReport(student_id=student_id, period=period, content=content)
    db.add(r)
    db.commit()
    return {"id": r.id, "content": content}


class ReportUpdateIn(BaseModel):
    content: str | None = None
    status: str | None = None


@router.patch("/reports/{report_id}")
def update_report(report_id: str, body: ReportUpdateIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    r = db.get(StudentReport, report_id)
    if not r:
        raise HTTPException(404)
    if body.content is not None:
        r.content = body.content
    if body.status in ("draft", "approved", "exported"):
        r.status = body.status
        if body.status == "approved":
            r.approved_by = user.id
    db.commit()
    return {"ok": True}

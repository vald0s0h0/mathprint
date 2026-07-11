"""Compétences, paramètres, coûts API et dashboard."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user, require_role
from ..models import (
    ApiUsageEvent, Assessment, Competency, CompetencyFramework, ManualReview,
    ProviderConfig, ScanBatch, SchoolClass, Student, StudentCompetencyState,
    SystemSetting, User,
)
from ..services.forgetting import recall_probability

router = APIRouter(prefix="/api", tags=["misc"], dependencies=[Depends(current_user)])


# ------------------------------------------------------------- compétences

@router.get("/competencies/frameworks")
def frameworks(db: Session = Depends(get_db)):
    return [{"id": f.id, "name": f.name, "grade_level": f.grade_level,
             "version": f.version, "status": f.status}
            for f in db.query(CompetencyFramework).all()]


@router.get("/competencies")
def competencies(framework_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Competency).order_by(Competency.order_index)
    if framework_id:
        q = q.filter_by(framework_id=framework_id)
    return [{"id": c.id, "code": c.code, "label": c.label,
             "description": c.description, "framework_id": c.framework_id,
             "domain_code": c.domain_code, "domain_name": c.domain_name,
             "theme_code": c.theme_code, "theme_name": c.theme_name}
            for c in q.all()]


@router.get("/competencies/tree")
def competencies_tree(framework_id: str, db: Session = Depends(get_db)):
    """Hiérarchie domaine > thème > compétences pour l'affichage compact."""
    rows = (db.query(Competency).filter_by(framework_id=framework_id)
            .order_by(Competency.order_index).all())
    domains: list[dict] = []
    for c in rows:
        d = next((x for x in domains if x["code"] == c.domain_code), None)
        if d is None:
            d = {"code": c.domain_code, "name": c.domain_name, "themes": []}
            domains.append(d)
        t = next((x for x in d["themes"] if x["code"] == c.theme_code), None)
        if t is None:
            t = {"code": c.theme_code, "name": c.theme_name, "competencies": []}
            d["themes"].append(t)
        t["competencies"].append({"id": c.id, "code": c.code, "label": c.label})
    return domains


# ------------------------------------------------------------- paramètres

class ProviderIn(BaseModel):
    provider: str        # mathpix | deepseek | anthropic
    model: str = ""
    secret: str = ""
    active: bool = True


@router.get("/settings/providers")
def get_providers(db: Session = Depends(get_db)):
    out = []
    for p in db.query(ProviderConfig).all():
        # la clé n'est jamais renvoyée intégralement (§11.4)
        masked = (p.encrypted_secret[:4] + "…") if p.encrypted_secret else ""
        out.append({"provider": p.provider, "model": p.model,
                    "secret_preview": masked, "active": p.active})
    return out


@router.post("/settings/providers", dependencies=[Depends(require_role("admin", "teacher"))])
def set_provider(body: ProviderIn, db: Session = Depends(get_db)):
    p = db.query(ProviderConfig).filter_by(provider=body.provider).first()
    if not p:
        p = ProviderConfig(provider=body.provider)
        db.add(p)
    p.model = body.model
    if body.secret:
        p.encrypted_secret = body.secret
    p.active = body.active
    db.commit()
    return {"ok": True}


@router.get("/settings/system")
def get_system_settings(db: Session = Depends(get_db)):
    rows = {r.key: r.value_json for r in db.query(SystemSetting).all()}
    rows.setdefault("mock_mode", {"enabled": settings.mock_mode})
    rows.setdefault("forgetting_threshold", {"value": settings.forgetting_threshold})
    rows.setdefault("correction_color", {"value": settings.correction_color})
    rows.setdefault("dropout_color", {"value": settings.dropout_color})
    return rows


class SettingIn(BaseModel):
    key: str
    value: dict


@router.post("/settings/system")
def set_system_setting(body: SettingIn, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    row = db.get(SystemSetting, body.key)
    if not row:
        row = SystemSetting(key=body.key)
        db.add(row)
    row.value_json = body.value
    row.version += 1
    row.updated_by = user.id
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------- coûts

@router.get("/costs")
def costs(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    out = {}
    for provider in ("mathpix", "deepseek", "anthropic"):
        day = db.query(func.coalesce(func.sum(ApiUsageEvent.estimated_cost), 0.0)).filter(
            ApiUsageEvent.provider == provider,
            ApiUsageEvent.created_at >= now - timedelta(days=1)).scalar()
        month = db.query(func.coalesce(func.sum(ApiUsageEvent.estimated_cost), 0.0)).filter(
            ApiUsageEvent.provider == provider,
            ApiUsageEvent.created_at >= now - timedelta(days=30)).scalar()
        calls = db.query(ApiUsageEvent).filter(
            ApiUsageEvent.provider == provider,
            ApiUsageEvent.created_at >= now - timedelta(days=30)).count()
        out[provider] = {"day_eur": round(day, 4), "month_eur": round(month, 4),
                         "calls_month": calls,
                         "daily_budget_eur": settings.llm_daily_cost_limit_eur}
    return out


# ------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    batches = (db.query(ScanBatch).order_by(ScanBatch.created_at.desc()).limit(5).all())
    pending_reviews = db.query(ManualReview).filter(ManualReview.resolved_at.is_(None)).count()

    classes = []
    for c in db.query(SchoolClass).filter(SchoolClass.archived_at.is_(None)).all():
        student_ids = [s.id for s in c.students if s.active]
        states = (db.query(StudentCompetencyState)
                  .filter(StudentCompetencyState.student_id.in_(student_ids)).all()
                  if student_ids else [])
        due = sum(1 for st in states if recall_probability(st) < settings.forgetting_threshold)
        avg = sum(st.mastery for st in states) / len(states) if states else 0
        classes.append({"id": c.id, "name": c.name, "students": len(student_ids),
                        "avg_mastery": round(avg, 2), "due_competencies": due,
                        "is_mock": c.is_mock})

    assessment_titles = {a.id: a.title for a in db.query(Assessment).all()}
    return {
        "pending_reviews": pending_reviews,
        "recent_batches": [{"id": b.id, "status": b.status,
                            "assessment_id": b.assessment_id,
                            "assessment_title": assessment_titles.get(b.assessment_id, "?"),
                            "created_at": str(b.created_at)} for b in batches],
        "classes": classes,
        "assessments_draft": db.query(Assessment).filter_by(status="draft").count(),
        "system": {"mock_mode": settings.mock_mode, "data_dir": str(settings.data_dir),
                   "version": "0.9.0"},
    }

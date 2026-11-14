"""Prêt pour l'examen : part des compétences du niveau solides AUJOURD'HUI.

Déterministe, sans LLM, recalculé à chaque lecture avec la date du jour comme
services.forgetting. Une compétence est :

    solide          maîtrise × fraîcheur ≥ SOLID_STRENGTH aujourd'hui ;
    à revoir        évaluée, mais sous le seuil (fragile ou oubliée) ;
    jamais évaluée  aucun devoir ne l'a encore portée. Ce n'est PAS un échec :
                    on ne sait rien, et on le dit séparément.

Chaque compétence pèse ses minutes recommandées (programme ivoirien : heures de
la leçon réparties sur ses contenus), à défaut les heures de la leçon partagées
entre ses compétences, à défaut 1 (référentiels français).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Competency, SchoolClass, Student, StudentCompetencyState
from . import forgetting
from .education import framework_for

SOLID_STRENGTH = 0.6
REFRESH_MASTERY = 0.6     # maîtrisée mais plus fraîche : « à rafraîchir »

EXAMS = {("fr", "3e"): "brevet", ("ci", "3e"): "BEPC", ("ci", "tle"): "BAC", ("fr", "tle"): "baccalauréat"}


def exam_name(cls: SchoolClass | None) -> str | None:
    if cls is None:
        return None
    return EXAMS.get((cls.program_id or "fr", cls.grade_level))


def level_competencies(db: Session, cls: SchoolClass, subject_id: str = "math") -> list[Competency]:
    fw = framework_for(db, cls, subject_id)
    if fw is None:
        return []
    return (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.teaching_order.is_(None), Competency.teaching_order,
                      Competency.order_index, Competency.id).all())


def weights(comps: list[Competency]) -> dict[str, float]:
    per_chapter: dict[str, int] = {}
    for c in comps:
        per_chapter[c.chapter_code] = per_chapter.get(c.chapter_code, 0) + 1
    out = {}
    for c in comps:
        if c.recommended_minutes:
            out[c.id] = float(c.recommended_minutes)
        elif c.chapter_hours:
            out[c.id] = c.chapter_hours * 60 / per_chapter[c.chapter_code]
        else:
            out[c.id] = 1.0
    return out


def _entry(c: Competency, weight: float) -> dict:
    return {"competency_id": c.id, "short_id": c.short_id, "label": c.label,
            "chapter": c.chapter_name, "weight": round(weight, 1)}


def readiness(comps: list[Competency], states: dict[str, StudentCompetencyState],
              at: datetime | None = None) -> dict:
    """Indicateur d'un élève sur une liste de compétences, à la date `at`."""
    at = at or datetime.now(timezone.utc)
    w = weights(comps)
    total = sum(w.values()) or 1.0
    mean_w = total / max(1, len(comps))
    solid, review, never = [], [], []
    for c in comps:
        state = states.get(c.id)
        if state is None or state.last_seen_at is None:
            never.append(_entry(c, w[c.id]))
            continue
        s = forgetting.strength(state, at)
        entry = {**_entry(c, w[c.id]), "strength": round(s, 3), "mastery": round(state.mastery, 3),
                 "priority": round(1 - s, 3)}
        if s >= SOLID_STRENGTH:
            solid.append(entry)
        else:
            entry["reason"] = "à rafraîchir" if state.mastery >= REFRESH_MASTERY else "à consolider"
            # Une leçon de 12 h pèse plus qu'une leçon de 3 h à priorité égale.
            entry["score"] = round(entry["priority"] * w[c.id] / mean_w, 4)
            review.append(entry)
    review.sort(key=lambda e: (-e["score"], -e["priority"], e["short_id"]))

    def share(rows):
        return round(sum(r["weight"] for r in rows) / total, 3) if comps else 0.0

    return {"total": len(comps), "solid_count": len(solid), "review_count": len(review),
            "never_count": len(never), "solid_share": share(solid), "review_share": share(review),
            "never_share": share(never), "weighted": any(c.recommended_minutes or c.chapter_hours for c in comps),
            "to_review": review, "never": never, "solid": solid}


def _states(db: Session, student_ids: list[str], comp_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {sid: {} for sid in student_ids}
    if not student_ids or not comp_ids:
        return out
    for st in (db.query(StudentCompetencyState)
               .filter(StudentCompetencyState.student_id.in_(student_ids),
                       StudentCompetencyState.competency_id.in_(comp_ids)).all()):
        out[st.student_id][st.competency_id] = st
    return out


def student_readiness(db: Session, student_id: str, subject_id: str = "math",
                      at: datetime | None = None) -> dict:
    student = db.get(Student, student_id)
    cls = db.get(SchoolClass, student.class_id) if student and student.class_id else None
    comps = level_competencies(db, cls, subject_id) if cls else []
    states = _states(db, [student_id], [c.id for c in comps])[student_id]
    return {"student": {"id": student_id, "name": student.name if student else ""},
            "class_name": cls.name if cls else "", "grade_level": cls.grade_level if cls else "",
            "exam": exam_name(cls), **readiness(comps, states, at)}


def class_readiness(db: Session, class_id: str, subject_id: str = "math",
                    at: datetime | None = None) -> dict:
    cls = db.get(SchoolClass, class_id)
    comps = level_competencies(db, cls, subject_id) if cls else []
    students = (db.query(Student).filter_by(class_id=class_id, active=True)
                .order_by(Student.order_index, Student.id).all())
    states = _states(db, [s.id for s in students], [c.id for c in comps])
    rows = []
    never_by_all = {c.id for c in comps}
    for s in students:
        r = readiness(comps, states[s.id], at)
        never_by_all &= {e["competency_id"] for e in r["never"]}
        rows.append({"id": s.id, "name": s.name, "solid_share": r["solid_share"],
                     "review_share": r["review_share"], "never_share": r["never_share"],
                     "solid_count": r["solid_count"], "review_count": r["review_count"],
                     "never_count": r["never_count"],
                     "top_review": [e["short_id"] or e["label"] for e in r["to_review"][:3]]})
    n = len(rows) or 1
    return {"class": {"id": class_id, "name": cls.name if cls else "",
                      "grade_level": cls.grade_level if cls else ""},
            "exam": exam_name(cls), "total": len(comps),
            "weighted": any(c.recommended_minutes or c.chapter_hours for c in comps),
            "solid_share": round(sum(r["solid_share"] for r in rows) / n, 3),
            "never_share": round(sum(r["never_share"] for r in rows) / n, 3),
            # jamais portées par aucun devoir de la classe : à programmer, pas à reprocher
            "never_for_class": [_entry(c, 0) for c in comps if c.id in never_by_all],
            "students": rows}


PLAN_MAX_LINES = 24


def revision_plan_pdf(data: dict) -> bytes:
    from reportlab.lib.units import mm
    from . import followup_pdf as fp
    exam = data.get("exam")

    def draw(new_page):
        page = new_page()
        title = f"Mon plan de révision{' · ' + exam if exam else ''}"
        page.header("Plan de révision", title, f"{data['student']['name']} · {data['class_name']}"
                    f" · {datetime.now(timezone.utc).strftime('%d/%m/%Y')}")
        page.paragraph(f"Aujourd'hui, {round(data['solid_share'] * 100)} % des notions du programme "
                       "sont solides. Voici, dans l'ordre, celles à revoir en premier : coche chacune "
                       "quand tu l'as retravaillée.", size=10.5)
        page.y -= 5 * mm
        rows = data["to_review"]
        if not rows:
            page.paragraph("Rien à revoir en priorité pour l'instant : continue à t'entraîner "
                           "régulièrement pour garder tes acquis bien frais.", color=fp.GREY)
        for i, e in enumerate(rows[:PLAN_MAX_LINES], 1):
            page.box(fp.X0, page.y - 3.6 * mm)
            text = f"{i}. {e['short_id'] + ' · ' if e['short_id'] else ''}{fp.plain(e['label'])}"
            y0 = page.y
            page.paragraph(text, size=10, x=fp.X0 + 7 * mm, width=fp.USABLE - 37 * mm)
            page.c.setFont("KNSans", 8); page.c.setFillColor(fp.GREY)
            page.c.drawRightString(fp.RIGHT, y0 - 3 * mm, e["reason"])
            if e.get("chapter"):
                page.paragraph(fp.plain(e["chapter"]), size=8, color=fp.GREY, x=fp.X0 + 7 * mm)
            page.y -= 2.2 * mm
            if page.y < 30 * mm:
                break
        rest = len(rows) - min(len(rows), PLAN_MAX_LINES)
        if rest > 0:
            page.paragraph(f"Et {rest} autre(s) ensuite, avec ton professeur.", size=9, color=fp.GREY)
        if data["never_count"]:
            page.y -= 3 * mm
            page.paragraph(f"{data['never_count']} notion(s) du programme n'ont pas encore été "
                           "travaillées en devoir : elles viendront en classe.", size=9, color=fp.GREY)
        page.footer("Kiro & Nina · plan calculé à partir de tes devoirs corrigés")

    return fp.document("Plan de révision", draw)

"""Groupes de besoin après un devoir corrigé (pour le professeur).

Déterministe, sans LLM. Pour un devoir :

  - chaque élève corrigé reçoit son besoin dominant : l'erreur type la plus
    souvent observée sur CE devoir (à égalité, celle qui touche le plus d'élèves
    de la classe), à défaut sa compétence la plus fragile parmi celles du devoir ;
  - on garde au plus trois groupes, les plus nombreux. Un élève dont le besoin
    dominant n'a pas de groupe rejoint un groupe qui vise une autre de ses
    erreurs du devoir, sinon il reste « hors groupe » ;
  - chaque groupe reçoit 3 à 5 exercices actifs de la banque qui travaillent son
    erreur ou sa compétence, choisis comme un sujet individuel : pas déjà vus par
    les élèves du groupe d'abord (student_history.candidate_rank), jamais une
    carte écartée par le professeur, une carte aimée de préférence, une seule
    carte par famille ;
  - « les trois erreurs à reprendre au tableau » : élèves concernés sur élèves
    qui ont eu l'occasion de la faire.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import (
    Assessment, Competency, Copy, CopyItemResult, CopyResult, GeneratedExercise,
    SchoolClass, Student, StudentCompetencyState, StudentLearningEvent,
)
from . import draw_rules, error_journal, forgetting, student_history
from .education import framework_for
from .exam_readiness import SOLID_STRENGTH
from .statement import strip_figure_marker, strip_guides

MAX_GROUPS = 3
MAX_BOARD = 3
MIN_EXERCISES, MAX_EXERCISES = 3, 5
COUNT_PREFIX = "comptage."   # attitude, pas notion (cf. error_journal.active_errors)
_TAXONOMY = Path(__file__).resolve().parents[1] / "data" / "misconceptions"


@lru_cache(maxsize=8)
def _labels(subject_id: str) -> dict[str, str]:
    path = _TAXONOMY / f"{subject_id}.json"
    if not path.exists():
        return {}
    return {e["code"]: e["label"] for e in json.loads(path.read_text(encoding="utf-8"))["errors"]}


def error_label(code: str, subject_id: str = "math", fallback: str = "") -> str:
    """L'erreur en clair : libellé de la taxonomie, à défaut le message figé au
    journal, à défaut le code rendu lisible."""
    return (_labels(subject_id).get(code) or fallback
            or code.rsplit(".", 1)[-1].replace("_", " ").capitalize())


def competency_text(c: Competency | None) -> str:
    if c is None:
        return "Compétence supprimée"
    return f"{c.short_id + ' · ' if c.short_id else ''}{c.label}"


# ------------------------------------------------------------------ lecture

def _errors_by_student(db: Session, copy_ids: list[str]) -> dict[str, list[StudentLearningEvent]]:
    """Dernière ligne de chaque couple (carte, erreur) du devoir, retraits exclus."""
    latest: dict[tuple[str, str], StudentLearningEvent] = {}
    if copy_ids:
        for row in (db.query(StudentLearningEvent)
                    .filter(StudentLearningEvent.copy_id.in_(copy_ids))
                    .order_by(StudentLearningEvent.revision).all()):
            latest[(row.copy_item_id, row.error_code)] = row
    out: dict[str, list[StudentLearningEvent]] = {}
    for row in latest.values():
        if row.kind == error_journal.KIND_ERROR and not row.error_code.startswith(COUNT_PREFIX):
            out.setdefault(row.student_id, []).append(row)
    return out


def board_errors(events: dict[str, list[StudentLearningEvent]], subject_id: str) -> list[dict]:
    """Erreurs à reprendre au tableau : élèves concernés / élèves exposés."""
    stats: dict[str, dict] = {}
    for student_id, rows in events.items():
        for row in rows:
            s = stats.setdefault(row.error_code, {"code": row.error_code, "concerned": set(),
                                                   "exposed": set(), "message": ""})
            s["exposed"].add(student_id)
            if row.observed:
                s["concerned"].add(student_id)
                s["message"] = s["message"] or row.message
    rows = [{"code": s["code"], "label": error_label(s["code"], subject_id, s["message"]),
             "students": len(s["concerned"]), "occasions": len(s["exposed"])}
            for s in stats.values() if s["concerned"]]
    rows.sort(key=lambda r: (-r["students"], -r["students"] / r["occasions"], r["code"]))
    return rows[:MAX_BOARD]


def _needs(db: Session, assessment: Assessment, results: list[CopyResult],
           events: dict[str, list[StudentLearningEvent]], class_counts: dict[str, int],
           at: datetime) -> dict[str, list[tuple]]:
    """Besoins ordonnés de chaque élève : ("error", code) puis ("competency", id)."""
    by_result = {r.id: r.student_id for r in results}
    ratios: dict[str, dict[str, list[float]]] = {}
    if by_result:
        for row in (db.query(CopyItemResult)
                    .filter(CopyItemResult.copy_result_id.in_(list(by_result))).all()):
            if row.competency_id:
                ratios.setdefault(by_result[row.copy_result_id], {}).setdefault(
                    row.competency_id, []).append(row.success_ratio or 0.0)
    states = {(s.student_id, s.competency_id): s for s in (
        db.query(StudentCompetencyState)
        .filter(StudentCompetencyState.student_id.in_(list(by_result.values()))).all())} if by_result else {}
    out = {}
    for student_id in by_result.values():
        counts: dict[str, int] = {}
        for row in events.get(student_id, []):
            if row.observed:
                counts[row.error_code] = counts.get(row.error_code, 0) + 1
        needs: list[tuple] = [("error", code) for code in sorted(
            counts, key=lambda c: (-counts[c], -class_counts.get(c, 0), c))]
        fragile = []
        for cid, values in ratios.get(student_id, {}).items():
            state = states.get((student_id, cid))
            strength = forgetting.strength(state, at) if state else sum(values) / len(values)
            if strength < SOLID_STRENGTH:
                fragile.append((strength, cid))
        needs += [("competency", cid) for _s, cid in sorted(fragile)]
        out[student_id] = needs
    return out


def _form_groups(needs: dict[str, list[tuple]]) -> tuple[list[tuple], dict[tuple, list[str]], list[str]]:
    dominant: dict[tuple, list[str]] = {}
    for sid, rows in needs.items():
        if rows:
            dominant.setdefault(rows[0], []).append(sid)
    keys = sorted(dominant, key=lambda k: (-len(dominant[k]), k[0] != "error", k[1]))[:MAX_GROUPS]
    members = {k: list(dominant[k]) for k in keys}
    unassigned = []
    for sid, rows in needs.items():
        if not rows or rows[0] in members:
            continue
        home = next((k for k in rows if k in members), None)
        if home:
            members[home].append(sid)
        else:
            unassigned.append(sid)
    return keys, members, unassigned


# ---------------------------------------------------------------- exercices

def _bank(db: Session, framework_comp_ids: set[str] | None) -> list[GeneratedExercise]:
    q = db.query(GeneratedExercise).filter(GeneratedExercise.status == "active")
    if framework_comp_ids is not None:
        q = q.filter(GeneratedExercise.competency_id.in_(framework_comp_ids))
    return q.all()


def propose_exercises(rows: list[GeneratedExercise], logs: list[dict], context: draw_rules.DrawContext,
                      at: datetime, preferred_comps: list[str] = ()) -> list[GeneratedExercise]:
    """3 à 5 cartes pour un groupe, le moins déjà vu d'abord."""
    rows = draw_rules.allowed(rows, context)
    pref = {cid: i for i, cid in enumerate(preferred_comps)}

    def key(row):
        ranks = [student_history.candidate_rank(row, log, at)[0] for log in logs]
        return (sum(1 for log in logs if row.id in log), sum(ranks),
                pref.get(row.competency_id, len(pref)), row.id not in context.likes,
                row.difficulty_level, row.id)

    chosen, families = [], set()
    for row in sorted(rows, key=key):
        family = (row.grading_json or {}).get("family_id") or row.twin_group
        if family and family in families:
            continue
        if family:
            families.add(family)
        chosen.append(row)
        if len(chosen) == MAX_EXERCISES:
            break
    return chosen


def _exercise_out(row: GeneratedExercise, comps: dict[str, Competency], logs: dict[str, dict]) -> dict:
    comp = comps.get(row.competency_id)
    text = strip_figure_marker(strip_guides(row.statement or "")).strip()
    return {"id": row.id, "competency_id": row.competency_id,
            "competency": competency_text(comp), "level": row.difficulty_level,
            "response_type": row.response_type, "statement": text,
            "correction": (row.correction or "")[:400],"has_figure": bool(row.figure_json),
            "seen_by": sum(1 for log in logs.values() if row.id in log)}


# ------------------------------------------------------------------- entrée

def need_groups(db: Session, assessment_id: str, at: datetime | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    assessment = db.get(Assessment, assessment_id)
    subject_id = assessment.subject_id or "math"
    copies = db.query(Copy).filter_by(assessment_id=assessment_id).all()
    results = db.query(CopyResult).filter_by(assessment_id=assessment_id).all()
    events = _errors_by_student(db, [c.id for c in copies])
    corrected = {r.student_id for r in results}
    events = {sid: rows for sid, rows in events.items() if sid in corrected}
    class_counts: dict[str, int] = {}
    for rows in events.values():
        for code in {r.error_code for r in rows if r.observed}:
            class_counts[code] = class_counts.get(code, 0) + 1
    needs = _needs(db, assessment, results, events, class_counts, at)
    keys, members, unassigned = _form_groups(needs)
    students = {s.id: s for s in db.query(Student).filter(Student.id.in_(list(corrected))).all()} if corrected else {}

    def people(ids):
        ids = [i for i in ids if i in students]
        ids.sort(key=lambda i: (students[i].order_index, students[i].name))
        return [{"id": i, "name": students[i].name} for i in ids]

    cls = db.get(SchoolClass, assessment.class_id)
    fw = None
    if assessment.framework_id:
        from ..models import CompetencyFramework
        fw = db.get(CompetencyFramework, assessment.framework_id)
    if fw is None and cls is not None:
        fw = framework_for(db, cls, subject_id)
    fw_comp_ids = ({c.id for c in db.query(Competency.id).filter_by(framework_id=fw.id)}
                   if fw else None)
    bank = _bank(db, fw_comp_ids) if keys else []
    context = draw_rules.load_context(db, assessment)
    comp_ids = {r.competency_id for r in bank} | {k[1] for k in keys if k[0] == "competency"}
    comps = {c.id: c for c in db.query(Competency).filter(Competency.id.in_(comp_ids)).all()} if comp_ids else {}

    groups = []
    for index, k in enumerate(keys, 1):
        sids = members[k]
        logs = {sid: student_history.exercise_log(db, sid) for sid in sids}
        if k[0] == "error":
            observed_comps: dict[str, int] = {}
            message = ""
            for sid in sids:
                for row in events.get(sid, []):
                    if row.error_code == k[1] and row.observed:
                        message = message or row.message
                        if row.competency_id:
                            observed_comps[row.competency_id] = observed_comps.get(row.competency_id, 0) + 1
            pool = [r for r in bank if k[1] in draw_rules.row_error_targets(r)]
            preferred = sorted(observed_comps, key=lambda c: -observed_comps[c])
            label = error_label(k[1], subject_id, message)
            detail = ", ".join(competency_text(comps.get(c)) for c in preferred[:2] if c in comps)
        else:
            pool = [r for r in bank if r.competency_id == k[1]]
            preferred = [k[1]]
            comp = comps.get(k[1]) or db.get(Competency, k[1])
            label = competency_text(comp)
            detail = comp.chapter_name if comp else ""
        picked = propose_exercises(pool, list(logs.values()), context, at, preferred)
        groups.append({
            "index": index, "kind": k[0], "code": k[1] if k[0] == "error" else None,
            "competency_id": k[1] if k[0] == "competency" else None,
            "label": label, "detail": detail, "students": people(sids),
            "exercises": [_exercise_out(r, comps, logs) for r in picked],
            "enough_exercises": len(picked) >= MIN_EXERCISES,
        })
    return {
        "assessment": {"id": assessment.id, "title": assessment.title,
                       "class_name": cls.name if cls else "", "status": assessment.status},
        "corrected": len(corrected),
        "groups": groups,
        "board": board_errors(events, subject_id),
        "unassigned": people(unassigned),
        "without_need": len([s for s, n in needs.items() if not n]),
    }


LEVELS = {1: "facile", 2: "base", 3: "difficile"}


def group_sheet_pdf(data: dict, group: dict) -> bytes:
    """Fiche du groupe pour le professeur : élèves, cible, exercices et réponses.
    Énoncés en texte (LaTeX aplati) : les figures restent dans la banque."""
    from reportlab.lib.units import mm
    from . import followup_pdf as fp

    def draw(new_page):
        page = new_page()
        a = data["assessment"]
        page.header("Groupe de besoin", fp.plain(group["label"]),
                    f"{a['class_name']} · après « {a['title']} »")
        if group.get("detail"):
            page.paragraph(fp.plain(group["detail"]), size=9.5, color=fp.GREY)
            page.y -= 3 * mm
        page.label(f"Élèves ({len(group['students'])})")
        page.paragraph(", ".join(s["name"] for s in group["students"]), size=11)
        page.y -= 6 * mm
        page.label("Exercices proposés")
        if not group["exercises"]:
            page.paragraph("Aucun exercice de la banque ne vise encore ce besoin.", color=fp.GREY)
        for i, ex in enumerate(group["exercises"], 1):
            if page.y < 45 * mm:
                page.footer("Kiro & Nina · fiche de groupe (suite page suivante)")
                page = new_page()
            page.paragraph(f"{i}. {fp.plain(ex['competency'])} · {LEVELS.get(ex['level'], '')}",
                           "KNSansSb", 10)
            page.y -= 1 * mm
            page.paragraph(fp.plain(ex["statement"]) or "(énoncé vide)", size=10)
            if ex["has_figure"]:
                page.paragraph("Figure : voir la carte dans la banque.", size=8.5, color=fp.GREY)
            if ex["correction"]:
                page.y -= 1 * mm
                page.paragraph(f"Réponse : {fp.plain(ex['correction'])}", size=8.5, color=fp.GREY)
            page.y -= 5 * mm
        page.footer("Kiro & Nina · fiche de groupe, à l'usage du professeur")

    return fp.document("Groupe de besoin", draw)

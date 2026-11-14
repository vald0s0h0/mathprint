"""Bilan pour la famille : une page papier par trimestre et par élève.

Déterministe, sans LLM. Le contenu est calculé, puis figé dans un StudentReport
(prompt_version FAMILY_VERSION, contenu JSON) que le professeur relit, retouche
et valide avant impression : brouillon → validé → imprimé.

Jamais de comparaison aux autres élèves : la courbe compare l'élève à son propre
passé, les acquis et la chose à travailler ne parlent que de lui.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    Competency, CompetencyEvidence, CompetencyFramework, CompetencyStateHistory,
    SchoolClass, Student, StudentCompetencyState, StudentReport,
)
from . import error_journal, forgetting
from .exam_readiness import SOLID_STRENGTH

FAMILY_VERSION = "famille-1"
N_ACQUIRED = 3
CURVE_POINTS = 12
TRIMESTERS = {1: ((9, 1), (12, 1)), 2: ((12, 1), (3, 1)), 3: ((3, 1), (8, 1))}
TRIMESTER_NAMES = {1: "1er trimestre", 2: "2e trimestre", 3: "3e trimestre"}

# Gestes à la maison : aucun ne demande de connaître la matière. Le type de la
# compétence est deviné sur son libellé et son chapitre (premier motif trouvé).
GESTURES: list[tuple[str, str, list[str]]] = [
    ("geometrie", r"constru|trac|figure|triangle|cercle|angle|droite|parall|perpendic|sym[ée]tri|quadrilat|polygone|solide|pav[ée]|cube|th[ée]or[èe]me|pythagore|thal[èe]s|trigono", [
        "Demandez-lui de refaire une figure du cahier sur une feuille blanche, en vous disant à voix haute chaque instrument qu'il prend et pourquoi.",
        "Proposez-lui de reconnaître autour de vous (meubles, carrelage, fenêtres) les formes vues en classe et de vous les nommer.",
    ]),
    ("mesure", r"aire|p[ée]rim[èe]tre|volume|longueur|masse|dur[ée]e|unit[ée]|convert|mesur|contenance|vitesse", [
        "Mesurez ensemble un objet de la maison (table, pièce, bouteille) et demandez-lui de vous dire le résultat dans deux unités différentes.",
        "Lors d'une course ou d'une recette, laissez-le lire les quantités et vous expliquer ce qu'elles représentent.",
    ]),
    ("proportion", r"proportion|pourcent|%|[ée]chelle|ratio|taux|r[èe]gle de trois", [
        "Pendant les courses, demandez-lui combien coûteraient deux ou trois fois la même quantité, puis comment il l'a trouvé.",
        "Doublez ou divisez une recette avec lui : il calcule, vous vérifiez seulement que le plat est réussi.",
    ]),
    ("donnees", r"statist|moyenne|m[ée]diane|probab|graphique|diagramme|tableau|donn[ée]es|fr[ée]quence", [
        "Montrez-lui un graphique du journal ou d'une facture et demandez-lui de vous raconter ce qu'il montre.",
        "Notez ensemble pendant une semaine une petite donnée du quotidien (temps de trajet, météo) et demandez-lui ce qu'il en conclut.",
    ]),
    ("algebre", r"[ée]quation|in[ée]quation|litt[ée]ral|expression|fonction|d[ée]velopp|factoris|variable|alg[ée]br|suite|d[ée]riv|polyn", [
        "Demandez-lui de vous expliquer, comme à quelqu'un qui n'y connaît rien, une méthode de son cahier, étape par étape.",
        "Proposez-lui d'inventer un exercice du même type que ceux du cahier, puis de le résoudre devant vous.",
    ]),
    ("calcul", r"calcul|op[ée]ration|addition|soustr|multipli|divis|fraction|d[ée]cimal|nombre|entier|relatif|puissance|racine|priorit|arrondi|comparer|ranger", [
        "Dix minutes, trois soirs par semaine : il refait sans le corrigé un calcul du cahier déjà corrigé, puis compare lui-même avec la correction.",
        "Au quotidien (monnaie, horaires, prix), laissez-lui faire les petits calculs de tête et demandez-lui comment il s'y est pris.",
    ]),
    ("raisonnement", r"r[ée]soudre|probl[èe]me|justifi|d[ée]montr|raisonn|argument|mod[ée]lis|d[ée]marche", [
        "Lisez avec lui l'énoncé d'un problème du cahier et demandez-lui de vous le raconter avec ses propres mots avant de chercher.",
        "Quand il bloque, demandez-lui simplement : « Qu'est-ce qu'on cherche ? Qu'est-ce qu'on sait déjà ? »",
    ]),
]
DEFAULT_GESTURES = [
    "Demandez-lui de vous expliquer à voix haute un exercice réussi du cahier, sans l'interrompre : expliquer, c'est apprendre une deuxième fois.",
    "Aidez-le à installer un petit rituel de dix minutes, trois soirs par semaine, pour relire sa leçon et refaire un exercice.",
]


def competency_kind(c: Competency) -> str:
    text = f"{c.label} {c.chapter_name} {c.domain_name}".lower()
    for kind, pattern, _g in GESTURES:
        if re.search(pattern, text):
            return kind
    return "general"


def gesture_for(c: Competency, seed: str) -> str:
    kind = competency_kind(c)
    options = next((g for k, _p, g in GESTURES if k == kind), DEFAULT_GESTURES)
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]


# ------------------------------------------------------------------ périodes

def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """« 2026-2027-T1 » → [1er sept. 2026, 1er déc. 2026[."""
    m = re.fullmatch(r"(\d{4})-(\d{4})-T([123])", period or "")
    if not m or int(m[2]) != int(m[1]) + 1:
        raise ValueError("Période attendue : AAAA-AAAA-T1, T2 ou T3")
    year, t = int(m[1]), int(m[3])
    (sm, sd), (em, ed) = TRIMESTERS[t]
    start = datetime(year if t == 1 or sm >= 9 else year + 1, sm, sd, tzinfo=timezone.utc)
    end = datetime(year if t == 1 else year + 1, em, ed, tzinfo=timezone.utc)
    return start, end


def current_period(at: datetime | None = None) -> str:
    at = at or datetime.now(timezone.utc)
    year = at.year if at.month >= 9 else at.year - 1
    for t in (1, 2, 3):
        start, end = period_bounds(f"{year}-{year + 1}-T{t}")
        if start <= at < end:
            return f"{year}-{year + 1}-T{t}"
    return f"{year}-{year + 1}-T3"   # été : le dernier trimestre de l'année


def period_label(period: str) -> str:
    year, nxt, t = period.split("-")
    return f"{TRIMESTER_NAMES[int(t[1])]} {year}-{nxt}"


# ------------------------------------------------------------------ calculs

def _subject_states(db: Session, student_id: str, subject_id: str) -> list[StudentCompetencyState]:
    comp_ids = (db.query(Competency.id).join(CompetencyFramework, CompetencyFramework.id == Competency.framework_id)
                .filter(CompetencyFramework.subject_id == subject_id))
    return (db.query(StudentCompetencyState)
            .filter(StudentCompetencyState.student_id == student_id,
                    StudentCompetencyState.competency_id.in_(comp_ids)).all())


def progress_curve(db: Session, student_id: str, comp_ids: set[str], end: datetime,
                   start: datetime) -> tuple[list[dict], int | None]:
    """Maîtrise moyenne des compétences déjà travaillées, devoir après devoir
    (un point par jour de devoir). Rejouée depuis l'historique de la courbe
    d'oubli : c'est la même grandeur que celle qui choisit les exercices."""
    if not comp_ids:
        return [], None
    evs = (db.query(CompetencyEvidence)
           .filter(CompetencyEvidence.student_id == student_id,
                   CompetencyEvidence.competency_id.in_(comp_ids)).all())
    evs = [e for e in evs if _utc(e.observed_at) and _utc(e.observed_at) < end]
    after: dict[str, float] = {}
    if evs:
        for h in (db.query(CompetencyStateHistory)
                  .filter(CompetencyStateHistory.evidence_id.in_([e.id for e in evs]))
                  .order_by(CompetencyStateHistory.created_at).all()):
            after[h.evidence_id] = float((h.after_json or {}).get("mastery", 0.0))
    evs.sort(key=lambda e: (_utc(e.observed_at), e.id))
    current: dict[str, float] = {}
    points: list[dict] = []
    for e in evs:
        if e.id not in after:
            continue
        current[e.competency_id] = after[e.id]
        day = _utc(e.observed_at).date()
        value = sum(current.values()) / len(current)
        if points and points[-1]["date"] == day.isoformat():
            points[-1]["value"] = round(value, 3)
        else:
            points.append({"date": day.isoformat(), "label": day.strftime("%d/%m"), "value": round(value, 3)})
    points = points[-CURVE_POINTS:]
    marker = next((i for i, p in enumerate(points) if p["date"] >= start.date().isoformat()), None)
    return points, marker


def vanquished_errors(db: Session, student_id: str, subject_id: str,
                      start: datetime, end: datetime) -> list[str]:
    """Erreurs types vaincues pendant la période et restées vaincues : la règle
    est celle d'error_journal.closures, la même que la mention « Erreur
    vaincue » imprimée sur la copie corrigée."""
    out = set()
    for code, row, still in error_journal.closures(db, student_id, subject_id):
        at = _utc(row.occurred_at)
        if still and at and start <= at < end:
            out.add(code)
    return sorted(out)


def compute(db: Session, student_id: str, subject_id: str, period: str,
            at: datetime | None = None) -> dict:
    start, end = period_bounds(period)
    now = at or datetime.now(timezone.utc)
    at = min(now, end)
    student = db.get(Student, student_id)
    cls = db.get(SchoolClass, student.class_id) if student.class_id else None
    states = [s for s in _subject_states(db, student_id, subject_id)
              if _utc(s.last_seen_at) and _utc(s.last_seen_at) < end]
    comps = {c.id: c for c in db.query(Competency).filter(
        Competency.id.in_([s.competency_id for s in states])).all()} if states else {}
    strengths = {s.competency_id: forgetting.strength(s, at) for s in states}

    solid = sorted((s for s in states if strengths[s.competency_id] >= SOLID_STRENGTH and s.competency_id in comps),
                   key=lambda s: (-strengths[s.competency_id], -_utc(s.last_seen_at).timestamp()))
    acquired = [{"competency_id": s.competency_id, "label": comps[s.competency_id].label}
                for s in solid[:N_ACQUIRED]]

    # Une chose à travailler : d'abord une compétence où traîne une erreur
    # active, sinon la plus prioritaire des fragiles, vue pendant la période de préférence.
    with_error = error_journal.active_error_competencies(db, student_id, subject_id)
    fragile = [s for s in states if strengths[s.competency_id] < SOLID_STRENGTH and s.competency_id in comps]
    fragile.sort(key=lambda s: (s.competency_id not in with_error,
                                not (_utc(s.last_seen_at) >= start),
                                strengths[s.competency_id], s.competency_id))
    to_work = None
    if fragile:
        c = comps[fragile[0].competency_id]
        to_work = {"competency_id": c.id, "label": c.label,
                   "gesture": gesture_for(c, f"{student_id}:{period}")}

    curve, marker = progress_curve(db, student_id, set(comps) | {s.competency_id for s in states}, end, start)
    vanquished = vanquished_errors(db, student_id, subject_id, start, end)
    return {"period": period, "period_label": period_label(period),
            "student": {"id": student_id, "name": student.name},
            "class_name": cls.name if cls else "", "subject_id": subject_id,
            "acquired": acquired, "to_work": to_work, "curve": curve, "curve_marker": marker,
            "vanquished": len(vanquished), "teacher_note": "",
            "computed_at": now.isoformat()}


# ------------------------------------------------------------ brouillon, validation

def find(db: Session, student_id: str, subject_id: str, period: str) -> StudentReport | None:
    return (db.query(StudentReport)
            .filter_by(student_id=student_id, subject_id=subject_id, period=period,
                       prompt_version=FAMILY_VERSION)
            .order_by(StudentReport.created_at.desc()).first())


def read(report: StudentReport) -> dict:
    try:
        data = json.loads(report.content or "{}")
    except ValueError:
        data = {}
    return {**data, "report_id": report.id, "status": report.status}


def prepare(db: Session, student_id: str, subject_id: str, period: str,
            at: datetime | None = None) -> StudentReport:
    """Crée ou recalcule le brouillon. Un bilan validé ou imprimé n'est jamais
    recalculé en silence : ValueError."""
    report = find(db, student_id, subject_id, period)
    if report and report.status != "draft":
        raise ValueError("Bilan déjà validé : repassez-le en brouillon pour le recalculer")
    data = compute(db, student_id, subject_id, period, at)
    if report is None:
        report = StudentReport(student_id=student_id, subject_id=subject_id, period=period,
                               prompt_version=FAMILY_VERSION)
        db.add(report)
    else:
        old = read(report)
        data["teacher_note"] = old.get("teacher_note", "")
    report.content = json.dumps(data, ensure_ascii=False)
    report.status = "draft"
    db.flush()
    return report


EDITABLE = ("to_work_label", "gesture", "teacher_note")


def edit(report: StudentReport, changes: dict) -> None:
    data = json.loads(report.content or "{}")
    if changes.get("teacher_note") is not None:
        data["teacher_note"] = changes["teacher_note"].strip()[:400]
    if data.get("to_work"):
        if changes.get("to_work_label"):
            data["to_work"]["label"] = changes["to_work_label"].strip()[:300]
        if changes.get("gesture"):
            data["to_work"]["gesture"] = changes["gesture"].strip()[:400]
    report.content = json.dumps(data, ensure_ascii=False)


# ------------------------------------------------------------------- impression

def draw_page(page, data: dict) -> None:
    from reportlab.lib.units import mm
    from . import followup_pdf as fp
    page.header("Bilan pour la famille", f"Bilan du {data['period_label']}",
                f"{data['student']['name']}{' · ' + data['class_name'] if data.get('class_name') else ''}")

    page.label("Ce qui est acquis aujourd'hui")
    if not data["acquired"]:
        page.paragraph("Les premiers acquis se construisent devoir après devoir.", color=fp.GREY)
    for a in data["acquired"]:
        page.check(fp.X0, page.y - 3.6 * mm)
        page.paragraph(fp.plain(a["label"]), size=11, x=fp.X0 + 7 * mm)
        page.y -= 2 * mm
    page.y -= 5 * mm

    page.label("Une chose à travailler")
    work = data.get("to_work")
    if work:
        page.paragraph(fp.plain(work["label"]), "KNSansMd", 11)
        page.y -= 3 * mm
        top = page.y
        page.y -= 3 * mm
        page.paragraph("À la maison", "KNSansSb", 9, color=fp.ACCENT, x=fp.X0 + 4 * mm)
        page.y -= 1 * mm
        page.paragraph(work["gesture"], size=10.5, x=fp.X0 + 4 * mm, width=fp.USABLE - 8 * mm)
        page.y -= 3 * mm
        page.c.setStrokeColor(fp.SOFT); page.c.setLineWidth(0.8)
        page.c.roundRect(fp.X0, page.y, fp.USABLE, top - page.y, 2 * mm, stroke=1, fill=0)
    else:
        page.paragraph("Rien de particulier ce trimestre : continuer à s'entraîner régulièrement.",
                       color=fp.GREY)
    page.y -= 8 * mm

    page.label("Ses progrès, comparés à son propre passé")
    page.paragraph("Maîtrise moyenne des notions travaillées, devoir après devoir.", size=8.5, color=fp.GREY)
    page.y -= 3 * mm
    fp.curve(page, data.get("curve") or [], marker_index=data.get("curve_marker"))
    page.y -= 3 * mm

    page.label("Erreurs vaincues")
    n = data.get("vanquished", 0)
    if n:
        page.paragraph(f"{n} erreur{'s' if n > 1 else ''} qui revenai{'en' if n > 1 else ''}t souvent "
                       f"{'ont' if n > 1 else 'a'} disparu ce trimestre.", size=11)
    else:
        page.paragraph("Chaque erreur corrigée sur les copies est une occasion de progresser : "
                       "les prochaines erreurs vaincues s'afficheront ici.", size=10, color=fp.GREY)
    if data.get("teacher_note"):
        page.y -= 6 * mm
        page.label("Le mot du professeur")
        page.paragraph(data["teacher_note"], "KNSerifIt", 11.5)

    page.c.setStrokeColor(fp.RULE); page.c.setLineWidth(0.5)
    page.c.line(fp.RIGHT - 60 * mm, 22 * mm, fp.RIGHT, 22 * mm)
    page.c.setFont("KNSans", 8); page.c.setFillColor(fp.GREY)
    page.c.drawString(fp.RIGHT - 60 * mm, 18.5 * mm, "Signature des parents")
    page.footer("Kiro & Nina · bilan relu et validé par le professeur")


def pdf(reports: list[dict]) -> bytes:
    from . import followup_pdf as fp

    def draw(new_page):
        for data in reports:
            draw_page(new_page(), data)

    return fp.document("Bilan pour la famille", draw)

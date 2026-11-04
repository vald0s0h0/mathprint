"""État de la banque, en lecture seule, pour l'onglet Exercices de l'administrateur.

Deux questions distinctes :
- Épuisement : reste-t-il des exercices inédits pour les élèves qui travaillent
  cette compétence ? Le stock total ne suffit pas : 20 exercices peuvent être
  épuisés pour une classe, et intacts pour une autre.
- Retour du terrain : comment les exercices se comportent-ils sur de vraies copies ?
  Le rapport signale ; rien n'est modifié automatiquement.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import (
    Assessment, Competency, CompetencyFramework, Copy, CopyItem, CopyItemResult, GeneratedExercise,
    GradingDecision, SchoolClass, Student, StudentResponse,
)

LEVEL_NAMES = {1: "facile", 2: "moyen", 3: "difficile"}
ORANGE_UNSEEN = 3
ORANGE_SHARE = 0.30
ORANGE_WEEKS = 4
RATE_WINDOW_WEEKS = 8
TARGET_WEEKS = 8
ADVICE_MIN, ADVICE_MAX = 5, 20

MIN_RESPONSES = 30
MIN_CLASSES = 3
EASY_TOO_HARD = 0.5
HARD_TOO_EASY = 0.85
BLANK_TOO_HIGH = 0.3
TEACHER_TOO_HIGH = 0.3
GROUP_SHARE = 0.27          # meilleurs et moins bons élèves, pour la discrimination


def _students_by_framework(db: Session) -> dict[str, list[str]]:
    """Élèves actifs de chaque référentiel, déduit de leur classe."""
    frameworks = {(f.program_id or "fr", f.grade_level, f.path_id): f.id
                  for f in db.query(CompetencyFramework).filter(CompetencyFramework.status != "archived").all()}
    out: dict[str, list[str]] = {}
    for student, klass in (db.query(Student, SchoolClass)
                           .join(SchoolClass, Student.class_id == SchoolClass.id)
                           .filter(Student.active.is_(True)).all()):
        key = (klass.program_id or "fr", klass.grade_level, klass.series_id)
        framework_id = frameworks.get(key)
        if framework_id:
            out.setdefault(framework_id, []).append(student.id)
    return out


def _seen_by_student(db: Session) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    rows = (db.query(Copy.student_id, CopyItem.generated_exercise_id)
            .join(CopyItem, CopyItem.copy_id == Copy.id)
            .filter(CopyItem.generated_exercise_id.isnot(None),
                    CopyItem.generated_exercise_id != "").all())
    for student_id, exercise_id in rows:
        out.setdefault(student_id, set()).add(exercise_id)
    return out


def _served_recently(db: Session, weeks: int = RATE_WINDOW_WEEKS) -> dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    out: dict[str, int] = {}
    rows = (db.query(CopyItem.generated_exercise_id)
            .join(Copy, Copy.id == CopyItem.copy_id)
            .filter(Copy.generated_at >= since.replace(tzinfo=None),
                    CopyItem.generated_exercise_id.isnot(None)).all())
    for (exercise_id,) in rows:
        out[exercise_id] = out.get(exercise_id, 0) + 1
    return out


def _advice(served_per_week: float, students: int) -> int:
    """Combien en générer : ce que consomment 8 semaines, borné. On ne génère que
    ce qui sert réellement ; 20 reste le point de départ, pas un objectif fixe."""
    if not served_per_week or not students:
        return ADVICE_MIN
    need = round(served_per_week * TARGET_WEEKS / students)
    return max(ADVICE_MIN, min(ADVICE_MAX, need))


def exhaustion(db: Session) -> list[dict]:
    """Compétences et chapitres où il manque des exercices, les plus urgents d'abord."""
    students = _students_by_framework(db)
    seen = _seen_by_student(db)
    served = _served_recently(db)
    frameworks = {f.id: f for f in db.query(CompetencyFramework).all()}
    comps = {c.id: c for c in db.query(Competency).filter(
        Competency.framework_id.in_(list(students) or [""])).all()}
    groups: dict[tuple, list[GeneratedExercise]] = {}
    for row in db.query(GeneratedExercise).filter(
            GeneratedExercise.status == "active",
            GeneratedExercise.competency_id.in_(list(comps) or [""])).all():
        comp = comps[row.competency_id]
        problem = row.kind == "probleme"
        key = (comp.framework_id, comp.chapter_code if problem else comp.id,
               "probleme" if problem else "exercice", row.difficulty_level)
        groups.setdefault(key, []).append(row)
    alerts = []
    for (framework_id, target, kind, level), rows in groups.items():
        audience = students.get(framework_id) or []
        if not audience:
            continue
        ids = {r.id for r in rows}
        unseen = [len(ids - seen.get(s, set())) for s in audience]
        out_of_stock = sum(1 for n in unseen if n == 0)
        low_share = sum(1 for n in unseen if n < ORANGE_UNSEEN) / len(audience)
        per_week = sum(served.get(i, 0) for i in ids) / RATE_WINDOW_WEEKS
        median_unseen = statistics.median(unseen)
        weeks_left = (median_unseen * len(audience) / per_week) if per_week else None
        if out_of_stock:
            severity = "rouge"
        elif low_share > ORANGE_SHARE or (weeks_left is not None and weeks_left < ORANGE_WEEKS):
            severity = "orange"
        else:
            continue
        framework = frameworks[framework_id]
        comp = comps.get(target)
        code = (comp.short_id or comp.code) if comp else target
        advice = _advice(per_week, len(audience))
        flag = {1: "facile", 2: "moyen", 3: "difficile"}[level]
        suffix = " --problemes" if kind == "probleme" else ""
        alerts.append({
            "severity": severity, "programme": framework.program_id, "cycle": framework.grade_level,
            "target": code, "label": comp.label if comp else "Problèmes du chapitre", "kind": kind,
            "difficulty": LEVEL_NAMES[level], "stock": len(rows), "students": len(audience),
            "students_out": out_of_stock, "median_unseen": median_unseen,
            "served_per_week": round(per_week, 1),
            "weeks_left": round(weeks_left, 1) if weeks_left is not None else None,
            "advice": advice,
            "command": f"forge {framework.program_id} {framework.grade_level} {code}{suffix} "
                       f"--complement {advice} --{flag} {advice}"})
    alerts.sort(key=lambda a: (a["severity"] != "rouge", a["weeks_left"] is None, a["weeks_left"] or 0,
                               a["target"]))
    return alerts


def _quality_notes(row, level: int, stats: dict) -> list[dict]:
    notes = []
    success = stats["success"]
    if level == 1 and success < EASY_TOO_HARD:
        notes.append({"signal": "difficulte", "message": f"Facile réussi par {success:.0%} des élèves : "
                      "difficulté mal étiquetée, ou énoncé ambigu."})
    if level == 3 and success > HARD_TOO_EASY:
        notes.append({"signal": "difficulte", "message": f"Difficile réussi à {success:.0%} : à reclasser, "
                      "et à revoir au barème."})
    if stats["blank"] > BLANK_TOO_HIGH:
        notes.append({"signal": "non_reponse", "message": f"{stats['blank']:.0%} de non-réponses : temps "
                      "sous-estimé, ou consigne mal comprise."})
    if stats["teacher"] > TEACHER_TOO_HIGH:
        notes.append({"signal": "correction", "message": f"{stats['teacher']:.0%} de corrections à la main : "
                      "lecture CV ou réponses équivalentes à revoir."})
    if stats.get("discrimination") is not None and stats["discrimination"] < 0:
        notes.append({"signal": "discrimination", "message": "Les bons élèves échouent plus que les autres : "
                      "signal fort d'erreur dans l'exercice ou son corrigé."})
    for option in stats["options"]:
        if option["share"] == 0:
            notes.append({"signal": "distracteur", "message": f"Distracteur « {option['text']} » jamais choisi : "
                          "piège sans intérêt, à remplacer."})
        elif option["share"] > stats["success"] and not option["correct"]:
            notes.append({"signal": "distracteur", "message": f"Distracteur « {option['text']} » choisi plus que "
                          f"la bonne réponse ({option['share']:.0%}) : vérifier d'abord le corrigé."})
    return notes


def field_report(db: Session) -> list[dict]:
    """Exercices dont le comportement réel mérite un regard. Seuil : 30 réponses sur
    au moins 3 classes, en dessous desquelles un écart ne prouve rien."""
    rows = (db.query(CopyItemResult, CopyItem, Copy, Assessment)
            .join(CopyItem, CopyItemResult.copy_item_id == CopyItem.id)
            .join(Copy, CopyItem.copy_id == Copy.id)
            .join(Assessment, Copy.assessment_id == Assessment.id)
            .filter(CopyItem.generated_exercise_id.isnot(None),
                    CopyItem.generated_exercise_id != "").all())
    by_exercise: dict[str, list[tuple]] = {}
    for result, item, copy, assessment in rows:
        by_exercise.setdefault(item.generated_exercise_id, []).append((result, item, copy, assessment))
    report = []
    bank = {r.id: r for r in db.query(GeneratedExercise).filter(
        GeneratedExercise.id.in_(list(by_exercise) or [""])).all()}
    for exercise_id, entries in by_exercise.items():
        row = bank.get(exercise_id)
        classes = {assessment.class_id for *_x, assessment in entries}
        if row is None or len(entries) < MIN_RESPONSES or len(classes) < MIN_CLASSES:
            continue
        stats = _exercise_stats(db, entries)
        notes = _quality_notes(row, row.difficulty_level, stats)
        if notes:
            report.append({"exercise_id": exercise_id, "statement": row.statement[:140],
                           "source": row.source, "source_version": row.source_version,
                           "difficulty": LEVEL_NAMES.get(row.difficulty_level),
                           "responses": len(entries), "classes": len(classes),
                           "success": round(stats["success"], 3), "notes": notes})
    report.sort(key=lambda r: -len(r["notes"]))
    return report


def _copy_ratio(db: Session, copy_id: str, cache: dict) -> float:
    if copy_id not in cache:
        results = db.query(CopyItemResult).join(CopyItem, CopyItemResult.copy_item_id == CopyItem.id).filter(
            CopyItem.copy_id == copy_id).all()
        total = sum(r.success_ratio or 0 for r in results)
        cache[copy_id] = total / len(results) if results else 0.0
    return cache[copy_id]


def _exercise_stats(db: Session, entries: list[tuple]) -> dict:
    n = len(entries)
    success = sum((r.success_ratio or 0) >= 0.999 for r, *_ in entries) / n
    item_ids = [item.id for _r, item, *_ in entries]
    responses = {r.copy_item_id: r for r in db.query(StudentResponse).filter(
        StudentResponse.copy_item_id.in_(item_ids)).all()}
    decisions: dict[str, GradingDecision] = {}
    if responses:
        for decision in (db.query(GradingDecision).filter(
                GradingDecision.response_id.in_([r.id for r in responses.values()]))
                .order_by(GradingDecision.created_at).all()):
            decisions[decision.response_id] = decision
    blank = teacher = 0
    # Compté par TEXTE de la proposition : son rang dépend de la copie quand les
    # champs de réponse sont mélangés (services.answer_mix).
    chosen: dict[str, int] = {}
    for _r, item, *_x in entries:
        response = responses.get(item.id)
        decision = decisions.get(response.id) if response else None
        reason = (decision.reason_code or "") if decision else ""
        blank += reason in ("blank", "qcm_blank", "matching_blank")
        teacher += (decision.source if decision else "") == "teacher" or reason.startswith("teacher_")
        choices = (item.grading_json or {}).get("choices") or []
        for index in (response.selected_choices or []) if response else []:
            if isinstance(index, int) and 0 <= index < len(choices):
                chosen[choices[index]] = chosen.get(choices[index], 0) + 1
    options = []
    _r, first_item, *_ = entries[0]
    if first_item.response_type.startswith("qcm"):
        correct = set((first_item.expected_json or {}).get("correct") or [])
        for index, text in enumerate((first_item.grading_json or {}).get("choices") or []):
            options.append({"text": text, "correct": index in correct, "share": chosen.get(text, 0) / n})
    cache: dict = {}
    ranked = sorted(entries, key=lambda e: _copy_ratio(db, e[2].id, cache))
    size = max(1, round(n * GROUP_SHARE))
    low = sum(r.success_ratio or 0 for r, *_ in ranked[:size]) / size
    high = sum(r.success_ratio or 0 for r, *_ in ranked[-size:]) / size
    return {"success": success, "blank": blank / n, "teacher": teacher / n, "options": options,
            "discrimination": high - low}

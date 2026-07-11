"""Création d'exercices et de rappels de leçon par DeepSeek (deepseek-v4-pro).

Pipeline robuste :
1. le prompt cible précisément une compétence du programme officiel (code,
   libellé, thème, domaine + objectifs voisins du même thème comme contexte) ;
2. sortie JSON stricte, validée champ par champ ; la réponse attendue doit être
   parsable par le moteur déterministe (grading) sinon l'exercice est rejeté ;
3. les exercices valides sont stockés en banque (generated_exercises) par
   couple compétence × niveau 1-5, et réutilisés ensuite sans nouvel appel ;
4. le niveau élève 1-10 est projeté sur les 5 niveaux de difficulté.
"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Competency, GeneratedExercise, LessonSnippet
from . import grading, providers

PROMPT_VERSION = "exgen-1"

LEVEL_DESCRIPTIONS = {
    1: "découverte : application directe d'une seule notion, nombres très simples",
    2: "consolidation : application directe, nombres un peu plus grands ou négatifs",
    3: "standard : niveau attendu du programme, une ou deux étapes",
    4: "approfondissement : plusieurs étapes, pièges classiques évités seulement par une bonne maîtrise",
    5: "défi : transfert, problème contextualisé ou raisonnement en plusieurs étapes",
}

_PROGRAM_CACHE: dict | None = None


def _program_data() -> dict:
    global _PROGRAM_CACHE
    if _PROGRAM_CACHE is None:
        path = Path(__file__).resolve().parents[1] / "data" / "competencies_fr.json"
        _PROGRAM_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _PROGRAM_CACHE


def _theme_objectives(grade: str, theme_name: str) -> list[str]:
    """Objectifs du même thème dans le programme officiel : contexte de ciblage."""
    for fw in _program_data()["frameworks"]:
        if fw["grade_level"] != grade:
            continue
        for dom in fw["domains"]:
            for th in dom["themes"]:
                if th["name"] == theme_name:
                    return [c["label"] for c in th["competencies"]]
    return []


def student_level_to_difficulty(level_1_10: int) -> int:
    """Niveau élève 1-10 -> niveau d'exercice 1-5."""
    return max(1, min(5, (max(1, min(10, level_1_10)) + 1) // 2))


# ------------------------------------------------------------------ validation

def _validate_exercise(raw: dict) -> dict | None:
    """Valide un exercice produit par le LLM. Retourne le contrat interne
    (statement, correction, response_type, expected, grading) ou None."""
    statement = str(raw.get("statement", "")).strip()
    if not 10 <= len(statement) <= 900:
        return None
    correction = str(raw.get("correction", "")).strip()
    rtype = raw.get("response_type", "short_text")
    answer = raw.get("answer") or {}
    atype = answer.get("type")

    if rtype in ("qcm_single", "qcm_multiple"):
        choices = [str(c) for c in (raw.get("choices") or [])]
        correct = answer.get("correct", [])
        if not (3 <= len(choices) <= 5) or not correct:
            return None
        if not all(isinstance(i, int) and 0 <= i < len(choices) for i in correct):
            return None
        expected = {"type": "choice", "correct": sorted(set(correct))}
        gpolicy = {"max_score": 1, "comparator": "qcm", "negative": 0, "choices": choices}
        return {"statement": statement, "correction": correction,
                "response_type": rtype, "expected": expected, "grading": gpolicy}

    if atype == "integer":
        try:
            expected = {"type": "integer", "value": int(answer["value"])}
        except (KeyError, TypeError, ValueError):
            return None
        gpolicy = {"max_score": 1, "comparator": "numeric", "tolerance": 0}
    elif atype in ("decimal", "number"):
        try:
            expected = {"type": "decimal", "value": float(str(answer["value"]).replace(",", "."))}
        except (KeyError, TypeError, ValueError):
            return None
        gpolicy = {"max_score": 1, "comparator": "numeric", "tolerance": 0}
    elif atype == "rational":
        v = answer.get("value")
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            return None
        try:
            expected = {"type": "rational", "value": [int(v[0]), int(v[1])]}
        except (TypeError, ValueError):
            return None
        if expected["value"][1] == 0:
            return None
        gpolicy = {"max_score": 2, "comparator": "rational_equiv"}
    elif atype == "expression":
        val = str(answer.get("value", "")).strip()
        if not val:
            return None
        expected = {"type": "expression", "value": val,
                    "variable": answer.get("variable", "x")}
        gpolicy = {"max_score": 2, "comparator": "symbolic_equiv"}
    else:
        return None

    # la réponse de référence doit être acceptée par le moteur déterministe
    ref_text = _reference_text(expected)
    verdict = grading.grade(expected, gpolicy, ref_text, 0.99)
    if verdict["score"] < gpolicy["max_score"]:
        return None
    return {"statement": statement, "correction": correction,
            "response_type": "short_text" if atype != "expression" else "short_text",
            "expected": expected, "grading": gpolicy}


def _reference_text(expected: dict) -> str:
    t = expected["type"]
    if t == "rational":
        return f"{expected['value'][0]}/{expected['value'][1]}"
    if t == "expression":
        return expected["value"]
    return str(expected["value"])


# ------------------------------------------------------------------- banque

def ensure_bank(db: Session, competency: Competency, level: int,
                min_variants: int | None = None) -> list[GeneratedExercise]:
    """Garantit min_variants exercices actifs pour (compétence, niveau).
    Génère via DeepSeek (v4-pro) et stocke ce qui manque."""
    level = max(1, min(5, level))
    min_variants = min_variants or settings.exercise_variants_per_level
    rows = (db.query(GeneratedExercise)
            .filter_by(competency_id=competency.id, difficulty_level=level,
                       status="active").all())
    missing = min_variants - len(rows)
    if missing <= 0:
        return rows

    fw_grade = _grade_of(db, competency)
    payload = {
        "grade": fw_grade,
        "competency_code": competency.code,
        "competency_label": competency.label,
        "theme": competency.theme_name,
        "domain": competency.domain_name,
        "program_objectives": _theme_objectives(fw_grade, competency.theme_name)[:12],
        "difficulty_level": level,
        "difficulty_description": LEVEL_DESCRIPTIONS[level],
        "count": missing,
        "allowed_response_types": ["short_text", "qcm_single"],
        "answer_types": ["integer", "decimal", "rational", "expression", "choice"],
    }
    system = (
        "Tu es un professeur de mathématiques de collège français. Tu crées des "
        "exercices ciblant EXACTEMENT la compétence du programme officiel fournie, "
        "au niveau de difficulté demandé (1-5). Chaque exercice doit avoir une "
        "réponse unique, vérifiable automatiquement. Réponds UNIQUEMENT en JSON : "
        '{"exercises":[{"statement":str,"correction":str,'
        '"response_type":"short_text"|"qcm_single","choices":[str]?,'
        '"answer":{"type":"integer"|"decimal"|"rational"|"expression"|"choice",'
        '"value":...,"variable":str?,"correct":[int]?}}]}. '
        "Énoncés en français, notation française (virgule décimale), sans LaTeX complexe. "
        "N'invente pas de compétence, ne sors pas du programme.")

    data = providers.deepseek_json(
        db, "exercise_generation", system, payload,
        max_tokens=1800, model=settings.deepseek_pro_model,
        correlation_id=f"exgen-{competency.code}-L{level}")

    next_variant = len(rows)
    added = []
    for raw in (data.get("exercises") or [])[:missing + 2]:
        valid = _validate_exercise(raw)
        if valid is None:
            continue
        # réponses de type choice viennent avec response_type qcm_single
        rt = raw.get("response_type", valid["response_type"])
        row = GeneratedExercise(
            competency_id=competency.id, difficulty_level=level,
            variant=next_variant, statement=valid["statement"],
            correction=valid["correction"],
            response_type=rt if rt in ("qcm_single", "qcm_multiple") else valid["response_type"],
            expected_json=valid["expected"], grading_json=valid["grading"],
            model=settings.deepseek_pro_model, prompt_version=PROMPT_VERSION)
        db.add(row)
        added.append(row)
        next_variant += 1
        if len(added) >= missing:
            break
    db.flush()
    if not rows and not added:
        raise ValueError(
            f"DeepSeek n'a produit aucun exercice valide pour {competency.code} niveau {level}")
    return rows + added


def pick_exercise(db: Session, competency: Competency, level: int,
                  seed: int) -> GeneratedExercise:
    """Choisit un exercice de banque, en générant si nécessaire ; si le niveau
    exact est vide et non générable, repli sur le niveau le plus proche."""
    for candidate in sorted(range(1, 6), key=lambda l: abs(l - level)):
        try:
            rows = ensure_bank(db, competency, candidate)
        except Exception:
            rows = (db.query(GeneratedExercise)
                    .filter_by(competency_id=competency.id,
                               difficulty_level=candidate, status="active").all())
        if rows:
            return rows[seed % len(rows)]
    raise ValueError(f"Aucun exercice disponible pour {competency.code}")


def _grade_of(db: Session, competency: Competency) -> str:
    from ..models import CompetencyFramework
    fw = db.get(CompetencyFramework, competency.framework_id)
    return fw.grade_level if fw else "5e"


# ---------------------------------------------------------- rappels de leçon

def ensure_lesson(db: Session, competency: Competency, level: int) -> LessonSnippet:
    """Rappel de leçon DeepSeek pour élève fragile, stocké et réutilisé.
    Un rappel par compétence × tranche de niveau (1-3 / 4-5)."""
    lo, hi = (1, 3) if level <= 3 else (4, 5)
    row = (db.query(LessonSnippet)
           .filter_by(competency_id=competency.id, level_min=lo, level_max=hi)
           .first())
    if row:
        return row
    data = providers.deepseek_json(
        db, "lesson_snippet",
        "Tu rédiges un très court rappel de leçon de mathématiques pour un élève "
        "de collège fragile, en français simple. JSON strict : "
        '{"title":str,"content":str(2-3 phrases),"example":str(1 exemple résolu court)}.',
        {"competency_code": competency.code, "competency_label": competency.label,
         "theme": competency.theme_name, "grade": _grade_of(db, competency),
         "level_range": f"{lo}-{hi}"},
        max_tokens=400, model=settings.deepseek_pro_model,
        correlation_id=f"lesson-{competency.code}")
    row = LessonSnippet(
        competency_id=competency.id, level_min=lo, level_max=hi,
        title=str(data.get("title", f"Rappel — {competency.label}"))[:180],
        content_latex=str(data.get("content", "")),
        example_latex=str(data.get("example", "")),
        version=PROMPT_VERSION, validated=False)
    db.add(row)
    db.flush()
    return row

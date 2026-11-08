"""Choix de la micro-correction imprimée sous une carte, sans aucun appel LLM.

Fonction pure des données déjà en base : elle est recalculée à chaque rendu
d'overlay, jamais stockée comme une note. Elle dit aussi quelles erreurs types la
réponse révèle, pour le journal de l'élève (services.error_journal).

Priorité du message : non-réponse, comptage (case ou lien en trop, manquant),
option choisie ou message retenu par le correcteur LLM, puis message générique.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import grading as grader
from . import construction, pdfgen, typography

BLANK_REASONS = ("blank", "qcm_blank", "matching_blank", "construction_blank")
# Un verdict du professeur peut s'accompagner d'une réponse enregistrée fictive
# (« Faux » écrit une colonne quelconque) : elle ne dit rien de l'erreur réelle.
_TEACHER_PREFIX = "teacher_"
_TEACHER_ANSWER = "teacher_answer"
COUNT_EXTRA_CHECK = "comptage.trop_de_cases"
COUNT_MISSING_CHECK = "comptage.cases_manquantes"
COUNT_EXTRA_LINK = "comptage.trop_de_liens"
COUNT_MISSING_LINK = "comptage.liens_manquants"
NO_ANSWER = "comptage.non_reponse"
FORM_MISMATCH_TEXT = "Ta réponse a la bonne valeur, mais pas la forme demandée."


@dataclass
class Selection:
    text: str = ""
    kind: str = ""              # blank | count | option | llm | generic | correct
    option_id: str | None = None
    # toutes les erreurs observées : (code, identifiant de l'option)
    errors: list[tuple[str, str | None]] = field(default_factory=list)
    comparable: bool = True     # False : réponse fictive, rien à journaliser


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many.format(n=count)


def extra_checks_text(count: int) -> str:
    return _plural(count, "Tu as coché une case de trop.", "Tu as coché {n} cases de trop.")


def missing_checks_text(count: int) -> str:
    return _plural(count, "Il manque une case.", "Il manque {n} cases.")


def extra_links_text(count: int) -> str:
    return _plural(count, "Tu as tracé un lien de trop.", "Tu as tracé {n} liens de trop.")


def missing_links_text(count: int) -> str:
    return _plural(count, "Il manque un lien.", "Il manque {n} liens.")


def _option(feedback: dict, option_id: str | None) -> dict:
    return (feedback.get("options") or {}).get(option_id or "") or {}


def _fits(text: str, lines: int) -> bool:
    final = typography.french(text)
    return (pdfgen.feedback_fits(final, lines)
            and pdfgen.feedback_fits(final, lines, dyslexic=True))


def _combine(first: str, second: str, lines: int) -> str:
    """Comptage suivi de l'explication, si les deux tiennent dans la bande."""
    joined = f"{first} {second}".strip() if second else first
    return joined if second and _fits(joined, lines) else first


def _wrong_answer(feedback: dict, raw: str, cell: tuple[int, int] | None = None) -> dict | None:
    """Réponse fausse prévue : le texte d'abord (6/8 n'est pas 3/4 quand la forme
    compte), puis la valeur."""
    written = grader.strip_answer_noise(grader.normalize(raw or ""))
    if not written:
        return None
    candidates = []
    for wrong in feedback.get("wrong_answers") or []:
        target = wrong.get("cell")
        if target is not None and (cell is None or tuple(target) != cell):
            continue
        candidates.append(wrong)
    for wrong in candidates:
        if grader.strip_answer_noise(grader.normalize(str(wrong.get("value")))) == written:
            return wrong
    got = grader.parse_number(written)
    if got is None:
        return None
    for wrong in candidates:
        value = grader.parse_number(grader.strip_answer_noise(grader.normalize(str(wrong.get("value")))))
        if value is not None and value == got:
            return wrong
    return None


def _select_qcm(item, feedback: dict, chosen: set[int], selection: Selection) -> None:
    policy = item.grading_json or {}
    ids = policy.get("option_ids") or []
    correct = set((item.expected_json or {}).get("correct") or [])
    lines = feedback.get("lines", 2)
    wrong_chosen = sorted(chosen - correct)
    for index in wrong_chosen:
        oid = ids[index] if index < len(ids) else None
        code = _option(feedback, oid).get("error")
        if code:
            selection.errors.append((code, oid))
    first = _option(feedback, ids[wrong_chosen[0]] if wrong_chosen and wrong_chosen[0] < len(ids) else None)
    if wrong_chosen:
        selection.option_id = ids[wrong_chosen[0]] if wrong_chosen[0] < len(ids) else None
    extra = len(chosen) - len(correct)
    if extra > 0 and not policy.get("exclusive"):
        selection.errors.append((COUNT_EXTRA_CHECK, None))
        selection.text, selection.kind = _combine(extra_checks_text(extra), first.get("text", ""), lines), "count"
    elif extra < 0 and not policy.get("exclusive"):
        selection.errors.append((COUNT_MISSING_CHECK, None))
        selection.text, selection.kind = _combine(missing_checks_text(-extra), first.get("text", ""), lines), "count"
    elif first.get("text"):
        selection.text, selection.kind = first["text"], "option"


def _select_grid(item, feedback: dict, chosen: list, selection: Selection) -> None:
    rows = (item.expected_json or {}).get("rows") or []
    ids = (item.grading_json or {}).get("option_ids") or []
    first_text = ""
    for index, row in enumerate(rows):
        got = chosen[index] if index < len(chosen) else -1
        if got == row.get("correct") or got in (-1, None):
            continue
        oid = ids[index] if index < len(ids) else None
        option = _option(feedback, oid)
        if option.get("error"):
            selection.errors.append((option["error"], oid))
        if not first_text and option.get("text"):
            first_text, selection.option_id = option["text"], oid
    missing = sum(1 for i in range(len(rows)) if (chosen[i] if i < len(chosen) else -1) in (-1, None))
    if missing:
        selection.errors.append((COUNT_MISSING_CHECK, None))
        selection.text = _combine(missing_checks_text(missing), first_text, feedback.get("lines", 2))
        selection.kind = "count"
    elif first_text:
        selection.text, selection.kind = first_text, "option"


def _select_matching(item, feedback: dict, pairs: list, selection: Selection) -> None:
    policy = item.grading_json or {}
    expected = {tuple(p) for p in (item.expected_json or {}).get("pairs") or []}
    got = {tuple(p) for p in pairs}
    ids = policy.get("right_ids") or []
    lines = feedback.get("lines", 2)
    first_text = ""
    for left, right in sorted(got - expected):
        oid = ids[right] if right < len(ids) else None
        option = _option(feedback, oid)
        if option.get("error"):
            selection.errors.append((option["error"], oid))
        if not first_text and option.get("text"):
            first_text, selection.option_id = option["text"], oid
    extra = len(got) - len(expected)
    if extra > 0:
        selection.errors.append((COUNT_EXTRA_LINK, None))
        selection.text, selection.kind = _combine(extra_links_text(extra), first_text, lines), "count"
    elif extra < 0:
        selection.errors.append((COUNT_MISSING_LINK, None))
        selection.text, selection.kind = _combine(missing_links_text(-extra), first_text, lines), "count"
    elif first_text:
        selection.text, selection.kind = first_text, "option"


def _select_written(item, feedback: dict, decision, response, reading, selection: Selection) -> None:
    policy = item.grading_json or {}
    raw = (reading.raw_json or {}) if reading else {}
    if policy.get("comparator") == "table_cells":
        credits = grader.table_credits(policy, raw.get("cells") or [])
        positions = [(r, c) for r, row in enumerate(policy.get("cells") or [])
                     for c, cell in enumerate(row) if not cell.get("given")]
        for index, text in enumerate(raw.get("cells") or []):
            if index >= len(positions) or (index < len(credits) and credits[index] == 1.0):
                continue
            wrong = _wrong_answer(feedback, str(text or ""), positions[index])
            if wrong:
                selection.errors.append((wrong.get("error"), None))
                if not selection.text:
                    selection.text, selection.kind = wrong.get("text", ""), "option"
        return
    wrong = _wrong_answer(feedback, str((response.final_text if response else "") or ""))
    if wrong:
        selection.errors.append((wrong.get("error"), None))
        selection.text, selection.kind = wrong.get("text", ""), "option"
    elif decision.reason_code == "form_mismatch":
        selection.text, selection.kind = FORM_MISMATCH_TEXT, "generic"


def _select_llm(feedback: dict, decision, selection: Selection) -> None:
    """Le correcteur a choisi un message prévu ou, à défaut, en a écrit un. Le
    texte libre est contrôlé ici : trop long, c'est le message générique."""
    lines = feedback.get("lines", 4)
    notes = ((decision.evidence_json or {}).get("llm") or [])
    for note in notes:
        option = _option(feedback, note.get("message_id"))
        if option.get("text"):
            if option.get("error"):
                selection.errors.append((option["error"], note.get("message_id")))
            if not selection.text:
                selection.text, selection.kind = option["text"], "option"
                selection.option_id = note.get("message_id")
            continue
        free = str(note.get("message_libre") or "").strip()
        if free:
            selection.errors.append(("autre", None))
            if not selection.text and _fits(free, lines):
                selection.text, selection.kind = free, "llm"


def select(item, decision, response=None, reading=None) -> Selection | None:
    """Micro-correction d'une réponse, ou None si l'exercice n'en porte pas."""
    policy = item.grading_json or {}
    feedback = policy.get("feedback")
    if not isinstance(feedback, dict):
        return None
    selection = Selection()
    reason = decision.reason_code or ""
    full = bool(decision.max_score) and decision.score >= decision.max_score
    level = max(1, min(3, round((item.difficulty or 3) / 3)))
    if reason.startswith(_TEACHER_PREFIX) and reason != _TEACHER_ANSWER:
        selection.comparable = False
        if full:
            selection.text, selection.kind = _correct_text(feedback, level), "correct"
        else:
            selection.text, selection.kind = (feedback.get("fallback") or {}).get("text", ""), "generic"
        return selection
    if full:
        selection.text, selection.kind = _correct_text(feedback, level), "correct"
        return selection
    if reason in BLANK_REASONS:
        selection.text, selection.kind = (feedback.get("blank") or {}).get("text", ""), "blank"
        selection.errors.append((NO_ANSWER, None))
        return selection
    rtype = item.response_type
    raw = (reading.raw_json or {}) if reading else {}
    if rtype.startswith("qcm"):
        _select_qcm(item, feedback, set((response.selected_choices if response else None) or []), selection)
    elif rtype == "checkbox_grid":
        chosen = raw.get("grid_selected")
        if chosen is None:
            chosen = (response.selected_choices if response else None) or []
        _select_grid(item, feedback, list(chosen), selection)
    elif rtype == "matching":
        pairs = raw.get("pairs")
        if pairs is None:
            pairs = (response.selected_pairs if response else None) or []
        _select_matching(item, feedback, list(pairs), selection)
    elif rtype == "manual_drawing" and isinstance(raw.get("construction"), dict):
        # tracé : l'erreur typique reconnue par la lecture donne son message ;
        # sinon, ce que les mesures disent (longueur fausse, élément manquant)
        error_id = raw["construction"].get("error_id")
        option = (feedback.get("options") or {}).get(error_id) if error_id else None
        if option and option.get("text"):
            selection.text, selection.kind, selection.option_id = option["text"], "option", error_id
            selection.errors.append((option.get("error"), error_id))
        else:
            measured = construction.feedback_text(raw["construction"])
            if measured and _fits(measured, int(feedback.get("lines") or 2)):
                selection.text, selection.kind = measured, "measure"
    elif (decision.evidence_json or {}).get("llm"):
        _select_llm(feedback, decision, selection)
    else:
        _select_written(item, feedback, decision, response, reading, selection)
    if not selection.text:
        selection.text, selection.kind = (feedback.get("fallback") or {}).get("text", ""), "generic"
    selection.errors = [(code, oid) for code, oid in selection.errors if code]
    return selection


def _correct_text(feedback: dict, level: int) -> str:
    """Sur une bonne réponse : moyens et difficiles seulement. Une réussite peut
    être chanceuse ; sur un facile, la coche suffit."""
    return (feedback.get("correct") or {}).get("text", "") if level >= 2 else ""


def error_targets(item) -> set[str]:
    """Erreurs que cette carte permet d'observer : le dénominateur du journal."""
    policy = item.grading_json or {}
    feedback = policy.get("feedback")
    if not isinstance(feedback, dict):
        return set()
    codes = {(node or {}).get("error") for node in (feedback.get("options") or {}).values()}
    codes |= {(node or {}).get("error") for node in feedback.get("wrong_answers") or []}
    return {code for code in codes if code and code != "autre"}


def group_text(texts: list[str], lines: int = pdfgen.FEEDBACK_MAX_LINES) -> str:
    """Messages des parties d'un composite, dans l'ordre, tant qu'ils tiennent.
    Ceux qui restent ne sont pas imprimés, mais vont au journal."""
    out = ""
    for text in texts:
        if not text:
            continue
        trial = f"{out} {text}".strip()
        if not _fits(trial, lines):
            break
        out = trial
    return out

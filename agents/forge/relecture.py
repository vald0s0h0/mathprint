"""Relecture pédagogique : un professeur exigeant, dans un contexte neuf, relit chaque
famille avant qu'elle parte en banque.

Le goldfish vérifie qu'un élève comprend la carte ; le relecteur vérifie qu'elle est
juste et qu'elle vaut la peine d'être faite. Il voit l'image de la carte principale
(première jumelle, première disposition), son corrigé et la section de la leçon.

Une carte bloquée est réécrite une fois avec la critique ; bloquée de nouveau, elle
est écartée : `persist` la laisse de côté, sans retour à l'humain."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import forge
import goldfish

HERE = Path(__file__).resolve().parent
PACK_SIZE = 3
MODEL = "opus"
CRITERIA = ("juste", "utile", "distracteurs", "niveau", "programme")
MAX_ATTEMPTS = 2
# Ce qui ne sert qu'à la mise en page ou au rendu : le relecteur voit déjà l'image.
_NOISE = {"figure", "figure_layout", "response_layout", "figure_constraints", "twin", "slot_swap",
          "leak_keys", "gives", "review_flags", "rework_summary", "dispositions", "ordre_impose"}


def state_path(run: Path) -> Path:
    return run / "relecture.json"


def load_state(run: Path) -> dict:
    path = state_path(run)
    return forge.load_json(path) if path.exists() else {"verdicts": {}, "usage": []}


def principal_keys(run: Path) -> list[str]:
    """Une carte par famille : celle dont le nom est l'emplacement seul."""
    return sorted(p.stem for p in (run / "cards").glob("*.png") if "." not in p.stem)


def _image_hash(run: Path, key: str) -> str:
    return hashlib.sha256((run / "cards" / f"{key}.png").read_bytes()).hexdigest()


def _clean(node):
    if isinstance(node, dict):
        return {k: _clean(v) for k, v in node.items() if k not in _NOISE}
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


def _error_codes(node, found: set) -> set:
    if isinstance(node, dict):
        code = node.get("error")
        if isinstance(code, str):
            found.add(code)
        for value in node.values():
            _error_codes(value, found)
    elif isinstance(node, list):
        for value in node:
            _error_codes(value, found)
    return found


def _taxonomy(subject: str) -> dict[str, str]:
    path = forge.REPO / "backend" / "app" / "data" / "misconceptions" / f"{subject}.json"
    if not path.exists():
        return {}
    data = forge.load_json(path)
    return {e["code"]: e.get("label") or "" for e in data.get("errors") or []}


def answer_key(exercise: dict, slot: dict, taxonomy: dict[str, str], lesson_facts: dict) -> str:
    """Le corrigé tel que le relecteur le lit."""
    cited = [f for f in exercise.get("faits") or [] if isinstance(f, str)]
    codes = sorted(_error_codes(exercise, set()))
    lines = [f"Niveau : {slot['difficulty']}. Archétype : {exercise.get('archetype') or slot.get('archetype')}. "
             f"Savoir-faire : {exercise.get('micro_skill')}.",
             "Faits cités : " + ("; ".join(f"{k} {lesson_facts[k]['text']}" for k in cited if k in lesson_facts)
                                 or "aucun"),
             "Codes d'erreur : " + ("; ".join(f"{c} = {taxonomy.get(c, '?')}" for c in codes) or "aucun"),
             "Corrigé (JSON) : " + json.dumps(_clean(exercise), ensure_ascii=False, sort_keys=True)]
    return "\n".join(lines)


def judge(verdict: dict) -> tuple[bool, list[str]]:
    """(bloquée, notes). Un critère juste ou programme en échec bloque toujours, même si
    le relecteur ne l'a pas dit."""
    notes = []
    for name in CRITERIA:
        entry = verdict.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("ok"), bool):
            raise goldfish.GoldfishError(f"relecture : critère {name} manquant")
        if not entry["ok"]:
            notes.append(f"{name} : {entry.get('note') or 'en échec'}")
    if not isinstance(verdict.get("bloquant"), bool):
        raise goldfish.GoldfishError("relecture : champ bloquant manquant")
    blocked = verdict["bloquant"] or any(
        not verdict[name]["ok"] for name in ("juste", "programme"))
    return blocked, notes


def run(run_dir: Path, *, slots: list[str] | None = None, cli: str = "claude",
        session_dir: Path | None = None, model: str | None = None) -> dict:
    """Relit les familles rendues qui n'ont pas de verdict pour leur image actuelle."""
    payload = forge.load_json(run_dir / "payload.json")
    grid = {s["slot"]: s for s in forge.load_json(run_dir / "grid.json")}
    exercises = forge.load_slots(run_dir)
    state = load_state(run_dir)
    context = payload["context"]
    lesson_text = Path(context.get("lecon") or context["fiche"]).read_text(encoding="utf-8")
    import lecon
    lesson_facts = lecon.facts(lesson_text)
    taxonomy = _taxonomy(payload.get("subject") or forge.SUBJECT_DEFAULT)
    keys = principal_keys(run_dir)
    todo = []
    for key in keys:
        entry = state["verdicts"].get(key) or {}
        if entry.get("ecartee"):
            continue
        if slots and key not in slots:
            continue
        if entry.get("image_sha256") != _image_hash(run_dir, key):
            todo.append(key)
    if not todo:
        return state
    system = (HERE / "prompts" / "relecteur.md").read_text(encoding="utf-8")
    model = model or MODEL
    read = ((lambda s, t, i: goldfish.session_ask(session_dir, s, t, i)) if session_dir
            else (lambda s, t, i: goldfish.ask(cli, s, t, i, model=model)))
    goldfish.USAGE.clear()
    pending = []
    for start in range(0, len(todo), PACK_SIZE):
        pack = todo[start:start + PACK_SIZE]
        images = [run_dir / "cards" / f"{k}.png" for k in pack]
        cards = "\n\n".join(f"## Carte {i + 1}\n{answer_key(exercises[k], grid[k], taxonomy, lesson_facts)}"
                            for i, k in enumerate(pack))
        text = (f"Classe : {payload['cycle']}. Les images sont dans l'ordre des cartes.\n\n"
                f"# Section de la leçon\n\n{lesson_text}\n\n# Cartes\n\n{cards}\n\n"
                f"Réponds par {{\"cartes\": [...]}}, un objet par carte, dans l'ordre.")
        try:
            answer = read(system, text, images)
        except goldfish.SessionPending:
            pending.extend(pack)
            continue
        verdicts = answer.get("cartes") or []
        if len(verdicts) != len(pack):
            raise goldfish.GoldfishError(f"relecture : {len(verdicts)} verdict(s) pour {len(pack)} carte(s)")
        for key, verdict in zip(pack, verdicts):
            blocked, notes = judge(verdict)
            previous = state["verdicts"].get(key) or {}
            attempts = int(previous.get("attempts") or 0) + 1
            state["verdicts"][key] = {
                "image_sha256": _image_hash(run_dir, key), "attempts": attempts,
                "blocked": blocked, "ecartee": blocked and attempts >= MAX_ATTEMPTS,
                "notes": notes, "critique": verdict.get("critique") or "",
                "remarques": verdict.get("remarques") or "", "raw": verdict,
                "reader": "session" if session_dir else model,
                "history": [*(previous.get("history") or []),
                            {"attempt": attempts, "blocked": blocked, "critique": verdict.get("critique") or ""}]}
        state.setdefault("usage", []).extend(goldfish.USAGE)
        goldfish.USAGE.clear()
        forge.dump_json(state_path(run_dir), state)
    state["pending"] = pending
    return state


def ecartees(run: Path) -> set[str]:
    return {k for k, v in load_state(run)["verdicts"].items() if v.get("ecartee")}


def problems(run: Path) -> list[str]:
    """Ce qui empêche `persist` : une carte sans relecture de son image actuelle, ou
    bloquée et pas encore réécrite."""
    state = load_state(run)
    out = []
    for key in principal_keys(run):
        entry = state["verdicts"].get(key)
        if entry and entry.get("ecartee"):
            continue
        if not entry or entry.get("image_sha256") != _image_hash(run, key):
            out.append(f"{key} : pas de relecture pédagogique de la carte actuelle (`relecture`)")
        elif entry.get("blocked"):
            out.append(f"{key} : bloquée par la relecture, à réécrire une fois : {entry.get('critique')}")
    return out

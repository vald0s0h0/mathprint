"""Goldfish : un lecteur sans contexte qui ne voit que l'image de la carte.

Le protocole reprend celui du modèle Elephant-Goldfish de Google : contexte
vierge, paraphrase d'abord, lecture seule, un seul re-contrôle. Sa règle se
transpose directement : une paraphrase fausse est un défaut.

L'isolement est physique, pas seulement demandé : l'appel CLI se fait sans aucun
outil, depuis un dossier vide, et l'image voyage dans le message. Le lecteur ne
peut donc ni ouvrir le corrigé ni lire le dépôt.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import forge

HERE = Path(__file__).resolve().parent
PACK_SIZE = 5
TIMEOUT_S = 300
MODEL_BY_CLI = {"claude": "sonnet", "codex": "gpt-6.1-terra"}
LEVELS = {"facile": 1, "moyen": 2, "difficile": 3}
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class GoldfishError(RuntimeError):
    """Le lecteur n'a pas pu répondre : à relancer, jamais à compter comme un succès."""


def _version(path: str) -> tuple[int, ...]:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in match.groups()) if match else ()


def claude_binary() -> str | None:
    """Le `claude` le plus récent : celui du terminal peut être ancien, et une
    ancienne version rend une sortie vide en mode image."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    candidates = [shutil.which("claude")]
    candidates += sorted(str(p) for p in Path.home().glob(
        ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    found = [(v, c) for c in candidates if c and (v := _version(c))]
    return max(found)[1] if found else None


def choose_cli(author_model: str) -> str:
    """L'autre fournisseur si possible : deux modèles n'ont pas les mêmes angles
    morts. Sinon celui qui est installé."""
    has_codex = shutil.which("codex") is not None
    has_claude = claude_binary() is not None
    prefers_codex = "claude" in (author_model or "").lower() or "opus" in (author_model or "").lower()
    if prefers_codex and has_codex:
        return "codex"
    if has_claude:
        return "claude"
    if has_codex:
        return "codex"
    raise GoldfishError("Ni `claude` ni `codex` n'est installé : goldfish impossible.")


def _clean_env() -> dict:
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    return env


def _parse(text: str) -> dict:
    match = _JSON_RE.search(text or "")
    if not match:
        raise GoldfishError(f"réponse sans JSON : {(text or '')[:200]!r}")
    # Le modèle ajoute parfois du texte ou un second objet après sa réponse : seul le
    # premier objet complet compte.
    try:
        return json.JSONDecoder().raw_decode(text[match.start():])[0]
    except json.JSONDecodeError as exc:
        raise GoldfishError(f"JSON illisible : {exc}") from exc


def _ask_claude(system: str, text: str, images: list[Path]) -> dict:
    binary = claude_binary()
    if not binary:
        raise GoldfishError("`claude` introuvable.")
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(p.read_bytes()).decode()}}
               for p in images]
    content.append({"type": "text", "text": text})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    cmd = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--model", MODEL_BY_CLI["claude"], "--system-prompt", system, "--tools", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run(cmd, input=json.dumps(message) + "\n", capture_output=True, text=True,
                              env=_clean_env(), cwd=empty, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise GoldfishError(f"claude code {proc.returncode} : {(proc.stderr or '')[-400:]}")
    for line in reversed(proc.stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            if event.get("is_error"):
                raise GoldfishError(f"claude : {str(event.get('result'))[:400]}")
            return _parse(str(event.get("result") or ""))
    raise GoldfishError("claude n'a rendu aucun résultat (version trop ancienne ?)")


def _ask_codex(system: str, text: str, images: list[Path]) -> dict:
    """Non testé sur cette machine tant que `codex` n'y est pas installé."""
    with tempfile.TemporaryDirectory() as empty:
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-C", empty,
               "--model", MODEL_BY_CLI["codex"]]
        for image in images:
            cmd += ["--image", str(image)]
        cmd.append(f"{system}\n\n{text}")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=_clean_env(), timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise GoldfishError(f"codex code {proc.returncode} : {(proc.stderr or '')[-400:]}")
    return _parse(proc.stdout)


def ask(cli: str, system: str, text: str, images: list[Path]) -> dict:
    return (_ask_claude if cli == "claude" else _ask_codex)(system, text, images)


def _prompt(name: str) -> str:
    return (HERE / "goldfish_prompts" / f"{name}.md").read_text(encoding="utf-8")


def _correct_answers(leaf: dict) -> list[str]:
    rtype = leaf.get("response_type")
    if rtype in ("qcm_single", "qcm_multiple"):
        return [str(c.get("text") or "") for c in leaf.get("choices") or []
                if isinstance(c, dict) and c.get("correct")]
    if rtype == "short_text":
        value = (leaf.get("answer") or {}).get("value")
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return [f"{value[0]}/{value[1]}"]
        return [str(value)] if value is not None else []
    return []


_NUMBER_RE = re.compile(r"-?\s?\d+(?:,\d+)?(?:/\d+)?")


def answer_matches(reader: str, expected: list[str]) -> bool | None:
    """La réponse du lecteur correspond-elle au corrigé ? None = non comparable
    par script (relier, tableau, rédaction) : son avis reste dans le rapport."""
    from app.services import mathrender
    if not expected:
        return None
    # Le lecteur écrit « x² » et « − » là où le corrigé a « x^2 » et « - » : même réponse.
    reader = str(reader or "").replace("\u2212", "-")
    given = [p for p in re.split(r"\s*(?:;|\bet\b)\s*", reader) if p.strip()]
    if not given:
        return False

    def flat(text: str) -> str:
        folded = forge.fold(mathrender.strip_math(text)).replace("\u2212", "-").replace("^", "")
        return re.sub(r"\s+", "", folded).rstrip(".")

    def same(a: str, b: str) -> bool:
        if flat(a) == flat(b):
            return True
        left, right = forge._value_of(a), forge._value_of(b)
        return left is not None and right is not None and forge._same_value(left, right)

    if len(expected) == 1:
        if any(same(g, expected[0]) for g in given) or same(reader, expected[0]):
            return True
        if forge._value_of(expected[0]) is None:
            return False
        # « 8/3 d'une tablette (soit 2 + 2/3) » : la valeur vient d'abord, le lecteur commente.
        # « donc x = 11. Inaya a choisi 11. » : le lecteur raisonne, puis il conclut.
        words = reader.split()
        numbers = _NUMBER_RE.findall(reader)
        return ((bool(words) and same(words[0].rstrip(",;:."), expected[0]))
                or (bool(numbers) and same(numbers[-1].replace(" ", ""), expected[0])))
    return all(any(same(g, e) for g in given) for e in expected)


def judge_student(verdict: dict, leaf: dict, level: int) -> tuple[str | None, list[str]]:
    """Compare ce qu'a compris le lecteur à ce que l'auteur voulait."""
    notes: list[str] = []
    severity = None
    if verdict.get("comprehensible") is False:
        severity = "blocker"
        notes.append("incompréhensible pour un lecteur sans contexte")
    for key, label in (("donnees_manquantes", "donnée manquante"),
                       ("seconde_lecture", "seconde interprétation valable")):
        if verdict.get(key):
            severity = "blocker"
            notes.append(f"{label} : {verdict[key]}")
    if verdict.get("ambiguites"):
        severity = severity or "major"
        notes.append(f"ambiguïté : {verdict['ambiguites']}")
    if verdict.get("guide_donne_reponse"):
        severity = "blocker"
        notes.append("le guide donne la réponse ou désigne la bonne proposition")
    if verdict.get("guide_inutile"):
        severity = severity or "major"
        notes.append(f"guide peu utile : {verdict['guide_inutile']}")
    match = answer_matches(verdict.get("reponse"), _correct_answers(leaf))
    if match is False:
        severity = "blocker"
        notes.append(f"réponse du lecteur « {verdict.get('reponse')} » différente du corrigé")
    guessed = LEVELS.get(forge.fold(str(verdict.get("difficulte") or "")))
    if guessed and abs(guessed - level) >= 2:
        severity = severity or "major"
        notes.append(f"difficulté perçue « {verdict['difficulte']} », annoncée niveau {level}")
    return severity, notes


def _correction_targets(exercise: dict, level: int, rng: random.Random) -> list[dict]:
    """Messages à faire relire : tous les pièges (au plus 2) sur un difficile, un
    seul tiré au hasard ailleurs."""
    leaf = forge._leaves(exercise)[0]
    options = []
    for key in ("choices", "right", "expected_errors", "wrong_answers"):
        for item in leaf.get(key) or []:
            if isinstance(item, dict) and item.get("feedback") and not item.get("correct"):
                label = item.get("text") or item.get("value") or item.get("id")
                # Au relier, un message vaut pour un lien faux vers ce point, même utile.
                options.append({"reponse": f"un mauvais lien vers « {label} »" if key == "right" else label,
                                "message": item["feedback"]})
    if not options:
        return []
    rng.shuffle(options)
    return options[:2 if level == 3 else 1]


def run(run_dir: Path, *, slots: list[str] | None = None, cli: str | None = None) -> dict:
    """Relit les cartes valides et rendues. `slots` : re-contrôle ciblé après
    correction d'une carte, lue alors seule. Une carte déjà re-contrôlée qui
    échoue encore doit être réécrite depuis zéro."""
    payload = forge.load_json(run_dir / "payload.json")
    grid = {s["slot"]: s for s in forge.load_json(run_dir / "grid.json")}
    exercises = forge.load_slots(run_dir)
    state_path = run_dir / "goldfish.json"
    state = forge.load_json(state_path) if state_path.exists() else {"verdicts": {}, "attempts": {}}
    cli = cli or choose_cli(payload.get("model", ""))
    rendered = sorted(p.stem for p in (run_dir / "cards").glob("*.png"))
    todo = [k for k in rendered if (k in slots if slots else k not in state["verdicts"])]
    if not todo:
        return state
    rng = random.Random(f"{run_dir.name}/{len(state['attempts'])}")
    rng.shuffle(todo)
    size = 1 if slots else PACK_SIZE
    system_student = _prompt("eleve")
    system_reader = _prompt("correction")
    for start in range(0, len(todo), size):
        pack = todo[start:start + size]
        images = [run_dir / "cards" / f"{k}.png" for k in pack]
        labels = [f"carte {i + 1}" for i in range(len(pack))]
        text = (f"Niveau de la classe : {payload['cycle']}.\nVoici {len(pack)} carte(s), dans l'ordre : "
                f"{', '.join(labels)}. Réponds par {{\"cartes\": [...]}} avec un objet par carte.")
        answer = ask(cli, system_student, text, images)
        verdicts = answer.get("cartes") or []
        if len(verdicts) != len(pack):
            raise GoldfishError(f"{len(verdicts)} verdict(s) pour {len(pack)} carte(s)")
        for key, verdict in zip(pack, verdicts):
            attempts = state["attempts"].get(key, 0) + 1
            state["attempts"][key] = attempts
            exercise = exercises[key]
            level = grid[key]["level"]
            leaves = forge._leaves(exercise)
            severity, notes = judge_student(verdict, leaves[0], level)
            state["verdicts"][key] = {
                "reader": cli, "severity": severity, "flag": bool(severity), "notes": notes,
                "paraphrase": verdict.get("paraphrase"), "notion": verdict.get("notion"),
                "reponse": verdict.get("reponse"), "attempts": attempts,
                "rewrite": bool(severity == "blocker" and attempts >= 2)}
        forge.dump_json(state_path, state)
        # Un seul appel par paquet aussi pour les messages : à plusieurs milliers de
        # cartes, un appel par carte consommerait vite le quota.
        targets = [(key, target) for key in pack
                   for target in _correction_targets(exercises[key], grid[key]["level"], rng)]
        if targets:
            order = {key: i + 1 for i, key in enumerate(pack)}
            listing = "\n".join(
                f"{i + 1}. Carte {order[key]}. Réponse de l'élève : {t['reponse']}\n   Message : {t['message']}"
                for i, (key, t) in enumerate(targets))
            answer = ask(cli, system_reader, f"Niveau : {payload['cycle']}.\n{listing}\n"
                         "Réponds par {\"messages\": [...]} avec un objet par message.", images)
            for (key, target), verdict in zip(targets, answer.get("messages") or []):
                if verdict.get("comprend") is False or verdict.get("aide") is False:
                    entry = state["verdicts"][key]
                    entry["flag"] = True
                    entry["severity"] = entry["severity"] or "major"
                    entry["notes"].append(f"micro-correction « {target['message'][:70]} » : "
                                          f"{verdict.get('probleme') or 'peu claire'}")
            forge.dump_json(state_path, state)
    return state

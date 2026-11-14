"""Appel d'un LLM en ligne de commande, sur abonnement (plan forge2 §10.2).

Sert la commande `vague` (scripts/vague.py) : Haiku par `claude -p`, Luna ou
Terra par `codex exec`. Même protocole que le lecteur Goldfish
(agents/forge/goldfish.py), sans image : aucun outil, aucun serveur MCP, un
dossier vide comme répertoire de travail, et les clés d'API retirées de
l'environnement pour que l'appel passe bien par l'abonnement.

Les identifiants de modèles viennent de la configuration (vague_model_*), jamais
d'ici.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import settings

ENGINES = ("haiku", "luna", "terra")
_JSON_RE = re.compile(r"[\{\[]", re.DOTALL)
# fenêtre d'usage épuisée : la vague s'arrête au lieu d'échouer paquet par paquet
_LIMIT_RE = re.compile(r"usage limit|limit reached|hit your (usage )?limit|rate.?limit|quota", re.I)


class CliError(RuntimeError):
    """Le moteur n'a pas rendu de réponse exploitable. `stop` : inutile de
    continuer la vague (moteur absent, fenêtre d'usage épuisée)."""

    def __init__(self, message: str, *, stop: bool = False):
        super().__init__(message)
        self.stop = stop


def engine_cli(engine: str) -> tuple[str, str]:
    """(cli, modèle) d'un moteur de la commande."""
    if engine not in ENGINES:
        raise CliError(f"moteur inconnu : {engine} (haiku, luna ou terra)", stop=True)
    model = getattr(settings, f"vague_model_{engine}") or ""
    if not model:
        raise CliError(f"identifiant du modèle {engine} non réglé "
                       f"(MATHPRINT_VAGUE_MODEL_{engine.upper()})", stop=True)
    return ("claude" if engine == "haiku" else "codex"), model


def _version(path: str) -> tuple[int, ...]:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in match.groups()) if match else ()


def claude_binary() -> str | None:
    """Le `claude` le plus récent (celui du terminal peut être ancien)."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    candidates = [shutil.which("claude")]
    candidates += sorted(str(p) for p in Path.home().glob(
        ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    found = [(v, c) for c in candidates if c and (v := _version(c))]
    return max(found)[1] if found else None


def codex_binary() -> str | None:
    return os.environ.get("CODEX_BIN") or shutil.which("codex")


def clean_env() -> dict:
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        env.pop(name, None)
    return env


def parse(text: str):
    """Premier objet (ou tableau) JSON complet de la sortie : le modèle ajoute
    parfois une phrase ou une clôture de code autour."""
    match = _JSON_RE.search(text or "")
    if not match:
        raise CliError(f"réponse sans JSON : {(text or '')[:200]!r}")
    try:
        return json.JSONDecoder().raw_decode(text[match.start():])[0]
    except json.JSONDecodeError as exc:
        raise CliError(f"JSON illisible : {exc}") from exc


def _run(cmd: list[str], stdin: str, cwd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              env=clean_env(), cwd=cwd, timeout=settings.vague_timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"délai dépassé ({settings.vague_timeout_s} s)") from exc
    except OSError as exc:
        raise CliError(f"lancement impossible : {exc}", stop=True) from exc


def _failed(name: str, proc: subprocess.CompletedProcess) -> CliError:
    tail = ((proc.stderr or "") + (proc.stdout or ""))[-400:]
    return CliError(f"{name} code {proc.returncode} : {tail}", stop=bool(_LIMIT_RE.search(tail)))


def _ask_claude(model: str, system: str, text: str):
    binary = claude_binary()
    if not binary:
        raise CliError("`claude` introuvable.", stop=True)
    cmd = [binary, "-p", "--output-format", "json", "--model", model,
           "--system-prompt", system, "--tools", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as empty:
        proc = _run(cmd, text, empty)
    if proc.returncode != 0:
        raise _failed("claude", proc)
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = str(event.get("result") or "")
            if event.get("is_error"):
                raise CliError(f"claude : {result[:400]}", stop=bool(_LIMIT_RE.search(result)))
            return parse(result)
    raise CliError("claude n'a rendu aucun résultat (version trop ancienne ?)")


def _ask_codex(model: str, system: str, text: str):
    binary = codex_binary()
    if not binary:
        raise CliError("`codex` introuvable.", stop=True)
    with tempfile.TemporaryDirectory() as empty:
        cmd = [binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
               "-C", empty, "--model", model, "--", "-"]
        proc = _run(cmd, f"{system}\n\n{text}", empty)
    if proc.returncode != 0:
        raise _failed("codex", proc)
    return parse(proc.stdout)


def ask(engine: str, system: str, text: str):
    """Un appel neuf et court, sans outil. Rend le JSON reçu (non validé)."""
    cli, model = engine_cli(engine)
    return (_ask_claude if cli == "claude" else _ask_codex)(model, system, text)

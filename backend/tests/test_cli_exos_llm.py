"""Tests du provider CLI de la pipeline `agents/cli-exos` (llm_cli).

On ne peut pas invoquer le vrai binaire `claude` en test : on le remplace par un
FAUX exécutable (script Python) piloté par des variables d'environnement, et on
vérifie (1) le parsing de l'enveloppe `--output-format json`, (2) la tolérance au
texte autour du JSON, (3) la remontée d'erreur (is_error / code non nul), et
surtout (4) le garde-fou « jamais l'API » : `ANTHROPIC_API_KEY` est bien RETIRÉE
de l'environnement passé au binaire.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# le module llm_cli vit dans agents/cli-exos/ (stdlib pur, pas d'import backend)
_CLI_EXOS = Path(__file__).resolve().parents[2] / "agents" / "cli-exos"
sys.path.insert(0, str(_CLI_EXOS))
import llm_cli  # noqa: E402


_FAKE = """#!/usr/bin/env python3
import os, sys, json
data = sys.stdin.read()
dump = os.environ.get("FAKE_ENV_DUMP")
if dump:
    with open(dump, "w") as f:
        json.dump({"ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
                   "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                   "stdin_len": len(data)}, f)
if os.environ.get("FAKE_MODE") == "rc1":
    sys.stderr.write("boom"); sys.exit(1)
result = os.environ.get("FAKE_RESULT", '{"exercises": []}')
is_error = os.environ.get("FAKE_IS_ERROR") == "1"
print(json.dumps({"type": "result", "is_error": is_error, "result": result,
                  "total_cost_usd": 0.01, "duration_ms": 3}))
"""


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    path = tmp_path / "fake_claude"
    path.write_text(_FAKE)
    path.chmod(0o755)
    monkeypatch.setattr(llm_cli, "CLAUDE_BIN", str(path))
    # env de contrôle du faux binaire
    for k in ("FAKE_RESULT", "FAKE_IS_ERROR", "FAKE_MODE", "FAKE_ENV_DUMP"):
        monkeypatch.delenv(k, raising=False)
    return path


def test_is_available(fake_claude):
    assert llm_cli.is_available() is True


def test_parses_clean_json(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"exercises": [{"source_number": "34"}]}')
    out = llm_cli.claude_cli_json("sys", {"x": 1}, model="sonnet")
    assert out == {"exercises": [{"source_number": "34"}]}


def test_tolerates_text_around_json(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", 'Voici le résultat :\n{"a": 1, "b": 2}\nvoilà.')
    assert llm_cli.claude_cli_json("sys", {}, model="sonnet") == {"a": 1, "b": 2}


def test_raises_on_is_error(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_IS_ERROR", "1")
    monkeypatch.setenv("FAKE_RESULT", "quota dépassé")
    with pytest.raises(llm_cli.ClaudeCliError):
        llm_cli.claude_cli_json("sys", {}, model="opus")


def test_raises_on_nonzero_exit(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "rc1")
    with pytest.raises(llm_cli.ClaudeCliError):
        llm_cli.claude_cli_json("sys", {}, model="sonnet")


def test_raises_when_no_json(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", "pas du tout du json")
    with pytest.raises(llm_cli.ClaudeCliError):
        llm_cli.claude_cli_json("sys", {}, model="sonnet")


def test_api_key_stripped_from_child_env(fake_claude, tmp_path, monkeypatch):
    """Cœur du garde-fou « jamais l'API » : la clé présente dans NOTRE environnement
    ne doit PAS être transmise au binaire `claude` (sinon il facturerait l'API)."""
    dump = tmp_path / "env.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-not-leak")
    monkeypatch.setenv("FAKE_ENV_DUMP", str(dump))
    monkeypatch.setenv("FAKE_RESULT", "{}")
    llm_cli.claude_cli_json("sys", {"p": True}, model="sonnet")
    seen = json.loads(dump.read_text())
    assert seen["ANTHROPIC_API_KEY"] is None
    assert seen["ANTHROPIC_AUTH_TOKEN"] is None
    assert seen["stdin_len"] > 0                      # le prompt a bien été passé en stdin
    # notre propre process garde sa variable (on n'a nettoyé que l'env ENFANT)
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-should-not-leak"


def test_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(llm_cli, "CLAUDE_BIN", "/nonexistent/claude-xyz")
    with pytest.raises(llm_cli.ClaudeCliError):
        llm_cli.claude_cli_json("sys", {}, model="sonnet")

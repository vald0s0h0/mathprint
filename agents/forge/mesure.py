"""Mesure de ce qu'un run consomme : les lecteurs (goldfish, relecture, vérificateur)
notent leur usage à chaque appel ; le rédacteur se mesure dans les transcriptions des
sous-agents Claude Code qui citent le run.

Une transcription répète l'usage d'un message sur chacun de ses blocs : on le compte
une fois par identifiant de message."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1].parent
FIELDS = ("input", "cache_read", "cache_write", "output")


def total(usage: list[dict]) -> dict:
    out = {name: sum(int(u.get(name) or 0) for u in usage) for name in FIELDS}
    out["calls"] = len(usage)
    return out


def transcripts_dir() -> Path:
    return Path.home() / ".claude" / "projects" / str(REPO).replace("/", "-")


def author_usage(run_name: str, folder: Path | None = None) -> dict:
    """Usage des sous-agents dont la transcription cite le run."""
    folder = folder or transcripts_dir()
    usage: list[dict] = []
    files = 0
    for path in sorted(folder.glob("*/subagents/*.jsonl")) if folder.exists() else []:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if run_name not in text:
            continue
        files += 1
        seen: set[str] = set()
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            if not isinstance(message, dict) or not message.get("usage"):
                continue
            key = message.get("id") or event.get("requestId") or event.get("uuid")
            if key in seen:
                continue
            seen.add(key)
            raw = message["usage"]
            usage.append({"model": message.get("model"), "input": raw.get("input_tokens") or 0,
                          "cache_read": raw.get("cache_read_input_tokens") or 0,
                          "cache_write": raw.get("cache_creation_input_tokens") or 0,
                          "output": raw.get("output_tokens") or 0})
    return {"files": files, "usage": usage, "calls": len(usage)}

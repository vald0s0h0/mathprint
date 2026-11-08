"""Historique admin durable, distinct du contenu imprimé et des avis courants."""
from datetime import datetime, timezone
import hashlib
import json


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def snapshot(ex) -> dict:
    raw = ex.raw_ocr_json or {}
    return {"id": ex.id, "ref": ex.source_number, "status": ex.status,
            "updated_at": ex.updated_at.isoformat() if ex.updated_at else None,
            "figure_path": ex.figure_path, "figure_spec": raw.get("figure_spec"),
            "statement": ex.statement, "expected": ex.expected_json,
            "grading": ex.grading_json, "comment": raw.get("review_comment", ""),
            "verdict": raw.get("review_verdict"), "notes": raw.get("review_notes", []),
            "goldfish": (raw.get("quality") or {}).get("goldfish")}


def event(status: str, summary: str, *, actor: str = "agent", **details) -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(), "status": status,
            "actor": actor, "summary": summary, **details}


def append(ex, item: dict) -> None:
    raw = dict(ex.raw_ocr_json or {})
    raw["review_history"] = [*raw.get("review_history", []), item]
    ex.raw_ocr_json = raw


def requires_intervention(ex) -> bool:
    raw = ex.raw_ocr_json or {}
    if raw.get("review_verdict") == "like":
        return False
    if raw.get("review_verdict") == "dislike":
        return True
    history = raw.get("review_history") or []
    latest = next((e for e in reversed(history) if e.get("status") in ("ok", "failed")), None)
    goldfish = (raw.get("quality") or {}).get("goldfish") or {}
    return bool(raw.get("review_notes") or goldfish.get("flag") or goldfish.get("notes")
                or raw.get("review_blocking") or (latest and latest["status"] == "failed"))

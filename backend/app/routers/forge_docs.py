"""Page admin « Forge » : lecture seule des consignes et des contextes de la forge.

Les fichiers servis sont une liste fermée de dossiers : consignes, contextes et prompts
de `agents/forge/`, taxonomies d'erreurs, programmes officiels, leçons écrites par
`forge lecon`. Sur le NAS, ces dossiers n'existent pas : la page le dit."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..deps import require_role

router = APIRouter(prefix="/api/admin/forge", tags=["forge"], dependencies=[Depends(require_role("admin"))])

_REPO = Path(__file__).resolve().parents[3]
_FACT = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*|__)?\s*F\d+\b", re.MULTILINE)


def _roots() -> dict[str, tuple[Path, tuple[str, ...]]]:
    """Préfixe → (dossier, extensions servies)."""
    return {
        "forge": (_REPO / "agents" / "forge", (".md", ".json")),
        "erreurs": (_REPO / "backend" / "app" / "data" / "misconceptions", (".json",)),
        "programmes": (_REPO / "docs" / "programmes", (".md",)),
        "lecons": (Path(settings.data_dir) / "forge" / "lecons", (".md", ".json", ".yaml")),
    }


def resolve(path: str) -> Path:
    """`forge/contexte/exercice.md` → fichier réel, ou 404 s'il sort de la liste fermée."""
    prefix, _, rest = (path or "").partition("/")
    roots = _roots()
    if prefix not in roots or not rest or ".." in Path(rest).parts:
        raise HTTPException(404, "Document inconnu")
    base, suffixes = roots[prefix]
    target = (base / rest).resolve()
    if base.resolve() not in target.parents or target.suffix.lower() not in suffixes:
        raise HTTPException(404, "Document inconnu")
    if prefix == "forge" and not (target.name.startswith("CONSIGNE-")
                                  or {"contexte", "prompts"} & set(target.relative_to(base.resolve()).parts)):
        raise HTTPException(404, "Document inconnu")
    return target


def _modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


@router.get("/file")
def read_file(path: str):
    target = resolve(path)
    if not target.is_file():
        raise HTTPException(404, "Ce fichier n'existe pas sur cette machine (la forge tourne sur le Mac).")
    text = target.read_text(encoding="utf-8", errors="replace")
    kind = {".md": "markdown", ".json": "json", ".yaml": "yaml"}[target.suffix.lower()]
    return {"path": path, "name": target.name, "kind": kind, "content": text,
            "size": len(text.encode("utf-8")), "modified": _modified(target)}


@router.get("/lecons")
def lessons():
    """Les leçons écrites, par matière, avec leur nombre de faits : une leçon sans fait
    est à l'ancien format et la forge la refuse."""
    base = Path(settings.data_dir) / "forge" / "lecons"
    out = []
    for path in sorted(base.glob("*/*.md")) if base.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        verif = path.with_suffix(".verif.json")
        out.append({"path": f"lecons/{path.parent.name}/{path.name}", "subject": path.parent.name,
                    "name": path.stem, "size": len(text.encode("utf-8")), "facts": len(_FACT.findall(text)),
                    "verified": verif.is_file(), "modified": _modified(path)})
    return {"available": base.is_dir(), "lessons": out}


@router.get("/status")
def status():
    """La forge est-elle présente sur cette machine ?"""
    return {"available": (_REPO / "agents" / "forge" / "CONSIGNE-EXERCICES.md").is_file()}

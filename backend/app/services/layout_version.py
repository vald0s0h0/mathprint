"""Version de la MISE EN PAGE des cartes (hauteurs et aperçus PNG en cache).

Empreinte du contenu des fichiers qui décident de l'aspect et des mesures d'une
carte (moteur PDF, blocs, formules, figures, polices). Un déploiement qui ne
touche pas à la présentation garde donc tout le cache ; dès qu'un de ces
fichiers change, les caches de l'ancienne version sont ignorés puis supprimés.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent
LAYOUT_FILES = (
    "pdfgen.py", "card_preview.py", "blocks.py", "construction.py", "generation.py",
    "handwriting.py", "mathrender.py", "typography.py", "statement.py", "sheet_engine.py",
    "answer_mix.py", "draw_rules.py", "figkit.py", "figures.py", "geofig.py", "svgfig.py",
    "chartfig.py", "figtemplate.py",
)


@lru_cache(maxsize=1)
def layout_version() -> str:
    h = hashlib.sha256()
    for name in LAYOUT_FILES:
        p = _SERVICES / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.exists() else b"-")
    fonts = _SERVICES.parent / "assets" / "fonts"
    if fonts.is_dir():
        for f in sorted(fonts.iterdir()):
            h.update(f"{f.name}:{f.stat().st_size}".encode())
    return h.hexdigest()[:12]

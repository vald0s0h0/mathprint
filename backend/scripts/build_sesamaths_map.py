#!/usr/bin/env python3
"""Génère la « maquette textuelle » du manuel Sésamath 5e et la gèle dans le
code (app/data/sesamaths_5e_map.json).

La carte = chapitres -> Séries -> plages de pages fichier (0-indexées), déduites
de la table des matières du PDF. Elle accélère la localisation des pages
d'exercices sans re-parser le PDF à chaque appel ; la pipeline la charge en
priorité (repli sur parse_toc live si absente/périmée, le 5.pdf étant toujours
livré avec le code).

Usage : .venv/bin/python scripts/build_sesamaths_map.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402

from app.services import sesamaths_pdf  # noqa: E402

PDF = Path(__file__).resolve().parents[2] / "context" / "5.pdf"
OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "sesamaths_5e_map.json"


def build(grade: str = "5e") -> dict:
    doc = fitz.open(str(PDF))
    toc = sesamaths_pdf.parse_toc(doc)
    chapters = {}
    for code in sorted(toc, key=lambda c: toc[c]["start_printed_page"]):
        start_idx, end_idx = sesamaths_pdf.chapter_page_range(doc, toc, code)
        chapters[code] = {
            "name": toc[code]["name"],
            "start_index": start_idx,
            "end_index": end_idx,
            "series": sesamaths_pdf.series_page_ranges(doc, toc, code),
            "exercise_pages": [p["index"]
                               for p in sesamaths_pdf.chapter_exercise_pages(doc, toc, code)],
        }
    return {"grade": grade, "page_offset": sesamaths_pdf.PAGE_OFFSET,
            "source_pdf": PDF.name, "chapters": chapters}


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n_chap = len(data["chapters"])
    n_pages = sum(len(c["exercise_pages"]) for c in data["chapters"].values())
    print(f"Carte écrite : {OUT}")
    print(f"{n_chap} chapitres, {n_pages} pages d'exercices (Culture exclue)")
    for code, c in data["chapters"].items():
        print(f"  {code} {c['name']}: {len(c['series'])} série(s), "
              f"pages fichier {c['exercise_pages'][:1]}…{c['exercise_pages'][-1:]}")


if __name__ == "__main__":
    main()

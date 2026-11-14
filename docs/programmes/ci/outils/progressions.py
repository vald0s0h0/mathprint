"""Progressions annuelles 2024-2025 (CND de mathématiques) : une page par niveau.

Sortie : {doc: [{rank, title, hours, week, month, trimester}]}, dans l'ordre de l'année.
La « Régulation » (1 h de remédiation après chaque leçon) et les « Révisions »
de fin d'année ne sont pas des leçons : elles sont comptées à part.
Usage : python -I progressions.py <progressions.pdf> <sortie.json>
"""
import json
import re
import sys

import pdfplumber

DOCS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14"]


def parse_page(page):
    lessons, regulation, revisions = [], 0, 0
    month = trimester = None
    week = None
    for table in page.extract_tables():
        for row in table:
            cells = [(c or "").replace("\n", " ").strip() for c in row]
            if cells and cells[0].startswith("Trimestre"):
                continue
            if cells[0]:
                trimester = int(re.match(r"(\d)", cells[0]).group(1))
            if len(cells) > 1 and cells[1]:
                month = cells[1]
            if len(cells) > 2 and cells[2].isdigit():
                week = int(cells[2])
            text = " ".join(c for c in cells[3:6] if c)
            hours = next((c for c in cells[6:9] if re.match(r"^\d+\s*h$", c)), None)
            h = int(re.match(r"\d+", hours).group(0)) if hours else 0
            if text.startswith("Régulation"):
                regulation += h
            elif text.startswith("Révision"):
                revisions += h
            else:
                m = re.match(r"^(\d+)\s*\.\s*(.+)$", text)
                if m:
                    lessons.append({"rank": int(m.group(1)), "title": m.group(2).strip(), "hours": h,
                                    "week": week, "month": month, "trimester": trimester})
    return {"lessons": lessons, "regulation_hours": regulation, "revision_hours": revisions}


def main(pdf_path, out):
    with pdfplumber.open(pdf_path) as pdf:
        result = {doc: parse_page(pdf.pages[i + 1]) for i, doc in enumerate(DOCS)}
    json.dump(result, open(out, "w"), ensure_ascii=False, indent=1)
    for doc, r in result.items():
        ranks = [l["rank"] for l in r["lessons"]]
        assert ranks == list(range(1, len(ranks) + 1)), (doc, ranks)
        print(doc, len(ranks), "leçons", sum(l["hours"] for l in r["lessons"]), "h",
              "+", r["regulation_hours"], "h régulation +", r["revision_hours"], "h révisions")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

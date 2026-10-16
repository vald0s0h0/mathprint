"""Correction simulée, pour tester « Affiner la correction » en dev local.

Prend un sujet généré (copies + subject_batch.pdf) qui n'a encore aucun lot de
scans et en fabrique un lot TERMINÉ — overlay prêt à imprimer — sans scanner
ni appeler aucune API :

- lectures et notes : chemin « lot simulé » du pipeline (pipeline._process_mock,
  ~70 % de réponses justes). TOUS les fournisseurs sont forcés sur leur repli
  hors-ligne : aucun appel Mathpix / DeepSeek / Claude, même avec des clés
  configurées ;
- revues restantes tranchées comme le ferait le professeur (lecture validée
  puis renotée ; rédactions et tracés notés en points) ;
- « scans » : chaque page du sujet rastérisée, avec les réponses de l'élève à
  l'encre bleue, posée en fond recadré (derived/page-*.png) + crops de zones
  pour la modale « Corriger » ;
- PIÈGES : sur quelques réponses, la copie montre autre chose que la lecture
  (lecture OCR fausse, dans les deux sens). Le récapitulatif final les liste :
  ce sont les réponses à retrouver et corriger dans « Affiner ».

Usage (depuis backend/) :
    .venv/bin/python scripts/mock_correction.py              # sujets possibles
    .venv/bin/python scripts/mock_correction.py <id> [<id>…] # préfixe d'id accepté

La base est sauvegardée avant toute écriture
(data/backups/before-mock-correction-<date>.sqlite). Pour retirer un lot :
Corrections → corbeille « Effacer la correction » (le sujet redevient « en
attente de scan », le script peut être relancé).
"""
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)                      # sqlite:///./mathprint.db = backend/mathprint.db
sys.path.insert(0, str(BACKEND))

import cv2                                            # noqa: E402
import numpy as np                                    # noqa: E402
from PIL import Image, ImageDraw, ImageFont           # noqa: E402

from app.config import settings                       # noqa: E402
from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
from app.models import (                              # noqa: E402
    Assessment, Copy, CopyItem, GradingDecision, ResponseZone, ScanBatch,
    SchoolClass, Student, StudentResponse, User,
)
from app.routers import scans as scans_router         # noqa: E402
from app.services import grading as grader            # noqa: E402
from app.services import pipeline, providers, scan_intake, worker_cv  # noqa: E402
from app.services.worker_cv import DPI, pt_to_px      # noqa: E402

# Aucun appel réseau, quelles que soient les clés configurées.
providers._mock_enabled = lambda db, cfg: True

INK = (25, 45, 125)                     # RGB, stylo bleu foncé
FONTS = [
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    str(BACKEND / "app" / "assets" / "fonts" / "OpenDyslexic-Regular.ttf"),
]
FONT_PATH = next((f for f in FONTS if Path(f).exists()), None)
EXPONENT = re.compile(r"\^(-?\w+)")      # « 2^6 » : exposant dessiné surélevé


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


# ------------------------------------------------------------------ dessin

def shown(value) -> str:
    """Valeur telle qu'un élève l'écrirait (LaTeX léger -> écriture)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}/{value[1]}"
    # virgule décimale {,} AVANT les fractions : ses accolades fermeraient le
    # groupe \frac{...} trop tôt (même piège que grading.normalize)
    s = str(value).replace("$", "").replace("{,}", ",")
    s = re.sub(r"\\d?frac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", s)
    s = s.replace("\\times", "×").replace("\\,", " ")
    s = re.sub(r"\^\{(-?\w+)\}", r"^\1", s)
    s = s.replace("{", "").replace("}", "").replace("\\left", "").replace("\\right", "")
    s = re.sub(r"\\([a-zA-Z]+)", r"\1", s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        s = s.replace(".", ",")
    return s.strip()


def plausible_wrong(value: str, h: int) -> str:
    """Erreur d'élève vraisemblable : un chiffre changé (« 6,3 » -> « 6,4 »,
    « 2² × 3² × 5 » -> « 2² × 3² × 6 »), à défaut une lettre."""
    s = shown(value) or "0"
    digits = [m.start() for m in re.finditer(r"[0-9]", s)]
    if digits:
        i = digits[-1]
        return s[:i] + str((int(s[i]) + 1 + h % 3) % 10) + s[i + 1:]
    letters = [m.start() for m in re.finditer(r"[A-Za-z]", s)]
    if letters:
        i = letters[-1]
        return s[:i] + ("N" if s[i] != "N" else "P") + s[i + 1:]
    return s + " ?"


def rect_px(g: dict) -> tuple[int, int, int, int]:
    x0, y1 = pt_to_px(g["x_pt"], g["y_pt"])
    x1, y0 = pt_to_px(g["x_pt"] + g["w_pt"], g["y_pt"] + g["h_pt"])
    return int(x0), int(y0), int(x1), int(y1)


class Sheet:
    """Page du sujet sur laquelle « l'élève » écrit."""

    def __init__(self, bgr: np.ndarray):
        self.img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        self.draw = ImageDraw.Draw(self.img)

    def text(self, g: dict, value: str, *, max_lines: int = 1):
        if not value:
            return
        x0, y0, x1, y1 = rect_px(g)
        w, h = x1 - x0, y1 - y0
        lines = [value] if max_lines == 1 else _wrap(value, max_lines)
        size = max(14, int(min(h / max(1, len(lines)) * 0.72, 64)))
        left = max(18, w * 0.05)        # marge : on écrit DANS la case
        while size > 14 and max(self._width(line, size) for line in lines) > w - 2 * left:
            size -= 2
        line_h = size * 1.15
        top = y0 + (h - line_h * len(lines)) / 2
        for i, line in enumerate(lines):
            self._line(x0 + left, top + i * line_h, line, size)

    def _runs(self, line: str, size: int):
        """Morceaux (texte, police, décalage vertical) : exposants plus petits
        et surélevés, comme écrits à la main."""
        pos = 0
        for m in EXPONENT.finditer(line):
            if m.start() > pos:
                yield line[pos:m.start()], _font(size), 0
            yield m.group(1), _font(int(size * 0.6)), -int(size * 0.08)
            pos = m.end()
        if pos < len(line):
            yield line[pos:], _font(size), 0

    def _width(self, line: str, size: int) -> float:
        return sum(self.draw.textlength(t, font=f) for t, f, _ in self._runs(line, size))

    def _line(self, x: float, y: float, line: str, size: int):
        for t, f, dy in self._runs(line, size):
            self.draw.text((x, y + dy), t, font=f, fill=INK)
            x += self.draw.textlength(t, font=f)

    def cross(self, g: dict):
        x0, y0, x1, y1 = rect_px(g)
        for a, b in (((x0, y0), (x1, y1)), ((x0, y1), (x1, y0))):
            self.draw.line([a, b], fill=INK, width=4)

    def link(self, a: dict, b: dict, wobble: int):
        ax, ay, bx, by = (*_center(a), *_center(b))
        mid = ((ax + bx) / 2, (ay + by) / 2 + wobble)
        self.draw.line([(ax, ay), mid, (bx, by)], fill=INK, width=4, joint="curve")

    def sketch(self, g: dict):
        x0, y0, x1, y1 = rect_px(g)
        w, h = x1 - x0, y1 - y0
        pts = [(x0 + w * 0.25, y0 + h * 0.8), (x0 + w * 0.5, y0 + h * 0.2),
               (x0 + w * 0.75, y0 + h * 0.8), (x0 + w * 0.25, y0 + h * 0.8)]
        self.draw.line(pts, fill=INK, width=4, joint="curve")

    def bgr(self) -> np.ndarray:
        return cv2.cvtColor(np.asarray(self.img), cv2.COLOR_RGB2BGR)


def _font(size: int):
    return ImageFont.truetype(FONT_PATH, size) if FONT_PATH else ImageFont.load_default()


def _wrap(value: str, max_lines: int) -> list[str]:
    words, lines, line = value.split(), [], ""
    per = max(18, len(value) // max_lines + 1)
    for w in words:
        if len(line) + len(w) + 1 > per and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return lines[:max_lines]


def _center(g: dict) -> tuple[float, float]:
    x0, y0, x1, y1 = rect_px(g)
    return (x0 + x1) / 2, (y0 + y1) / 2


# ------------------------------------------------------------------ données

def _reading(db, resp: StudentResponse) -> dict:
    r = pipeline.effective_reading(db, resp.zone_id)
    raw = (r.raw_json or {}) if r else {}
    return {"text": resp.final_text or (r.text if r and r.text else ""),
            "selected": list(resp.selected_choices or []),
            "grid": raw.get("grid_selected"),
            "pairs": raw.get("pairs"),
            "cells": raw.get("cells")}


def _expected_answer(item: CopyItem) -> dict:
    exp, pol = item.expected_json or {}, item.grading_json or {}
    rt = item.response_type
    if rt.startswith("qcm"):
        return {"selected": list(exp.get("correct") or [])}
    if rt == "checkbox_grid":
        return {"grid": [r.get("correct") for r in (pol.get("rows") or exp.get("rows") or [])]}
    if rt == "matching":
        return {"pairs": [list(p) for p in exp.get("pairs") or []]}
    if rt in ("table_fill", "multi_blank"):
        return {"cells": [shown(c["value"]) for c in grader.fillable_cells(pol)]}
    return {"text": shown(pipeline._expected_as_text(exp))}


def _is_editable(item: CopyItem) -> bool:
    from app.services.refine import edit_kind
    return edit_kind(item) != "points"


def _describe(item: CopyItem, ans: dict) -> str:
    rt, pol = item.response_type, item.grading_json or {}
    if rt.startswith("qcm"):
        choices = pol.get("choices") or []
        return " + ".join(shown(choices[i]) if i < len(choices) else str(i)
                          for i in ans.get("selected") or []) or "rien coché"
    if rt == "checkbox_grid":
        cols = pol.get("cols") or []
        return ", ".join(f"L{i + 1}:{cols[c] if c is not None and 0 <= c < len(cols) else '—'}"
                         for i, c in enumerate(ans.get("grid") or []))
    if rt == "matching":
        return ", ".join(f"{a + 1}→{chr(65 + b)}" for a, b in ans.get("pairs") or []) or "aucune liaison"
    if rt in ("table_fill", "multi_blank"):
        return " | ".join(shown(c) or "∅" for c in ans.get("cells") or [])
    return shown(ans.get("text") or "") or "∅"


# ------------------------------------------------------------------ lot

def _resolve_reviews(db, assessment: Assessment) -> int:
    """Le professeur valide la lecture puis la note (renotation déterministe à
    confiance pleine) ; rédactions et tracés reçoivent des points."""
    n = 0
    for review in scans_router._pending_reviews_query(db, assessment.id).all():
        decision = db.get(GradingDecision, review.decision_id)
        resp = db.get(StudentResponse, decision.response_id)
        item = db.get(CopyItem, resp.copy_item_id)
        rt = item.response_type
        if rt in ("multiline_text", "composite", "manual_drawing"):
            ratio = [0.0, 1 / 3, 2 / 3, 1.0][_h(resp.id) % 4]
        else:
            r = _reading(db, resp)
            verdict = grader.grade(
                item.expected_json or {}, item.grading_json or {}, r["text"], 1.0,
                r["grid"] if rt == "checkbox_grid" else (r["selected"] if rt.startswith("qcm") else None),
                cell_texts=r["cells"] if rt in ("table_fill", "multi_blank") else None,
                selected_pairs=r["pairs"] if rt == "matching" else None)
            ratio = (verdict["score"] / verdict["max_score"]
                     if verdict["tier"] in ("A", "B", "C") and verdict["max_score"] else 0.0)
        scans_router._apply_resolution(db, resp, scans_router.ResolveIn(
            action="set_ratio", ratio=ratio, note="correction simulée"))
        n += 1
    return n


def _draw_answer(sheet: Sheet, item: CopyItem, zone: ResponseZone, ans: dict, h: int):
    meta = zone.meta_json or {}
    rt = item.response_type
    geo = {"x_pt": zone.x_pt, "y_pt": zone.y_pt, "w_pt": zone.w_pt, "h_pt": zone.h_pt}
    if rt.startswith("qcm"):
        boxes = {b.get("index"): b for b in meta.get("boxes") or []}
        for i in ans.get("selected") or []:
            if i in boxes:
                sheet.cross(boxes[i])
    elif rt == "checkbox_grid":
        boxes = {(b.get("row"), b.get("col")): b for b in meta.get("boxes") or []}
        for ri, col in enumerate(ans.get("grid") or []):
            if (ri, col) in boxes:
                sheet.cross(boxes[(ri, col)])
    elif rt == "matching":
        left = {p.get("index"): p for p in meta.get("left_points") or []}
        right = {p.get("index"): p for p in meta.get("right_points") or []}
        for k, (a, b) in enumerate(ans.get("pairs") or []):
            if a in left and b in right:
                sheet.link(left[a], right[b], wobble=((h >> k) % 9 - 4) * 4)
    elif rt in ("table_fill", "multi_blank"):
        exp_cells = (item.expected_json or {}).get("cells") or []
        values, k = ans.get("cells") or [], 0
        for ri, row in enumerate(meta.get("cells") or []):
            for ci, cell in enumerate(row):
                if ri < len(exp_cells) and ci < len(exp_cells[ri]) and exp_cells[ri][ci].get("given"):
                    continue
                if cell and k < len(values):
                    sheet.text(cell, shown(values[k]))
                k += 1
    elif rt == "manual_drawing":
        sheet.sketch(geo)
    elif rt in ("multiline_text", "composite"):
        sheet.text(geo, shown(ans.get("text") or "") or "Je calcule puis je conclus.", max_lines=3)
    else:
        sheet.text(geo, shown(ans.get("text") or ""))


def _write_crops(derived: Path, img: np.ndarray, item: CopyItem, zone: ResponseZone):
    """Mêmes fichiers que le vrai pipeline : crop de zone + crops par case."""
    crop = worker_cv.crop_zone(img, zone.x_pt, zone.y_pt, zone.w_pt, zone.h_pt, zone.padding_pt)
    if crop.size:
        (derived / f"{zone.id}.png").write_bytes(
            worker_cv.encode_png(worker_cv.dropout_filter(crop)))
    if item.response_type not in ("table_fill", "multi_blank"):
        return
    exp_cells = (item.expected_json or {}).get("cells") or []
    k = 0
    for ri, row in enumerate((zone.meta_json or {}).get("cells") or []):
        for ci, cell in enumerate(row):
            if ri < len(exp_cells) and ci < len(exp_cells[ri]) and exp_cells[ri][ci].get("given"):
                continue
            disp = worker_cv.crop_zone(img, cell["x_pt"], cell["y_pt"], cell["w_pt"],
                                       cell["h_pt"], padding_pt=2.5)
            if disp.size:
                (derived / f"{zone.id}-c{k}.png").write_bytes(
                    worker_cv.encode_png(worker_cv.dropout_filter(disp)))
            k += 1


def _labels(db, rows) -> dict[str, str]:
    """« Ex. 5a / 5b » : sous-questions lettrées dans l'ordre de la copie
    (page, puis de haut en bas), comme dans la modale Affiner."""
    from app.models import DocumentPage
    groups: dict[int, list] = {}
    for item, zone, resp, _, _ in rows:
        page = db.get(DocumentPage, zone.page_id)
        key = (page.page_no if page else 0, -(zone.y_pt + zone.h_pt), zone.x_pt)
        groups.setdefault(item.sequence, []).append((key, resp.id))
    out = {}
    for seq, group in groups.items():
        group.sort()
        for i, (_, rid) in enumerate(group):
            out[rid] = f"Ex. {seq}{chr(97 + i) if len(group) > 1 else ''}"
    return out


def _plan_answers(db, copy: Copy) -> tuple[dict, list[str]]:
    """Ce que la copie MONTRE pour chaque réponse : la lecture, sauf sur les
    pièges (lecture OCR fausse) où le scan dit autre chose."""
    plan, traps = {}, []
    rows = []
    for item in db.query(CopyItem).filter_by(copy_id=copy.id).order_by(CopyItem.sequence):
        resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
        zone = db.query(ResponseZone).filter_by(item_id=item.id).first()
        if not resp or not zone:
            continue
        dec = (db.query(GradingDecision).filter_by(response_id=resp.id)
               .order_by(GradingDecision.created_at.desc()).first())
        full = bool(dec and dec.max_score and dec.score >= dec.max_score)
        read = _reading(db, resp)
        rt = item.response_type
        ans = {"selected": read["selected"]} if rt.startswith("qcm") else \
              {"grid": read["grid"] or []} if rt == "checkbox_grid" else \
              {"pairs": read["pairs"] or []} if rt == "matching" else \
              {"cells": read["cells"] or []} if rt in ("table_fill", "multi_blank") else \
              {"text": read["text"]}
        rows.append((item, zone, resp, full, ans))

    labels = _labels(db, rows)
    h = _h(copy.id)
    wrong = [r for r in rows if not r[3] and _is_editable(r[0])]
    right = [r for r in rows if r[3] and r[0].response_type in
             ("short_text", "table_fill", "multi_blank") and _is_editable(r[0])]
    misread = {r[2].id for r in sorted(wrong, key=lambda r: _h(h, r[2].id))[:2]}
    generous = {r[2].id for r in sorted(right, key=lambda r: _h(h, r[2].id))[:1]}
    for item, zone, resp, full, ans in rows:
        unreadable = ans.get("text") == "?" or (item.response_type == "matching" and not ans["pairs"])
        if resp.id in misread or unreadable:
            shown_ans = _expected_answer(item)
            traps.append(f"{labels[resp.id]} ({item.response_type}) : la copie porte "
                         f"« {_describe(item, shown_ans)} », lu « {_describe(item, ans)} » "
                         f"→ note à remonter")
        elif resp.id in generous:
            if "cells" in ans and ans["cells"]:
                cells = list(ans["cells"])
                cells[0] = plausible_wrong(cells[0], h)
                shown_ans = {"cells": cells}
            else:
                shown_ans = {"text": plausible_wrong(ans.get("text") or "0", h)}
            traps.append(f"{labels[resp.id]} ({item.response_type}) : la copie porte "
                         f"« {_describe(item, shown_ans)} », lu « {_describe(item, ans)} » "
                         f"→ note à baisser")
        else:
            shown_ans = ans
        plan[zone.id] = (item, zone, shown_ans)
    return plan, traps


def mock(db, assessment: Assessment, user_id: str | None) -> dict:
    gen = settings.data_dir / "assessments" / assessment.id / "generated"
    manifest = json.loads((gen / "copy_manifest.json").read_text())
    order = [p["page_id"] for c in manifest["copies"] for p in c["pages"]]
    pages = worker_cv.raster_pdf(str(gen / "subject_batch.pdf"), dpi=DPI)
    if len(pages) != len(order):
        raise SystemExit(f"{assessment.title} : le PDF ({len(pages)} p.) ne correspond "
                         f"pas au manifeste ({len(order)} p.)")
    raster = dict(zip(order, pages))

    batch = scan_intake.get_or_create_batch(db, assessment.id, user_id)
    db.commit()
    pipeline.process_batch(db, batch)                 # lectures + notes simulées
    resolved = _resolve_reviews(db, assessment)
    db.refresh(batch)
    if batch.status not in ("finalized", "overlay_ready"):
        pipeline.finalize_batch(db, batch)
        db.commit()

    derived = settings.data_dir / "assessments" / assessment.id / "scans" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    report = {}
    copies = pipeline._copies_in_scan_order(db, batch, assessment.id)
    for copy in copies:
        plan, traps = _plan_answers(db, copy)
        student = db.get(Student, copy.student_id)
        by_page: dict[str, list] = {}
        for item, zone, ans in plan.values():
            by_page.setdefault(zone.page_id, []).append((item, zone, ans))
        for page_id, entries in by_page.items():
            if page_id not in raster:
                continue
            sheet = Sheet(raster[page_id])
            for item, zone, ans in entries:
                _draw_answer(sheet, item, zone, ans, _h(zone.id))
            img = sheet.bgr()
            (derived / f"page-{page_id}.png").write_bytes(worker_cv.encode_png(img))
            for item, zone, _ in entries:
                _write_crops(derived, img, item, zone)
        report[student.name] = traps
    pipeline.build_overlays(db, batch)                # fonds de scan dans l'aperçu
    db.commit()
    db.refresh(batch)
    return {"batch": batch, "resolved": resolved, "traps": report}


# ------------------------------------------------------------------ CLI

def candidates(db) -> list[Assessment]:
    with_batch = {a for (a,) in db.query(ScanBatch.assessment_id).distinct()}
    out = []
    for a in (db.query(Assessment).filter(Assessment.status.in_(("ready", "printed")))
              .order_by(Assessment.created_at.desc())):
        gen = settings.data_dir / "assessments" / a.id / "generated"
        if a.id in with_batch or not (gen / "subject_batch.pdf").exists() \
                or not (gen / "copy_manifest.json").exists():
            continue
        if db.query(Copy).filter_by(assessment_id=a.id).count():
            out.append(a)
    return out


def main(args: list[str]):
    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    pool = candidates(db)
    if not args:
        print("Sujets générés sans lot de scans :\n")
        for a in pool:
            cls = db.get(SchoolClass, a.class_id)
            types = sorted({t for (t,) in db.query(CopyItem.response_type)
                            .join(Copy, CopyItem.copy_id == Copy.id)
                            .filter(Copy.assessment_id == a.id).distinct()})
            n_copies = db.query(Copy).filter_by(assessment_id=a.id).count()
            print(f"  {a.id}  {a.title} · {a.type} · {cls.name if cls else '?'} · "
                  f"{n_copies} copie(s){' · recto-verso' if a.duplex else ''}\n"
                  f"      {', '.join(types)}")
        print("\nUsage : .venv/bin/python scripts/mock_correction.py <id> [<id>…]")
        return
    chosen = []
    for arg in args:
        match = [a for a in pool if a.id.startswith(arg)]
        if len(match) != 1:
            raise SystemExit(f"« {arg} » : {len(match)} sujet(s) possible(s) "
                             "(déjà corrigé, ou id inconnu)")
        chosen.append(match[0])

    db_file = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    if db_file.exists():
        backups = settings.data_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        target = backups / f"before-mock-correction-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
        shutil.copy2(db_file, target)
        print(f"Base sauvegardée : {target}")

    admin = db.query(User).filter_by(role="admin").first() or db.query(User).first()
    for a in chosen:
        out = mock(db, a, admin.id if admin else None)
        b = out["batch"]
        print(f"\n✓ {a.title} ({a.id[:8]}) — lot {b.id[:8]} : {b.status}, "
              f"{b.page_count} page(s), {out['resolved']} revue(s) tranchée(s)")
        for student, traps in out["traps"].items():
            print(f"  {student}")
            for t in traps or ["(aucun piège)"]:
                print(f"    · {t}")
    db.close()


if __name__ == "__main__":
    main(sys.argv[1:])

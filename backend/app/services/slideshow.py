"""Diaporama de correction au tableau (menu ⋮ d'un lot : « Lancer / Exporter diaporama »).

Une page HTML autonome, en un seul fichier : un diapo par famille d'exercices du
sujet (jumelles comprises, servies ou non), la carte vierge et sa correction rendues
par le moteur d'aperçu (card_preview), le scan de chaque élève recadré sur sa carte,
et son résultat. Toutes les images sont intégrées en data URI : la même page sert
au lancement dans l'application et à l'export.

Lecture seule : rien n'est écrit en base. Le dessin et le pointeur vivent dans la
page (assets/slideshow.html) et ne sont jamais enregistrés.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (Assessment, Copy, CopyItem, GeneratedExercise, GradingDecision, ResponseZone,
                      ScanBatch, SchoolClass, Student, StudentResponse)
from . import card_preview, card_warm, distribution, pdfgen, scoring
from . import skeleton as sk
from .pipeline import effective_reading

logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "slideshow.html"
TEXT_TYPES = ("short_text", "multiline_text", "table_fill", "multi_blank")
# en dessous, la lecture d'une réponse écrite n'est pas jugée sûre : la copie n'est
# pas proposée par « Élève au hasard »
OCR_SURE = 0.85
SCAN_MAX_W = 1100


def _data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _bbox(d: str) -> tuple[float, float, float, float]:
    n = [float(x) for x in re.findall(r"-?\d+\.?\d*", d)]
    return min(n[0::2]), min(n[1::2]), max(n[0::2]), max(n[1::2])


def _exercise_meta(row: GeneratedExercise) -> dict:
    from ..routers.content import _exercise_out
    eo = _exercise_out(row, None)
    return {"level": row.difficulty_level, "title": eo.get("title") or "", "kind": eo.get("kind"),
            "is_problem": bool(eo.get("is_problem")), "badge_type": eo.get("badge_type"),
            "competency_label": eo.get("competency_label") or ""}


def _cards(db: Session, row: GeneratedExercise) -> dict:
    """Carte vierge et carte corrigée, rendues par le moteur d'aperçu (cache disque)."""
    try:
        ex = card_preview.resolve_card(card_warm.card_for_catalog(row), db)
        return {"card_blank": _data_uri(card_preview.render(ex, True, False), "image/png"),
                "card_answers": _data_uri(card_preview.render(ex, True, True), "image/png")}
    except Exception as exc:    # une carte en échec n'empêche pas le diaporama
        logger.warning("diaporama : carte %s non rendue : %s", row.id, exc)
        return {"render_error": str(exc)}


class _ScanCropper:
    """Recadre le scan redressé d'une page sur la carte qui contient une zone.
    Une seule page en mémoire à la fois (le NAS a peu de RAM)."""

    def __init__(self):
        self.skel: dict[str, list] = {}
        self.page_id: str | None = None
        self.img = None

    def crop(self, copy: Copy, zone: ResponseZone) -> str | None:
        import cv2
        zr = sk._zone_rect(zone)
        idx = sk._page_index(copy.assessment_id)
        if zone.page_id not in self.skel:
            self.skel[zone.page_id] = (sk._pdf_skeleton(copy.assessment_id, idx[zone.page_id])
                                       if zone.page_id in idx else None) or []
        cx, cy = zr["x"] + zr["w"] / 2, zr["y"] + zr["h"] / 2
        cards = [_bbox(p["d"]) for p in self.skel[zone.page_id] if p["kind"] == "card"]
        inside = [k for k in cards if k[0] <= cx <= k[2] and k[1] <= cy <= k[3]]
        x0, y0, x1, y1 = (min(inside, key=lambda k: (k[2] - k[0]) * (k[3] - k[1])) if inside
                          else (zr["x"] - 8, zr["y"] - 60, zr["x"] + zr["w"] + 8, zr["y"] + zr["h"] + 8))
        x0, y0, x1, y1 = x0 - 4, y0 - 4, x1 + 4, y1 + 4
        if self.page_id != zone.page_id:
            path = (settings.data_dir / "assessments" / copy.assessment_id / "scans" / "derived"
                    / f"page-{zone.page_id}.png")
            self.page_id, self.img = zone.page_id, (cv2.imread(str(path)) if path.exists() else None)
        if self.img is None:
            return None
        H, W = self.img.shape[:2]
        k = W / pdfgen.PAGE_W
        crop = self.img[max(0, int(y0 * k)):min(H, int(y1 * k)), max(0, int(x0 * k)):min(W, int(x1 * k))]
        if not crop.size:
            return None
        f = min(1.0, SCAN_MAX_W / crop.shape[1])
        if f < 1:
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 74])
        return _data_uri(buf.tobytes(), "image/jpeg") if ok else None


def build(db: Session, batch: ScanBatch) -> dict:
    """Données du diaporama : {lot, students, families: [{key, variants}], responses}."""
    from ..routers.scans import _latest_decision_for_response, _pending_reviews_query
    a = db.get(Assessment, batch.assessment_id)
    cls = db.get(SchoolClass, a.class_id) if a.class_id else None
    open_resp = set()
    for r in _pending_reviews_query(db, a.id).all():
        d = db.get(GradingDecision, r.decision_id)
        if d:
            open_resp.add(d.response_id)
    out = {"lot": {"id": batch.id, "title": a.title, "class_name": cls.name if cls else ""},
           "students": [], "responses": []}
    families: dict[str, dict] = {}
    twin_cache: dict = {}
    cropper = _ScanCropper()
    copies = db.query(Copy).filter_by(assessment_id=a.id).all()
    students = {s.id: s for s in db.query(Student).filter(Student.id.in_([c.student_id for c in copies])).all()}
    for c in copies:
        st = students.get(c.student_id)
        out["students"].append({"copy_id": c.id, "name": st.name if st else "?",
                                "order": st.order_index if st else 0, "status": c.status})
        for item in db.query(CopyItem).filter_by(copy_id=c.id).all():
            row = db.get(GeneratedExercise, item.generated_exercise_id) if item.generated_exercise_id else None
            sig = hashlib.sha1(item.statement.encode()).hexdigest()[:8]
            vkey = item.generated_exercise_id or f"{item.catalog_id}|{sig}"
            fkey = row.twin_group if row and row.twin_group else vkey
            fam = families.setdefault(fkey, {"key": fkey, "variants": {}})
            if vkey not in fam["variants"]:
                v = {"key": vkey, "response_type": item.response_type, "difficulty": item.difficulty,
                     "bareme": scoring.item_bareme(item.grading_json or {}, item.response_type),
                     "twin_jeu": row.twin_jeu if row else 0, "twin_disposition": row.twin_disposition if row else 0}
                if row:
                    v.update(_exercise_meta(row), **_cards(db, row))
                fam["variants"][vkey] = v
                # jumelles de la famille que personne n'a eues : montrées quand même
                if row and row.twin_group:
                    for sib in distribution.twin_siblings(db, row, twin_cache).values():
                        if sib.id not in fam["variants"]:
                            fam["variants"][sib.id] = {
                                **{k: x for k, x in v.items() if not k.startswith("card")},
                                "key": sib.id, "twin_jeu": sib.twin_jeu, "twin_disposition": sib.twin_disposition,
                                "unserved": True, **_exercise_meta(sib), **_cards(db, sib)}
                elif row is None:
                    v["render_error"] = "exercice hors banque"
            # la variante tirée par une jumelle déjà ajoutée n'est plus « non servie »
            fam["variants"][vkey].pop("unserved", None)
            out["responses"].append(_response(db, c, item, fkey, vkey, open_resp, cropper,
                                              _latest_decision_for_response))
    out["families"] = [{"key": f["key"], "variants": list(f["variants"].values())} for f in families.values()]
    return out


def _response(db, copy, item, fkey, vkey, open_resp, cropper, latest) -> dict:
    resps = db.query(StudentResponse).filter_by(copy_item_id=item.id).all()
    score = mx = 0.0
    graded = pending = False
    ocr_ok, blank, reason, answers, zone = True, True, "", [], None
    for resp in resps:
        d = latest(db, resp.id)
        if d:
            graded = True
            score += d.score or 0
            mx += d.max_score or 0
            reason = d.reason_code or reason
        if resp.id in open_resp:
            ocr_ok, pending = False, True
        rd = effective_reading(db, resp.zone_id) if resp.zone_id else None
        txt = (rd.text if rd and rd.text is not None else resp.final_text) or ""
        if item.response_type in TEXT_TYPES and rd is not None and rd.confidence is not None \
                and rd.confidence < OCR_SURE:
            ocr_ok = False
        if resp.selected_choices or resp.selected_pairs or txt.strip():
            blank = False
        try:
            answers.append(scoring.student_answer_text(db, resp))
        except Exception:
            answers.append(txt)
        if zone is None and resp.zone_id:
            zone = db.get(ResponseZone, resp.zone_id)
    r = {"copy_id": copy.id, "family": fkey, "variant": vkey, "score": score, "max": mx,
         "graded": graded, "pending": pending, "reason": reason, "ocr_ok": ocr_ok and not blank,
         "blank": blank, "answer": " | ".join(x for x in answers if x)[:200]}
    if zone is not None and copy.status not in ("absent", "generated", "printed"):
        try:
            r["scan"] = cropper.crop(copy, zone)
        except Exception as exc:
            logger.warning("diaporama : scan %s non recadré : %s", zone.id, exc)
    return r


def render_html(data: dict) -> str:
    # « </ » échappé : une chaîne de l'énoncé ne peut pas fermer la balise <script>
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload, 1)


def filename(data: dict) -> str:
    base = re.sub(r"[^\w-]+", "-", f"diaporama-{data['lot']['title']}", flags=re.UNICODE).strip("-")
    return f"{base[:80] or 'diaporama'}.html"

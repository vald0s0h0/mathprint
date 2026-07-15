"""Extraction PyMuPDF des manuels Sésamath (§ pipeline Sésamaths).

Repères de pagination : chaque manuel a une unique page "Sommaire" listant
les chapitres avec leur page IMPRIMÉE de départ. La page fichier (1-indexée)
= page imprimée + 2 (vérifié empiriquement sur le manuel 5e : "A2 ... 23"
dans le sommaire -> page fichier 25 = début réel du chapitre A2). Le pied de
page de chaque page porte le code chapitre + le numéro de page imprimé, réutilisé
en contrôle croisé — mais PAS comme source primaire, l'ordre de ces lignes
n'étant pas fiable en extraction brute (blocs de mise en page superposés).

Seule la classe de 5e est couverte pour l'instant (`settings.sesamaths_manuals`) ;
un manuel absent ou illisible est journalisé et ne bloque jamais l'appelant
(la pipeline Sésamaths se contente alors de renvoyer aucun exercice).
"""
import hashlib
import logging
import re
from pathlib import Path

import fitz  # PyMuPDF
from sqlalchemy.orm import Session

from ..config import settings
from ..models import FileObject, SesamathsManual

logger = logging.getLogger(__name__)

# page imprimée (table des matières) -> page fichier 1-indexée
PAGE_OFFSET = 2

_TOC_LINE_RE = re.compile(r"^([A-E]\d)\s+(.+?)\s*\.{3,}\s*(\d+)\s*$")

# heuristique v1 de détection de figure (voir _figure_rects) — à recalibrer
# une fois des exemples de découpe réels disponibles (pas de prévisualisation
# possible dans cette pipeline, donc pas de boucle de réglage visuel pour l'instant)
_MIN_FIGURE_AREA = 250.0
_MAX_FIGURE_AREA_RATIO = 0.40
_MERGE_MARGIN = 5.0


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_or_create_manual(db: Session, grade_level: str) -> SesamathsManual:
    row = db.query(SesamathsManual).filter_by(grade_level=grade_level).first()
    if row is None:
        row = SesamathsManual(grade_level=grade_level, status="missing")
        db.add(row)
        db.flush()
    return row


def parse_toc(doc: "fitz.Document") -> dict:
    """Cherche la page "Sommaire" (6 premières pages), extrait les lignes de
    chapitre `^[A-E]\\d ... page` (les sous-lignes "Série ..." sont ignorées :
    seule la borne de chapitre nous intéresse). Retourne
    {chapter_code: {"name": str, "start_printed_page": int}}."""
    for i in range(min(6, len(doc))):
        text = doc[i].get_text()
        if "Sommaire" not in text:
            continue
        entries: dict = {}
        for line in text.splitlines():
            m = _TOC_LINE_RE.match(line.strip())
            if not m:
                continue
            code, name, page = m.group(1), m.group(2).strip(), int(m.group(3))
            entries[code] = {"name": name, "start_printed_page": page}
        if entries:
            return entries
    raise ValueError("Page Sommaire introuvable ou vide")


def open_manual(db: Session, grade_level: str) -> tuple["fitz.Document | None", SesamathsManual]:
    """Ouvre le manuel PDF du niveau demandé. Si absent/illisible : journalise
    l'erreur, marque `SesamathsManual.status`, retourne (None, manual) —
    jamais d'exception (un manuel manquant ne doit jamais casser la
    génération de sujet, cf. contraintes Sésamaths)."""
    manual = get_or_create_manual(db, grade_level)
    path_str = settings.sesamaths_manuals.get(grade_level)
    if not path_str:
        manual.status = "missing"
        manual.error_message = f"Aucun manuel Sésamaths configuré pour le niveau {grade_level}"
        logger.error("Sésamaths : %s", manual.error_message)
        db.commit()
        return None, manual

    path = Path(path_str)
    if not path.exists():
        manual.status = "missing"
        manual.error_message = f"Fichier manuel introuvable : {path}"
        logger.error("Sésamaths : %s", manual.error_message)
        db.commit()
        return None, manual

    try:
        sha = _sha256_file(path)
        doc = fitz.open(str(path))
    except Exception as e:
        manual.status = "error"
        manual.error_message = f"Ouverture PDF impossible ({path.name}) : {e}"
        logger.error("Sésamaths : %s", manual.error_message)
        db.commit()
        return None, manual

    if manual.sha256 != sha or not manual.file_object_id:
        fobj = FileObject(owner_type="sesamaths_manual", owner_id=grade_level,
                          storage_path=str(path), sha256=sha,
                          mime="application/pdf", size=path.stat().st_size)
        db.add(fobj)
        db.flush()
        manual.file_object_id = fobj.id
        manual.sha256 = sha
        manual.toc_json = {}  # re-parse si le fichier a changé

    if not manual.toc_json:
        try:
            manual.toc_json = parse_toc(doc)
        except Exception as e:
            manual.status = "error"
            manual.error_message = f"Table des matières illisible : {e}"
            logger.error("Sésamaths : %s", manual.error_message)
            db.commit()
            return None, manual

    manual.status = "ready"
    manual.error_message = ""
    db.commit()
    return doc, manual


def _footer_has_code(doc: "fitz.Document", idx: int, code: str) -> bool:
    if not (0 <= idx < len(doc)):
        return False
    lines = doc[idx].get_text().splitlines()
    tail = "\n".join(lines[-8:])
    return bool(re.search(rf"\b{re.escape(code)}\b", tail))


def chapter_page_range(doc: "fitz.Document", toc: dict, chapter_code: str) -> tuple[int, int]:
    """Bornes fichier (0-indexées, incluses) du chapitre `chapter_code`,
    déduites de la table des matières (+2) puis recalées si le pied de page
    de la page de départ ne confirme pas le code (contrôle croisé, jamais
    bloquant : on garde la déduction ToC si le recalage échoue)."""
    if chapter_code not in toc:
        raise ValueError(f"Chapitre {chapter_code} absent de la table des matières")
    ordered = sorted(toc.items(), key=lambda kv: kv[1]["start_printed_page"])
    codes = [c for c, _ in ordered]
    idx = codes.index(chapter_code)
    start_printed = toc[chapter_code]["start_printed_page"]
    end_printed = (ordered[idx + 1][1]["start_printed_page"] - 1
                   if idx + 1 < len(ordered) else None)

    start_idx = start_printed + PAGE_OFFSET - 1
    end_idx = (end_printed + PAGE_OFFSET - 1) if end_printed is not None else len(doc) - 1

    # Contrôle croisé PUREMENT informatif : la page de départ d'un chapitre
    # est souvent une page de rappel de leçon ("À RETENIR"), dont le pied de
    # page ne porte PAS le code chapitre (seul le numéro de page y figure) —
    # seules les pages de Série (exercices) l'affichent. Une absence ici est
    # donc normale et NE DOIT PAS décaler la plage : la règle table des
    # matières + 2 s'est révélée fiable à 100% sur les cas vérifiés (A1, A2,
    # B4). On se contente de journaliser si le code n'apparaît nulle part
    # dans les pages de la plage déduite, signe possible d'un vrai problème.
    if not any(_footer_has_code(doc, i, chapter_code)
              for i in range(start_idx, min(end_idx, start_idx + 4) + 1)):
        logger.warning("Sésamaths : code %s non retrouvé dans les pieds de page "
                       "des 5 premières pages déduites (page fichier %s) — "
                       "position issue de la table des matières conservée telle quelle",
                       chapter_code, start_idx + 1)

    return max(0, start_idx), min(len(doc) - 1, end_idx)


def _merge_rects(rects: list["fitz.Rect"], margin: float = _MERGE_MARGIN) -> list["fitz.Rect"]:
    merged = list(rects)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i in range(len(merged)):
            infl = fitz.Rect(merged[i].x0 - margin, merged[i].y0 - margin,
                             merged[i].x1 + margin, merged[i].y1 + margin)
            for j in range(i + 1, len(merged)):
                if infl.intersects(merged[j]):
                    merged[i] |= merged[j]
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _figure_rects(page: "fitz.Page") -> list["fitz.Rect"]:
    """Regroupe tracés vectoriels et images en rectangles de figure plausibles.
    Heuristique v1 (aucune boucle de réglage visuel possible dans cette
    pipeline automatisée) : les bbox de lignes parfaitement horizontales ou
    verticales sont dégénérées (aire nulle), donc légèrement épaissies avant
    fusion ; les éléments couvrant l'essentiel de la page (fond, cadre) sont
    écartés. À recalibrer si des figures sont mal découpées en pratique."""
    prect = page.rect
    page_area = prect.get_area()
    raw: list[fitz.Rect] = []
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"]) & prect
        if r.is_empty:
            continue
        if r.width < 1e-6:
            r.x0, r.x1 = r.x0 - 0.75, r.x1 + 0.75
        if r.height < 1e-6:
            r.y0, r.y1 = r.y0 - 0.75, r.y1 + 0.75
        raw.append(r)
    for img in page.get_image_info():
        r = fitz.Rect(img["bbox"]) & prect
        if not r.is_empty:
            raw.append(r)
    raw = [r for r in raw if r.get_area() < _MAX_FIGURE_AREA_RATIO * page_area]
    merged = _merge_rects(raw)
    return [r for r in merged
            if _MIN_FIGURE_AREA <= r.get_area() < _MAX_FIGURE_AREA_RATIO * page_area]


def _crop_svg(page: "fitz.Page", rect: "fitz.Rect") -> str:
    """SVG d'une seule zone de page — `Page.get_svg_image()` ne prend pas de
    `clip` dans cette version de PyMuPDF : on recopie la zone dans une page
    temporaire à sa taille exacte via `show_pdf_page(clip=...)`."""
    tmp = fitz.open()
    tpage = tmp.new_page(width=rect.width, height=rect.height)
    tpage.show_pdf_page(tpage.rect, page.parent, page.number, clip=rect)
    svg = tpage.get_svg_image()
    tmp.close()
    return svg


def _extract_page_figures(db: Session, page: "fitz.Page", idx: int,
                          out_dir: Path) -> list[dict]:
    figs = []
    for fi, rect in enumerate(_figure_rects(page)):
        try:
            pix = page.get_pixmap(clip=rect, dpi=150)
            png_path = out_dir / f"p{idx}_f{fi}.png"
            pix.save(str(png_path))
            svg_path = out_dir / f"p{idx}_f{fi}.svg"
            svg_path.write_text(_crop_svg(page, rect), encoding="utf-8")
        except Exception as e:
            logger.warning("Sésamaths : figure page %s#%s non extraite : %s", idx, fi, e)
            continue
        fobj = FileObject(owner_type="sesamaths_figure", owner_id=f"{idx}:{fi}",
                          storage_path=str(png_path), mime="image/png",
                          size=png_path.stat().st_size)
        db.add(fobj)
        db.flush()
        figs.append({"file_object_id": fobj.id, "png_path": str(png_path),
                    "svg_path": str(svg_path), "bbox": list(rect)})
    return figs


def _save_master_pdf(db: Session, doc: "fitz.Document", manual: SesamathsManual,
                     chapter_code: str, start_idx: int, end_idx: int,
                     out_dir: Path) -> str:
    sub = fitz.open()
    sub.insert_pdf(doc, from_page=start_idx, to_page=end_idx)
    master_path = out_dir / "master.pdf"
    sub.save(str(master_path))
    sub.close()
    fobj = FileObject(owner_type="sesamaths_chapter",
                      owner_id=f"{manual.grade_level}:{chapter_code}",
                      storage_path=str(master_path), mime="application/pdf",
                      size=master_path.stat().st_size)
    db.add(fobj)
    db.flush()
    return fobj.id


def extract_chapter_raw(db: Session, doc: "fitz.Document", manual: SesamathsManual,
                        chapter_code: str, start_idx: int, end_idx: int) -> dict:
    """Texte (ordre de lecture) + figures + PDF maître du chapitre. Ne
    parcourt QUE la plage [start_idx, end_idx] — jamais le manuel entier."""
    out_dir = (settings.data_dir / "sesamaths" / manual.grade_level / chapter_code)
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = []
    for idx in range(start_idx, end_idx + 1):
        page = doc[idx]
        blocks = page.get_text("blocks")
        ordered = sorted(blocks, key=lambda b: (round(b[1] / 4) * 4, b[0]))
        text = "\n".join(b[4].strip() for b in ordered if b[4].strip())
        figures = _extract_page_figures(db, page, idx, out_dir)
        pages.append({"index": idx, "text": text, "figures": figures})

    master_id = _save_master_pdf(db, doc, manual, chapter_code, start_idx, end_idx, out_dir)
    return {"pages": pages, "master_pdf_file_object_id": master_id}

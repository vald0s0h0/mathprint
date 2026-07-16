"""Extraction PyMuPDF des manuels Sésamath (§ pipeline Sésamaths, refonte vision).

Repères de pagination : chaque manuel a une unique page "Sommaire" listant les
chapitres ET leurs Séries avec leur page IMPRIMÉE de départ. La page fichier
(1-indexée) = page imprimée + 2 (vérifié empiriquement sur le manuel 5e : "A1 …
3" dans le sommaire -> page fichier 5 = début réel du chapitre A1). La table des
matières donne donc, à elle seule, la plage de pages fichier de CHAQUE Série —
bien plus fiable que l'ancien balayage des pieds de page (superposition des
blocs de mise en page en extraction brute).

La refonte « vision » ne lit plus le texte des pages : chaque page d'exercices
est RENDUE en image (`render_page_png`) puis extraite par un LLM multimodal
(cf. services.sesamaths). Les figures géométriques dont un exercice a besoin
sont recadrées (`crop_bbox_png`) à partir des coordonnées relatives fournies
par le LLM, jamais devinées.

Seule la classe de 5e est couverte pour l'instant (`settings.sesamaths_manuals`) ;
un manuel absent ou illisible est journalisé et ne bloque jamais l'appelant.
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

_TOC_CHAPTER_RE = re.compile(r"^([A-E]\d)\s+(.+?)\s*\.{3,}\s*(\d+)\s*$")
# ligne de Série : les pointillés sont parfois absents (ex. « Série 2 Angles
# alternes-internes et correspondants  91 »), donc séparateur = pointillés
# ET/OU espaces avant le numéro de page imprimé.
_TOC_SERIES_RE = re.compile(r"^Série\s+(\d+)\s+(.+?)[.\s]+(\d+)\s*$")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# (chemin, mtime, taille) -> (sha256, Document). `open_manual` est appelé une
# fois par élève × compétence × tentative de remplissage lors d'une génération
# de sujet (des centaines de fois), et relisait à chaque appel les 43 Mo du
# manuel pour les hasher, en rouvrant un Document jamais refermé. Le manuel est
# livré avec le code et ne change qu'au déploiement : l'empreinte du fichier
# (mtime + taille) suffit à détecter un remplacement, et on ne repaie alors que
# ce cas-là.
_DOC_CACHE: dict[tuple, tuple[str, "fitz.Document"]] = {}


def _open_cached(path: Path) -> tuple[str, "fitz.Document"]:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _DOC_CACHE.get(key)
    if hit is not None:
        return hit
    # fichier remplacé : ne jeter que les versions périmées de CE manuel — les
    # autres niveaux (4e, 3e… quand ils arriveront) gardent le leur, sinon deux
    # niveaux utilisés en alternance se chasseraient l'un l'autre du cache
    for stale in [k for k in _DOC_CACHE if k[0] == str(path)]:
        _DOC_CACHE.pop(stale)
    entry = (_sha256_file(path), fitz.open(str(path)))
    _DOC_CACHE[key] = entry
    return entry


def get_or_create_manual(db: Session, grade_level: str) -> SesamathsManual:
    row = db.query(SesamathsManual).filter_by(grade_level=grade_level).first()
    if row is None:
        row = SesamathsManual(grade_level=grade_level, status="missing")
        db.add(row)
        db.flush()
    return row


def _is_culture(name: str) -> bool:
    return name.strip().lower().startswith("culture")


def parse_toc(doc: "fitz.Document") -> dict:
    """Cherche la page "Sommaire" (6 premières pages) et en extrait la carte
    complète du manuel : chapitres `^[A-E]\\d … page` ET leurs sous-lignes
    `Série N …`. Retourne
    {chapter_code: {"name": str, "start_printed_page": int,
                    "series": [{"number": int, "name": str,
                                "start_printed_page": int, "is_culture": bool}]}}.
    Les Séries sont rattachées au dernier chapitre rencontré (ordre de lecture)."""
    for i in range(min(6, len(doc))):
        text = doc[i].get_text()
        if "Sommaire" not in text:
            continue
        entries: dict = {}
        current: dict | None = None
        for line in text.splitlines():
            line = line.strip()
            mc = _TOC_CHAPTER_RE.match(line)
            if mc:
                code, name, page = mc.group(1), mc.group(2).strip(), int(mc.group(3))
                current = {"name": name, "start_printed_page": page, "series": []}
                entries[code] = current
                continue
            ms = _TOC_SERIES_RE.match(line)
            if ms and current is not None:
                current["series"].append({
                    "number": int(ms.group(1)),
                    "name": ms.group(2).strip(),
                    "start_printed_page": int(ms.group(3)),
                    "is_culture": _is_culture(ms.group(2)),
                })
        if entries:
            return entries
    raise ValueError("Page Sommaire introuvable ou vide")


def _resolve_manual_path(path_str: str) -> Path | None:
    """Localise le PDF du manuel de façon robuste (dev ET conteneur Docker).
    Essaie le chemin configuré tel quel, puis, à défaut, cherche le même nom de
    fichier dans des racines candidates connues — évite qu'un chemin absolu
    « figé » (ex. « /context/5.pdf » dans l'image, où _REPO_ROOT vaut « / »)
    fasse échouer une extraction alors que le fichier est bien livré."""
    p = Path(path_str)
    if p.exists():
        return p
    from ..config import _APP_DIR, _REPO_ROOT
    candidates = [
        _APP_DIR / "data" / "manuals" / p.name,   # livré avec le code (Docker)
        _REPO_ROOT / "context" / p.name,          # emplacement historique (dev)
        Path.cwd() / "context" / p.name,
        settings.data_dir / "manuals" / p.name,   # dépôt manuel sur le volume
    ]
    for cand in candidates:
        if cand.exists():
            logger.info("Sésamaths : manuel résolu sur %s (chemin configuré %s absent)",
                        cand, p)
            return cand
    return None


def open_manual(db: Session, grade_level: str) -> tuple["fitz.Document | None", SesamathsManual]:
    """Ouvre le manuel PDF du niveau demandé. Si absent/illisible : journalise
    l'erreur, marque `SesamathsManual.status`, retourne (None, manual) —
    jamais d'exception (un manuel manquant ne doit jamais casser la génération
    de sujet, cf. contraintes Sésamaths)."""
    manual = get_or_create_manual(db, grade_level)
    path_str = settings.sesamaths_manuals.get(grade_level)
    if not path_str:
        manual.status = "missing"
        manual.error_message = f"Aucun manuel Sésamaths configuré pour le niveau {grade_level}"
        logger.error("Sésamaths : %s", manual.error_message)
        db.commit()
        return None, manual

    path = _resolve_manual_path(path_str)
    if path is None:
        manual.status = "missing"
        manual.error_message = (
            f"PDF du manuel Sésamath {grade_level} introuvable (configuré : "
            f"{path_str}). Les exercices n'ont pas pu être extraits.")
        logger.error("Sésamaths : %s", manual.error_message)
        db.commit()
        return None, manual

    try:
        sha, doc = _open_cached(path)
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


def chapter_page_range(doc: "fitz.Document", toc: dict, chapter_code: str) -> tuple[int, int]:
    """Bornes fichier (0-indexées, incluses) du chapitre `chapter_code`,
    déduites de la table des matières (+2). Conservé pour compatibilité et
    contrôle : les plages de Séries (`series_page_ranges`) sont désormais le
    point d'entrée de l'extraction."""
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
    return max(0, start_idx), min(len(doc) - 1, end_idx)


def series_page_ranges(doc: "fitz.Document", toc: dict, chapter_code: str) -> list[dict]:
    """Plages de pages fichier (0-indexées, incluses) de chaque Série NON-Culture
    du chapitre. Chaque Série va de sa page de départ jusqu'à la veille de la
    Série suivante (dernière Série : jusqu'à la fin du chapitre). Les rubriques
    « Culture » sont exclues (cf. contraintes Sésamaths).

    Retourne [{"number", "name", "start_index", "end_index"}] trié par page."""
    if chapter_code not in toc:
        raise ValueError(f"Chapitre {chapter_code} absent de la table des matières")
    _, chapter_end_idx = chapter_page_range(doc, toc, chapter_code)
    series = sorted((toc[chapter_code].get("series") or []),
                    key=lambda s: s["start_printed_page"])
    out: list[dict] = []
    for i, s in enumerate(series):
        start_idx = s["start_printed_page"] + PAGE_OFFSET - 1
        if i + 1 < len(series):
            end_idx = series[i + 1]["start_printed_page"] + PAGE_OFFSET - 2
        else:
            end_idx = chapter_end_idx
        start_idx = max(0, start_idx)
        end_idx = min(len(doc) - 1, end_idx)
        if s.get("is_culture") or end_idx < start_idx:
            continue
        out.append({"number": s["number"], "name": s["name"],
                    "start_index": start_idx, "end_index": end_idx})
    return out


def chapter_exercise_pages(doc: "fitz.Document", toc: dict, chapter_code: str) -> list[dict]:
    """Liste ordonnée et dédupliquée des pages fichier d'exercices d'un chapitre
    (toutes Séries non-Culture confondues), chacune annotée de sa Série.
    Retourne [{"index", "series_number", "series_name", "is_geometry_hint"}]."""
    pages: dict[int, dict] = {}
    for sr in series_page_ranges(doc, toc, chapter_code):
        for idx in range(sr["start_index"], sr["end_index"] + 1):
            pages.setdefault(idx, {"index": idx, "series_number": sr["number"],
                                   "series_name": sr["name"]})
    return [pages[k] for k in sorted(pages)]


def render_page_png(doc: "fitz.Document", idx: int, dpi: int = 200) -> bytes:
    """Rendu PNG d'une page complète (aperçu/diagnostic). 200 dpi = bon
    compromis lisibilité des tableaux/figures vs. taille de l'image."""
    return doc[idx].get_pixmap(dpi=dpi).tobytes("png")


def extract_page_range_pdf(doc: "fitz.Document", start_index: int, end_index: int) -> bytes:
    """Construit un PDF autonome (bytes) ne contenant QUE les pages
    [start_index, end_index] (0-indexées, incluses) de `doc` — on n'envoie à
    l'OCR Mistral que la plage utile (une Série), jamais le manuel entier."""
    sub = fitz.open()
    try:
        sub.insert_pdf(doc, from_page=start_index, to_page=end_index)
        return sub.tobytes()
    finally:
        sub.close()


def crop_bbox_png(doc: "fitz.Document", idx: int, bbox_pct: list[float],
                  out_path: Path, dpi: int = 200) -> bool:
    """Recadre une zone de la page `idx` (coordonnées relatives 0-1
    [x0, y0, x1, y1] fournies par le LLM) et l'enregistre en PNG. Retourne
    False si la bbox est invalide/dégénérée (figure alors abandonnée)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox_pct)
    except (TypeError, ValueError):
        return False
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return False
    page = doc[idx]
    r = page.rect
    clip = fitz.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height,
                     r.x0 + x1 * r.width, r.y0 + y1 * r.height)
    try:
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
    except Exception as e:
        logger.warning("Sésamaths : recadrage figure page %s échoué : %s", idx, e)
        return False
    return True

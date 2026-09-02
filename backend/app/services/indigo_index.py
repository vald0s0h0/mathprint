"""Indigo — INDEX du manuel : supprime la saisie manuelle des plages.

L'assistant d'extraction demandait, pour CHAQUE compétence, trois plages à
relever à la main dans deux PDF de 161 et 216 pages : pages élève, pages prof,
numéros d'exercices. C'était le vrai coût de l'onglet — et l'OCR des mêmes pages
était repayé à chaque relance.

Ce module lit les DEUX manuels UNE fois et en tire un index déterministe :

  • manuel ÉLÈVE — aucune couche texte (161 pages d'images) : OCR Mistral par
    petits lots, écriture INCRÉMENTALE sur disque. Une construction interrompue
    reprend où elle s'était arrêtée, et une extraction ultérieure lit l'index au
    lieu de rappeler (et repayer) l'OCR.
  • manuel PROF — il a, lui, une couche texte : les corrigés se lisent
    GRATUITEMENT avec PyMuPDF, sans un centime d'OCR. Sa mise en page est très
    régulière : le numéro d'exercice est un petit bloc isolé dans la GOUTTIÈRE
    d'une colonne, à la même hauteur que le premier bloc de son corrigé. On
    apparie les deux et on préfixe le corrigé de son numéro — les blocs
    ressortent alors dans le vocabulaire de Mistral, et tout l'aval
    (`_order_blocks`, `_leading_num`, `_segment_corrections_by_numbers`)
    fonctionne sans savoir d'où ils viennent.

De l'index on tire ensuite, pour une compétence donnée, ses pages et ses numéros
d'exercices : `resolve_targets` produit exactement la structure `targets_json`
que consomme déjà `indigo._process_target`. L'assistant n'a plus qu'à demander
QUELLES compétences.
"""
from __future__ import annotations

import json
import logging
import re

from ..config import settings
from ..models import Competency, CompetencyFramework
from . import indigo_manual, providers

logger = logging.getLogger("app.indigo")

INDEX_VERSION = 1
# Pages par appel OCR. Le manuel élève pèse ~1,25 Mo par page et part en base64
# dans le corps de la requête : au-delà de 5 pages, la charge dépasse 10 Mo et
# les délais s'allongent au point de faire échouer le lot entier.
OCR_CHUNK = 5

# Un numéro d'exercice du manuel PROF : un bloc qui ne contient QUE ce nombre,
# et qui est étroit (il tient dans la gouttière, jamais toute une colonne).
_NUM_ONLY_RE = re.compile(r"^\s*(\d{1,3})\s*$")
_MARKER_MAX_WIDTH_PT = 45.0
# Tolérance d'appariement numéro <-> corps du corrigé, en points PDF : même
# hauteur de ligne à la ligne de base près, et corps immédiatement à droite.
_PAIR_DY_PT = 8.0
_PAIR_DX_PT = 45.0
# En-tête courant des pages du livre du professeur.
_CHAPTER_RE = re.compile(r"Chapitre\s+(\d+)\s+(.+)")
# Écart maximal entre deux numéros d'exercice consécutifs (même règle que
# indigo._SEQ_GAP : couvre un numéro raté sans avaler une sous-question).
_SEQ_GAP = 6


# --------------------------------------------------------------- stockage

def index_dir(grade: str):
    d = settings.data_dir / "indigo" / "index" / grade
    d.mkdir(parents=True, exist_ok=True)
    return d


def index_path(grade: str, which: str):
    return index_dir(grade) / f"{which}.json"


def _fingerprint(grade: str, which: str) -> str:
    """Empreinte du PDF source. Un manuel remplacé invalide l'index plutôt que
    de servir des pages qui ne sont plus les bonnes."""
    from .sesamaths_pdf import _open_cached
    path = indigo_manual.manual_path(grade, which)
    if path is None:
        return ""
    sha, _doc = _open_cached(path)
    return sha


def load(grade: str, which: str) -> dict | None:
    """Index sur disque, ou None s'il manque, est illisible, ou ne correspond
    plus au PDF présent."""
    path = index_path(grade, which)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Indigo/index : %s illisible — index ignoré", path)
        return None
    if data.get("version") != INDEX_VERSION:
        return None
    fp = _fingerprint(grade, which)
    if fp and data.get("sha256") and data["sha256"] != fp:
        logger.info("Indigo/index : le manuel %s %s a changé — index périmé", grade, which)
        return None
    return data


def _save(grade: str, which: str, data: dict) -> None:
    index_path(grade, which).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _empty(grade: str, which: str, page_count: int) -> dict:
    return {"version": INDEX_VERSION, "grade_level": grade, "which": which,
            "sha256": _fingerprint(grade, which), "page_count": page_count,
            "pages": {}}


def page_entry(grade: str, which: str, page_index: int) -> dict | None:
    """Page indexée {source_page, dims, blocks}, ou None. Sert à `indigo._ocr_pages`
    pour ne jamais repayer l'OCR d'une page déjà lue."""
    data = load(grade, which)
    if not data:
        return None
    return (data.get("pages") or {}).get(str(page_index))


# ------------------------------------------------------- manuel professeur

def correction_blocks(doc, idx: int) -> list[dict]:
    """Blocs d'une page du manuel PROF (cf. `correction_page`)."""
    return correction_page(doc, idx)[0]


def correction_page(doc, idx: int) -> tuple[list[dict], list[int]]:
    """(blocs, numéros d'exercices) d'une page du manuel PROF : le numéro est
    REMIS EN TÊTE de son corrigé, au format des blocs Mistral.

    Sans cet appariement, la couche texte est inexploitable : le numéro vit dans
    son propre bloc de gouttière, et `_leading_num` ne verrait jamais que des
    corrigés anonymes (mesuré : 1 numéro reconnu sur ~20). Avec, la page se
    découpe exactement comme une page OCRisée.

    Les numéros rendus sont ceux RÉELLEMENT APPARIÉS, et eux seuls. Les relire
    ensuite avec `_leading_num` sur tous les blocs ramasserait les résultats de
    calcul en tête de ligne (« 756 », « 441 »… — mesuré : 36 « numéros » pour 13
    exercices sur une seule page), et le choix des pages de corrigés partirait de
    travers. Un bloc où PyMuPDF a déjà fondu le numéro et son corrigé ne compte
    donc pas ici — il reste parfaitement exploitable en aval, où
    `_segment_corrections_by_numbers` le filtre par la plage attendue."""
    raw = list(doc[idx].get_text("blocks"))
    markers: list[tuple] = []
    bodies: list[tuple] = []
    for b in raw:
        content = str(b[4] or "").strip()
        if not content:
            continue
        if _NUM_ONLY_RE.match(content) and (b[2] - b[0]) < _MARKER_MAX_WIDTH_PT:
            markers.append(b)
        else:
            bodies.append(b)

    # Un marqueur non apparié est un LABEL DE FIGURE (une cote « 36 » posée sur
    # un schéma), pas un numéro d'exercice : il est simplement écarté.
    number_of: dict[int, str] = {}
    for m in markers:
        for i, b in enumerate(bodies):
            if i in number_of:
                continue
            if (abs(b[1] - m[1]) <= _PAIR_DY_PT and b[0] > m[0]
                    and b[0] - m[2] < _PAIR_DX_PT):
                number_of[i] = _NUM_ONLY_RE.match(str(m[4]).strip()).group(1)
                break

    scale = indigo_manual.RASTER_DPI / 72.0
    out: list[dict] = []
    numbers: list[int] = []
    for i, b in enumerate(bodies):
        content = str(b[4]).strip()
        num = number_of.get(i)
        if num:
            content = f"{num} {content}"
            numbers.append(int(num))
        out.append({"type": "text", "content": content,
                    "top_left_x": b[0] * scale, "top_left_y": b[1] * scale,
                    "bottom_right_x": b[2] * scale, "bottom_right_y": b[3] * scale})
    return out, sorted(set(numbers))


def page_chapter(blocks: list[dict]) -> str:
    """Chapitre lu dans l'en-tête courant (« Chapitre 4 Équations »), ou "".

    Les numéros d'exercices REPARTENT À 1 à chaque chapitre : sans cette
    information, un corrigé n°12 du chapitre 4 serait apparié à l'exercice n°12
    du chapitre 1."""
    for b in blocks:
        for line in str(b.get("content") or "").split("\n"):
            m = _CHAPTER_RE.search(line)
            if m:
                return m.group(2).strip()
    return ""


# --------------------------------------------------------------- construction

def build(db, grade: str, progress_cb) -> dict:
    """Construit (ou complète) l'index des deux manuels. Retourne un récapitulatif.

    REPRENABLE : chaque lot est écrit sur disque dès qu'il est lu. Une extraction
    interrompue (réseau, plafond de dépense, redémarrage) reprend aux pages
    manquantes, sans repayer celles qui sont déjà là."""
    stats = {}
    prof = indigo_manual.open_doc(grade, "prof")
    if prof is not None:
        stats["prof"] = _build_prof(grade, prof, progress_cb)
    else:
        progress_cb("⚠ Manuel prof introuvable : les corrigés manqueront.")
        stats["prof"] = 0

    eleve = indigo_manual.open_doc(grade, "eleve")
    if eleve is None:
        raise RuntimeError(
            f"Manuel élève {grade} introuvable — vérifie settings.indigo_manuals "
            f"(le PDF reste local à l'instance admin, non livré dans l'image).")
    stats["eleve"] = _build_eleve(db, grade, eleve, progress_cb)
    return stats


def _build_prof(grade: str, doc, progress_cb) -> int:
    """Index du manuel PROF — GRATUIT (couche texte, aucun appel OCR)."""
    data = load(grade, "prof") or _empty(grade, "prof", doc.page_count)
    data["page_count"] = doc.page_count
    pages = data.setdefault("pages", {})
    todo = [i for i in range(doc.page_count) if str(i) not in pages]
    if not todo:
        progress_cb(f"Index prof déjà complet ({len(pages)} pages).")
        return len(pages)
    progress_cb(f"Index du manuel prof : {len(todo)} page(s) à lire "
                f"(couche texte, aucun appel OCR)…")
    for i in todo:
        blocks, numbers = correction_page(doc, i)
        pages[str(i)] = {"source_page": i, "dims": indigo_manual.page_dims(doc, i),
                         "blocks": blocks, "chapter": page_chapter(blocks),
                         "numbers": numbers}
    _save(grade, "prof", data)
    progress_cb(f"Index prof : {len(pages)} page(s) indexée(s), 0 €.")
    return len(pages)


def _build_eleve(db, grade: str, doc, progress_cb) -> int:
    """Index du manuel ÉLÈVE — OCR Mistral par lots, repris s'il s'interrompt."""
    from .indigo import _ocr_pages         # import tardif : cycle indigo <-> index

    data = load(grade, "eleve") or _empty(grade, "eleve", doc.page_count)
    data["page_count"] = doc.page_count
    pages = data.setdefault("pages", {})
    todo = [i for i in range(doc.page_count) if str(i) not in pages]
    if not todo:
        progress_cb(f"Index élève déjà complet ({len(pages)} pages) — "
                    f"aucun OCR à repayer.")
        return len(pages)
    progress_cb(f"Index du manuel élève : {len(todo)} page(s) à OCRiser "
                f"(≈ {len(todo) * 0.004:.2f} $, une seule fois).")
    for start in range(0, len(todo), OCR_CHUNK):
        chunk = todo[start:start + OCR_CHUNK]
        try:
            ocr = _ocr_pages(db, doc, chunk, f"index-{grade}", use_index=False)
        except providers.BudgetExceeded as e:
            progress_cb(f"⛔ Index élève ARRÊTÉ : {e}. Les {len(pages)} page(s) "
                        f"déjà lues sont conservées — relance pour reprendre.")
            break
        for entry in ocr:
            pages[str(entry["source_page"])] = {
                "source_page": entry["source_page"], "dims": entry["dims"],
                "blocks": entry["blocks"], "numbers": _page_numbers(entry["blocks"])}
        _save(grade, "eleve", data)         # écriture INCRÉMENTALE : reprise possible
        progress_cb(f"Index élève : {len(pages)}/{doc.page_count} page(s)…")
    return len(pages)


def _page_numbers(blocks: list[dict]) -> list[int]:
    """Numéros d'exercice repérés sur une page, dans l'ordre de lecture.

    Rendu à titre de RÉSUMÉ (couverture affichée dans l'onglet) : la vérité
    reste les blocs, que `resolve_targets` relit avec la règle de croissance."""
    from .indigo import _leading_num, _order_blocks
    width = 0.0
    for b in blocks:
        width = max(width, float(b.get("bottom_right_x") or 0))
    out = []
    for b in _order_blocks(list(blocks), width):
        n = _leading_num(b.get("content"))
        if n is not None and n not in out:
            out.append(n)
    return out


# ------------------------------------------------------------ exploitation

def _competencies(db, grade: str) -> list[Competency]:
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade).first()
    if fw is None:
        return []
    return (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.order_index).all())


def _eleve_sections(db, grade: str) -> dict[str, dict]:
    """{code de compétence -> {pages: [...], numbers: [...]}} lu dans l'index élève.

    Une SECTION s'ouvre sur un bloc-titre qui correspond au libellé d'une
    compétence (même reconnaissance que le découpage : `indigo._match_competency`)
    et court jusqu'au titre de la compétence suivante. Les numéros y sont
    acceptés par CROISSANCE STRICTE bornée, exactement comme dans le découpage
    géométrique — un « 1. » de sous-question ou un nombre au fil du texte n'ouvre
    pas d'exercice."""
    from .indigo import _SKIP_BLOCKS, _leading_num, _match_competency, _order_blocks

    data = load(grade, "eleve")
    if not data:
        return {}
    comps = _competencies(db, grade)
    out: dict[str, dict] = {}
    current: Competency | None = None
    last = 0
    for idx in sorted((int(k) for k in (data.get("pages") or {})),):
        page = data["pages"][str(idx)]
        blocks = [b for b in page.get("blocks") or []
                  if b.get("type") not in _SKIP_BLOCKS]
        width = float((page.get("dims") or {}).get("width") or 0)
        for b in _order_blocks(blocks, width):
            n = _leading_num(b.get("content"))
            if b.get("type") == "title" and n is None:
                match = _match_competency(str(b.get("content") or ""), comps)
                if match is not None:
                    current, last = match, 0
                continue
            if current is None or n is None:
                continue
            if last == 0 or last < n <= last + _SEQ_GAP:
                last = n
                sec = out.setdefault(current.code, {"pages": [], "numbers": []})
                if idx not in sec["pages"]:
                    sec["pages"].append(idx)
                if n not in sec["numbers"]:
                    sec["numbers"].append(n)
    for sec in out.values():
        sec["pages"].sort()
        sec["numbers"].sort()
    return out


def _prof_pages_for(grade: str, chapter_name: str, numbers: set[int]) -> list[int]:
    """Pages du manuel prof portant les corrigés de `numbers`, restreintes au
    CHAPITRE (les numéros repartent à 1 d'un chapitre à l'autre)."""
    data = load(grade, "prof")
    if not data or not numbers:
        return []
    wanted = _fold(chapter_name)
    pages = []
    for key, page in (data.get("pages") or {}).items():
        chapter = _fold(page.get("chapter") or "")
        if wanted and chapter and wanted not in chapter and chapter not in wanted:
            continue
        if set(page.get("numbers") or []) & numbers:
            pages.append(int(key))
    return sorted(pages)


def _fold(s: str) -> str:
    from .indigo import _fold as fold
    return fold(s)


def coverage(db, grade: str) -> dict:
    """Ce que l'index sait, par compétence — affiché dans l'assistant pour que
    l'admin voie AVANT de lancer ce qui sera extrait (et ce qui manque)."""
    eleve = load(grade, "eleve")
    prof = load(grade, "prof")
    sections = _eleve_sections(db, grade)
    comps = _competencies(db, grade)
    rows = []
    for c in comps:
        sec = sections.get(c.code)
        numbers = sec["numbers"] if sec else []
        rows.append({
            "competency_id": c.id, "code": c.code, "short_id": c.short_id,
            "label": c.label, "chapter_name": c.chapter_name,
            "pages": sec["pages"] if sec else [],
            "numbers": numbers,
            "prof_pages": _prof_pages_for(grade, c.chapter_name, set(numbers)),
        })
    return {
        "grade_level": grade,
        "eleve": {"indexed": len((eleve or {}).get("pages") or {}),
                  "total": (eleve or {}).get("page_count", 0)},
        "prof": {"indexed": len((prof or {}).get("pages") or {}),
                 "total": (prof or {}).get("page_count", 0)},
        "competencies": rows,
    }


def resolve_targets(db, grade: str, competency_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Cibles d'extraction déduites de l'index. Retourne (cibles, non couvertes).

    Le format des cibles est EXACTEMENT `targets_json` (`eleve_pages`,
    `prof_pages`, `numbers`, index de page 0-based) : la pipeline d'extraction
    ne sait pas si les plages viennent de l'index ou de la saisie manuelle."""
    sections = _eleve_sections(db, grade)
    by_id = {c.id: c for c in _competencies(db, grade)}
    targets, missing = [], []
    for cid in competency_ids:
        comp = by_id.get(cid)
        if comp is None:
            missing.append(cid)
            continue
        sec = sections.get(comp.code)
        if not sec or not sec["pages"] or not sec["numbers"]:
            missing.append(comp.short_id or comp.code)
            continue
        numbers = sec["numbers"]
        targets.append({
            "competency_id": comp.id,
            "eleve_pages": list(sec["pages"]),
            "prof_pages": _prof_pages_for(grade, comp.chapter_name, set(numbers)),
            "numbers": list(numbers),
            # plages textuelles, pour que l'extraction reste LISIBLE dans le
            # journal et rejouable à la main si l'index se trompe
            "eleve_page_range": f"{sec['pages'][0] + 1}-{sec['pages'][-1] + 1}",
            "number_range": f"{numbers[0]}-{numbers[-1]}",
        })
    return targets, missing

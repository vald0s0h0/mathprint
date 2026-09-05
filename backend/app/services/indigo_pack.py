"""Indigo — PACK DE TRAVAIL : fabriquer des exercices sans les PDF des manuels.

Les deux manuels (203 Mo + 34 Mo, sous droits) restent LOCAUX à la machine qui
les possède : ils ne sont pas dans le dépôt, donc pas dans l'image Docker (qui
ne copie que `backend/app`). Une instance déployée — le NAS du professeur — n'a
donc ni les PDF, ni l'index qui en est tiré, qui vit sur le volume de l'instance
qui l'a construit. L'onglet Exercices y est inutilisable : « Indexer » est grisé
et une extraction échouerait sur « Manuel élève introuvable ».

Le pack est le pont. Exporté UNE fois depuis la machine qui a les manuels,
importé sur l'instance qui fabrique, il porte tout ce dont la fabrication a
besoin — et rien de plus :

  • l'INDEX (blocs OCR du manuel élève, couche texte des corrigés du prof) :
    l'OCR est déjà payé, il ne sera jamais repayé ;
  • le RASTER de chaque page élève au DPI de travail, en JPEG.

Pourquoi les PAGES, et pas les exercices déjà découpés : tout l'aval — découpe
du crop, lecture CV de la couleur du badge, recadrage d'une figure, ajout d'une
figure sur un exercice qui n'en avait pas — passe par le seul
`indigo_manual.raster_page(doc, i)`. En livrant les pages, le pack se substitue
au PDF à cet unique endroit ; rien d'autre dans la pipeline ne sait qu'il
travaille sans manuel.

Pourquoi du JPEG 4:4:4 et pas le réglage par défaut : la difficulté d'un
exercice se LIT DANS UNE COULEUR (badge teal/orange/rouge, et surtout le fond
vert d'eau « expert », reconnu à ±16 par canal — cf. indigo_cv). Le
sous-échantillonnage de chrominance habituel (4:2:0) fait dériver ces aplats
jusqu'à 49 sur 255 : de quoi transformer un expert en exercice ordinaire. En
4:4:4 qualité 92, la dérive mesurée sur les zones plates — les seules que lit
le test du fond — tombe à 4, et la classification CV est identique à celle du
PDF (cf. tests/test_indigo_pack.py).
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2

from ..config import settings
from . import indigo_manual

logger = logging.getLogger("app.indigo")

PACK_VERSION = 1
MANIFEST = "pack.json"

# JPEG SANS sous-échantillonnage de chrominance : c'est la couleur qui porte la
# difficulté de l'exercice (cf. en-tête du module). Ne pas baisser sans rejouer
# test_le_pack_ne_change_pas_la_lecture_des_couleurs.
JPEG_QUALITY = 92
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY,
                cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]


def encode_page(bgr) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, _JPEG_PARAMS)
    if not ok:
        raise RuntimeError("Échec d'encodage JPEG d'une page du pack Indigo")
    return buf.tobytes()


# ----------------------------------------------------------------- stockage

def pack_dir(grade: str) -> Path:
    return settings.data_dir / "indigo" / "pack" / grade


def _manifest_path(grade: str) -> Path:
    return pack_dir(grade) / MANIFEST


def page_file(grade: str, idx: int) -> Path:
    return pack_dir(grade) / "pages" / f"{idx:04d}.jpg"


def manifest(grade: str) -> dict | None:
    """Manifeste du pack importé sur CETTE instance, ou None."""
    path = _manifest_path(grade)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Indigo/pack : %s illisible", path)
        return None
    return data if data.get("version") == PACK_VERSION else None


class PagePack:
    """Les pages du manuel élève, rendues d'avance — se substitue au PDF.

    N'expose QUE ce dont la pipeline a besoin d'un manuel élève : le nombre de
    pages et le raster d'une page. `indigo_manual.raster_page` le reconnaît à
    sa méthode `raster_page` (un `fitz.Document` n'en a pas) et l'utilise à la
    place du rendu PDF."""

    def __init__(self, grade: str, data: dict):
        self.grade_level = grade
        self.page_count = int(data.get("page_count") or 0)
        self.dpi = float(data.get("dpi") or indigo_manual.RASTER_DPI)
        self.built_at = data.get("built_at") or ""
        self.is_pack = True

    def raster_page(self, idx: int, dpi: float | None = None):
        """Raster BGR d'une page, remis à l'échelle si un autre DPI est demandé
        (les vignettes de l'assistant, par exemple)."""
        path = page_file(self.grade_level, idx)
        if not (0 <= idx < self.page_count) or not path.exists():
            raise RuntimeError(
                f"Page {idx + 1} absente du pack de travail {self.grade_level} — "
                f"le pack est incomplet, réimporte-le depuis l'instance qui porte "
                f"les manuels.")
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"Page {idx + 1} du pack illisible ({path.name})")
        if dpi and abs(dpi - self.dpi) > 0.5:
            scale = dpi / self.dpi
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        return img


def load(grade: str) -> PagePack | None:
    """Pack de pages installé sur cette instance, ou None."""
    data = manifest(grade)
    return PagePack(grade, data) if data else None


def status(grade: str) -> dict:
    """Ce que cette instance possède : le manuel lui-même, le pack, ou rien.

    C'est ce que l'onglet affiche pour que l'admin sache s'il peut EXPORTER
    (il a les PDF) ou s'il doit IMPORTER (il ne les a pas)."""
    from . import indigo_index
    pdf = indigo_manual.manual_path(grade, "eleve") is not None
    data = manifest(grade)
    pages = 0
    if data:
        folder = pack_dir(grade) / "pages"
        pages = len(list(folder.glob("*.jpg"))) if folder.is_dir() else 0
    eleve = indigo_index.load(grade, "eleve") or {}
    prof = indigo_index.load(grade, "prof") or {}
    return {
        "grade_level": grade,
        "has_manuals": pdf,
        "can_export": pdf,
        "pack": None if not data else {
            "page_count": int(data.get("page_count") or 0),
            "pages_present": pages,
            "built_at": data.get("built_at") or "",
            "source": data.get("source_instance") or "",
        },
        "index": {"eleve": len(eleve.get("pages") or {}),
                  "prof": len(prof.get("pages") or {})},
        # source effective du manuel élève pour la fabrication
        "source": "manuel" if pdf else ("pack" if data else "aucune"),
    }


# ---------------------------------------------------------------- export

def export_zip(grade: str, dest: Path, progress_cb=None) -> dict:
    """Écrit dans `dest` l'archive à porter sur l'instance qui fabrique.

    Rend les pages À LA VOLÉE depuis le PDF, directement dans l'archive : rien
    n'est mis en cache sur disque, la machine qui exporte a déjà les manuels.
    """
    from . import indigo_index

    def say(msg):
        if progress_cb:
            progress_cb(msg)

    doc = indigo_manual.open_pdf(grade, "eleve")
    if doc is None:
        raise RuntimeError(
            f"Manuel élève {grade} introuvable : seule une instance qui porte les "
            f"PDF peut exporter un pack de travail.")
    eleve = indigo_index.load(grade, "eleve")
    if not eleve or len(eleve.get("pages") or {}) < doc.page_count:
        got = len((eleve or {}).get("pages") or {})
        raise RuntimeError(
            f"Index élève incomplet ({got}/{doc.page_count} pages) : lance d'abord "
            f"« Indexer le manuel ». Sans l'index, le pack ne porterait aucun "
            f"énoncé et l'instance de destination devrait repayer l'OCR.")
    prof = indigo_index.load(grade, "prof")

    dest.parent.mkdir(parents=True, exist_ok=True)
    total = doc.page_count
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index/eleve.json", json.dumps(eleve, ensure_ascii=False))
        if prof:
            z.writestr("index/prof.json", json.dumps(prof, ensure_ascii=False))
        for idx in range(total):
            # les JPEG sont déjà compressés : les stocker tels quels évite de
            # relire 86 Mo pour ~1 % de gain
            z.writestr(zipfile.ZipInfo(f"pages/{idx:04d}.jpg"),
                       encode_page(indigo_manual.raster_page(doc, idx)),
                       compress_type=zipfile.ZIP_STORED)
            if idx % 20 == 19:
                say(f"Pack : {idx + 1}/{total} page(s) rendue(s)…")
        z.writestr(MANIFEST, json.dumps({
            "version": PACK_VERSION, "grade_level": grade,
            "dpi": indigo_manual.RASTER_DPI, "page_count": total,
            "jpeg_quality": JPEG_QUALITY,
            "source_sha256": eleve.get("sha256") or "",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "index": {"eleve": len(eleve.get("pages") or {}),
                      "prof": len((prof or {}).get("pages") or {})},
        }, ensure_ascii=False, indent=1))
    size = dest.stat().st_size
    say(f"Pack prêt : {total} page(s), {size / 1e6:.0f} Mo.")
    return {"pages": total, "bytes": size,
            "index": {"eleve": len(eleve.get("pages") or {}),
                      "prof": len((prof or {}).get("pages") or {})}}


def export_name(grade: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"indigo-pack-{grade}-{stamp}.zip"


# ---------------------------------------------------------------- import

# Emplacement conventionnel pour un dépôt À LA MAIN sur le volume (File Station
# du NAS) quand l'archive est trop grosse à envoyer confortablement par le
# navigateur : /data/indigo-pack.zip
DROP_NAME = "indigo-pack.zip"


def drop_path() -> Path:
    return settings.data_dir / DROP_NAME


def _safe_member(name: str) -> str | None:
    """Nom d'entrée d'archive accepté, ou None. Refuse tout ce qui sortirait du
    dossier de destination (« ../ », chemin absolu, lien)."""
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        return None
    parts = p.parts
    if not parts:
        return None
    if parts[0] == MANIFEST and len(parts) == 1:
        return name
    if parts[0] in ("pages", "index") and len(parts) == 2:
        return name
    return None


def import_zip(src, grade_hint: str = "3e") -> dict:
    """Installe un pack exporté : index + pages, sur le volume de CETTE instance.

    `src` est un chemin ou un objet fichier. L'archive est vérifiée AVANT
    d'écrire quoi que ce soit — un pack tronqué ou d'un autre niveau ne doit pas
    laisser l'instance à moitié équipée."""
    from . import indigo_index

    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if MANIFEST not in names:
            raise RuntimeError(
                "Ce fichier n'est pas un pack de travail Indigo (pack.json manquant).")
        data = json.loads(z.read(MANIFEST).decode("utf-8"))
        if data.get("version") != PACK_VERSION:
            raise RuntimeError(
                f"Pack de version {data.get('version')} : cette instance attend la "
                f"version {PACK_VERSION}. Mets à jour l'application, ou réexporte "
                f"le pack depuis une instance à jour.")
        grade = str(data.get("grade_level") or grade_hint)
        total = int(data.get("page_count") or 0)
        pages = [n for n in names if _safe_member(n) and n.startswith("pages/")]
        if len(pages) < total:
            raise RuntimeError(
                f"Pack incomplet : {len(pages)} page(s) sur {total} annoncées. "
                f"Le transfert est probablement tronqué — recommence.")

        base = pack_dir(grade)
        pages_dir = base / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        idx_dir = indigo_index.index_dir(grade)
        written = 0
        for name in names:
            safe = _safe_member(name)
            if safe is None or name.endswith("/"):
                continue
            payload = z.read(name)
            if name.startswith("pages/"):
                (pages_dir / Path(name).name).write_bytes(payload)
                written += 1
            elif name.startswith("index/"):
                (idx_dir / Path(name).name).write_bytes(payload)
        # le manifeste EN DERNIER : tant qu'il n'est pas là, `load` rend None et
        # l'instance ne croit pas disposer d'un pack à moitié écrit
        _manifest_path(grade).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    logger.info("Indigo/pack : %s page(s) installée(s) pour %s", written, grade)
    return {"grade_level": grade, "pages": written,
            "index": data.get("index") or {}, "built_at": data.get("built_at") or ""}


def clear(grade: str) -> None:
    """Retire le pack (pas l'index) — pour repartir d'un transfert propre."""
    shutil.rmtree(pack_dir(grade), ignore_errors=True)

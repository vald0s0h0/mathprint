"""File d'impression de l'overlay : quelle feuille, dans quel ordre, depuis qui.

Le PDF de correction contient une page par position de l'ADF (invariant de
pipeline.build_overlays) : sa k-ième page correspond donc à la k-ième page non
écartée du lot. La file s'en déduit sans lire le contenu du PDF, ce qui la rend
disponible aussi pour les overlays générés avant son introduction.

Une « feuille » est l'unité physique qu'on reprend après une interruption :
- sujet recto : une page d'overlay = une feuille ;
- sujet recto-verso : les deux faces d'une même feuille sont regroupées par
  (copie, numéro de feuille), même si l'ADF a produit tous les rectos puis tous
  les versos. Une face manquante (verso blanc non scanné) reste une face blanche
  à l'impression : sans elle, la feuille suivante recevrait ce verso.
"""
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Literal

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from reportlab.lib.units import mm
from sqlalchemy.orm import Session

from ..models import Copy, DocumentPage, ScanBatch, ScannedPage, Student

UNIDENTIFIED = "Non identifié"


class OverlayQueueError(Exception):
    """La file ne peut pas être établie : message destiné au professeur."""


@dataclass
class Sheet:
    student: str
    identified: bool
    stream_index: int                 # rang de la feuille dans le flux ADF
    recto: int | None = None          # index de page dans le PDF d'overlay
    verso: int | None = None
    page_nos: list[int] = field(default_factory=list)

    @property
    def pages_label(self) -> str:
        return f"p. {'-'.join(str(n) for n in sorted(self.page_nos))}" if self.page_nos else ""


def overlay_batch(db: Session, assessment_id: str) -> ScanBatch | None:
    """L'unique lot du sujet (scan_intake.get_or_create_batch garde le plus ancien)."""
    return (db.query(ScanBatch).filter_by(assessment_id=assessment_id)
            .order_by(ScanBatch.created_at.asc()).first())


def overlay_sides(db: Session, batch: ScanBatch, pdf_pages: int) -> list[dict]:
    """Une entrée par page du PDF d'overlay, dans l'ordre du PDF."""
    rows = (db.query(ScannedPage, DocumentPage, Student.name)
            .outerjoin(DocumentPage, ScannedPage.page_id == DocumentPage.id)
            .outerjoin(Copy, DocumentPage.copy_id == Copy.id)
            .outerjoin(Student, Copy.student_id == Student.id)
            .filter(ScannedPage.batch_id == batch.id, ScannedPage.dismissed.is_(False))
            .order_by(ScannedPage.source_index).all())
    if not rows:
        # Anciens lots simulés sans position persistée : pages sans identité.
        return [{"student": f"Page {i + 1}", "identified": False,
                 "copy_id": None, "page_no": None} for i in range(pdf_pages)]
    if len(rows) != pdf_pages:
        raise OverlayQueueError(
            "Le nombre de pages de l'overlay ne correspond plus aux scans : "
            "relancez sa génération depuis Corrections.")
    return [{"student": name or UNIDENTIFIED, "identified": bool(name),
             "copy_id": document_page.copy_id if document_page else None,
             "page_no": document_page.page_no if document_page else None}
            for _scanned, document_page, name in rows]


def group_sheets(sides: list[dict], *, duplex: bool) -> list[Sheet]:
    """Regroupe les pages d'overlay en feuilles physiques, dans l'ordre du flux."""
    sheets: list[Sheet] = []

    def new_sheet(side: dict) -> Sheet:
        sheet = Sheet(student=side["student"], identified=side["identified"],
                      stream_index=len(sheets))
        sheets.append(sheet)
        return sheet

    if not duplex:
        for index, side in enumerate(sides):
            sheet = new_sheet(side)
            sheet.recto = index
            if side["page_no"]:
                sheet.page_nos.append(side["page_no"])
        return sheets

    by_key: dict[tuple[str, int], Sheet] = {}
    for index, side in enumerate(sides):
        page_no = side["page_no"]
        if side["copy_id"] and page_no:
            face = "recto" if page_no % 2 else "verso"
            key = (side["copy_id"], (page_no + 1) // 2)
            sheet = by_key.get(key)
            # Face déjà prise : doublon dans la pile, donc une autre feuille.
            if sheet is None or getattr(sheet, face) is not None:
                sheet = by_key[key] = new_sheet(side)
            setattr(sheet, face, index)
            sheet.page_nos.append(page_no)
            continue
        # Face sans identité (verso blanc scanné, QR illisible) : elle complète
        # la feuille dont le recto vient juste de passer dans l'ADF.
        previous = sheets[-1] if sheets else None
        if previous and previous.verso is None and previous.recto == index - 1:
            previous.verso = index
        else:
            new_sheet(side).recto = index
    return sheets


def physical_queue(sheets: list[Sheet], *, adf_reversed: bool) -> list[Sheet]:
    """Feuilles dans l'ordre où l'imprimante les prendra dans son bac : l'ordre
    de la pile sortie de l'ADF, seule pile qui compte pour poser les overlays."""
    return list(reversed(sheets)) if adf_reversed else list(sheets)


def overlay_document(source: Path, sheets: list[Sheet],
                     side: Literal["all", "recto", "verso"], *, duplex: bool,
                     offset_mm: tuple[float, float] = (0.0, 0.0)) -> bytes:
    """PDF des seules feuilles demandées, dans l'ordre du flux ADF.

    `offset_mm` déplace les marques sur chaque face imprimée ; le format de la
    page ne change pas. L'appelant applique ensuite la compensation d'ordre,
    comme pour tout document.
    """
    reader = PdfReader(str(source))
    writer = PdfWriter()

    def add(index: int | None):
        if index is not None:
            page = reader.pages[index]
            if any(offset_mm):
                page.add_transformation(
                    Transformation().translate(offset_mm[0] * mm, offset_mm[1] * mm))
            writer.add_page(page)
        else:
            first = reader.pages[0]
            writer.add_page(PageObject.create_blank_page(
                width=first.mediabox.width, height=first.mediabox.height))

    for sheet in sorted(sheets, key=lambda value: value.stream_index):
        if side == "recto":
            add(sheet.recto)
        elif side == "verso":
            add(sheet.verso)
        else:
            add(sheet.recto)
            if duplex:
                add(sheet.verso)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()

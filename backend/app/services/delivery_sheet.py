"""Intercalaire A4 : contenu sur la moitié supérieure, seule cette feuille est pliée."""
from io import BytesIO
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from xml.sax.saxutils import escape
from pypdf import PdfReader, PdfWriter
from ..models import SchoolClass, School, TeacherProfile, Subject, Copy


def render_delivery_sheet(db, assessment, *, printed_at=None):
    cls = db.get(SchoolClass, assessment.class_id)
    school = db.get(School, cls.school_id) if cls and cls.school_id else None
    teacher = db.get(TeacherProfile, assessment.teacher_profile_id) if assessment.teacher_profile_id else None
    subject = db.get(Subject, assessment.subject_id or "math")
    zone = ZoneInfo("Africa/Abidjan") if school and school.country_id == "CI" else ZoneInfo("Europe/Paris")
    stamp = (printed_at or datetime.now(timezone.utc)).astimezone(zone)
    count = db.query(Copy).filter_by(assessment_id=assessment.id).count()
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4; left = 15 * mm; usable = w - 2 * left
    c.setTitle(f"Livraison - {assessment.title}")
    c.setFillColor(HexColor("#23394d")); c.rect(0, h - 23 * mm, w, 23 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff")); c.setFont("Helvetica-Bold", 15)
    c.drawString(left, h - 14 * mm, "KIRO & NINA  /  LIVRAISON")
    c.setFillColor(HexColor("#23394d"))
    style = ParagraphStyle("delivery", fontName="Helvetica", fontSize=10, leading=13, textColor=HexColor("#23394d"))
    y = h - 30 * mm
    rows = [("Établissement", school.name if school else "Sans établissement"),
            ("Professeur", teacher.name if teacher else "Non affecté"),
            ("Classe / matière", f"{cls.name if cls else ''} / {subject.name if subject else assessment.subject_id}"),
            ("Sujet", assessment.title),
            ("Impression", stamp.strftime("%d/%m/%Y à %H:%M") + f" ({zone.key})"),
            ("Lot", f"{count} copies élèves"),
            ("Référence", assessment.id)]
    for label, value in rows:
        paragraph = Paragraph(f"<b>{escape(label)}</b> : {escape(str(value))}", style)
        _, height = paragraph.wrap(usable, 500)
        paragraph.drawOn(c, left, y - height)
        y -= height + 3 * mm
    c.setFont("Helvetica", 8); c.setFillColor(HexColor("#687684"))
    c.drawString(left, h / 2 + 10 * mm, "Conserver cet intercalaire pour le retour des copies corrigées.")
    c.setStrokeColor(HexColor("#8b96a0")); c.setDash(3, 3)
    c.line(8 * mm, h / 2, w - 8 * mm, h / 2); c.setDash()
    c.drawCentredString(w / 2, h / 2 - 5 * mm, "PLIURE - CETTE FEUILLE UNIQUEMENT")
    c.showPage(); c.save(); return buf.getvalue()


def with_delivery_sheet(document: bytes, sheet: bytes, *, duplex=False, pass_side="all"):
    """Ordre logique final : intercalaire AU-DESSUS. Le pilote applique ensuite
    la compensation du chemin papier. En duplex, son verso est toujours vide.
    En deux passes, le verso vide occupe la même feuille que l'intercalaire.
    Les overlays ne passent jamais par cette fonction.
    """
    writer = PdfWriter()
    if pass_side == "verso": writer.add_blank_page(width=A4[0], height=A4[1])
    else: writer.add_page(PdfReader(BytesIO(sheet)).pages[0])
    if duplex and pass_side == "all": writer.add_blank_page(width=A4[0], height=A4[1])
    for page in PdfReader(BytesIO(document)).pages: writer.add_page(page)
    output = BytesIO(); writer.write(output); return output.getvalue()

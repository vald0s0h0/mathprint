"""Intercalaire A4 (professeurs accompagnés) : contenu sur la moitié inférieure,
texte pivoté de 90° (sens horaire), seule cette feuille est pliée."""
import hashlib
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from xml.sax.saxutils import escape
from pypdf import PdfReader, PdfWriter
from ..models import SchoolClass, School, TeacherProfile, Subject, Copy


_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONTS = {"KNSerif": "DMSerifDisplay-Regular.ttf", "KNSerifIt": "DMSerifDisplay-Italic.ttf",
          "KNSans": "FiraSans-Regular.ttf", "KNSansMd": "FiraSans-Medium.ttf",
          "KNSansSb": "FiraSans-SemiBold.ttf"}
# Une teinte par professeur (stable d'une impression à l'autre) : le tri des
# paquets se fait à la couleur avant même de lire le nom. Teintes foncées,
# lisibles aussi en niveaux de gris.
_ACCENTS = ["#c2552f", "#1f7a74", "#3d4fa8", "#8a3f7a", "#5f7a24", "#b07a12", "#2a6fa0", "#a33b4f"]


def _register_fonts():
    for name, filename in _FONTS.items():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(_FONT_DIR / filename)))


def _accent(key):
    return HexColor(_ACCENTS[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(_ACCENTS)])


def render_delivery_sheet(db, assessment, *, printed_at=None):
    cls = db.get(SchoolClass, assessment.class_id)
    school = db.get(School, cls.school_id) if cls and cls.school_id else None
    teacher = db.get(TeacherProfile, assessment.teacher_profile_id) if assessment.teacher_profile_id else None
    subject = db.get(Subject, assessment.subject_id or "math")
    zone = ZoneInfo("Africa/Abidjan") if school and school.country_id == "CI" else ZoneInfo("Europe/Paris")
    stamp = (printed_at or datetime.now(timezone.utc)).astimezone(zone)
    count = db.query(Copy).filter_by(assessment_id=assessment.id).count()
    _register_fonts()
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setTitle(f"Livraison - {assessment.title}")
    # Moitié inférieure uniquement, texte pivoté de 90° dans le sens horaire :
    # repère local (x vers le BAS de la page, y vers la DROITE), région L × H.
    # Aucun aplat (économie d'encre) : la couleur passe par le texte et des filets.
    L, H = h / 2, w
    c.saveState(); c.translate(0, h / 2); c.rotate(-90)
    x0 = 16 * mm; right = L - 14 * mm; usable = right - x0
    name = teacher.name if teacher else "Non affecté"
    accent = _accent(teacher.id if teacher else name)
    ink, grey, rule = HexColor("#16202a"), HexColor("#6b7782"), HexColor("#cfd4d9")

    def fit(text, font, size, min_size, width=usable):
        while size > min_size and c.stringWidth(text, font, size) > width:
            size -= 1
        return size

    # Filet d'accent vertical le long du bord gauche du bloc (repère de couleur).
    c.setStrokeColor(accent); c.setLineWidth(2.2)
    c.line(x0 - 6 * mm, 22 * mm, x0 - 6 * mm, H - 14 * mm)

    # En-tête : marque + nature du document
    y = H - 18 * mm
    c.setFillColor(accent); c.setFont("KNSerif", 13); c.drawString(x0, y, "Kiro & Nina")
    c.setFillColor(grey); c.setFont("KNSansMd", 7.5)
    c.drawRightString(right, y + 0.5, "LIVRAISON DES SUJETS")
    y -= 4 * mm
    c.setStrokeColor(rule); c.setLineWidth(0.5); c.line(x0, y, right, y)

    # 1. Professeur : le plus grand, en serif
    style = ParagraphStyle("name", fontName="KNSerif", fontSize=fit(name, "KNSerif", 50, 32),
                           textColor=ink)
    style.leading = style.fontSize * 1.0
    para = Paragraph(escape(name), style); _, ph = para.wrap(usable, 500)
    y -= 8 * mm
    para.drawOn(c, x0, y - ph); y -= ph + 9 * mm

    # 2. Classe (encadrée, couleur du professeur) + matière à côté
    cname = cls.name if cls else "-"
    csize = fit(cname, "KNSansSb", 26, 16, usable * 0.6)
    tw = c.stringWidth(cname, "KNSansSb", csize)
    bh = csize * 1.45; pad = csize * 0.45
    c.setStrokeColor(accent); c.setLineWidth(1.4)
    c.roundRect(x0, y - bh, tw + 2 * pad, bh, bh / 2.2, stroke=1, fill=0)
    c.setFillColor(accent); c.setFont("KNSansSb", csize)
    c.drawString(x0 + pad, y - bh / 2 - csize * 0.34, cname)
    sname = subject.name if subject else assessment.subject_id
    c.setFont("KNSans", 15); c.setFillColor(ink)
    c.drawString(x0 + tw + 2 * pad + 5 * mm, y - bh / 2 - 15 * 0.34, sname)
    y -= bh + 6 * mm
    c.setFont("KNSans", 11); c.setFillColor(grey)
    c.drawString(x0, y - 11 * 0.8, school.name if school else "Sans établissement")
    y -= 11 * mm

    # 3. Sujet, en italique
    c.setStrokeColor(rule); c.setLineWidth(0.5); c.line(x0, y, right, y); y -= 7 * mm
    c.setFont("KNSansMd", 7.5); c.setFillColor(accent); c.drawString(x0, y, "SUJET"); y -= 3 * mm
    st = ParagraphStyle("subj", fontName="KNSerifIt", fontSize=19, leading=22, textColor=ink)
    para = Paragraph(escape(assessment.title), st); _, ph = para.wrap(usable, 500)
    para.drawOn(c, x0, y - ph); y -= ph + 9 * mm

    # 4. Logistique : trois colonnes, chiffre clé en grand
    c.setStrokeColor(rule); c.line(x0, y, right, y); y -= 7 * mm
    cols = [("COPIES", str(count), "KNSerif", 24),
            ("IMPRIMÉ LE", stamp.strftime("%d/%m/%Y"), "KNSansMd", 12),
            ("RÉFÉRENCE", assessment.id[:8], "KNSansMd", 12)]
    col_w = usable / 3
    for i, (lab, val, font, size) in enumerate(cols):
        cx = x0 + i * col_w
        c.setFont("KNSansMd", 7.5); c.setFillColor(grey); c.drawString(cx, y, lab)
        c.setFont(font, size); c.setFillColor(accent if i == 0 else ink)
        c.drawString(cx, y - 4 * mm - size * 0.75, val)
    c.setFont("KNSans", 8.5); c.setFillColor(grey)
    c.drawString(x0 + col_w, y - 4 * mm - 12 * 0.75 - 4.5 * mm,
                 stamp.strftime("à %H:%M") + f" · {zone.key}")

    # Pied : consigne
    c.setFont("KNSerifIt", 10.5); c.setFillColor(grey)
    c.drawString(x0, 16 * mm, "À conserver pour le retour des copies corrigées.")
    c.setFont("KNSans", 6.5); c.drawRightString(right, 16 * mm, assessment.id)
    c.restoreState()
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

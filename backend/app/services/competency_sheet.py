"""Liste destinée aux professeurs : partie commune puis compléments du parcours."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from ..models import Competency, Program, Subject, CurriculumPath
from .education import GRADE_NAMES


def render_competency_sheet(db, framework):
    rows = db.query(Competency).filter_by(framework_id=framework.id).order_by(Competency.order_index, Competency.id).all()
    program = db.get(Program, framework.program_id)
    subject = db.get(Subject, framework.subject_id)
    path = db.get(CurriculumPath, framework.path_id) if framework.path_id else None
    styles = getSampleStyleSheet()
    title = f"{subject.name if subject else framework.subject_id} · {GRADE_NAMES.get(framework.grade_level, framework.grade_level)}"
    context = f"{program.name if program else framework.program_id}" + (f" · {path.name}" if path else "")
    story = [Paragraph(escape(title), styles['Title']), Paragraph(escape(context), styles['Heading2']),
             Paragraph(escape(f"{framework.name} · version {framework.version}"), styles['Normal']), Spacer(1, 6*mm)]
    for section, label in [('common', 'Compétences communes'), ('complement', 'Compléments de la série ou de l’enseignement')]:
        selected = [c for c in rows if (c.section or 'common') == section]
        if not selected: continue
        if section == 'complement' and path: label = f"Compléments · {path.name}"
        story.append(Paragraph(escape(label), styles['Heading2']))
        current = None
        for c in selected:
            chapter = (c.domain_code, c.chapter_code)
            if chapter != current:
                story.append(Paragraph(escape(' · '.join(v for v in (c.domain_name, c.chapter_name) if v) or 'Compétences'), styles['Heading3']))
                current = chapter
            table = Table([[Paragraph(escape(c.short_id or c.code), styles['Normal']),
                            Paragraph(escape(c.label), styles['Normal'])]], colWidths=[24*mm, 156*mm])
            table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),0.3,colors.HexColor('#dce2e8')),
                                       ('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
            story.append(table)
    def footer(canvas, doc):
        canvas.setFont('Helvetica',8)
        canvas.drawString(15*mm,10*mm,'Kiro & Nina · Liste des compétences')
        canvas.drawRightString(A4[0]-15*mm,10*mm,str(doc.page))
    buf=BytesIO()
    SimpleDocTemplate(buf,pagesize=A4,leftMargin=15*mm,rightMargin=15*mm,topMargin=15*mm,bottomMargin=18*mm,
                      title=title).build(story,onFirstPage=footer,onLaterPages=footer)
    return buf.getvalue()

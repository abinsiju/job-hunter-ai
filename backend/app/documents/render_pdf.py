from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from app.documents.model import CoverLetter, Resume

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=13)
NAME = ParagraphStyle("name", parent=BASE, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=TA_CENTER)
CONTACT = ParagraphStyle("contact", parent=BASE, alignment=TA_CENTER, spaceAfter=4)
SEC = ParagraphStyle("sec", parent=BASE, fontName="Helvetica-Bold", fontSize=11, spaceBefore=8)
HEAD = ParagraphStyle("head", parent=BASE, fontName="Helvetica-Bold", spaceBefore=4)
SUB = ParagraphStyle("sub", parent=BASE, fontName="Helvetica-Oblique")
BULLET = ParagraphStyle("bullet", parent=BASE, leftIndent=14, bulletIndent=4)
PARA = ParagraphStyle("para", parent=BASE, leading=14, spaceAfter=8)


def _p(text: str, style) -> Paragraph:
    return Paragraph(escape(text), style)


def _doc(path: Path, title: str, author: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm,
                             bottomMargin=16 * mm, title=title, author=author)


def render_resume_pdf(r: Resume, path: Path) -> Path:
    flow = [_p(r.name, NAME), _p(" | ".join(r.contact), CONTACT)]
    for sec in r.sections:
        flow += [_p(sec.title.upper(), SEC), HRFlowable(width="100%", thickness=0.6, color="#808080", spaceAfter=2)]
        if sec.text:
            flow.append(_p(sec.text, BASE))
        for e in sec.entries:
            flow.append(_p(e.heading, HEAD))
            if e.sub:
                flow.append(_p(e.sub, SUB))
            flow += [Paragraph(escape(b), BULLET, bulletText="•") for b in e.bullets]
    _doc(path, f"{r.name} - Resume", r.name).build(flow)
    return path


def render_cover_letter_pdf(c: CoverLetter, path: Path) -> Path:
    flow = [_p(c.name, NAME), _p(" | ".join(c.contact), CONTACT), Spacer(1, 10), _p(c.date_line, PARA),
            _p(c.subject, ParagraphStyle("subj", parent=PARA, fontName="Helvetica-Bold")), _p(c.greeting, PARA)]
    flow += [_p(t, PARA) for t in c.paragraphs]
    flow += [_p(c.signoff, BASE), _p(c.name, BASE)]
    _doc(path, f"{c.name} - Cover letter", c.name).build(flow)
    return path

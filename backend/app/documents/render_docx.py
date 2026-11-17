from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from app.documents.model import CoverLetter, Resume


def _base() -> Document:
    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = Inches(0.8)
        s.top_margin = s.bottom_margin = Inches(0.7)
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    st.paragraph_format.space_after = Pt(2)
    return d


def _rule(par) -> None:
    pPr = par._p.get_or_add_pPr()
    b = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "808080")):
        bottom.set(qn(k), v)
    b.append(bottom)
    pPr.append(b)


def _header(d: Document, name: str, contact: list[str]) -> None:
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(name)
    r.bold, r.font.size = True, Pt(18)
    c = d.add_paragraph(" | ".join(contact))
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER


def render_resume_docx(r: Resume, path: Path) -> Path:
    d = _base()
    _header(d, r.name, r.contact)
    for sec in r.sections:
        h = d.add_paragraph()
        h.paragraph_format.space_before = Pt(8)
        run = h.add_run(sec.title.upper())
        run.bold, run.font.size = True, Pt(11)
        _rule(h)
        if sec.text:
            d.add_paragraph(sec.text)
        for e in sec.entries:
            p = d.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.add_run(e.heading).bold = True
            if e.sub:
                s = d.add_paragraph()
                s.add_run(e.sub).italic = True
            for b in e.bullets:
                d.add_paragraph(b, style="List Bullet")
    d.save(path)
    return path


def render_cover_letter_docx(c: CoverLetter, path: Path) -> Path:
    d = _base()
    _header(d, c.name, c.contact)
    d.add_paragraph()
    d.add_paragraph(c.date_line)
    d.add_paragraph().add_run(c.subject).bold = True
    d.add_paragraph(c.greeting)
    for para in c.paragraphs:
        p = d.add_paragraph(para)
        p.paragraph_format.space_after = Pt(8)
    d.add_paragraph(c.signoff)
    d.add_paragraph(c.name)
    d.save(path)
    return path

"""Render a CoverLetter to an ATS-safe .docx (matches resume styling)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

from jobhunt.pipeline.cover import CoverLetter
from jobhunt.resume.render_docx import (
    BODY_FONT,
    BODY_SIZE,
    NAME_SIZE,
    _add_paragraph,
    _set_default_font,
    _set_margins,
    _tighten,
)


def render_cover(cover: CoverLetter, contact_line: str, name: str, out_path: Path) -> Path:
    doc = Document()
    _set_margins(doc)
    _set_default_font(doc)

    p_name = doc.add_paragraph()
    p_name.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    r_name = p_name.add_run(name)
    r_name.bold = True
    r_name.font.name = BODY_FONT
    r_name.font.size = NAME_SIZE
    _tighten(p_name, after=2)

    p_contact = doc.add_paragraph()
    p_contact.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    r_contact = p_contact.add_run(contact_line)
    r_contact.font.name = BODY_FONT
    r_contact.font.size = BODY_SIZE
    _tighten(p_contact, after=12)

    _add_paragraph(doc, date.today().strftime("%B %d, %Y"))
    _add_paragraph(doc, cover.salutation)

    for para in cover.body:
        text = para.strip()
        if text:
            _add_paragraph(doc, text)

    for line in cover.sign_off.split("\n"):
        _add_paragraph(doc, line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path

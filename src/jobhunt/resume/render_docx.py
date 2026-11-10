"""Render a TailoredResume to an ATS-safe .docx (Calibri, single-column, real bullets).

ATS rules enforced (`kb/policies/tailoring-rules.md`, "ATS-safe output"):
- Single column. No tables for layout. No graphics, headers, or footers.
- Calibri 10–11pt body, 20pt name.
- Real bullet list style (not typed asterisks).
- US Letter, 0.65" side margins.

The resume layout follows the 2026-11-09 design resume, with the divergences
AGENTS.md records under "Resume render vs design". The cover letter shares
`NAME_SIZE`, `_set_margins` and `_tighten` and keeps their defaults.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Pt, RGBColor, Twips

from jobhunt.pipeline.tailor import TailoredResume

BODY_FONT = "Calibri"
BODY_SIZE = Pt(10.5)
NAME_SIZE = Pt(16)  # cover letter
HEADING_SIZE = Pt(11)

# Design resume values (Casey_Hsu_Design_2026-11-09.docx, ledger 2026-11-10).
RESUME_NAME_SIZE = Pt(20)
TAGLINE_SIZE = Pt(10)
CONTACT_SIZE = Pt(9)
SEPARATOR_SIZE = Pt(10)
DARK = RGBColor(0x1F, 0x1F, 0x1F)
MID = RGBColor(0x40, 0x40, 0x40)
GREY = RGBColor(0x59, 0x59, 0x59)
LINK = RGBColor(0x11, 0x55, 0xCC)
RULE_COLOR = "BFBFBF"
RESUME_LINE_SPACING = 1.08  # w:line 259
RESUME_SIDE_MARGIN = Inches(0.65)
RESUME_TOP_MARGIN = Twips(700)  # 0.486"
CONTACT_SEPARATOR = "  |  "


def render(tailored: TailoredResume, contact_line: str, name: str, out_path: Path) -> Path:
    """Render the resume. `contact_line` is the verified.json form, whose
    optional headline prefix ("Headline  City | phone | ...") becomes the
    tagline; a line without one renders no tagline."""
    doc = Document()
    _set_margins(doc, side=RESUME_SIDE_MARGIN, top=RESUME_TOP_MARGIN)
    _set_default_font(doc)
    _scrub_metadata(doc, name)

    headline, contact_items = _split_contact(contact_line)
    _add_name(doc, name)
    if headline:
        _add_tagline(doc, headline)
    if contact_items:
        _add_contact(doc, contact_items)

    # Unheaded summary under the contact row (ledger divergence A).
    _add_paragraph(doc, tailored.summary)

    _add_section_heading(doc, "CORE EXPERTISE")
    for cat in tailored.skills_categories:
        if not cat.items:
            continue
        p = doc.add_paragraph()
        run_label = p.add_run(f"{cat.name}: ")
        run_label.bold = True
        run_label.font.name = BODY_FONT
        run_label.font.size = BODY_SIZE
        run_items = p.add_run(", ".join(cat.items))
        run_items.font.name = BODY_FONT
        run_items.font.size = BODY_SIZE
        _tighten_resume(p)

    _add_section_heading(doc, "WORK EXPERIENCE")
    for role in tailored.roles:
        p = doc.add_paragraph()
        run_t = p.add_run(f"{role.title} | {role.employer}")
        run_t.bold = True
        run_t.font.name = BODY_FONT
        run_t.font.size = BODY_SIZE
        # Right-align dates via tab + tab-stop at the right margin.
        run_d = p.add_run(f"\t{role.dates}")
        run_d.font.name = BODY_FONT
        run_d.font.size = BODY_SIZE
        _add_right_tab_stop(p)
        _tighten_resume(p)
        for bullet in role.bullets:
            bp = doc.add_paragraph(style="List Bullet")
            br = bp.add_run(bullet)
            br.font.name = BODY_FONT
            br.font.size = BODY_SIZE
            _tighten_resume(bp)

    if tailored.projects:
        _add_section_heading(doc, "PROJECTS")
        for proj in tailored.projects:
            p = doc.add_paragraph()
            run_n = p.add_run(proj.name)
            run_n.bold = True
            run_n.font.name = BODY_FONT
            run_n.font.size = BODY_SIZE
            if proj.url:
                run_u = p.add_run(f"\t{proj.url}")
                run_u.font.name = BODY_FONT
                run_u.font.size = BODY_SIZE
                _add_right_tab_stop(p)
            _tighten_resume(p)
            if proj.stack:
                sp = doc.add_paragraph()
                r_lbl = sp.add_run("Stack: ")
                r_lbl.bold = True
                r_lbl.font.name = BODY_FONT
                r_lbl.font.size = BODY_SIZE
                r_items = sp.add_run(", ".join(proj.stack))
                r_items.font.name = BODY_FONT
                r_items.font.size = BODY_SIZE
                _tighten_resume(sp)
            for bullet in proj.bullets:
                bp = doc.add_paragraph(style="List Bullet")
                br = bp.add_run(bullet)
                br.font.name = BODY_FONT
                br.font.size = BODY_SIZE
                _tighten_resume(bp)

    _add_section_heading(doc, "EDUCATION AND EARLIER CAREER")
    for line in tailored.certifications:
        _add_paragraph(doc, line)
    for line in tailored.education:
        _add_paragraph(doc, line)
    if tailored.coursework:
        p = doc.add_paragraph()
        r1 = p.add_run("Dean's List (all terms). ")
        r1.bold = True
        r1.font.name = BODY_FONT
        r1.font.size = BODY_SIZE
        r2 = p.add_run("Coursework: " + ", ".join(tailored.coursework) + ".")
        r2.font.name = BODY_FONT
        r2.font.size = BODY_SIZE
        _tighten_resume(p)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def _scrub_metadata(doc: Any, name: str) -> None:
    """Strip OOXML core properties that leak machine identity. Recruiter-side
    AI screeners in 2026 occasionally inspect these. Set author and last-modifier
    to the candidate's own name; clear comments/keywords/category."""
    from datetime import UTC, datetime

    cp = doc.core_properties
    cp.author = name
    cp.last_modified_by = name
    cp.title = ""
    cp.subject = ""
    cp.keywords = ""
    cp.comments = ""
    cp.category = ""
    cp.revision = 1
    now = datetime.now(UTC)
    cp.created = now
    cp.modified = now


def _set_margins(doc: Any, *, side: Length = Inches(0.75), top: Length = Inches(0.5)) -> None:
    for section in doc.sections:
        section.top_margin = top
        section.bottom_margin = Inches(0.5)
        section.left_margin = side
        section.right_margin = side


def _set_default_font(doc: Any) -> None:
    style = doc.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = BODY_SIZE
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement

        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), BODY_FONT)
    rfonts.set(qn("w:hAnsi"), BODY_FONT)


def _split_contact(contact_line: str) -> tuple[str, list[str]]:
    """('Headline', ['City', 'phone', ...]) from a verified contact line.

    `parse_docx` joins the resume's header lines with two spaces, so a
    headline sits before the first item separated by 2+ spaces. A line
    without that prefix has no headline."""
    items = [part.strip() for part in contact_line.split("|") if part.strip()]
    if not items:
        return "", []
    first = re.split(r"\s{2,}", items[0], maxsplit=1)
    if len(first) == 2:
        items[0] = first[1]
        return first[0], items
    return "", items


_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?", re.IGNORECASE)


def _contact_link(item: str) -> tuple[str, str | None]:
    """Display text (scheme and 'www.' stripped) and link target, if any."""
    text = re.sub(r"^https?://", "", item.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE)
    if _EMAIL_RE.fullmatch(text):
        return text, f"mailto:{text}"
    if _DOMAIN_RE.fullmatch(text):
        return text, f"https://{text}"
    return text, None


def _add_name(doc: Any, name: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(name.upper())
    r.bold = True
    r.font.name = BODY_FONT
    r.font.size = RESUME_NAME_SIZE
    r.font.color.rgb = DARK
    _tighten_resume(p, after=0)


def _add_tagline(doc: Any, headline: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(headline.upper())
    r.font.name = BODY_FONT
    r.font.size = TAGLINE_SIZE
    r.font.color.rgb = MID
    _tighten_resume(p, after=5)


def _add_contact(doc: Any, items: list[str]) -> None:
    p = doc.add_paragraph()
    for i, item in enumerate(items):
        if i:
            sep = p.add_run(CONTACT_SEPARATOR)
            sep.font.name = BODY_FONT
            sep.font.size = SEPARATOR_SIZE
            sep.font.color.rgb = MID
        text, target = _contact_link(item)
        r = p.add_run(text)
        r.font.name = BODY_FONT
        r.font.size = CONTACT_SIZE
        if target is None:
            r.font.color.rgb = GREY
            continue
        r.font.color.rgb = LINK
        r.font.underline = True
        _wrap_in_hyperlink(p, r, target)
    _tighten_resume(p, after=3)
    _add_rules(p, ("top", "bottom"), color=RULE_COLOR, space=7)


def _wrap_in_hyperlink(paragraph: Any, run: Any, url: str) -> None:
    from docx.oxml import OxmlElement

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run._r.addprevious(link)
    link.append(run._r)


def _add_section_heading(doc: Any, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.name = BODY_FONT
    r.font.size = HEADING_SIZE
    r.font.color.rgb = DARK
    _tighten_resume(p, before=10, after=3)
    p.paragraph_format.keep_with_next = True


def _add_paragraph(doc: Any, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = BODY_SIZE
    _tighten_resume(p)


def _tighten(
    paragraph: Any, *, before: int = 0, after: int = 2, line_spacing: float = 1.15
) -> None:
    pf = paragraph.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line_spacing


def _tighten_resume(paragraph: Any, *, before: int = 0, after: int = 2) -> None:
    _tighten(paragraph, before=before, after=after, line_spacing=RESUME_LINE_SPACING)


def _add_right_tab_stop(paragraph: Any) -> None:
    from docx.enum.text import WD_TAB_ALIGNMENT

    paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(7.0), WD_TAB_ALIGNMENT.RIGHT)


def _add_rules(paragraph: Any, edges: tuple[str, ...], *, color: str, space: int) -> None:
    from docx.oxml import OxmlElement

    p_pr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    for edge in edges:
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), str(space))
        el.set(qn("w:color"), color)
        pbdr.append(el)
    p_pr.append(pbdr)


# --- one-page heuristic check -------------------------------------------------

# Conservative budget for 10.5pt Calibri on US Letter with 0.5"/0.75" margins.
# The previous 52-line budget overshot — section headings, role headers, and
# skill lines that wrap each cost more than the flat 1 line we counted.
# 48 lines with wrap-aware skill/summary/bullet counts produces reliable
# single-page output.
LINES_PER_PAGE = 48
# Two lines of headroom: the flat per-section estimates accumulate small
# rounding errors, so a resume estimated close to LINES_PER_PAGE renders onto
# a second page in practice (observed 2026-05-28 when a 6th skills category —
# the Projects tier — tipped a real resume's Dean's List onto page 2 while the
# estimate landed exactly on 48). The shrink ladder trims until the estimate is
# within this margin.
#
# Raised 1 -> 2 on 2026-09-10. A margin of 1 still let the boundary through: a
# `jobhunt resume` CMS lane estimated at exactly 47 (== the budget, so
# `fits_one_page` returned True and the ladder stopped) rendered its diploma
# line onto page 2, while the AI lane at 46 fit. Measured against LibreOffice,
# 46 fits and 47 does not, so the usable budget is 46. This mattered beyond one
# stray page: the tailor samples at temperature 0.3, so output length varies
# run to run and the same lane fit one day and overflowed the next — and every
# `apply` resume goes through this same ladder.
_PAGE_SAFETY_MARGIN = 2
BULLET_CHARS_PER_LINE = 95
SUMMARY_CHARS_PER_LINE = 100
SKILL_CHARS_PER_LINE = 95
# The coursework block renders as one paragraph: "Dean's List (all terms).
# Coursework: <list>." Estimate it wrap-aware rather than as a flat 2 lines.
_DEANS_PREFIX = "Dean's List (all terms). Coursework: "


def _wrapped_lines(text: str, width: int) -> int:
    return max(1, (len(text) + width - 1) // width)


def estimate_lines(tailored: TailoredResume) -> int:
    lines = 5  # name + tagline + contact + spacing (re-measured in Phase 6d)
    lines += _wrapped_lines(tailored.summary, SUMMARY_CHARS_PER_LINE)  # unheaded
    lines += 1
    for cat in tailored.skills_categories:
        if not cat.items:
            continue
        line_text = f"{cat.name}: " + ", ".join(cat.items)
        lines += _wrapped_lines(line_text, SKILL_CHARS_PER_LINE)
    lines += 1
    for role in tailored.roles:
        lines += 1
        for b in role.bullets:
            lines += _wrapped_lines(b, BULLET_CHARS_PER_LINE)
    if tailored.projects:
        lines += 1  # PROJECTS heading
        for proj in tailored.projects:
            lines += 1  # name + url header
            if proj.stack:
                stack_text = "Stack: " + ", ".join(proj.stack)
                lines += _wrapped_lines(stack_text, SKILL_CHARS_PER_LINE)
            for b in proj.bullets:
                lines += _wrapped_lines(b, BULLET_CHARS_PER_LINE)
    lines += 1 + len(tailored.certifications) + len(tailored.education)
    if tailored.coursework:
        deans_text = _DEANS_PREFIX + ", ".join(tailored.coursework) + "."
        lines += _wrapped_lines(deans_text, SKILL_CHARS_PER_LINE)
    return lines


def fits_one_page(tailored: TailoredResume) -> bool:
    return estimate_lines(tailored) <= LINES_PER_PAGE - _PAGE_SAFETY_MARGIN

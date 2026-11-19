"""Render a TailoredResume to an ATS-safe .docx (Calibri, single-column, real bullets).

ATS rules enforced (`kb/policies/tailoring-rules.md`, "ATS-safe output"):
- Single column. No tables for layout. No graphics, headers, or footers.
- Calibri 10–11pt body, 25pt name.
- Real bullet list style (not typed asterisks).
- US Letter, 0.5" side margins.

The layout follows the master resume (Casey_Hsu_Resume.docx, ledger signed
off 2026-11-19), with the divergences AGENTS.md records under "Resume render
vs design". The cover letter shares
`NAME_SIZE`, `_set_margins` and `_tighten` and keeps their defaults.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Pt, RGBColor, Twips

from jobhunt.pipeline.tailor import TailoredProject, TailoredResume, TailoredRole

BODY_FONT = "Calibri"
BODY_SIZE = Pt(10.5)
NAME_SIZE = Pt(16)  # cover letter
HEADING_SIZE = Pt(11)

# Master resume values (Casey_Hsu_Resume.docx, ledger signed off 2026-11-19).
RESUME_BODY_SIZE = Pt(10)
RESUME_NAME_SIZE = Pt(25)
RESUME_NAME_TRACKING = -10  # twentieths of a point, w:spacing
TAGLINE_SIZE = Pt(10.5)
CONTACT_SIZE = Pt(8.5)
SECTION_LABEL_SIZE = Pt(9)
META_SIZE = Pt(9.5)
DARK = RGBColor(0x1F, 0x2A, 0x33)
GREY = RGBColor(0x4A, 0x55, 0x60)
ACCENT = RGBColor(0x2F, 0x7F, 0xB5)
HEADER_RULE_COLOR = "2F7FB5"
HEADER_RULE_SIZE = 18  # eighths of a point: 2.25 pt
SECTION_RULE_COLOR = "B9C7D3"
SECTION_RULE_SIZE = 6  # 0.75 pt
RESUME_LINE_SPACING = 250 / 240  # w:line 250
RESUME_SIDE_MARGIN = Inches(0.5)
RESUME_TOP_MARGIN = Twips(620)  # 0.43", top and bottom
RESUME_TEXT_WIDTH = Inches(7.5)  # US Letter less both side margins
ROLE_SEPARATOR = "  ·  "
CONTACT_SEPARATOR = "  |  "
ROLE_GAP_PT = 5
PROJECT_AFTER_PT = 1.5
BULLET_LEFT = Twips(540)  # 0.375"
BULLET_HANGING = Twips(240)  # 0.167"
BULLET_AFTER = Pt(2.5)


def render(tailored: TailoredResume, contact_line: str, name: str, out_path: Path) -> Path:
    """Render the resume. `contact_line` is the verified.json form, whose
    optional headline prefix ("Headline  City | phone | ...") becomes the
    tagline; a line without one renders no tagline."""
    doc = Document()
    _set_margins(doc, side=RESUME_SIDE_MARGIN, top=RESUME_TOP_MARGIN, bottom=RESUME_TOP_MARGIN)
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

    _add_section_heading(doc, "SKILLS")
    for label, items in _skill_rows(tailored):
        p = doc.add_paragraph()
        _styled_run(p, f"{label}: ", color=DARK, bold=True)
        _styled_run(p, items, color=DARK)
        _tighten_resume(p, after=1)

    _add_section_heading(doc, "EXPERIENCE")
    work = [r for r in tailored.roles if not r.earlier_career]
    for i, role in enumerate(work):
        _add_role_header(doc, role, first=i == 0)
        for bullet in role.bullets:
            _add_bullet(doc, bullet)

    if tailored.projects:
        _add_section_heading(doc, "PROJECTS")
        for proj in tailored.projects:
            _add_project_row(doc, proj)

    _add_section_heading(doc, _education_heading(tailored))
    # Education before certifications, as in the design.
    for line in [*tailored.education, *tailored.certifications]:
        _add_credential(doc, line)
    if tailored.coursework:
        p = doc.add_paragraph()
        r1 = p.add_run("Dean's List (all terms). ")
        r1.bold = True
        r1.font.name = BODY_FONT
        r1.font.size = RESUME_BODY_SIZE
        r1.font.color.rgb = DARK
        r2 = p.add_run("Coursework: " + ", ".join(tailored.coursework) + ".")
        r2.font.name = BODY_FONT
        r2.font.size = RESUME_BODY_SIZE
        r2.font.color.rgb = DARK
        _tighten_resume(p)
    for role in tailored.roles:
        if role.earlier_career:
            _add_one_liner(doc, role.title, _earlier_career_tail(role))

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


def _set_margins(
    doc: Any,
    *,
    side: Length = Inches(0.75),
    top: Length = Inches(0.5),
    bottom: Length = Inches(0.5),
) -> None:
    for section in doc.sections:
        section.top_margin = top
        section.bottom_margin = bottom
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


EXPOSURE_LABEL = "Exposure"


def _skill_rows(tailored: TailoredResume) -> list[tuple[str, str]]:
    """('Label', 'a, b, c') per non-empty category, as the master lists
    skills (ledger 2026-11-19). Familiar items come last under "Exposure",
    apart from the claimable rows (ledger 2026-11-10)."""
    rows: list[tuple[str, str]] = []
    familiar: list[str] = []
    for cat in tailored.skills_categories:
        if cat.name.strip().lower() == "familiar":
            familiar.extend(cat.items)
        elif cat.items:
            rows.append((cat.name.strip(), ", ".join(cat.items)))
    if familiar:
        rows.append((EXPOSURE_LABEL, ", ".join(familiar)))
    return rows


def _education_heading(tailored: TailoredResume) -> str:
    """The master's "EDUCATION", widened only when an earlier-career role
    is listed under it."""
    if any(r.earlier_career for r in tailored.roles):
        return "EDUCATION AND EARLIER CAREER"
    return "EDUCATION"


def _styled_run(
    paragraph: Any,
    text: str,
    *,
    color: RGBColor,
    bold: bool | None = None,
    italic: bool | None = None,
    size: Length = RESUME_BODY_SIZE,
) -> Any:
    r = paragraph.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = size
    r.font.color.rgb = color
    if bold is not None:
        r.bold = bold
    if italic is not None:
        r.italic = italic
    return r


def _earlier_career_tail(role: TailoredRole) -> str:
    """'  |  Employer  |  Dates  |  first bullet' for an earlier-career one-liner,
    the bullet's trailing period dropped as in the design."""
    cells = [role.employer, role.dates]
    if role.bullets:
        cells.append(role.bullets[0].rstrip().rstrip("."))
    return "".join(CONTACT_SEPARATOR + c for c in cells)


def _add_role_header(doc: Any, role: TailoredRole, *, first: bool) -> None:
    """'Title  ·  Employer' with the dates at the right margin, on one line as
    in the master (ledger 2026-11-19)."""
    p = doc.add_paragraph()
    _styled_run(p, role.title, color=DARK, bold=True, size=HEADING_SIZE)
    if role.employer:
        _styled_run(p, f"{ROLE_SEPARATOR}{role.employer}", color=DARK, size=HEADING_SIZE)
    if role.dates:
        p.add_run("\t")
        _styled_run(p, role.dates, color=DARK, bold=True, size=META_SIZE)
        p.paragraph_format.tab_stops.add_tab_stop(RESUME_TEXT_WIDTH, WD_TAB_ALIGNMENT.RIGHT)
    _tighten_resume(p, before=0 if first else ROLE_GAP_PT, after=1)
    p.paragraph_format.keep_with_next = True


def _project_line(proj: TailoredProject) -> tuple[str, str]:
    """('Name (stack):', ' first bullet') for a one-line project entry. The
    URL is not rendered (Casey, 2026-11-10): the GitHub link in the contact row
    leads to the repos."""
    lead = f"{proj.name} ({', '.join(proj.stack)})" if proj.stack else proj.name
    if not proj.bullets:
        return lead, ""
    return f"{lead}:", f" {proj.bullets[0]}"


def _add_one_liner(doc: Any, bold: str, rest: str) -> None:
    """A line with an optional bold lead: credentials and earlier-career
    roles in the last section."""
    p = doc.add_paragraph()
    if bold:
        _styled_run(p, bold, color=DARK, bold=True)
    if rest:
        _styled_run(p, rest, color=DARK)
    _tighten_resume(p)


_YEAR_RE = re.compile(r"\b\d{4}\b")


def _add_credential(doc: Any, line: str) -> None:
    """'**Credential**  |  School' with a trailing year or date cell at the
    right margin, as in the master (ledger 2026-11-19). A single cell is one
    plain line."""
    cells = [c.strip() for c in line.split("|") if c.strip()]
    if len(cells) < 2:
        _add_one_liner(doc, "", line.strip())
        return
    dated = _YEAR_RE.search(cells[-1]) is not None
    middle = cells[1:-1] if dated else cells[1:]
    p = doc.add_paragraph()
    _styled_run(p, cells[0], color=DARK, bold=True)
    if middle:
        _styled_run(p, "".join(CONTACT_SEPARATOR + c for c in middle), color=DARK)
    if dated:
        p.add_run("\t")
        _styled_run(p, cells[-1], color=DARK, size=META_SIZE)
        p.paragraph_format.tab_stops.add_tab_stop(RESUME_TEXT_WIDTH, WD_TAB_ALIGNMENT.RIGHT)
    _tighten_resume(p, after=1)


def _add_project_row(doc: Any, proj: TailoredProject) -> None:
    """'**Name** (stack): sentence' as a plain row, as in the master."""
    lead, rest = _project_line(proj)
    p = doc.add_paragraph()
    _styled_run(p, proj.name, color=DARK, bold=True)
    tail = lead[len(proj.name) :] + rest
    if tail:
        _styled_run(p, tail, color=DARK)
    _tighten_resume(p, after=PROJECT_AFTER_PT)


def _add_bullet(doc: Any, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    _styled_run(p, text, color=DARK)
    _tighten_resume(p)
    pf = p.paragraph_format
    pf.left_indent = BULLET_LEFT
    pf.first_line_indent = -BULLET_HANGING
    pf.space_after = BULLET_AFTER


def _split_contact(contact_line: str) -> tuple[str, list[str]]:
    """('Headline', ['City', 'phone', ...]) from a verified contact line.

    `parse_docx` separates the headline from the contact items by two
    spaces. The headline is found before splitting on "|", because a
    headline may itself contain one ("Developer | CMS and AI Automation").
    A gap touching a "|" is separator padding ("a  |  b"), not that marker,
    so a line without a headline has none."""
    headline = ""
    gap = _HEADLINE_GAP_RE.search(contact_line)
    if gap is not None and contact_line[: gap.start()].strip():
        headline = contact_line[: gap.start()].strip()
        contact_line = contact_line[gap.end() :]
    items = [part.strip() for part in contact_line.split("|") if part.strip()]
    return headline, items


_HEADLINE_GAP_RE = re.compile(r"(?<![|\s])\s{2,}(?![|\s])")


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
    from docx.oxml import OxmlElement

    tracking = OxmlElement("w:spacing")
    tracking.set(qn("w:val"), str(RESUME_NAME_TRACKING))
    r._r.get_or_add_rPr().append(tracking)
    _tighten_resume(p, after=2)


def _add_tagline(doc: Any, headline: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(headline)
    r.bold = True
    r.font.name = BODY_FONT
    r.font.size = TAGLINE_SIZE
    r.font.color.rgb = ACCENT
    _tighten_resume(p, after=5)


def _add_contact(doc: Any, items: list[str]) -> None:
    p = doc.add_paragraph()
    for i, item in enumerate(items):
        if i:
            sep = p.add_run(CONTACT_SEPARATOR)
            sep.font.name = BODY_FONT
            sep.font.size = CONTACT_SIZE
            sep.font.color.rgb = GREY
        text, target = _contact_link(item)
        r = p.add_run(text)
        r.font.name = BODY_FONT
        r.font.size = CONTACT_SIZE
        r.font.color.rgb = GREY
        # A link keeps its target but reads as plain text, as in the master.
        if target is not None:
            _wrap_in_hyperlink(p, r, target)
    _tighten_resume(p, after=6)
    _add_rules(p, ("bottom",), color=HEADER_RULE_COLOR, size=HEADER_RULE_SIZE, space=5)


def _wrap_in_hyperlink(paragraph: Any, run: Any, url: str) -> None:
    from docx.oxml import OxmlElement

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run._r.addprevious(link)
    link.append(run._r)


def _add_section_heading(doc: Any, text: str) -> None:
    """A section label with the master's thin rule above it. SKILLS takes
    one too: the summary sits between it and the header rule."""
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.name = BODY_FONT
    r.font.size = SECTION_LABEL_SIZE
    r.font.color.rgb = DARK
    _tighten_resume(p, before=5.5, after=2)
    p.paragraph_format.keep_with_next = True
    _add_rules(p, ("top",), color=SECTION_RULE_COLOR, size=SECTION_RULE_SIZE, space=4)


def _add_paragraph(doc: Any, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = RESUME_BODY_SIZE
    r.font.color.rgb = DARK
    _tighten_resume(p)


def _tighten(
    paragraph: Any, *, before: float = 0, after: float = 2, line_spacing: float = 1.15
) -> None:
    pf = paragraph.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line_spacing


def _tighten_resume(paragraph: Any, *, before: float = 0, after: float = 2) -> None:
    _tighten(paragraph, before=before, after=after, line_spacing=RESUME_LINE_SPACING)


def _add_rules(
    paragraph: Any, edges: tuple[str, ...], *, color: str, size: int, space: int
) -> None:
    from docx.oxml import OxmlElement

    p_pr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    for edge in edges:
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), str(space))
        el.set(qn("w:color"), color)
        pbdr.append(el)
    p_pr.append(pbdr)


# --- one-page heuristic check -------------------------------------------------

# Budget for the master layout (0.5" sides, 250/240 spacing, 10 pt body),
# re-measured 2026-11-19 on 70 real-profile variants rendered through
# LibreOffice: every estimate <= 54 fit one page, 55 to 59 were mixed, and
# >= 60 always overflowed. With the 2-line margin below, the usable budget is
# 52. Earlier layouts measured 50 (2026-11-10 design) and 46 (0.75" sides,
# 1.15 spacing). The width constants were left alone: the cut-off is what the
# measurement pins, not each element's cost. Re-measure after any change to
# margins, spacing or font sizes.
LINES_PER_PAGE = 54
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
# line onto page 2, while the AI lane at 46 fit. Measured against LibreOffice
# on the old layout, 46 fit and 47 did not. This mattered beyond one
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
    lines = 5  # name + tagline + contact + spacing
    lines += _wrapped_lines(tailored.summary, SUMMARY_CHARS_PER_LINE)  # unheaded
    lines += 1  # SKILLS heading
    for label, items in _skill_rows(tailored):
        lines += _wrapped_lines(f"{label}: {items}", SKILL_CHARS_PER_LINE)
    lines += 1  # EXPERIENCE heading
    for role in tailored.roles:
        if role.earlier_career:
            # One line in the last section (title + tail), instead of a block.
            lines += _wrapped_lines(role.title + _earlier_career_tail(role), BULLET_CHARS_PER_LINE)
            continue
        lines += 1  # "Title · Employer    dates"
        for b in role.bullets:
            lines += _wrapped_lines(b, BULLET_CHARS_PER_LINE)
    if tailored.projects:
        lines += 1  # PROJECTS heading
        for proj in tailored.projects:
            lines += _wrapped_lines("".join(_project_line(proj)), BULLET_CHARS_PER_LINE)
    lines += 1  # EDUCATION heading
    for line in [*tailored.education, *tailored.certifications]:
        lines += _wrapped_lines(line, BULLET_CHARS_PER_LINE)
    if tailored.coursework:
        deans_text = _DEANS_PREFIX + ", ".join(tailored.coursework) + "."
        lines += _wrapped_lines(deans_text, SKILL_CHARS_PER_LINE)
    return lines


def fits_one_page(tailored: TailoredResume) -> bool:
    return estimate_lines(tailored) <= LINES_PER_PAGE - _PAGE_SAFETY_MARGIN

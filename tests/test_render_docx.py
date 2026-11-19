from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import RGBColor

from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredProject,
    TailoredResume,
    TailoredRole,
)
from jobhunt.resume.render_docx import estimate_lines, fits_one_page, render


@pytest.fixture
def tailored() -> TailoredResume:
    return TailoredResume(
        summary=(
            "Full-stack developer with hands-on Shopify, HubSpot, and React work. "
            "Owns project lifecycles end-to-end. GBC diploma, Dean's List."
        ),
        skills_categories=[
            TailoredCategory("Core", ["JavaScript", "TypeScript", "React", "Next.js"]),
            TailoredCategory("CMS", ["Shopify (Liquid)", "HubSpot CMS"]),
            TailoredCategory("Familiar", ["Java", "Python"]),
        ],
        roles=[
            TailoredRole(
                title="Web Developer (Contract)",
                employer="Custom Jewelry Brand (NDA)",
                dates="2023 – Present",
                bullets=["Built 14+ page Shopify storefront.", "Shipped ring builder."],
            ),
        ],
        certifications=["Contentful Certified Professional (October 2025)"],
        education=["Computer Programming & Analysis (Advanced Diploma), GBC, April 2024"],
        coursework=["Data Structures & Algorithms", "Full-Stack Development"],
        model="qwen3.5:9b",
    )


def test_render_writes_valid_docx(tailored: TailoredResume, tmp_path: Path):
    out = render(
        tailored,
        contact_line="me@example.com | site.com",
        name="Casey Hsu",
        out_path=tmp_path / "out.docx",
    )
    assert out.exists()
    doc = Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "CASEY HSU" in text
    assert "SKILLS" in text
    assert "EXPERIENCE" in text
    assert "Exposure: Java, Python" in text
    assert "Custom Jewelry Brand" in text


def test_estimate_fits_one_page(tailored: TailoredResume):
    assert fits_one_page(tailored)
    assert estimate_lines(tailored) > 0


def test_fits_one_page_enforces_safety_margin():
    """Regression (2026-05-28): a resume estimated at exactly LINES_PER_PAGE
    rendered onto a second page in practice (the Dean's List line tipped over
    when a 6th skills category was added). fits_one_page must reserve one line
    of headroom, so an estimate equal to the raw budget is NOT a fit."""
    from jobhunt.resume.render_docx import _PAGE_SAFETY_MARGIN, LINES_PER_PAGE

    assert _PAGE_SAFETY_MARGIN >= 1
    # Build a resume padded to estimate exactly LINES_PER_PAGE, then assert it
    # is rejected (would have passed under the old zero-margin check).
    base = TailoredResume(
        summary="Full-stack developer.",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["b"])],
        certifications=[],
        education=["Diploma"],
        coursework=[],
        model="x",
    )
    pad = LINES_PER_PAGE - estimate_lines(base)
    base.roles[0].bullets.extend(["padding bullet"] * max(0, pad))
    assert estimate_lines(base) == LINES_PER_PAGE
    assert not fits_one_page(base)  # exactly-at-budget must fail with margin


def test_render_emits_single_deans_list_paragraph(tailored: TailoredResume, tmp_path: Path):
    out = render(
        tailored,
        contact_line="me@example.com",
        name="Casey Hsu",
        out_path=tmp_path / "out.docx",
    )
    doc = Document(str(out))
    deans = [p for p in doc.paragraphs if p.text.startswith("Dean")]
    assert len(deans) == 1, [p.text for p in deans]


def test_render_includes_projects_section(tailored: TailoredResume, tmp_path: Path):
    """PB4b: a tailored resume with projects renders a PROJECTS section with the
    project name, stack and lead bullet (no url since 6c.3)."""
    tailored.projects = [
        TailoredProject(
            name="jobhunt",
            url="github.com/SimBuds/Jobhunt",
            stack=["Python", "Ollama", "SQLite"],
            bullets=["Built a local-first ATS CLI with a local LLM scorer."],
        )
    ]
    out = render(
        tailored,
        contact_line="me@example.com | site.com",
        name="Casey Hsu",
        out_path=tmp_path / "out.docx",
    )
    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "PROJECTS" in text
    # One line per project since 6c.3: stack in parentheses, no URL, no
    # separate Stack row (ledger fix 11, Casey 2026-11-10).
    assert "jobhunt (Python, Ollama, SQLite): Built a local-first ATS CLI" in text
    assert "github.com/SimBuds/Jobhunt" not in text
    assert "Stack:" not in text


def test_render_omits_projects_section_when_empty(tailored: TailoredResume, tmp_path: Path):
    """No projects → no PROJECTS heading (back-compat for project-less resumes)."""
    out = render(
        tailored,
        contact_line="me@example.com | site.com",
        name="Casey Hsu",
        out_path=tmp_path / "out.docx",
    )
    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "PROJECTS" not in text


def test_estimate_lines_counts_projects(tailored: TailoredResume):
    """The page-fit estimate must grow when projects are added, or the shrink
    ladder would never trim them."""
    before = estimate_lines(tailored)
    tailored.projects = [
        TailoredProject("jobhunt", "github.com/SimBuds/Jobhunt", ["Python"], ["Built a CLI."]),
    ]
    assert estimate_lines(tailored) > before


# --- Phase 6a.1 (2026-11-10): header and headings match the design resume ---
#
# Values come from Casey_Hsu_Design_2026-11-09.docx, ledger signed off
# 2026-11-10 (AGENTS.md "Resume render vs design").


VERIFIED_CONTACT = (
    "Shopify and WordPress Developer  Toronto, ON | 416-500-2939 | "
    "casey-hsu@outlook.com | caseyhsu.com | https://www.linkedin.com/in/casey-hsu | "
    "github.com/SimBuds"
)
# Master resume colours (Casey_Hsu_Resume.docx, ledger signed off 2026-11-19).
DARK_INK = RGBColor(0x1F, 0x2A, 0x33)
SOFT_GREY = RGBColor(0x4A, 0x55, 0x60)
ACCENT_BLUE = RGBColor(0x2F, 0x7F, 0xB5)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _doc(tailored: TailoredResume, tmp_path: Path, contact: str = VERIFIED_CONTACT):
    out = render(tailored, contact_line=contact, name="Casey Hsu", out_path=tmp_path / "o.docx")
    return Document(str(out))


def test_name_is_capitals_25pt_bold_dark_left(tailored, tmp_path):
    p = _doc(tailored, tmp_path).paragraphs[0]
    assert p.text == "CASEY HSU"
    r = p.runs[0]
    assert (r.bold, r.font.size.pt, r.font.color.rgb) == (True, 25.0, DARK_INK)
    # Tightened half a point, as in the master (w:spacing -10 twentieths).
    assert r._r.rPr.find(f"{W}spacing").get(f"{W}val") == "-10"
    assert p.alignment in (None, 0)  # left


def test_headline_prefix_becomes_tagline(tailored, tmp_path):
    p = _doc(tailored, tmp_path).paragraphs[1]
    assert p.text == "Shopify and WordPress Developer"
    r = p.runs[0]
    assert (r.bold, r.font.size.pt, r.font.color.rgb) == (True, 10.5, ACCENT_BLUE)


def test_no_headline_means_no_tagline(tailored, tmp_path):
    doc = _doc(tailored, tmp_path, contact="me@example.com | site.com")
    assert doc.paragraphs[1].text == "me@example.com  |  site.com"


def test_empty_contact_renders_neither_tagline_nor_row(tailored, tmp_path):
    doc = _doc(tailored, tmp_path, contact="")
    assert doc.paragraphs[0].text == "CASEY HSU"
    assert doc.paragraphs[1].text == tailored.summary


def test_contact_row_items_links_and_rules(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    row = doc.paragraphs[2]
    assert row.text == (
        "Toronto, ON  |  416-500-2939  |  casey-hsu@outlook.com  |  caseyhsu.com"
        "  |  linkedin.com/in/casey-hsu  |  github.com/SimBuds"
    )
    rels = doc.part.rels
    links = {
        "".join(t.text for t in h.iter(f"{W}t")): rels[h.get(f"{R}id")].target_ref
        for h in row._p.iter(f"{W}hyperlink")
    }
    assert links == {
        "casey-hsu@outlook.com": "mailto:casey-hsu@outlook.com",
        "caseyhsu.com": "https://caseyhsu.com",
        "linkedin.com/in/casey-hsu": "https://linkedin.com/in/casey-hsu",
        "github.com/SimBuds": "https://github.com/SimBuds",
    }
    # Links keep their target but read as plain grey text, as in the master.
    for r in row.runs:
        assert (r.font.size.pt, r.font.color.rgb, r.font.underline) == (8.5, SOFT_GREY, None)
    for h in row._p.iter(f"{W}hyperlink"):
        for rpr in h.iter(f"{W}rPr"):
            assert rpr.find(f"{W}u") is None
            assert rpr.find(f"{W}color").get(f"{W}val").upper() == "4A5560"
    bdr = row._p.pPr.find(f"{W}pBdr")
    assert bdr.find(f"{W}top") is None
    bottom = bdr.find(f"{W}bottom")
    assert (bottom.get(f"{W}color").upper(), bottom.get(f"{W}sz")) == ("2F7FB5", "18")


def test_headings_match_design(tailored, tmp_path):
    tailored.projects = [TailoredProject("jobhunt", "github.com/x", ["Python"], ["Built it."])]
    doc = _doc(tailored, tmp_path)
    names = ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]
    heads = [p for p in doc.paragraphs if p.text in names + ["SUMMARY", "TECHNICAL SKILLS"]]
    assert [p.text for p in heads] == names
    for p in heads:
        r = p.runs[0]
        assert (r.bold, r.font.size.pt) == (True, 9.0)
        assert r.font.color.rgb == DARK_INK
        assert p.paragraph_format.keep_with_next is True
        # Every section has the rule, SKILLS included: the summary sits
        # between it and the header rule (Casey, 2026-11-19).
        top = p._p.pPr.find(f"{W}pBdr").find(f"{W}top")
        assert (top.get(f"{W}color").upper(), top.get(f"{W}sz")) == ("B9C7D3", "6")


def test_summary_renders_unheaded_after_contact_row(tailored, tmp_path):
    texts = [p.text for p in _doc(tailored, tmp_path).paragraphs]
    assert texts[3] == tailored.summary
    assert texts[4] == "SKILLS"


def test_margins_and_line_spacing_match_design(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    s = doc.sections[0]
    margins = (s.left_margin, s.right_margin, s.top_margin, s.bottom_margin)
    assert [round(m.inches, 2) for m in margins] == [0.5, 0.5, 0.43, 0.43]
    summary = doc.paragraphs[3]
    assert summary.paragraph_format.line_spacing == pytest.approx(1.04, abs=0.005)
    assert (summary.runs[0].font.size.pt, summary.runs[0].font.color.rgb) == (10.0, DARK_INK)


# --- Phase 6b (2026-11-10): skills line, role header, bold lead clause ------


def _para(doc, startswith: str):
    return next(p for p in doc.paragraphs if p.text.startswith(startswith))


def test_skills_render_as_labelled_rows_with_exposure_last(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    texts = [p.text for p in doc.paragraphs]
    i = texts.index("SKILLS")
    assert texts[i + 1 : i + 5] == [
        "Core: JavaScript, TypeScript, React, Next.js",
        "CMS: Shopify (Liquid), HubSpot CMS",
        "Exposure: Java, Python",
        "EXPERIENCE",
    ]
    label, items = doc.paragraphs[i + 1].runs
    assert (label.text, label.bold) == ("Core: ", True)
    assert items.bold is None
    for r in (label, items):
        assert (r.font.size.pt, r.font.color.rgb) == (10.0, DARK_INK)


def test_estimate_counts_one_wrapped_row_per_skill_category(tailored):
    before = estimate_lines(tailored)
    tailored.skills_categories.append(TailoredCategory("Infrastructure", ["Docker"]))
    assert estimate_lines(tailored) == before + 1
    tailored.skills_categories.append(TailoredCategory("Long", ["x" * 150]))
    assert estimate_lines(tailored) == before + 1 + 2


def test_no_familiar_category_means_no_exposure_line(tailored, tmp_path):
    tailored.skills_categories = [
        TailoredCategory("Core", ["JavaScript"]),
        TailoredCategory("Empty", []),
    ]
    texts = [p.text for p in _doc(tailored, tmp_path).paragraphs]
    i = texts.index("SKILLS")
    assert texts[i + 1 : i + 3] == ["Core: JavaScript", "EXPERIENCE"]


def test_role_header_is_one_line_with_right_aligned_dates(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    head = _para(doc, "Web Developer (Contract)")
    assert head.text == "Web Developer (Contract)  ·  Custom Jewelry Brand (NDA)\t2023 – Present"
    title, employer, _tab, dates = head.runs
    assert (title.text, title.bold, title.font.size.pt) == ("Web Developer (Contract)", True, 11.0)
    assert (employer.text, employer.bold, employer.font.size.pt) == (
        "  ·  Custom Jewelry Brand (NDA)",
        None,
        11.0,
    )
    assert (dates.text, dates.bold, dates.font.size.pt) == ("2023 – Present", True, 9.5)
    for r in (title, employer, dates):
        assert r.font.color.rgb == DARK_INK
    [stop] = head.paragraph_format.tab_stops
    assert (round(stop.position.inches, 2), stop.alignment) == (7.5, WD_TAB_ALIGNMENT.RIGHT)
    assert head.paragraph_format.keep_with_next is True


def test_role_without_dates_or_employer_renders_what_it_has(tailored, tmp_path):
    tailored.roles = [TailoredRole("Dev", "", "", ["Built it."])]
    assert _para(_doc(tailored, tmp_path), "Dev").text == "Dev"


def test_bullets_are_plain_with_no_bold_lead(tailored, tmp_path):
    tailored.roles[0].bullets = [
        "Moved average Google position from 38 to 13 in 16 months, ranking 44 terms.",
    ]
    bullet = _para(_doc(tailored, tmp_path), "Moved average")
    assert bullet.style.name == "List Bullet"
    [run] = bullet.runs
    assert (run.bold, run.font.size.pt, run.font.color.rgb) == (None, 10.0, DARK_INK)
    pf = bullet.paragraph_format
    assert round(pf.left_indent.inches, 3) == 0.375
    assert round(-pf.first_line_indent.inches, 3) == 0.167
    assert pf.space_after.pt == 2.5


def test_estimate_counts_one_header_line_per_role(tailored):
    before = estimate_lines(tailored)
    tailored.roles.append(TailoredRole("Dev", "Acme", "2020 – 2021", []))
    assert estimate_lines(tailored) == before + 1


# --- Phase 6c.2 (2026-11-10): earlier-career roles move to the last section --


def _with_kitchen(tailored, bullets=("Led teams of up to 20 and managed kitchen operations.",)):
    tailored.roles.append(
        TailoredRole(
            "Night Sous Chef",
            "JOEY and other restaurants",
            "Feb 2014 – Nov 2024",
            list(bullets),
            earlier_career=True,
        )
    )
    return tailored


def test_earlier_career_role_renders_as_one_liner_in_last_section(tailored, tmp_path):
    texts = [p.text for p in _doc(_with_kitchen(tailored), tmp_path).paragraphs]
    work = texts[texts.index("EXPERIENCE") : texts.index("EDUCATION AND EARLIER CAREER")]
    assert not any("Sous Chef" in t or "JOEY" in t for t in work)
    assert texts[-1] == (
        "Night Sous Chef  |  JOEY and other restaurants  |  Feb 2014 – Nov 2024"
        "  |  Led teams of up to 20 and managed kitchen operations"
    )


def test_earlier_career_title_is_bold(tailored, tmp_path):
    doc = _doc(_with_kitchen(tailored), tmp_path)
    line = doc.paragraphs[-1]
    assert (line.runs[0].text, line.runs[0].bold) == ("Night Sous Chef", True)
    assert line.runs[1].bold is None


def test_earlier_career_role_without_bullets(tailored, tmp_path):
    doc = _doc(_with_kitchen(tailored, bullets=()), tmp_path)
    assert doc.paragraphs[-1].text == (
        "Night Sous Chef  |  JOEY and other restaurants  |  Feb 2014 – Nov 2024"
    )


def test_estimate_counts_earlier_career_role_as_its_wrapped_one_liner(tailored):
    """One line in the last section, wrapped like a bullet, never the title,
    meta line and bullets of a Work Experience block."""
    before = estimate_lines(tailored)
    assert estimate_lines(_with_kitchen(tailored, bullets=("Led teams.",))) == before + 1
    tailored.roles.pop()
    # The real one-liner is 127 characters, two lines at BULLET_CHARS_PER_LINE.
    assert estimate_lines(_with_kitchen(tailored)) == before + 2


# --- Phase 6c.3 (2026-11-10): one-line projects and credentials -------------


def _project(stack=("Python", "SQLite"), bullets=("Built a local-first CLI.",)):
    return TailoredProject("Jobhunt", "github.com/SimBuds/Jobhunt", list(stack), list(bullets))


def _section(doc, heading, next_heading=None):
    texts = [p.text for p in doc.paragraphs]
    start = texts.index(heading) + 1
    end = texts.index(next_heading) if next_heading else len(texts)
    return doc.paragraphs[start:end]


def test_project_is_one_plain_row_with_a_bold_name(tailored, tmp_path):
    tailored.projects = [_project()]
    doc = _doc(tailored, tmp_path)
    [line] = _section(doc, "PROJECTS", "EDUCATION")
    assert line.text == "Jobhunt (Python, SQLite): Built a local-first CLI."
    assert [(r.text, r.bold) for r in line.runs] == [
        ("Jobhunt", True),
        (" (Python, SQLite): Built a local-first CLI.", None),
    ]
    # A row, not a list item, as in the master.
    assert line.style.name != "List Bullet"
    assert line.paragraph_format.space_after.pt == 1.5
    assert "github.com/SimBuds/Jobhunt" not in "\n".join(p.text for p in doc.paragraphs)


def test_project_without_stack_or_bullet(tailored, tmp_path):
    tailored.projects = [_project(stack=()), _project(bullets=())]
    lines = _section(_doc(tailored, tmp_path), "PROJECTS", "EDUCATION")
    assert [p.text for p in lines] == [
        "Jobhunt: Built a local-first CLI.",
        "Jobhunt (Python, SQLite)",
    ]


def test_education_precedes_certifications_with_bold_first_cell(tailored, tmp_path):
    tailored.education = ["Advanced Diploma, Computer Programming | Sep 2021 – Apr 2024 | GBC"]
    tailored.certifications = ["Contentful Certified Professional  |  Oct 2025", "Plain cert"]
    tailored.coursework = []
    lines = _section(_doc(tailored, tmp_path), "EDUCATION")
    assert [p.text for p in lines] == [
        "Advanced Diploma, Computer Programming  |  Sep 2021 – Apr 2024  |  GBC",
        "Contentful Certified Professional\tOct 2025",
        "Plain cert",
    ]
    assert [(r.text, r.bold) for r in lines[0].runs][0] == (
        "Advanced Diploma, Computer Programming",
        True,
    )
    assert [r.bold for r in lines[2].runs] == [None]


def test_trailing_year_cell_is_right_aligned(tailored, tmp_path):
    tailored.education = ["Advanced Diploma, Software | Harbour Polytechnic | Dean's List | 2024"]
    tailored.certifications = []
    tailored.coursework = []
    [line] = _section(_doc(tailored, tmp_path), "EDUCATION")
    assert line.text == ("Advanced Diploma, Software  |  Harbour Polytechnic  |  Dean's List\t2024")
    assert (line.runs[0].text, line.runs[0].bold) == ("Advanced Diploma, Software", True)
    year = line.runs[-1]
    assert (year.text, year.bold, year.font.size.pt) == ("2024", None, 9.5)
    [stop] = line.paragraph_format.tab_stops
    assert (round(stop.position.inches, 2), stop.alignment) == (7.5, WD_TAB_ALIGNMENT.RIGHT)


def test_estimate_counts_project_and_credential_one_liners(tailored):
    tailored.coursework = []
    tailored.education = []
    tailored.certifications = []
    base = estimate_lines(tailored)
    tailored.projects = [_project(bullets=("x" * 150,))]
    assert estimate_lines(tailored) == base + 1 + 2  # PROJECTS heading + 2 wrapped lines
    tailored.certifications = ["c" * 150]
    assert estimate_lines(tailored) == base + 3 + 2


# --- Phase M2f (2026-11-19): page budget re-measured on the master layout ---
#
# 70 real-profile variants through LibreOffice: every estimate <= 54 fit one
# page, 55 to 59 were mixed, >= 60 always overflowed. Budget 52 keeps a
# 2-line margin below the last estimate that always fit (AGENTS.md audit
# rule 8).


def _padded(target: int) -> TailoredResume:
    t = TailoredResume(
        summary="Shopify developer.",
        skills_categories=[TailoredCategory("Core", ["JavaScript"])],
        roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["b"])],
        certifications=[],
        education=["Diploma"],
        coursework=[],
        model="x",
    )
    t.roles[0].bullets.extend(["padding bullet"] * (target - estimate_lines(t)))
    assert estimate_lines(t) == target
    return t


def test_budget_accepts_estimate_52_and_rejects_53():
    assert fits_one_page(_padded(52))
    assert not fits_one_page(_padded(53))


# --- Phase H1 (2026-11-17): a headline may itself contain "|" ---------------


def test_headline_with_a_pipe_stays_whole(tailored, tmp_path):
    contact = (
        "Shopify and WordPress Developer | CMS and AI Automation  "
        "416-500-2939 | Toronto, ON | casey-hsu@outlook.com"
    )
    doc = _doc(tailored, tmp_path, contact=contact)
    assert doc.paragraphs[1].text == "Shopify and WordPress Developer | CMS and AI Automation"
    assert doc.paragraphs[2].text == "416-500-2939  |  Toronto, ON  |  casey-hsu@outlook.com"


def test_padded_pipes_without_headline_render_no_tagline(tailored, tmp_path):
    doc = _doc(tailored, tmp_path, contact="me@example.com  |  555-010-0000  |  site.com")
    assert doc.paragraphs[1].text == "me@example.com  |  555-010-0000  |  site.com"

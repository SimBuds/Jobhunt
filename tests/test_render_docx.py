from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
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
    assert "CORE EXPERTISE" in text
    assert "WORK EXPERIENCE" in text
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
    project name, url, stack, and bullets."""
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
    assert "jobhunt" in text
    assert "github.com/SimBuds/Jobhunt" in text
    assert "Stack: Python, Ollama, SQLite" in text
    assert "local-first ATS CLI" in text


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
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _doc(tailored: TailoredResume, tmp_path: Path, contact: str = VERIFIED_CONTACT):
    out = render(tailored, contact_line=contact, name="Casey Hsu", out_path=tmp_path / "o.docx")
    return Document(str(out))


def test_name_is_capitals_20pt_bold_dark_left(tailored, tmp_path):
    p = _doc(tailored, tmp_path).paragraphs[0]
    assert p.text == "CASEY HSU"
    r = p.runs[0]
    assert (r.bold, r.font.size.pt, r.font.color.rgb) == (True, 20.0, RGBColor(0x1F, 0x1F, 0x1F))
    assert p.alignment in (None, 0)  # left


def test_headline_prefix_becomes_tagline(tailored, tmp_path):
    p = _doc(tailored, tmp_path).paragraphs[1]
    assert p.text == "SHOPIFY AND WORDPRESS DEVELOPER"
    r = p.runs[0]
    assert (r.font.size.pt, r.font.color.rgb) == (10.0, RGBColor(0x40, 0x40, 0x40))


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
    bdr = row._p.pPr.find(f"{W}pBdr")
    assert bdr is not None
    for edge in ("top", "bottom"):
        assert bdr.find(f"{W}{edge}").get(f"{W}color").upper() == "BFBFBF"


def test_headings_match_design(tailored, tmp_path):
    tailored.projects = [TailoredProject("jobhunt", "github.com/x", ["Python"], ["Built it."])]
    doc = _doc(tailored, tmp_path)
    names = ["CORE EXPERTISE", "WORK EXPERIENCE", "PROJECTS", "EDUCATION AND EARLIER CAREER"]
    heads = [p for p in doc.paragraphs if p.text in names + ["SUMMARY", "TECHNICAL SKILLS"]]
    assert [p.text for p in heads] == names
    for p in heads:
        r = p.runs[0]
        assert (r.bold, r.font.size.pt) == (True, 11.0)
        assert r.font.color.rgb == RGBColor(0x1F, 0x1F, 0x1F)
        pf = p.paragraph_format
        assert (pf.space_before.pt, pf.space_after.pt, pf.keep_with_next) == (10.0, 3.0, True)
        assert p._p.pPr.find(f"{W}pBdr") is None


def test_summary_renders_unheaded_after_contact_row(tailored, tmp_path):
    texts = [p.text for p in _doc(tailored, tmp_path).paragraphs]
    assert texts[3] == tailored.summary
    assert texts[4] == "CORE EXPERTISE"


def test_margins_and_line_spacing_match_design(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    s = doc.sections[0]
    margins = (s.left_margin, s.right_margin, s.top_margin, s.bottom_margin)
    assert [round(m.inches, 2) for m in margins] == [0.65, 0.65, 0.49, 0.5]
    assert doc.paragraphs[3].paragraph_format.line_spacing == pytest.approx(1.08, abs=0.005)


# --- Phase 6b (2026-11-10): skills line, role header, bold lead clause ------


def _para(doc, startswith: str):
    return next(p for p in doc.paragraphs if p.text.startswith(startswith))


def test_skills_render_as_one_flat_line_plus_exposure(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    texts = [p.text for p in doc.paragraphs]
    i = texts.index("CORE EXPERTISE")
    assert texts[i + 1] == "  •  ".join(
        ["JavaScript", "TypeScript", "React", "Next.js", "Shopify (Liquid)", "HubSpot CMS"]
    )
    assert texts[i + 2] == "Exposure: Java, Python"
    assert texts[i + 3] == "WORK EXPERIENCE"
    r = doc.paragraphs[i + 1].runs[0]
    assert (r.font.size.pt, r.font.color.rgb) == (10.5, RGBColor(0x59, 0x59, 0x59))


def test_no_familiar_category_means_no_exposure_line(tailored, tmp_path):
    tailored.skills_categories = [
        TailoredCategory("Core", ["JavaScript"]),
        TailoredCategory("Empty", []),
    ]
    texts = [p.text for p in _doc(tailored, tmp_path).paragraphs]
    i = texts.index("CORE EXPERTISE")
    assert texts[i + 1 : i + 3] == ["JavaScript", "WORK EXPERIENCE"]


def test_role_header_is_title_line_then_italic_meta_line(tailored, tmp_path):
    doc = _doc(tailored, tmp_path)
    title = _para(doc, "Web Developer (Contract)")
    assert title.text == "Web Developer (Contract)"
    t = title.runs[0]
    assert (t.bold, t.font.size.pt, t.font.color.rgb) == (True, 11.0, RGBColor(0x1F, 0x1F, 0x1F))
    meta = _para(doc, "Custom Jewelry Brand")
    assert meta.text == "Custom Jewelry Brand (NDA)  |  2023 – Present"
    m = meta.runs[0]
    assert (m.italic, m.font.size.pt, m.font.color.rgb) == (True, 10.5, RGBColor(0x59, 0x59, 0x59))


def test_bullet_lead_is_bold_through_first_comma(tailored, tmp_path):
    tailored.roles[0].bullets = [
        "Moved average Google position from 38 to 13 in 16 months, ranking 44 terms.",
        "Built the pipeline in 20 hours after 100 hours of manual entry and more words "
        "past seventy characters, then handed it over.",
        "Shipped ring builder.",
    ]
    doc = _doc(tailored, tmp_path)
    lead = _para(doc, "Moved average")
    assert [(r.text, r.bold) for r in lead.runs] == [
        ("Moved average Google position from 38 to 13 in 16 months,", True),
        (" ranking 44 terms.", None),
    ]
    assert lead.runs[0].font.color.rgb == RGBColor(0x1F, 0x1F, 0x1F)
    assert lead.runs[1].font.color.rgb == RGBColor(0x59, 0x59, 0x59)
    far = _para(doc, "Built the pipeline")
    assert [r.bold for r in far.runs] == [None]
    plain = _para(doc, "Shipped ring builder")
    assert [r.bold for r in plain.runs] == [None]
    pf = lead.paragraph_format
    assert round(pf.left_indent.inches, 3) == 0.375
    assert round(-pf.first_line_indent.inches, 3) == 0.167
    assert pf.space_after.pt == 0.75


def test_estimate_counts_one_meta_line_per_role(tailored):
    before = estimate_lines(tailored)
    tailored.roles.append(TailoredRole("Dev", "Acme", "2020 – 2021", []))
    assert estimate_lines(tailored) == before + 2

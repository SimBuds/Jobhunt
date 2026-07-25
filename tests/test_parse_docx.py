from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.errors import PipelineError
from jobhunt.resume.parse_docx import (
    _ROLE_LINE_RE,
    _split_skills,
    parse_baseline,
    write_kb_markdown,
    write_verified_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _live_baseline() -> Path | None:
    """The real baseline resume, or None when the repo has none.

    Resolved through `resume.locate` rather than a hard-coded filename: on
    2026-07-24 the file was renamed and the previous `REPO_ROOT /
    "Baseline_Resume.docx"` turned two regression guards into silent skips,
    which is strictly worse than a failure because nothing surfaces it.

    These are the **only** tests that touch the user's real resume, and they
    assert it parses cleanly rather than asserting its content. Content
    assertions belong on the fictional fixture profile (tests/conftest.py).
    """
    from jobhunt.errors import PipelineError
    from jobhunt.resume.locate import find_baseline_resume

    try:
        found = find_baseline_resume(REPO_ROOT)
    except PipelineError:
        return None
    return found if found.suffix.lower() == ".docx" else None


BASELINE = _live_baseline() or REPO_ROOT / "Baseline_Resume.docx"


def test_contact_block_spans_multiple_paragraphs(tmp_path: Path):
    """A contact block wrapped across two paragraphs is joined into one line."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com  |")
    doc.add_paragraph("https://janedev.com  |  https://github.com/jane")
    doc.add_paragraph("SUMMARY")
    doc.add_paragraph("A developer.")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, _ = parse_baseline(path)
    assert facts.name == "Jane Dev"
    assert "jane@example.com" in facts.contact_line
    assert "janedev.com" in facts.contact_line  # second paragraph captured
    assert "github.com/jane" in facts.contact_line
    assert facts.summary == "A developer."  # contact block didn't swallow the summary


@pytest.mark.skipif(not BASELINE.is_file(), reason="baseline .docx not present")
def test_parse_baseline_round_trip(tmp_path: Path):
    facts, warnings = parse_baseline(BASELINE)
    assert warnings == []  # the curated master parses cleanly (RP1 regression guard)
    assert facts.name
    assert "Toronto" in facts.contact_line
    # The master wraps contact info onto a second paragraph (links on line 2);
    # the whole block must be captured, not just the first line.
    assert "github.com/SimBuds" in facts.contact_line
    assert "caseyhsu.com" in facts.contact_line
    assert len(facts.work_history) == 4

    employers = {r.employer for r in facts.work_history}
    assert "Atelier Dacko, Custom Jewelry Brand" in employers
    assert "Sous Chef & Team Lead" in {r.title for r in facts.work_history}

    # Familiar must stay separate. Bucket layout per kb/profile/verified-notes.md:
    # Java/Spring Boot are Familiar (coursework-only); Python is Core (data_devops
    # bucket) — Casey writes and operates this CLI in Python daily, not Familiar.
    assert "Java" in facts.skills_familiar
    assert "Spring Boot" in facts.skills_familiar
    assert "Python" in facts.skills_data_devops
    assert "Java" not in facts.skills_core

    # PB1: the "Project Stack:" skills line populates a Core-grade bucket
    # distinct from Familiar (project-demonstrated, not academic).
    assert "FastAPI" in facts.skills_projects
    assert "FastAPI" not in facts.skills_familiar
    assert "FastAPI" not in facts.skills_core

    # PB3: the PROJECTS narrative section parses into structured projects.
    # Baseline carries FIVE projects: Jobhunt + Auto-Agent (2026-06-18),
    # SEO-LLM + AI Context Stack (re-added 2026-06-23 at Casey's request), and
    # Portfolio (added 2026-07-17 — the Astro/nginx/GH-Actions deploy story that
    # grounds Astro's move into skills_projects). macOS Ventura on KVM + the
    # Hybrid coding agent stay long-form-only in kb/profile/work-long-form.md,
    # off the baseline.
    assert len(facts.projects) == 5
    names = [p.name for p in facts.projects]
    assert "Jobhunt" in names  # product name is "Jobhunt" (capital J) per branding
    assert "SEO-LLM" in names
    assert "AI Context Stack" in names
    portfolio = next(p for p in facts.projects if p.name == "Portfolio")
    assert portfolio.url == "github.com/SimBuds/Portfolio"
    assert "Astro" in portfolio.stack
    # Astro is project-demonstrated now (moved from Familiar 2026-07-17).
    assert "Astro" in facts.skills_projects
    assert "Astro" not in facts.skills_familiar
    auto = next(p for p in facts.projects if p.name == "Auto-Agent")
    assert auto.url == "github.com/SimBuds/Auto-Agent"
    assert "FastAPI" in auto.stack
    assert auto.bullets and "Claude API" in auto.bullets[0]
    # PROJECTS narrative must NOT leak into education (the pre-PB3 behaviour).
    assert not any("github.com" in e for e in facts.education)

    # Round-trip via verified.json.
    out = tmp_path / "verified.json"
    write_verified_json(facts, out)
    payload = json.loads(out.read_text())
    assert payload["name"] == facts.name
    assert len(payload["work_history"]) == 4
    assert len(payload["projects"]) == 5
    assert payload["projects"][0]["stack"]  # nested dataclass round-trips

    # KB markdown writer leaves five files (projects.md added when projects exist).
    kb = tmp_path / "kb"
    paths = write_kb_markdown(facts, kb)
    assert len(paths) == 5
    skills_md = (kb / "profile" / "skills.md").read_text()
    assert skills_md.count("## Familiar") == 1
    assert skills_md.count("## Project Stack") == 1
    assert "FastAPI" in skills_md
    projects_md = (kb / "profile" / "projects.md").read_text()
    assert "## Auto-Agent" in projects_md


@pytest.mark.skipif(not BASELINE.is_file(), reason="baseline .docx not present")
def test_parse_baseline_positioning_and_atomic_skills():
    """Regression guard for the 2026-06 specialist re-positioning (Initiative:
    Positioning resync). Locks the things that were either hand-patched or newly
    moved, so a future re-parse can't silently regress them:
    - skills_ai stays ATOMIC and paren-aware (retires PLAN.md's stale "skills_ai
      produces a run-on, patch by hand" caveat).
    - Figma is Familiar (2026-06-18 decision): Casey builds from Figma handoffs,
      he does not author designs in it, so it is not promoted to Core.
    - "Dawn" survives the parse (it lives in the Atelier bullet, not the skills
      line).
    - The lead role carries the JD-aligned retitle, not the old generic title.
    """
    facts, warnings = parse_baseline(BASELINE)
    assert warnings == []

    # Atomic + paren-aware: a naive comma split would shatter this entry into
    # "GPU optimization (cache" + "flash attention)". The whole item must survive.
    assert "GPU optimization (cache, flash attention)" in facts.skills_ai
    # No item is a comma-split artifact (a stray dangling close-paren with no open).
    for item in facts.skills_ai:
        assert item.count("(") == item.count(")"), f"unbalanced parens: {item!r}"

    # Figma is Familiar (2026-06-18 decision), not promoted to Core.
    assert "Figma" in facts.skills_familiar
    assert "Figma" not in facts.skills_core

    # Dawn is captured somewhere in the verified facts (it lives in a bullet).
    assert any("Dawn" in b for r in facts.work_history for b in r.bullets)

    # The lead role is the confirmed CMS-focused specialist title, not
    # "Web Developer" or a generic full-stack label.
    assert facts.work_history[0].title.startswith("CMS / E-commerce Developer")


def test_parse_warns_on_unknown_skill_label(tmp_path: Path):
    """An unrecognized TECHNICAL SKILLS label is reported as a warning, not
    silently dropped (RP1 + RP3). 'Hobbies' is neither a canonical bucket nor an
    RP3 alias, so it stays unknown."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph("Core: Python, TypeScript")
    doc.add_paragraph("Hobbies: Climbing, Chess")  # unknown label, no bucket / alias
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert "Python" in facts.skills_core
    assert any("Hobbies" in w for w in warnings)
    # The dropped items must not have leaked into any bucket.
    all_skills = (
        facts.skills_core
        + facts.skills_cms
        + facts.skills_data_devops
        + facts.skills_ai
        + facts.skills_projects
        + facts.skills_familiar
    )
    assert "Climbing" not in all_skills


def test_skill_label_aliases(tmp_path: Path):
    """RP3: alternate skill-section labels map onto the canonical buckets so a
    resume that does not use Casey's exact headings still populates them."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph("Frameworks: React, Vue")  # -> Core
    doc.add_paragraph("Databases: Postgres, Redis")  # -> Data & DevOps
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert "React" in facts.skills_core
    assert "Postgres" in facts.skills_data_devops
    assert warnings == []


def test_compound_skill_labels_map_to_buckets(tmp_path: Path):
    """A9: compound labels must not drop their whole bucket.

    Regression for 2026-07-24, when a reformatted baseline used 'Languages &
    Frameworks' and 'AI & Automation'. Neither was an alias, so both buckets
    were dropped silently — verified.json reported zero core and zero AI
    skills, and the fabrication guard then rejected real skills as unverified.
    Both '&' and 'and' spellings are covered.
    """
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph("Languages & Frameworks: TypeScript, Next.js")
    doc.add_paragraph("AI and Automation: Claude API, Ollama")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert warnings == []
    assert "TypeScript" in facts.skills_core
    assert "Next.js" in facts.skills_core
    assert "Claude API" in facts.skills_ai
    assert "Ollama" in facts.skills_ai


def test_technical_projects_heading_is_recognized(tmp_path: Path):
    """A9: an unaliased projects heading is absorbed *silently*.

    Regression for 2026-07-24: the resume used 'TECHNICAL PROJECTS', which was
    not an alias, so the heading and every project line became bullets on the
    preceding role — with **zero warnings**, so the convert-resume guard could
    not catch it. Silent absorption is the worst failure mode here.
    """
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph("Dev | Acme   Jan 2024 – Present")
    doc.add_paragraph("Shipped a thing.")
    doc.add_paragraph("TECHNICAL PROJECTS")
    doc.add_paragraph("Widget | github.com/jane/widget")
    doc.add_paragraph("Stack: Python, SQLite")
    doc.add_paragraph("Built the widget.")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert warnings == []
    assert len(facts.projects) == 1
    assert facts.projects[0].name == "Widget"
    # The role must NOT have swallowed the projects section.
    assert facts.work_history[0].bullets == ["Shipped a thing."]


def test_em_dash_project_header_parses(tmp_path: Path):
    """A9: `Name — Description — url` headers, not just `Name | url`."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("TECHNICAL PROJECTS")
    doc.add_paragraph("Widget — A Small Tool  —  github.com/jane/widget")
    doc.add_paragraph("Stack: Python, SQLite")
    doc.add_paragraph("Built it — and shipped it to production users.")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert warnings == []
    assert len(facts.projects) == 1
    assert facts.projects[0].name == "Widget — A Small Tool"
    assert facts.projects[0].url == "github.com/jane/widget"
    # The em-dash inside a prose bullet must not read as a header.
    assert facts.projects[0].bullets == [
        "Built it — and shipped it to production users."
    ]


def test_classifies_generic_certs(tmp_path: Path):
    """RP2: certs are detected by generic keywords (certified / certification /
    badge), not Casey-specific literals. The 'Associate' cert tier must not be
    mistaken for an associate degree, and Casey's Contentful + Skill Badge line
    still classifies as a cert (regression guard)."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("CERTIFICATIONS & EDUCATION")
    doc.add_paragraph("AWS Certified Solutions Architect - Associate, Amazon (2024)")
    doc.add_paragraph("PMP Certification, PMI (2023)")
    doc.add_paragraph("CompTIA Security+ ce Certification (2023)")
    doc.add_paragraph("Contentful Certified Professional + Personalization Skill Badge (2025)")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert len(facts.certifications) == 4  # all four routed to certifications
    assert facts.education == []  # the AWS Associate cert did not leak to education
    assert warnings == []


def test_classifies_degrees(tmp_path: Path):
    """RP2: degree lines route to education under the generic classifier."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("CERTIFICATIONS & EDUCATION")
    doc.add_paragraph("Bachelor of Science in Computer Science, University of Toronto (2020)")
    doc.add_paragraph("Computer Programming (Advanced Diploma), George Brown College (2024)")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert len(facts.education) == 2
    assert facts.certifications == []
    assert warnings == []


def test_section_header_aliases(tmp_path: Path):
    """RP4: alternate section headings (PROFILE / SKILLS / WORK EXPERIENCE /
    EDUCATION) resolve to the canonical sections so a resume that does not use
    Casey's exact headers still parses every section."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("PROFILE")
    doc.add_paragraph("A frontend developer.")
    doc.add_paragraph("SKILLS")
    doc.add_paragraph("Core: Python, TypeScript")
    doc.add_paragraph("WORK EXPERIENCE")
    doc.add_paragraph("Frontend Developer | Acme Inc  2022 – 2024")
    doc.add_paragraph("Built things.")
    doc.add_paragraph("EDUCATION")
    doc.add_paragraph("Bachelor of Science, University of Toronto (2020)")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)
    assert facts.summary == "A frontend developer."  # PROFILE -> SUMMARY
    assert "Python" in facts.skills_core  # SKILLS -> TECHNICAL SKILLS
    assert len(facts.work_history) == 1  # WORK EXPERIENCE -> PROFESSIONAL EXPERIENCE
    assert facts.work_history[0].employer == "Acme Inc"
    assert any("Bachelor" in e for e in facts.education)  # EDUCATION -> CERTS & EDU
    assert warnings == []


def test_orphan_bullet_warns_not_raises(tmp_path: Path):
    """RP5: a bullet before any role header is warned and skipped, not raised
    (previously a fatal PipelineError that aborted the whole conversion)."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph("Did some things before any role header.")  # orphan bullet
    doc.add_paragraph("Frontend Developer | Acme Inc  2022 – 2024")
    doc.add_paragraph("Built the thing.")
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)  # must not raise
    assert len(facts.work_history) == 1
    assert facts.work_history[0].bullets == ["Built the thing."]
    assert any("bullet before any role header" in w for w in warnings)


def test_unparseable_role_header_warns(tmp_path: Path):
    """RP5: a line with a pipe that does not match the role-header pattern (no
    parseable date) is warned and skipped, not raised. The valid role survives."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    doc.add_paragraph("Toronto, ON  |  jane@example.com")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph("Frontend Developer | Acme Inc  2022 – 2024")
    doc.add_paragraph("Built the thing.")
    doc.add_paragraph("Some Title | No Date Here")  # pipe but no parseable date
    path = tmp_path / "r.docx"
    doc.save(path)

    facts, warnings = parse_baseline(path)  # must not raise
    assert len(facts.work_history) == 1  # only the valid role
    assert any("unparseable role header" in w for w in warnings)


def test_role_line_accepts_parenthesized_dates():
    """2026-06 reformat: role dates wrapped in parentheses must parse, with the
    parens kept in `dates` (so the tailored resume renders them back) and the
    '(NDA)' employer suffix staying in the employer."""
    m = _ROLE_LINE_RE.match(
        "Web Developer (Contract) | Custom Jewelry Brand (Atelier Dacko) (2023 – Present)"
    )
    assert m is not None
    assert m.group("employer") == "Custom Jewelry Brand (Atelier Dacko)"
    assert m.group("dates") == "(2023 – Present)"

    m2 = _ROLE_LINE_RE.match("Web Developer (Contract) | AI Agency (NDA) (Jan 2026 – Apr 2026)")
    assert m2 is not None
    assert m2.group("employer") == "AI Agency (NDA)"
    assert m2.group("dates") == "(Jan 2026 – Apr 2026)"


def test_role_line_still_accepts_bare_dates():
    """The original bare-date format (no surrounding parens) must keep working."""
    m = _ROLE_LINE_RE.match("Sous Chef | Multiple Venues, Toronto 2015 – 2024")
    assert m is not None
    assert m.group("employer") == "Multiple Venues, Toronto"
    assert m.group("dates") == "2015 – 2024"


def test_parse_baseline_missing_file_errors(tmp_path: Path):
    with pytest.raises(PipelineError, match="not found"):
        parse_baseline(tmp_path / "nope.docx")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a, b, c", ["a", "b", "c"]),
        (
            "Shopify (Liquid, Custom Themes), HubSpot",
            ["Shopify (Liquid, Custom Themes)", "HubSpot"],
        ),
        ("foo (a, b, c), bar", ["foo (a, b, c)", "bar"]),
        ("  a  ,  b  ", ["a", "b"]),
        ("", []),
    ],
)
def test_split_skills_paren_aware(value: str, expected: list[str]):
    assert _split_skills(value) == expected

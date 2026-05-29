from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.errors import PipelineError
from jobhunt.resume.parse_docx import (
    _split_skills,
    parse_baseline,
    write_kb_markdown,
    write_verified_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "Resume.docx"
LEGACY_BASELINE = REPO_ROOT / "Resume.docx"
if not BASELINE.is_file() and LEGACY_BASELINE.is_file():
    BASELINE = LEGACY_BASELINE


@pytest.mark.skipif(not BASELINE.is_file(), reason="baseline .docx not present")
def test_parse_baseline_round_trip(tmp_path: Path):
    facts = parse_baseline(BASELINE)
    assert facts.name
    assert "Toronto" in facts.contact_line
    assert len(facts.work_history) == 4

    employers = {r.employer for r in facts.work_history}
    assert "Custom Jewelry Brand (Atelier Dacko)" in employers
    assert "Sous Chef & Team Lead" in {r.title for r in facts.work_history}

    # Familiar must stay separate. Bucket layout per Resume_Tailoring_Instructions §2:
    # Java/Spring Boot are Familiar (coursework-only); Python is Core (data_devops
    # bucket) — Casey writes and operates this CLI in Python daily, not Familiar.
    assert "Java" in facts.skills_familiar
    assert "Spring Boot" in facts.skills_familiar
    assert "Python" in facts.skills_data_devops
    assert "Java" not in facts.skills_core

    # Round-trip via verified.json.
    out = tmp_path / "verified.json"
    write_verified_json(facts, out)
    payload = json.loads(out.read_text())
    assert payload["name"] == facts.name
    assert len(payload["work_history"]) == 4

    # KB markdown writer leaves four files.
    kb = tmp_path / "kb"
    paths = write_kb_markdown(facts, kb)
    assert len(paths) == 4
    assert (kb / "profile" / "skills.md").read_text().count("## Familiar") == 1


def test_parse_baseline_missing_file_errors(tmp_path: Path):
    with pytest.raises(PipelineError, match="not found"):
        parse_baseline(tmp_path / "nope.docx")


def _build_minimal_resume_docx(path: Path) -> None:
    """Synthesize a baseline .docx covering all four sections, including a
    'Projects:' skills line. Independent of the real Resume.docx so it runs in
    CI (unlike the skipif-gated round-trip test above)."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Casey Hsu")
    doc.add_paragraph("me@example.com | example.com")
    doc.add_paragraph("SUMMARY")
    doc.add_paragraph("Full-stack developer with 2+ years of client work.")
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph("Core: JavaScript, TypeScript, React")
    doc.add_paragraph("Projects: React Native, Astro")
    doc.add_paragraph("Familiar: Java, Angular")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph("Web Developer (Contract) | Atelier Dacko\t2023 – Present")
    doc.add_paragraph("Built a Shopify storefront.", style="List Paragraph")
    doc.add_paragraph("CERTIFICATIONS & EDUCATION")
    doc.add_paragraph("Computer Programming & Analysis (Advanced Diploma), GBC, April 2024")
    doc.save(str(path))


def test_parse_baseline_populates_projects_tier(tmp_path: Path):
    p = tmp_path / "resume.docx"
    _build_minimal_resume_docx(p)
    facts = parse_baseline(p)
    assert facts.skills_projects == ["React Native", "Astro"]
    # Projects items must NOT leak into Core or Familiar.
    assert "React Native" not in facts.skills_core
    assert "React Native" not in facts.skills_familiar
    # Familiar still parses independently (Angular stays Familiar).
    assert facts.skills_familiar == ["Java", "Angular"]


def test_parse_baseline_projects_absent_yields_empty_list(tmp_path: Path):
    """A resume with no Projects skills line parses to an empty list, not an
    error — the tier is optional."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Casey Hsu")
    doc.add_paragraph("me@example.com | example.com")
    doc.add_paragraph("SUMMARY")
    doc.add_paragraph("Full-stack developer with 2+ years of client work.")
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph("Core: JavaScript, React")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph("Web Developer (Contract) | Atelier Dacko\t2023 – Present")
    doc.add_paragraph("Built a Shopify storefront.", style="List Paragraph")
    doc.add_paragraph("CERTIFICATIONS & EDUCATION")
    doc.add_paragraph("Diploma, GBC, April 2024")
    p = tmp_path / "resume.docx"
    doc.save(str(p))
    assert parse_baseline(p).skills_projects == []


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

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

    # PB1: the "Project Stack:" skills line populates a Core-grade bucket
    # distinct from Familiar (project-demonstrated, not academic).
    assert "FastAPI" in facts.skills_projects
    assert "FastAPI" not in facts.skills_familiar
    assert "FastAPI" not in facts.skills_core

    # PB3: the PROJECTS narrative section parses into structured projects.
    assert len(facts.projects) == 4
    names = [p.name for p in facts.projects]
    assert "jobhunt" in names
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
    assert len(payload["projects"]) == 4
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

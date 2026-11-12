"""Round trip for `scripts/audit_lane_resumes.parse_rendered` (Phase 6d.2).

The script audits what a rendered lane resume says by parsing the .docx back
into a TailoredResume, so it must track the renderer's layout. Rendering a
fixture with the real renderer and parsing it back pins that contract.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredProject,
    TailoredResume,
    TailoredRole,
    _enforce_no_fabrication,
)
from jobhunt.resume.render_docx import render

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_lane_resumes.py"
_spec = importlib.util.spec_from_file_location("audit_lane_resumes", _SCRIPT)
assert _spec and _spec.loader
audit_lane_resumes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_lane_resumes)

VERIFIED = {
    "name": "Jane Dev",
    "summary": "Shopify developer.",
    "skills_core": ["JavaScript", "HTML/CSS"],
    "skills_cms": ["Shopify (Liquid)", "WordPress"],
    "skills_familiar": ["React", "TypeScript"],
    "work_history": [
        {
            "title": "Developer (Contract)",
            "employer": "Acme, Toronto, ON",
            "dates": "Apr 2023 – Present",
            "bullets": ["x"],
        },
        {
            "title": "Line Cook",
            "employer": "Example Bistro",
            "dates": "Feb 2012 – Nov 2018",
            "bullets": ["Ran the grill."],
            "earlier_career": True,
        },
    ],
    "projects": [{"name": "Jobhunt", "url": "github.com/x/jobhunt", "stack": ["Python"]}],
}


def _tailored(projects: bool = True) -> TailoredResume:
    return TailoredResume(
        summary="Shopify developer since 2022 who builds CMS sites.",
        skills_categories=[
            TailoredCategory("CMS", ["Shopify (Liquid)", "WordPress"]),
            TailoredCategory("Core", ["JavaScript", "HTML/CSS"]),
            TailoredCategory("Familiar", ["React", "TypeScript"]),
        ],
        roles=[
            TailoredRole(
                "Developer (Contract)",
                "Acme, Toronto, ON",
                "Apr 2023 – Present",
                [
                    "Moved average position from 38 to 13, growing impressions 78%.",
                    "Built the theme.",
                ],
            ),
            TailoredRole(
                "Line Cook",
                "Example Bistro",
                "Feb 2012 – Nov 2018",
                ["Ran the grill."],
                earlier_career=True,
            ),
        ],
        certifications=["Example Certified Professional  |  Oct 2025"],
        education=["Advanced Diploma, Software | Sep 2019 – Apr 2022 | Example Polytechnic"],
        coursework=[],
        model="x",
        projects=[
            TailoredProject(
                "Jobhunt",
                "github.com/x/jobhunt",
                ["Python", "SQLite"],
                ["Scores postings with a local LLM."],
            )
        ]
        if projects
        else [],
    )


def _round_trip(tmp_path: Path, contact: str, projects: bool = True) -> TailoredResume:
    path = render(
        _tailored(projects), contact_line=contact, name="Jane Dev", out_path=tmp_path / "r.docx"
    )
    return audit_lane_resumes.parse_rendered(path, VERIFIED)


@pytest.mark.parametrize("contact", ["Shopify Developer  Toronto, ON | 555 | a@b.com", ""])
def test_rendered_lane_resume_parses_back(tmp_path: Path, contact: str) -> None:
    got = _round_trip(tmp_path, contact)
    assert got.summary == "Shopify developer since 2022 who builds CMS sites."
    assert [(c.name, c.items) for c in got.skills_categories] == [
        ("Core Expertise", ["Shopify (Liquid)", "WordPress", "JavaScript", "HTML/CSS"]),
        ("Familiar", ["React", "TypeScript"]),
    ]
    assert [(r.title, r.employer, r.dates, r.bullets, r.earlier_career) for r in got.roles] == [
        (
            "Developer (Contract)",
            "Acme, Toronto, ON",
            "Apr 2023 – Present",
            ["Moved average position from 38 to 13, growing impressions 78%.", "Built the theme."],
            False,
        ),
        ("Line Cook", "Example Bistro", "Feb 2012 – Nov 2018", ["Ran the grill"], True),
    ]
    assert [(p.name, p.url, p.stack, p.bullets) for p in got.projects] == [
        ("Jobhunt", "", ["Python", "SQLite"], ["Scores postings with a local LLM."])
    ]
    assert got.education == [
        "Advanced Diploma, Software  |  Sep 2019 – Apr 2022  |  Example Polytechnic"
    ]
    assert got.certifications == ["Example Certified Professional  |  Oct 2025"]


def test_no_projects_parses_to_empty_list(tmp_path: Path) -> None:
    assert _round_trip(tmp_path, "", projects=False).projects == []


def test_parsed_resume_passes_the_fabrication_guard(tmp_path: Path) -> None:
    got = _round_trip(tmp_path, "Shopify Developer  Toronto, ON | 555 | a@b.com")
    # The render lists this certification, so the profile must hold it. The
    # guard did not check certifications before 2026-11-12 (Phase C1).
    verified = {
        **VERIFIED,
        "skills_core": [*VERIFIED["skills_core"], "SQLite", "Python"],
        "certifications": ["Example Certified Professional  |  Oct 2025"],
    }
    _enforce_no_fabrication(got, verified)

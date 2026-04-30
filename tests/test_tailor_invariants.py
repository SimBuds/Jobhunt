from __future__ import annotations

import pytest

from jobhunt.errors import PipelineError
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredResume,
    TailoredRole,
    _enforce_no_fabrication,
)

VERIFIED = {
    "summary": "Full-stack JavaScript developer with 2+ years of professional client work.",
    "work_history": [
        {"employer": "Acme", "dates": "2023 – Present", "title": "Dev"},
        {"employer": "BetaCo", "dates": "2021 – 2023", "title": "Dev"},
    ],
    "skills_core": ["JavaScript", "TypeScript", "React"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)"],
    "skills_data_devops": ["Docker"],
    "skills_ai": ["Local LLM hosting with Ollama"],
    "skills_familiar": ["Java", "Python"],
}


def _make(
    *,
    roles=None,
    skills_categories=None,
    summary="Full-stack JavaScript developer with 2+ years building things.",
) -> TailoredResume:
    return TailoredResume(
        summary=summary,
        skills_categories=skills_categories
        or [TailoredCategory("Core", ["JavaScript"]), TailoredCategory("Familiar", ["Java"])],
        roles=roles
        or [
            TailoredRole("Dev", "Acme", "2023 – Present", ["b1"]),
            TailoredRole("Dev", "BetaCo", "2021 – 2023", ["b1"]),
        ],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def test_passes_when_roles_and_skills_match_verified():
    _enforce_no_fabrication(_make(), VERIFIED)


def test_rejects_missing_role():
    bad = _make(roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["b1"])])
    with pytest.raises(PipelineError, match="missing"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_invented_employer():
    bad = _make(
        roles=[
            TailoredRole("Dev", "Acme", "2023 – Present", ["b1"]),
            TailoredRole("Dev", "BetaCo", "2021 – 2023", ["b1"]),
            TailoredRole("Staff", "FAANG Inc", "2024", ["b1"]),
        ]
    )
    with pytest.raises(PipelineError, match="extra"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_familiar_skill_in_core():
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["JavaScript", "Python"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="Familiar"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_invented_skill():
    bad = _make(
        skills_categories=[
            TailoredCategory("Core", ["JavaScript", "Rust"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    with pytest.raises(PipelineError, match="not in verified"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_summary_with_unverified_seniority():
    bad = _make(summary="Senior Full Stack Developer with 2+ years of experience.")
    with pytest.raises(PipelineError, match="seniority token"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_rejects_summary_leading_with_culinary():
    bad = _make(
        summary="Nine years of leadership in high-pressure culinary environments. "
        "Also a developer."
    )
    with pytest.raises(PipelineError, match="culinary"):
        _enforce_no_fabrication(bad, VERIFIED)


def test_allows_culinary_clause_at_end():
    ok = _make(
        summary="Full-stack JavaScript developer with 2+ years of client work. "
        "Prior experience leading culinary teams."
    )
    _enforce_no_fabrication(ok, VERIFIED)


def test_allows_seniority_when_present_in_verified_summary():
    verified = {**VERIFIED, "summary": "Senior full-stack developer with 2+ years."}
    ok = _make(summary="Senior full-stack developer with 2+ years of work.")
    _enforce_no_fabrication(ok, verified)


def test_paren_substring_tolerated():
    """Tailored skill 'Shopify (Liquid)' should match verified 'Shopify (Liquid, Custom Themes)'."""
    ok = _make(
        skills_categories=[
            TailoredCategory("CMS", ["Shopify (Liquid)"]),
            TailoredCategory("Familiar", ["Java"]),
        ]
    )
    _enforce_no_fabrication(ok, VERIFIED)

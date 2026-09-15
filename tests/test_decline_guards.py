"""Deterministic checks that clear model declines the posting does not back.

Cases are drawn from the 2026-09-15 audit of 253 `lite` scores.
"""

from __future__ import annotations

import pytest

from jobhunt.pipeline._decline_guards import _years_asks, decline_is_unsupported

# --- years asks --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5+ years experience building web apps", [5]),
        ("Backend Mastery: 4-8 years of software engineering", [4]),
        ("3 to 5 years of professional experience", [3]),
        ("8+ years building cloud infrastructure", [8]),
        ("10+ years shipping production software", [10]),
        ("Several years (5+) of professional software development", [5]),
        ("seven years of hands-on experience", [7]),
        ("5&#43; years with React", [5]),
    ],
)
def test_years_asks_reads_requirement_forms(text: str, expected: list[int]) -> None:
    assert _years_asks(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "a spot on the Forbes Cloud 100 for four years in a row",
        "Named five years in a row to the Globe & Mail's top employers",
        "unlimited sick days per year",
        "founded 20 years ago",
    ],
)
def test_years_asks_ignores_company_copy(text: str) -> None:
    assert _years_asks(text) == []


# --- years declines ----------------------------------------------------------


def test_clears_years_decline_within_limit() -> None:
    """3 YoE -> limit 6. A 5+ ask does not exceed it (Senior Full Stack
    Developer, declined `years_gap`)."""
    assert decline_is_unsupported(
        "years_gap", "Senior Full Stack Developer", "5+ years of experience", 3
    )


def test_clears_snake_case_years_code() -> None:
    assert decline_is_unsupported(
        "years_required_exceeds_limit", "Software Engineer", "4 years of experience", 3
    )


def test_keeps_years_decline_above_limit() -> None:
    assert not decline_is_unsupported(
        "years_required_exceeds_limit",
        "Principal Applied AI/ML Scientist",
        "8+ years in ML / applied research roles",
        3,
    )


def test_keeps_years_decline_when_any_ask_exceeds() -> None:
    desc = "3+ years of Python. 7+ years of distributed systems experience."
    assert not decline_is_unsupported("years_gap", "Staff Engineer", desc, 3)


def test_clears_years_decline_triggered_by_marketing_copy() -> None:
    desc = "Forbes Cloud 100 for four years in a row. React and TypeScript."
    assert decline_is_unsupported("years_exceeded", "Software Engineer", desc, 3)


def test_years_guard_needs_years_experience() -> None:
    assert not decline_is_unsupported("years_gap", "Software Engineer", "5+ years", None)


# --- people-management declines ---------------------------------------------


def test_clears_management_decline_on_ic_title_without_duties() -> None:
    reason = "Title is Senior/Lead with implied people-management duties"
    desc = "Build and ship React features with the platform team."
    assert decline_is_unsupported(reason, "Lead Software Engineer", desc, 3)


def test_keeps_management_decline_when_body_names_duties() -> None:
    reason = "Title is Senior/Lead with implied people-management duties"
    desc = "You will have 4 direct reports and run performance reviews."
    assert not decline_is_unsupported(reason, "Lead Software Engineer", desc, 3)


def test_keeps_management_decline_on_management_title() -> None:
    reason = "Title is people-management (Engineering Manager)"
    assert not decline_is_unsupported(reason, "Engineering Manager", "Write code.", 3)


def test_mixed_reason_needs_every_trigger_unsupported() -> None:
    reason = "people-management implied and years required exceed limit"
    desc = "Lead a team of five engineers. 5+ years of experience."
    assert not decline_is_unsupported(reason, "Senior Software Engineer", desc, 3)


# --- ineligible reasons and titles ------------------------------------------


def test_keeps_reason_citing_another_trigger() -> None:
    reason = "Domain requires regulated experience (securities) and 5+ years"
    assert not decline_is_unsupported(reason, "Software Engineer", "5+ years", 3)


@pytest.mark.parametrize(
    "title",
    ["Senior GTM Engineering Analyst", "Scrum Master", "Senior Tax Planner"],
)
def test_keeps_decline_on_non_engineering_title(title: str) -> None:
    assert not decline_is_unsupported("years_gap", title, "4 years of experience", 3)


def test_ignores_unrelated_reason() -> None:
    assert not decline_is_unsupported(
        "4+ tier-1 requirements the candidate cannot satisfy", "Software Engineer", "", 3
    )

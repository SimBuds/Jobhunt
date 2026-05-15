"""Deterministic override that rejects bogus seniority-only auto-declines.

qwen3.5:9b often emits decline reasons like "Title implies Lead seniority"
or "Staff seniority mismatch" even though the May 2026 score prompt allows
IC roles at Senior/Lead/Staff/Principal/Architect titles. The filter in
score.py nullifies these so the score band stands.

Hard people-management titles (Manager/Director/Head of/VP) are still
auto-declines because Casey is an IC engineer, not a people leader.
"""

from __future__ import annotations

from jobhunt.pipeline.score import _is_bogus_senior_decline


def test_plain_senior_decline_is_overridden() -> None:
    assert _is_bogus_senior_decline(
        "Title implies Senior/Staff/Lead seniority", "Senior Software Engineer"
    )
    assert _is_bogus_senior_decline("Title seniority mismatch", "Sr. Developer")
    assert _is_bogus_senior_decline(
        "Title implies Senior seniority", "Senior Full Stack Engineer"
    )


def test_staff_lead_principal_architect_title_is_overridden() -> None:
    """May 2026: IC roles at Staff/Lead/Principal/Architect are valid for Casey
    when the JD reads IC-coding-heavy. The title alone is NOT a trigger."""
    assert _is_bogus_senior_decline(
        "Title implies Staff seniority", "Staff Software Engineer"
    )
    assert _is_bogus_senior_decline(
        "Title implies Lead seniority", "Lead Full Stack Web Developer"
    )
    assert _is_bogus_senior_decline(
        "Principal-level seniority required", "Principal Engineer"
    )
    assert _is_bogus_senior_decline(
        "Architect title implies seniority gap", "Solutions Architect"
    )


def test_decline_preserved_when_reason_cites_management() -> None:
    """When the decline reason explicitly names management responsibilities,
    the decline is real and must be kept."""
    assert not _is_bogus_senior_decline(
        "Title implies Lead seniority and JD requires mentoring",
        "Senior Lead Engineer",
    )
    assert not _is_bogus_senior_decline(
        "Senior role requires managing 4+ direct reports", "Senior Engineer"
    )
    assert not _is_bogus_senior_decline(
        "Lead seniority with headcount ownership", "Lead Engineer"
    )


def test_manager_director_titles_still_auto_decline() -> None:
    """Casey is an IC. Manager/Director/Head of/VP titles always decline."""
    assert not _is_bogus_senior_decline(
        "Title is people-management (Engineering Manager)", "Engineering Manager"
    )
    assert not _is_bogus_senior_decline(
        "Senior seniority mismatch", "Director of Engineering"
    )
    assert not _is_bogus_senior_decline(
        "Lead seniority mismatch", "Head of Platform"
    )
    assert not _is_bogus_senior_decline(
        "Staff seniority mismatch", "VP of Engineering"
    )


def test_no_decline_reason_is_passthrough() -> None:
    assert not _is_bogus_senior_decline(None, "Senior Software Engineer")
    assert not _is_bogus_senior_decline("", "Senior Software Engineer")


def test_non_senior_decline_is_preserved() -> None:
    assert not _is_bogus_senior_decline(
        "Domain requires regulated experience (medical devices)",
        "Software Developer",
    )
    assert not _is_bogus_senior_decline(
        "Stack mismatch (.NET vs JavaScript/TypeScript)", "IT Developer"
    )

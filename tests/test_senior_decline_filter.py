"""Deterministic override that rejects bogus 'Senior'-only auto-declines.

qwen3.5:9b often emits decline reasons like "Title implies Senior seniority"
even though the score prompt forbids declining on "Senior" alone. The filter
in score.py nullifies these so the score band stands.
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


def test_lead_decline_with_leadership_is_preserved() -> None:
    assert not _is_bogus_senior_decline(
        "Title implies Lead seniority and JD requires mentoring",
        "Senior Lead Engineer",
    )
    assert not _is_bogus_senior_decline(
        "Title is people-management (Engineering Manager)", "Engineering Manager"
    )


def test_staff_title_decline_is_preserved_even_if_reason_says_senior() -> None:
    # Reason mentions "Senior" but title contains a real trigger word.
    assert not _is_bogus_senior_decline(
        "Title implies Senior/Staff seniority", "Staff Software Engineer"
    )
    assert not _is_bogus_senior_decline(
        "Title implies Senior/Lead seniority", "Lead Full Stack Web Developer"
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

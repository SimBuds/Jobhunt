"""Tests for ingest-time pre-filters: management titles + freshness window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobhunt.ingest._filter import (
    is_management_title,
    is_within_age_window,
)

# --- is_management_title ----------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Engineering Manager",
        "Senior Engineering Manager",
        "Director of Engineering",
        "Director, Software Engineering",
        "VP Product",
        "VP, Customer Engineering",
        "Vice President, Engineering",
        "Head of Data",
        "Head of Platform",
        "People Manager",
        "Chief Technology Officer",
        "Chief Information Officer",
    ],
)
def test_is_management_title_matches_hard_titles(title: str) -> None:
    assert is_management_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Sr. Software Engineer",
        "Lead Backend Developer",
        "Staff Engineer",
        "Staff Software Engineer",
        "Principal Architect",
        "Principal Engineer",
        "Software Architect",
        "Solutions Architect",
        "Engineering",
        "Software Engineer",
        "Full Stack Developer",
        "Frontend Developer (Intermediate)",
        "SEO Automation Engineer",
    ],
)
def test_is_management_title_keeps_ic_titles(title: str) -> None:
    assert is_management_title(title) is False


def test_is_management_title_handles_empty() -> None:
    assert is_management_title(None) is False
    assert is_management_title("") is False


def test_is_management_title_word_boundaries() -> None:
    # "managerial" should not match the bare "manager" word — word boundary
    # protects substring collisions.
    assert is_management_title("Managerial Trainee") is False
    # "Markham" must not collide with the regex
    assert is_management_title("Software Engineer - Markham") is False


# --- is_within_age_window ---------------------------------------------------


def _days_ago(n: int, *, naive: bool = False) -> datetime:
    dt = datetime.now(UTC) - timedelta(days=n)
    if naive:
        return dt.replace(tzinfo=None)
    return dt


def test_within_age_window_fresh() -> None:
    assert is_within_age_window(_days_ago(5), 14) is True


def test_within_age_window_stale() -> None:
    assert is_within_age_window(_days_ago(30), 14) is False


def test_within_age_window_boundary() -> None:
    # Exactly 14 days old should pass (>=, not >).
    assert is_within_age_window(_days_ago(14) + timedelta(seconds=10), 14) is True


def test_within_age_window_none_passes() -> None:
    # Workday-gap pass-through.
    assert is_within_age_window(None, 14) is True


def test_within_age_window_zero_disables() -> None:
    # max_age_days=0 disables — everything passes.
    assert is_within_age_window(_days_ago(365), 0) is True
    assert is_within_age_window(None, 0) is True


def test_within_age_window_negative_disables() -> None:
    assert is_within_age_window(_days_ago(365), -1) is True


def test_within_age_window_naive_datetime_coerced() -> None:
    # Naive datetime treated as UTC.
    assert is_within_age_window(_days_ago(5, naive=True), 14) is True
    assert is_within_age_window(_days_ago(30, naive=True), 14) is False


def test_within_age_window_future_passes() -> None:
    # Edge case: a posted_at in the future (clock skew, bad data) shouldn't
    # be rejected — it's clearly "fresh".
    future = datetime.now(UTC) + timedelta(days=2)
    assert is_within_age_window(future, 14) is True

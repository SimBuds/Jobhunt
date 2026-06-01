from __future__ import annotations

import pytest

from jobhunt.ingest._filter import is_explicit_junior_title, is_senior_title


@pytest.mark.parametrize(
    "title,expected",
    [
        # Hits — Senior+ band.
        ("Senior Software Engineer", True),
        ("Sr. Frontend Developer", True),
        ("Sr Software Engineer", True),
        ("Lead Software Engineer", True),
        ("Lead Frontend Developer", True),
        ("Staff Engineer - Growth Platform", True),
        ("Staff Software Engineer, Stripe Dashboard", True),
        ("Principal Software Engineer, AI", True),
        ("Software Architect", True),
        ("Solutions Architect", True),
        ("Senior Full Stack Developer (Contractor)", True),
        # Misses — Junior to Mid IC titles must pass through.
        ("Software Engineer", False),
        ("Frontend Engineer", False),
        ("Full-Stack Developer", False),
        ("Shopify Developer", False),
        ("Junior Software Engineer", False),
        ("Intermediate Developer", False),
        ("Mid-Level Frontend Engineer", False),
        ("Developer I", False),
        ("Developer II", False),
        ("Associate Software Developer", False),
        ("Web Developer", False),
        ("Application Developer", False),
        # Empty / None defensive.
        ("", False),
        (None, False),
    ],
)
def test_is_senior_title(title: str | None, expected: bool) -> None:
    assert is_senior_title(title) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        # Co-op / intern / campus / early-talent markers (2026-06 addition) —
        # these nullify a bogus "Senior-band" score decline in pipeline.score.
        ("Early Talent Programs and Strategy Co-op", True),
        ("Business Systems Analyst Co-op", True),
        ("Software Developer Co-op", True),
        ("Campus Recruitment Fall 2026", True),
        ("Software Engineer Intern", True),
        ("Marketing Internship", True),
        ("Software Practicum", True),
        # Existing junior/mid markers still match.
        ("Junior Software Engineer", True),
        ("Intermediate Developer", True),
        ("Developer I", True),
        ("New Grad Software Engineer", True),
        # Must NOT false-match on lookalikes or senior/neutral titles.
        ("Internal Communications Specialist", False),
        ("International Sales Manager", False),
        ("Cooperative Bank Engineer", False),
        ("Senior Software Engineer", False),
        ("Software Developer", False),
        ("", False),
        (None, False),
    ],
)
def test_is_explicit_junior_title(title: str | None, expected: bool) -> None:
    assert is_explicit_junior_title(title) is expected

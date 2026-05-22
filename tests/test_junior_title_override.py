"""Junior-title override on Senior-band declines (2026-05-22)."""
from __future__ import annotations

import pytest

from jobhunt.ingest._filter import is_explicit_junior_title


@pytest.mark.parametrize(
    "title,expected",
    [
        # Hits — explicit Junior/Mid-band markers.
        ("Junior Full Stack Developer (.NET / Cloud)", True),
        ("Jr. Frontend Developer", True),
        ("Intermediate Software Engineer", True),
        ("Mid Software Engineer", True),
        ("Mid-Level Frontend Engineer", True),
        ("Associate Developer", True),
        ("Developer I", True),
        ("Developer II", True),
        ("Engineer I", True),
        ("Engineer II", True),
        ("Entry Level Software Engineer", True),
        ("Entry-Level Web Developer", True),
        ("New Grad Software Engineer", True),
        ("Graduate Developer", True),
        # Misses — Senior-band or band-neutral titles must not match.
        ("Senior Software Engineer", False),
        ("Staff Engineer", False),
        ("Lead Frontend Developer", False),
        ("Software Engineer", False),
        ("Frontend Developer", False),
        ("Full Stack Developer", False),
        ("Web Developer", False),
        # Edge: "Associate Consultant" (konradgroup-style) — Associate marks
        # a junior-band consulting role, so it counts.
        ("Associate Consultant", True),
        # Defensive
        ("", False),
        (None, False),
    ],
)
def test_is_explicit_junior_title(title: str | None, expected: bool) -> None:
    assert is_explicit_junior_title(title) is expected

from __future__ import annotations

import pytest

from jobhunt.ingest._filter import is_gta_eligible


@pytest.mark.parametrize(
    "loc,expected",
    [
        ("Toronto, ON", True),
        ("Mississauga, Ontario, Canada", True),
        ("North York", True),
        ("Remote, Canada", True),
        ("Remote (Ontario)", True),
        ("Remote — EST", True),
        ("Remote, Anywhere", False),
        ("Remote, US", False),
        ("Remote, EMEA", False),
        ("Remote", False),
        # Regression: "on" as English word must not match the ON province hint.
        ("Remote (on-call) — US", False),
        ("Remote, working on-call rotation, US", False),
        # Regression: comma-delimited ON province code is still a Canada hint.
        ("Remote, ON", True),
        ("Remote, ON, Canada", True),
        # KW corridor is in scope (README promises GTA + 100 km).
        ("Waterloo, ON", True),
        ("Kitchener, Ontario", True),
        ("Cambridge, ON, Canada", True),
        ("Guelph, ON", True),
        # May 2026: Barrie added (~90 km, in scope).
        ("Barrie, ON", True),
        ("Barrie, Ontario", True),
        # May 2026: weak Canada hint (EST / Eastern Time) loses to a non-Canada
        # anchor in the same string. US Eastern Time is also EST.
        ("Remote (Eastern Time, US-only)", False),
        ("Remote, Eastern Time, United States", False),
        # But a weak hint alone (no non-Canada anchor) still accepts.
        ("Remote, Eastern Time", True),
        ("San Francisco, CA", False),
        ("New York, NY", False),
        ("", False),
        (None, False),
    ],
)
def test_is_gta_eligible(loc, expected):
    assert is_gta_eligible(loc) is expected

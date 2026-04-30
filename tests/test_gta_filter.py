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
        ("San Francisco, CA", False),
        ("New York, NY", False),
        ("", False),
        (None, False),
    ],
)
def test_is_gta_eligible(loc, expected):
    assert is_gta_eligible(loc) is expected

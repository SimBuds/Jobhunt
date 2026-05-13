from __future__ import annotations

import pytest

from jobhunt.discover.slug_candidates import candidates


@pytest.mark.parametrize(
    "name,expected",
    [
        # Two-word with suffix that's part of the real slug (Konrad's board is konradgroup)
        ("Konrad Group", ["konradgroup", "konrad"]),
        # Comma kills tail; suffix strip leaves the brand
        ("Magna International, Inc", ["magnainternational", "magna"]),
        ("Magna International, Inc.", ["magnainternational", "magna"]),
        # Diacritics normalized
        ("Beauté Co.", ["beauteco", "beaute"]),
        # Single word, no suffix
        ("Okta", ["okta"]),
        # Mixed case
        ("eBay", ["ebay"]),
        # Apostrophes stripped
        ("McDonald's", ["mcdonalds"]),
        # Multi-word, no recognized suffix
        ("Bank of Montreal", ["bankofmontreal", "bank"]),
        # Multi-suffix chain
        ("Acme Holdings LLC", ["acmeholdingsllc", "acme"]),
        # Plain inc
        ("BitGo Inc", ["bitgoinc", "bitgo"]),
        # Empty / blank
        ("", []),
        ("   ", []),
        # Too short after normalization
        ("AI", []),
        # Staffing agency exclusions (the noisy ones from this user's DB)
        ("Astra North Infoteck Inc.", []),
        ("Targeted Talent", []),
        ("Insight Global", []),
        ("hireVouch", []),
        ("Ignite Talent Solutions", []),
        ("ABC Staffing", []),
        ("Recruit Inc", []),
        # Non-string input
        (None, []),
        (123, []),
    ],
)
def test_candidates(name: object, expected: list[str]) -> None:
    assert candidates(name) == expected


def test_candidates_returns_at_most_three() -> None:
    # Even with many words, output is capped at 3
    out = candidates("Alpha Beta Gamma Delta Epsilon Holdings")
    assert len(out) <= 3


def test_candidates_dedupes() -> None:
    # Single word — joined, stripped, and first-word are all the same
    assert candidates("Stripe") == ["stripe"]


def test_candidates_drops_60_char_overflow() -> None:
    # Pathological input that would produce a >60-char slug should not crash
    long_name = "a" * 80
    assert candidates(long_name) == []

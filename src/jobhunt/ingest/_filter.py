"""GTA + Remote-Canada location filter.

Matches a job's free-text location string against the GTA city allowlist.
Also accepts Remote-Canada / Remote-Ontario postings as eligible.
"""

from __future__ import annotations

import re

GTA_CITIES = (
    "toronto",
    "mississauga",
    "brampton",
    "hamilton",
    "oakville",
    "markham",
    "vaughan",
    "burlington",
    "oshawa",
    "richmond hill",
    "pickering",
    "ajax",
    "whitby",
    "milton",
    "north york",
    "scarborough",
    "etobicoke",
)

_NON_CANADA_REMOTE = re.compile(
    r"\b(us(a)?|united states|emea|europe|uk|asia|latam|anywhere)\b", re.IGNORECASE
)
_CANADA_HINT = re.compile(
    r"\b(canada|canadian|ontario|toronto|gta|on\b|est\b|eastern\s+time)\b", re.IGNORECASE
)


def is_gta_eligible(location: str | None) -> bool:
    """True if the location is in the GTA or a Canada-eligible remote posting."""
    if not location:
        return False
    loc = location.lower()
    if any(city in loc for city in GTA_CITIES):
        return True
    if "remote" not in loc:
        return False
    if _CANADA_HINT.search(loc):
        return True
    if _NON_CANADA_REMOTE.search(loc):
        return False
    # Bare "Remote" with no country qualifier — too ambiguous, skip.
    return False

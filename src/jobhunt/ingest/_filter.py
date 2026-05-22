"""GTA + Remote-Canada location filter.

Matches a job's free-text location string against the GTA city allowlist.
Also accepts Remote-Canada / Remote-Ontario postings as eligible.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

RemoteType = Literal["onsite", "hybrid", "remote", "unknown"]

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
    # Kitchener-Waterloo corridor — within the 100 km radius the README promises.
    "waterloo",
    "kitchener",
    "cambridge",
    "guelph",
    # Barrie (~90 km north of Toronto, well within the 100 km radius).
    "barrie",
)

_NON_CANADA_REMOTE = re.compile(
    r"\b(us(a)?|united states|emea|europe|uk|asia|latam|anywhere)\b", re.IGNORECASE
)
# Strong Canada hints — any one of these is sufficient.
_CANADA_STRONG = re.compile(
    r"\b(?:canada|canadian|ontario|toronto|gta)\b", re.IGNORECASE
)
# Weak Canada hints — "EST"/"Eastern Time"/the "on" province code. May 2026:
# these are too noisy to act on alone (US Eastern Time is also EST; "Remote
# (Eastern Time, US-only)" was being accepted as Canadian). Require BOTH a
# weak hint AND no non-Canada anchor in the same string before treating as
# Canada-eligible.
_CANADA_WEAK = re.compile(
    r"(?:\b(?:est|eastern\s+time)\b"
    r"|(?:^|,\s*|\(\s*)on(?=\s*(?:,|\)|$|\s+canada)))",
    re.IGNORECASE,
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
    # Strong Canada hint — accept.
    if _CANADA_STRONG.search(loc):
        return True
    # Any non-Canada anchor wins over a weak hint. "Remote (Eastern Time, US)"
    # has both "eastern time" and "US" — the latter must dominate.
    if _NON_CANADA_REMOTE.search(loc):
        return False
    # Weak hint with no non-Canada anchor — accept. "Remote, EST" is
    # legitimately Canadian here.
    if _CANADA_WEAK.search(loc):
        return True
    # Bare "Remote" with no country qualifier — too ambiguous, skip.
    return False


# People-management title regex. Drops Manager/Director/Head of/VP/Vice
# President/Chief X Officer at ingest so the score prompt doesn't have to
# decline them. Word-boundaried to avoid false matches like "Markham" or
# "managerial" within a longer non-management word.
#
# DOES NOT match Senior / Lead / Staff / Principal / Architect — those are
# handled separately by `is_senior_title`, which is YoE-gated at ingest in
# scan_cmd (drops them when `applicant.years_experience < 4`).
_MANAGEMENT_TITLE_RE = re.compile(
    r"\b(?:manager|director|head\s+of|vp|vice\s+president|"
    r"engineering\s+manager|people\s+manager|chief\s+\w+\s+officer)\b",
    re.IGNORECASE,
)


def is_management_title(title: str | None) -> bool:
    """True when the title is a people-management role we shouldn't apply to."""
    if not title:
        return False
    return bool(_MANAGEMENT_TITLE_RE.search(title))


# Research / ML-science / data-platform title regex. Opt-in via
# `[ingest] drop_research_titles = true` for profiles where these roles are
# never a fit (frontend / CMS / full-stack devs). Drops at ingest so the
# scorer doesn't burn budget on roles it will deterministically decline.
#
# Matches: Applied Scientist, ML/AI Scientist, Machine Learning Engineer,
# Research Engineer/Scientist, Data Scientist, Data Engineer, Data Platform,
# Quant / Quantitative Researcher.
#
# DOES NOT match plain "Engineer" or "Software Engineer" — only when paired
# with a research/ML/data-platform qualifier.
_RESEARCH_TITLE_RE = re.compile(
    r"\b(?:"
    r"applied\s+(?:ai/?ml\s+)?scientist"
    r"|(?:ml|ai|machine\s+learning)\s+(?:scientist|engineer|researcher)"
    r"|research\s+(?:scientist|engineer)"
    r"|data\s+(?:scientist|engineer)"
    r"|data\s+platform"
    r"|quant(?:itative)?\s+(?:researcher|analyst|developer|engineer)"
    r")\b",
    re.IGNORECASE,
)


def is_research_title(title: str | None) -> bool:
    """True when the title is an ML/research/data-platform role.

    Opt-in filter — only enable for profiles where these roles are never a
    fit. See `[ingest] drop_research_titles` in `config.toml`.
    """
    if not title:
        return False
    return bool(_RESEARCH_TITLE_RE.search(title))


# Senior-band title regex. Drives an opt-in ingest drop in
# `scan_cmd._ingest_all`: when `applicant.include_senior_roles` is False,
# these titles are filtered out before scoring. The user opts in/out via
# the setup wizard — no YoE inference is applied.
_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|sr\.?|lead|staff|principal|architect)\b",
    re.IGNORECASE,
)


def is_senior_title(title: str | None) -> bool:
    """True when the title sits in the Senior+ band."""
    if not title:
        return False
    return bool(_SENIOR_TITLE_RE.search(title))


# Explicit junior / mid-band markers. Used by the score pipeline to override
# a "Senior-band" decline reason when the title literally says Junior, since
# qwen3.5:9b has been observed reading senior-coded JD body language and
# declining a Junior-titled posting on that basis (e.g. "Junior Full Stack
# Developer (.NET / Cloud)" got the Senior-band decline reason). Title is
# the canonical band signal; body inference loses.
_JUNIOR_TITLE_RE = re.compile(
    r"\b(?:junior|jr\.?|intermediate|mid(?:-?level)?|associate|"
    r"developer\s+i{1,2}\b|engineer\s+i{1,2}\b|"
    r"entry[-\s]?level|new\s+grad|graduate)\b",
    re.IGNORECASE,
)


def is_explicit_junior_title(title: str | None) -> bool:
    """True when the title carries an explicit Junior/Intermediate/Mid marker."""
    if not title:
        return False
    return bool(_JUNIOR_TITLE_RE.search(title))


def is_within_age_window(
    posted_at: datetime | None,
    max_age_days: int,
) -> bool:
    """True when the job is fresh enough to ingest.

    - `max_age_days <= 0` disables the filter (returns True).
    - `posted_at is None` returns True (adapter gap — don't penalize;
      e.g. Workday doesn't populate this field yet).
    - Otherwise: True when `posted_at >= now - max_age_days`.

    Naive datetimes are coerced to UTC for comparison.
    """
    if max_age_days <= 0:
        return True
    if posted_at is None:
        return True
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    return posted_at >= cutoff


def classify_remote_type(*, location: str | None, extra: str | None = None) -> RemoteType:
    """Classify a posting as onsite/hybrid/remote from free-text signals.

    `extra` is an optional second string (e.g. Lever's commitment field, or
    a description excerpt) checked alongside the location.
    """
    blob = " ".join(s for s in (location, extra) if s).lower()
    if not blob:
        return "unknown"
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob or "work from home" in blob or "wfh" in blob:
        return "remote"
    if any(city in blob for city in GTA_CITIES):
        return "onsite"
    return "unknown"

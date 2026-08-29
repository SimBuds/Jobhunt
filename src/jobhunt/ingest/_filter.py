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
# GTA city match, word-boundaried so a city name can't fire inside a longer
# word. Multi-word entries ("richmond hill") still match as phrases.
_GTA_CITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in GTA_CITIES) + r")\b"
)
# Non-Canada anchor veto for the city branch (June 2026): GTA city names are
# not globally unique — Cambridge MA, Burlington VT, Richmond Hill NY,
# Hamilton NZ, Markham IL, Waterloo Belgium, Milton Keynes UK all passed the
# bare substring match. A GTA city only accepts when no non-Canada anchor
# appears in the same string. Two tiers: country/region names (sibling of
# _NON_CANADA_REMOTE, which stays remote-branch-only), and comma-delimited
# US state codes. "ON" is excluded (Ontario). "CA" is excluded too —
# aggregators emit "Toronto, CA" meaning the country code, so treating it as
# California would veto real GTA rows.
_NON_CANADA_ANCHOR = re.compile(
    r"\b(?:us(?:a)?|united states|u\.s\.(?:a\.)?|uk|united kingdom|england|"
    r"scotland|wales|new zealand|australia|belgium|ireland|germany|france|"
    r"netherlands|emea|europe|asia|latam|milton keynes)\b"
    r"|,\s*(?:al|ak|az|ar|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|"
    r"mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|"
    r"ut|vt|va|wa|wv|wi|wy|dc)\b",
    re.IGNORECASE,
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
    if _GTA_CITY_RE.search(loc):
        # Homonym guard: the city name only counts when nothing in the same
        # string anchors it outside Canada ("Cambridge, MA" is not ours).
        return not _NON_CANADA_ANCHOR.search(loc)
    if "remote" not in loc:
        return False
    # Strong Canada hint — accept.
    if _CANADA_STRONG.search(loc):
        return True
    # Any non-Canada anchor wins over a weak hint. "Remote (Eastern Time, US)"
    # has both "eastern time" and "US" — the latter must dominate.
    if _NON_CANADA_REMOTE.search(loc):
        return False
    # Weak hint with no non-Canada anchor is accepted. Bare "Remote" remains
    # too ambiguous.
    return bool(_CANADA_WEAK.search(loc))


# People-management title regex. Drops Manager/Director/Head of/VP/Vice
# President/Chief X Officer at ingest so the score prompt doesn't have to
# decline them. Word-boundaried to avoid false matches like "Markham" or
# "managerial" within a longer non-management word.
#
# DOES NOT match Senior / Lead / Staff / Principal / Architect — those are
# handled separately by `is_senior_title`, which is YoE-gated at ingest in
# scan_cmd (drops them when `applicant.years_experience < 4`).
def location_search_terms(
    *, city: str = "", region: str = "", country: str = ""
) -> tuple[str, ...]:
    """Board-side `searchText` probes for the applicant's location.

    Used by adapters whose boards have no server-side location filter and must
    narrow with free-text queries before `is_gta_eligible` does the real work.

    Ordered city, region, then remote-in-country, because a region query
    empirically returns a superset of its cities and the remote term adds rows
    neither covers. Blank fields are skipped and duplicates dropped, so a
    partially-filled profile still yields usable probes.

    Returns `()` when nothing is configured — callers treat that as "no
    narrowing possible" and fall back to a blank scan rather than inventing a
    location. Replaces a hardcoded ("Toronto", "Ontario", "Remote, Canada")
    tuple that ignored the applicant profile entirely.
    """
    terms: list[str] = []
    for term in (city.strip(), region.strip()):
        if term and term not in terms:
            terms.append(term)
    if country.strip():
        remote = f"Remote, {country.strip()}"
        if remote not in terms:
            terms.append(remote)
    return tuple(terms)


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
# Matches: Applied Scientist, ML/AI Scientist/Researcher, Research
# Engineer/Scientist, Data Scientist, Data Engineer, Data Platform,
# Quant / Quantitative Researcher.
#
# DOES NOT match plain "Engineer" or "Software Engineer" — only when paired
# with a research/data-platform qualifier. Since July 2026 it also does NOT
# match "AI Engineer" / "ML Engineer" / "Machine Learning Engineer": those
# titles increasingly mean LLM-integration full-stack work (the candidate's AI lane —
# Ollama, Claude API, agentic pipelines), so they flow through to the scorer,
# which handles the genuinely research-flavored ones via gaps/declines.
_RESEARCH_TITLE_RE = re.compile(
    r"\b(?:"
    r"applied\s+(?:ai/?ml\s+)?scientist"
    r"|(?:ml|ai|machine\s+learning)\s+(?:scientist|researcher)"
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


# Non-engineering function regex. Drives an ingest drop (default-on via
# `[ingest] drop_non_engineering_titles`) so the scorer doesn't burn budget on
# roles it will always decline. Large Workday tenants (BMO/TD/HelloFresh/Sanofi/
# Live Nation) post their entire org — Office Administrator, Sanitation Associate,
# Food Safety Specialist, Maintenance Technician, Operational Buyer, Account
# Executive, Legal Counsel, etc. — which previously each cost a full LLM score.
#
# Curated, high-precision *function* terms only. Deliberately EXCLUDES ambiguous
# tokens (`analyst`, `associate`, bare `specialist`/`coordinator`, `engineer`,
# `security`) that co-occur with engineering roles. `_ENG_GUARD_RE` wins over a
# match so a real dev/eng title is never dropped even if a non-eng token appears.
_NON_ENG_TITLE_RE = re.compile(
    r"\b(?:"
    # sales / business development
    r"account\s+executive|sales\s+(?:representative|associate|consultant|rep)"
    r"|business\s+development\s+(?:representative|rep)|inside\s+sales|outside\s+sales"
    # admin / office
    r"|administrative\s+assistant|office\s+administrator|receptionist"
    r"|executive\s+assistant|data\s+entry"
    # legal
    r"|legal\s+counsel|\bcounsel\b|paralegal|attorney|solicitor"
    # finance / accounting
    r"|accountant|bookkeeper|accounts\s+(?:payable|receivable)|payroll|underwriter|teller"
    # HR / recruiting
    r"|recruiter|talent\s+acquisition|human\s+resources|hr\s+generalist"
    # marketing / comms
    r"|performance\s+marketing|content\s+writer|copywriter"
    r"|communications?\s+specialist|public\s+relations|brand\s+ambassador"
    # supply chain / operations / production
    r"|\bbuyer\b|procurement|supply\s+(?:planner|chain)|logistics"
    r"|warehouse|production\s+(?:supervisor|associate|worker)|machine\s+operator"
    r"|forklift|dispatcher|merchandiser"
    # trades / facilities
    r"|maintenance\s+technician|millwright|electrician|plumber|hvac|welder"
    r"|machinist|assembler|fabricator|custodian|janitor|sanitation|groundskeeper"
    # food / health-safety (food context — NOT software QA)
    r"|food\s+safety|fsqa|butcher|baker|line\s+cook|dishwasher|barista"
    r"|bartender|food\s+service"
    # healthcare — clinical roles (hospital tenants like UHN post these heavily).
    # High-precision multi-word forms; the eng guard still wins so a "Clinical
    # Software Engineer" / "Healthcare Software Developer" survives.
    r"|\bnurse\b|physician|pharmacist|phlebotomist|caregiver|veterinary"
    r"|personal\s+support\s+worker|\bpsw\b|care\s+attendant|\bporter\b|orderly"
    r"|ward\s+clerk"
    r"|respiratory\s+therap(?:ist|y)|radiation\s+therapist"
    r"|physiotherap(?:ist|y)|physical\s+therap(?:ist|y)"
    r"|occupational\s+therap(?:ist|y)|speech[-\s]language\s+pathologist"
    r"|social\s+worker|dietitian|perfusion(?:ist)?|sonographer|paramedic"
    r"|midwife|audiologist|optometrist|kinesiologist|psychologist"
    r"|dental\s+(?:hygienist|assistant)"
    r"|medical\s+lab(?:oratory)?\s+technologist|pulmonary\s+function"
    r"|computed\s+tomography|radiologic\s+technologist|mri\s+technologist"
    # healthcare — research/coordination layer (2026-06-11: UHN leaked 14 of
    # these past the treatment-profession tier above). Multi-word forms only;
    # bare `coordinator` stays excluded per the ambiguous-token policy.
    r"|clinical\s+research|patient\s+(?:flow|care)\s+coordinator"
    r"|research\s+technician|postdoctoral|interventional\s+radiology"
    r"|staffing\s+representative|counsell?or\b"
    # security (physical) / retail / service
    r"|security\s+(?:guard|officer)|loss\s+prevention|event\s+security"
    r"|cashier|retail\s+associate|store\s+associate|stocker|delivery\s+driver|courier"
    r")\b",
    re.IGNORECASE,
)

# Dev/engineering signal — protects real roles from a coincidental non-eng match.
_ENG_GUARD_RE = re.compile(
    r"\b(?:software|developer|programmer|devops|sre|site\s+reliability"
    r"|front[\s-]?end|back[\s-]?end|full[\s-]?stack|web\s+developer"
    r"|data\s+engineer|platform\s+engineer|cloud\s+engineer|security\s+engineer"
    r"|qa\s+engineer|test\s+engineer|automation\s+engineer|software\s+engineer"
    r"|ml\s+engineer|ai\s+engineer|machine\s+learning\s+engineer"
    r"|mobile\s+developer|ios\s+developer|android\s+developer)\b",
    re.IGNORECASE,
)


def is_non_engineering_title(title: str | None) -> bool:
    """True when the title is a clearly non-engineering function.

    Default-on ingest drop (see `[ingest] drop_non_engineering_titles`). A dev/eng
    signal (`_ENG_GUARD_RE`) always wins, so engineering roles are never dropped.
    """
    if not title:
        return False
    if _ENG_GUARD_RE.search(title):
        return False
    return bool(_NON_ENG_TITLE_RE.search(title))


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
    r"entry[-\s]?level|new\s+grad|graduate|"
    # Co-op / intern / campus / early-talent markers (2026-06): qwen sometimes
    # emits a "Senior-band" decline on these despite the title literally being
    # entry-level (e.g. "Business Systems Analyst Co-op", "Campus Recruitment").
    # `intern` is \b-bounded so it doesn't match "internal"/"international".
    r"co[-\s]?op|intern(?:ship)?|campus|early[-\s]?talent|student|practicum)\b",
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

"""Deterministic checks on model-emitted decline reasons. No LLM call.

The score model's `decline_reason` is trusted as a reading of the posting, but
two §8 triggers are checkable against the JD text itself, and the model gets
them wrong often enough to hide applyable roles. Audit 2026-09-15 on 253 `lite`
scores:

- Years: 61 years-based declines, ~26 of them on postings asking for 4-6 years
  against a limit of `years_experience + 3` = 6, and 2 triggered by marketing
  copy ("Forbes Cloud 100 for four years in a row").
- People management: "Sr. Solutions Engineer implies people management" and a
  "Specialist" title declined as management, against §8's rule that senior IC
  titles decline only on management duties named in the body.

Each guard only ever clears a decline; none adds one.
"""

from __future__ import annotations

import html
import re

# Bump whenever a guard's behaviour changes. Folded into `score.prompt_hash`,
# so a guard fix re-scores the backlog instead of leaving stale declines.
DECLINE_GUARDS_VERSION = 1

# A reason that also cites another §8 trigger is left alone: clearing it would
# discard a decline that may stand on its other ground.
_OTHER_TRIGGER_RE = re.compile(
    r"regulated|clinical|securities|medical|non-engineering|location|toronto|"
    r"gta|remote|tier-1|tier 1|familiar|sales|marketing|recruit|designer|analyst",
    re.IGNORECASE,
)

# Reasons arrive as prose or as snake_case codes (`years_required_exceeds_limit`),
# so `_` must not count as a word character here.
_YEARS_REASON_RE = re.compile(r"(?<![a-z])(?:years?|yoe|tenure)(?![a-z])", re.IGNORECASE)

# "5+ years", "4-8 years", "3 to 5 years", "minimum of 7 years". The FIRST
# number is the bar: a range's lower bound is what the posting requires.
_NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        [
            "zero", "one", "two", "three", "four", "five", "six",
            "seven", "eight", "nine", "ten", "eleven", "twelve",
        ]
    )
}
_YEARS_ASK_RE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s*"
    r"(?:\+|(?:-|–|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b"
    # "Several years (5+) of professional software development"
    r"|\byears?\s*\((\d{1,2})\s*\+?\s*\)",
    re.IGNORECASE,
)
# Company copy, not a requirement. Deliberately a deny-list: requirement
# phrasing is too varied ("8+ years building", "10+ years shipping") for an
# allow-list, and missing a real ask would clear a justified decline.
_NOT_AN_ASK_RE = re.compile(
    r"^\W*(?:in a row|running|ago|old|of (?:growth|history|operation|service)|"
    r"in business|anniversary|per year|a year)"
    r"|(?:named|for over|for the (?:past|last)|founded|celebrat\w*|over the (?:past|last))\s*$",
    re.IGNORECASE,
)


def _years_asks(description: str) -> list[int]:
    text = html.unescape(description)
    asks: list[int] = []
    for m in _YEARS_ASK_RE.finditer(text):
        raw = (m.group(1) or m.group(2)).lower()
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        if n == 0 or n > 25:
            continue
        after = text[m.end() : m.end() + 25]
        before = text[max(0, m.start() - 25) : m.start()]
        if _NOT_AN_ASK_RE.search(after) or _NOT_AN_ASK_RE.search(before):
            continue
        asks.append(n)
    return asks


_MANAGEMENT_REASON_RE = re.compile(
    r"people[- ]?management|non-ic|\bmanag|supervis|direct reports|headcount",
    re.IGNORECASE,
)
_MANAGEMENT_TITLE_RE = re.compile(
    r"\b(manager|director|head of|vp|vice president|chief|supervisor|"
    r"superintendent)\b",
    re.IGNORECASE,
)
_MANAGEMENT_DUTY_RE = re.compile(
    r"direct reports?|headcount|performance reviews?|people management|"
    r"people leader|manage (?:a|the) team|managing (?:a|the)? ?team|"
    r"lead(?:ing)? a team of|hire,? (?:and )?(?:grow|develop|mentor) (?:a|the) team|"
    r"supervis(?:e|ing) (?:a|the)? ?(?:team|staff)",
    re.IGNORECASE,
)


# Only hands-on engineering titles are eligible. A Scrum Master or Tax Planner
# that the model mislabels "people-management" is still a §8 non-engineering
# decline, so clearing it would surface a role the policy excludes anyway.
_ENGINEERING_TITLE_RE = re.compile(
    r"engineer|developer|programmer|software|devops|\bsre\b|full[- ]?stack|"
    r"front[- ]?end|back[- ]?end|\bml\b|\bai\b|machine learning|data scientist|"
    r"architect",
    re.IGNORECASE,
)
_NON_ENGINEERING_TITLE_RE = re.compile(
    r"analyst|designer|consultant|sales|account|marketing|recruit|planner|"
    r"scrum master|technician|product owner|\bmanager\b",
    re.IGNORECASE,
)


def _years_unsupported(description: str, years_experience: int | None) -> bool:
    if years_experience is None:
        return False
    limit = years_experience + 3
    return not any(n > limit for n in _years_asks(description))


def _management_unsupported(title: str, description: str) -> bool:
    if _MANAGEMENT_TITLE_RE.search(title):
        return False
    return not _MANAGEMENT_DUTY_RE.search(html.unescape(description))


def decline_is_unsupported(
    reason: str, title: str, description: str, years_experience: int | None
) -> bool:
    """True when `reason` names only years and/or people-management triggers,
    and the posting backs none of them: no experience ask above
    `years_experience + 3`, and no management title or duties in the body.

    Every trigger the reason names must fail its check. A reason citing any
    other §8 ground, or a non-engineering title, is left standing."""
    names_years = bool(_YEARS_REASON_RE.search(reason))
    names_management = bool(_MANAGEMENT_REASON_RE.search(reason))
    if not (names_years or names_management):
        return False
    if (
        _OTHER_TRIGGER_RE.search(reason)
        or not _ENGINEERING_TITLE_RE.search(title)
        or _NON_ENGINEERING_TITLE_RE.search(title)
    ):
        return False
    if names_years and not _years_unsupported(description, years_experience):
        return False
    return not (names_management and not _management_unsupported(title, description))

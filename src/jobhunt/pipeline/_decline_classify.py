"""Deterministic classifier for `jobs.decline_reason` free-text.

Maps prose written by the scorer (qwen, but the prompt is deterministic
enough that wording converges) into a small enum. Used by:

- `pipeline.score` at score time: stamps `jobs.decline_category` so
  future analyses can group rows in SQL rather than re-scanning text.
- `db.migrate`-time backfill: classifies any pre-existing
  `decline_reason` rows the first time the `decline_category` column
  exists (`0007_decline_category` migration).
- `analyze` subcommands: aggregate by category over a window.

The classifier is pure regex + ordered match. No LLM. Add new patterns
to `_PATTERNS` (most specific first) — the first match wins.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

# Order matters: more specific patterns come first so the wider ones don't
# capture cases that have a sharper category.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The title-function decline comes first: when a reason cites it alongside
    # another ground, the title is the primary one (every live example leads
    # with it, e.g. "Title is non-engineering function (Risk Analyst) and
    # domain requires regulated ...").
    (
        "non_engineering",
        re.compile(r"\bnon[- ]?engineering\b", re.IGNORECASE),
    ),
    (
        "regulated_domain",
        re.compile(
            r"\b(regulated|clinical|medical[- ]device|fda|hipaa|securities|trading|"
            r"investment[- ]bank|defense|aerospace)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "people_management",
        re.compile(
            r"\b(people[- ]?management|direct reports?|manage[r]? \d+|"
            r"head of|vice president|engineering manager|"
            r"hiring manager role|own headcount|own performance reviews?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "location_mismatch",
        re.compile(
            r"\b(on[- ]?site (?:in )?(?!toronto|gta|canada)|"
            r"located in (?!toronto|gta|canada|ontario)|"
            r"outside (?:of )?(?:toronto|gta|canada)|"
            r"us[- ]?only|us residents only|must be (?:located )?in the (?:us|united states)|"
            r"based in (?!toronto|gta|canada|ontario))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "years_gap",
        re.compile(
            r"\b("
            r"\d+\+? years? (?:of )?(?:experience|production)|"
            r"requires? \d+\+? years?|"
            r"(?:5|6|7|8|9|10|seven|eight|nine|ten)\+? years?|"
            r"years? gap|"
            r"years? (?:required )?(?:exceed\w*|threshold|limit)|"
            r"insufficient (?:years|experience)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "familiar_only",
        re.compile(
            r"\b(familiar[- ](only|bucket)|matched skills are all familiar|"
            r"academic[/ ]light use only|coursework[- ]only)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tier1_gaps",
        re.compile(r"\b\d+\+? (?:hard )?tier[- ]?1 requirements?\b", re.IGNORECASE),
    ),
    (
        "wrong_stack",
        re.compile(
            r"\b(no production .* experience|requires? .* "
            r"(Go|Rust|C#|\.NET|PHP|Ruby|Laravel|Rails|Django|Flask|Vue|Angular)|"
            r"primary stack (?:is|in) (Go|Rust|C#|\.NET|PHP|Ruby)|"
            r"stack mismatch|"
            r"non[- ]overlapping (?:required )?stack)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "wrong_domain",
        re.compile(
            r"\b(domain mismatch|requires? .* domain experience|"
            r"specific to (?:gaming|fintech|healthtech|insurtech|martech) industry|"
            r"industry experience required)\b",
            re.IGNORECASE,
        ),
    ),
)

VALID_CATEGORIES = (
    "non_engineering",
    "years_gap",
    "people_management",
    "tier1_gaps",
    "wrong_domain",
    "wrong_stack",
    "familiar_only",
    "regulated_domain",
    "location_mismatch",
    "other",
)


def classify_decline_reason(reason: str | None) -> str | None:
    """Classify a single `decline_reason` string. Returns `None` if reason is
    None or empty; otherwise returns one of `VALID_CATEGORIES`.
    """
    if not reason:
        return None
    # The model also emits snake_case codes (`years_required_exceeds_limit`,
    # `location_outside_toronto`). `_` is a word character, so `\b` never fires
    # inside them; match against the spaced form instead.
    text = reason.replace("_", " ")
    for category, pattern in _PATTERNS:
        if pattern.search(text):
            return category
    return "other"


def reclassify_all(conn: sqlite3.Connection) -> int:
    """Re-derive `jobs.decline_category` for every declined row. Run after a
    pattern change; `backfill_existing` only fills NULLs. Returns rows changed.
    """
    rows = conn.execute(
        "SELECT id, decline_reason, decline_category FROM jobs "
        "WHERE decline_reason IS NOT NULL"
    ).fetchall()
    changed = 0
    with conn:
        for job_id, reason, old in rows:
            new = classify_decline_reason(reason)
            if new != old:
                conn.execute(
                    "UPDATE jobs SET decline_category = ? WHERE id = ?", (new, job_id)
                )
                changed += 1
    return changed


def backfill_existing(conn: sqlite3.Connection) -> int:
    """Populate `jobs.decline_category` for rows that have a
    `decline_reason` but no category yet. Returns the count updated.

    Safe to re-run — only touches rows with NULL `decline_category`.
    """
    rows: Iterable[sqlite3.Row] = conn.execute(
        "SELECT id, decline_reason FROM jobs "
        "WHERE decline_reason IS NOT NULL AND decline_category IS NULL"
    ).fetchall()
    updated = 0
    with conn:
        for row in rows:
            category = classify_decline_reason(row["decline_reason"])
            conn.execute(
                "UPDATE jobs SET decline_category = ? WHERE id = ?",
                (category, row["id"]),
            )
            updated += 1
    return updated

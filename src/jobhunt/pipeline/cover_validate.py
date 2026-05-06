"""Deterministic cover-letter validator.

Mirrors the hard rules in `kb/prompts/cover.md` so that violations are caught
post-decode rather than relying on the LLM to obey the prompt. Run after
`write_cover` and before .docx render. Returns a list of violation strings;
the caller decides whether to abort or warn.
"""

from __future__ import annotations

import re
from typing import Any

from jobhunt.pipeline.cover import CoverLetter

# From cover.md §7. Lowercased; matched as case-insensitive substrings on a
# normalized body so "Passionate" and "passionate" both fire.
BANNED_PHRASES: tuple[str, ...] = (
    "passionate",
    "synergy",
    "leveraged",
    "spearheaded",
    "results-driven",
    "i'm excited",
    "i believe",
    "aligns with",
    "core requirements",
    "production-grade",
    "complementing my practical experience",
    "track record",
    "proven ability",
    "deeply passionate",
    "hit the ground running",
    "value-add",
    "direct match",
    "mirrors the kind of",
    "technical rigor",
    "i'd bring to",
    "i'd welcome the chance",
    "the chance to discuss",
    "i'm drawn to",
    "transform enterprises",
    "support your team's goals",
)

# Form-letter openers banned by §2.
BANNED_OPENERS: tuple[str, ...] = (
    "applying for",
    "i am writing to",
    "i am excited",
    "to whom it may concern",
)

_DIGIT_CLUSTER_RE = re.compile(r"\d[\d,.]*")
_WORD_RE = re.compile(r"\b\w+\b")


def _body_text(cover: CoverLetter) -> str:
    return "\n\n".join(p for p in cover.body if p).strip()


def _full_text(cover: CoverLetter) -> str:
    return "\n".join([cover.salutation, _body_text(cover), cover.sign_off]).strip()


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _verified_numbers(verified: dict[str, Any]) -> set[str]:
    """Every digit cluster that appears anywhere in verified.json. Used to
    sanity-check that any number in the cover letter has a source."""
    blob_parts: list[str] = []
    for key in ("summary",):
        v = verified.get(key)
        if isinstance(v, str):
            blob_parts.append(v)
    for role in verified.get("work_history", []):
        for b in role.get("bullets", []):
            blob_parts.append(b)
    for key in ("certifications", "education", "coursework_baseline"):
        for line in verified.get(key, []):
            blob_parts.append(line)
    blob = " ".join(blob_parts)
    return set(_DIGIT_CLUSTER_RE.findall(blob))


def validate_cover(
    cover: CoverLetter,
    *,
    verified: dict[str, Any],
    company: str | None,
    max_words: int,
) -> list[str]:
    """Return a list of violation strings. Empty list = clean."""
    violations: list[str] = []
    body = _body_text(cover)
    body_lower = body.lower()
    full_lower = _full_text(cover).lower()

    for phrase in BANNED_PHRASES:
        if phrase in full_lower:
            violations.append(f"banned phrase: {phrase!r}")

    if cover.body:
        first_lower = cover.body[0].lower().lstrip()
        for opener in BANNED_OPENERS:
            if first_lower.startswith(opener):
                violations.append(f"form-letter opener: {opener!r}")

    wc = _word_count(body)
    if wc > max_words:
        violations.append(f"body is {wc} words; max is {max_words}")

    if not (3 <= len(cover.body) <= 4):
        violations.append(f"expected 3-4 paragraphs; got {len(cover.body)}")

    if cover.body and company and company.lower() not in cover.body[0].lower():
        violations.append(f"lead paragraph does not name company {company!r}")

    # Numeric facts: any digit cluster in the body must trace back to
    # verified.json, with two carve-outs — "30%" → strip to "30" before
    # comparing, and bare single digits 1-5 are too generic to flag (they
    # tend to appear in echoed resume phrases like "3 years").
    allowed = _verified_numbers(verified)
    for cluster in _DIGIT_CLUSTER_RE.findall(body):
        normalized = cluster.rstrip(".,")
        if not normalized:
            continue
        if normalized in allowed:
            continue
        if len(normalized) == 1 and normalized in {"1", "2", "3", "4", "5"}:
            continue
        violations.append(f"unverified number: {cluster!r}")

    # cover.md §5 — closing paragraph must be forward-looking, not a
    # diploma/coursework recap. Only check if there are ≥3 paragraphs.
    if len(cover.body) >= 3:
        closing_lower = cover.body[-1].lower()
        for token in ("dean's list", "coursework", "george brown", "diploma"):
            if token in closing_lower:
                violations.append(f"closing recaps resume material: {token!r}")
                break

    sal = cover.salutation.strip().lower()
    if "to whom it may concern" in sal:
        violations.append("salutation: 'To whom it may concern' is banned")

    if "!" in body:
        violations.append("body contains an exclamation mark")

    if "{" in body_lower or "}" in body_lower:
        violations.append("body contains an unfilled template placeholder")

    return violations

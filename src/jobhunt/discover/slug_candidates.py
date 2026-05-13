"""Normalize a free-form company name into candidate Greenhouse/Ashby slugs."""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = (
    "incorporated",
    "international",
    "technologies",
    "consulting",
    "corporation",
    "solutions",
    "holdings",
    "partners",
    "services",
    "limited",
    "company",
    "group",
    "tech",
    "corp",
    "llc",
    "ltd",
    "inc",
    "co",
)

# Companies whose normalized name contains any of these tokens are skipped — they
# are staffing/recruiting agencies that repost JDs and never run a public ATS board.
_STAFFING_HINTS = frozenset({
    "staffing",
    "talent",
    "recruit",
    "infoteck",
    "hirevouch",
    "targeted",
    "insight global",
    "ignite",
    "trident staff",
    "morson",
    "cleo consulting",
    "innosystech",
    "nearsource",
    "source code",
    "alquemy",
    "maarut",
    "tangentia",
    "virtusa",
    "recrute action",
})

_NON_SLUG = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")
# Characters that should NOT split a word — apostrophes, acute marks, etc. Stripped
# before the broader punctuation pass so "McDonald's" → "mcdonalds", not "mcdonald s".
_GLUED = re.compile(r"['’`]")


def _normalize(name: str) -> str:
    """Lowercase, strip diacritics, drop post-comma noise, remove punctuation."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    head = ascii_only.split(",", 1)[0]
    lowered = head.lower()
    deglued = _GLUED.sub("", lowered)
    cleaned = _NON_SLUG.sub(" ", deglued)
    return _WHITESPACE.sub(" ", cleaned).strip()


def _strip_suffixes(words: list[str]) -> list[str]:
    changed = True
    while changed and words:
        changed = False
        if words[-1] in _SUFFIXES:
            words.pop()
            changed = True
    return words


def candidates(company_name: object) -> list[str]:
    """Return up to 3 candidate slugs for the given company name.

    Pure function. Empty input, staffing agencies, and unparseable names all
    return ``[]``. Candidates are de-duplicated in priority order:

    1. All words joined, suffixes kept (``"konrad group"`` → ``konradgroup``)
    2. All words joined with corporate suffixes stripped (``konrad``)
    3. First word alone (often equal to #2 for single-word companies)

    Both #1 and #2 are emitted because real slugs vary: Konrad's board is
    ``konradgroup`` (suffix kept) while Magna's is ``magna`` (suffix stripped).

    Candidates shorter than 3 chars or longer than 60 are dropped.
    """
    if not isinstance(company_name, str) or not company_name.strip():
        return []

    normalized = _normalize(company_name)
    if not normalized:
        return []

    if any(hint in normalized for hint in _STAFFING_HINTS):
        return []

    full_words = normalized.split()
    if not full_words:
        return []

    stripped_words = _strip_suffixes(list(full_words))

    raw: list[str] = []
    raw.append("".join(full_words))
    if stripped_words:
        raw.append("".join(stripped_words))
        raw.append(stripped_words[0])

    seen: set[str] = set()
    out: list[str] = []
    for c in raw:
        if 3 <= len(c) <= 60 and c not in seen:
            seen.add(c)
            out.append(c)
    return out

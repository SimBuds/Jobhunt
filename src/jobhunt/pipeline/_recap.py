"""Education-recap tokens, derived from the verified profile.

Three validators independently reject generated prose that recites the
resume's education block instead of making a case for the role: the cover
validator (cover.md §5), the answer validator, and the interview-prep
validator. Each used to carry its own hard-coded tuple naming one specific
school, which meant the guard silently no-opped for anybody else — the literal
simply never matched. See IMPLEMENT.md Phase A10 disposition D1.

Institution names now come from `verified.json`'s `education` entries, so the
guard follows whichever profile is loaded.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# Recap markers that are person-independent. "coursework" is deliberately NOT
# here: the answer validator matches "coursework:" (the resume's literal label)
# while the other two match the bare word, and unifying them would widen one
# validator as a side effect of a refactor. Each call site passes its own.
_GENERIC_RECAP_TOKENS: tuple[str, ...] = ("dean's list", "diploma")

# Trailing words that name the *kind* of institution. Stripping them yields the
# short form people actually write ("Waterloo" for "Waterloo University"), so
# both spellings are caught.
_INSTITUTION_SUFFIXES = (
    "college", "university", "institute", "school", "polytechnic", "academy",
)

# `education` entries are free text shaped like
#   "<program>, <credential> — <institution> (<dates>). <honours>. Coursework: …"
# The em-dash is the reliable separator; the date parenthetical ends the name.
_EM_DASH_RE = re.compile(r"\s[—–-]\s")


def _institution_names(verified: Mapping[str, Any]) -> list[str]:
    """Institution names mentioned in the verified profile's education block."""
    names: list[str] = []
    entries = verified.get("education") or []
    if isinstance(entries, str):
        entries = [entries]
    for entry in entries:
        if isinstance(entry, Mapping):
            raw = str(
                entry.get("institution") or entry.get("school") or entry.get("name") or ""
            )
        else:
            parts = _EM_DASH_RE.split(str(entry), 1)
            raw = parts[1] if len(parts) > 1 else ""
        raw = raw.split("(")[0].strip().rstrip(".,;")
        if not raw:
            continue
        low = raw.lower()
        names.append(low)
        words = low.split()
        if len(words) > 1 and words[-1] in _INSTITUTION_SUFFIXES:
            names.append(" ".join(words[:-1]))
    return names


def recap_tokens(
    verified: Mapping[str, Any], *, extra: Sequence[str] = ()
) -> tuple[str, ...]:
    """Lowercase substrings whose presence means a document is reciting
    education credentials.

    `extra` carries the caller's own markers so each validator keeps its
    existing sensitivity. Longer names sort first so a violation message quotes
    the full institution name rather than the truncated short form.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for token in (*_GENERIC_RECAP_TOKENS, *extra, *_institution_names(verified)):
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    unique.sort(key=len, reverse=True)
    return tuple(unique)

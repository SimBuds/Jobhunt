"""Shared phrase/keyword matching used by both score-time clamp and post-gen audit.

Kept identical between the two so a phrase that the score pipeline credits as
matched against `verified.json` will also be credited as matched by
`pipeline.audit.keyword_coverage` when it lands in the rendered resume — no
drift between the two checks.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9+#./-]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "or", "the", "of", "in", "on", "to", "for", "with",
        "is", "are", "be", "as", "at", "by", "it", "this", "that", "you",
        "we", "our", "your", "their", "from", "have", "has", "will", "can",
        "year", "years", "experience", "skills", "knowledge", "ability",
        "strong", "good", "great", "able", "must", "should", "would",
        "preferred", "plus", "bonus", "required", "required.",
    }
)


def phrase_tokens(phrase: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(phrase.lower()) if t not in _STOPWORDS and len(t) > 1]


def phrase_present(phrase: str, blob: str) -> bool:
    """Phrase counts as covered if (a) the full phrase appears as a substring,
    or (b) every non-stopword token in the phrase appears somewhere in the blob.
    Blob must already be lower-cased.
    """
    p = phrase.lower().strip()
    if not p:
        return False
    if p in blob:
        return True
    tokens = phrase_tokens(p)
    if not tokens:
        return False
    return all(t in blob for t in tokens)

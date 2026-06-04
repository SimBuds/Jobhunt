"""P2 (context-budget initiative): the interview-prep --research caps were
raised so both fetched pages (JD URL + company root) survive into the prompt.
Before the bump the per-source strip capped at 6000 and the blob cap clipped
the assembled result back to 6000, dropping the second source."""

from __future__ import annotations

from jobhunt.commands.interview_prep_cmd import (
    _RESEARCH_PER_SOURCE_CHARS,
    _strip_html,
)
from jobhunt.pipeline.interview_prep import _RESEARCH_MAX_CHARS


def test_research_blob_cap_holds_two_sources() -> None:
    # Two per-source pages fit within the blob cap (the point of P2).
    assert _RESEARCH_MAX_CHARS == 18000
    assert 2 * _RESEARCH_PER_SOURCE_CHARS <= _RESEARCH_MAX_CHARS


def test_strip_html_keeps_more_than_old_6000_cap() -> None:
    # A long page now retains up to _RESEARCH_PER_SOURCE_CHARS, not 6000.
    html = "<p>" + ("word " * 4000) + "</p>"  # ~20k chars of text
    out = _strip_html(html)
    assert len(out) == _RESEARCH_PER_SOURCE_CHARS
    assert len(out) > 6000  # fails at the old per-source cap

"""`tailoring-rules.md` must fit `MAX_POLICY_CHARS` (Phase D5, 2026-11-11).

The policy is truncated to that limit before it reaches the score and tailor
prompts. At 6180 characters the end of the last tone rule was cut from every
call without any error. The raw file is the conservative bound: rendering
replaces `{candidate_name}` (16 characters) with a first name.
"""

from __future__ import annotations

from pathlib import Path

from jobhunt.pipeline.score import MAX_POLICY_CHARS

POLICY = Path(__file__).resolve().parents[1] / "kb" / "policies" / "tailoring-rules.md"


def test_policy_fits_the_prompt_limit() -> None:
    size = len(POLICY.read_text(encoding="utf-8"))
    assert size <= MAX_POLICY_CHARS, f"{size} characters, limit {MAX_POLICY_CHARS}"

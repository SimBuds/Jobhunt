"""Application-question answer pipeline.

Drafts a response to a single application-form question using the same
honesty rules as the cover-letter pipeline. Mirrors `pipeline.cover`:

- `write_answer` makes one LLM call and returns the drafted string.
- `write_answer_with_retry` validates the response against the same
  banned-phrase / fabrication / defensive-pattern rules and re-prompts up
  to N times on violations before falling back to the last attempt.
- `validate_answer` reuses the cover validator's core checks (banned
  phrases, defensive patterns, fabrication watchlist, unverified numbers)
  while dropping the cover-only structural rules (salutation, sign-off,
  paragraph count, company-in-lead).

Designed to be invoked from `commands.answer_cmd` for the new
`jobhunt answer` subcommand. Standalone (no JD) and job-scoped (JD context
loaded from the jobs table) modes share the same pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.pipeline._profile import candidate_name
from jobhunt.pipeline._recap import recap_tokens
from jobhunt.pipeline.cover_validate import (
    _BRIDGE_PATTERNS,
    _DEFENSIVE_PATTERNS,
    _DIGIT_CLUSTER_RE,
    _FABRICATION_WATCHLIST,
    _NEGATION_PRECEDES_RE,
    _TIME_OF_DAY_RE,
    _YEAR_RANGE_RE,
    BANNED_PHRASES,
    _normalize,
    _verified_numbers,
    _verified_skill_blob,
)


@dataclass
class Answer:
    text: str
    model: str


async def write_answer(
    cfg: Config,
    *,
    question: str,
    jd_context: str = "",
    max_words: int,
    revisions: str = "",
) -> Answer:
    """Single-attempt LLM call. Raises `PipelineError` on schema mismatch."""
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `jobhunt convert-resume`")
    verified_text = verified_path.read_text(encoding="utf-8")

    prompt = load_prompt(cfg.paths.kb_dir, "answer")
    # `max_words` also appears in the SYSTEM half. Until the system prompt was
    # rendered, that placeholder shipped to the model verbatim as the literal
    # text `{max_words}` — the cap only landed because the USER half repeats
    # it. Rendering both halves fixes the instruction the model actually reads.
    name = candidate_name(json.loads(verified_text))
    system = prompt.render_system(candidate_name=name, max_words=str(max_words))
    # The answer prompt uses single-brace placeholders rendered by
    # `prompt.render_user` (frontmatter loader's str.format). Same convention
    # as score/tailor/cover — including the `{revisions}` slot which is
    # populated on retry attempts with a corrective hint.
    user = prompt.render_user(
        verified_facts=verified_text,
        question=question,
        jd_context=jd_context or "(none — standalone answer)",
        max_words=str(max_words),
        revisions=revisions,
    )

    # Force temperature=0 on retry attempts (mirrors tailor retry behaviour
    # from Phase 9.2 — at the frontmatter default the model re-samples the
    # same offending phrase across attempts).
    temperature = 0.0 if revisions else prompt.temperature

    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks.get(
        "cover", "qwen-custom:latest"
    )
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=system,
        user=user,
        schema=prompt.schema,
        temperature=temperature,
    )
    text = raw.get("answer")
    if not isinstance(text, str) or not text.strip():
        raise PipelineError(
            f"answer returned malformed shape (missing 'answer' string); "
            f"keys={sorted(raw.keys())}"
        )
    return Answer(text=text.strip(), model=model)


# Words that legitimately start an answer and shouldn't be confused with the
# cover-letter form-letter openers. Answers don't have a salutation, so we
# don't enforce that. But we do still reject the most formulaic openers.
_BANNED_ANSWER_OPENERS: tuple[str, ...] = (
    "i am applying for",
    "i'm applying for",
    "i am writing to",
    "i am excited",
    "i'm excited",
    "i'm thrilled",
)

_WORD_RE = re.compile(r"\b\w+\b")
_LEADING_FILLER_RE = re.compile(
    r"^(?:hello,?\s*|hi,?\s*|dear[^,\.]*,?\s*)+", re.IGNORECASE
)
# Institution names come from the verified profile via `_recap.recap_tokens`;
# "coursework:" is this validator's own marker (the resume's literal label).
_RECAP_EXTRA = ("coursework:",)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def validate_answer(
    answer: Answer,
    *,
    verified: dict[str, Any],
    max_words: int,
) -> list[str]:
    """Return a list of violation strings. Empty list = clean.

    Reuses the cover validator's core checks but skips structural rules
    that don't apply to short-form answers (paragraph count, salutation,
    sign-off, company name in lead).
    """
    violations: list[str] = []
    body = answer.text
    body_lower = _normalize(body)

    # Banned phrases — shared substring tier with cover validator.
    for phrase in BANNED_PHRASES:
        if phrase in body_lower:
            violations.append(f"banned phrase: {phrase!r}")

    # Defensive gap-volunteering patterns (including the May 2026 "concepts"
    # framing added in Phase 8).
    for pattern, label in _DEFENSIVE_PATTERNS:
        if re.search(pattern, body_lower):
            violations.append(label)

    # Overconfident bridge claims — context-anchored tier shared with the
    # cover validator (tone guardrails).
    for pattern, label in _BRIDGE_PATTERNS:
        if re.search(pattern, body_lower):
            violations.append(label)

    # Formulaic openers — answers shouldn't start with cover-letter openers.
    first_normalized = _LEADING_FILLER_RE.sub("", body_lower).lstrip()
    for opener in _BANNED_ANSWER_OPENERS:
        if first_normalized.startswith(opener):
            violations.append(f"formulaic opener: {opener!r}")
            break

    # Word count — answers are short-form by design.
    wc = _word_count(body)
    if wc > max_words:
        violations.append(f"answer is {wc} words; max is {max_words}")

    # Unverified numbers (digit-cluster check). Year ranges and clock-style
    # references are stripped first, matching the cover-validator pre-process.
    allowed = _verified_numbers(verified)
    scratch = _TIME_OF_DAY_RE.sub(" ", body)
    scratch = _YEAR_RANGE_RE.sub(" ", scratch)
    for cluster in _DIGIT_CLUSTER_RE.findall(scratch):
        normalized = cluster.rstrip(".,")
        if not normalized:
            continue
        if normalized in allowed:
            continue
        if len(normalized) == 1 and normalized in {"1", "2", "3", "4", "5"}:
            continue
        violations.append(f"unverified number: {cluster!r}")

    # Resume-recap suppression. Answers should NOT cite the GBC diploma /
    # coursework — that material lives on the resume.
    for token in recap_tokens(verified, extra=_RECAP_EXTRA):
        if token in body_lower:
            violations.append(f"answer recaps resume material: {token!r}")
            break

    # Unfilled prompt placeholder (defensive).
    if "{" in body_lower or "}" in body_lower:
        violations.append("answer contains an unfilled template placeholder")

    # Fabrication watchlist with negation-context suppression. Same logic as
    # cover_validate's tail block.
    verified_blob = _verified_skill_blob(verified)
    for tech in _FABRICATION_WATCHLIST:
        token = tech.strip(", ")
        if not token:
            continue
        token_pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
        if not token_pattern.search(body):
            continue
        if token_pattern.search(verified_blob):
            continue
        all_negated = True
        for m in token_pattern.finditer(body_lower):
            window = body_lower[max(0, m.start() - 40) : m.start()]
            if not _NEGATION_PRECEDES_RE.search(window):
                all_negated = False
                break
        if all_negated:
            continue
        violations.append(f"unverified tech claim: {token!r}")

    # Exclamation marks read as form-letter enthusiasm.
    if "!" in body:
        violations.append("answer contains an exclamation mark")

    return violations


async def write_answer_with_retry(
    cfg: Config,
    *,
    question: str,
    jd_context: str = "",
    verified: dict[str, Any],
    max_words: int,
    max_attempts: int,
) -> tuple[Answer, list[str], int]:
    """Generate an answer, re-running up to `max_attempts` times when the
    validator flags violations. Returns `(answer, final_violations,
    attempts_used)`. Falls back to the last attempt after the final retry
    — the caller decides whether to ship-with-warnings or hand-edit.

    Mirrors `pipeline.cover.write_cover_with_retry` exactly. The retry
    layer never weakens the validation rules; a violating LLM still gets
    its draft annotated with the violations the user must address.
    """
    attempts = max(1, max_attempts)
    last_answer: Answer | None = None
    last_violations: list[str] = []
    revisions = ""
    for attempt in range(1, attempts + 1):
        ans = await write_answer(
            cfg,
            question=question,
            jd_context=jd_context,
            max_words=max_words,
            revisions=revisions,
        )
        violations = validate_answer(ans, verified=verified, max_words=max_words)
        if not violations:
            return ans, [], attempt
        last_answer = ans
        last_violations = violations
        revisions = _format_revision_hint(violations, attempt)
    assert last_answer is not None  # loop runs at least once
    return last_answer, last_violations, attempts


def _format_revision_hint(violations: list[str], attempt: int) -> str:
    """Build the `{revisions}` block injected at the end of the next
    attempt's user prompt. Names each violation concretely so the model
    can fix it rather than re-guessing. Mirrors `cover._format_revision_hint`.
    """
    lines = [
        "",
        "## Previous attempt was rejected by the validator. Fix these:",
    ]
    for v in violations:
        lines.append(f"- {v}")
    lines.append(
        f"Rewrite the answer from scratch. This is retry {attempt + 1}; "
        "do not reuse phrasing from the prior attempt that triggered a "
        "violation. Keep the answer short and concrete."
    )
    return "\n".join(lines)

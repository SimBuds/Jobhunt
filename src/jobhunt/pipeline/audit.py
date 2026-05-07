"""Deterministic post-generation audit.

Runs after `tailor_resume` + `write_cover` and before .docx render. Checks:

- JD must-have keyword coverage in the rendered resume markdown.
- Tailor invariants (re-runs `_enforce_no_fabrication` defensively).
- Cover-letter validator (`cover_validate.validate_cover`).

Returns an `AuditResult` with a verdict: ship | revise | block. The caller
chooses what to do with each verdict; this module never raises on its own
(except for catastrophic missing-input errors, which propagate as
`PipelineError`).

Scope choice: this is intentionally LLM-free. The `qa` task slot in
`config.gateway.tasks` exists for a future second-opinion pass, but the
deterministic checks here are the load-bearing ones — they don't drift,
they don't cost a model swap, and they're what the user has asked for
under "Scoring audit needed".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jobhunt.errors import PipelineError
from jobhunt.pipeline._keywords import phrase_present
from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.cover_validate import validate_cover
from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredResume, _enforce_no_fabrication

# Coverage threshold (Scale.jobs 2026 ATS guidance: aim 70-80%).
MIN_KEYWORD_COVERAGE_PCT = 70


@dataclass
class AuditResult:
    keyword_coverage_pct: int | None  # None when no must-haves were extracted
    matched_keywords: list[str]
    missing_must_haves: list[str]
    fabrication_flags: list[str]
    cover_letter_violations: list[str]
    verdict: str  # ship | revise | block

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _resume_text(tailored: TailoredResume) -> str:
    """Flatten the tailored resume into a single lower-cased text blob for
    keyword matching. Mirrors what the rendered docx will say."""
    parts: list[str] = [tailored.summary]
    for cat in tailored.skills_categories:
        parts.append(cat.name)
        parts.extend(cat.items)
    for role in tailored.roles:
        parts.append(role.title)
        parts.append(role.employer)
        parts.append(role.dates)
        parts.extend(role.bullets)
    parts.extend(tailored.certifications)
    parts.extend(tailored.education)
    parts.extend(tailored.coursework)
    return "\n".join(parts).lower()


def keyword_coverage(
    must_haves: list[str], tailored: TailoredResume
) -> tuple[int | None, list[str], list[str]]:
    """Return (coverage_pct, matched, missing). pct is None when no must-haves."""
    if not must_haves:
        return None, [], []
    blob = _resume_text(tailored)
    matched: list[str] = []
    missing: list[str] = []
    for phrase in must_haves:
        if phrase_present(phrase, blob):
            matched.append(phrase)
        else:
            missing.append(phrase)
    pct = round(100 * len(matched) / len(must_haves))
    return pct, matched, missing


def audit(
    *,
    tailored: TailoredResume,
    cover: CoverLetter,
    score: ScoreResult | None,
    verified: dict[str, Any],
    company: str | None,
    cover_max_words: int,
) -> AuditResult:
    must_haves = list(score.matched_must_haves) if score else []
    if score and score.gaps:
        # Treat gaps as additional candidate keywords — if the tailor surfaced
        # any of them via adjacent skills, count it.
        must_haves = must_haves + list(score.gaps)

    coverage_pct, matched, missing = keyword_coverage(must_haves, tailored)

    fabrication_flags: list[str] = []
    try:
        _enforce_no_fabrication(tailored, verified)
    except PipelineError as e:
        fabrication_flags.append(str(e))

    cover_violations = validate_cover(
        cover, verified=verified, company=company, max_words=cover_max_words
    )

    if fabrication_flags:
        verdict = "block"
    elif cover_violations or (coverage_pct is not None and coverage_pct < MIN_KEYWORD_COVERAGE_PCT):
        verdict = "revise"
    else:
        verdict = "ship"

    return AuditResult(
        keyword_coverage_pct=coverage_pct,
        matched_keywords=matched,
        missing_must_haves=missing,
        fabrication_flags=fabrication_flags,
        cover_letter_violations=cover_violations,
        verdict=verdict,
    )


def write_audit(out_dir: Path, result: AuditResult) -> Path:
    p = out_dir / "audit.json"
    p.write_text(result.to_json(), encoding="utf-8")
    return p

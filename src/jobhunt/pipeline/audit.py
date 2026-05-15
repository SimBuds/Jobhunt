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
from jobhunt.pipeline._keywords import peer_match, phrase_present
from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.cover_validate import validate_cover
from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredResume, _enforce_no_fabrication

# Adzuna ships truncated description snippets (~500 chars). Below this
# threshold, the audit broadens must-have extraction via PEER_FAMILIES so a
# JD that mentions "Vue" can still surface "React" as a must-have for Casey.
# Long full JDs (Greenhouse, Lever, manual) already have enough surface text
# to land canonical tech names; broadening there would create false positives.
_SHORT_JD_THRESHOLD = 800

# Coverage threshold (Scale.jobs 2026 ATS guidance: aim 70-80%).
MIN_KEYWORD_COVERAGE_PCT = 70


@dataclass
class AuditResult:
    keyword_coverage_pct: int | None  # None when no must-haves were extracted
    matched_keywords: list[str]
    missing_must_haves: list[str]
    fabrication_flags: list[str]
    cover_letter_violations: list[str]
    alignment_flags: list[str]  # resume↔cover project-drift warnings
    verdict: str  # ship | revise | block

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# Project anchors mined from verified.json work_history bullets. Detecting
# these in both the cover's body and the resume's lead bullet lets us flag
# drift — when the cover centers on Atelier Dacko but the resume's lead
# role's first bullet is about HubSpot, the artifact pair reads inconsistent
# to an AI-screener reading both.
#
# Anchor design rules:
# - Each anchor names a distinct verified project, NOT a generic platform.
#   Bare "Shopify" is intentionally NOT an anchor because both Atelier Dacko
#   and Vintage Gaming are Shopify projects — using "shopify" alone would
#   conflate them and create false alignment-flag positives.
# - Terms inside an anchor must be specific enough to identify ONE verified
#   project. "atelier dacko", "ring builder", "custom jewellery client" all
#   identify the same contract; "8-page hubspot", "hubl" identify the AI
#   agency contract; etc.
_PROJECT_ANCHORS: tuple[tuple[str, frozenset[str]], ...] = (
    ("atelier_dacko", frozenset({
        "atelier dacko", "ring builder", "custom jewelry", "custom jewellery",
        "jewelry brand", "jewellery brand", "jewellery client", "jewelry client",
    })),
    ("vintage_gaming", frozenset({
        "vintage gaming", "400+ item", "vintage gaming retailer", "gaming catalog",
    })),
    ("hubspot", frozenset({
        "hubspot", "hubl", "8-page hubspot",
    })),
    ("ollama", frozenset({
        "ollama", "local llm", "gpu optimization",
    })),
)


def _find_project_anchor(text: str) -> str | None:
    """Return the first project anchor key found in `text` (case-insensitive).
    None if no anchor matches. Used by the alignment check."""
    low = text.lower()
    for anchor_key, terms in _PROJECT_ANCHORS:
        if any(term in low for term in terms):
            return anchor_key
    return None


def _alignment_flags(tailored: TailoredResume, cover: CoverLetter) -> list[str]:
    """Detect resume↔cover project drift.

    The cover's middle paragraph(s) typically anchor on one centerpiece
    project; the resume's first role's lead bullet should anchor on the
    same project for a coherent AI-screener pass. If they diverge — cover
    centers on the Shopify ring builder, resume leads with HubSpot — flag
    `revise` (not block; the user can still ship, but they'll see the warning).

    Returns an empty list when:
    - no project anchor can be detected in either side (signal too weak);
    - both sides anchor on the same project.
    """
    if not cover.body or not tailored.roles:
        return []
    # Use paragraphs 2+ of the cover (middle/closing) as the cover-side anchor
    # source. The lead paragraph is hook+company-naming, not project-deep.
    cover_mid = "\n\n".join(cover.body[1:]) if len(cover.body) > 1 else cover.body[0]
    cover_anchor = _find_project_anchor(cover_mid)
    if cover_anchor is None:
        return []
    first_role = tailored.roles[0]
    if not first_role.bullets:
        return []
    resume_lead_anchor = _find_project_anchor(first_role.bullets[0])
    if resume_lead_anchor is None:
        # Resume's lead bullet doesn't name a tracked project; can't compare.
        return []
    if cover_anchor != resume_lead_anchor:
        return [
            f"resume lead bullet anchors on {resume_lead_anchor!r} "
            f"but cover middle paragraphs anchor on {cover_anchor!r} — "
            f"reorder resume bullets or rewrite cover so both center on "
            f"the same project for AI-screener coherence"
        ]
    return []


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


def _verified_skills(verified: dict[str, Any]) -> list[str]:
    skills: list[str] = []
    for key in ("skills_core", "skills_cms", "skills_data_devops", "skills_ai", "skills_familiar"):
        for s in verified.get(key, []) or []:
            if isinstance(s, str) and s.strip():
                skills.append(s.strip())
    return skills


def _extract_must_haves_from_jd(
    job_description: str | None,
    verified: dict[str, Any],
    job_title: str | None = None,
) -> list[str]:
    """Deterministic fallback when the score LLM returns empty must-haves.

    Returns verified skills that appear in the JD — those are the JD's
    must-haves the candidate satisfies. Used to drive keyword coverage when
    `scores.reasons` is `[]` (qwen3.5:9b often emits empty arrays even though
    the schema requires the field).

    Adzuna returns ~500-char description snippets, so we also intersect with
    `job_title`, which is not truncated and almost always names canonical tech
    ("Java", "Front-end", "React", "Full Stack").

    **Peer-family broadening (May 2026).** When the JD is short (< 800 chars,
    signaling Adzuna), we also count a verified skill as a must-have when any
    of its `PEER_FAMILIES` peers appears in the JD. Example: verified has
    "React", JD names "Vue" — React surfaces as an inferred must-have. The
    tailor's JD-surface-form rule (tailor.md rule 9) will render it as the
    JD's exact form ("Vue") in the output where appropriate. Long JDs skip
    this broadening to avoid false positives — they have enough surface text
    to land canonical names directly.
    """
    parts: list[str] = []
    if job_title:
        parts.append(job_title)
    if job_description:
        parts.append(job_description)
    if not parts:
        return []
    blob = "\n".join(parts).lower()
    is_short = (job_description or "") and len(job_description) < _SHORT_JD_THRESHOLD
    out: list[str] = []
    for s in _verified_skills(verified):
        if phrase_present(s, blob):
            out.append(s)
        elif is_short and peer_match(s, blob):
            out.append(s)
    return out


def audit(
    *,
    tailored: TailoredResume,
    cover: CoverLetter,
    score: ScoreResult | None,
    verified: dict[str, Any],
    company: str | None,
    cover_max_words: int,
    job_description: str | None = None,
    job_title: str | None = None,
) -> AuditResult:
    must_haves = list(score.matched_must_haves) if score else []
    if score and score.gaps:
        # Treat gaps as additional candidate keywords — if the tailor surfaced
        # any of them via adjacent skills, count it.
        must_haves = must_haves + list(score.gaps)

    if not must_haves:
        must_haves = _extract_must_haves_from_jd(job_description, verified, job_title)

    coverage_pct, matched, missing = keyword_coverage(must_haves, tailored)

    fabrication_flags: list[str] = []
    try:
        _enforce_no_fabrication(tailored, verified)
    except PipelineError as e:
        fabrication_flags.append(str(e))

    cover_violations = validate_cover(
        cover, verified=verified, company=company, max_words=cover_max_words
    )

    alignment = _alignment_flags(tailored, cover)

    if fabrication_flags:
        verdict = "block"
    elif (
        cover_violations
        or alignment
        or (coverage_pct is not None and coverage_pct < MIN_KEYWORD_COVERAGE_PCT)
    ):
        verdict = "revise"
    else:
        verdict = "ship"

    return AuditResult(
        keyword_coverage_pct=coverage_pct,
        matched_keywords=matched,
        missing_must_haves=missing,
        fabrication_flags=fabrication_flags,
        cover_letter_violations=cover_violations,
        alignment_flags=alignment,
        verdict=verdict,
    )


def write_audit(out_dir: Path, result: AuditResult) -> Path:
    p = out_dir / "audit.json"
    p.write_text(result.to_json(), encoding="utf-8")
    return p

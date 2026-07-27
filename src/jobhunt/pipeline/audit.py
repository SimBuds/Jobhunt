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
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jobhunt.errors import PipelineError
from jobhunt.pipeline._keywords import peer_family_of, peer_match, phrase_present
from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.cover_validate import validate_cover
from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredResume, _enforce_no_fabrication

# Adzuna ships truncated description snippets (~500 chars). Below this
# threshold, the audit broadens must-have extraction via PEER_FAMILIES so a
# JD that mentions "Vue" can still surface "React" as a must-have for the candidate.
# Long full JDs (Greenhouse, Lever, manual) already have enough surface text
# to land canonical tech names; broadening there would create false positives.
_SHORT_JD_THRESHOLD = 800

# Coverage threshold (Scale.jobs 2026 ATS guidance: aim 70-80%). Soft line —
# below this triggers `revise`; the user can still ship.
MIN_KEYWORD_COVERAGE_PCT = 70

# Hard floor — below this the keyword screen will toss the resume before any
# human sees it, so we escalate to `block` and apply_cmd skips the job.
HARD_COVERAGE_FLOOR_PCT = 50


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


# Project anchors are DERIVED from verified.json at audit time, not hard-coded,
# so the alignment check works for any candidate's profile. Each work-history
# role and each project is a "source"; a term (unigram or bigram mined from the
# employer/name + bullets) anchors a source only when it is DISTINCTIVE, i.e. it
# appears in exactly one source. This automatically drops shared platforms like
# "Shopify" (in two roles) and shared tech like "Ollama" (in several projects),
# enforcing the original rule that an anchor must identify ONE verified project,
# with no curated term list to maintain.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Generic resume/employer filler that should never become an anchor term even
# when it lands in a single source. Cross-source words are already dropped by
# the distinctiveness filter; this list only guards single-source noise.
_ANCHOR_STOPWORDS: frozenset[str] = frozenset({
    "built", "build", "maintained", "designed", "shipped", "developed", "created",
    "page", "pages", "item", "items", "skus", "monthly", "visitors", "serving",
    "custom", "brand", "agency", "retailer", "confidential", "company", "group",
    "studio", "solutions", "technologies", "labs", "venue", "venues", "multiple",
    "toronto", "client", "clients", "project", "projects", "team", "teams",
    "stack", "developer", "development", "theme", "themes", "module", "modules",
    "layouts", "storefront", "years", "year", "shopify",
})


def _anchor_terms(text: str) -> set[str]:
    """Mine candidate anchor terms (unigrams >= 4 chars + adjacent bigrams) from
    one source's text, lowercased and normalized to alnum tokens."""
    toks = _TOKEN_RE.findall(text.lower())
    terms: set[str] = set()
    for tok in toks:
        if len(tok) >= 4 and not tok.isdigit() and tok not in _ANCHOR_STOPWORDS:
            terms.add(tok)
    for a, b in zip(toks, toks[1:], strict=False):
        if a in _ANCHOR_STOPWORDS and b in _ANCHOR_STOPWORDS:
            continue
        if len(a) < 2 or len(b) < 2:
            continue
        terms.add(f"{a} {b}")
    return terms


def _anchor_key(name: str) -> str:
    """Stable, readable key for an anchor source. Prefers a non-generic
    parenthetical proper name (e.g. 'Atelier Dacko'), else the leading words."""
    inner_match = re.search(r"\(([^)]+)\)", name)
    inner = inner_match.group(1) if inner_match else ""
    base = (
        inner
        if inner and inner.lower() not in {"confidential", "nda"}
        else re.sub(r"\([^)]*\)", "", name)
    )
    toks = _TOKEN_RE.findall(base.lower())
    return "_".join(toks[:3]) or "source"


def _derive_project_anchors(
    verified: dict[str, Any],
) -> tuple[tuple[str, frozenset[str]], ...]:
    """Build per-project anchors from verified.json. A term anchors a source
    only if it is distinctive (document frequency 1 across all sources)."""
    sources: list[tuple[str, str]] = []
    for role in verified.get("work_history", []):
        text = role.get("employer", "") + " " + " ".join(role.get("bullets", []))
        sources.append((_anchor_key(role.get("employer", "")), text))
    for proj in verified.get("projects", []):
        text = " ".join(
            [
                proj.get("name", ""),
                " ".join(proj.get("bullets", [])),
                " ".join(proj.get("stack", [])),
            ]
        )
        sources.append((_anchor_key(proj.get("name", "")), text))

    per_source = [_anchor_terms(text) for _, text in sources]
    df: Counter[str] = Counter()
    for terms in per_source:
        df.update(terms)

    anchors: list[tuple[str, frozenset[str]]] = []
    used_keys: set[str] = set()
    for (key, _), terms in zip(sources, per_source, strict=True):
        distinctive = frozenset(t for t in terms if df[t] == 1)
        if distinctive and key not in used_keys:
            anchors.append((key, distinctive))
            used_keys.add(key)
    return tuple(anchors)


def _find_project_anchor(
    text: str, anchors: tuple[tuple[str, frozenset[str]], ...]
) -> str | None:
    """Return the first anchor key whose terms appear in `text` (token-boundary,
    case-insensitive). None if no anchor matches. Used by the alignment check."""
    low = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    for anchor_key, terms in anchors:
        if any(f" {term} " in low for term in terms):
            return anchor_key
    return None


def _alignment_flags(
    tailored: TailoredResume,
    cover: CoverLetter,
    anchors: tuple[tuple[str, frozenset[str]], ...],
) -> list[str]:
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
    cover_anchor = _find_project_anchor(cover_mid, anchors)
    if cover_anchor is None:
        return []
    first_role = tailored.roles[0]
    if not first_role.bullets:
        return []
    resume_lead_anchor = _find_project_anchor(first_role.bullets[0], anchors)
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
    for proj in tailored.projects:
        parts.append(proj.name)
        parts.extend(proj.stack)
        parts.extend(proj.bullets)
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
        if phrase_present(phrase, blob) or peer_match(phrase, blob):
            matched.append(phrase)
        else:
            missing.append(phrase)
    pct = round(100 * len(matched) / len(must_haves))
    return pct, matched, missing


def _verified_skills(verified: dict[str, Any]) -> list[str]:
    skills: list[str] = []
    for key in (
        "skills_core",
        "skills_cms",
        "skills_data_devops",
        "skills_ai",
        "skills_projects",
        "skills_familiar",
    ):
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
    description_text = job_description or ""
    is_short = bool(description_text) and len(description_text) < _SHORT_JD_THRESHOLD

    # Two-pass: first pass collects direct hits and tracks which peer families
    # are already covered. Second pass adds peer-broadened inferences only
    # when no sibling from the same family already matched directly.
    #
    # Phase 10.1 fix: the candidate has both AWS and Azure verified; when the JD names
    # only AWS, the old single-pass added Azure as an inferred must-have via
    # the cloud_provider family. The tailor (correctly) didn't include Azure,
    # so audit marked it missing and dropped coverage to 80%. With the dedupe,
    # AWS matches directly → cloud_provider family is "covered" → Azure is
    # not added as a peer inference. Coverage stays 100% honestly.
    direct: list[str] = []
    covered_families: set[frozenset[str]] = set()
    for s in _verified_skills(verified):
        if phrase_present(s, blob):
            direct.append(s)
            family = peer_family_of(s)
            if family is not None:
                covered_families.add(family)

    out: list[str] = list(direct)
    if is_short:
        for s in _verified_skills(verified):
            if s in direct:
                continue
            family = peer_family_of(s)
            if family is not None and family in covered_families:
                # Same-family sibling already matched directly; skip the
                # inferred add to avoid manufactured "missing" must-haves.
                continue
            if peer_match(s, blob):
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

    alignment = _alignment_flags(tailored, cover, _derive_project_anchors(verified))

    if fabrication_flags or (
        coverage_pct is not None and coverage_pct < HARD_COVERAGE_FLOOR_PCT
    ):
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

"""Interview-prep pipeline.

Hybrid generator: deterministic skeleton (header, comp heads-up, pre-call
checklist) + structured LLM call for high-judgment sections (anchors,
likely questions, honest gaps). Mirrors `pipeline.answer` for the LLM
retry loop and reuses cover-validator honesty helpers.

Public surface:
- `draft_prep_sections` — single LLM call returning a `PrepDocSections`.
- `draft_prep_with_retry` — re-prompts on validator violations.
- `validate_prep_sections` — reuses cover_validate banned phrases /
  defensive patterns / fabrication watchlist / unverified-numbers checks
  against the concatenated LLM output. Also enforces the identity-subset
  rule from `pipeline.tailor` against each anchor.
- `render_prep_markdown` — composes the final .md file.

Designed to be invoked from `commands.interview_prep_cmd`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.pipeline._profile import FALLBACK_NAME, display_name
from jobhunt.pipeline._profile import candidate_name as _name
from jobhunt.pipeline._recap import recap_tokens
from jobhunt.pipeline.cover_validate import (
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
from jobhunt.pipeline.score import MAX_DESC_CHARS

# Valid `--stage` values. Kept here (not in config) so the prompt and CLI
# agree on the enum.
VALID_STAGES: tuple[str, ...] = ("agency", "hiring_manager", "assessment")

# JD body cap is bound to the score/tailor/cover budget (MAX_DESC_CHARS) so prep
# sees the same JD scope the scoring path does. A DB sweep (2026-06-04, n=469)
# found 63% of JDs exceed 6000 chars, so the prior 6000 cap clipped most JDs'
# requirements tail; 16000 clips only 4% and stays well under the 32k context
# budget. _RESEARCH_MAX_CHARS stays 6000 pending its own phase.
_JD_MAX_CHARS = MAX_DESC_CHARS
# Research blob holds up to two fetched pages (JD URL + company root), each
# capped at 9000 chars by interview_prep_cmd._strip_html, so 18000 lets both
# survive instead of the old 6000 cap dropping the second source. At ~4.5k
# tokens it sits comfortably under the 32k context budget (P2, 2026-06-04).
_RESEARCH_MAX_CHARS = 18000


@dataclass
class LikelyQuestion:
    question: str
    # 3-5 talking-point bullets the user can riff on during the call. Each
    # bullet is a stand-alone string (≤ 25 words) — see the prompt's
    # length-guidance section. Empty list is rejected by the validator.
    beats: list[str]


@dataclass
class HonestGap:
    gap: str
    # 2-4 reframe bullets. Bullet 1 acknowledges the gap honestly; later
    # bullets cite verified adjacent bridges. Empty list is rejected.
    reframes: list[str]


@dataclass
class PrepDocSections:
    role_decode: list[str]
    strongest_anchors: list[str]
    likely_questions: list[LikelyQuestion]
    questions_to_ask: list[str]
    honest_gaps: list[HonestGap]
    model: str = ""


@dataclass
class PrepContext:
    """Deterministic inputs for the renderer + LLM prompt."""

    job_id: str
    job_title: str
    job_company: str
    job_description: str
    job_url: str
    stage: str
    audit_summary: str = ""
    cover_summary: str = ""
    research_blob: str = ""
    comp_section: str = ""  # rendered comp heads-up markdown (may be empty)
    applicant_salary_expectation: str = ""
    # Drives the likely-questions bias in `_RECRUITER_BIAS_BLURB`. One of
    # `internal_recruiter`, `hiring_manager`, `external_agency`, `unknown`
    # (matches `db._VALID_RECRUITER_TYPES`). Defaults to `unknown` →
    # balanced mix.
    recruiter_type: str = "unknown"


_RECRUITER_BIAS_BLURB: dict[str, str] = {
    "internal_recruiter": (
        "Recruiter type: INTERNAL_RECRUITER (company HR / talent acquisition).\n"
        "Likely-questions mix: 60% behavioral (motivation, fit, past situations), "
        "20% compensation / logistics (notice period, work auth, location), "
        "20% role-confirmation (resume walkthrough, recent project)."
    ),
    "hiring_manager": (
        "Recruiter type: HIRING_MANAGER (team lead / director who owns the seat).\n"
        "Likely-questions mix: 70% deep technical + team-fit (architecture choices, "
        "trade-offs you made, debugging stories, system design), "
        "20% scope (autonomy, collaboration patterns, code review philosophy), "
        "10% behavioral. Skip generic 'tell me about yourself' filler."
    ),
    "external_agency": (
        "Recruiter type: EXTERNAL_AGENCY (third-party recruiter, e.g. Robert Half).\n"
        "Likely-questions mix: 60% personal/soft-skills (communication style, "
        "career goals, why now, salary expectations), 30% behavioral, "
        "10% basic resume confirmation. Deep technical questions are rare at "
        "this stage; technical fit gets re-evaluated by the hiring company."
    ),
    "unknown": (
        "Recruiter type: UNKNOWN. Produce a balanced question mix across "
        "technical, behavioral, motivation, and logistics."
    ),
}


# --- LLM call -----------------------------------------------------------------


async def draft_prep_sections(
    cfg: Config,
    *,
    ctx: PrepContext,
    revisions: str = "",
) -> PrepDocSections:
    """Single-attempt LLM call. Raises `PipelineError` on schema mismatch."""
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `jobhunt convert-resume`")
    verified_text = verified_path.read_text(encoding="utf-8")

    prompt = load_prompt(cfg.paths.kb_dir, "interview-prep")
    _verified = json.loads(verified_text)
    system = prompt.render_system(
        candidate_name=_name(_verified),
        candidate_full_name=display_name(_verified),
    )
    recruiter_type = ctx.recruiter_type or "unknown"
    recruiter_bias = _RECRUITER_BIAS_BLURB.get(
        recruiter_type, _RECRUITER_BIAS_BLURB["unknown"]
    )
    user = prompt.render_user(
        verified_facts=verified_text,
        stage=ctx.stage,
        job_title=ctx.job_title or "(unknown)",
        job_company=ctx.job_company or "(unknown)",
        job_description=_truncate(ctx.job_description, _JD_MAX_CHARS),
        applicant_salary_expectation=(
            ctx.applicant_salary_expectation
            or cfg.applicant.salary_expectation_cad
            or "(not configured)"
        ),
        applicant_work_auth_canada=str(cfg.applicant.work_auth_canada).lower(),
        applicant_requires_visa_sponsorship=str(
            cfg.applicant.requires_visa_sponsorship
        ).lower(),
        audit_summary=ctx.audit_summary or "(no application yet)",
        cover_summary=ctx.cover_summary or "(no cover letter drafted)",
        research_blob=_truncate(ctx.research_blob, _RESEARCH_MAX_CHARS) or "(none)",
        interview_context=_format_interview_context_for_prompt(
            build_interview_context(ctx.stage)
        ),
        recruiter_type=recruiter_type,
        recruiter_bias=recruiter_bias,
        revisions=revisions,
    )

    # Force temperature=0 on retry attempts so the corrective hint isn't
    # re-sampled away (matches the same pattern in pipeline.answer and
    # pipeline.tailor — at the frontmatter default temperature qwen often
    # re-samples the violation it was just told to fix).
    temperature = 0.0 if revisions else prompt.temperature

    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks.get(
        "tailor", "qwen-custom:latest"
    )
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=system,
        user=user,
        schema=prompt.schema,
        temperature=temperature,
    )
    return _decode_sections(raw, model=model)


def _decode_sections(raw: dict[str, Any], *, model: str) -> PrepDocSections:
    """Decode the LLM payload into typed sections. Tolerant of qwen's common
    shape drift: `likely_questions` items emitted as strings instead of
    `{question, beat}` dicts, alternate key names like `answer` for `beat`
    or `gap_description` for `gap`. Surfaces the offending field in the
    error message so retries can carry a useful hint.
    """
    try:
        role = [str(x) for x in raw["role_decode"]]
        anchors = [str(x) for x in raw["strongest_anchors"]]
        likely = [_coerce_likely_question(q) for q in raw["likely_questions"]]
        ask = [str(x) for x in raw["questions_to_ask"]]
        gaps = [_coerce_honest_gap(g) for g in raw["honest_gaps"]]
    except (KeyError, TypeError, ValueError) as e:
        raise PipelineError(
            f"interview-prep returned malformed shape: {e} "
            f"(keys={sorted(raw.keys())})"
        ) from e
    return PrepDocSections(
        role_decode=role,
        strongest_anchors=anchors,
        likely_questions=likely,
        questions_to_ask=ask,
        honest_gaps=gaps,
        model=model,
    )


def _coerce_likely_question(q: Any) -> LikelyQuestion:
    """Tolerant constructor. Accepts:
    - the schema'd form `{question, beats: [str, ...]}` (current);
    - a legacy `{question, beat: str}` (older payloads / qwen drift) —
      single string wrapped as `[beat]`;
    - alternate keys (`answer`/`response`/`how_to_answer`) for the legacy
      single-string slot;
    - a plain string (treated as the question with empty `beats=[]`).
    Drops empty bullets; preserves order.
    """
    if isinstance(q, str):
        return LikelyQuestion(question=q, beats=[])
    if not isinstance(q, dict):
        raise ValueError(f"likely_question must be dict or str, got {type(q).__name__}")
    question = q.get("question") or q.get("q") or ""
    beats = _normalize_bullet_list(
        q.get("beats")
        or q.get("beat")
        or q.get("answer")
        or q.get("response")
        or q.get("how_to_answer")
        or []
    )
    return LikelyQuestion(question=str(question), beats=beats)


def _coerce_honest_gap(g: Any) -> HonestGap:
    """Same tolerance as `_coerce_likely_question` for the `honest_gaps`
    list. Accepts the schema'd `{gap, reframes: [str, ...]}`, the legacy
    single-string `{gap, reframe}` / `{gap_description, response}` (qwen
    drift), or a plain string (treated as the gap with empty
    `reframes=[]`)."""
    if isinstance(g, str):
        return HonestGap(gap=g, reframes=[])
    if not isinstance(g, dict):
        raise ValueError(f"honest_gap must be dict or str, got {type(g).__name__}")
    gap = g.get("gap") or g.get("gap_description") or g.get("weakness") or ""
    reframes = _normalize_bullet_list(
        g.get("reframes")
        or g.get("reframe")
        or g.get("response")
        or g.get("how_to_reframe")
        or g.get("framing")
        or []
    )
    return HonestGap(gap=str(gap), reframes=reframes)


def _normalize_bullet_list(raw: Any) -> list[str]:
    """Coerce a bullet-list payload into a clean ``list[str]``.

    Accepts a list (filter to non-empty strings), a single string (wrap
    as one-element list), or anything else (return empty list — the
    validator will flag the missing bullets).
    """
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
    if isinstance(raw, str):
        stripped = raw.strip()
        return [stripped] if stripped else []
    return []


# --- validation ---------------------------------------------------------------


def validate_prep_sections(
    sections: PrepDocSections,
    *,
    verified: dict[str, Any],
    allowed_numbers: set[str] | None = None,
    job_description: str = "",
) -> list[str]:
    """Return a list of violation strings. Empty list = clean.

    Reuses `cover_validate` helpers on the concatenated free-form output
    and adds an identity-subset check on each anchor.
    """
    violations: list[str] = []
    free_text = _concat_freeform(sections)
    body_lower = _normalize(free_text)

    for i, q in enumerate(sections.likely_questions, start=1):
        if not q.question.strip():
            violations.append(f"likely question {i} is blank")
        if not q.beats or not any(b.strip() for b in q.beats):
            violations.append(f"likely question {i} is missing an answer beat")

    for i, gap in enumerate(sections.honest_gaps, start=1):
        if not gap.gap.strip():
            violations.append(f"honest gap {i} is blank")
        if not gap.reframes or not any(r.strip() for r in gap.reframes):
            violations.append(f"honest gap {i} is missing a reframe")

    # Banned phrases (substring tier).
    for phrase in BANNED_PHRASES:
        if phrase in body_lower:
            violations.append(f"banned phrase: {phrase!r}")

    # Defensive gap-volunteering patterns.
    for pattern, label in _DEFENSIVE_PATTERNS:
        if re.search(pattern, body_lower):
            violations.append(label)

    # Unverified numbers (digit-cluster check) — strip year ranges and
    # clock-style references first, matching the cover/answer preprocessor.
    # Only check the candidate-owned claims. Role decode / questions-to-ask often cite
    # employer-scale JD context ("100+ client sites", "Fortune 500"), which is
    # useful interview prep and not a claim about the candidate.
    allowed = _verified_numbers(verified)
    if allowed_numbers:
        allowed.update(allowed_numbers)
    scratch = _TIME_OF_DAY_RE.sub(" ", _candidate_claim_text(sections))
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

    for token in recap_tokens(verified, extra=("coursework",)):
        if token in body_lower:
            violations.append(f"interview-prep education recap: {token!r}")

    if re.search(r"\b(?:start|available)\s+immediately\b", body_lower):
        violations.append("unverified availability claim: immediate start")
    if re.search(r"\b(?:within|in)\s+two\s+weeks\b|\btwo-week\b", body_lower):
        violations.append("unverified availability claim: two-week notice")

    # Fabrication watchlist with negation-context suppression.
    verified_blob = _verified_skill_blob(verified)
    verified_blob_lower = verified_blob.lower()
    jd_only_phrases = _jd_only_claim_phrases(job_description, verified_blob)
    claim_bullets = _candidate_claim_bullets(sections)
    reported: set[str] = set()
    for bullet in claim_bullets:
        bullet_lower = bullet.lower()
        for phrase in jd_only_phrases:
            if phrase in reported:
                continue
            if _bullet_claims_phrase(bullet_lower, phrase):
                violations.append(
                    f"candidate claim mirrors unverified JD phrase: {phrase!r}"
                )
                reported.add(phrase)

    for i, gap in enumerate(sections.honest_gaps, start=1):
        # Verified-trace check: at least ONE reframe bullet must trace to
        # verified facts. A bullet 1 that's pure acknowledgement
        # ("I have not shipped X") legitimately has no trace; bullet 2+
        # is where the bridge lives.
        non_empty = [r for r in gap.reframes if r.strip()]
        if non_empty and not any(
            _has_verified_trace(r, verified_blob_lower) for r in non_empty
        ):
            violations.append(f"honest gap {i} reframe lacks verified trace")
        for bullet in non_empty:
            bullet_lower = bullet.lower()
            for phrase in jd_only_phrases:
                if phrase in bullet_lower:
                    violations.append(
                        f"honest gap {i} reframe mirrors unverified JD phrase: {phrase!r}"
                    )
                    break  # one report per bullet is enough

    for tech in _FABRICATION_WATCHLIST:
        token = tech.strip(", ")
        if not token:
            continue
        tech_re = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
        if not tech_re.search(free_text):
            continue
        if tech_re.search(verified_blob):
            continue
        all_negated = True
        for m in tech_re.finditer(body_lower):
            window = body_lower[max(0, m.start() - 40) : m.start()]
            if not _NEGATION_PRECEDES_RE.search(window):
                all_negated = False
                break
        if all_negated:
            continue
        violations.append(f"unverified tech claim: {token!r}")

    # Anchor authenticity check — anchors are full sentences, so the strict
    # token-subset rule from `tailor._enforce_no_fabrication` doesn't apply
    # cleanly (stop words like "for" fragment the check). Instead, require
    # each anchor to contain at least one substantive token (alphabetic,
    # length ≥ 5) that appears verbatim in the verified blob (skills + work
    # history + summary). This rejects "Built Kubernetes clusters" (no
    # substantive token traces) while accepting "Built a 14+ page Shopify
    # storefront for Acme Retail" (shopify, storefront, acme all trace).
    for anchor in sections.strongest_anchors:
        if not _substantive_tokens(anchor):
            # Pure stop-word / numeric anchor — too vague, but don't reject;
            # the LLM may emit a project-style phrase ("Three phases over
            # two years") that's still a valid anchor.
            continue
        if _has_verified_trace(anchor, verified_blob_lower):
            continue
        violations.append(f"unverified anchor: {anchor!r}")

    return violations


def has_blocking_prep_violations(violations: list[str]) -> bool:
    """Return True for structural failures that make the rendered prep unusable.

    Content warnings like an unverified number can still be surfaced for human
    review, but missing answer beats/reframes produce hollow sections and should
    not overwrite a usable prep artifact.
    """
    blocking_needles = (
        "is blank",
        "missing an answer beat",
        "missing a reframe",
        "unverified number",
        "interview-prep education recap",
        "unverified availability claim",
        "reframe lacks verified trace",
        "reframe mirrors unverified jd phrase",
        "candidate claim mirrors unverified jd phrase",
        "unverified anchor",
    )
    return any(
        any(needle in violation.lower() for needle in blocking_needles)
        for violation in violations
    )


def _concat_freeform(sections: PrepDocSections) -> str:
    parts: list[str] = []
    parts.extend(sections.role_decode)
    parts.extend(sections.strongest_anchors)
    for q in sections.likely_questions:
        parts.append(q.question)
        parts.extend(q.beats)
    parts.extend(sections.questions_to_ask)
    for g in sections.honest_gaps:
        parts.append(g.gap)
        parts.extend(g.reframes)
    return "\n".join(parts)


def _candidate_claim_text(sections: PrepDocSections) -> str:
    parts: list[str] = []
    parts.extend(sections.strongest_anchors)
    for q in sections.likely_questions:
        parts.extend(q.beats)
    for g in sections.honest_gaps:
        parts.extend(g.reframes)
    return "\n".join(parts)


def _all_verified_skills(verified: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("skills_core", "skills_cms", "skills_data_devops", "skills_ai", "skills_familiar"):
        out.extend(str(s) for s in verified.get(key, []))
    return out


# --- retry loop ---------------------------------------------------------------


async def draft_prep_with_retry(
    cfg: Config,
    *,
    ctx: PrepContext,
    verified: dict[str, Any],
    max_attempts: int,
) -> tuple[PrepDocSections, list[str], int]:
    """Mirror of `pipeline.answer.write_answer_with_retry`. Returns
    `(sections, final_violations, attempts_used)`.
    """
    attempts = max(1, max_attempts)
    last_sections: PrepDocSections | None = None
    last_violations: list[str] = []
    revisions = ""
    for attempt in range(1, attempts + 1):
        sections = await draft_prep_sections(cfg, ctx=ctx, revisions=revisions)
        allowed_numbers = _numbers_from_text(cfg.applicant.salary_expectation_cad)
        violations = validate_prep_sections(
            sections,
            verified=verified,
            allowed_numbers=allowed_numbers,
            job_description=ctx.job_description,
        )
        if not violations:
            return sections, [], attempt
        last_sections = sections
        last_violations = violations
        revisions = _format_revision_hint(
            violations, attempt, candidate_name=_name(verified)
        )
    assert last_sections is not None

    # Last-resort cleanup for qwen's repeat offenders in interview prep.
    # These are narrow rewrites from unsafe generic phrasing to verified,
    # lower-claim phrasing. Only keep them when they reduce violations.
    patched = _patch_prep_sections(last_sections, cfg=cfg)
    if patched is not None:
        allowed_numbers = _numbers_from_text(cfg.applicant.salary_expectation_cad)
        patched_violations = validate_prep_sections(
            patched,
            verified=verified,
            allowed_numbers=allowed_numbers,
            job_description=ctx.job_description,
        )
        if len(patched_violations) < len(last_violations):
            return patched, patched_violations, attempts

    return last_sections, last_violations, attempts


def _patch_prep_sections(sections: PrepDocSections, *, cfg: Config) -> PrepDocSections | None:
    changed = False

    def patch_claim(text: str) -> str:
        nonlocal changed
        original = text
        out = text
        if "coursework" in out.lower():
            out = (
                "I stay current through hands-on project work, local LLM "
                "tooling with Ollama, and CMS implementation work across "
                "Shopify and HubSpot."
            )
        out = _replace_case_insensitive(
            out,
            "I can start immediately with full work authorization.",
            (
                "I have Canadian work authorization and can discuss availability "
                "once the role structure and timeline are clear."
            ),
        )
        out = _replace_case_insensitive(
            out,
            "can start immediately",
            "can discuss availability once the role structure and timeline are clear",
        )
        out = _replace_case_insensitive(
            out,
            "or within two weeks depending on the offer",
            "",
        )
        out = _replace_case_insensitive(
            out,
            "within two weeks depending on the offer",
            "once the role structure and timeline are clear",
        )
        out = _replace_case_insensitive(
            out,
            "full automation",
            "CMS implementation, scripting, and QA work",
        )
        out = _replace_case_insensitive(
            out,
            "fully automate",
            "improve CMS publishing workflows",
        )
        out = _replace_case_insensitive(
            out,
            "zero errors",
            "fewer preventable QA issues",
        )
        out = _replace_case_insensitive(
            out,
            "automated content upload systems",
            "CMS implementation and scripting workflows",
        )
        # JD-mirror: "AI-generated content pipeline" — verbatim borrow that
        # qwen latches onto for any AI-flavored content role. Replace with
        # a narrower phrasing that names the candidate's actual verified surface
        # (Ollama local LLM + Shopify/HubSpot CMS) so the artifact still
        # speaks to the JD without claiming a product the candidate hasn't shipped.
        out = _replace_case_insensitive(
            out,
            "ai-generated content pipeline",
            "Ollama local-LLM tooling alongside Shopify and HubSpot CMS work",
        )
        out = _replace_case_insensitive(
            out,
            "ai generated content pipeline",
            "Ollama local-LLM tooling alongside Shopify and HubSpot CMS work",
        )
        out = _replace_case_insensitive(
            out,
            "full content automation",
            "CMS content workflows",
        )
        out = _replace_case_insensitive(
            out,
            "content and fulfillment teams",
            "the content team and adjacent stakeholders",
        )
        out = _replace_case_insensitive(
            out,
            "automated systems",
            "CMS and scripting workflows",
        )
        out = _replace_case_insensitive(
            out,
            "developed scripts and API integrations that keep pipelines running smoothly",
            (
                "wrote bulk JSON migrations and set up GitHub Actions CI "
                "pipelines with automated linting"
            ),
        )
        out = _replace_case_insensitive(
            out,
            "scripts and API integrations",
            "bulk JSON migrations and GitHub Actions CI",
        )
        out = _replace_case_insensitive(
            out,
            "error-free implementation",
            "careful implementation",
        )
        out = _replace_case_insensitive(
            out,
            "replace manual uploads",
            "reduce manual CMS work",
        )
        out = _replace_case_insensitive(
            out,
            "replacing manual uploads",
            "reducing manual CMS work",
        )
        if out != original:
            changed = True
        return out

    likely = [
        LikelyQuestion(
            question=q.question,
            beats=[patch_claim(b) for b in q.beats],
        )
        for q in sections.likely_questions
    ]
    gaps = [
        HonestGap(
            gap=g.gap,
            reframes=[patch_claim(r) for r in g.reframes],
        )
        for g in sections.honest_gaps
    ]
    anchors = [
        patch_claim(a)
        for a in sections.strongest_anchors
        if not _is_logistics_anchor(a)
    ]
    if len(anchors) != len(sections.strongest_anchors):
        changed = True

    if not changed:
        return None
    return PrepDocSections(
        role_decode=sections.role_decode,
        strongest_anchors=anchors,
        likely_questions=likely,
        questions_to_ask=sections.questions_to_ask,
        honest_gaps=gaps,
        model=sections.model,
    )


def _replace_case_insensitive(text: str, needle: str, replacement: str) -> str:
    pattern = re.compile(re.escape(needle), re.IGNORECASE)

    def repl(match: re.Match[str]) -> str:
        matched = match.group(0)
        if matched and matched[0].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return pattern.sub(repl, text)


def _is_logistics_anchor(text: str) -> bool:
    low = text.lower()
    logistics_tokens = (
        "authorized to work",
        "work authorization",
        "visa sponsorship",
        "can start",
        "notice period",
        "availability",
        "timeline you need",
    )
    return any(token in low for token in logistics_tokens)


def _format_revision_hint(
    violations: list[str], attempt: int, *, candidate_name: str = FALLBACK_NAME
) -> str:
    lines = [
        "",
        "## Previous attempt was rejected by the validator. Fix these:",
    ]
    for v in violations:
        lines.append(f"- {v}")
    lines.append(
        f"Rewrite the prep sections from scratch. This is retry {attempt + 1}; "
        "do not reuse phrasing from the prior attempt that triggered a "
        "violation. Anchors must trace to real items in verified_facts."
    )
    # Targeted hint when at least one violation is `unverified number:` —
    # the dominant cause is qwen pulling stats from the research blob
    # (company pricing, metric strips) and dropping them into beats. Tell
    # the model explicitly that those numbers don't count as the candidate's facts.
    if any("unverified number" in v.lower() for v in violations):
        lines.append(
            "IMPORTANT: numbers appearing in the `research_blob` section are "
            "the EMPLOYER's stats (pricing, user counts, hero metrics). They "
            "are NOT yours to cite in anchors, beats, or questions. Only "
            "numbers that appear inside `verified_facts.work_history` "
            "bullets (e.g. '14+ page Shopify storefront', '30% page load "
            "reduction') are quotable. If you need to mention an employer "
            "stat at all, name it as 'their N customers' or similar — "
            "never as your own work product."
        )
        lines.append(
            "For this retry, avoid all unverified employer-scale numbers in "
            "answers and gap reframes, including client counts, team sizes, "
            "Fortune rankings, and the JD's posted pay range. Salary answers "
            "must use applicant logistics only."
        )
    if any("missing an answer beat" in v.lower() for v in violations):
        lines.append(
            "Every likely_questions item must include a non-empty `beats` "
            "list of 3-5 short talking-point bullets (≤ 25 words each). "
            "Bullet 1 leads with a real project name; bullets 2-N add "
            "specifics from verified_facts. Do NOT collapse the beats into "
            "a single sentence."
        )
    if any("missing a reframe" in v.lower() for v in violations):
        lines.append(
            "Every honest_gaps item must include a non-empty `reframes` "
            "list of 2-4 short bullets (≤ 25 words each). Bullet 1 "
            "acknowledges the gap honestly; bullet 2+ names a verified "
            "adjacent bridge with project/tech specifics. Do NOT emit "
            "a single sentence."
        )
    if any("education recap" in v.lower() for v in violations):
        lines.append(
            "Do not mention the school name, diploma, honours, or coursework "
            "inside interview prep answers. That material belongs on the resume."
        )
    if any("availability claim" in v.lower() for v in violations):
        lines.append(
            f"Do not claim {candidate_name} can start immediately unless that exact "
            "fact is present in applicant logistics. Use a confirm-on-call phrasing "
            "instead."
        )
    if any("reframe lacks verified trace" in v.lower() for v in violations):
        lines.append(
            "Every honest gap reframe must name a verified adjacent project, "
            "skill, or work-history fact. Do not answer with generic confidence."
        )
    if any("reframe mirrors unverified jd phrase" in v.lower() for v in violations):
        lines.append(
            f"Do not reframe a gap by claiming {candidate_name} has already done the "
            "JD's exact unverified work. Use a narrower verified bridge instead."
        )
    if any("candidate claim mirrors unverified jd phrase" in v.lower() for v in violations):
        lines.append(
            f"Do not put the JD's exact ownership phrases into {candidate_name}-owned "
            "answers, anchors, or gap reframes unless verified_facts already "
            "contains that work. Replace them with narrower verified facts."
        )
    return "\n".join(lines)


def _numbers_from_text(text: str) -> set[str]:
    return set(_DIGIT_CLUSTER_RE.findall(text or ""))


_CLAIM_JD_PHRASES: tuple[str, ...] = (
    "automated content upload systems",
    "ai-generated content pipeline",
    "full content automation",
    "full automation",
    "automated systems",
    "scripts and api integrations",
    "pipelines running smoothly",
    "error-free implementation",
    "replace manual uploads",
    "replacing manual uploads",
    "zero errors",
    "100+ client",
    "fortune 500",
    "director of search",
    "content and fulfillment teams",
)


def _jd_only_claim_phrases(job_description: str, verified_blob: str) -> list[str]:
    jd_lower = job_description.lower()
    verified_lower = verified_blob.lower()
    return [
        phrase
        for phrase in _CLAIM_JD_PHRASES
        if phrase in jd_lower and phrase not in verified_lower
    ]


# Title-style phrases identify someone the candidate would WORK WITH, not someone
# The candidate IS. A beat that says "you'd coordinate with the Director of Search"
# is legitimate context; a beat that says "I led search as Director of
# Search" is a fabrication. Distinguish them by requiring a strict
# ownership pattern (as the / my role as / I am/was/served/held) in the
# 40-char window before the title before firing the mirror check.
_TITLE_PHRASE_RE = re.compile(
    r"\b(?:director|head|vp|vice\s+president|chief|cto|cmo|ceo|cpo|svp)\s+of\b",
    re.IGNORECASE,
)
# Patterns that signal the candidate claims the title that follows the window.
_TITLE_OWNERSHIP_RES = (
    # `as the` / `as a` / `as` right before the title.
    re.compile(r"\bas(?:\s+(?:the|a|an))?\s*$", re.IGNORECASE),
    # `my role as` ... title.
    re.compile(r"\bmy\s+role\s+as\b", re.IGNORECASE),
    # `I am/was/served/hold/held` somewhere in the recent window.
    re.compile(r"\bi\s+(?:am|was|served|hold|held)\b", re.IGNORECASE),
)


def _is_title_phrase(phrase: str) -> bool:
    return bool(_TITLE_PHRASE_RE.search(phrase))


def _bullet_claims_phrase(bullet_lower: str, phrase: str) -> bool:
    """True when `bullet_lower` makes a the candidate-owned claim of `phrase`.

    For activity-style JD phrases (e.g. "automated content upload systems"),
    any appearance in a the candidate-claim section is treated as a fabrication.
    For title-style phrases (e.g. "Director of Search"), require an
    explicit ownership pattern in the 40-char window before the phrase —
    context references like "you'd report to the Director of Search"
    don't fire.
    """
    if phrase not in bullet_lower:
        return False
    if not _is_title_phrase(phrase):
        return True
    idx = bullet_lower.find(phrase)
    window = bullet_lower[max(0, idx - 40):idx]
    return any(rx.search(window) for rx in _TITLE_OWNERSHIP_RES)


def _candidate_claim_bullets(sections: PrepDocSections) -> list[str]:
    """Flat list of every the candidate-owned bullet. Each item gets the
    per-bullet mirror check — bullet-scoped windowing is what lets the
    title-phrase ownership marker live in the same string."""
    out: list[str] = []
    out.extend(sections.strongest_anchors)
    for q in sections.likely_questions:
        out.extend(q.beats)
    for g in sections.honest_gaps:
        out.extend(g.reframes)
    return out


def _substantive_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower()) if len(t) >= 5]


def _has_verified_trace(text: str, verified_blob_lower: str) -> bool:
    return any(tok in verified_blob_lower for tok in _substantive_tokens(text))


# --- markdown renderer --------------------------------------------------------


_STAGE_LABEL: dict[str, str] = {
    "agency": "Agency Screen",
    "hiring_manager": "Hiring Manager",
    "assessment": "Assessment / Final Round",
}


def build_interview_context(stage: str) -> list[str]:
    """Deterministic May 2026 interview-prep context.

    Keep this focused on likely interview coverage and candidate strategy, not
    the candidate-specific claims. It is rendered into the prep doc and injected into
    the prompt so the LLM's question choices match the current hiring loop.
    """
    common = [
        (
            "AI-tool fluency is now a normal screening topic, but teams verify "
            "judgment: be ready to explain where AI helped, what you reviewed "
            "manually, and where you would not trust it."
        ),
        (
            "Practical work samples are favored over abstract trivia in many "
            "technical loops; expect the next stage to test a real CMS/API/QA "
            "workflow rather than only definitions."
        ),
        (
            "Recruiters and hiring managers are filtering for specificity "
            "because AI-written applications have made generic answers less "
            "useful."
        ),
    ]
    by_stage: dict[str, list[str]] = {
        "agency": [
            (
                "Agency screens usually validate basics before selling you "
                "internally: salary range, work authorization, remote/timezone "
                "fit, notice period, motivation, and whether your resume maps "
                "to the JD."
            ),
            (
                "Keep technical stories short and concrete; the recruiter "
                "needs portable proof points they can repeat to the client or "
                "hiring manager."
            ),
            (
                "Ask what the assessment covers, whether AI tools are allowed, "
                "who evaluates it, and what signals matter most for advancement."
            ),
            (
                "Avoid over-claiming gaps. A good agency answer is narrow: "
                "confirm the gap, name the closest verified bridge, and move "
                "back to the role's core need."
            ),
        ],
        "hiring_manager": [
            (
                "Hiring-manager rounds usually probe ownership, tradeoffs, "
                "debugging, stakeholder communication, and how you decide what "
                "to automate versus leave manual."
            ),
            (
                "Expect follow-ups on project scope: what you personally "
                "built, what broke, what you measured, and how you kept "
                "quality high."
            ),
            (
                "Ask about the first 30-60 days, current failure modes, review "
                "process, and how AI-assisted code is accepted or audited on "
                "the team."
            ),
        ],
        "assessment": [
            (
                "Assessment rounds increasingly resemble job-simulation work: "
                "reading an existing codebase, building a small feature, "
                "fixing a bug, wiring an API, or explaining QA coverage."
            ),
            (
                "Clarify AI-tool policy before using assistants. If tools are "
                "allowed, narrate review steps, tests, and security/privacy "
                "judgment."
            ),
            (
                "Prepare to discuss tradeoffs in the submitted work: what you "
                "optimized, what you deferred, and how you would harden it for "
                "production."
            ),
        ],
    }
    return by_stage.get(stage, by_stage["agency"]) + common


def _format_interview_context_for_prompt(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_prep_markdown(
    sections: PrepDocSections,
    *,
    ctx: PrepContext,
) -> str:
    """Compose the final markdown by wrapping the LLM sections with the
    deterministic header, comp section, and footer.
    """
    stage_label = _STAGE_LABEL.get(ctx.stage, ctx.stage.capitalize())
    parts: list[str] = [
        f"# {ctx.job_company} — {ctx.job_title} ({stage_label})",
        "",
        f"- **Job ID**: `{ctx.job_id}`",
    ]
    if ctx.job_url:
        parts.append(f"- **JD URL**: {ctx.job_url}")
    parts.append(f"- **Stage**: {stage_label}")
    parts.append("")

    if ctx.comp_section:
        parts.append("## Comp heads-up")
        parts.append("")
        parts.append(ctx.comp_section)
        parts.append("")

    context = build_interview_context(ctx.stage)
    if context:
        parts.append("## 2026 interview context")
        parts.append("")
        for item in context:
            parts.append(f"- {item}")
        parts.append("")

    parts.append("## Role decode")
    parts.append("")
    for b in sections.role_decode:
        parts.append(f"- {b}")
    parts.append("")

    parts.append("## Strongest anchors (verified — no fabrication)")
    parts.append("")
    for b in sections.strongest_anchors:
        parts.append(f"- {b}")
    parts.append("")

    parts.append("## Likely questions")
    parts.append("")
    for q in sections.likely_questions:
        parts.append(f"**{q.question}**")
        parts.append("")
        for bullet in q.beats:
            parts.append(f"- {bullet}")
        parts.append("")

    parts.append("## Questions to ask back")
    parts.append("")
    for ask in sections.questions_to_ask:
        parts.append(f"- {ask}")
    parts.append("")

    parts.append("## Honest gaps (don't hide — reframe)")
    parts.append("")
    for g in sections.honest_gaps:
        parts.append(f"- **{g.gap}**")
        for bullet in g.reframes:
            parts.append(f"  - {bullet}")
    parts.append("")

    parts.append("## Pre-call checklist")
    parts.append("")
    parts.extend(
        [
            "- [ ] Skim the company homepage; note one product or initiative by name",
            "- [ ] Have Baseline_Resume.docx and the tailored resume open in tabs",
            "- [ ] Test the video link beforehand (camera, mic)",
            "- [ ] Quiet room, water, JD open in another tab",
            "- [ ] Confirm meeting time in your timezone",
        ]
    )
    parts.append("")

    parts.append("## After the call")
    parts.append("")
    parts.extend(
        [
            f"- Update status: `jobhunt apply --set-status <next-status> {ctx.job_id}`",
            "- If advancing to the next stage, regenerate this doc with the new stage",
            "- Capture any signal the interviewer gave: comp, timeline, hiring manager",
        ]
    )
    parts.append("")

    if sections.model:
        parts.append(f"_Generated by interview-prep ({sections.model})_")
        parts.append("")

    return "\n".join(parts)


def render_skeleton_offline(ctx: PrepContext) -> str:
    """Renderer used by `--no-llm` mode. Produces the deterministic shell
    with TODO placeholders for the LLM sections. Useful for offline /
    debug / scaffold workflows.
    """
    placeholder_sections = PrepDocSections(
        role_decode=["_TODO_ run without --no-llm to populate role decode_"],
        strongest_anchors=["_TODO_ strongest anchors will be drafted by the LLM_"],
        likely_questions=[
            LikelyQuestion(
                question="_TODO_",
                beats=["Run without `--no-llm` to draft questions and beats."],
            )
        ],
        questions_to_ask=["_TODO_ questions to ask back_"],
        honest_gaps=[
            HonestGap(
                gap="_TODO_",
                reframes=["Run without `--no-llm` to draft honest gap reframes."],
            )
        ],
        model="",
    )
    return render_prep_markdown(placeholder_sections, ctx=ctx)


# --- helpers ------------------------------------------------------------------


def _truncate(s: str, limit: int) -> str:
    if not s:
        return ""
    return s if len(s) <= limit else s[:limit] + "\n[truncated]"


# --- comp heads-up (deterministic) --------------------------------------------


# Match common JD salary patterns. Returns (low, high, currency, unit).
# Unit accepts "per hour", "an hour", "/hr", "hourly", "annually", etc. —
# Indeed-style postings write "$18–$19 an hour", not "per hour".
_SALARY_RE = re.compile(
    r"\$(\d{1,3}(?:[,.]\d{3})*(?:\.\d{1,2})?)\s*[–\-—to]+\s*"
    r"\$?(\d{1,3}(?:[,.]\d{3})*(?:\.\d{1,2})?)\s*"
    r"(USD|CAD)?\s*"
    r"(?:per\s+|an?\s+|/\s*)?(hourly|hour|hr|yearly|year|yr|annum|annually)?",
    re.IGNORECASE,
)

# A pay bound below this can't be an annual salary; treat a unitless match
# as hourly. Real annual ranges start ~5 digits, hourly rates run 2-3.
_MAX_PLAUSIBLE_HOURLY = 1000

# Conservative USD→CAD multiplier. Static value is fine here: this is a
# recruiter-call heads-up, not a contract negotiation.
_USD_TO_CAD = 1.37
# Hours per year for hourly-rate annualization.
_FT_HOURS_PER_YEAR = 2080


def extract_comp_section(
    jd_text: str,
    applicant_range_cad: str | None,
) -> str:
    """Build the deterministic comp heads-up markdown.

    Returns empty string if no salary pattern is found in the JD or no
    applicant range is configured.
    """
    if not jd_text or not applicant_range_cad:
        return ""
    m = _SALARY_RE.search(jd_text)
    if m is None:
        return ""
    low_s, high_s, currency, unit = m.groups()
    try:
        low = float(low_s.replace(",", ""))
        high = float(high_s.replace(",", ""))
    except ValueError:
        return ""
    # GTA-scoped tool: an unstated currency is almost always CAD. Defaulting
    # to USD inflated the CAD estimate by the conversion multiplier.
    currency = (currency or "CAD").upper()
    unit_norm = unit.lower() if unit else ("hour" if high < _MAX_PLAUSIBLE_HOURLY else "year")
    is_hourly = unit_norm in {"hour", "hr", "hourly"}

    if is_hourly:
        annual_low = low * _FT_HOURS_PER_YEAR
        annual_high = high * _FT_HOURS_PER_YEAR
    else:
        annual_low = low
        annual_high = high

    if currency == "USD":
        cad_low = annual_low * _USD_TO_CAD
        cad_high = annual_high * _USD_TO_CAD
    else:
        cad_low = annual_low
        cad_high = annual_high

    lines = [
        f"- JD range: **${low:,.2f}–${high:,.2f} {currency}/{unit_norm}**",
    ]
    if is_hourly and currency == "USD":
        lines.append(
            f"- Annualized FT: ~${annual_low:,.0f}–${annual_high:,.0f} {currency} "
            f"(~${cad_low:,.0f}–${cad_high:,.0f} CAD)"
        )
    elif is_hourly:
        lines.append(f"- Annualized FT: ~${cad_low:,.0f}–${cad_high:,.0f} CAD")
    elif currency == "USD":
        lines.append(f"- ~${cad_low:,.0f}–${cad_high:,.0f} CAD")
    lines.append(f"- Your stated range: **{applicant_range_cad}**")
    fit = _range_fit(cad_low, cad_high, applicant_range_cad)
    if fit == "below":
        lines.append(
            "- **Heads-up: the JD range sits below your stated range.** Decide "
            "before the call whether the role is worth the gap. If comp comes "
            "up, anchor to your range, not the posting's."
        )
    elif fit == "above":
        lines.append(
            "- The JD range sits above your stated range. Let them name "
            "numbers first; don't anchor low."
        )
    else:
        lines.append(
            "- Suggested recruiter phrasing: \"Your range looks in line with what "
            "I'm looking at. I'd want to confirm contract vs full-time structure "
            "and benefits before locking a specific number.\""
        )
    return "\n".join(lines)


# Ordered amount parse for the applicant's free-text range ("60,000 - 90,000",
# "60k-90k"). `_numbers_from_text` is set-shaped (allowlist use), so it can't
# supply ordered bounds.
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?", re.IGNORECASE)


def _range_fit(
    jd_cad_low: float, jd_cad_high: float, applicant_range_cad: str
) -> str | None:
    """Compare the JD's annualized CAD range to the applicant's stated range.

    Returns "below" (JD tops out under the applicant floor), "above" (JD
    floor exceeds the applicant ceiling), "overlaps", or None when the
    applicant text yields no parseable amounts (caller keeps neutral
    phrasing).
    """
    amounts: list[float] = []
    for num, k_suffix in _AMOUNT_RE.findall(applicant_range_cad):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        amounts.append(value)
    if not amounts:
        return None
    app_low, app_high = min(amounts), max(amounts)
    if jd_cad_high < app_low:
        return "below"
    if jd_cad_low > app_high:
        return "above"
    return "overlaps"

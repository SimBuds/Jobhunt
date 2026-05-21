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

import re
from dataclasses import dataclass
from typing import Any

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
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

# Valid `--stage` values. Kept here (not in config) so the prompt and CLI
# agree on the enum.
VALID_STAGES: tuple[str, ...] = ("screen", "assessment", "hm", "onsite")

# JD body cap matches answer.py's truncation budget. Comfortably under the
# 20k-token server context with room for verified.json + prompt template.
_JD_MAX_CHARS = 6000
_RESEARCH_MAX_CHARS = 6000


@dataclass
class LikelyQuestion:
    question: str
    beat: str


@dataclass
class HonestGap:
    gap: str
    reframe: str


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
    # Phase 13: drives interview-prep likely-questions bias. One of
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
        audit_summary=ctx.audit_summary or "(no application yet)",
        cover_summary=ctx.cover_summary or "(no cover letter drafted)",
        research_blob=_truncate(ctx.research_blob, _RESEARCH_MAX_CHARS) or "(none)",
        recruiter_type=recruiter_type,
        recruiter_bias=recruiter_bias,
        revisions=revisions,
    )

    # Force temperature=0 on retry attempts so the corrective hint isn't
    # re-sampled away (matches pipeline.answer / pipeline.tailor Phase 9).
    temperature = 0.0 if revisions else prompt.temperature

    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks.get(
        "tailor", "qwen-custom:latest"
    )
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
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
    """Tolerant constructor. Accepts a dict with `question`+`beat` (the
    schema'd form), a dict with `answer`/`response`/`how_to_answer` for the
    beat (qwen drift), or a plain string (treated as the question with an
    empty beat — better than crashing)."""
    if isinstance(q, str):
        return LikelyQuestion(question=q, beat="")
    if not isinstance(q, dict):
        raise ValueError(f"likely_question must be dict or str, got {type(q).__name__}")
    question = q.get("question") or q.get("q") or ""
    beat = (
        q.get("beat")
        or q.get("answer")
        or q.get("response")
        or q.get("how_to_answer")
        or ""
    )
    return LikelyQuestion(question=str(question), beat=str(beat))


def _coerce_honest_gap(g: Any) -> HonestGap:
    """Same tolerance as `_coerce_likely_question` for the `honest_gaps`
    list. Accepts `{gap, reframe}` (schema), `{gap_description, response}`
    (qwen drift), or a plain string (treated as the gap with an empty
    reframe)."""
    if isinstance(g, str):
        return HonestGap(gap=g, reframe="")
    if not isinstance(g, dict):
        raise ValueError(f"honest_gap must be dict or str, got {type(g).__name__}")
    gap = g.get("gap") or g.get("gap_description") or g.get("weakness") or ""
    reframe = (
        g.get("reframe")
        or g.get("response")
        or g.get("how_to_reframe")
        or g.get("framing")
        or ""
    )
    return HonestGap(gap=str(gap), reframe=str(reframe))


# --- validation ---------------------------------------------------------------


def validate_prep_sections(
    sections: PrepDocSections,
    *,
    verified: dict[str, Any],
) -> list[str]:
    """Return a list of violation strings. Empty list = clean.

    Reuses `cover_validate` helpers on the concatenated free-form output
    and adds an identity-subset check on each anchor.
    """
    violations: list[str] = []
    free_text = _concat_freeform(sections)
    body_lower = _normalize(free_text)

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
    allowed = _verified_numbers(verified)
    scratch = _TIME_OF_DAY_RE.sub(" ", free_text)
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

    # Fabrication watchlist with negation-context suppression.
    verified_blob = _verified_skill_blob(verified)
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
    # storefront for Atelier Dacko" (shopify, storefront, atelier all trace).
    verified_blob_lower = verified_blob.lower()
    for anchor in sections.strongest_anchors:
        substantive = [
            t for t in re.findall(r"[a-z]+", anchor.lower()) if len(t) >= 5
        ]
        if not substantive:
            # Pure stop-word / numeric anchor — too vague, but don't reject;
            # the LLM may emit a project-style phrase ("Three phases over
            # two years") that's still a valid anchor.
            continue
        if any(tok in verified_blob_lower for tok in substantive):
            continue
        violations.append(f"unverified anchor: {anchor!r}")

    return violations


def _concat_freeform(sections: PrepDocSections) -> str:
    parts: list[str] = []
    parts.extend(sections.role_decode)
    parts.extend(sections.strongest_anchors)
    for q in sections.likely_questions:
        parts.append(q.question)
        parts.append(q.beat)
    parts.extend(sections.questions_to_ask)
    for g in sections.honest_gaps:
        parts.append(g.gap)
        parts.append(g.reframe)
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
        violations = validate_prep_sections(sections, verified=verified)
        if not violations:
            return sections, [], attempt
        last_sections = sections
        last_violations = violations
        revisions = _format_revision_hint(violations, attempt)
    assert last_sections is not None
    return last_sections, last_violations, attempts


def _format_revision_hint(violations: list[str], attempt: int) -> str:
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
    # the model explicitly that those numbers don't count as Casey's facts.
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
    return "\n".join(lines)


# --- markdown renderer --------------------------------------------------------


_STAGE_LABEL: dict[str, str] = {
    "screen": "Initial Screen",
    "assessment": "Skills Assessment",
    "hm": "Hiring Manager",
    "onsite": "Onsite / Final Round",
}


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
        parts.append(q.beat)
        parts.append("")

    parts.append("## Questions to ask back")
    parts.append("")
    for ask in sections.questions_to_ask:
        parts.append(f"- {ask}")
    parts.append("")

    parts.append("## Honest gaps (don't hide — reframe)")
    parts.append("")
    for g in sections.honest_gaps:
        parts.append(f"- **{g.gap}** — {g.reframe}")
    parts.append("")

    parts.append("## Pre-call checklist")
    parts.append("")
    parts.extend(
        [
            "- [ ] Skim the company homepage; note one product or initiative by name",
            "- [ ] Have Resume.docx and the tailored resume open in tabs",
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
                beat="Run without `--no-llm` to draft questions and beats.",
            )
        ],
        questions_to_ask=["_TODO_ questions to ask back_"],
        honest_gaps=[
            HonestGap(
                gap="_TODO_",
                reframe="Run without `--no-llm` to draft honest gap reframes.",
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
_SALARY_RE = re.compile(
    r"\$(\d{1,3}(?:[,.]\d{3})*(?:\.\d{1,2})?)\s*[–\-—to]+\s*"
    r"\$?(\d{1,3}(?:[,.]\d{3})*(?:\.\d{1,2})?)\s*"
    r"(USD|CAD)?\s*"
    r"(?:per\s+)?(hour|hr|year|yr|annum|annually)?",
    re.IGNORECASE,
)

# Conservative USD→CAD multiplier. Static value is fine here — this is a
# screen-call heads-up, not a contract negotiation.
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
    currency = (currency or "USD").upper()
    unit_norm = (unit or "year").lower()
    is_hourly = unit_norm in {"hour", "hr"}

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
    if is_hourly:
        lines.append(
            f"- Annualized FT: ~${annual_low:,.0f}–${annual_high:,.0f} {currency} "
            f"(~${cad_low:,.0f}–${cad_high:,.0f} CAD)"
        )
    elif currency == "USD":
        lines.append(f"- ~${cad_low:,.0f}–${cad_high:,.0f} CAD")
    lines.append(f"- Your stated range: **{applicant_range_cad}**")
    lines.append(
        "- Suggested screen phrasing: \"Your range looks in line with what "
        "I'm looking at. I'd want to confirm contract vs full-time structure "
        "and benefits before locking a specific number.\""
    )
    return "\n".join(lines)

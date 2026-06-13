"""Tests for the interview-prep pipeline.

Covers:
- Single-pass success (clean payload → 1 attempt).
- Retry-until-clean and fall-back after max attempts.
- Validator catches banned phrase / unverified anchor / unverified tech /
  unverified number.
- Skeleton-offline renderer fills the deterministic shell.
- Markdown renderer composes all expected sections.
- Comp section extraction handles USD/CAD hourly/annual formats.
- `apply --set-status interviewing` prints the prep-doc nudge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import (
    ApplicantProfile,
    Config,
    GatewayConfig,
    PathsConfig,
    PipelineConfig,
)
from jobhunt.pipeline import interview_prep as ip_mod
from jobhunt.pipeline.interview_prep import (
    HonestGap,
    LikelyQuestion,
    PrepContext,
    PrepDocSections,
    _coerce_honest_gap,
    _coerce_likely_question,
    _patch_prep_sections,
    build_interview_context,
    draft_prep_with_retry,
    extract_comp_section,
    has_blocking_prep_violations,
    render_prep_markdown,
    render_skeleton_offline,
    validate_prep_sections,
)

VERIFIED = {
    "summary": "Full-Stack Developer with 2+ years of professional client experience.",
    "work_history": [
        {
            "title": "Web Developer (Contract)",
            "employer": "Atelier Dacko",
            "dates": "2023 – Present",
            "bullets": [
                "Built 14+ page Shopify storefront with 200+ SKUs.",
                "Shipped an interactive ring builder app on the Shopify storefront.",
            ],
        }
    ],
    "skills_core": ["JavaScript", "TypeScript", "React", "Node.js"],
    "skills_cms": ["Shopify (Liquid, Custom Themes, Apps)", "Shopify App Development"],
    "skills_data_devops": ["GitHub Actions CI/CD", "Python"],
    "skills_ai": ["Ollama (Local LLM hosting)", "Prompt engineering"],
    "skills_familiar": ["Java"],
    "certifications": [],
    "education": [],
    "coursework_baseline": [],
}


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(json.dumps(VERIFIED))
    # Minimal prompt mirroring the real one's variables.
    (kb / "prompts" / "interview-prep.md").write_text(
        "---\n"
        "task: tailor\n"
        "temperature: 0.3\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {role_decode: {type: array}}\n"
        "---\n"
        "## SYSTEM\nDraft prep.\n"
        "## USER\n{verified_facts}\n{stage}\n{job_title}\n{job_company}\n"
        "{job_description}\n{audit_summary}\n{cover_summary}\n{research_blob}\n"
        "{revisions}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"tailor": "qwen-custom:latest"}),
        pipeline=PipelineConfig(cover_retry_attempts=3),
    )


def _ctx() -> PrepContext:
    return PrepContext(
        job_id="manual:test",
        job_title="Senior Shopify Developer",
        job_company="Acme Corp",
        job_description="We need someone with Shopify + Liquid experience.",
        job_url="https://acme.example.com/jobs/1",
        stage="agency",
    )


def _clean_payload() -> dict[str, Any]:
    return {
        "role_decode": [
            "Build content automation across Shopify and WordPress.",
            "Maintain QA gating before content reaches client sites.",
        ],
        "strongest_anchors": [
            "Built a 14+ page Shopify storefront for Atelier Dacko.",
            "Shipped a ring builder app on the Shopify storefront.",
        ],
        "likely_questions": [
            {
                "question": "Walk me through your background.",
                "beats": [
                    "Contractor doing Shopify migrations at Atelier Dacko.",
                    "AI tooling work with Ollama on the local LLM workflow.",
                    "Adjacent: HubSpot HubL theme work at the AI Agency.",
                ],
            }
        ],
        "questions_to_ask": [
            "What is the breakdown of client sites by CMS?",
        ],
        "honest_gaps": [
            {
                "gap": "Webflow",
                "reframes": [
                    "I haven't shipped Webflow specifically.",
                    "Closest verified bridge: I work daily in Shopify Liquid and HubSpot HubL.",
                ],
            }
        ],
    }


def _banned_payload() -> dict[str, Any]:
    p = _clean_payload()
    p["role_decode"][0] = "Spearheaded content automation across Shopify and WordPress."
    return p


def _fabrication_payload() -> dict[str, Any]:
    p = _clean_payload()
    p["strongest_anchors"].append("Built Kubernetes clusters for production deploys.")
    return p


# --- success / retry --------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_first_attempt_when_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _clean_payload()

    monkeypatch.setattr(ip_mod, "complete_json", fake_complete_json)
    sections, violations, attempts = await draft_prep_with_retry(
        _cfg(kb_dir), ctx=_ctx(), verified=VERIFIED, max_attempts=3
    )
    assert attempts == 1
    assert violations == []
    assert calls["n"] == 1
    assert any("Atelier Dacko" in a for a in sections.strongest_anchors)


@pytest.mark.asyncio
async def test_retries_until_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [_banned_payload(), _clean_payload()]

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(ip_mod, "complete_json", fake_complete_json)
    _, violations, attempts = await draft_prep_with_retry(
        _cfg(kb_dir), ctx=_ctx(), verified=VERIFIED, max_attempts=3
    )
    assert attempts == 2
    assert violations == []


@pytest.mark.asyncio
async def test_falls_back_after_max_attempts(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _banned_payload()

    monkeypatch.setattr(ip_mod, "complete_json", fake_complete_json)
    _, violations, attempts = await draft_prep_with_retry(
        _cfg(kb_dir), ctx=_ctx(), verified=VERIFIED, max_attempts=3
    )
    assert attempts == 3
    assert any("spearheaded" in v.lower() for v in violations)


@pytest.mark.asyncio
async def test_retry_uses_temperature_zero(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temps: list[float] = []
    responses = [_banned_payload(), _clean_payload()]

    async def fake_complete_json(**kwargs: Any) -> dict[str, Any]:
        temps.append(kwargs["temperature"])
        return responses.pop(0)

    monkeypatch.setattr(ip_mod, "complete_json", fake_complete_json)
    await draft_prep_with_retry(
        _cfg(kb_dir), ctx=_ctx(), verified=VERIFIED, max_attempts=3
    )
    assert temps[0] == 0.3   # frontmatter
    assert temps[1] == 0.0   # retry forced to 0


# --- validator --------------------------------------------------------------


def test_validator_passes_clean_sections() -> None:
    sections = PrepDocSections(
        role_decode=["Build content automation."],
        strongest_anchors=["Built a 14+ page Shopify storefront for Atelier Dacko."],
        likely_questions=[
            LikelyQuestion(
                "Walk me through your work?",
                [
                    "Atelier Dacko Shopify migration — 14+ pages, ring builder app.",
                    "Ollama local-LLM tooling sits alongside the CMS work.",
                ],
            )
        ],
        questions_to_ask=["What is the CMS breakdown?"],
        honest_gaps=[
            HonestGap(
                "Webflow",
                [
                    "I have not shipped Webflow in production.",
                    "Closest verified bridge: Shopify Liquid and HubSpot HubL templates daily.",
                ],
            )
        ],
    )
    assert validate_prep_sections(sections, verified=VERIFIED) == []


def test_validator_catches_banned_phrase() -> None:
    sections = PrepDocSections(
        role_decode=["Spearheaded content automation efforts."],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("spearheaded" in s.lower() for s in v)


def test_validator_catches_unverified_anchor() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=["Built Kubernetes clusters for production deploys."],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("unverified anchor" in s.lower() or "unverified tech" in s.lower() for s in v)


def test_validator_catches_unverified_number() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "What result should you mention?",
                ["Cut load time by 87%."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("unverified number" in s.lower() for s in v)


def test_validator_allows_jd_numbers_in_questions_to_ask() -> None:
    sections = PrepDocSections(
        role_decode=["Support automation across 100+ client websites."],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=["How many of the 100+ client sites are on Shopify?"],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert not any("unverified number" in s.lower() for s in v)


def test_validator_allows_configured_salary_numbers() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "What are your salary expectations?",
                ["My configured range is 50,000 - 90,000 CAD."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        allowed_numbers={"50,000", "90,000"},
    )
    assert not any("unverified number" in s.lower() for s in v)


def test_validator_catches_education_recap() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "How do you stay current?",
                ["I apply my coursework in Machine Learning."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("education recap" in s.lower() for s in v)


def test_validator_catches_unverified_immediate_start() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "When can you start?",
                ["I can start immediately with full work authorization."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("availability claim" in s.lower() for s in v)


def test_validator_catches_unverified_two_week_notice() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "What is your notice period?",
                ["I can start within two weeks depending on the offer."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("two-week notice" in s.lower() for s in v)


def test_validator_catches_blank_likely_question_beat() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[LikelyQuestion("How do you handle QA?", [])],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("missing an answer beat" in s.lower() for s in v)


def test_validator_catches_empty_string_beats_list() -> None:
    """List with only whitespace strings is still missing — defense against the
    coercion path that drops empties but a hand-built section that doesn't."""
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[LikelyQuestion("How do you handle QA?", ["   ", ""])],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("missing an answer beat" in s.lower() for s in v)


def test_validator_catches_blank_honest_gap_reframe() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[HonestGap("I haven't shipped Webflow.", [])],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("missing a reframe" in s.lower() for s in v)


def test_validator_catches_gap_reframe_without_verified_trace() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "I have not shipped Webflow specifically.",
                ["Closest verified bridge: I can learn the workflow quickly."],
            )
        ],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("reframe lacks verified trace" in s.lower() for s in v)


def test_validator_catches_gap_reframe_that_mirrors_unverified_jd_phrase() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "I have not shipped a dedicated SEO automation product.",
                [
                    (
                        "Closest verified bridge: I built automated content upload "
                        "systems across Shopify and WordPress."
                    ),
                ],
            )
        ],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        job_description=(
            "Design and build automated content upload systems across CMS "
            "platforms."
        ),
    )
    assert any("mirrors unverified jd phrase" in s.lower() for s in v)


def test_validator_catches_likely_beat_that_mirrors_unverified_jd_phrase() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "How do you handle QA?",
                ["I use GitHub Actions to ensure zero errors on client sites."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        job_description="Own quality end-to-end with zero errors on client sites.",
    )
    assert any("casey claim mirrors unverified jd phrase" in s.lower() for s in v)


def test_validator_passes_title_phrase_as_context() -> None:
    """A beat that references a JD title as third-party context (the role
    Casey would report to / coordinate with) must NOT fire the casey-claim
    mirror check. "Director of Search" is not something Casey claims to
    be — it's the interviewer's org structure."""
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "Who would you collaborate with?",
                [
                    "Day-to-day I'd coordinate with the Director of Search on roadmap priorities.",
                    "Closest verified context: I shipped Shopify Liquid templates daily at Atelier Dacko.",
                ],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        job_description="Report directly to the Director of Search.",
    )
    assert not any("director of search" in s.lower() for s in v), v


def test_validator_catches_title_phrase_with_ownership_claim() -> None:
    """If the bullet does claim the title with an ownership marker, the
    mirror check still fires. Casey applying for an IC role can't claim
    Director-of-Search experience absent that fact in verified."""
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "Walk me through your background.",
                ["I led search engineering as the Director of Search at a startup."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        job_description="Hiring a Director of Search.",
    )
    assert any("director of search" in s.lower() for s in v), v


def test_validator_catches_gap_reframe_with_scripts_api_jd_phrase() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "I have not used n8n.",
                [
                    (
                        "Closest verified bridge: I developed scripts and API "
                        "integrations that keep pipelines running smoothly."
                    ),
                ],
            )
        ],
    )
    v = validate_prep_sections(
        sections,
        verified=VERIFIED,
        job_description="Develop and maintain scripts and API integrations.",
    )
    assert any("casey claim mirrors unverified jd phrase" in s.lower() for s in v)


def test_blocking_violations_include_unusable_or_unsafe_output() -> None:
    assert has_blocking_prep_violations(["likely question 1 is missing an answer beat"])
    assert has_blocking_prep_violations(["honest gap 1 is missing a reframe"])
    assert has_blocking_prep_violations(["unverified number: '100'"])
    assert has_blocking_prep_violations(["interview-prep education recap: 'coursework'"])
    assert has_blocking_prep_violations(["unverified availability claim: immediate start"])
    assert has_blocking_prep_violations(["honest gap 1 reframe lacks verified trace"])
    assert has_blocking_prep_violations([
        "honest gap 1 reframe mirrors unverified JD phrase: 'full automation'"
    ])
    assert has_blocking_prep_violations([
        "casey claim mirrors unverified JD phrase: 'zero errors'"
    ])
    assert has_blocking_prep_violations(["unverified anchor: 'Kubernetes'"])
    assert not has_blocking_prep_violations(["banned phrase: 'spearheaded'"])


def test_patch_prep_sections_rewrites_ai_generated_content_pipeline() -> None:
    """Live-run repro from manual:89f772b92cf1: qwen kept dropping the JD
    phrase 'AI-generated content pipeline' into beats and gap reframes,
    blocking the artifact after 3 attempts. The patch tier converts it to
    Casey's verified surface (Ollama + Shopify/HubSpot)."""
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "What's your AI experience?",
                ["I built an AI-generated content pipeline for client sites."],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "I have not shipped a dedicated SEO automation product.",
                ["Closest verified bridge: my AI-generated content pipeline work."],
            )
        ],
    )
    patched = _patch_prep_sections(sections, cfg=Config())
    assert patched is not None
    blob = "\n".join(
        [b for q in patched.likely_questions for b in q.beats]
        + [r for g in patched.honest_gaps for r in g.reframes]
    ).lower()
    assert "ai-generated content pipeline" not in blob
    assert "ollama" in blob


def test_patch_prep_sections_rewrites_observed_bad_gap_patterns() -> None:
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[
            "I am authorized to work in Canada and can start within the timeline you need."
        ],
        likely_questions=[
            LikelyQuestion(
                "When can you start?",
                ["I can start immediately with full work authorization."],
            ),
            LikelyQuestion(
                "How do you stay current?",
                ["I apply my coursework in Machine Learning."],
            ),
        ],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "I have not shipped a full automation product.",
                ["Closest verified bridge: I have worked on full automation."],
            )
        ],
    )
    patched = _patch_prep_sections(sections, cfg=Config())
    assert patched is not None
    out = "\n".join(
        [b for q in patched.likely_questions for b in q.beats]
        + [r for g in patched.honest_gaps for r in g.reframes]
    ).lower()
    assert "start immediately" not in out
    assert "coursework" not in out
    assert "full automation" not in out
    assert "ollama" in out
    assert patched.strongest_anchors == []


# --- renderers --------------------------------------------------------------


def test_render_markdown_has_all_sections() -> None:
    sections = PrepDocSections(
        role_decode=["First bullet"],
        strongest_anchors=["Atelier Dacko anchor"],
        likely_questions=[LikelyQuestion("Q?", ["B1.", "B2."])],
        questions_to_ask=["Ask?"],
        honest_gaps=[HonestGap("G", ["R1.", "R2."])],
        model="qwen-custom:latest",
    )
    ctx = _ctx()
    ctx.comp_section = "- JD range: $20/hr"
    out = render_prep_markdown(sections, ctx=ctx)
    assert "Acme Corp" in out
    assert "Senior Shopify Developer" in out
    assert "Agency Screen" in out
    assert "## Comp heads-up" in out
    assert "## 2026 interview context" in out
    assert "## Role decode" in out
    assert "## Strongest anchors" in out
    assert "## Likely questions" in out
    assert "## Questions to ask back" in out
    assert "## Honest gaps" in out
    assert "## Pre-call checklist" in out
    assert "## After the call" in out
    assert "qwen-custom:latest" in out


def test_interview_context_is_stage_specific() -> None:
    agency = "\n".join(build_interview_context("agency")).lower()
    assessment = "\n".join(build_interview_context("assessment")).lower()
    assert "salary range" in agency
    assert "ai tools are allowed" in agency
    assert "job-simulation" in assessment
    assert agency != assessment


def test_render_markdown_skips_comp_when_empty() -> None:
    sections = PrepDocSections([], [], [], [], [])
    ctx = _ctx()
    out = render_prep_markdown(sections, ctx=ctx)
    assert "## Comp heads-up" not in out


def test_render_markdown_beats_become_nested_bullets() -> None:
    """Each Likely question renders its bolded question followed by one
    `- bullet` line per beat. Reframes render as `  - bullet` nested under
    the gap. This is the user-visible payoff of the May 2026 bullet bump."""
    sections = PrepDocSections(
        role_decode=[],
        strongest_anchors=[],
        likely_questions=[
            LikelyQuestion(
                "Walk me through your background.",
                [
                    "Atelier Dacko Shopify storefront — 14+ pages.",
                    "Ollama local-LLM tooling on the side.",
                    "Adjacent: HubSpot HubL theme work at the AI Agency.",
                ],
            )
        ],
        questions_to_ask=[],
        honest_gaps=[
            HonestGap(
                "Webflow",
                [
                    "I have not shipped Webflow in production.",
                    "Closest verified bridge: Shopify Liquid daily at Atelier Dacko.",
                ],
            )
        ],
    )
    out = render_prep_markdown(sections, ctx=_ctx())
    assert "**Walk me through your background.**" in out
    assert "- Atelier Dacko Shopify storefront — 14+ pages." in out
    assert "- Ollama local-LLM tooling on the side." in out
    # Reframes are nested (two-space indent) under the gap bullet.
    assert "- **Webflow**" in out
    assert "  - I have not shipped Webflow in production." in out
    assert "  - Closest verified bridge: Shopify Liquid daily at Atelier Dacko." in out


def test_coerce_likely_question_accepts_schema_shape() -> None:
    q = _coerce_likely_question({"question": "Q?", "beats": ["B1.", "B2.", "B3."]})
    assert q.question == "Q?"
    assert q.beats == ["B1.", "B2.", "B3."]


def test_coerce_likely_question_back_compat_single_beat_string() -> None:
    """Older payloads (and qwen drift) emit `beat` as a single string. Wrap
    it as a one-element list so existing data still decodes cleanly."""
    q = _coerce_likely_question({"question": "Q?", "beat": "B."})
    assert q.question == "Q?"
    assert q.beats == ["B."]


def test_coerce_likely_question_accepts_answer_alias() -> None:
    q = _coerce_likely_question({"question": "Q?", "answer": "A."})
    assert q.beats == ["A."]


def test_coerce_likely_question_accepts_plain_string() -> None:
    q = _coerce_likely_question("Walk me through your background.")
    assert q.question == "Walk me through your background."
    assert q.beats == []


def test_coerce_likely_question_drops_empty_bullets() -> None:
    q = _coerce_likely_question({"question": "Q?", "beats": ["B1.", "", "  ", "B2."]})
    assert q.beats == ["B1.", "B2."]


def test_coerce_honest_gap_accepts_aliases() -> None:
    g = _coerce_honest_gap({"gap_description": "Webflow", "response": "I do HubL."})
    assert g.gap == "Webflow"
    assert g.reframes == ["I do HubL."]


def test_coerce_honest_gap_back_compat_single_reframe_string() -> None:
    g = _coerce_honest_gap({"gap": "Webflow", "reframe": "I do HubL."})
    assert g.reframes == ["I do HubL."]


def test_coerce_honest_gap_accepts_list_shape() -> None:
    g = _coerce_honest_gap(
        {"gap": "Webflow", "reframes": ["No prod ship.", "Closest bridge: HubL."]}
    )
    assert g.reframes == ["No prod ship.", "Closest bridge: HubL."]


def test_skeleton_offline_returns_placeholders() -> None:
    out = render_skeleton_offline(_ctx())
    assert "_TODO_" in out
    assert "## Role decode" in out


# --- comp section -----------------------------------------------------------


def test_comp_section_hourly_defaults_cad() -> None:
    jd = "Compensation: $17.32 – $28.86 per hour."
    out = extract_comp_section(jd, "50,000 - 90,000 CAD")
    assert "17.32" in out
    assert "Annualized FT" in out
    assert "CAD" in out
    assert "USD" not in out
    assert "50,000 - 90,000 CAD" in out


def test_comp_section_an_hour_phrasing() -> None:
    # Indeed-style: "$18–$19 an hour", no currency. Must parse hourly CAD,
    # not USD/year (the Urban Customz misparse, 2026-06-12).
    jd = "Pay: $18.00-$19.00 an hour. Job Type: Full-time."
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "CAD/hour" in out
    assert "USD" not in out
    assert "/year" not in out
    # 18*2080=37,440 and 19*2080=39,520 annualized, no 1.37x inflation.
    assert "37,440" in out
    assert "39,520" in out


def test_comp_section_unitless_low_numbers_infer_hourly() -> None:
    jd = "Rate: $30 - $35"
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "CAD/hour" in out
    assert "62,400" in out  # 30 * 2080


def test_comp_section_unitless_large_numbers_stay_annual() -> None:
    jd = "Salary: $90,000 - $110,000"
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "CAD/year" in out
    assert "Annualized FT" not in out


def test_comp_section_usd_hourly_keeps_conversion() -> None:
    jd = "Compensation: $40 - $50 USD per hour."
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "USD/hour" in out
    assert "Annualized FT" in out
    # 40*2080=83,200 USD → 113,984 CAD at 1.37.
    assert "83,200" in out
    assert "113,984" in out


def test_comp_section_usd_annual() -> None:
    jd = "Salary: $80,000 - $120,000 USD per year."
    out = extract_comp_section(jd, "50,000 - 90,000 CAD")
    assert "80,000" in out
    assert "CAD" in out


def test_comp_section_below_range_warns() -> None:
    # The Urban Customz case: $18-19/hr CAD annualizes to ~$37k-$40k, half
    # the applicant floor. Must warn, never say "in line".
    jd = "Pay: $18.00-$19.00 an hour."
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "below your stated range" in out
    assert "in line" not in out


def test_comp_section_overlap_keeps_in_line_phrasing() -> None:
    jd = "Salary: $70,000 - $85,000 CAD per year."
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "Your range looks in line" in out


def test_comp_section_above_range() -> None:
    jd = "Salary: $120,000 - $150,000 CAD per year."
    out = extract_comp_section(jd, "60,000 - 90,000")
    assert "above your stated range" in out
    assert "in line" not in out


def test_comp_section_k_suffix_applicant_range() -> None:
    jd = "Pay: $18.00-$19.00 an hour."
    out = extract_comp_section(jd, "60k-90k CAD")
    assert "below your stated range" in out


def test_comp_section_unparseable_applicant_range_stays_neutral() -> None:
    jd = "Pay: $18.00-$19.00 an hour."
    out = extract_comp_section(jd, "negotiable")
    assert "Your range looks in line" in out


def test_comp_section_empty_on_no_match() -> None:
    assert extract_comp_section("No salary listed.", "50,000 - 90,000 CAD") == ""
    assert extract_comp_section("$50/hr", "") == ""
    assert extract_comp_section("", "50,000 - 90,000 CAD") == ""


# --- apply --set-status interviewing nudge ----------------------------------


def test_set_status_interviewing_prints_nudge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Smoke test that setting status to `interviewing` prints the prep-doc
    nudge. Doesn't exercise the real DB path — just monkeypatches the
    helpers to isolate the echo logic."""
    from jobhunt.commands import apply_cmd

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    cfg = Config(
        paths=PathsConfig(data_dir=data_dir, kb_dir=tmp_path / "kb"),
        applicant=ApplicantProfile(salary_expectation_cad="50,000 - 90,000 CAD"),
    )

    monkeypatch.setattr(apply_cmd, "load_config", lambda: cfg)

    # Stub DB to return an existing application row.
    class FakeConn:
        def execute(self, _q: str, _p: tuple[str, ...]) -> Any:
            class R:
                def __getitem__(self, k: str) -> str:
                    return {"id": 1, "status": "applied"}[k]
            return _Cursor(R())

        def __enter__(self) -> FakeConn:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def close(self) -> None:
            pass

    class _Cursor:
        def __init__(self, row: Any) -> None:
            self._row = row

        def fetchone(self) -> Any:
            return self._row

    monkeypatch.setattr(apply_cmd, "connect", lambda _path: FakeConn())
    monkeypatch.setattr(apply_cmd, "upsert_application", lambda *a, **kw: None)

    apply_cmd._run_lifecycle(
        "manual:abc123",
        set_status="interviewing",
        mark_response=None,
        mark_interview=None,
        set_outcome=None,
        recruiter_type=None,
    )
    out = capsys.readouterr().out
    assert "applied → interviewing" in out
    assert "draft prep doc: jobhunt interview-prep manual:abc123" in out


def test_set_status_applied_no_nudge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from jobhunt.commands import apply_cmd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg = Config(
        paths=PathsConfig(data_dir=data_dir, kb_dir=tmp_path / "kb"),
    )
    monkeypatch.setattr(apply_cmd, "load_config", lambda: cfg)

    class FakeConn:
        def execute(self, _q: str, _p: tuple[str, ...]) -> Any:
            class R:
                def __getitem__(self, k: str) -> str:
                    return {"id": 1, "status": "drafted"}[k]
            class _C:
                _row = R()
                def fetchone(self) -> Any:
                    return self._row
            return _C()
        def __enter__(self) -> FakeConn: return self
        def __exit__(self, *_: Any) -> None: pass
        def close(self) -> None: pass

    monkeypatch.setattr(apply_cmd, "connect", lambda _p: FakeConn())
    monkeypatch.setattr(apply_cmd, "upsert_application", lambda *a, **kw: None)

    apply_cmd._run_lifecycle(
        "manual:abc123",
        set_status="applied",
        mark_response=None,
        mark_interview=None,
        set_outcome=None,
        recruiter_type=None,
    )
    out = capsys.readouterr().out
    assert "draft prep doc" not in out

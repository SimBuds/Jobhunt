"""Score-time deterministic coverage clamp.

Closes the loophole where qwen3.5:9b returned `score=95` while listing must-haves
it hadn't actually matched in `matched_must_haves`. We re-partition the LLM's
must-have list against verified.json ourselves and cap the score to the band
the deterministic coverage justifies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import score as score_mod
from jobhunt.pipeline.score import (
    _clamp_by_coverage,
    _coverage_pct,
    _is_bogus_senior_decline,
    _verify_against_profile,
    score_job,
)


# --- pure-function tests ---------------------------------------------------


VERIFIED_BLOB = json.dumps(
    {
        "skills_core": [
            "JavaScript (ES6+)",
            "TypeScript",
            "React",
            "Next.js",
            "Node.js",
            "Shopify (Liquid, Custom Themes)",
        ],
        "skills_familiar": ["Python", "Java"],
        "ai_tooling": "Local LLM hosting via Ollama; prompt engineering for code generation.",
    }
)


def test_verify_credits_phrases_present_in_profile() -> None:
    matched, gaps = _verify_against_profile(
        ["TypeScript", "React"], ["Vue.js"], VERIFIED_BLOB
    )
    assert matched == ["TypeScript", "React"]
    assert gaps == ["Vue.js"]


def test_verify_demotes_llm_matched_when_not_in_profile() -> None:
    """The Pigment regression: model claimed 'Front-end frameworks' and 'AI/LLM
    tools' as matched, but 'Front-end frameworks' is not a phrase in the
    profile. Token-fallback in phrase_present means 'AI/LLM tools' DOES match
    via the `ai_tooling` blob entry — verify both behaviours."""
    matched, gaps = _verify_against_profile(
        ["Front-end frameworks", "AI/LLM tools"], [], VERIFIED_BLOB
    )
    # AI/LLM tools — tokens "ai", "llm", "tools" — "ai" is in "ai_tooling",
    # "llm" is in "local llm", but "tools" is not. So it falls into gaps.
    assert "Front-end frameworks" in gaps
    assert "AI/LLM tools" in gaps


def test_verify_dedupes_overlap_between_matched_and_gaps() -> None:
    matched, gaps = _verify_against_profile(["React"], ["React"], VERIFIED_BLOB)
    assert matched.count("React") + gaps.count("React") == 1


def test_coverage_pct_handles_empty() -> None:
    assert _coverage_pct([], []) == 100


def test_coverage_pct_rounds() -> None:
    assert _coverage_pct(["a"], ["b", "c"]) == 33  # 1/3


@pytest.mark.parametrize(
    "raw,coverage,expected",
    [
        (95, 100, 95),  # full coverage — keep
        (95, 80, 89),   # one missing — cap at 89
        (95, 67, 79),   # two missing of six — cap at 79 (Pigment scenario)
        (95, 50, 64),   # three+ missing — cap at 64
        (95, 0, 64),    # nothing matches — cap at 64
        (60, 100, 60),  # clamp never raises a score
        (60, 50, 60),   # raw already below cap — leave alone
        (89, 80, 89),   # at-the-cap — unchanged
    ],
)
def test_clamp_by_coverage(raw: int, coverage: int, expected: int) -> None:
    assert _clamp_by_coverage(raw, coverage) == expected


# --- end-to-end test (mocks complete_json, no Ollama call) -----------------


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "policies").mkdir()
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(VERIFIED_BLOB)
    (kb / "policies" / "tailoring-rules.md").write_text("policy text")
    # Minimal score prompt so load_prompt works.
    (kb / "prompts" / "score.md").write_text(
        "---\n"
        "task: score\n"
        "temperature: 0.0\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {score: {type: integer}}\n"
        "---\n"
        "## SYSTEM\nScore.\n## USER\n{{title}} {{description}}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"score": "qwen3.5:9b"}),
    )


def _job() -> Job:
    return Job(
        id="test:1",
        source="test",
        external_id="1",
        title="Front-end Engineer",
        description="React + TypeScript, AI/LLM tooling required.",
        company="Pigment",
    )


@pytest.mark.asyncio
async def test_score_job_clamps_when_llm_inflates(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the Pigment regression. LLM returns score=95 with two phrases
    in matched_must_haves that the verified profile does not actually back."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 95,
            "matched_must_haves": [
                "JavaScript (ES6+)",
                "TypeScript",
                "React",
                "Next.js",
                "Front-end frameworks",  # not in profile
                "AI/LLM tools",           # not fully in profile
            ],
            "gaps": [],
            "decline_reason": None,
            "ai_bonus_present": True,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # 4/6 matched = 67% coverage → cap at 79.
    assert result.score == 79
    assert "Front-end frameworks" in result.gaps
    assert "AI/LLM tools" in result.gaps
    assert "TypeScript" in result.matched_must_haves


# --- bogus-decline guard (May 2026) ---


def test_bogus_decline_nullifies_lead_seniority_on_plain_lead_title() -> None:
    """qwen3.5:9b manufactures 'Title implies Lead seniority' on IC Lead titles.
    The May 2026 prompt allows IC Lead/Staff/Principal titles — auto-decline
    only fires when JD names management responsibilities. The guard catches
    this when the reason has only seniority tokens and the title doesn't
    carry a hard people-management word."""
    assert _is_bogus_senior_decline(
        "Title implies Lead seniority mismatch", "Lead Software Engineer"
    )


def test_bogus_decline_nullifies_staff_principal_architect_too() -> None:
    """Extends the original Senior-only guard to Staff/Principal/Architect."""
    for reason in (
        "Staff seniority mismatch",
        "Principal-level seniority required",
        "Architect title indicates seniority gap",
    ):
        assert _is_bogus_senior_decline(reason, "Staff Engineer"), reason


def test_bogus_decline_keeps_decline_when_reason_cites_management() -> None:
    """If the reason explicitly mentions management/mentoring/direct reports,
    the decline is real and must be kept — Casey is not a people leader."""
    assert not _is_bogus_senior_decline(
        "Lead role requires managing 4+ direct reports", "Lead Software Engineer"
    )
    assert not _is_bogus_senior_decline(
        "Senior role with mentoring responsibilities", "Senior Software Engineer"
    )


def test_bogus_decline_keeps_decline_when_title_is_manager_director() -> None:
    """Plain Senior/Lead/Staff in the title is no longer a hard trigger, but
    Manager/Director/Head of/VP titles must still decline."""
    assert not _is_bogus_senior_decline("Senior seniority mismatch", "Engineering Manager")
    assert not _is_bogus_senior_decline("Lead seniority mismatch", "Director of Engineering")
    assert not _is_bogus_senior_decline("Staff seniority mismatch", "Head of Platform")


def test_bogus_decline_returns_false_when_no_seniority_tokens() -> None:
    """Guard is scoped to seniority-related declines only — other declines pass through."""
    assert not _is_bogus_senior_decline("Not in Toronto/GTA", "Software Engineer")
    assert not _is_bogus_senior_decline("Required Kubernetes (4+ years)", "DevOps Engineer")


# --- tiny-denominator clamp carve-out (May 2026) ---


@pytest.mark.asyncio
async def test_score_job_skips_clamp_on_tiny_denominator(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adzuna's ~500-char snippets often yield only 1-2 phrases. Clamping a
    1/1 or 0/1 coverage to cap-at-64 over-penalizes signal-poor postings —
    skip the clamp when matched + gaps < 3."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,
            "matched_must_haves": ["React"],
            "gaps": ["GraphQL"],  # 1 matched, 1 gap = 2 total < 3 threshold
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Without the carve-out this would clamp 78 → 64 (50% coverage).
    # With the carve-out, raw 78 stands.
    assert result.score == 78


@pytest.mark.asyncio
async def test_score_job_still_clamps_when_denominator_sufficient(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tiny-denominator carve-out only applies under 3 must-haves total.
    Three or more must-haves still get clamped — protects against the Pigment
    regression that originally motivated the clamp."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 95,
            "matched_must_haves": ["React"],
            "gaps": ["Vue.js", "Angular", "Svelte"],  # 1/4 = 25% coverage
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # 1/4 = 25% < 60% → cap at 64.
    assert result.score == 64


@pytest.mark.asyncio
async def test_score_job_keeps_score_when_coverage_full(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 95,
            "matched_must_haves": ["TypeScript", "React", "Next.js"],
            "gaps": [],
            "decline_reason": None,
            "ai_bonus_present": True,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    assert result.score == 95
    assert result.gaps == []

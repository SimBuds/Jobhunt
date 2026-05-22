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


# --- Junior-title override on Senior-band declines (2026-05-22) ---


@pytest.mark.asyncio
async def test_score_job_nullifies_senior_band_decline_for_junior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the title explicitly says Junior/Intermediate/Mid/Associate, a
    Senior-band decline reason emitted by qwen3.5:9b must be nullified —
    the title is the canonical band signal."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 60,
            "matched_must_haves": ["React"],
            "gaps": [".NET"],
            "decline_reason": "Senior-band title; candidate YoE under typical floor",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    job = Job(
        id="test:jr",
        source="test",
        external_id="jr",
        title="Junior Full Stack Developer (.NET / Cloud)",
        description="React + .NET. Junior role.",
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is None, result.decline_reason


@pytest.mark.asyncio
async def test_score_job_keeps_senior_band_decline_for_senior_title(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override is title-gated: a Senior-titled posting with a Senior-
    band decline reason must keep the decline."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 0,
            "matched_must_haves": [],
            "gaps": [],
            "decline_reason": "Senior-band title; candidate YoE under typical floor",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    job = Job(
        id="test:sr",
        source="test",
        external_id="sr",
        title="Senior Software Engineer",
        description="React + Node.",
        company="Acme",
    )
    result = await score_job(_cfg(kb_dir), job)
    assert result.decline_reason is not None
    assert "senior-band" in result.decline_reason.lower()


# --- Familiar-only-fit cap (May 2026, Phase 10.2) ---


@pytest.mark.asyncio
async def test_score_job_caps_familiar_only_fit(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 10.2 regression: when every matched must-have resolves to a
    Familiar-bucket skill (Java/Spring Boot, academic-only), the score must
    cap at <55 and set a decline reason. Avoids the Java-Developer-shipped-
    with-Familiar-only-section pattern."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,  # LLM was overly generous
            "matched_must_haves": ["Java", "Spring Boot"],
            "gaps": ["10+ years"],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    assert result.score <= 54, f"expected cap, got {result.score}"
    assert result.decline_reason is not None
    assert "familiar" in result.decline_reason.lower()


@pytest.mark.asyncio
async def test_score_job_does_not_cap_when_core_skill_also_matched(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When at least one matched must-have is a Core skill (TypeScript,
    React, etc.), the Familiar-only cap should NOT fire. Roles with mixed
    Core+Familiar matches are legitimate fits."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 78,
            "matched_must_haves": ["TypeScript", "Java"],  # mixed
            "gaps": ["Kubernetes"],
            "decline_reason": None,
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Phase 2.5 tiny-denominator carve-out applies (3 must-haves total), so
    # the clamp kicks in: 2/3 = 67% → cap at 79. Result stays at 78 since
    # raw was already at 78. Confirm no Familiar-only cap fires.
    assert result.score >= 64
    assert result.decline_reason is None


@pytest.mark.asyncio
async def test_score_job_does_not_cap_when_already_declined(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LLM already returned a decline_reason, the Familiar-only cap
    shouldn't overwrite it. Pre-existing decline survives."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return {
            "score": 0,
            "matched_must_haves": ["Java"],
            "gaps": [],
            "decline_reason": "Title is people-management (Engineering Manager)",
            "ai_bonus_present": False,
        }

    monkeypatch.setattr(score_mod, "complete_json", fake_complete_json)
    result = await score_job(_cfg(kb_dir), _job())
    # Pre-existing decline reason preserved.
    assert result.decline_reason == "Title is people-management (Engineering Manager)"


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

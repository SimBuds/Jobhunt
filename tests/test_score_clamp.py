"""Score-time deterministic coverage clamp.

Closes the loophole where qwen3:8b returned `score=95` while listing must-haves
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
        gateway=GatewayConfig(tasks={"score": "qwen3:8b"}),
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

"""Tests for the application-question answer pipeline.

Mirrors `tests/test_cover_retry.py`. Confirms:
- Single-pass success (clean payload → 1 attempt, empty violations).
- Retry-until-clean (dirty payload → clean payload across 2 attempts).
- Fall-back after max attempts (always-dirty payload → ships with violations).
- Validator coverage of the key rules: banned phrases, word cap, fabrication,
  recap suppression.
- Retry temperature drops to 0 on attempts 2+ (mirrors tailor Phase 9.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig, PipelineConfig
from jobhunt.pipeline import answer as answer_mod
from jobhunt.pipeline.answer import (
    Answer,
    validate_answer,
    write_answer_with_retry,
)

VERIFIED = {
    "summary": "Full-Stack Developer with 2+ years of professional client experience.",
    "work_history": [
        {
            "title": "Web Developer (Contract)",
            "employer": "Atelier Dacko",
            "dates": "2023 – Present",
            "bullets": [
                "Built 14+ page Shopify storefront with 200+ SKUs serving 500+ monthly visitors.",
            ],
        },
    ],
    "skills_core": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)"],
    "skills_data_devops": ["GitHub Actions CI/CD", "Python"],
    "skills_ai": ["Ollama (Local LLM hosting)", "Prompt engineering"],
    "skills_familiar": ["Java", "Spring Boot"],
    "certifications": [
        (
            "Contentful Certified Professional + Personalization Skill Badge, "
            "Contentful (October 2025)"
        ),
    ],
    "education": [],
    "coursework_baseline": [],
}


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(json.dumps(VERIFIED))
    (kb / "prompts" / "answer.md").write_text(
        "---\n"
        "task: answer\n"
        "temperature: 0.5\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {answer: {type: string}}\n"
        "---\n"
        "## SYSTEM\nDraft a response.\n"
        "## USER\n{verified_facts}\n{question}\n{jd_context}\n{max_words}\n{revisions}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"answer": "qwen-custom:latest"}),
        pipeline=PipelineConfig(cover_retry_attempts=3, answer_max_words=200),
    )


def _clean_payload() -> dict[str, Any]:
    return {
        "answer": (
            "I built a 14+ page Shopify storefront for a jewelry brand and "
            "shipped a ring builder that replaced manual quote requests. "
            "I owned scoping through deployment as the sole developer."
        )
    }


def _banned_payload() -> dict[str, Any]:
    """Contains 'leveraged' — on BANNED_PHRASES, no negation context."""
    return {
        "answer": (
            "I leveraged my Shopify expertise to build a 14+ page storefront "
            "for a jewelry brand. The ring builder replaced manual quote requests."
        )
    }


def _fabrication_payload() -> dict[str, Any]:
    """Claims Kubernetes (on _FABRICATION_WATCHLIST, not in VERIFIED)."""
    return {
        "answer": (
            "I shipped Shopify storefronts and a HubSpot theme, and I deployed "
            "them on Kubernetes for high availability in production."
        )
    }


# --- success-path tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_returns_first_attempt_when_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _clean_payload()

    monkeypatch.setattr(answer_mod, "complete_json", fake_complete_json)
    answer, violations, attempts = await write_answer_with_retry(
        _cfg(kb_dir),
        question="Tell me about a project you shipped.",
        verified=VERIFIED,
        max_words=200,
        max_attempts=3,
    )
    assert attempts == 1
    assert violations == []
    assert calls["n"] == 1
    assert "shopify" in answer.text.lower()


@pytest.mark.asyncio
async def test_retries_until_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attempt 1 leaks 'leveraged'; attempt 2 returns a clean payload."""
    responses = [_banned_payload(), _clean_payload()]

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(answer_mod, "complete_json", fake_complete_json)
    answer, violations, attempts = await write_answer_with_retry(
        _cfg(kb_dir),
        question="Tell me about a project you shipped.",
        verified=VERIFIED,
        max_words=200,
        max_attempts=3,
    )
    assert attempts == 2
    assert violations == []
    assert "leveraged" not in answer.text.lower()


@pytest.mark.asyncio
async def test_falls_back_after_max_attempts(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every attempt leaks a banned phrase, the loop must return the
    last attempt WITH violations rather than raising — the user can choose
    to ship-with-warnings or hand-edit (matches cover retry behaviour)."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _banned_payload()

    monkeypatch.setattr(answer_mod, "complete_json", fake_complete_json)
    answer, violations, attempts = await write_answer_with_retry(
        _cfg(kb_dir),
        question="Tell me about a project you shipped.",
        verified=VERIFIED,
        max_words=200,
        max_attempts=3,
    )
    assert attempts == 3
    assert violations  # non-empty
    assert any("leveraged" in v.lower() for v in violations)


@pytest.mark.asyncio
async def test_retry_uses_temperature_zero(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9 pattern carried into answers: retries force temp=0 so the
    model deterministically obeys the corrective hint instead of resampling
    the same banned phrase."""
    temps: list[float] = []
    responses = [_banned_payload(), _clean_payload()]

    async def fake_complete_json(**kwargs: Any) -> dict[str, Any]:
        temps.append(kwargs["temperature"])
        return responses.pop(0)

    monkeypatch.setattr(answer_mod, "complete_json", fake_complete_json)
    await write_answer_with_retry(
        _cfg(kb_dir),
        question="Tell me about a project.",
        verified=VERIFIED,
        max_words=200,
        max_attempts=3,
    )
    assert len(temps) == 2
    assert temps[0] == 0.5  # frontmatter
    assert temps[1] == 0.0  # retry forced to 0


# --- validator-only tests (no LLM mocking needed) -------------------------


def test_validator_passes_clean_answer() -> None:
    ans = Answer(
        text=(
            "I shipped a 14+ page Shopify storefront for a jewelry brand. "
            "The ring builder replaced manual quote requests."
        ),
        model="test",
    )
    assert validate_answer(ans, verified=VERIFIED, max_words=200) == []


def test_validator_flags_banned_phrase() -> None:
    ans = Answer(
        text="I'm passionate about building Shopify storefronts that ship clean code.",
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("passionate" in v.lower() for v in violations)


def test_validator_flags_overconfident_tone_phrase() -> None:
    ans = Answer(
        text=(
            "My Shopify migration proves this capability for client work, and "
            "that project maps directly to the role."
        ),
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("proves this capability" in v.lower() for v in violations)
    assert any("maps directly" in v.lower() for v in violations)


def test_validator_flags_unverified_tech() -> None:
    """Kubernetes is on _FABRICATION_WATCHLIST and not in verified.json."""
    ans = Answer(
        text=(
            "I deployed our Shopify backend on Kubernetes for high availability "
            "across production environments."
        ),
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("kubernetes" in v.lower() for v in violations)


def test_validator_word_cap() -> None:
    ans = Answer(text="word " * 60, model="test")
    violations = validate_answer(ans, verified=VERIFIED, max_words=50)
    assert any("60 words" in v or "max is 50" in v for v in violations)


def test_validator_flags_recap_token() -> None:
    """Answers must NOT recap the GBC diploma / coursework — that's resume
    material, not form-field material."""
    ans = Answer(
        text=(
            "I completed George Brown's Computer Programming diploma and shipped "
            "a Shopify storefront for a jewelry brand."
        ),
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("recaps resume material" in v.lower() for v in violations)


def test_validator_allows_negated_unverified_tech() -> None:
    """The same negation-context regex used by the cover validator should
    suppress fabrication flags when the model honestly disclaims the gap."""
    ans = Answer(
        text=(
            "I shipped Shopify and HubSpot themes in production. However, I "
            "haven't worked with Kubernetes yet — my deployments have been "
            "on AWS App Runner and managed Shopify infrastructure."
        ),
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert not any(
        "kubernetes" in v.lower() and "unverified" in v.lower()
        for v in violations
    )


def test_validator_flags_formulaic_opener() -> None:
    ans = Answer(
        text="I am excited to apply for this React role at your company.",
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("formulaic opener" in v.lower() or "i'm excited" in v.lower() for v in violations)


def test_validator_flags_exclamation_mark() -> None:
    ans = Answer(
        text="I shipped a Shopify storefront that serves 500+ visitors monthly!",
        model="test",
    )
    violations = validate_answer(ans, verified=VERIFIED, max_words=200)
    assert any("exclamation" in v.lower() for v in violations)

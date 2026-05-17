"""Auto-retry on `_enforce_no_fabrication` violations.

Mirrors `tests/test_cover_retry.py`. Closes the Redux-style failure where the
LLM mirrors a JD-mentioned skill into `skills_categories` that isn't in
`verified.json`, the fabrication check correctly rejects, and the whole job
gets skipped. The retry loop re-prompts with a "REMOVE X" hint until the
fabrication check passes, falling back to a re-raise after N tries (so an
unverified resume never ships).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import tailor as tailor_mod
from jobhunt.pipeline.tailor import (
    FabricationError,
    FabricationViolation,
    _format_tailor_revision_hint,
    tailor_resume_with_retry,
)

VERIFIED = {
    "summary": "Full-stack JavaScript developer with 2+ years of professional client work.",
    "contact_line": "Toronto, ON | x@y.com",
    "name": "Casey Hsu",
    "work_history": [
        {
            "title": "Web Developer (Contract)",
            "employer": "Atelier Dacko",
            "dates": "2023 – Present",
            "bullets": ["Built 14+ page Shopify storefront."],
        },
    ],
    "skills_core": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)", "Contentful (Certified Professional)"],
    "skills_data_devops": ["PostgreSQL", "GitHub Actions CI/CD"],
    "skills_ai": ["Ollama (Local LLM hosting)"],
    "skills_familiar": ["Java", "Spring Boot"],
    "certifications": [],
    "education": [],
    "coursework_baseline": [],
}


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "policies").mkdir()
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(json.dumps(VERIFIED))
    (kb / "policies" / "tailoring-rules.md").write_text("policy")
    (kb / "prompts" / "tailor.md").write_text(
        "---\n"
        "task: tailor\n"
        "temperature: 0.3\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {summary: {type: string}}\n"
        "---\n"
        "## SYSTEM\nTailor.\n## USER\n{title} {description}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"tailor": "qwen3.5:9b"}),
    )


def _job() -> Job:
    return Job(
        id="t:1",
        source="t",
        external_id="1",
        title="Senior React Developer",
        company="Acme",
        description="React + TypeScript + Redux required. Build the next phase of our app.",
    )


def _clean_payload() -> dict[str, Any]:
    """Tailored resume shape that passes _enforce_no_fabrication."""
    return {
        "summary": "Full-stack JavaScript developer with 2+ years of professional client work.",
        "skills_categories": [
            {"name": "Frontend Engineering", "items": ["React", "TypeScript", "JavaScript"]},
            {"name": "Familiar", "items": ["Java", "Spring Boot"]},
        ],
        "roles": [
            {
                "title": "Web Developer (Contract)",
                "employer": "Atelier Dacko",
                "dates": "2023 – Present",
                "bullets": [
                    "Built 14+ page Shopify storefront serving 500+ monthly visitors.",
                    "Shipped Stripe payments integration end to end.",
                ],
            },
        ],
        "certifications": [],
        "education": [],
        "coursework": [],
    }


def _redux_payload() -> dict[str, Any]:
    """Same shape but with an unverified 'Redux' claim in skills_categories.
    Verified.json has React but NOT Redux; the fabrication check must reject."""
    p = _clean_payload()
    p["skills_categories"][0]["items"] = ["React", "Redux", "TypeScript", "JavaScript"]
    return p


# --- success-path tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_returns_first_attempt_when_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _clean_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    tailored, violations, attempts = await tailor_resume_with_retry(
        _cfg(kb_dir), _job(), max_attempts=3
    )
    assert attempts == 1
    assert violations == []
    assert calls["n"] == 1
    # The clean payload has React but no Redux — confirm pipeline accepted it.
    flat = [item for c in tailored.skills_categories for item in c.items]
    assert "React" in flat
    assert "Redux" not in flat


@pytest.mark.asyncio
async def test_retries_until_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attempt 1 leaks Redux; attempt 2 returns a clean payload. Retry loop
    must surface the clean result with attempts=2 and no Redux in output."""
    responses = [_redux_payload(), _clean_payload()]

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    tailored, violations, attempts = await tailor_resume_with_retry(
        _cfg(kb_dir), _job(), max_attempts=3
    )
    assert attempts == 2
    assert violations == []
    flat = [item for c in tailored.skills_categories for item in c.items]
    assert "Redux" not in flat


@pytest.mark.asyncio
async def test_falls_back_after_max_attempts(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every attempt leaks Redux, the loop must re-raise rather than
    ship a fabricated resume. apply_cmd's `except JobHuntError` will then
    surface the failure and skip the job — matches today's UX exactly."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _redux_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    with pytest.raises(FabricationError) as exc_info:
        await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3)
    # The retry layer never weakens the check.
    assert any(v.kind == "unverified-skill" for v in exc_info.value.violations)
    assert "Redux" in str(exc_info.value)


@pytest.mark.asyncio
async def test_retry_attempts_use_temperature_zero(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9 fix: at the frontmatter default temp=0.3, qwen kept producing
    Redux even after the corrective hint. Dropping to temp=0 on retries makes
    the second attempt deterministic enough to obey the 'REMOVE Redux' hint.
    First attempt still uses the frontmatter temperature (typically 0.3)."""
    temps: list[float] = []
    responses = [_redux_payload(), _clean_payload()]

    async def fake_complete_json(**kwargs: Any) -> dict[str, Any]:
        temps.append(kwargs["temperature"])
        return responses.pop(0)

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3)
    assert len(temps) == 2
    # First attempt: frontmatter temperature (whatever the prompt declares).
    # Second attempt: forced to 0.0 because the retry passes a non-empty
    # revisions hint.
    assert temps[1] == 0.0


@pytest.mark.asyncio
async def test_first_attempt_uses_prompt_temperature(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First attempt should use the frontmatter temperature, not 0.0 — the
    retry temperature drop is exclusive to retry paths."""
    temps: list[float] = []

    async def fake_complete_json(**kwargs: Any) -> dict[str, Any]:
        temps.append(kwargs["temperature"])
        return _clean_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3)
    assert len(temps) == 1
    # The fixture's prompt file declares `temperature: 0.3`. Confirm the
    # first attempt passes that through unchanged.
    assert temps[0] == 0.3


# --- hint-formatter tests --------------------------------------------------


def test_format_revision_hint_unverified_skill() -> None:
    """Per-kind hint text guides the model to the correct corrective rule."""
    hint = _format_tailor_revision_hint(
        [FabricationViolation("unverified-skill", "Redux")], attempt=1
    )
    assert "'Redux'" in hint
    assert "NOT in verified.json" in hint
    assert "REMOVE it entirely" in hint
    assert "retry 2" in hint


def test_format_revision_hint_familiar_promoted() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("familiar-promoted", "Java")], attempt=1
    )
    assert "'Java'" in hint
    assert "Familiar" in hint
    assert "category named exactly 'Familiar'" in hint


def test_format_revision_hint_lists_each_violation() -> None:
    """Multi-violation aggregation — confirm both violations are named."""
    hint = _format_tailor_revision_hint(
        [
            FabricationViolation("unverified-skill", "Redux"),
            FabricationViolation("familiar-promoted", "Java"),
        ],
        attempt=1,
    )
    assert "'Redux'" in hint
    assert "'Java'" in hint
    # Two bullets, both surfaced.
    assert hint.count("- ") >= 2


def test_format_revision_hint_role_divergence() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("role-divergence", "extra=[('Fake Corp', '2025')]")],
        attempt=1,
    )
    assert "roles diverged" in hint.lower()
    assert "every verified role" in hint


def test_format_revision_hint_summary_seniority() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("summary-seniority", "senior")], attempt=1
    )
    assert "seniority token" in hint
    assert "'senior'" in hint


# --- backward compatibility ------------------------------------------------


def test_fabrication_error_subclasses_pipeline_error() -> None:
    """Existing audit + apply callers `except PipelineError` — confirm
    FabricationError still satisfies that catch."""
    from jobhunt.errors import PipelineError

    e = FabricationError(
        [FabricationViolation("unverified-skill", "X")],
        "skill not in verified facts: 'X'",
    )
    assert isinstance(e, PipelineError)
    assert "not in verified facts" in str(e)

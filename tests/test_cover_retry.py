"""Auto-retry on cover-letter validator violations.

Closes the Pigment-style failure where the cover letter shipped with banned
phrases ('aligns with', 'direct match', etc.) and the audit verdict 'revise'
left Casey to hand-edit the .docx. The retry loop re-prompts with a hint
naming the violations until the validator returns clean, falling back to the
last attempt after N tries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import cover as cover_mod
from jobhunt.pipeline.cover import _format_revision_hint, write_cover_with_retry

VERIFIED = {
    "summary": "Web developer.",
    "work_history": [{"employer": "X", "dates": "2024", "bullets": ["Built 14+ page Shopify site."]}],
    "certifications": ["Contentful Certified Professional (October 2025)"],
    "education": [],
    "coursework_baseline": [],
}


@pytest.fixture
def kb_dir(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(json.dumps(VERIFIED))
    (kb / "prompts" / "cover.md").write_text(
        "---\n"
        "task: cover\n"
        "temperature: 0.7\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {body: {type: array}}\n"
        "---\n"
        "## SYSTEM\nWrite a cover letter.\n## USER\n{title} {description} {revisions}\n"
    )
    return kb


def _cfg(kb: Path) -> Config:
    return Config(
        paths=PathsConfig(kb_dir=kb),
        gateway=GatewayConfig(tasks={"cover": "qwen3.5:9b"}),
    )


def _job() -> Job:
    return Job(
        id="t:1",
        source="t",
        external_id="1",
        title="Engineer",
        company="Acme",
        description="React + TypeScript role.",
    )


def _clean_payload() -> dict[str, Any]:
    """3-paragraph cover that names the company, no banned phrases."""
    return {
        "salutation": "Dear Hiring Team,",
        "body": [
            "I built a 14+ page Shopify storefront for a jewelry brand and shipped a "
            "ring builder for Acme-style configuration workflows.",
            "The migration moved a WordPress site to Shopify across three phases over "
            "two years; I owned scoping through deployment as the sole developer.",
            "Happy to walk through what I shipped if useful.",
        ],
        "sign_off": "Best,\nCasey Hsu",
    }


def _dirty_payload() -> dict[str, Any]:
    """Same body but with a banned phrase ('aligns with') in paragraph 2."""
    p = _clean_payload()
    p["body"][1] = p["body"][1] + " This aligns with Acme's mission."
    return p


@pytest.mark.asyncio
async def test_returns_first_attempt_when_clean(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def fake(**_: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _clean_payload()

    monkeypatch.setattr(cover_mod, "complete_json", fake)
    cover, violations, attempts = await write_cover_with_retry(
        _cfg(kb_dir), _job(), verified=VERIFIED, company="Acme",
        max_words=280, max_attempts=3,
    )
    assert calls["n"] == 1
    assert attempts == 1
    assert violations == []
    assert cover.body[0].startswith("I built a 14+")


@pytest.mark.asyncio
async def test_retries_until_clean(kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [_dirty_payload(), _clean_payload()]
    calls = {"n": 0}
    seen_users: list[str] = []

    async def fake(**kwargs: Any) -> dict[str, Any]:
        seen_users.append(kwargs["user"])
        n = calls["n"]
        calls["n"] += 1
        return payloads[n]

    monkeypatch.setattr(cover_mod, "complete_json", fake)
    cover, violations, attempts = await write_cover_with_retry(
        _cfg(kb_dir), _job(), verified=VERIFIED, company="Acme",
        max_words=280, max_attempts=3,
    )
    assert calls["n"] == 2
    assert attempts == 2
    assert violations == []
    # Second prompt should include the revision hint naming the banned phrase.
    assert "aligns with" in seen_users[1]
    assert "rejected by the validator" in seen_users[1].lower()
    # Returned cover is the clean one (no 'aligns with').
    assert "aligns with" not in " ".join(cover.body).lower()


@pytest.mark.asyncio
async def test_falls_back_after_max_attempts(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def fake(**_: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _dirty_payload()

    monkeypatch.setattr(cover_mod, "complete_json", fake)
    cover, violations, attempts = await write_cover_with_retry(
        _cfg(kb_dir), _job(), verified=VERIFIED, company="Acme",
        max_words=280, max_attempts=3,
    )
    assert calls["n"] == 3
    assert attempts == 3
    assert any("aligns with" in v for v in violations)
    # The last attempt is still returned so apply has something to render.
    assert cover.body  # not raised, not empty


def test_format_revision_hint_lists_each_violation() -> None:
    hint = _format_revision_hint(
        ["banned phrase: 'aligns with'", "banned phrase: 'direct match'"], attempt=1
    )
    assert "aligns with" in hint
    assert "direct match" in hint
    assert "retry 2" in hint

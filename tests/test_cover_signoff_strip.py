"""Strip a stray trailing sign-off line from cover-letter body paragraphs.

qwen3.5:9b habitually appends 'Best,' or 'Best,\\nCasey Hsu' to the last body
paragraph despite the schema's sign_off field being rendered separately. The
deterministic strip prevents the duplicate-sign-off violation from firing in
the validator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import cover as cover_mod
from jobhunt.pipeline.cover import _strip_trailing_signoff


def test_strips_best_with_name() -> None:
    para = "I am ready to discuss this role. Best,\nCasey Hsu"
    assert _strip_trailing_signoff(para, "Casey Hsu") == "I am ready to discuss this role."


def test_strips_best_with_non_casey_name() -> None:
    # Genericization guard: the name is data-driven, not hard-coded to Casey.
    para = "I would welcome a conversation. Best,\nJane Q. Public"
    assert (
        _strip_trailing_signoff(para, "Jane Q. Public")
        == "I would welcome a conversation."
    )


def test_strips_best_alone() -> None:
    para = "Looking forward to hearing from you. Best,"
    assert _strip_trailing_signoff(para) == "Looking forward to hearing from you."


def test_strips_regards_kind_sincerely() -> None:
    for closer in ("Regards,", "Sincerely,", "Cheers,", "Kind regards,"):
        para = f"This closes the para. {closer}"
        assert _strip_trailing_signoff(para) == "This closes the para."


def test_strips_signoff_on_new_line() -> None:
    para = "This closes the para.\n\nBest,\nCasey Hsu"
    assert _strip_trailing_signoff(para, "Casey Hsu") == "This closes the para."


def test_passthrough_when_no_signoff() -> None:
    para = "I look forward to discussing this role with your team."
    assert _strip_trailing_signoff(para) == para


def test_does_not_strip_inline_best() -> None:
    # "best" used as an adjective mid-sentence must not be touched.
    para = "I do my best work on greenfield e-commerce builds."
    assert _strip_trailing_signoff(para) == para


# --- Phase A11a: sign_off is identity, taken from the verified profile ---


def _kb_with_name(tmp_path: Path, name: str) -> Path:
    kb = tmp_path / "kb"
    (kb / "profile").mkdir(parents=True)
    (kb / "prompts").mkdir()
    (kb / "profile" / "verified.json").write_text(
        json.dumps({"name": name, "summary": "Web developer.", "work_history": []}),
        encoding="utf-8",
    )
    (kb / "prompts" / "cover.md").write_text(
        "---\n"
        "task: cover\n"
        "temperature: 0.7\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {body: {type: array}}\n"
        "---\n"
        "## SYSTEM\nWrite a cover letter.\n## USER\n{title} {description} {revisions}\n",
        encoding="utf-8",
    )
    return kb


@pytest.mark.asyncio
async def test_signoff_uses_verified_name_not_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for A11a: `kb/prompts/cover.md` used to hard-code one
    candidate's name in the sign-off rule, and the pipeline only used the
    verified name as a *fallback* — so a different user's letter shipped
    signed with the wrong person's name."""
    kb = _kb_with_name(tmp_path, "Jane Dev")

    async def fake(**_: object) -> dict[str, object]:
        # The model echoes the old hard-coded name; it must not win.
        return {
            "salutation": "Dear Hiring Team,",
            "body": ["I built a Shopify storefront and shipped a configurator."],
            "sign_off": "Best,\nCasey Hsu",
        }

    monkeypatch.setattr(cover_mod, "complete_json", fake)
    cover = await cover_mod.write_cover(
        Config(
            paths=PathsConfig(kb_dir=kb),
            gateway=GatewayConfig(tasks={"cover": "qwen3.5:9b"}),
        ),
        Job(
            id="t:1", source="t", external_id="1", title="Engineer",
            company="Acme", description="React role.",
        ),
    )
    assert cover.sign_off == "Best,\nJane Dev"
    assert "Casey" not in cover.sign_off


@pytest.mark.asyncio
async def test_signoff_falls_back_to_model_when_profile_has_no_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = _kb_with_name(tmp_path, "")

    async def fake(**_: object) -> dict[str, object]:
        return {
            "salutation": "Dear Hiring Team,",
            "body": ["A paragraph."],
            "sign_off": "Best,\nSomeone",
        }

    monkeypatch.setattr(cover_mod, "complete_json", fake)
    cover = await cover_mod.write_cover(
        Config(
            paths=PathsConfig(kb_dir=kb),
            gateway=GatewayConfig(tasks={"cover": "qwen3.5:9b"}),
        ),
        Job(
            id="t:1", source="t", external_id="1", title="Engineer",
            company="Acme", description="React role.",
        ),
    )
    assert cover.sign_off == "Best,\nSomeone"

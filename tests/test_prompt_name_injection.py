"""Phase A13b — prompts name the configured applicant, not a hard-coded person.

Background (IMPLEMENT.md A13): replacing the name with "the candidate" was
measured on the golden set and cost 17 points of keyword coverage — the model
grounds better on a concrete referent. So the fix is to inject the real name
rather than abstract it away. These tests pin the two halves of that:
the gateway can substitute into a system prompt, and the name is derived from
the verified profile in the exact surface form the prompts were written around.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.errors import PipelineError
from jobhunt.gateway import load_prompt
from jobhunt.pipeline.tailor import _candidate_name


def _prompt(tmp_path: Path, system: str) -> object:
    kb = tmp_path / "prompts"
    kb.mkdir(parents=True)
    (kb / "t.md").write_text(
        "---\n"
        "task: tailor\n"
        "temperature: 0.3\n"
        "schema:\n"
        "  type: object\n"
        "  properties: {summary: {type: string}}\n"
        "---\n"
        f"## SYSTEM\n{system}\n## USER\n{{title}}\n",
        encoding="utf-8",
    )
    return load_prompt(tmp_path, "t")


def test_render_system_substitutes_the_name(tmp_path: Path) -> None:
    p = _prompt(tmp_path, "You are tailoring {candidate_name}'s resume.")
    assert p.render_system(candidate_name="Jane") == "You are tailoring Jane's resume."  # type: ignore[attr-defined]


def test_render_system_is_a_noop_without_placeholders(tmp_path: Path) -> None:
    """Safe to call on every prompt, including ones that take no variables."""
    p = _prompt(tmp_path, "Plain instructions, no variables.")
    assert p.render_system(candidate_name="Jane") == "Plain instructions, no variables."  # type: ignore[attr-defined]


def test_render_system_reports_a_missing_variable(tmp_path: Path) -> None:
    p = _prompt(tmp_path, "Tailoring {candidate_name} for {unknown_var}.")
    with pytest.raises(PipelineError, match="missing variable"):
        p.render_system(candidate_name="Jane")  # type: ignore[attr-defined]


def test_candidate_name_downcases_an_all_caps_header() -> None:
    """Resume headers are usually all-caps; shouting inside a prompt is not
    what the hard-coded version said, and A13b must reproduce it exactly."""
    assert _candidate_name({"name": "CASEY HSU"}) == "Casey"


def test_candidate_name_takes_the_first_name_as_written() -> None:
    assert _candidate_name({"name": "Jane Dev"}) == "Jane"
    assert _candidate_name({"name": "Jane"}) == "Jane"


def test_candidate_name_falls_back_when_the_profile_has_no_name() -> None:
    for profile in ({}, {"name": ""}, {"name": "   "}, {"name": None}):
        assert _candidate_name(profile) == "the candidate"


def test_live_tailor_prompt_carries_the_placeholder() -> None:
    """Regression: the shipped prompt must not re-acquire a hard-coded name."""
    text = Path("kb/prompts/tailor.md").read_text(encoding="utf-8")
    assert "{candidate_name}" in text
    assert "Casey" not in text

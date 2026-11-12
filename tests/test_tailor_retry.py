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
            "employer": "Harbour Loom",
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
        paths=PathsConfig(kb_dir=kb, profile_dir=kb / "profile"),
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
                "employer": "Harbour Loom",
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


def _role_payload() -> dict[str, Any]:
    """A violation the strip in `_tailor_once` cannot repair (a changed
    employer), so it still forces a retry. The retry-loop tests below used
    `_redux_payload` until 2026-11-12, when an unverified skill became
    strippable on the first attempt (Phase T2)."""
    p = _clean_payload()
    p["roles"][0]["employer"] = "Invented Corp"
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
async def test_retries_until_clean(kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Attempt 1 leaks Redux; attempt 2 returns a clean payload. Retry loop
    must surface the clean result with attempts=2 and no Redux in output."""
    responses = [_role_payload(), _clean_payload()]

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
async def test_falls_back_after_max_attempts(kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When every attempt changes an employer, the loop must re-raise rather
    than ship a fabricated resume. (Until 2026-11-12 the trigger was a leaked
    unverified skill, which Phase T2 now strips instead.) apply_cmd's
    `except JobHuntError` will then surface the failure and skip the job —
    matches today's UX exactly."""

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _role_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    with pytest.raises(FabricationError) as exc_info:
        await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3)
    # The retry layer never weakens the check.
    assert any(v.kind == "role-divergence" for v in exc_info.value.violations)
    assert "Invented Corp" in str(exc_info.value)


@pytest.mark.asyncio
async def test_retry_attempts_use_temperature_zero(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9 fix: at the frontmatter default temp=0.3, qwen kept producing
    Redux even after the corrective hint. Dropping to temp=0 on retries makes
    the second attempt deterministic enough to obey the 'REMOVE Redux' hint.
    First attempt still uses the frontmatter temperature (typically 0.3)."""
    temps: list[float] = []
    responses = [_role_payload(), _clean_payload()]

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


def test_check_summary_accepts_grounded_jd_label() -> None:
    # "JavaScript" is a verified Core skill, so the JD-lane label passes.
    tailor_mod._check_summary(
        "Full-stack JavaScript developer with 2+ years of client work.", VERIFIED
    )


def test_check_summary_accepts_verbatim_verified_label() -> None:
    tailor_mod._check_summary(VERIFIED["summary"], VERIFIED)


def test_check_summary_rejects_ungrounded_label_token() -> None:
    with pytest.raises(FabricationError) as exc:
        tailor_mod._check_summary("Django developer with 2+ years of client work.", VERIFIED)
    v = exc.value.violations[0]
    assert v.kind == "summary-label-ungrounded"
    assert v.detail == "django"


def test_check_summary_rejects_familiar_grounded_label() -> None:
    # Java is Familiar-only in VERIFIED; an identity label may not rest on it.
    with pytest.raises(FabricationError) as exc:
        tailor_mod._check_summary("Java developer with 2+ years of client work.", VERIFIED)
    assert exc.value.violations[0].kind == "summary-label-ungrounded"
    assert exc.value.violations[0].detail == "java"


def test_check_summary_skips_label_check_when_no_label_isolates() -> None:
    # No "with"/comma/paren delimiter and more than 6 words: the guard
    # declines to guess where the label ends rather than risk a false reject.
    tailor_mod._check_summary(
        "Builder of unusual storefront experiences across many client industries daily.",
        VERIFIED,
    )


def test_format_revision_hint_summary_label_ungrounded() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("summary-label-ungrounded", "django")], attempt=1
    )
    assert "'django'" in hint
    assert "role label" in hint
    assert "verified" in hint


def test_format_revision_hint_summary_seniority() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("summary-seniority", "senior")],
        attempt=1,
        candidate_name="Jane Q. Public",
    )
    assert "seniority token" in hint
    assert "'senior'" in hint
    assert "Jane Q. Public's verified profile" in hint
    assert "Casey" not in hint


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


# --- Phase 6c.3 (2026-11-10): one bullet per project -------------------------
#
# Projects render as one line, and the audit reads the tailored data, so the
# tailor keeps only the lead bullet: the page and the audit see the same text.


@pytest.mark.asyncio
async def test_tailor_keeps_only_each_projects_first_bullet(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = {
        **VERIFIED,
        "projects": [
            {
                "name": "Jobhunt",
                "url": "github.com/SimBuds/Jobhunt",
                "stack": ["Python"],
                "bullets": ["a", "b"],
            },
            {"name": "Empty", "url": "example.com", "stack": [], "bullets": []},
        ],
    }
    (kb_dir / "profile" / "verified.json").write_text(json.dumps(verified))
    payload = _clean_payload()
    payload["projects"] = [
        {
            "name": "Jobhunt",
            "url": "github.com/SimBuds/Jobhunt",
            "stack": ["Python"],
            "bullets": ["Scores postings with a local LLM.", "Fills forms with Playwright."],
        },
        {"name": "Empty", "url": "example.com", "stack": [], "bullets": []},
    ]

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    tailored, _, _ = await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=1)
    assert [p.bullets for p in tailored.projects] == [["Scores postings with a local LLM."], []]


# --- Phase D7 (2026-11-11): each rejected attempt is reported as it happens --
#
# The loop was silent: up to three rejected attempts at 30 to 60 seconds each
# printed nothing until it resolved, which read as a hang.


def _recorder() -> tuple[list[tuple[int, int, list[str]]], Any]:
    calls: list[tuple[int, int, list[str]]] = []

    def on_retry(attempt: int, total: int, violations: list[FabricationViolation]) -> None:
        calls.append((attempt, total, [v.kind for v in violations]))

    return calls, on_retry


@pytest.mark.asyncio
async def test_on_retry_reports_a_rejected_attempt_before_the_next(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [_role_payload(), _clean_payload()]

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    calls, on_retry = _recorder()
    await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3, on_retry=on_retry)
    assert calls == [(1, 3, ["role-divergence"])]


@pytest.mark.asyncio
async def test_on_retry_is_quiet_on_a_clean_first_attempt(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _clean_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    calls, on_retry = _recorder()
    await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3, on_retry=on_retry)
    assert calls == []


@pytest.mark.asyncio
async def test_on_retry_skips_the_final_attempt_which_raises(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return _role_payload()

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    calls, on_retry = _recorder()
    with pytest.raises(FabricationError):
        await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=3, on_retry=on_retry)
    assert [(a, t) for a, t, _ in calls] == [(1, 3), (2, 3)]


def test_retry_line_names_the_first_violation() -> None:
    from jobhunt.pipeline.tailor import retry_line

    v = [FabricationViolation("unverified-skill", "robots.txt")]
    assert retry_line(1, 3, v) == (
        "    tailor: attempt 1 of 3 rejected (unverified skill: 'robots.txt'), retrying"
    )


# --- Phase T2 (2026-11-12): strip an unverified skill, then re-check ---------
#
# Brafton's tailor run failed 3 times on "Gutenberg blocks": the hint said to
# remove it, and `lite` re-added it each time because the posting named it 3
# times. Removing a skill can never fabricate, so `_tailor_once` now strips
# the named items and runs the full guard again. Any other violation, or one
# that surfaces after the strip, still raises.


async def _once(kb_dir: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]):
    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    return await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=1)


def _items(tailored: Any) -> list[str]:
    return [i for c in tailored.skills_categories for i in c.items]


@pytest.mark.asyncio
async def test_unverified_skill_is_stripped_on_first_attempt(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tailored, violations, attempts = await _once(kb_dir, monkeypatch, _redux_payload())
    assert attempts == 1
    assert violations == []
    assert "Redux" not in _items(tailored)
    assert "React" in _items(tailored)


@pytest.mark.asyncio
async def test_category_left_empty_by_the_strip_is_dropped(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _clean_payload()
    p["skills_categories"].insert(1, {"name": "CMS", "items": ["Gutenberg blocks", "Drupal"]})
    tailored, _, _ = await _once(kb_dir, monkeypatch, p)
    assert "CMS" not in [c.name for c in tailored.skills_categories]
    assert all(c.items for c in tailored.skills_categories)


@pytest.mark.asyncio
async def test_summary_violation_is_not_stripped(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _redux_payload()
    p["summary"] = "Expert full-stack JavaScript developer with 2+ years of client work."
    with pytest.raises(FabricationError) as exc_info:
        await _once(kb_dir, monkeypatch, p)
    assert all(v.kind != "unverified-skill" for v in exc_info.value.violations)


@pytest.mark.asyncio
async def test_violation_surfacing_after_the_strip_still_raises(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _redux_payload()
    p["skills_categories"][0]["items"].append("Java")  # Familiar in a Core category
    with pytest.raises(FabricationError) as exc_info:
        await _once(kb_dir, monkeypatch, p)
    assert [v.kind for v in exc_info.value.violations] == ["familiar-promoted"]


# --- Phase U1 (2026-11-12): the summary may not claim the posting's gaps ----
#
# The summary check only knew watchlisted tech, so anything not listed in
# advance could be claimed (Brafton's "Gutenberg blocks" shipped this way).
# The score already knows which of the posting's asks the profile lacks.
# Those gaps are exactly what a summary is tempted to echo. Cases use
# Sitecore, which is not watchlisted, so only the new check can catch it.


async def _summary(
    kb_dir: Path, monkeypatch: pytest.MonkeyPatch, summary: str, gaps: list[str] | None
):
    p = _clean_payload()
    p["summary"] = summary

    async def fake_complete_json(**_: Any) -> dict[str, Any]:
        return p

    monkeypatch.setattr(tailor_mod, "complete_json", fake_complete_json)
    kwargs = {} if gaps is None else {"gaps": gaps}
    return await tailor_resume_with_retry(_cfg(kb_dir), _job(), max_attempts=1, **kwargs)


BASE = "Full-stack JavaScript developer with 2+ years of professional client work."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("summary", "gap"),
    [
        (BASE + " Builds Sitecore sites.", "Sitecore"),
        (BASE + " Ships Sitecore XM Cloud sites.", "Sitecore XM Cloud (headless or MVC)"),
    ],
)
async def test_summary_naming_a_gap_raises(kb_dir, monkeypatch, summary, gap) -> None:
    with pytest.raises(FabricationError) as e:
        await _summary(kb_dir, monkeypatch, summary, [gap])
    assert [(v.kind, v.detail) for v in e.value.violations] == [("summary-unverified-tech", gap)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("summary", "gaps"),
    [
        # A disclaimer is not a claim.
        (BASE + " However, I haven't worked with Sitecore.", ["Sitecore"]),
        # Gaps under 3 chars are skipped: "Go" would match the English word.
        (BASE + " Ready to go further.", ["Go"]),
        # Whole word: a "Java" gap is not found inside "JavaScript".
        (BASE, ["Java"]),
        # A skill the profile has is not a gap.
        (BASE + " Builds React interfaces.", ["Sitecore"]),
    ],
)
async def test_summary_without_a_gap_claim_passes(kb_dir, monkeypatch, summary, gaps) -> None:
    tailored, _, _ = await _summary(kb_dir, monkeypatch, summary, gaps)
    assert tailored.summary == summary


@pytest.mark.asyncio
async def test_no_gaps_passed_is_unchanged(kb_dir, monkeypatch) -> None:
    summary = BASE + " Builds Sitecore sites."
    tailored, _, _ = await _summary(kb_dir, monkeypatch, summary, None)
    assert tailored.summary == summary


def test_summary_gap_has_a_revision_hint() -> None:
    hint = _format_tailor_revision_hint(
        [FabricationViolation("summary-unverified-tech", "Sitecore")], 2
    )
    assert "Sitecore" in hint

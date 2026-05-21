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
    draft_prep_with_retry,
    extract_comp_section,
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
        stage="screen",
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
                "beat": "Contractor doing Shopify migrations and AI tooling at Atelier Dacko.",
            }
        ],
        "questions_to_ask": [
            "What is the breakdown of client sites by CMS?",
        ],
        "honest_gaps": [
            {
                "gap": "Webflow",
                "reframe": (
                    "I haven't shipped Webflow, but I work daily in "
                    "Shopify Liquid and HubSpot HubL."
                ),
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
            LikelyQuestion("Walk me through your work?", "Atelier Dacko Shopify migration.")
        ],
        questions_to_ask=["What is the CMS breakdown?"],
        honest_gaps=[
            HonestGap("Webflow", "I work in Shopify Liquid and HubSpot HubL.")
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
        role_decode=["Cut load time by 87%."],   # 87 not in verified
        strongest_anchors=[],
        likely_questions=[],
        questions_to_ask=[],
        honest_gaps=[],
    )
    v = validate_prep_sections(sections, verified=VERIFIED)
    assert any("unverified number" in s.lower() for s in v)


# --- renderers --------------------------------------------------------------


def test_render_markdown_has_all_sections() -> None:
    sections = PrepDocSections(
        role_decode=["First bullet"],
        strongest_anchors=["Atelier Dacko anchor"],
        likely_questions=[LikelyQuestion("Q?", "B.")],
        questions_to_ask=["Ask?"],
        honest_gaps=[HonestGap("G", "R")],
        model="qwen-custom:latest",
    )
    ctx = _ctx()
    ctx.comp_section = "- JD range: $20/hr"
    out = render_prep_markdown(sections, ctx=ctx)
    assert "Acme Corp" in out
    assert "Senior Shopify Developer" in out
    assert "Initial Screen" in out
    assert "## Comp heads-up" in out
    assert "## Role decode" in out
    assert "## Strongest anchors" in out
    assert "## Likely questions" in out
    assert "## Questions to ask back" in out
    assert "## Honest gaps" in out
    assert "## Pre-call checklist" in out
    assert "## After the call" in out
    assert "qwen-custom:latest" in out


def test_render_markdown_skips_comp_when_empty() -> None:
    sections = PrepDocSections([], [], [], [], [])
    ctx = _ctx()
    out = render_prep_markdown(sections, ctx=ctx)
    assert "## Comp heads-up" not in out


def test_coerce_likely_question_accepts_schema_shape() -> None:
    q = _coerce_likely_question({"question": "Q?", "beat": "B."})
    assert q.question == "Q?" and q.beat == "B."


def test_coerce_likely_question_accepts_answer_alias() -> None:
    q = _coerce_likely_question({"question": "Q?", "answer": "A."})
    assert q.beat == "A."


def test_coerce_likely_question_accepts_plain_string() -> None:
    q = _coerce_likely_question("Walk me through your background.")
    assert q.question == "Walk me through your background."
    assert q.beat == ""


def test_coerce_honest_gap_accepts_aliases() -> None:
    g = _coerce_honest_gap({"gap_description": "Webflow", "response": "I do HubL."})
    assert g.gap == "Webflow"
    assert g.reframe == "I do HubL."


def test_skeleton_offline_returns_placeholders() -> None:
    out = render_skeleton_offline(_ctx())
    assert "_TODO_" in out
    assert "## Role decode" in out


# --- comp section -----------------------------------------------------------


def test_comp_section_usd_hourly() -> None:
    jd = "Compensation: $17.32 – $28.86 per hour."
    out = extract_comp_section(jd, "50,000 - 90,000 CAD")
    assert "17.32" in out
    assert "Annualized FT" in out
    assert "CAD" in out
    assert "50,000 - 90,000 CAD" in out


def test_comp_section_usd_annual() -> None:
    jd = "Salary: $80,000 - $120,000 USD per year."
    out = extract_comp_section(jd, "50,000 - 90,000 CAD")
    assert "80,000" in out
    assert "CAD" in out


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

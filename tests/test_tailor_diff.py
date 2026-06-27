"""Phase 3 tests — tailor diff artifact."""

from __future__ import annotations

from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume, TailoredRole
from jobhunt.pipeline.tailor_diff import build_tailor_diff

VERIFIED = {
    "summary": "Full-stack dev.",
    "skills_core": ["JavaScript (ES6+)", "TypeScript", "React", "Node.js"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)"],
    "skills_data_devops": ["PostgreSQL", "Docker", "Python"],
    "skills_ai": [
        (
            "Ollama (Local LLM hosting), GPU optimization (cache, flash "
            "attention), Claude Code CLI, OpenAI Codex"
        )
    ],
    "skills_familiar": ["Java", "Spring Boot"],
    "work_history": [
        {
            "title": "Web Developer (Contract)",
            "employer": "Atelier Dacko",
            "dates": "2023 – Present",
            "bullets": [
                "Built 14+ page Shopify storefront.",
                "Shipped ring builder app.",
                "Integrated Stripe payments.",
            ],
        }
    ],
    "coursework_baseline": ["Machine Learning", "Data Structures & Algorithms"],
}


def _score() -> ScoreResult:
    return ScoreResult(
        score=78,
        matched_must_haves=["TypeScript", "React"],
        gaps=["Vue"],
        decline_reason=None,
        ai_bonus_present=False,
        model="test",
    )


def _tailored(
    *,
    lead_name: str = "Frontend",
    lead_items: list[str] | None = None,
    bullets: list[str] | None = None,
    coursework: list[str] | None = None,
) -> TailoredResume:
    return TailoredResume(
        summary="React + TypeScript developer for Toronto frontend roles.",
        skills_categories=[
            TailoredCategory(lead_name, lead_items or ["TypeScript", "React", "Node.js"]),
            TailoredCategory("Data", ["PostgreSQL", "Docker"]),
            TailoredCategory("Familiar", ["Java", "Spring Boot"]),
        ],
        roles=[
            TailoredRole(
                title="Web Developer (Contract)",
                employer="Atelier Dacko",
                dates="2023 – Present",
                bullets=bullets or [
                    "Built 14+ page Shopify storefront.",
                    "Shipped ring builder app.",
                ],
            )
        ],
        certifications=[],
        education=[],
        coursework=coursework or ["Machine Learning"],
        model="qwen-custom:latest",
    )


def test_diff_header_includes_job_metadata() -> None:
    out = build_tailor_diff(
        verified=VERIFIED, tailored=_tailored(), score=_score(),
        job_title="Frontend Dev", job_company="Acme",
    )
    assert "# Tailor diff" in out
    assert "Frontend Dev @ Acme" in out
    assert "model: qwen-custom:latest" in out


def test_diff_marks_lead_category() -> None:
    out = build_tailor_diff(
        verified=VERIFIED, tailored=_tailored(lead_name="Frontend Engineering"),
        score=_score(),
    )
    assert "**Lead category:** `Frontend Engineering`" in out
    assert "LEAD `Frontend Engineering`" in out


def test_diff_flags_promoted_skills() -> None:
    """Node.js is verified under Core but the tailored lead is named
    'Frontend' — should annotate as promoted from Core."""
    out = build_tailor_diff(
        verified=VERIFIED, tailored=_tailored(), score=_score(),
    )
    # Node.js was placed in 'Frontend' but origin is 'Core' → promoted note
    assert "`Node.js` (promoted from `Core`)" in out


def test_diff_flags_unknown_skill() -> None:
    out = build_tailor_diff(
        verified=VERIFIED,
        tailored=_tailored(lead_items=["TypeScript", "Bogus Skill"]),
        score=_score(),
    )
    assert "not found in verified" in out


def test_diff_marks_verbatim_vs_reworded_bullets() -> None:
    out = build_tailor_diff(
        verified=VERIFIED,
        tailored=_tailored(bullets=[
            "Built 14+ page Shopify storefront.",            # verbatim
            "Reworded: shipped a configurator on Shopify.",  # reworded
        ]),
        score=_score(),
    )
    assert "*(verbatim)*" in out
    assert "*(reworded)*" in out


def test_diff_detects_reordered_bullet() -> None:
    out = build_tailor_diff(
        verified=VERIFIED,
        tailored=_tailored(bullets=[
            "Shipped ring builder app.",            # was index 1, now index 0
            "Built 14+ page Shopify storefront.",  # was index 0, now index 1
        ]),
        score=_score(),
    )
    assert "verbatim + reordered" in out


def test_diff_surfaces_coursework_changes() -> None:
    out = build_tailor_diff(
        verified=VERIFIED,
        tailored=_tailored(coursework=["Machine Learning", "DevOps"]),
        score=_score(),
    )
    assert "1 baseline course(s) kept" in out
    assert "1 additional course(s) surfaced: DevOps" in out


def test_diff_score_summary_includes_matched_and_gaps() -> None:
    out = build_tailor_diff(
        verified=VERIFIED, tailored=_tailored(), score=_score(),
    )
    assert "score=78" in out
    assert "TypeScript, React" in out
    assert "Vue" in out


def test_diff_handles_missing_score() -> None:
    out = build_tailor_diff(
        verified=VERIFIED, tailored=_tailored(), score=None,
    )
    assert "(no score on file)" in out


def test_diff_handles_role_not_in_baseline() -> None:
    """Defensive — should never happen post-fabrication-check, but the
    diff should not crash if it does."""
    t = _tailored()
    t.roles.append(TailoredRole(
        title="Imaginary",
        employer="Made Up Co",
        dates="2099",
        bullets=["never happened"],
    ))
    out = build_tailor_diff(verified=VERIFIED, tailored=t, score=_score())
    assert "role not in baseline" in out


def test_diff_handles_ai_line_split_into_subskills() -> None:
    """The AI & Tooling row in verified.json is one prose string with
    comma-joined sub-skills. The diff's normalizer should split it so
    individual tools (Claude Code CLI) resolve when surfaced as discrete
    items in the tailored output."""
    out = build_tailor_diff(
        verified=VERIFIED,
        tailored=_tailored(lead_items=["Claude Code CLI", "Ollama"]),
        score=_score(),
    )
    assert "not found in verified" not in out

"""JD-relevance bullet ordering (`_order_bullets_by_jd`).

Regression: `_shrink_to_one_page` preserves each role's LEAD bullet and pops
from the end, so which bullet survives a squeezed page is decided entirely by
the order the model emitted. Measured 2026-09-22 on the AI-automation lane: the
model put a Shopify storefront bullet ahead of the LLM content pipeline bullet
for Atelier Dacko, the ladder trimmed to one bullet per role, and the lane's own
evidence was the thing that got cut.

Ordering by how many JD-named verified skills each bullet carries makes the
survivor the relevant one. It only permutes bullets, so no claim changes.
"""

from __future__ import annotations

from jobhunt.models import Job
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredResume,
    TailoredRole,
    _order_bullets_by_jd,
)

VERIFIED = {
    "skills_core": ["JavaScript (ES6+)", "React (Redux, Native)", "Python"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)", "WordPress"],
    "skills_ai": ["Claude API", "llama.cpp (llama-server)", "prompt engineering"],
    "skills_data_devops": ["Docker", "PostgreSQL"],
    "skills_projects": ["Playwright"],
}

AI_JD = (
    "You will build agentic tooling on the Claude API, run local inference with "
    "llama.cpp, and apply prompt engineering to production pipelines in Python."
)
PIPELINE_BULLET = (
    "Built a human-in-the-loop LLM content pipeline on the Claude API with "
    "prompt engineering, in Python."
)
STOREFRONT_BULLET = (
    "Sole developer for a 16-page Shopify storefront on a customized theme, "
    "replacing a WordPress site."
)


def _resume(roles: list[TailoredRole]) -> TailoredResume:
    return TailoredResume(
        summary="Developer.",
        skills_categories=[TailoredCategory("Core", ["Python"])],
        roles=roles,
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def _job(description: str, title: str = "AI Automation Developer") -> Job:
    return Job(
        id="test:1", source="test", external_id="1",
        title=title, company="Acme", description=description,
    )


def test_the_jd_relevant_bullet_leads_after_reordering() -> None:
    """The AI-lane scenario: the storefront bullet was emitted first, the
    pipeline bullet carries the JD's skills, so it moves to the lead."""
    role = TailoredRole("Dev", "Atelier Dacko", "2023 – Present",
                        [STOREFRONT_BULLET, PIPELINE_BULLET])
    resume = _resume([role])
    _order_bullets_by_jd(resume, _job(AI_JD), VERIFIED)
    assert resume.roles[0].bullets[0] == PIPELINE_BULLET


def test_the_surviving_bullet_is_the_relevant_one_after_the_ladder() -> None:
    """End to end against the real trim: ordering plus a one-bullet squeeze
    leaves the pipeline bullet, which is what the lane render lost."""
    from jobhunt.pipeline.tailor import _try_drop_weakest_bullet

    role = TailoredRole("Dev", "Atelier Dacko", "2023 – Present",
                        [STOREFRONT_BULLET, PIPELINE_BULLET])
    resume = _resume([role])
    _order_bullets_by_jd(resume, _job(AI_JD), VERIFIED)
    assert _try_drop_weakest_bullet(resume) is True
    assert resume.roles[0].bullets == [PIPELINE_BULLET]


def test_ties_keep_the_models_original_order() -> None:
    first = "Shipped a React interface in JavaScript."
    second = "Built a React dashboard in JavaScript."
    role = TailoredRole("Dev", "Acme", "2023 – Present", [first, second])
    resume = _resume([role])
    _order_bullets_by_jd(resume, _job("We need React and JavaScript."), VERIFIED)
    assert resume.roles[0].bullets == [first, second]


def test_single_bullet_and_unmatched_jd_are_left_alone() -> None:
    single = TailoredRole("Dev", "Acme", "2023 – Present", [STOREFRONT_BULLET])
    unmatched = TailoredRole("Chef", "JOEY", "2015 – 2025",
                             ["Led kitchen teams.", "Ran food-cost budgeting."])
    resume = _resume([single, unmatched])
    _order_bullets_by_jd(resume, _job("Embedded firmware in Rust and C."), VERIFIED)
    assert resume.roles[0].bullets == [STOREFRONT_BULLET]
    assert resume.roles[1].bullets == ["Led kitchen teams.", "Ran food-cost budgeting."]


def test_prose_bullet_outranks_a_keyword_bullet_from_another_lane() -> None:
    """The live failure, pinned. Ranking by verified skills named in the bullet
    promoted this SEO bullet on the AI lane, because it spells out `JSON-LD
    structured data` (an AI-brief nice-to-have) while the pipeline bullet
    describes its work in prose and names no verified skill at all. Measured
    2026-09-23 against the real verified bullets."""
    seo_bullet = (
        "Implemented title tags, meta descriptions, heading structure, and "
        "JSON-LD structured data in the storefront's Liquid templates."
    )
    prose_pipeline_bullet = (
        "Built a human-in-the-loop LLM content pipeline that drafts product "
        "descriptions and briefs for editorial review, then audits published "
        "output against target keywords."
    )
    jd = (
        "Build LLM automation: agents, prompt pipelines with deterministic "
        "validation, and human-in-the-loop content review that audits output."
    )
    role = TailoredRole("Dev", "Atelier Dacko", "2023 – Present",
                        [seo_bullet, prose_pipeline_bullet])
    resume = _resume([role])
    _order_bullets_by_jd(resume, _job(jd), VERIFIED)
    assert resume.roles[0].bullets[0] == prose_pipeline_bullet


def test_reordering_never_adds_drops_or_edits_a_bullet() -> None:
    bullets = [STOREFRONT_BULLET, PIPELINE_BULLET, "Ran QA in Docker with Playwright."]
    resume = _resume([TailoredRole("Dev", "Acme", "2023 – Present", list(bullets))])
    _order_bullets_by_jd(resume, _job(AI_JD), VERIFIED)
    assert sorted(resume.roles[0].bullets) == sorted(bullets)

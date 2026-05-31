"""JD-required-skill backfill (`_ensure_jd_required_skills`).

Regression: the tailor LLM reorganizes verified skills into JD-relevant
categories and sometimes drops infra/cloud/tooling skills that don't fit them —
even when the JD requires them. Observed 2026-05-31: the shyftlabs JD required
Git/AWS/Azure (all in verified.skills_data_devops) but the tailor folded that
bucket into a 'Backend & APIs' category and dropped them, sinking keyword
coverage to 62%. The deterministic backfill re-adds any verified skill the JD
names but the tailored output omits — honest by construction (verified-only).
"""

from __future__ import annotations

from jobhunt.models import Job
from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredResume,
    TailoredRole,
    _ensure_jd_required_skills,
)

VERIFIED = {
    "skills_core": ["JavaScript (ES6+)", "TypeScript", "React (Redux, Native)", "Node.js"],
    "skills_cms": ["Shopify (Liquid, Custom Themes)"],
    "skills_data_devops": ["MongoDB", "PostgreSQL", "Docker", "Git", "AWS", "Azure"],
    "skills_ai": ["Ollama (Local LLM hosting)"],
    "skills_familiar": ["Java", "Angular"],
}


def _resume(skills_categories) -> TailoredResume:
    return TailoredResume(
        summary="Full-stack developer.",
        skills_categories=skills_categories,
        roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["Built things"])],
        certifications=[],
        education=[],
        coursework=[],
        model="test",
    )


def _job(description: str, title: str = "Software Developer") -> Job:
    return Job(
        id="test:1", source="test", external_id="1",
        title=title, company="ShyftLabs", description=description,
    )


def _all_skill_items(resume: TailoredResume) -> set[str]:
    return {it.lower() for cat in resume.skills_categories for it in cat.items}


def test_backfills_jd_required_skills_the_tailor_dropped() -> None:
    """The shyftlabs scenario: JD names Git/AWS/Azure, tailored output dropped
    them (kept only MongoDB/Docker from the same bucket). They get re-added."""
    resume = _resume([
        TailoredCategory("Frontend & React", ["React", "TypeScript"]),
        TailoredCategory("Backend & APIs", ["Node.js", "MongoDB", "Docker"]),
        TailoredCategory("Familiar", ["Java", "Angular"]),
    ])
    job = _job("We use Git, AWS and Azure for our cloud-native backend.")
    _ensure_jd_required_skills(resume, VERIFIED, job)
    items = _all_skill_items(resume)
    assert {"git", "aws", "azure"} <= items


def test_backfill_places_skill_in_best_sibling_category() -> None:
    """AWS (data_devops) lands in the category holding its bucket siblings
    (MongoDB/Docker), not the Frontend category."""
    backend = TailoredCategory("Backend & APIs", ["Node.js", "MongoDB", "Docker"])
    resume = _resume([
        TailoredCategory("Frontend & React", ["React", "TypeScript"]),
        backend,
        TailoredCategory("Familiar", ["Java"]),
    ])
    _ensure_jd_required_skills(resume, VERIFIED, _job("Cloud on AWS."))
    assert "AWS" in backend.items


def test_does_not_add_skill_the_jd_never_names() -> None:
    """Azure is verified but the JD doesn't mention it — don't pad the resume."""
    resume = _resume([
        TailoredCategory("Backend & APIs", ["Node.js", "Docker"]),
        TailoredCategory("Familiar", ["Java"]),
    ])
    _ensure_jd_required_skills(resume, VERIFIED, _job("We use Git only."))
    items = _all_skill_items(resume)
    assert "git" in items
    assert "azure" not in items


def test_does_not_duplicate_already_present_skill() -> None:
    """AWS already in the resume → not added a second time."""
    backend = TailoredCategory("Backend & APIs", ["Node.js", "AWS"])
    resume = _resume([backend, TailoredCategory("Familiar", ["Java"])])
    _ensure_jd_required_skills(resume, VERIFIED, _job("Cloud on AWS."))
    assert [it.lower() for it in backend.items].count("aws") == 1


def test_paren_skill_present_in_clean_form_not_re_added() -> None:
    """Verified 'React (Redux, Native)' is satisfied by a resume rendering plain
    'React' — the paren-stripped core match prevents a redundant re-add."""
    resume = _resume([
        TailoredCategory("Frontend & React", ["React", "TypeScript"]),
        TailoredCategory("Familiar", ["Java"]),
    ])
    _ensure_jd_required_skills(resume, VERIFIED, _job("React and Redux required."))
    items = [it.lower() for cat in resume.skills_categories for it in cat.items]
    assert "react (redux, native)" not in items  # not re-added in verbose form


def test_present_in_a_bullet_counts_as_covered() -> None:
    """A JD skill already named in a role bullet isn't re-added to the skills
    list (mirrors how audit.keyword_coverage flattens the whole resume)."""
    resume = _resume([
        TailoredCategory("Backend & APIs", ["Node.js", "Docker"]),
        TailoredCategory("Familiar", ["Java"]),
    ])
    resume.roles[0].bullets = ["Deployed services to AWS with Docker"]
    _ensure_jd_required_skills(resume, VERIFIED, _job("Cloud on AWS."))
    assert "aws" not in _all_skill_items(resume)  # already covered by the bullet

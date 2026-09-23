"""Education backfill (`_ensure_education`).

Regression: `kb/prompts/tailor.md` rule 5 requires exactly one education entry,
but the model can silently return `education: []` and fold the credential into
the summary instead. Observed 2026-09-22 on the AI-automation lane
(`data/resumes/tailored-ai-automation.json` shipped `education: []`), and two
of the three lane renders reached the .docx with no degree line at all.

The backfill mirrors `_ensure_jd_required_skills`: deterministic, and honest by
construction because it only ever copies a line already in `verified.json`. The
honours clause is dropped from the copy, because `render_docx` composes its own
"Dean's List (all terms). Coursework: ..." paragraph and `_dedupe_education`
strips any education line naming Dean's List for exactly that reason.
"""

from __future__ import annotations

from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredResume,
    TailoredRole,
    _dedupe_education,
    _ensure_education,
)

DIPLOMA = (
    "Advanced Diploma, Computer Programming & Analysis | George Brown Polytechnic "
    "| Sep 2021 – Apr 2024 | Dean’s List, all terms"
)
VERIFIED = {
    "education": [
        DIPLOMA,
        "Coursework: Machine Learning, Data Structures & Algorithms",
        "Capstone: Restaurant inventory management system.",
    ]
}


def _resume(education: list[str]) -> TailoredResume:
    return TailoredResume(
        summary="Full-stack developer.",
        skills_categories=[TailoredCategory("Core", ["React"])],
        roles=[TailoredRole("Dev", "Acme", "2023 – Present", ["Built things"])],
        certifications=[],
        education=education,
        coursework=[],
        model="test",
    )


def test_backfills_the_credential_when_the_model_returned_none() -> None:
    """The lane scenario: `education: []` gets the verified diploma back."""
    resume = _resume([])
    _ensure_education(resume, VERIFIED)
    assert len(resume.education) == 1
    assert resume.education[0].startswith("Advanced Diploma, Computer Programming")
    assert "George Brown Polytechnic" in resume.education[0]


def test_backfilled_line_survives_the_dedupe_pass() -> None:
    """The honours clause is dropped, so `_dedupe_education` cannot strip the
    line back out and leave the resume degreeless again."""
    resume = _resume([])
    _ensure_education(resume, VERIFIED)
    _dedupe_education(resume)
    assert len(resume.education) == 1
    assert "dean" not in resume.education[0].lower()


def test_leaves_a_model_supplied_entry_untouched() -> None:
    model_line = (
        "Advanced Diploma, Computer Programming & Analysis (Diploma), "
        "George Brown, Toronto (Apr 2024)"
    )
    resume = _resume([model_line])
    _ensure_education(resume, VERIFIED)
    assert resume.education == [model_line]


def test_no_verified_education_is_not_an_error() -> None:
    for verified in ({}, {"education": []}, {"education": [""]}):
        resume = _resume([])
        _ensure_education(resume, verified)
        assert resume.education == []


def test_only_the_credential_entry_is_used_not_coursework_or_capstone() -> None:
    """`verified.education` also carries the coursework and capstone lines.
    Those render elsewhere, so the backfill must not reach for them."""
    resume = _resume([])
    _ensure_education(resume, {"education": VERIFIED["education"][1:]})
    assert resume.education == []

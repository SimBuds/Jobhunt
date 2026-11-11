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
    "Advanced Diploma, Computer Programming & Analysis | Elm Ridge Polytechnic "
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
    assert "Elm Ridge Polytechnic" in resume.education[0]


def test_honours_stay_when_no_coursework_paragraph_renders() -> None:
    """Phase D1 (2026-11-11): with no coursework, nothing else prints the
    honours clause, so the verbatim line keeps it."""
    resume = _resume([])
    _dedupe_education(resume)
    _ensure_education(resume, VERIFIED)
    assert resume.education == [DIPLOMA]


def test_honours_drop_when_the_coursework_paragraph_prints_them() -> None:
    """`render_docx` composes "Dean's List (all terms). Coursework: ..." when
    coursework is set, so the education line would repeat it."""
    resume = _resume([])
    resume.coursework = ["Data Structures & Algorithms"]
    _ensure_education(resume, VERIFIED)
    assert len(resume.education) == 1
    assert "dean" not in resume.education[0].lower()
    assert resume.education[0].startswith("Advanced Diploma, Computer Programming")


def test_a_model_rewrite_is_replaced_by_the_verbatim_line() -> None:
    """Phase D1 (2026-11-11): rule 5's old template carried a <city> slot,
    and the AI lane rewrote the diploma with a city the profile does not
    hold. The verified entry always wins."""
    model_line = (
        "Advanced Diploma, Computer Programming & Analysis (Diploma), Elm Ridge, Toronto (Apr 2024)"
    )
    resume = _resume([model_line])
    _ensure_education(resume, VERIFIED)
    assert resume.education == [DIPLOMA]


def test_no_verified_credential_leaves_the_model_entry() -> None:
    resume = _resume(["Diploma, Somewhere"])
    _ensure_education(resume, {"education": []})
    assert resume.education == ["Diploma, Somewhere"]


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

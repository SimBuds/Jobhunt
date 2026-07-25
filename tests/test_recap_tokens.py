"""Phase A11b — education-recap tokens derive from the verified profile.

Three validators (cover, answer, interview-prep) reject prose that recites the
resume's education block. Each used to hard-code one school's name, so the
guard silently no-opped for any other profile: the literal never matched.
"""

from __future__ import annotations

from jobhunt.pipeline._recap import recap_tokens

_CASEY_EDU = [
    "Computer Programming & Analysis, Advanced Diploma — George Brown College "
    "(Sept 2021 – Apr 2024). Dean's List, all terms. Coursework: Machine Learning."
]


def test_extracts_institution_from_freetext_entry() -> None:
    tokens = recap_tokens({"education": _CASEY_EDU})
    assert "george brown college" in tokens
    # Short form too — people write "George Brown" without "College".
    assert "george brown" in tokens


def test_generic_markers_always_present() -> None:
    tokens = recap_tokens({})
    assert "dean's list" in tokens
    assert "diploma" in tokens


def test_follows_a_different_profile() -> None:
    """The whole point: another user's school is guarded, Casey's is not."""
    tokens = recap_tokens(
        {"education": ["B.Sc. Computer Science — Waterloo University (2018 - 2022)."]}
    )
    assert "waterloo university" in tokens
    assert "waterloo" in tokens
    assert not any("george brown" in t for t in tokens)


def test_accepts_dict_shaped_education_entries() -> None:
    assert "mit" in recap_tokens({"education": [{"institution": "MIT"}]})
    assert "queens" in recap_tokens({"education": [{"school": "Queens"}]})


def test_accepts_a_bare_string_education_field() -> None:
    tokens = recap_tokens({"education": "Diploma — Seneca College (2020)."})
    assert "seneca college" in tokens


def test_extra_markers_are_included_and_deduped() -> None:
    tokens = recap_tokens({}, extra=("coursework", "diploma"))
    assert "coursework" in tokens
    assert tokens.count("diploma") == 1


def test_longest_token_first_so_violations_quote_the_full_name() -> None:
    tokens = recap_tokens({"education": _CASEY_EDU})
    assert tokens.index("george brown college") < tokens.index("george brown")


def test_missing_or_empty_education_is_safe() -> None:
    for profile in ({}, {"education": []}, {"education": None}, {"education": [""]}):
        tokens = recap_tokens(profile)  # type: ignore[arg-type]
        assert "dean's list" in tokens


def test_reproduces_the_previous_hard_coded_sets() -> None:
    """Regression guard: no validator loses sensitivity in the refactor.

    Old literals were:
      answer.py        ("dean's list", "george brown", "diploma", "coursework:")
      cover_validate   ("dean's list", "coursework", "george brown", "diploma")
      interview_prep   ("coursework", "george brown", "dean's list", "diploma")
    """
    answer = recap_tokens({"education": _CASEY_EDU}, extra=("coursework:",))
    assert set(answer) >= {"dean's list", "george brown", "diploma", "coursework:"}

    shared = recap_tokens({"education": _CASEY_EDU}, extra=("coursework",))
    assert set(shared) >= {"dean's list", "coursework", "george brown", "diploma"}

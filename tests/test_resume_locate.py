"""Phase A15 — locate the baseline resume by filename pattern.

Hard-coding `Baseline_Resume.docx` meant renaming the file broke
`convert-resume` and `setup` outright. These tests pin the discovery rule and,
importantly, the *non-recursive* search: generated lane resumes and tailored
per-application copies all carry "Resume" in their names.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from jobhunt.errors import PipelineError
from jobhunt.resume.locate import describe_choice, find_baseline_resume


def _touch(path: Path, *, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if age:
        old = time.time() - age
        os.utime(path, (old, old))
    return path


def test_finds_a_renamed_resume(tmp_path: Path) -> None:
    """The exact case that broke: the file is not named Baseline_Resume.docx."""
    _touch(tmp_path / "Casey_Hsu_Resume.docx")
    assert find_baseline_resume(tmp_path).name == "Casey_Hsu_Resume.docx"


def test_match_is_case_insensitive(tmp_path: Path) -> None:
    _touch(tmp_path / "MY-RESUME-2026.DOCX")
    assert find_baseline_resume(tmp_path).name == "MY-RESUME-2026.DOCX"


def test_baseline_name_wins_over_a_newer_file(tmp_path: Path) -> None:
    """'baseline' is the documented convention — the only way to state intent."""
    _touch(tmp_path / "Baseline_Resume.docx", age=9999)
    _touch(tmp_path / "Other_Resume.docx")
    assert find_baseline_resume(tmp_path).name == "Baseline_Resume.docx"


def test_docx_wins_over_pdf(tmp_path: Path) -> None:
    """Only .docx can actually be parsed, so prefer it when both exist."""
    _touch(tmp_path / "Resume.pdf")
    _touch(tmp_path / "Resume.docx", age=9999)
    assert find_baseline_resume(tmp_path).name == "Resume.docx"


def test_pdf_is_still_discovered_when_alone(tmp_path: Path) -> None:
    """Found, so the failure downstream is explicit rather than 'no resume'."""
    _touch(tmp_path / "Resume.pdf")
    assert find_baseline_resume(tmp_path).suffix == ".pdf"


def test_newest_wins_among_equals(tmp_path: Path) -> None:
    _touch(tmp_path / "A_Resume.docx", age=9999)
    _touch(tmp_path / "B_Resume.docx")
    assert find_baseline_resume(tmp_path).name == "B_Resume.docx"


def test_word_lock_files_are_ignored(tmp_path: Path) -> None:
    """Word leaves ~$Name.docx beside an open document."""
    _touch(tmp_path / "~$Baseline_Resume.docx")
    _touch(tmp_path / "Baseline_Resume.docx")
    assert find_baseline_resume(tmp_path).name == "Baseline_Resume.docx"


def test_non_resume_documents_are_ignored(tmp_path: Path) -> None:
    _touch(tmp_path / "Cover_Letter.docx")
    _touch(tmp_path / "notes.pdf")
    with pytest.raises(PipelineError, match="no resume found"):
        find_baseline_resume(tmp_path)


def test_search_is_not_recursive(tmp_path: Path) -> None:
    """A generated artifact must never become the source of truth for itself.

    data/resumes/ holds `jobhunt resume` output and data/applications/<id>/
    holds tailored copies — all named *Resume*.docx.
    """
    _touch(tmp_path / "data" / "resumes" / "Casey_Hsu_Resume_AI_Automation.docx")
    _touch(tmp_path / "data" / "applications" / "x" / "Casey_Hsu_Resume.docx")
    with pytest.raises(PipelineError, match="no resume found"):
        find_baseline_resume(tmp_path)


def test_explicit_path_wins_even_if_unconventionally_named(tmp_path: Path) -> None:
    _touch(tmp_path / "Baseline_Resume.docx")
    odd = _touch(tmp_path / "cv-final-FINAL.docx")
    assert find_baseline_resume(tmp_path, explicit=odd) == odd


def test_explicit_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="resume not found"):
        find_baseline_resume(tmp_path, explicit=tmp_path / "nope.docx")


def test_describe_choice_names_the_alternatives(tmp_path: Path) -> None:
    """Silently picking one of several resumes is the dangerous outcome."""
    _touch(tmp_path / "Baseline_Resume.docx")
    _touch(tmp_path / "Old_Resume.docx")
    chosen = find_baseline_resume(tmp_path)
    line = describe_choice(chosen, tmp_path)
    assert "Baseline_Resume.docx" in line
    assert "Old_Resume.docx" in line


def test_describe_choice_stays_quiet_when_unambiguous(tmp_path: Path) -> None:
    _touch(tmp_path / "Baseline_Resume.docx")
    chosen = find_baseline_resume(tmp_path)
    assert "also found" not in describe_choice(chosen, tmp_path)

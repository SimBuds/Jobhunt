"""Phase A9b — `convert-resume` refuses to write a partial profile.

On 2026-07-24 a reformatted baseline parsed with 12 warnings and
`convert-resume` wrote `kb/profile/` anyway. The resulting verified.json
claimed zero core skills, zero AI skills, and one role ("Sous Chef"), which
would have made the scorer grade against a hospitality profile and the
fabrication guard reject every real skill as unverified. A partial profile is
strictly worse than no profile, because nothing downstream can tell the
difference between "not in the resume" and "the parser lost it".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.commands.convert_resume_cmd import _dropped_content_warnings

runner = CliRunner()


def test_dropped_classifier_flags_lossy_warnings() -> None:
    lossy = [
        "TECHNICAL SKILLS: unrecognized skill label 'Foo', items dropped: 'A, B'",
        "PROFESSIONAL EXPERIENCE: bullet before any role header, skipped: 'x'",
        "PROFESSIONAL EXPERIENCE: unparseable role header, skipped: 'y'",
    ]
    assert _dropped_content_warnings(lossy) == lossy


def test_dropped_classifier_ignores_advisory_warnings() -> None:
    """The guard blocks data loss, not any imperfect parse."""
    advisory = [
        "SUMMARY: section is unusually short",
        "CONTACT: no portfolio URL found",
    ]
    assert _dropped_content_warnings(advisory) == []


def _resume(path: Path, *, skill_label: str, role_line: str) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Jane Dev")
    # Full contact block, split across two paragraphs like the real baseline:
    # convert-resume separately exits 1 on missing applicant fields, which
    # would mask the guard's own exit code.
    doc.add_paragraph("Toronto, ON  |  416-555-0100  |  jane@example.com")
    doc.add_paragraph(
        "linkedin.com/in/jane-dev  |  janedev.com  |  github.com/janedev"
    )
    doc.add_paragraph("TECHNICAL SKILLS")
    doc.add_paragraph(f"{skill_label}: TypeScript, React")
    doc.add_paragraph("PROFESSIONAL EXPERIENCE")
    doc.add_paragraph(role_line)
    doc.add_paragraph("Shipped a thing that worked.")
    out = path / "r.docx"
    doc.save(out)
    return out


@pytest.fixture
def env(tmp_path: Path, tmp_config_dir: Path) -> Path:
    """Scratch config so convert-resume writes into tmp, not the real kb/."""
    jh = tmp_config_dir / "jobhunt"
    jh.mkdir(parents=True, exist_ok=True)
    (jh / "config.toml").write_text(
        "[paths]\n"
        f'data_dir = "{tmp_path / "data"}"\n'
        f'db_path = "{tmp_path / "data" / "jobhunt.db"}"\n'
        f'kb_dir = "{tmp_path / "kb"}"\n'
    )
    return tmp_path


def test_convert_resume_blocks_write_when_content_dropped(env: Path) -> None:
    from jobhunt.commands import convert_resume_cmd

    docx = _resume(
        env,
        skill_label="Hobbies",  # non-skill label → items genuinely dropped
        role_line="Dev | Acme   Jan 2024 – Present",
    )
    result = runner.invoke(convert_resume_cmd.app, ["--docx", str(docx)])

    assert result.exit_code == 1
    assert "NOT written" in result.output
    assert not (env / "kb" / "profile" / "verified.json").exists()


def test_force_overrides_the_guard(env: Path) -> None:
    from jobhunt.commands import convert_resume_cmd

    docx = _resume(
        env,
        skill_label="Hobbies",
        role_line="Dev | Acme   Jan 2024 – Present",
    )
    runner.invoke(convert_resume_cmd.app, ["--docx", str(docx), "--force"])

    # Asserting on the artifact, not the exit code: `convert-resume` also exits
    # 1 on missing [applicant] fields, an unrelated pre-existing check that
    # this minimal fixture trips. The guard's contract is whether kb/profile/
    # gets written.
    assert (env / "kb" / "profile" / "verified.json").is_file()


def test_clean_parse_writes_normally(env: Path) -> None:
    from jobhunt.commands import convert_resume_cmd

    docx = _resume(
        env,
        skill_label="Languages & Frameworks",  # A9 alias
        role_line="Dev | Acme   Jan 2024 – Present",
    )
    runner.invoke(convert_resume_cmd.app, ["--docx", str(docx)])

    # See the note in test_force_overrides_the_guard on asserting the artifact
    # rather than the exit code.
    assert (env / "kb" / "profile" / "verified.json").is_file()

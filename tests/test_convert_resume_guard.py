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


def test_dropped_classifier_ignores_known_benign_warnings() -> None:
    """Warnings where the parser KEPT the content must not block a write.

    Both of these are real `parse_baseline` outputs: the cert-vs-education
    classifier falling back, and an unrecognised skill label whose items are
    still filed under Core.
    """
    benign = [
        "CERTIFICATIONS & EDUCATION: could not classify as cert or education, "
        "defaulted to education: 'Capstone: ...'",
        "TECHNICAL SKILLS: unrecognized skill label 'Wingdings', assigned to "
        "Core — add an alias if that is wrong",
    ]
    assert _dropped_content_warnings(benign) == []


def test_unrecognized_warning_kinds_block_by_default() -> None:
    """Phase A9d: the classifier fails CLOSED.

    The previous version allow-listed the words "dropped"/"skipped", so a
    future warning that discarded content while describing it differently
    would sail through the guard — the exact failure class the guard exists to
    prevent, and the same mistake the skill-label allow-list made before A9c.
    An unclassified warning must now refuse the write and get noticed.
    """
    novel = ["PROJECTS: three entries were discarded, reason unknown"]
    assert _dropped_content_warnings(novel) == novel


class TestContactLineUrls:
    """Bare domains on a contact line must resolve to `[applicant]` URLs.

    Printed resumes almost never write the `https://` scheme — there is nothing
    to click on paper. The original patterns required it, so `linkedin_url` and
    `github_url` (both in `_REQUIRED_FIELDS`) came back empty for a normally
    formatted resume, and `convert-resume` exited 1 for essentially every new
    user.
    """

    def test_bare_domains_are_extracted_and_given_a_scheme(self) -> None:
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line(
            "Toronto, ON  |  416-555-0100  |  jane@example.com  |  "
            "linkedin.com/in/jane-dev  |  janedev.com  |  github.com/janedev"
        )
        assert got["linkedin_url"] == "https://linkedin.com/in/jane-dev"
        assert got["github_url"] == "https://github.com/janedev"
        assert got["portfolio_url"] == "https://janedev.com"
        assert got["phone"] == "416-555-0100"

    def test_scheme_ful_urls_still_work(self) -> None:
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line(
            "jane@example.com | https://www.linkedin.com/in/jane | "
            "https://github.com/jane | https://jane.dev"
        )
        assert got["linkedin_url"] == "https://www.linkedin.com/in/jane"
        assert got["github_url"] == "https://github.com/jane"
        assert got["portfolio_url"] == "https://jane.dev"

    def test_email_domain_is_not_mistaken_for_a_portfolio(self) -> None:
        """A bare-domain pattern would otherwise match inside `x@outlook.com`."""
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line("Jane Dev  |  jane@outlook.com  |  416-555-0100")
        assert "portfolio_url" not in got
        assert got["email"] == "jane@outlook.com"

    def test_dotted_library_names_are_not_portfolios(self) -> None:
        """`Node.js` / `Next.js` look like hosts; the TLD allowlist rejects them."""
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line("Node.js / Next.js Developer  |  jane@example.com")
        assert "portfolio_url" not in got


class TestUserAgentBackfill:
    """The ingest User-Agent must not keep shipping a placeholder address."""

    def test_placeholder_constant_matches_the_config_default(self) -> None:
        """Pins the copy in convert_resume_cmd to the real schema default.

        The two live in different modules; if the default UA is ever reworded
        in config.py, the substring match here would silently stop firing and
        the placeholder would come back. Fail loudly instead.
        """
        from jobhunt.commands.convert_resume_cmd import (
            _DEFAULT_USER_AGENT,
            _PLACEHOLDER_CONTACT,
        )
        from jobhunt.config import IngestConfig

        assert IngestConfig().user_agent == _DEFAULT_USER_AGENT
        assert _PLACEHOLDER_CONTACT in IngestConfig().user_agent


class TestContactLineCityRegion:
    """`City, REGION` must be found wherever it sits on the line.

    The pattern was `^`-anchored, so any contact line that opened with a job
    title (the common layout) matched nothing and city/region silently kept
    their Toronto/Ontario defaults — correct for exactly one user.
    """

    @pytest.mark.parametrize(
        ("contact", "city", "region"),
        [
            # Title first, fields separated by double spaces — the layout that
            # defeated the anchored pattern.
            (
                "Full-Stack Developer  |  CMS & AI  Toronto, ON  |  a@b.io",
                "Toronto",
                "Ontario",
            ),
            # City first: what the anchored pattern was written for.
            ("Toronto, ON | 647-555-0199 | a@b.io", "Toronto", "Ontario"),
            ("Jane Dev | Vancouver, BC | jane@example.com", "Vancouver", "British Columbia"),
            # Region spelled out, and a hyphenated multi-word city.
            ("Dev | Sainte-Anne-de-Bellevue, Quebec | d@e.ca", "Sainte-Anne-de-Bellevue", "Quebec"),
            ("Engineer | Richmond Hill, Ontario | d@e.ca", "Richmond Hill", "Ontario"),
        ],
    )
    def test_city_region_is_found_anywhere_on_the_line(
        self, contact: str, city: str, region: str
    ) -> None:
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line(contact)
        assert got["city"] == city
        assert got["region"] == region

    def test_leftmost_comma_does_not_win(self) -> None:
        """Anchoring on the region is what keeps a free search honest.

        An unconstrained `(...),\\s*([A-Za-z]{2,})` matches the first comma in
        the line. Here that would yield city="Developer", region="Toronto".
        """
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line("Senior Developer, Toronto | Markham, ON | a@b.io")
        assert got["city"] == "Markham"
        assert got["region"] == "Ontario"

    def test_unknown_region_falls_back_rather_than_guessing(self) -> None:
        """Region allowlist is Canadian — the app is GTA/Remote-Canada scoped.

        A non-Canadian line yields nothing, leaving the config defaults in
        place, rather than recording a city paired with a region the expansion
        table cannot interpret.
        """
        from jobhunt.commands.convert_resume_cmd import _parse_contact_line

        got = _parse_contact_line("Engineer | Austin, TX | d@e.ca")
        assert "city" not in got
        assert "region" not in got


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

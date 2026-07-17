"""Tests for `jobhunt track` — manual-application tracking (July 2026)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.commands import track_cmd
from jobhunt.db import connect, migrate, upsert_application, upsert_job
from jobhunt.models import Job

runner = CliRunner()


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _job(id_: str, company: str, title: str) -> Job:
    return Job(
        id=id_,
        source=id_.split(":")[0],
        external_id=id_.rsplit(":", 1)[-1],
        company=company,
        title=title,
        location="Toronto, ON",
        description="A job.",
        url=f"https://example.com/{id_}",
    )


def _seed_application(conn, id_: str, company: str, title: str, channel=None) -> str:
    upsert_job(conn, _job(id_, company, title))
    upsert_application(
        conn,
        application_id=f"app-{id_}",
        job_id=id_,
        status="applied",
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week="2026-W29",
        channel=channel,
    )
    return id_


# --- channel column semantics (migration 0009) ---


def test_migration_adds_channel_with_pipeline_default(conn) -> None:
    _seed_application(conn, "greenhouse:acme:1", "Acme", "Dev")
    row = conn.execute(
        "SELECT channel FROM applications WHERE job_id = 'greenhouse:acme:1'"
    ).fetchone()
    assert row["channel"] == "pipeline"


def test_upsert_preserves_manual_channel_on_retailor(conn) -> None:
    """A job logged via `track applied --channel linkedin` must keep its
    channel when `apply` later re-upserts the row without a channel."""
    _seed_application(conn, "manual:abc", "Acme", "Dev", channel="linkedin")
    upsert_application(
        conn,
        application_id="app-2",
        job_id="manual:abc",
        status="applied",
        resume_path="/tmp/resume.docx",
        cover_path=None,
        fill_plan_path=None,
        applied_week=None,
    )
    row = conn.execute(
        "SELECT channel FROM applications WHERE job_id = 'manual:abc'"
    ).fetchone()
    assert row["channel"] == "linkedin"


def test_upsert_explicit_channel_wins(conn) -> None:
    _seed_application(conn, "manual:abc", "Acme", "Dev", channel="linkedin")
    upsert_application(
        conn,
        application_id="app-2",
        job_id="manual:abc",
        status="applied",
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week=None,
        channel="indeed",
    )
    row = conn.execute(
        "SELECT channel FROM applications WHERE job_id = 'manual:abc'"
    ).fetchone()
    assert row["channel"] == "indeed"


# --- ref resolver ---


def test_resolve_ref_exact_job_id(conn) -> None:
    _seed_application(conn, "greenhouse:acme:1", "Acme", "Dev")
    assert track_cmd._resolve_ref(conn, "greenhouse:acme:1") == "greenhouse:acme:1"


def test_resolve_ref_unique_fragment(conn) -> None:
    _seed_application(conn, "greenhouse:acme:1", "Acme Corp", "Backend Developer")
    _seed_application(conn, "manual:xyz", "Globex", "Frontend Developer")
    assert track_cmd._resolve_ref(conn, "globex") == "manual:xyz"


def test_resolve_ref_ambiguous_lists_candidates(conn, capsys) -> None:
    _seed_application(conn, "greenhouse:acme:1", "Acme", "Backend Developer")
    _seed_application(conn, "manual:xyz", "Globex", "Frontend Developer")
    with pytest.raises(Exception):  # typer.Exit
        track_cmd._resolve_ref(conn, "developer")
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "greenhouse:acme:1" in err and "manual:xyz" in err


def test_resolve_ref_no_match_errors(conn, capsys) -> None:
    with pytest.raises(Exception):
        track_cmd._resolve_ref(conn, "nonexistent")
    assert "no tracked application" in capsys.readouterr().err


# --- track applied (paste path, end to end through the CLI) ---


@pytest.fixture
def isolated_env(tmp_config_dir: Path, tmp_path: Path) -> Path:
    """Seed a scratch config.toml under the redirected XDG_CONFIG_HOME so the
    CLI runs against a tmp DB (same pattern as test_add_cmd)."""
    jh_dir = tmp_config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    (jh_dir / "config.toml").write_text(
        "[paths]\n"
        f'data_dir = "{data}"\n'
        f'db_path = "{data / "jobhunt.db"}"\n'
        f'migrations_dir = "{migrations}"\n'
        f'kb_dir = "{tmp_path / "kb"}"\n'
    )
    return data


def test_track_applied_paste_path_writes_row(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        [
            "applied", "https://www.linkedin.com/jobs/view/4211",
            "--channel", "linkedin",
            "--title", "Junior Developer",
            "--company", "Next Match AI",
            "--jd-from-stdin",
            "--when", "2026-07-15",
            "--notes", "easy apply",
        ],
        input=(
            "We are hiring a Junior Developer to join our engineering team. "
            "You will build and maintain web applications using React, Node.js, "
            "TypeScript, and SQL databases, collaborate with product and design, "
            "write automated tests, and participate in code review. "
        )
        * 3,  # comfortably clears the 400-char minimum-JD intake guard
    )
    assert result.exit_code == 0, result.output
    assert "tracked:" in result.output

    conn = sqlite3.connect(isolated_env / "jobhunt.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT a.status, a.channel, a.applied_at, a.applied_week, a.notes, j.company
        FROM applications a JOIN jobs j ON j.id = a.job_id
        """
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "applied"
    assert row["channel"] == "linkedin"
    assert row["applied_at"] == "2026-07-15"
    assert row["applied_week"] == "2026-W29"
    assert row["notes"] == "easy apply"
    assert row["company"] == "Next Match AI"


def test_track_applied_rejects_bad_channel(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "https://example.com/x", "--channel", "carrier-pigeon"],
    )
    assert result.exit_code == 2
    assert "invalid channel" in result.output


def test_track_applied_rejects_bad_date(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        [
            "applied", "https://example.com/x",
            "--channel", "indeed", "--when", "last tuesday",
        ],
    )
    assert result.exit_code == 2
    assert "--when must be YYYY-MM-DD" in result.output


def test_track_applied_stdin_requires_title_company(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "https://example.com/x", "--channel", "indeed", "--jd-from-stdin"],
        input="jd body",
    )
    assert result.exit_code == 2
    assert "--title and --company" in result.output


def test_track_applied_non_url_non_id_errors(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "not-a-job", "--channel", "indeed"],
    )
    assert result.exit_code == 2
    assert "neither a known job id nor a URL" in result.output


# --- --no-jd historical backfill ---


def test_track_applied_no_jd_backfill_writes_stub_row(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        [
            "applied", "--no-jd",
            "--channel", "indeed",
            "--title", "Web Developer",
            "--company", "Oldco",
            "--when", "2026-06-02",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no JD stored" in result.output

    conn = sqlite3.connect(isolated_env / "jobhunt.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT a.channel, a.applied_at, j.description, j.company
        FROM applications a JOIN jobs j ON j.id = a.job_id
        """
    ).fetchone()
    conn.close()
    assert row["channel"] == "indeed"
    assert row["applied_at"] == "2026-06-02"
    assert row["description"] is None
    assert row["company"] == "Oldco"


def test_track_applied_no_jd_dedupes_on_same_title_company(isolated_env: Path) -> None:
    args = [
        "applied", "--no-jd", "--channel", "indeed",
        "--title", "Web Developer", "--company", "Oldco",
    ]
    assert runner.invoke(track_cmd.app, args).exit_code == 0
    assert runner.invoke(track_cmd.app, args).exit_code == 0
    conn = sqlite3.connect(isolated_env / "jobhunt.db")
    n_jobs, n_apps = conn.execute(
        "SELECT (SELECT COUNT(*) FROM jobs), (SELECT COUNT(*) FROM applications)"
    ).fetchone()
    conn.close()
    assert (n_jobs, n_apps) == (1, 1)


def test_track_applied_no_jd_requires_title_company(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app, ["applied", "--no-jd", "--channel", "indeed"]
    )
    assert result.exit_code == 2
    assert "--no-jd requires --title and --company" in result.output


def test_track_applied_no_jd_conflicts_with_stdin(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "--no-jd", "--jd-from-stdin", "--channel", "indeed"],
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_track_applied_no_ref_without_no_jd_errors(isolated_env: Path) -> None:
    result = runner.invoke(track_cmd.app, ["applied", "--channel", "indeed"])
    assert result.exit_code == 2
    assert "URL or job id is required" in result.output


# --- --paste: LinkedIn job-page paste intake ---

_LI_HEADER = """Company logo for, OpenTable.
OpenTable


Staff Frontend Software Engineer (Availability Planning & Experiences)

Toronto, ON · Reposted 1 week ago · Over 100 people clicked apply

Promoted by hirer · Responses managed off LinkedIn

Full-time"""


def test_parse_linkedin_paste_header_only() -> None:
    from jobhunt.ingest.manual import parse_linkedin_paste

    title, company, location, body = parse_linkedin_paste(_LI_HEADER)
    assert title == "Staff Frontend Software Engineer (Availability Planning & Experiences)"
    assert company == "OpenTable"
    assert location == "Toronto, ON"
    assert body is None


def test_parse_linkedin_paste_with_about_the_job_body() -> None:
    from jobhunt.ingest.manual import parse_linkedin_paste

    text = _LI_HEADER + "\n\nAbout the job\nWe build availability planning tools.\nYou will ship React features."
    title, company, location, body = parse_linkedin_paste(text)
    assert company == "OpenTable"
    assert body is not None
    assert "availability planning tools" in body
    assert "About the job" not in body


def test_parse_linkedin_paste_city_ending_in_ago_is_not_noise() -> None:
    from jobhunt.ingest.manual import parse_linkedin_paste

    text = "Acme\nBackend Developer\nChicago · 2 weeks ago"
    title, company, location, body = parse_linkedin_paste(text)
    assert (title, company, location) == ("Backend Developer", "Acme", "Chicago")


def test_track_applied_paste_header_only_creates_stub(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "--channel", "linkedin", "--paste", "--when", "2026-07-12"],
        input=_LI_HEADER,
    )
    assert result.exit_code == 0, result.output
    assert "header only" in result.output

    conn = sqlite3.connect(isolated_env / "jobhunt.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT a.channel, a.applied_at, j.title, j.company, j.location, j.description
        FROM applications a JOIN jobs j ON j.id = a.job_id
        """
    ).fetchone()
    conn.close()
    assert row["company"] == "OpenTable"
    assert row["location"] == "Toronto, ON"
    assert row["description"] is None
    assert row["applied_at"] == "2026-07-12"


def test_track_applied_paste_full_page_stores_jd(isolated_env: Path) -> None:
    jd = (
        "About the job\n"
        + "We build availability planning and dining experiences at scale. "
        "You will ship React and TypeScript features, own frontend architecture, "
        "and collaborate with design and product on guest-facing surfaces. " * 4
    )
    result = runner.invoke(
        track_cmd.app,
        ["applied", "--channel", "linkedin", "--paste"],
        input=_LI_HEADER + "\n\n" + jd,
    )
    assert result.exit_code == 0, result.output
    assert "JD captured" in result.output

    conn = sqlite3.connect(isolated_env / "jobhunt.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT description FROM jobs").fetchone()
    conn.close()
    assert row["description"] and "availability planning" in row["description"]


def test_track_applied_paste_conflicts_with_jd_from_stdin(isolated_env: Path) -> None:
    result = runner.invoke(
        track_cmd.app,
        ["applied", "--channel", "linkedin", "--paste", "--jd-from-stdin"],
        input="x",
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output

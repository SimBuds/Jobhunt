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

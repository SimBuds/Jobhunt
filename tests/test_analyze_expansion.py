"""Phase 14 tests — analyze skills --gaps / employers / response-rate."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.db import (
    connect,
    mark_response_received,
    migrate,
    set_decline_reason,
    upsert_application,
    upsert_job,
    write_score,
)
from jobhunt.models import Job


def _seed_minimal_config(config_dir: Path, db_path: Path) -> Path:
    """Write a config that points at the given DB + has a kb/profile/verified.json
    so `ensure_profile` passes."""
    jh_dir = config_dir / "jobhunt"
    jh_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = jh_dir / "config.toml"
    cfg_path.write_text(
        "[paths]\n"
        f'data_dir = "{db_path.parent}"\n'
        f'db_path = "{db_path}"\n'
        f'kb_dir = "{config_dir / "kb"}"\n'
        "[ingest]\n"
        'greenhouse = ["live-co", "dead-co"]\n'
        'ashby = ["good-ash"]\n'
        "lever = []\n"
        "smartrecruiters = []\n"
        "workday = []\n"
    )
    profile = config_dir / "kb" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "verified.json").write_text("{}")
    return cfg_path


def _job(suffix: str, *, source: str = "greenhouse", company: str = "live-co",
         description: str = "Build with React and Node.js.",
         title: str = "Dev", posted: str | None = None) -> Job:
    return Job(
        id=f"{source}:{company}:{suffix}",
        source=source,
        external_id=suffix,
        company=company,
        title=title,
        location="Toronto, ON",
        description=description,
        url=f"https://example.com/{suffix}",
        posted_at=posted,
    )


# --- analyze skills --gaps --------------------------------------------------


def test_analyze_skills_gaps_surfaces_overrepresented_tokens(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    # Two declined jobs heavy in Go; one accepted job heavy in React.
    upsert_job(c, _job("1", description="Go and Rust experience required."))
    set_decline_reason(c, "greenhouse:live-co:1", "Wrong stack — Go required")
    upsert_job(c, _job("2", description="Production Go services."))
    set_decline_reason(c, "greenhouse:live-co:2", "Wrong stack — Go required")
    upsert_job(c, _job("3", description="React and TypeScript stack."))
    c.commit()
    c.close()

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "skills", "--gaps", "--window-days", "365"])
    assert result.exit_code == 0, result.output
    # `go` should appear with a positive decline-share delta.
    assert "go" in result.output
    # Sanity: header columns present.
    assert "Decline-share" in result.output


def test_analyze_skills_requires_gaps_flag(tmp_config_dir: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    c.commit()
    c.close()
    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "skills"])
    assert result.exit_code != 0
    assert "--gaps" in result.output


def test_analyze_skills_no_declines_in_window(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    upsert_job(c, _job("1"))  # not declined
    c.commit()
    c.close()

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "skills", "--gaps"])
    assert result.exit_code == 0
    assert "nothing to compare" in result.output


# --- analyze employers --hiring-velocity ------------------------------------


def test_analyze_employers_groups_by_slug(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    # Two posts under live-co, zero under dead-co (configured but absent).
    upsert_job(c, _job("1", company="live-co"))
    upsert_job(c, _job("2", company="live-co"))
    upsert_job(c, _job("3", source="ashby", company="good-ash"))
    c.commit()
    c.close()

    runner = CliRunner()
    result = runner.invoke(
        app, ["analyze", "employers", "--hiring-velocity", "--window-days", "365"]
    )
    assert result.exit_code == 0, result.output
    assert "live-co" in result.output
    assert "good-ash" in result.output
    # dead-co was configured but has no posts → should show up in the
    # "configured but 0 posts" callout.
    assert "dead-co" in result.output
    assert "0 posts in window" in result.output


def test_analyze_employers_requires_flag(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    c.commit()
    c.close()
    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "employers"])
    assert result.exit_code != 0


# --- analyze response-rate --------------------------------------------------


def test_analyze_response_rate_by_score_bands(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    # Three applied jobs at varying scores; one got a response.
    for i, sc in enumerate([90, 80, 60]):
        j = _job(str(i))
        upsert_job(c, j)
        write_score(
            c, job_id=j.id, score=sc,
            reasons=[], red_flags=[], must_clarify=[],
            model="t", prompt_hash="h",
        )
        upsert_application(
            c, application_id=f"a{i}", job_id=j.id, status="applied",
            resume_path=None, cover_path=None, fill_plan_path=None,
            applied_week="2026-W21",
        )
    mark_response_received(c, "greenhouse:live-co:0", "2026-05-20", None)
    c.commit()
    c.close()

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "response-rate", "--by", "score"])
    assert result.exit_code == 0, result.output
    assert "85–100" in result.output
    assert "TOTAL" in result.output


def test_analyze_response_rate_by_ats(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    j_gh = _job("1", source="greenhouse")
    j_ash = _job("2", source="ashby", company="good-ash")
    upsert_job(c, j_gh)
    upsert_job(c, j_ash)
    upsert_application(
        c, application_id="a1", job_id=j_gh.id, status="applied",
        resume_path=None, cover_path=None, fill_plan_path=None, applied_week="2026-W21",
    )
    upsert_application(
        c, application_id="a2", job_id=j_ash.id, status="applied",
        resume_path=None, cover_path=None, fill_plan_path=None, applied_week="2026-W21",
    )
    mark_response_received(c, j_ash.id, "2026-05-20", None)
    c.commit()
    c.close()

    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "response-rate", "--by", "ats"])
    assert result.exit_code == 0, result.output
    assert "greenhouse" in result.output
    assert "ashby" in result.output


def test_analyze_response_rate_rejects_bad_by(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    c.commit()
    c.close()
    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "response-rate", "--by", "bogus"])
    assert result.exit_code != 0
    assert "must be" in result.output


def test_analyze_response_rate_no_applications(
    tmp_config_dir: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "jobhunt.db"
    _seed_minimal_config(tmp_config_dir, db_path)
    c = connect(db_path)
    migrate(c, Path(__file__).resolve().parent.parent / "migrations")
    c.commit()
    c.close()
    runner = CliRunner()
    result = runner.invoke(app, ["analyze", "response-rate"])
    assert result.exit_code == 0
    assert "no submitted applications" in result.output

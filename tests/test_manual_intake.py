"""Phase 1 — shared manual-job intake (`commands._manual_intake`).

Covers the paste-JD happy path (synth + upsert into the jobs DB) and the
`interview_prep_cmd._resolve_job_id` validation branches that gate it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.commands._manual_intake import synth_manual_job
from jobhunt.commands.interview_prep_cmd import _resolve_job_id
from jobhunt.config import Config, IngestConfig, PathsConfig
from jobhunt.db import connect, migrate

_JD_BODY = (
    "We are hiring a Shopify and headless CMS developer to own our storefront. "
    "You will build Liquid themes, integrate Stripe, run bulk JSON catalog "
    "migrations, and set up GitHub Actions CI with automated linting. "
) * 4  # comfortably over MIN_BODY_CHARS (400)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(data_dir=tmp_path, db_path=tmp_path / "jobhunt.db"),
        ingest=IngestConfig(user_agent="test/1.0"),
    )


@pytest.mark.parametrize("command", (["apply"], ["interview-prep"]))
def test_stdin_alias_is_accepted_by_manual_intake_commands(
    command: list[str],
) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [*command, "--stdin", "--help"])
    assert result.exit_code == 0, result.output
    assert "No such option" not in result.output
    assert "--stdin" in result.output


@pytest.mark.asyncio
async def test_paste_intake_synths_and_upserts(
    tmp_path: Path, migrations_dir: Path
) -> None:
    cfg = _cfg(tmp_path)
    migrate(connect(cfg.paths.db_path), migrations_dir)

    job = await synth_manual_job(
        cfg,
        url="https://www.linkedin.com/jobs/view/123",
        title="Headless CMS Developer",
        company="Acme Commerce",
        force_robots=False,
        description=_JD_BODY,
    )

    assert job.source == "manual"
    assert job.id.startswith("manual:")
    # The row is persisted, so it shows up for `list` / prep downstream.
    conn = connect(cfg.paths.db_path)
    row = conn.execute("SELECT id, company FROM jobs WHERE id = ?", (job.id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["company"] == "Acme Commerce"


@pytest.mark.asyncio
async def test_paste_intake_requires_title_and_company(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        await synth_manual_job(
            cfg, url=None, title=None, company=None,
            force_robots=False, description=_JD_BODY,
        )
    assert exc.value.exit_code == 2


def test_resolve_job_id_rejects_both_id_and_url(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        _resolve_job_id(
            cfg, job_id="adzuna_ca:1", url="https://x.test/jd",
            title=None, company=None, description_from_stdin=False,
            force_robots=False,
        )
    assert exc.value.exit_code == 2


def test_resolve_job_id_rejects_neither(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        _resolve_job_id(
            cfg, job_id=None, url=None, title=None, company=None,
            description_from_stdin=False, force_robots=False,
        )
    assert exc.value.exit_code == 2


def test_resolve_job_id_passthrough_existing_id(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    out = _resolve_job_id(
        cfg, job_id="adzuna_ca:1", url=None, title=None, company=None,
        description_from_stdin=False, force_robots=False,
    )
    assert out == "adzuna_ca:1"

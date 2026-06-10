"""Apply-time Adzuna redirect resolution (Phase C1).

`_resolve_adzuna_url` chases the tracking redirect for adzuna-sourced rows
only, persists the terminal URL onto the job row, and leaves every other
source untouched. The chase itself is stubbed (resolve_redirect has its own
MockTransport suite in test_redirect_resolve.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands import apply_cmd
from jobhunt.config import Config
from jobhunt.db import connect, migrate, upsert_job
from jobhunt.models import Job

TERMINAL = "https://employer.example.com/apply/123"


@pytest.fixture
def cfg(tmp_path: Path, migrations_dir: Path) -> Config:
    c = Config()
    c.paths.data_dir = tmp_path
    c.paths.db_path = tmp_path / "test.db"
    conn = connect(c.paths.db_path)
    migrate(conn, migrations_dir)
    conn.close()
    return c


def _job(source: str, url: str | None) -> Job:
    return Job(
        id=f"{source}:1",
        source=source,
        external_id="1",
        company="ACME Inc",
        title="Full-Stack Developer",
        location="Toronto, ON",
        description="…",
        url=url,
    )


@pytest.fixture
def stub_chase(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_resolve(client, url, limiter, **kw):  # noqa: ANN001, ANN003
        calls.append(url)
        return TERMINAL

    monkeypatch.setattr(apply_cmd, "resolve_redirect", fake_resolve)
    return calls


@pytest.mark.asyncio
async def test_adzuna_url_resolved_and_persisted(cfg: Config, stub_chase: list[str]) -> None:
    job = _job("adzuna_ca", "https://www.adzuna.ca/land/ad/1")
    conn = connect(cfg.paths.db_path)
    with conn:
        upsert_job(conn, job)
    conn.close()

    out = await apply_cmd._resolve_adzuna_url(cfg, job)
    assert out.url == TERMINAL
    assert stub_chase == ["https://www.adzuna.ca/land/ad/1"]
    conn = connect(cfg.paths.db_path)
    row = conn.execute("SELECT url FROM jobs WHERE id = ?", (job.id,)).fetchone()
    conn.close()
    assert row[0] == TERMINAL


@pytest.mark.asyncio
async def test_non_adzuna_source_untouched(cfg: Config, stub_chase: list[str]) -> None:
    job = _job("greenhouse", "https://boards.greenhouse.io/acme/jobs/1")
    out = await apply_cmd._resolve_adzuna_url(cfg, job)
    assert out is job
    assert stub_chase == []


@pytest.mark.asyncio
async def test_missing_url_skipped(cfg: Config, stub_chase: list[str]) -> None:
    job = _job("adzuna_ca", None)
    out = await apply_cmd._resolve_adzuna_url(cfg, job)
    assert out is job
    assert stub_chase == []


@pytest.mark.asyncio
async def test_unresolved_chase_leaves_job_unchanged(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def same_url(client, url, limiter, **kw):  # noqa: ANN001, ANN003
        return url

    monkeypatch.setattr(apply_cmd, "resolve_redirect", same_url)
    job = _job("adzuna_ca", "https://www.adzuna.ca/land/ad/1")
    out = await apply_cmd._resolve_adzuna_url(cfg, job)
    assert out is job

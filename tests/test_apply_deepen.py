"""Thin-Adzuna deep-fetch at apply time (Phase C2).

`_deepen_thin_adzuna` enriches snippet-length adzuna_ca rows from the
employer page before tailoring, persists the full JD, and invalidates the
snippet-based score row. Fetch and robots are stubbed — no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands import apply_cmd
from jobhunt.config import Config
from jobhunt.db import connect, migrate, upsert_job, write_score
from jobhunt.models import Job

SNIPPET = "Short Adzuna snippet about a Full-Stack Developer role."
FULL_JD = "A full employer-page job description. " * 40  # well over thin_jd_chars


@pytest.fixture
def cfg(tmp_path: Path, migrations_dir: Path) -> Config:
    c = Config()
    c.paths.data_dir = tmp_path
    c.paths.db_path = tmp_path / "test.db"
    conn = connect(c.paths.db_path)
    migrate(conn, migrations_dir)
    conn.close()
    return c


def _job(source: str = "adzuna_ca", description: str = SNIPPET) -> Job:
    return Job(
        id=f"{source}:1",
        source=source,
        external_id="1",
        company="ACME Inc",
        title="Full-Stack Developer",
        location="Toronto, ON",
        description=description,
        url="https://www.adzuna.ca/land/ad/1",
    )


def _seed(cfg: Config, job: Job, *, scored: bool = True) -> None:
    conn = connect(cfg.paths.db_path)
    with conn:
        upsert_job(conn, job)
        if scored:
            write_score(
                conn, job_id=job.id, score=70, reasons=[], red_flags=[],
                must_clarify=[], model="test", prompt_hash="x",
            )
    conn.close()


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the network seams; returns the list of fetched URLs."""
    fetch_calls: list[str] = []

    async def fake_resolve(client, url, limiter, **kw):  # noqa: ANN001, ANN003
        return url  # redirect already terminal — C1 has its own suite

    async def fake_fetch(url, *, user_agent, **kw):  # noqa: ANN001, ANN003
        fetch_calls.append(url)
        return _job(description=FULL_JD)

    monkeypatch.setattr(apply_cmd, "resolve_redirect", fake_resolve)
    monkeypatch.setattr(apply_cmd, "fetch_url_as_job", fake_fetch)
    monkeypatch.setattr(apply_cmd, "robots_allowed", lambda url, ua: True)
    return fetch_calls


@pytest.mark.asyncio
async def test_thin_adzuna_enriched_and_score_invalidated(cfg: Config, stubs: list[str]) -> None:
    job = _job()
    _seed(cfg, job)

    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out.description == FULL_JD
    assert stubs == [job.url]
    conn = connect(cfg.paths.db_path)
    desc = conn.execute("SELECT description FROM jobs WHERE id = ?", (job.id,)).fetchone()[0]
    n_scores = conn.execute("SELECT COUNT(*) FROM scores WHERE job_id = ?", (job.id,)).fetchone()[0]
    conn.close()
    assert desc == FULL_JD
    assert n_scores == 0


@pytest.mark.asyncio
async def test_fat_adzuna_not_fetched(cfg: Config, stubs: list[str]) -> None:
    job = _job(description="x" * (cfg.pipeline.thin_jd_chars + 1))
    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out is job
    assert stubs == []


@pytest.mark.asyncio
async def test_non_adzuna_not_fetched(cfg: Config, stubs: list[str]) -> None:
    job = _job(source="greenhouse")
    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out is job
    assert stubs == []


@pytest.mark.asyncio
async def test_robots_denial_keeps_snippet(
    cfg: Config,
    stubs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apply_cmd, "robots_allowed", lambda url, ua: False)
    job = _job()
    _seed(cfg, job)
    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out.description == SNIPPET
    assert stubs == []


@pytest.mark.asyncio
async def test_fetch_failure_keeps_snippet(
    cfg: Config,
    stubs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(url, *, user_agent, **kw):  # noqa: ANN001, ANN003
        raise RuntimeError("render timeout")

    monkeypatch.setattr(apply_cmd, "fetch_url_as_job", boom)
    job = _job()
    _seed(cfg, job)
    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out.description == SNIPPET
    conn = connect(cfg.paths.db_path)
    n_scores = conn.execute("SELECT COUNT(*) FROM scores WHERE job_id = ?", (job.id,)).fetchone()[0]
    conn.close()
    assert n_scores == 1  # score untouched when nothing changed


@pytest.mark.asyncio
async def test_shorter_fetch_result_keeps_snippet(
    cfg: Config,
    stubs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def tiny(url, *, user_agent, **kw):  # noqa: ANN001, ANN003
        return _job(description="tiny")

    monkeypatch.setattr(apply_cmd, "fetch_url_as_job", tiny)
    job = _job()
    _seed(cfg, job)
    out = await apply_cmd._deepen_thin_adzuna(cfg, job)
    assert out.description == SNIPPET

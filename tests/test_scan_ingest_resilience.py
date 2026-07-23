"""Ingest must not deadlock when a single source misbehaves.

Regression for the scan hang: `_safe_stream` only caught `IngestError`, so a
raw `httpx.HTTPStatusError` (a non-404 4xx like a 403 bot-wall) or a
`JSONDecodeError` (non-JSON body) escaping the HTTP helpers killed the
producer task. `closer()` then re-raised at `gather(return_exceptions=False)`
before enqueuing the `None` sentinel, and the drain loop blocked on
`queue.get()` forever. No network, no Ollama.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

import jobhunt.commands.scan_cmd as scan_cmd
from jobhunt.config import Config, IngestConfig, PathsConfig
from jobhunt.db import connect, migrate
from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json
from jobhunt.models import Job


class _NoSecrets:
    adzuna_app_id = None
    adzuna_app_key = None


def _job(slug: str) -> Job:
    return Job(
        id=f"greenhouse:{slug}:1",
        source="greenhouse",
        external_id="1",
        company=slug,
        title="Software Developer",
        location="Toronto, ON",
        description="Need Python.",
        url=f"https://boards.greenhouse.io/{slug}/jobs/1",
    )


async def test_ingest_survives_adapter_crash(
    tmp_path: Path, migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One slug raising a non-IngestError must not hang the scan: the bad slug
    is reported as failed and the healthy slug still ingests."""

    async def fake_fetch(
        client: httpx.AsyncClient, limiter: RateLimiter, slug: str
    ) -> AsyncIterator[Job]:
        if slug == "bad":
            # Stand-in for a raw httpx.HTTPStatusError / JSONDecodeError
            # escaping the HTTP helpers — the class of bug that deadlocked ingest.
            raise RuntimeError("boom")
        yield _job(slug)

    monkeypatch.setattr(scan_cmd.greenhouse, "fetch", fake_fetch)
    monkeypatch.setattr(scan_cmd, "load_secrets", lambda: _NoSecrets())

    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "test.db",
            kb_dir=tmp_path / "kb",
            migrations_dir=migrations_dir,
        ),
        ingest=IngestConfig(greenhouse=["good", "bad"], rate_limit_per_sec=0.0),
    )
    conn = connect(cfg.paths.db_path)
    migrate(conn, migrations_dir)
    try:
        try:
            inserted, per_source, _filtered = await asyncio.wait_for(
                scan_cmd._ingest_all(cfg, conn, max_age_days=7), timeout=5.0
            )
        except TimeoutError:
            pytest.fail(
                "ingest deadlocked: an adapter crash was not contained "
                "(producer died, drain loop never received its sentinel)"
            )
    finally:
        conn.close()

    assert inserted == 1
    failed = [(label, err) for _s, label, _n, err in per_source if err is not None]
    assert failed == [("bad", "RuntimeError: boom")]
    healthy = [(label, n) for _s, label, n, err in per_source if err is None]
    assert healthy == [("good", 1)]


def _client(status: int, *, text: str | None = None, json_body: object = None) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(status, json=json_body)
        return httpx.Response(status, text=text or "")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_json_403_becomes_ingesterror() -> None:
    """A 403 (common ATS bot-wall) must surface as IngestError, not a raw
    HTTPStatusError that would escape _safe_stream."""
    async with _client(403, text="forbidden") as client:
        with pytest.raises(IngestError, match="403"):
            await get_json(client, "https://x.test/api", RateLimiter(0.0))


async def test_get_json_non_json_body_becomes_ingesterror() -> None:
    """A 200 with a non-JSON body must surface as IngestError, not a raw
    JSONDecodeError."""
    async with _client(200, text="<html>not json</html>") as client:
        with pytest.raises(IngestError, match="invalid JSON"):
            await get_json(client, "https://x.test/api", RateLimiter(0.0))


async def test_get_json_404_still_ingesterror() -> None:
    async with _client(404, text="nope") as client:
        with pytest.raises(IngestError, match="404"):
            await get_json(client, "https://x.test/api", RateLimiter(0.0))


async def test_get_json_ok_returns_parsed() -> None:
    async with _client(200, json_body={"jobs": [{"id": 1}]}) as client:
        data = await get_json(client, "https://x.test/api", RateLimiter(0.0))
    assert data == {"jobs": [{"id": 1}]}

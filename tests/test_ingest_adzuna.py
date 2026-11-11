"""Adzuna adapter: the Toronto-radius pass and the Canada-wide remote pass
(Phase R1, 2026-11-11).

Adzuna locations are always a city, so a remote job based outside the GTA
never reached the queue: the main pass searches 100 km around the applicant's
city and `is_gta_eligible` drops everything else. The remote pass searches
Canada-wide for "<query> remote" and keeps only postings whose text says
remote. The fixture mirrors the API's response shape; no network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import jobhunt.commands.scan_cmd as scan_cmd
from jobhunt.config import AdzunaConfig, Config, IngestConfig, PathsConfig
from jobhunt.db import connect, migrate
from jobhunt.http import RateLimiter
from jobhunt.ingest import adzuna_ca
from jobhunt.models import Job

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "adzuna_ca.json").read_text(encoding="utf-8")
)


async def _run(**kw: Any) -> tuple[list[Job], list[dict[str, str]]]:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = [
            j
            async for j in adzuna_ca.fetch(
                client,
                RateLimiter(0.0),
                app_id="id",
                app_key="key",
                query="cms developer",
                pages=1,
                **kw,
            )
        ]
    return jobs, seen


async def test_main_pass_is_unchanged() -> None:
    jobs, seen = await _run(where="Toronto")
    assert [j.id for j in jobs] == ["adzuna_ca:5001"]
    assert (seen[0]["where"], seen[0]["distance"], seen[0]["what"]) == (
        "Toronto",
        "100",
        "cms developer",
    )


async def test_remote_pass_searches_canada_wide_for_remote_roles() -> None:
    _, seen = await _run(where="", remote=True)
    assert "where" not in seen[0] and "distance" not in seen[0]
    assert seen[0]["what"] == "cms developer remote"


async def test_remote_pass_keeps_only_remote_postings() -> None:
    """Toronto on-site, Calgary on-site and Halifax hybrid are dropped; the
    Vancouver remote role, which the radius pass can never see, is kept."""
    jobs, _ = await _run(where="", remote=True)
    assert [(j.id, j.remote_type) for j in jobs] == [("adzuna_ca:5002", "remote")]


def test_remote_pages_defaults_to_one() -> None:
    assert AdzunaConfig().remote_pages == 1


# --- scan wiring ----------------------------------------------------------------


class _Secrets:
    adzuna_app_id = "id"
    adzuna_app_key = "key"


async def _adapters(
    tmp_path: Path, migrations_dir: Path, monkeypatch: pytest.MonkeyPatch, remote_pages: int
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_fetch(_client: Any, _limiter: Any, **kw: Any) -> AsyncIterator[Job]:
        calls.append(kw)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(scan_cmd.adzuna_ca, "fetch", fake_fetch)
    monkeypatch.setattr(scan_cmd, "load_secrets", lambda: _Secrets())
    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "t.db",
            kb_dir=tmp_path / "kb",
            migrations_dir=migrations_dir,
        ),
        ingest=IngestConfig(
            rate_limit_per_sec=0.0,
            adzuna=AdzunaConfig(queries=["shopify developer"], remote_pages=remote_pages),
        ),
    )
    cfg.applicant.city = "Toronto"
    conn = connect(cfg.paths.db_path)
    migrate(conn, migrations_dir)
    try:
        await asyncio.wait_for(scan_cmd._ingest_all(cfg, conn, max_age_days=7), timeout=5.0)
    finally:
        conn.close()
    return calls


async def test_scan_adds_one_remote_pass_per_query(
    tmp_path: Path, migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _adapters(tmp_path, migrations_dir, monkeypatch, remote_pages=1)
    assert sorted((c["query"], c.get("remote", False), c["pages"]) for c in calls) == [
        ("shopify developer", False, 3),
        ("shopify developer", True, 1),
    ]


async def test_remote_pages_zero_turns_the_pass_off(
    tmp_path: Path, migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _adapters(tmp_path, migrations_dir, monkeypatch, remote_pages=0)
    assert [c.get("remote", False) for c in calls] == [False]

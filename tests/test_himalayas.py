"""Himalayas public jobs API adapter (Phase P2, 2026-11-17). No network.

The fixture is a trimmed capture of /jobs/api/search (descriptions cut to 600
chars). It holds one job restricted to a list including Canada, one worldwide
job, one worldwide job whose title says U.S. based, and one US-only job.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from jobhunt.ingest import himalayas
from jobhunt.models import Job

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "himalayas.json").read_text())


def _drain(
    cache_dir: Path, calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> list[Job]:
    async def fake_get_json(client: Any, url: str, limiter: Any, *, params: Any = None) -> Any:
        calls.append({"url": url, "params": dict(params or {})})
        return FIXTURE

    monkeypatch.setattr(himalayas, "get_json", fake_get_json)

    async def run() -> list[Job]:
        return [j async for j in himalayas.fetch(None, None, "shopify", cache_dir=cache_dir)]  # type: ignore[arg-type]

    return asyncio.run(run())


def test_keeps_canada_listed_and_clean_worldwide_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = _drain(tmp_path, [], monkeypatch)
    titles = [j.title for j in jobs]
    assert any(t.startswith("Shopify Services") for t in titles)
    assert "Shopify Developer" in titles
    assert not any("U.S. Based" in t for t in titles)
    assert "WordPress Front-End Developer / Engineer" not in titles


def test_job_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = next(j for j in _drain(tmp_path, [], monkeypatch) if j.title == "Shopify Developer")
    assert job.source == "himalayas"
    assert job.id == "himalayas:hivex/shopify-developer-8397197847"
    assert job.company
    assert job.remote_type == "remote"
    assert job.url and job.url.startswith("https://himalayas.app/companies/hivex/jobs/")
    assert "<" not in (job.description or "")
    assert job.posted_at is not None and job.posted_at.year >= 2026
    assert job.location == "Remote (worldwide)"


def test_canada_listed_job_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = next(
        j for j in _drain(tmp_path, [], monkeypatch) if j.title.startswith("Shopify Services")
    )
    assert job.location == "Remote (Canada)"


def test_search_asks_for_canada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _drain(tmp_path, calls, monkeypatch)
    assert calls[0]["url"] == himalayas.SEARCH_API
    assert calls[0]["params"] == {"q": "shopify", "country": "CA"}


def test_second_scan_within_a_day_uses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    first = _drain(tmp_path, calls, monkeypatch)
    second = _drain(tmp_path, calls, monkeypatch)
    assert len(calls) == 1
    assert [j.id for j in first] == [j.id for j in second]


def test_cache_older_than_a_day_is_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    _drain(tmp_path, calls, monkeypatch)
    for f in tmp_path.rglob("*.json"):
        old = time.time() - himalayas.CACHE_TTL_SECONDS - 60
        os.utime(f, (old, old))
    _drain(tmp_path, calls, monkeypatch)
    assert len(calls) == 2


def test_config_has_himalayas_list() -> None:
    from jobhunt.config import Config

    assert Config().ingest.himalayas == []

"""Himalayas public remote-jobs API (2026-11-17). Key-less.

https://himalayas.app/docs/remote-jobs-api. Terms checked 2026-11-17: robots.txt
allows `/jobs/api`, and jobs may be shown in your own app with visible
Himalayas credit and a link back, Apply going through the API's
`applicationLink`, and no passing jobs on to other boards. Unkeyed calls share
one public allowance and the data refreshes daily, so every search response is
cached for `CACHE_TTL_SECONDS` and a scan re-fetches at most once a day.

`[ingest] himalayas` holds search terms. Each runs once against the search
endpoint with `country=CA`. A job restricted to a list of countries is kept
only when Canada is on it. A worldwide job (empty list) is kept unless its
title or description names a region outside Canada, the same rule as remote
RSS boards (`rss_generic._passes_remote_board`).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._rss import strip_html
from jobhunt.ingest.rss_generic import _passes_remote_board
from jobhunt.models import Job

SOURCE = "himalayas"
SEARCH_API = "https://himalayas.app/jobs/api/search"
# GTA scope plus Remote-Canada (AGENTS ingestion rule 2).
COUNTRY = "CA"
COUNTRY_NAME = "Canada"
CACHE_TTL_SECONDS = 24 * 60 * 60
_GUID_PREFIX = "https://himalayas.app/companies/"


async def _search(
    client: httpx.AsyncClient, limiter: RateLimiter, query: str, cache_dir: Path
) -> Any:
    """One search response, from the cache when it is under a day old."""
    params = {"q": query, "country": COUNTRY}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    path = cache_dir / SOURCE / f"{key}.json"
    if path.is_file() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(path.read_text(encoding="utf-8"))
    data = await get_json(client, SEARCH_API, limiter, params=params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _eligible(j: dict[str, Any], description: str) -> bool:
    countries = [str(c) for c in j.get("locationRestrictions") or []]
    if countries:
        return COUNTRY_NAME in countries
    return _passes_remote_board(f"{j.get('title') or ''}\n{description}")


def _posted(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


async def fetch(
    client: httpx.AsyncClient, limiter: RateLimiter, query: str, *, cache_dir: Path
) -> AsyncIterator[Job]:
    data = await _search(client, limiter, query, cache_dir)
    if not isinstance(data, dict):
        return
    for j in data.get("jobs", []):
        if not isinstance(j, dict) or not j.get("title"):
            continue
        description = strip_html(j.get("description")) or ""
        if not _eligible(j, description):
            continue
        guid = str(j.get("guid") or "")
        ext = guid.removeprefix(_GUID_PREFIX).replace("/jobs/", "/") if guid else ""
        if not ext:
            continue
        located = COUNTRY_NAME if j.get("locationRestrictions") else "worldwide"
        yield Job(
            id=f"{SOURCE}:{ext}",
            source=SOURCE,
            external_id=ext,
            company=j.get("companyName"),
            title=j.get("title"),
            location=f"Remote ({located})",
            remote_type="remote",
            description=description or None,
            # Apply must go through Himalayas' own link (API terms).
            url=j.get("applicationLink") or guid,
            posted_at=_posted(j.get("pubDate")),
            raw_json=json.dumps(j),
        )

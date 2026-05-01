"""Ashby public job-board API. https://api.ashbyhq.com/posting-api/job-board/<slug>"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import is_gta_eligible
from jobhunt.models import Job

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    params = {"includeCompensation": "false"}
    data = await get_json(client, API.format(slug=slug), limiter, params=params)
    if not isinstance(data, dict):
        return
    for j in data.get("jobs", []):
        location = j.get("locationName") or j.get("location")
        if j.get("isRemote") and location and "remote" not in location.lower():
            location = f"{location} (Remote)"
        if not is_gta_eligible(location):
            continue
        ext = str(j.get("id"))
        yield Job(
            id=f"ashby:{slug}:{ext}",
            source="ashby",
            external_id=ext,
            company=slug,
            title=j.get("title"),
            location=location,
            description=j.get("descriptionPlain") or j.get("descriptionHtml"),
            url=j.get("jobUrl") or j.get("applyUrl"),
            posted_at=_parse_dt(j.get("publishedAt")),
            raw_json=json.dumps(j),
        )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

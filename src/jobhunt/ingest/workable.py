"""Workable public widget API. https://apply.workable.com/api/v1/widget/accounts/<slug>

Returns ``{"jobs": [...]}``. Key-less; the same endpoint powers Workable's
embedded job-board widget.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    data = await get_json(client, API.format(slug=slug), limiter)
    if not isinstance(data, dict):
        return
    for j in data.get("jobs", []):
        location = _format_location(j)
        if j.get("telecommuting") and location and "remote" not in location.lower():
            location = f"{location} (Remote)"
        if not is_gta_eligible(location):
            continue
        ext = str(j.get("shortcode") or j.get("id") or "")
        if not ext:
            continue
        rt = "remote" if j.get("telecommuting") else classify_remote_type(location=location)
        yield Job(
            id=f"workable:{slug}:{ext}",
            source="workable",
            external_id=ext,
            company=j.get("company") or slug,
            title=j.get("title"),
            location=location,
            remote_type=rt,
            description=j.get("description") or j.get("requirements"),
            url=j.get("url") or j.get("application_url"),
            posted_at=_parse_dt(j.get("published_on") or j.get("created_at")),
            raw_json=json.dumps(j),
        )


def _format_location(j: dict[str, Any]) -> str | None:
    """Workable nests location fields. Reduce to a single comma-joined string."""
    loc = j.get("location") or {}
    parts = [
        loc.get("city"),
        loc.get("region"),
        loc.get("country"),
    ]
    text = ", ".join(p for p in parts if p)
    return text or None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

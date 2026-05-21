"""Recruitee public offers API. https://<slug>.recruitee.com/api/offers/

Returns ``{"offers": [...]}``. Key-less.
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

API = "https://{slug}.recruitee.com/api/offers/"


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    data = await get_json(client, API.format(slug=slug), limiter)
    if not isinstance(data, dict):
        return
    for j in data.get("offers", []):
        location = _format_location(j)
        if j.get("remote") and location and "remote" not in location.lower():
            location = f"{location} (Remote)"
        elif j.get("remote") and not location:
            location = "Remote"
        if not is_gta_eligible(location):
            continue
        ext = str(j.get("id") or j.get("slug") or "")
        if not ext:
            continue
        rt = "remote" if j.get("remote") else classify_remote_type(location=location)
        yield Job(
            id=f"recruitee:{slug}:{ext}",
            source="recruitee",
            external_id=ext,
            company=(j.get("company_name") or slug),
            title=j.get("title"),
            location=location,
            remote_type=rt,
            description=j.get("description") or j.get("requirements"),
            url=j.get("careers_url") or j.get("careers_apply_url"),
            posted_at=_parse_dt(j.get("published_at") or j.get("created_at")),
            raw_json=json.dumps(j),
        )


def _format_location(j: dict[str, Any]) -> str | None:
    """Recruitee exposes locations as either a flat 'location' string or a
    structured 'locations' list of dicts. Coalesce to a single string."""
    flat = j.get("location")
    if isinstance(flat, str) and flat.strip():
        return flat
    locs = j.get("locations") or []
    if isinstance(locs, list) and locs:
        parts = []
        for loc in locs:
            if isinstance(loc, dict):
                bits = [loc.get("city"), loc.get("state"), loc.get("country")]
                parts.append(", ".join(b for b in bits if b))
            elif isinstance(loc, str):
                parts.append(loc)
        joined = "; ".join(p for p in parts if p)
        return joined or None
    city = j.get("city")
    country = j.get("country")
    text = ", ".join(p for p in (city, country) if p)
    return text or None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

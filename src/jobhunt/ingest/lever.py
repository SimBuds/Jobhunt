"""Lever public postings API. https://api.lever.co/v0/postings/<slug>?mode=json"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

API = "https://api.lever.co/v0/postings/{slug}"


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    data = await get_json(client, API.format(slug=slug), limiter, params={"mode": "json"})
    if not isinstance(data, list):
        return
    for j in data:
        cats = j.get("categories") or {}
        location = cats.get("location")
        commitment = cats.get("commitment")
        if commitment and "remote" in commitment.lower():
            location = f"{location or ''} Remote".strip()
        if not is_gta_eligible(location):
            continue
        ext = str(j.get("id"))
        descr = (j.get("descriptionPlain") or j.get("description") or "").strip()
        yield Job(
            id=f"lever:{slug}:{ext}",
            source="lever",
            external_id=ext,
            company=slug,
            title=j.get("text"),
            location=location,
            remote_type=classify_remote_type(location=location, extra=commitment),
            description=descr or None,
            url=j.get("hostedUrl") or j.get("applyUrl"),
            posted_at=_from_ms(j.get("createdAt")),
            raw_json=json.dumps(j),
        )


def _from_ms(ms: int | None) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None

"""Generic RSS/Atom adapter for company career feeds.

Feeds are configured per-URL under `[ingest] rss = [...]` in config.toml. The
adapter assumes <title> contains the role and <description> contains enough
text to score against. Location is best-effort: if the description mentions a
GTA city or 'Remote (Canada)' it passes the filter; otherwise it's dropped.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx

from jobhunt.http import RateLimiter
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.ingest._rss import fetch_feed, parse_feed
from jobhunt.models import Job

SOURCE = "rss"


async def fetch(
    client: httpx.AsyncClient, limiter: RateLimiter, feed_url: str
) -> AsyncIterator[Job]:
    xml = await fetch_feed(client, feed_url, limiter)
    for item in parse_feed(xml):
        if not item.title:
            continue
        # Generic feeds rarely separate location; rely on the description blob.
        location_blob = item.description or item.title or ""
        if not is_gta_eligible(location_blob):
            continue
        ext = item.guid or hashlib.sha1((item.link or item.title).encode("utf-8")).hexdigest()[:16]
        yield Job(
            id=f"{SOURCE}:{ext}",
            source=SOURCE,
            external_id=ext,
            company=None,
            title=item.title,
            location=location_blob[:120] if location_blob else None,
            remote_type=classify_remote_type(location=location_blob),
            description=item.description,
            url=item.link,
            posted_at=item.pub_date,
            raw_json=None,
        )

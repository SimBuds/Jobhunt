"""Government of Canada Job Bank RSS adapter.

Job Bank exposes a public RSS feed per search query, e.g.:

    https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=developer
        &locationstring=Toronto%2C+ON&fage=7&sort=M&format=rss

The user puts the full feed URLs in `~/.config/jobhunt/config.toml` under
`[ingest.job_bank_ca] feeds = [...]` so query terms can be tuned without
shipping code changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx

from jobhunt.http import RateLimiter
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.ingest._rss import fetch_feed, parse_feed
from jobhunt.models import Job

SOURCE = "job_bank_ca"


async def fetch(
    client: httpx.AsyncClient, limiter: RateLimiter, feed_url: str
) -> AsyncIterator[Job]:
    xml = await fetch_feed(client, feed_url, limiter)
    for item in parse_feed(xml):
        if not item.title or not item.link:
            continue
        # Job Bank embeds "Title - Employer - City, Province" in <title>.
        company, title, location = _split_title(item.title)
        if not is_gta_eligible(location):
            continue
        ext = item.guid or hashlib.sha1(item.link.encode("utf-8")).hexdigest()[:16]
        yield Job(
            id=f"{SOURCE}:{ext}",
            source=SOURCE,
            external_id=ext,
            company=company,
            title=title,
            location=location,
            remote_type=classify_remote_type(location=location, extra=item.description),
            description=item.description,
            url=item.link,
            posted_at=item.pub_date,
            raw_json=None,
        )


def _split_title(raw: str) -> tuple[str | None, str | None, str | None]:
    """Job Bank titles look like 'web developer - ACME Inc - Toronto (ON)'."""
    parts = [p.strip() for p in raw.split(" - ")]
    if len(parts) >= 3:
        return parts[1], parts[0], parts[-1]
    if len(parts) == 2:
        return parts[1], parts[0], None
    return None, parts[0] if parts else None, None

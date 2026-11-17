"""Generic RSS/Atom adapter for company career feeds.

Feeds are configured per-URL under `[ingest] rss = [...]` in config.toml. The
adapter assumes <title> contains the role and <description> contains enough
text to score against. Location is best-effort: if the description mentions a
GTA city or 'Remote (Canada)' it passes the filter; otherwise it's dropped.

Feeds under `[ingest] rss_remote` are remote-only or Canada-filtered boards.
Their items also pass when the description names no region outside Canada,
because those boards often carry no location text at all.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator

import httpx

from jobhunt.http import RateLimiter
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.ingest._rss import fetch_feed, parse_feed
from jobhunt.models import Job, RemoteType

SOURCE = "rss"

# Whole-description veto for remote boards. The US forms are case-sensitive so
# the pronoun ("join us") does not match. A body mention such as "our US
# customers" also vetoes: a dropped posting costs less than a wrong pass.
_REMOTE_RESTRICTION = re.compile(
    r"(?<![\w.])(?:US(?:A|-based)?|U\.S\.(?:A\.)?)(?![\w-])"
    r"|(?i:\b(?:united states|uk|united kingdom|europe|european|emea|latam"
    r"|latin america|apac|asia|australia|india)\b)"
)


def _passes_remote_board(text: str) -> bool:
    return is_gta_eligible(text) or _REMOTE_RESTRICTION.search(text) is None


async def fetch(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    feed_url: str,
    *,
    remote_board: bool = False,
) -> AsyncIterator[Job]:
    xml = await fetch_feed(client, feed_url, limiter)
    for item in parse_feed(xml):
        if not item.title:
            continue
        # Generic feeds rarely separate location; rely on the description blob.
        location_blob = item.description or item.title or ""
        remote_type: RemoteType
        if remote_board:
            if not _passes_remote_board(location_blob):
                continue
            remote_type = "remote"
        elif is_gta_eligible(location_blob):
            remote_type = classify_remote_type(location=location_blob)
        else:
            continue
        ext = item.guid or hashlib.sha1((item.link or item.title).encode("utf-8")).hexdigest()[:16]
        yield Job(
            id=f"{SOURCE}:{ext}",
            source=SOURCE,
            external_id=ext,
            company=None,
            title=item.title,
            location=location_blob[:120] if location_blob else None,
            remote_type=remote_type,
            description=item.description,
            url=item.link,
            posted_at=item.pub_date,
            raw_json=None,
        )

"""Minimal RSS / Atom parser using stdlib xml.etree.

We avoid adding feedparser as a dep; the feeds we read (Job Bank Canada, generic
employer career RSS) all return well-formed RSS 2.0 or Atom 1.0.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, host_of

ATOM_NS = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class RSSItem:
    title: str | None
    link: str | None
    description: str | None
    pub_date: datetime | None
    guid: str | None


def strip_html(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(xml_text: str) -> Iterator[RSSItem]:
    """Yield RSSItem regardless of RSS 2.0 vs Atom 1.0."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise IngestError(f"feed parse error: {e}") from e

    # RSS 2.0: <rss><channel><item>…</item></channel></rss>
    for item in root.iter("item"):
        yield RSSItem(
            title=(item.findtext("title") or "").strip() or None,
            link=(item.findtext("link") or "").strip() or None,
            description=strip_html(item.findtext("description")),
            pub_date=_parse_dt(item.findtext("pubDate")),
            guid=(item.findtext("guid") or "").strip() or None,
        )

    # Atom 1.0: <feed><entry>…</entry></feed>
    for entry in root.iter(f"{ATOM_NS}entry"):
        link_el = entry.find(f"{ATOM_NS}link")
        link = link_el.get("href") if link_el is not None else None
        summary = entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content")
        yield RSSItem(
            title=(entry.findtext(f"{ATOM_NS}title") or "").strip() or None,
            link=link,
            description=strip_html(summary),
            pub_date=_parse_dt(
                entry.findtext(f"{ATOM_NS}updated") or entry.findtext(f"{ATOM_NS}published")
            ),
            guid=(entry.findtext(f"{ATOM_NS}id") or "").strip() or None,
        )


async def fetch_feed(
    client: httpx.AsyncClient,
    url: str,
    limiter: RateLimiter,
    *,
    max_retries: int = 3,
) -> str:
    """GET an RSS/Atom URL with backoff. Returns raw XML text."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        await limiter.wait(host_of(url))
        try:
            r = await client.get(url, headers={"Accept": "application/rss+xml, application/xml"})
        except httpx.HTTPError as e:
            last_exc = e
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 404:
            raise IngestError(f"404 {url}")
        r.raise_for_status()
        return r.text
    raise IngestError(f"failed after {max_retries} retries: {url} ({last_exc})")

"""Greenhouse boards-api adapter. https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import is_gta_eligible
from jobhunt.models import Job

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str | None:
    if not s:
        return s
    return _TAG_RE.sub(" ", s).replace("&nbsp;", " ").replace("&amp;", "&").strip()


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    data = await get_json(client, API.format(slug=slug), limiter, params={"content": "true"})
    if not isinstance(data, dict):
        return
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name")
        if not is_gta_eligible(location):
            continue
        ext = str(j.get("id"))
        yield Job(
            id=f"greenhouse:{slug}:{ext}",
            source="greenhouse",
            external_id=ext,
            company=slug,
            title=j.get("title"),
            location=location,
            description=_strip_html(j.get("content")),
            url=j.get("absolute_url"),
            posted_at=_parse_dt(j.get("updated_at")),
            raw_json=json.dumps(j),
        )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

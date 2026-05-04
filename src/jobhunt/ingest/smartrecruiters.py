"""SmartRecruiters public Posting API.

    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings

No auth required for public boards. Each posting has location.{city, country,
remote}, name, ref, applyUrl, jobAd.sections.jobDescription.text.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
SOURCE = "smartrecruiters"


async def fetch(client: httpx.AsyncClient, limiter: RateLimiter, slug: str) -> AsyncIterator[Job]:
    offset = 0
    page_size = 100
    while True:
        params = {"limit": str(page_size), "offset": str(offset)}
        data = await get_json(client, API.format(slug=slug), limiter, params=params)
        if not isinstance(data, dict):
            return
        items = data.get("content") or []
        if not items:
            return
        for j in items:
            location = _format_location(j.get("location"))
            if not is_gta_eligible(location):
                continue
            ext = str(j.get("id") or j.get("uuid") or "")
            if not ext:
                continue
            description = _extract_description(j)
            yield Job(
                id=f"{SOURCE}:{slug}:{ext}",
                source=SOURCE,
                external_id=ext,
                company=(j.get("company") or {}).get("name") or slug,
                title=j.get("name"),
                location=location,
                remote_type=classify_remote_type(location=location),
                description=description,
                url=j.get("applyUrl") or j.get("ref"),
                posted_at=_parse_dt(j.get("releasedDate") or j.get("createdOn")),
                raw_json=json.dumps(j),
            )
        total = int(data.get("totalFound") or 0)
        offset += page_size
        if offset >= total:
            return


def _format_location(loc: object) -> str | None:
    if not isinstance(loc, dict):
        return None
    parts: list[str] = []
    for key in ("city", "region", "country"):
        v = loc.get(key)
        if v:
            parts.append(str(v))
    base = ", ".join(parts) if parts else None
    if loc.get("remote"):
        base = f"{base} (Remote)" if base else "Remote"
    return base


def _extract_description(j: dict[str, object]) -> str | None:
    job_ad = j.get("jobAd")
    if not isinstance(job_ad, dict):
        return None
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return None
    chunks: list[str] = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key)
        if isinstance(sec, dict):
            text = sec.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n\n".join(chunks) or None


def _parse_dt(s: object) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

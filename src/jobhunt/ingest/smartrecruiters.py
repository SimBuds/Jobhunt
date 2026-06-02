"""SmartRecruiters public Posting API.

    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings        (list)
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}   (detail)

No auth required for public boards. The LIST endpoint returns only summary
metadata per posting (id, name, location, company, applyUrl, releasedDate) and
has NO `jobAd` field — the full description (`jobAd.sections.*.text`) lives only
on the per-posting DETAIL endpoint. The adapter therefore fetches detail for
each GTA-eligible posting that the non-engineering title gate doesn't drop.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json
from jobhunt.ingest._filter import (
    classify_remote_type,
    is_gta_eligible,
    is_non_engineering_title,
)
from jobhunt.models import Job

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{ext}"
SOURCE = "smartrecruiters"


async def fetch(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    slug: str,
    *,
    drop_non_eng: bool = True,
) -> AsyncIterator[Job]:
    """Yield GTA-eligible postings for a SmartRecruiters company board.

    The list endpoint carries no description, so for each kept posting the
    per-posting detail endpoint is fetched (see `_fetch_detail_description`).
    When `drop_non_eng` is True the detail fetch is skipped for titles the
    ingest non-engineering filter will drop anyway, so a hospital tenant like
    UHN doesn't spend a request per clinical role. `scan_cmd` stays the single
    drop authority, so those rows are still yielded (description=None) and
    dropped + counted there.
    """
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
            title = j.get("name")
            description = _extract_description(j)
            if description is None and not (drop_non_eng and is_non_engineering_title(title)):
                description = await _fetch_detail_description(client, limiter, slug, ext)
            yield Job(
                id=f"{SOURCE}:{slug}:{ext}",
                source=SOURCE,
                external_id=ext,
                company=(j.get("company") or {}).get("name") or slug,
                title=title,
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


async def _fetch_detail_description(
    client: httpx.AsyncClient, limiter: RateLimiter, slug: str, ext: str
) -> str | None:
    """Fetch a posting's detail endpoint and return its description text.

    The list endpoint omits `jobAd`; the description lives only on
    `/postings/{id}`. Returns None on any `IngestError` (404 / transient) so a
    single bad posting degrades gracefully instead of aborting the slug's
    stream — a later scan re-fetches it on the next upsert.
    """
    try:
        detail = await get_json(client, DETAIL_API.format(slug=slug, ext=ext), limiter)
    except IngestError:
        return None
    if not isinstance(detail, dict):
        return None
    return _extract_description(detail)


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

"""Workday CXS public-search adapter.

Workday-hosted career sites expose a public CXS endpoint that the employer's
own React career portal calls from the browser. We hit the same endpoint:

    POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

with a small JSON body. Tenants are configured explicitly per company in
`config.toml` — we never crawl to discover them. Targets the Toronto employer
base (RBC, TD, BMO, CIBC, Scotia, Manulife, Sun Life, Telus, Bell, Rogers,
Loblaw Digital, Thomson Reuters), most of which run on Workday.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, post_json
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

_PAGE_LIMIT = 20


def _parse_tenant(spec: str) -> tuple[str, str, str]:
    """Parse a 'tenant:host:site' config string. Example: 'rbc:wd3:RBC_Careers'."""
    parts = spec.split(":")
    if len(parts) != 3 or not all(parts):
        raise IngestError(
            f"workday tenant spec must be 'tenant:host:site' (e.g. 'rbc:wd3:RBC_Careers'), "
            f"got {spec!r}"
        )
    return parts[0], parts[1], parts[2]


def _location_text(item: dict[str, Any]) -> str | None:
    loc = item.get("locationsText") or item.get("bulletFields") or None
    if isinstance(loc, list):
        return ", ".join(str(x) for x in loc) or None
    return loc if isinstance(loc, str) else None


async def fetch(
    client: httpx.AsyncClient, limiter: RateLimiter, spec: str, *, max_pages: int = 5
) -> AsyncIterator[Job]:
    tenant, host, site = _parse_tenant(spec)
    base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    url = f"{base}/jobs"

    for page in range(max_pages):
        body = {
            "appliedFacets": {},
            "limit": _PAGE_LIMIT,
            "offset": page * _PAGE_LIMIT,
            "searchText": "",
        }
        data = await post_json(client, url, limiter, json_body=body)
        if not isinstance(data, dict):
            return
        postings = data.get("jobPostings") or []
        if not postings:
            return
        for p in postings:
            location = _location_text(p)
            if not is_gta_eligible(location):
                continue
            ext_path = p.get("externalPath") or ""
            ext_id = ext_path.rsplit("/", 1)[-1] or p.get("bulletFields", [""])[0]
            if not ext_id:
                continue
            posting_url = f"https://{tenant}.{host}.myworkdayjobs.com{ext_path}"
            yield Job(
                id=f"workday:{tenant}:{ext_id}",
                source="workday",
                external_id=ext_id,
                company=tenant,
                title=p.get("title"),
                location=location,
                remote_type=classify_remote_type(location=location),
                description=p.get("shortDescription"),
                url=posting_url,
                raw_json=json.dumps(p),
            )
        if len(postings) < _PAGE_LIMIT:
            return

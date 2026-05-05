"""Adzuna CA — `where=Toronto&distance=100&country=ca`. https://developer.adzuna.com/"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json, resolve_redirect
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

API = "https://api.adzuna.com/v1/api/jobs/ca/search/{page}"


async def fetch(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    *,
    app_id: str,
    app_key: str,
    query: str,
    pages: int = 3,
    results_per_page: int = 50,
) -> AsyncIterator[Job]:
    if not app_id or not app_key:
        raise IngestError("Adzuna requires app_id and app_key in secrets.toml")
    for page in range(1, pages + 1):
        data = await get_json(
            client,
            API.format(page=page),
            limiter,
            params={
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": results_per_page,
                "what": query,
                "where": "Toronto",
                "distance": 100,
                "content-type": "application/json",
            },
        )
        if not isinstance(data, dict):
            return
        results = data.get("results", []) or []
        if not results:
            return
        for j in results:
            loc_obj = j.get("location") or {}
            loc_str = loc_obj.get("display_name") or " ".join(loc_obj.get("area", []))
            if not is_gta_eligible(loc_str):
                continue
            ext = str(j.get("id"))
            adzuna_url = j.get("redirect_url")
            # Adzuna's redirect_url is a tracking link that lands on
            # adzuna.ca/details/<id> and then bounces to the employer. Chase
            # the chain at ingest time so the apply pipeline opens the real
            # posting page (where the Apply form actually lives). On any
            # error we fall back to the original URL — never block ingest.
            final_url = (
                await resolve_redirect(client, adzuna_url, limiter)
                if adzuna_url
                else None
            )
            yield Job(
                id=f"adzuna_ca:{ext}",
                source="adzuna_ca",
                external_id=ext,
                company=(j.get("company") or {}).get("display_name"),
                title=j.get("title"),
                location=loc_str,
                remote_type=classify_remote_type(
                    location=loc_str, extra=j.get("title") or ""
                ),
                description=j.get("description"),
                url=final_url,
                posted_at=_parse_dt(j.get("created")),
                raw_json=json.dumps(j),
            )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

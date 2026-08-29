"""Adzuna CA — `where=<applicant.city>&distance=100&country=ca`.

https://developer.adzuna.com/
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json
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
    where: str,
    pages: int = 3,
    results_per_page: int = 50,
) -> AsyncIterator[Job]:
    """`where` is the Adzuna location anchor, paired with `distance=100`.

    Required rather than defaulted: it used to be the literal "Toronto", which
    silently ignored `config.applicant.city` and searched the GTA no matter what
    the user had configured. A default here would let that regress quietly, and
    the caller always has the applicant profile to hand.
    """
    if not where.strip():
        raise IngestError(
            "Adzuna requires a location: set applicant.city in config.toml "
            "(run `jobhunt setup`, or re-run `jobhunt convert-resume` to fill "
            "it from the resume contact line)."
        )
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
                "where": where,
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
            # Store Adzuna's tracking URL as-is. It redirects to the employer's
            # posting page, and the autofill browser follows the redirect chain
            # natively via Playwright — picking the ATS handler from page.url
            # after navigation. Resolving here would cost ~1 req/sec/host on
            # adzuna.ca for every job and dominate ingest time.
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
                url=j.get("redirect_url"),
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

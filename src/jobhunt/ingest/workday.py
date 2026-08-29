"""Workday CXS public-search adapter.

Workday-hosted career sites expose a public CXS endpoint that the employer's
own React career portal calls from the browser. We hit the same endpoint:

    POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

with a small JSON body. Tenants are configured explicitly per company in
`config.toml` — we never crawl to discover them. Targets the Toronto employer
base (RBC, TD, BMO, CIBC, Scotia, Manulife, Sun Life, Telus, Bell, Rogers,
Loblaw Digital, Thomson Reuters), most of which run on Workday.

Scan strategy is adaptive (see `_scan`): small boards get a blank first-100
walk, but large global boards (NVIDIA, Live Nation, Capital One) bury their
handful of GTA roles past the first 100 unsorted postings, so they instead
issue a union of GTA-targeted `searchText` queries. `is_gta_eligible` does the
precise filtering in both branches.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import httpx

from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json, post_json
from jobhunt.ingest._filter import classify_remote_type, is_gta_eligible
from jobhunt.models import Job

_PAGE_LIMIT = 20

# Boards at or under this many total postings keep the blank first-100 scan —
# their GTA roles surface early (Canada-centric tenants: TD, BMO, Moneris).
# Larger global boards bury GTA roles past offset 100, so they switch to the
# GTA search-term union below. See `_scan`.
_BLANK_SCAN_MAX = 200

# Location-targeted CXS `searchText` queries for large boards are built per-run
# from the applicant profile by `_filter.location_search_terms`, then unioned,
# deduped, and narrowed by `is_gta_eligible`. The bare country name is
# deliberately never used on its own: on some tenants it matched every posting
# (boilerplate), making it a useless discriminator.

# Per-tenant wall-clock budget. Workday tenants have no server-side GTA filter
# and giants like RBC/TD paginate through hundreds of global postings before
# yielding a handful of GTA hits — a single slow tenant used to stall the whole
# scan. Past this budget the adapter aborts cleanly via asyncio.TimeoutError,
# which _safe_stream catches and surfaces as a failed slug in the summary.
# 180s leaves room for per-posting detail fetches (rate-limited at ~1 req/s per
# host) on tenants with many GTA hits like Moneris.
_TENANT_BUDGET_SECONDS = 180.0

# Workday's CXS response carries `postedOn` as prose ("Posted Today",
# "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago").
# We parse it back to a timestamp so the freshness filter in scan_cmd
# (`max_age_days`) applies to Workday postings.
_DAYS_AGO_RE = re.compile(r"(\d+)\+?\s*days?\s*ago", re.IGNORECASE)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "li", "div", "tr"):
            self._parts.append("\n")

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts)).strip()


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.text()


async def _fetch_description(
    client: httpx.AsyncClient, limiter: RateLimiter, base: str, ext_path: str
) -> str | None:
    """Fetch the per-posting detail endpoint and return plain-text JD.

    The list endpoint's `shortDescription` is empty on most tenants; the real
    JD only lives on `{base}{externalPath}` as HTML under
    `jobPostingInfo.jobDescription`. Failures are swallowed (returns None) so
    one broken posting doesn't fail the tenant.
    """
    url = f"{base}{ext_path}"
    try:
        data = await get_json(client, url, limiter)
    except (IngestError, httpx.HTTPError):
        return None
    if not isinstance(data, dict):
        return None
    info = data.get("jobPostingInfo")
    if not isinstance(info, dict):
        return None
    html = info.get("jobDescription")
    if not isinstance(html, str) or not html.strip():
        return None
    return _strip_html(html) or None


def _parse_tenant(spec: str) -> tuple[str, str, str]:
    """Parse a 'tenant:host:site' config string. Example: 'rbc:wd3:RBC_Careers'."""
    parts = spec.split(":")
    if len(parts) != 3 or not all(parts):
        raise IngestError(f"invalid workday tenant spec {spec!r}; expected 'tenant:host:site'")
    return parts[0], parts[1], parts[2]


def _parse_posted_on(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """Map Workday's prose `postedOn` to an approximate posted-at timestamp.

    Returns None when value is falsy or unparseable — the adapter then leaves
    `Job.posted_at` as None and the freshness filter will treat the row as
    fresh (consistent with the pre-Phase-5 behavior for those rows).
    """
    if not value:
        return None
    now = now or datetime.now(UTC)
    v = value.strip().lower()
    if "today" in v or "just posted" in v:
        return now
    if "yesterday" in v:
        return now - timedelta(days=1)
    m = _DAYS_AGO_RE.search(v)
    if m:
        return now - timedelta(days=int(m.group(1)))
    return None


def _location_text(item: dict[str, Any]) -> str | None:
    loc = item.get("locationsText") or item.get("bulletFields") or None
    if isinstance(loc, list):
        return ", ".join(str(x) for x in loc) or None
    return loc if isinstance(loc, str) else None


def _body(search_text: str, offset: int) -> dict[str, Any]:
    return {
        "appliedFacets": {},
        "limit": _PAGE_LIMIT,
        "offset": offset,
        "searchText": search_text,
    }


async def fetch(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    spec: str,
    *,
    max_pages: int = 5,
    search_terms: tuple[str, ...] = (),
) -> AsyncIterator[Job]:
    """`search_terms` narrows large boards; build it with
    `_filter.location_search_terms` from the applicant profile. Empty means no
    narrowing is possible, and a large board falls back to a blank scan."""
    tenant, host, site = _parse_tenant(spec)
    base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    url = f"{base}/jobs"

    try:
        async with asyncio.timeout(_TENANT_BUDGET_SECONDS):
            async for job in _scan(
                client, limiter, url, base, tenant, host, max_pages, search_terms
            ):
                yield job
    except TimeoutError as e:
        raise IngestError(
            f"workday tenant {tenant} exceeded {_TENANT_BUDGET_SECONDS:.0f}s budget"
        ) from e


async def _scan(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    url: str,
    base: str,
    tenant: str,
    host: str,
    max_pages: int,
    search_terms: tuple[str, ...] = (),
) -> AsyncIterator[Job]:
    """Adaptive scan: a blank first-100 walk for small boards, or a union of
    location-targeted searchText queries for large ones.

    A single `seen` set spans the whole tenant run so a posting surfaced under
    multiple search terms (e.g. both a city and its region) yields once.

    A large board with no search terms still gets the blank walk: scanning wide
    and letting `is_gta_eligible` filter is strictly better than skipping the
    tenant, and it is what an unconfigured profile deserves.
    """
    seen: set[str] = set()

    # One probe page reports the board size and doubles as page 0 of a blank scan.
    first = await post_json(client, url, limiter, json_body=_body("", 0))
    if not isinstance(first, dict):
        return
    total = first.get("total") or 0

    if total <= _BLANK_SCAN_MAX or not search_terms:
        async for job in _walk(
            client, limiter, url, base, tenant, host, "", max_pages, seen, first_page=first
        ):
            yield job
        return

    for term in search_terms:
        async for job in _walk(client, limiter, url, base, tenant, host, term, max_pages, seen):
            yield job


async def _walk(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    url: str,
    base: str,
    tenant: str,
    host: str,
    search_text: str,
    max_pages: int,
    seen: set[str],
    *,
    first_page: dict[str, Any] | None = None,
) -> AsyncIterator[Job]:
    """Paginate one searchText query, emitting deduped GTA-eligible jobs."""
    for page in range(max_pages):
        if page == 0 and first_page is not None:
            data: Any = first_page
        else:
            data = await post_json(
                client, url, limiter, json_body=_body(search_text, page * _PAGE_LIMIT)
            )
        if not isinstance(data, dict):
            return
        postings = data.get("jobPostings") or []
        if not postings:
            return
        for p in postings:
            job = await _emit(client, limiter, base, tenant, host, p, seen)
            if job is not None:
                yield job
        if len(postings) < _PAGE_LIMIT:
            return


async def _emit(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    base: str,
    tenant: str,
    host: str,
    posting: dict[str, Any],
    seen: set[str],
) -> Job | None:
    """Turn one CXS posting into a Job, or None if non-GTA or already seen.

    Dedup is keyed on externalPath so the same posting reached under different
    search terms is emitted only once.
    """
    location = _location_text(posting)
    if not is_gta_eligible(location):
        return None
    ext_path = posting.get("externalPath") or ""
    ext_id = ext_path.rsplit("/", 1)[-1] or posting.get("bulletFields", [""])[0]
    if not ext_id:
        return None
    dedup_key = ext_path or ext_id
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    posting_url = f"https://{tenant}.{host}.myworkdayjobs.com{ext_path}"
    description = posting.get("shortDescription")
    if not description or not str(description).strip():
        description = await _fetch_description(client, limiter, base, ext_path)
    return Job(
        id=f"workday:{tenant}:{ext_id}",
        source="workday",
        external_id=ext_id,
        company=tenant,
        title=posting.get("title"),
        location=location,
        remote_type=classify_remote_type(location=location),
        description=description,
        url=posting_url,
        posted_at=_parse_posted_on(posting.get("postedOn")),
        raw_json=json.dumps(posting),
    )

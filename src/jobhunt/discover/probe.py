"""Probe public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters) to
discover slugs for known companies."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

import httpx

from jobhunt.config import Config
from jobhunt.discover.slug_candidates import candidates
from jobhunt.discover.url_extract import extract as extract_url
from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json


def _count_greenhouse_ashby(data: Any) -> int | None:
    """Both Greenhouse and Ashby return {"jobs": [...]}."""
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs")
    return len(jobs) if isinstance(jobs, list) else None


def _count_lever(data: Any) -> int | None:
    """Lever returns a top-level JSON array of postings."""
    return len(data) if isinstance(data, list) else None


def _count_smartrecruiters(data: Any) -> int | None:
    """SmartRecruiters returns {"content": [...], "totalFound": N}."""
    if not isinstance(data, dict):
        return None
    total = data.get("totalFound")
    if isinstance(total, int):
        return total
    content = data.get("content")
    return len(content) if isinstance(content, list) else None


_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
_SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"

# Each entry: (url_template, query_params, response_counter).
_ENDPOINTS: Mapping[str, tuple[str, Mapping[str, str], Callable[[Any], int | None]]] = {
    "greenhouse": (_GREENHOUSE_URL, {"content": "false"}, _count_greenhouse_ashby),
    "ashby": (_ASHBY_URL, {"includeCompensation": "false"}, _count_greenhouse_ashby),
    "lever": (_LEVER_URL, {"mode": "json", "limit": "1"}, _count_lever),
    "smartrecruiters": (_SMARTRECRUITERS_URL, {"limit": "1"}, _count_smartrecruiters),
}

# Per-company budget. asyncio.wait_for cap protects the run from a hung host.
_COMPANY_TIMEOUT_SECONDS = 15.0
# How many companies probe concurrently. Per-host rate-limit (1 req/sec) is the real
# throttle; this just bounds the queued task count.
_COMPANY_CONCURRENCY = 4


class ProbeOutcome(NamedTuple):
    company: str
    ats: str
    slug: str
    status: int  # 200 hit, 404 miss, 0 network/other error
    job_count: int | None


async def _probe_one(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    company: str,
    ats: str,
    slug: str,
) -> ProbeOutcome:
    url_tpl, params, counter = _ENDPOINTS[ats]
    url = url_tpl.format(slug=slug)
    try:
        data = await get_json(client, url, limiter, params=params, max_retries=1)
    except IngestError as e:
        status = 404 if str(e).startswith("404 ") else 0
        return ProbeOutcome(company, ats, slug, status, None)
    except (httpx.HTTPError, TimeoutError):
        return ProbeOutcome(company, ats, slug, 0, None)

    count = counter(data)
    if count is None:
        return ProbeOutcome(company, ats, slug, 0, None)
    if count == 0:
        # Board responds but has zero postings — functionally useless. Cache
        # as a miss so we don't keep re-surfacing it. (SmartRecruiters in
        # particular 200s with totalFound=0 for both empty and stale boards.)
        return ProbeOutcome(company, ats, slug, 404, 0)
    return ProbeOutcome(company, ats, slug, 200, count)


async def probe_company(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    company: str,
    *,
    atses: list[str],
    slugs_to_try: list[str],
) -> list[ProbeOutcome]:
    """Probe every (ats, slug) combination for one company. Returns all outcomes."""
    out: list[ProbeOutcome] = []
    for ats in atses:
        for slug in slugs_to_try:
            outcome = await _probe_one(client, limiter, company, ats, slug)
            out.append(outcome)
            # Short-circuit further slugs for this ATS once we have a hit — one
            # company won't legitimately own multiple Greenhouse boards.
            if outcome.status == 200:
                break
    return out


def _company_rows(conn: sqlite3.Connection, limit: int) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT company, COUNT(*) AS n
        FROM jobs
        WHERE company IS NOT NULL AND company != ''
        GROUP BY company
        ORDER BY n DESC, company ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _cached_misses(
    conn: sqlite3.Connection, company: str
) -> set[tuple[str, str]]:
    """(ats, slug) pairs previously probed for this company that did NOT hit."""
    rows = conn.execute(
        "SELECT ats, slug FROM slug_probes WHERE company = ? AND status != 200",
        (company,),
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _cached_hits(
    conn: sqlite3.Connection, atses: list[str]
) -> list[ProbeOutcome]:
    """Previously-discovered hits, regardless of company. Used to re-surface
    findings the user hasn't applied yet."""
    placeholders = ",".join("?" * len(atses))
    rows = conn.execute(
        f"SELECT company, ats, slug, job_count FROM slug_probes "
        f"WHERE status = 200 AND ats IN ({placeholders})",
        atses,
    ).fetchall()
    return [ProbeOutcome(r[0], r[1], r[2], 200, r[3]) for r in rows]


def _persist_outcomes(
    conn: sqlite3.Connection, outcomes: list[ProbeOutcome]
) -> None:
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO slug_probes
              (company, ats, slug, status, job_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(o.company, o.ats, o.slug, o.status, o.job_count) for o in outcomes],
        )


def harvest_urls(
    conn: sqlite3.Connection, atses: list[str]
) -> list[ProbeOutcome]:
    """Scan jobs.url for confirmed ATS slugs. Offline, deterministic — no HTTP.

    Returns one ProbeOutcome(status=200, job_count=N) per (ats, slug) pair
    seen in the URLs, where N is the number of jobs whose URL produced it.
    The "company" is the most frequent jobs.company associated with that URL.
    iCIMS is recognized by the extractor but never returned here unless caller
    includes "icims" in `atses` — only probe-supported ATSes are surfaced.
    """
    rows = conn.execute(
        "SELECT url, company FROM jobs WHERE url IS NOT NULL AND url != ''"
    ).fetchall()

    targets = set(atses)
    # (ats, slug) -> {company -> count}
    tallies: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        ext = extract_url(r[0])
        if ext is None or ext.ats not in targets:
            continue
        key = (ext.ats, ext.slug)
        co = (r[1] or "").strip() or ext.slug
        tallies.setdefault(key, {})[co] = tallies.setdefault(key, {}).get(co, 0) + 1

    out: list[ProbeOutcome] = []
    for (ats, slug), companies in tallies.items():
        top_company = max(companies.items(), key=lambda kv: kv[1])[0]
        total = sum(companies.values())
        out.append(ProbeOutcome(top_company, ats, slug, 200, total))
    return out


class ProgressEvent(NamedTuple):
    company: str
    outcomes: list[ProbeOutcome]
    probed: int   # companies finished so far
    total: int    # total companies queued


async def discover(
    client: httpx.AsyncClient,
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    atses: list[str],
    limit: int,
    include_cached: bool,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> list[ProbeOutcome]:
    """Run slug discovery against the jobs DB. Returns 200-status outcomes only."""
    limiter = RateLimiter(rate_per_sec=cfg.ingest.rate_limit_per_sec)
    sem = asyncio.Semaphore(_COMPANY_CONCURRENCY)

    # Offline pass: pull slugs out of existing jobs.url values. Persist so
    # subsequent runs treat them as cached hits and skip re-deriving. Tracked
    # separately from `known` (config) so harvested slugs still surface as
    # "unapplied" suggestions; tracked here only to short-circuit the live
    # probe loop, which would otherwise overwrite these 200s with 404s under
    # the slug_probes (company, ats, slug) primary key.
    url_hits = harvest_urls(conn, atses)
    harvested_slugs: set[str] = set()
    if url_hits:
        _persist_outcomes(conn, url_hits)
        harvested_slugs = {h.slug for h in url_hits}

    known: dict[str, set[str]] = {
        "greenhouse": set(cfg.ingest.greenhouse),
        "ashby": set(cfg.ingest.ashby),
        "lever": set(cfg.ingest.lever),
        "smartrecruiters": set(cfg.ingest.smartrecruiters),
    }
    all_known: set[str] = set().union(*known.values())

    async def _bounded(company: str, slugs: list[str]) -> list[ProbeOutcome]:
        async with sem:
            try:
                return await asyncio.wait_for(
                    probe_company(
                        client, limiter, company, atses=atses, slugs_to_try=slugs
                    ),
                    timeout=_COMPANY_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return [
                    ProbeOutcome(company, ats, slugs[0], 0, None) for ats in atses
                ]

    tasks: list[asyncio.Task[list[ProbeOutcome]]] = []
    for company, _count in _company_rows(conn, limit):
        slugs = candidates(company)
        if not slugs:
            continue

        # Drop the company if any candidate slug is already wired up for any
        # supported ATS, OR already confirmed by URL harvest this run.
        if any(s in all_known or s in harvested_slugs for s in slugs):
            continue

        if not include_cached:
            misses = _cached_misses(conn, company)
            slugs = [s for s in slugs if not any((ats, s) in misses for ats in atses)]
            if not slugs:
                continue

        tasks.append(asyncio.create_task(_bounded(company, slugs)))

    all_outcomes: list[ProbeOutcome] = []
    total = len(tasks)
    for i, fut in enumerate(asyncio.as_completed(tasks), start=1):
        outcomes = await fut
        _persist_outcomes(conn, outcomes)
        all_outcomes.extend(outcomes)
        if on_progress and outcomes:
            on_progress(ProgressEvent(outcomes[0].company, outcomes, i, total))

    # Combine this run's hits with previously-cached hits that the user hasn't
    # applied yet. Otherwise running discover twice without --apply hides the
    # results from the first run.
    fresh_hits = [o for o in all_outcomes if o.status == 200]
    cached_hits = _cached_hits(conn, atses)
    by_key: dict[tuple[str, str], ProbeOutcome] = {(h.ats, h.slug): h for h in cached_hits}
    for h in fresh_hits:
        by_key[(h.ats, h.slug)] = h  # fresh wins on tie

    unapplied = [
        h
        for h in by_key.values()
        if h.slug not in known.get(h.ats, set())
    ]
    unapplied.sort(key=lambda o: (-(o.job_count or 0), o.ats, o.slug))
    return unapplied


__all__ = ["ProbeOutcome", "ProgressEvent", "discover", "harvest_urls", "probe_company"]

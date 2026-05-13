"""Probe Greenhouse and Ashby public APIs to discover slugs for known companies."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple

import httpx

from jobhunt.config import Config
from jobhunt.discover.slug_candidates import candidates
from jobhunt.errors import IngestError
from jobhunt.http import RateLimiter, get_json

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_ENDPOINTS: Mapping[str, tuple[str, Mapping[str, str]]] = {
    "greenhouse": (_GREENHOUSE_URL, {"content": "false"}),
    "ashby": (_ASHBY_URL, {"includeCompensation": "false"}),
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


@dataclass(frozen=True)
class DiscoverReport:
    companies_seen: int
    companies_skipped_no_candidates: int
    companies_skipped_configured: int
    companies_skipped_cached: int
    companies_probed: int
    probes_attempted: int
    probe_hits: int
    probe_misses: int
    probe_errors: int
    cached_hits_reused: int
    hits: list[ProbeOutcome]


async def _probe_one(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    company: str,
    ats: str,
    slug: str,
) -> ProbeOutcome:
    url_tpl, params = _ENDPOINTS[ats]
    url = url_tpl.format(slug=slug)
    try:
        data = await get_json(client, url, limiter, params=params, max_retries=1)
    except IngestError as e:
        status = 404 if str(e).startswith("404 ") else 0
        return ProbeOutcome(company, ats, slug, status, None)
    except (httpx.HTTPError, TimeoutError):
        return ProbeOutcome(company, ats, slug, 0, None)

    if not isinstance(data, dict):
        return ProbeOutcome(company, ats, slug, 0, None)
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return ProbeOutcome(company, ats, slug, 0, None)
    return ProbeOutcome(company, ats, slug, 200, len(jobs))


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


async def discover(
    client: httpx.AsyncClient,
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    atses: list[str],
    limit: int,
    include_cached: bool,
) -> list[ProbeOutcome]:
    return (
        await discover_with_report(
            client,
            cfg,
            conn,
            atses=atses,
            limit=limit,
            include_cached=include_cached,
        )
    ).hits


async def discover_with_report(
    client: httpx.AsyncClient,
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    atses: list[str],
    limit: int,
    include_cached: bool,
) -> DiscoverReport:
    """Run slug discovery against the jobs DB and return hits plus run metadata."""
    limiter = RateLimiter(rate_per_sec=cfg.ingest.rate_limit_per_sec)
    sem = asyncio.Semaphore(_COMPANY_CONCURRENCY)

    known: dict[str, set[str]] = {
        "greenhouse": set(cfg.ingest.greenhouse),
        "ashby": set(cfg.ingest.ashby),
    }
    companies_seen = 0
    companies_skipped_no_candidates = 0
    companies_skipped_configured = 0
    companies_skipped_cached = 0

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
        companies_seen += 1
        slugs = candidates(company)
        if not slugs:
            companies_skipped_no_candidates += 1
            continue

        # Drop slugs already configured for ANY target ATS — saves probes when the
        # user has already wired up a company manually.
        if any(s in known["greenhouse"] or s in known["ashby"] for s in slugs):
            companies_skipped_configured += 1
            continue

        if not include_cached:
            misses = _cached_misses(conn, company)
            slugs = [s for s in slugs if not any((ats, s) in misses for ats in atses)]
            if not slugs:
                companies_skipped_cached += 1
                continue

        tasks.append(asyncio.create_task(_bounded(company, slugs)))

    all_outcomes: list[ProbeOutcome] = []
    for fut in asyncio.as_completed(tasks):
        outcomes = await fut
        _persist_outcomes(conn, outcomes)
        all_outcomes.extend(outcomes)

    # Combine this run's hits with previously-cached hits that the user hasn't
    # applied yet. Otherwise running discover twice without --apply hides the
    # results from the first run.
    fresh_hits = [o for o in all_outcomes if o.status == 200]
    cached_hits = _cached_hits(conn, atses)
    by_key: dict[tuple[str, str], ProbeOutcome] = {(h.ats, h.slug): h for h in cached_hits}
    for h in fresh_hits:
        by_key[(h.ats, h.slug)] = h  # fresh wins on tie

    fresh_keys = {(h.ats, h.slug) for h in fresh_hits}
    unapplied = [
        h
        for h in by_key.values()
        if h.slug not in known.get(h.ats, set())
    ]
    cached_hits_reused = sum(1 for h in unapplied if (h.ats, h.slug) not in fresh_keys)
    unapplied.sort(key=lambda o: (-(o.job_count or 0), o.ats, o.slug))
    return DiscoverReport(
        companies_seen=companies_seen,
        companies_skipped_no_candidates=companies_skipped_no_candidates,
        companies_skipped_configured=companies_skipped_configured,
        companies_skipped_cached=companies_skipped_cached,
        companies_probed=len(tasks),
        probes_attempted=len(all_outcomes),
        probe_hits=sum(1 for o in all_outcomes if o.status == 200),
        probe_misses=sum(1 for o in all_outcomes if o.status == 404),
        probe_errors=sum(1 for o in all_outcomes if o.status == 0),
        cached_hits_reused=cached_hits_reused,
        hits=unapplied,
    )


__all__ = ["DiscoverReport", "ProbeOutcome", "discover", "discover_with_report", "probe_company"]

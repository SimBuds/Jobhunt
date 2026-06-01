"""Government of Canada Job Bank adapter (HTML search-results scraper).

Job Bank's public RSS feed is dead as of 2026-06: `format=rss` on
`/jobsearch/jobsearch` returns the HTML page, and the real feed endpoint
`/jobsearch/feed/jobSearchRSSfeed?empl=...` returns an empty `<feed>` even with
a valid session + search context. The only live data is the HTML results page.

This adapter therefore parses the HTML results list. Job Bank is a Govt-of-Canada
public service; its robots.txt has no Disallow and requests `Crawl-delay: 5`, so
`scan_cmd` passes a dedicated 5 s-spaced RateLimiter to this adapter. See the
sanctioned-exception note in AGENTS.md §"Ingestion rules" rule 1.

The user puts full Job Bank search URLs in `~/.config/jobhunt/config.toml` under
`[ingest] job_bank_ca = [...]`, e.g.:

    https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=software+developer
        &locationstring=Toronto%2C+ON&fage=7&sort=M

Job Bank's own location filter is loose (a Toronto search leaks Surrey/Montréal),
so `is_gta_eligible` stays the precision gate — same model as the Workday adapter.
Result cards carry no JD body, so a thin description is synthesized from
title/employer/location/salary; the thin-JD score cap handles signal-poor rows
just as it does for Adzuna snippets.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from jobhunt.http import RateLimiter, get_text
from jobhunt.ingest._filter import RemoteType, classify_remote_type, is_gta_eligible
from jobhunt.ingest._rss import strip_html
from jobhunt.models import Job

SOURCE = "job_bank_ca"
_BASE = "https://www.jobbank.gc.ca"

# Job Bank machine-generated markup is regular and class-keyed. Each result is an
# <article id="article-NNN"> block; fields live in class-tagged <li>/<span>.
_ARTICLE_RE = re.compile(
    r'<article\b[^>]*\bid="article-(?P<id>\d+)"[^>]*>(?P<body>.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)
_JOB_URL_RE = re.compile(r'href="(?P<href>/jobsearch/jobposting/[^"]+)"', re.IGNORECASE)
_NOCTITLE_RE = re.compile(
    r'<span[^>]*class="[^"]*\bnoctitle\b[^"]*"[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE
)
_TELEWORK_RE = re.compile(
    r'<span[^>]*class="[^"]*\btelework\b[^"]*"[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE
)
# Strip Job Bank's screen-reader-only labels ("Location", "Job number:") before extraction.
_WB_INV_RE = re.compile(
    r'<span[^>]*class="[^"]*\bwb-inv\b[^"]*"[^>]*>.*?</span>', re.DOTALL | re.IGNORECASE
)
# Strip session id from posting URLs: `/jobposting/NNN;jsessionid=XXX?...` → `/jobposting/NNN?...`.
_JSESSIONID_RE = re.compile(r";jsessionid=[^?]*", re.IGNORECASE)


async def fetch(
    client: httpx.AsyncClient, limiter: RateLimiter, search_url: str
) -> AsyncIterator[Job]:
    html = await get_text(client, search_url, limiter)
    for art in _ARTICLE_RE.finditer(html):
        ext = art.group("id")
        body = art.group("body")
        location = _li(body, "location")
        if not is_gta_eligible(location):
            continue
        title = _clean(_first(_NOCTITLE_RE, body))
        employer = _li(body, "business")
        salary = _li(body, "salary", drop_label="Salary")
        url = _job_url(body)
        yield Job(
            id=f"{SOURCE}:{ext}",
            source=SOURCE,
            external_id=ext,
            company=employer,
            title=title,
            location=location,
            remote_type=_remote_type(_clean(_first(_TELEWORK_RE, body)), location),
            description=_synth_description(title, employer, location, salary),
            url=url,
            posted_at=_parse_date(_li(body, "date")),
            raw_json=None,
        )


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def _clean(fragment: str | None, *, drop_label: str | None = None) -> str | None:
    """Strip screen-reader labels + tags, collapse whitespace, drop a leading label."""
    if fragment is None:
        return None
    text = strip_html(_WB_INV_RE.sub(" ", fragment))
    if text is None:
        return None
    if drop_label and text.lower().startswith(drop_label.lower()):
        text = text[len(drop_label):].strip()
    return text or None


def _li(body: str, cls: str, *, drop_label: str | None = None) -> str | None:
    m = re.search(
        rf'<li[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</li>', body, re.DOTALL | re.IGNORECASE
    )
    return _clean(m.group(1), drop_label=drop_label) if m else None


def _job_url(body: str) -> str | None:
    m = _JOB_URL_RE.search(body)
    if not m:
        return None
    href = _JSESSIONID_RE.sub("", m.group("href"))
    return _BASE + href


def _remote_type(telework: str | None, location: str | None) -> RemoteType:
    """Map Job Bank's telework flag → RemoteType, falling back to location text."""
    tw = (telework or "").lower()
    if "hybrid" in tw:
        return "hybrid"
    if "remote" in tw or "telework" in tw:
        return "remote"
    if "on site" in tw or "onsite" in tw:
        return "onsite"
    return classify_remote_type(location=location)


def _synth_description(
    title: str | None, employer: str | None, location: str | None, salary: str | None
) -> str:
    """Job Bank result cards carry no JD body — synthesize a thin one (Adzuna-style)."""
    bits = [b for b in (title, employer, location) if b]
    text = " — ".join(bits) if bits else "Job Bank posting"
    if salary:
        text += f". Salary: {salary}"
    return text


def _parse_date(s: str | None) -> datetime | None:
    """Job Bank prints 'May 29, 2026' (occasionally abbreviated 'May 29, 2026')."""
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

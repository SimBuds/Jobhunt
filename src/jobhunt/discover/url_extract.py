"""Extract ATS provider + slug from job application URLs.

Deterministic, offline counterpart to `probe.py`. The jobs table already holds
apply URLs for everything we've ingested; many of those URLs encode the ATS
slug in the host or path. Pulling slugs from URLs is dramatically higher-signal
than name→slug guessing — when a hit exists in the URL, it's confirmed by
construction.

URL shapes handled:
- Greenhouse:     boards.greenhouse.io/{slug}[/jobs/...]
                  job-boards.greenhouse.io/{slug}[/jobs/...]
- Lever:          jobs.lever.co/{slug}[/...]
- Ashby:          jobs.ashbyhq.com/{slug}[/...]
- SmartRecruiters: jobs.smartrecruiters.com/{slug}[/...]
                  careers.smartrecruiters.com/{slug}[/...]
- Workday:        {tenant}.wd{N}.myworkdayjobs.com/[en-US/]{site}[/...]
- iCIMS:          careers-{tenant}.icims.com/[...] (tenant only; iCIMS isn't a probe target yet)
"""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlparse


class ExtractedSlug(NamedTuple):
    ats: str
    slug: str           # tenant for Workday, board slug elsewhere
    site: str | None    # Workday site path segment; None for everything else
    host: str | None    # Workday wd-host segment ("wd1", "wd3", "wd5"); None elsewhere


_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_LEVER_HOSTS = {"jobs.lever.co"}
_ASHBY_HOSTS = {"jobs.ashbyhq.com"}
_SMARTRECRUITERS_HOSTS = {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}
_WORKDAY_HOST_RE = re.compile(
    r"^(?P<tenant>[a-z0-9-]+)\.(?P<host>wd\d+)\.myworkdayjobs\.com$"
)
_ICIMS_HOST_RE = re.compile(r"^careers-(?P<tenant>[a-z0-9-]+)\.icims\.com$")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,60}$", re.IGNORECASE)


def extract(url: str) -> ExtractedSlug | None:
    """Return the ATS+slug encoded in `url`, or None if the URL isn't a known ATS."""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None

    segments = [s for s in parsed.path.split("/") if s]

    if host in _GREENHOUSE_HOSTS:
        return _first_segment("greenhouse", segments)
    if host in _LEVER_HOSTS:
        return _first_segment("lever", segments)
    if host in _ASHBY_HOSTS:
        return _first_segment("ashby", segments)
    if host in _SMARTRECRUITERS_HOSTS:
        return _first_segment("smartrecruiters", segments)

    m = _WORKDAY_HOST_RE.match(host)
    if m:
        tenant = m.group("tenant")
        wd_host = m.group("host")
        # Workday paths look like /en-US/{site}/... — strip a locale prefix if present.
        path_segs = segments
        if path_segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", path_segs[0]):
            path_segs = path_segs[1:]
        site = path_segs[0] if path_segs and _SLUG_RE.match(path_segs[0]) else None
        if not _SLUG_RE.match(tenant):
            return None
        return ExtractedSlug("workday", tenant, site, wd_host)

    m = _ICIMS_HOST_RE.match(host)
    if m:
        tenant = m.group("tenant")
        if not _SLUG_RE.match(tenant):
            return None
        return ExtractedSlug("icims", tenant, None, None)

    return None


def _first_segment(ats: str, segments: list[str]) -> ExtractedSlug | None:
    if not segments:
        return None
    slug = segments[0]
    if not _SLUG_RE.match(slug):
        return None
    return ExtractedSlug(ats, slug, None, None)


__all__ = ["ExtractedSlug", "extract"]

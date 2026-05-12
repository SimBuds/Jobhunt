"""Shared async HTTP client. Per-host rate limit, contact-bearing UA, exponential backoff."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from jobhunt.errors import IngestError

DEFAULT_UA = "jobhunt/0.1 (+personal-use; your-email@example.com)"


class RateLimiter:
    """Simple per-host rate limiter — at most `rate` requests per second per host."""

    def __init__(self, rate_per_sec: float) -> None:
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        if self._min_interval <= 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            sleep_for = self._min_interval - (now - last)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._last[host] = time.monotonic()


def host_of(url: str) -> str:
    return urlparse(url).hostname or "unknown"


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    limiter: RateLimiter,
    *,
    params: Mapping[str, Any] | None = None,
    max_retries: int = 3,
) -> Any:
    """GET a URL, return parsed JSON. Backs off on 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        await limiter.wait(host_of(url))
        try:
            r = await client.get(url, params=dict(params) if params else None)
        except httpx.HTTPError as e:
            last_exc = e
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 404:
            raise IngestError(f"404 {url}")
        r.raise_for_status()
        return r.json()
    raise IngestError(f"failed after {max_retries} retries: {url} ({last_exc})")


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    limiter: RateLimiter,
    *,
    json_body: Mapping[str, Any],
    max_retries: int = 3,
) -> Any:
    """POST a URL with a JSON body, return parsed JSON. Backs off on 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        await limiter.wait(host_of(url))
        try:
            r = await client.post(url, json=dict(json_body))
        except httpx.HTTPError as e:
            last_exc = e
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code in (401, 403):
            raise IngestError(f"{r.status_code} {url} (tenant auth-walled — skipping)")
        if r.status_code == 404:
            raise IngestError(f"404 {url}")
        r.raise_for_status()
        return r.json()
    raise IngestError(f"failed after {max_retries} retries: {url} ({last_exc})")


async def resolve_redirect(
    client: httpx.AsyncClient,
    url: str,
    limiter: RateLimiter,
    *,
    max_hops: int = 5,
) -> str:
    """Follow Location headers manually and return the final URL.

    Used to chase Adzuna's tracking-link `redirect_url` to the employer's
    actual posting page so the autofill handler lands on the apply form
    instead of a listing-redirect intermediary.

    Never raises and never blocks ingest — on any error (network, loop,
    non-redirect 4xx/5xx, timeout) returns the original URL. We try HEAD
    first; some hosts reject HEAD with 405, so we fall back to GET with
    streaming so we don't pull the body just to read headers.
    """
    current = url
    seen: set[str] = set()
    for _ in range(max_hops):
        if current in seen:
            return url  # loop detected — give up, keep original
        seen.add(current)
        await limiter.wait(host_of(current))
        try:
            r = await client.head(current, follow_redirects=False)
            if r.status_code == 405:
                # HEAD not allowed — try a streaming GET instead.
                async with client.stream("GET", current, follow_redirects=False) as gr:
                    status = gr.status_code
                    location = gr.headers.get("location")
            else:
                status = r.status_code
                location = r.headers.get("location")
        except httpx.HTTPError:
            return url
        if 300 <= status < 400 and location:
            current = str(httpx.URL(current).join(location))
            continue
        # Terminal status (2xx, 4xx, 5xx without redirect) — current is the
        # final URL we landed on.
        return current
    # Hop budget exhausted — fall back to original.
    return url


async def with_client[T](
    fn: Callable[[httpx.AsyncClient], Awaitable[T]], *, user_agent: str = DEFAULT_UA
) -> T:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        return await fn(client)

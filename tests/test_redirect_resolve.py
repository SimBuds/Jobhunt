"""Tests for jobhunt.http.resolve_redirect — Adzuna chase to employer URL.

Uses httpx.MockTransport so no real network. Verifies:
- happy-path 302 chain returns the final URL
- loop is detected and original URL returned
- network error returns the original URL (never raises)
- HEAD-405 fallback to streaming GET works
- max_hops budget caps the chase
"""

from __future__ import annotations

import httpx
import pytest

from jobhunt.http import RateLimiter, resolve_redirect


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_happy_path_chain_returns_final_url() -> None:
    chain = {
        "https://www.adzuna.ca/details/1": "https://employer.example.com/apply/123",
        "https://employer.example.com/apply/123": None,  # terminal 200
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if chain.get(url):
            return httpx.Response(302, headers={"Location": chain[url]})
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await resolve_redirect(
            client, "https://www.adzuna.ca/details/1", RateLimiter(0)
        )
    assert result == "https://employer.example.com/apply/123"


@pytest.mark.asyncio
async def test_loop_returns_original_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A → B → A loop.
        if "/a" in str(request.url):
            return httpx.Response(302, headers={"Location": "https://x.test/b"})
        return httpx.Response(302, headers={"Location": "https://x.test/a"})

    async with _client(handler) as client:
        result = await resolve_redirect(client, "https://x.test/a", RateLimiter(0))
    assert result == "https://x.test/a"


@pytest.mark.asyncio
async def test_network_error_returns_original_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        result = await resolve_redirect(
            client, "https://broken.test/", RateLimiter(0)
        )
    assert result == "https://broken.test/"


@pytest.mark.asyncio
async def test_head_405_falls_back_to_streaming_get() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD" and "/initial" in str(request.url):
            return httpx.Response(405)
        if request.method == "GET" and "/initial" in str(request.url):
            return httpx.Response(302, headers={"Location": "https://final.test/job"})
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await resolve_redirect(
            client, "https://no-head.test/initial", RateLimiter(0)
        )
    assert result == "https://final.test/job"


@pytest.mark.asyncio
async def test_max_hops_exhausted_returns_original() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        # Always redirect to a brand-new URL so loop detection doesn't trigger.
        return httpx.Response(
            302, headers={"Location": f"https://hop.test/{counter['n']}"}
        )

    async with _client(handler) as client:
        result = await resolve_redirect(
            client, "https://hop.test/start", RateLimiter(0), max_hops=3
        )
    assert result == "https://hop.test/start"  # fell back to original
    assert counter["n"] == 3


@pytest.mark.asyncio
async def test_relative_location_resolves_against_current() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://x.test/start":
            return httpx.Response(302, headers={"Location": "/jobs/42"})
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await resolve_redirect(client, "https://x.test/start", RateLimiter(0))
    assert result == "https://x.test/jobs/42"

"""Remote-board RSS feeds: keep a posting unless it names a non-Canada restriction."""

from __future__ import annotations

import asyncio
from typing import Any
from xml.sax.saxutils import escape

import pytest

from jobhunt.config import Config
from jobhunt.ingest import rss_generic
from jobhunt.models import Job


def _feed(descriptions: list[str]) -> str:
    items = "".join(
        f"<item><title>Shopify Developer {i}</title><link>https://x.test/{i}</link>"
        f"<guid>g{i}</guid><description>{escape(d)}</description></item>"
        for i, d in enumerate(descriptions)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


def _run(
    monkeypatch: pytest.MonkeyPatch, descriptions: list[str], *, remote_board: bool
) -> list[Job]:
    xml = _feed(descriptions)

    async def fake_fetch_feed(*args: Any, **kwargs: Any) -> str:
        return xml

    monkeypatch.setattr(rss_generic, "fetch_feed", fake_fetch_feed)

    async def drain() -> list[Job]:
        gen = rss_generic.fetch(None, None, "x", remote_board=remote_board)  # type: ignore[arg-type]
        return [j async for j in gen]

    return asyncio.run(drain())


@pytest.mark.parametrize(
    "description",
    [
        "Build Liquid themes. Work from anywhere.",
        "Build Liquid themes for our merchants.",
        "Remote. We focus on users and status pages.",
        "Remote. Join us and help us build themes.",
        "Hybrid role in Toronto, ON.",
    ],
)
def test_remote_board_keeps_unrestricted_items(
    monkeypatch: pytest.MonkeyPatch, description: str
) -> None:
    jobs = _run(monkeypatch, [description], remote_board=True)
    assert len(jobs) == 1


@pytest.mark.parametrize(
    "description",
    [
        "Remote, but you must live within the United States.",
        "This role is U.S. Based.",
        "Remote. Must overlap European business hours.",
        "Remote for engineers based in Latin America.",
        "US-based applicants only.",
        "Remote (EMEA).",
        "Remote, must reside in the US.",
        "Remote within the united kingdom.",
    ],
)
def test_remote_board_drops_restricted_items(
    monkeypatch: pytest.MonkeyPatch, description: str
) -> None:
    assert _run(monkeypatch, [description], remote_board=True) == []


def test_remote_board_items_are_remote_type_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = _run(monkeypatch, ["Work from anywhere."], remote_board=True)
    assert jobs[0].remote_type == "remote"


def test_plain_rss_keeps_the_strict_gta_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = _run(monkeypatch, ["Remote", "Hybrid role in Toronto, ON."], remote_board=False)
    assert [j.title for j in jobs] == ["Shopify Developer 1"]


def test_config_has_rss_remote_list() -> None:
    assert Config().ingest.rss_remote == []


_CONTENT_NS = 'xmlns:content="http://purl.org/rss/1.0/modules/content/"'
_JOB_NS = 'xmlns:job="https://jobs.example"'


def _rich_feed(item_body: str) -> str:
    return (
        f'<?xml version="1.0"?><rss version="2.0" {_CONTENT_NS} {_JOB_NS}><channel>'
        f"<item><title>Shopify Developer</title><link>https://x.test/1</link>"
        f"<guid>g1</guid>{item_body}</item></channel></rss>"
    )


def _one(monkeypatch: pytest.MonkeyPatch, item_body: str) -> Job:
    xml = _rich_feed(item_body)

    async def fake_fetch_feed(*args: Any, **kwargs: Any) -> str:
        return xml

    monkeypatch.setattr(rss_generic, "fetch_feed", fake_fetch_feed)

    async def drain() -> list[Job]:
        gen = rss_generic.fetch(None, None, "x", remote_board=True)  # type: ignore[arg-type]
        return [j async for j in gen]

    jobs = asyncio.run(drain())
    assert len(jobs) == 1
    return jobs[0]


def test_full_body_replaces_a_shorter_excerpt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boards put a ~200-char excerpt in <description> and the posting in
    <content:encoded>. Scoring the excerpt was scoring a headline."""
    body = "<p>Build Liquid themes.</p><p>" + "Metafields and Online Store 2.0. " * 20 + "</p>"
    job = _one(
        monkeypatch,
        f"<description>Build Liquid themes.</description>"
        f"<content:encoded>{escape(body)}</content:encoded>",
    )
    assert job.description is not None
    assert "Metafields and Online Store 2.0." in job.description
    assert "<p>" not in job.description
    assert len(job.description) > 500


def test_description_kept_when_there_is_no_full_body(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _one(monkeypatch, "<description>Build Liquid themes.</description>")
    assert job.description == "Build Liquid themes."
    assert job.company is None


def test_description_kept_when_the_full_body_is_shorter(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _one(
        monkeypatch,
        "<description>Build Liquid themes for our merchants.</description>"
        "<content:encoded>Short.</content:encoded>",
    )
    assert job.description == "Build Liquid themes for our merchants."


def test_company_element_is_read_in_any_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _one(
        monkeypatch,
        "<description>Build Liquid themes.</description><job:company> Acme Co </job:company>",
    )
    assert job.company == "Acme Co"


def test_empty_company_element_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _one(
        monkeypatch,
        "<description>Build Liquid themes.</description><job:company></job:company>",
    )
    assert job.company is None


def test_region_filter_still_reads_the_excerpt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full body is for scoring. Filtering on it vetoed 53 of 182 items on
    a Canada-filtered feed over boilerplate such as 'offices across EMEA'."""
    body = "Build Liquid themes. " * 30 + "We have offices across EMEA and the United States."
    job = _one(
        monkeypatch,
        f"<description>Build Liquid themes.</description>"
        f"<content:encoded>{escape(body)}</content:encoded>",
    )
    assert job.description is not None and "EMEA" in job.description
    assert job.location == "Build Liquid themes."

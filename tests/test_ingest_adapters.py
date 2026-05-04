"""Parser tests for new ingest adapters — no network, no Ollama."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.ingest._rss import RSSItem, parse_feed, strip_html
from jobhunt.ingest.job_bank_ca import _split_title
from jobhunt.ingest.smartrecruiters import (
    _extract_description,
    _format_location,
    _parse_dt,
)
from jobhunt.models import Job

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# RSS parser (_rss.py)
# ---------------------------------------------------------------------------


def test_rss_parse_job_bank_feed() -> None:
    xml = (FIXTURES / "job_bank_ca.xml").read_text()
    items = list(parse_feed(xml))
    assert len(items) == 3
    assert items[0].title == "web developer - ACME Inc - Toronto (ON)"
    assert items[0].link and "123456" in items[0].link
    assert items[0].pub_date is not None


def test_rss_parse_generic_feed() -> None:
    xml = (FIXTURES / "rss_generic.xml").read_text()
    items = list(parse_feed(xml))
    assert len(items) == 2
    assert items[0].title and "Toronto" in items[0].title


def test_strip_html_removes_tags() -> None:
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html(None) is None
    assert strip_html("") is None


# ---------------------------------------------------------------------------
# job_bank_ca adapter
# ---------------------------------------------------------------------------


def test_job_bank_split_title_full() -> None:
    company, title, location = _split_title("web developer - ACME Inc - Toronto (ON)")
    assert title == "web developer"
    assert company == "ACME Inc"
    assert location == "Toronto (ON)"


def test_job_bank_split_title_two_parts() -> None:
    company, title, location = _split_title("developer - ACME Inc")
    assert title == "developer"
    assert company == "ACME Inc"
    assert location is None


def test_job_bank_gta_filter_applies() -> None:
    """Only Toronto + Remote Canada items should pass the GTA filter."""
    xml = (FIXTURES / "job_bank_ca.xml").read_text()
    items = list(parse_feed(xml))
    from jobhunt.ingest._filter import is_gta_eligible
    from jobhunt.ingest.job_bank_ca import _split_title

    eligible = []
    for item in items:
        if not item.title:
            continue
        _, _, location = _split_title(item.title)
        if is_gta_eligible(location) or is_gta_eligible(item.description):
            eligible.append(item.title)
    # Vancouver item must NOT be eligible.
    assert not any("Vancouver" in t for t in eligible)
    # Toronto item must be eligible.
    assert any("Toronto" in t for t in eligible)


# ---------------------------------------------------------------------------
# smartrecruiters adapter
# ---------------------------------------------------------------------------


def test_smartrecruiters_format_location_onsite() -> None:
    loc = {"city": "Toronto", "region": "Ontario", "country": "Canada", "remote": False}
    result = _format_location(loc)
    assert result == "Toronto, Ontario, Canada"


def test_smartrecruiters_format_location_remote() -> None:
    loc = {"city": "Toronto", "country": "Canada", "remote": True}
    result = _format_location(loc)
    assert result and "Remote" in result


def test_smartrecruiters_extract_description() -> None:
    raw = json.loads((FIXTURES / "smartrecruiters.json").read_text())
    first = raw["content"][0]
    desc = _extract_description(first)
    assert desc and "TypeScript" in desc
    assert desc and "Shopify" in desc


def test_smartrecruiters_parse_dt_valid() -> None:
    dt = _parse_dt("2026-05-04T09:00:00Z")
    assert dt is not None
    assert dt.year == 2026


def test_smartrecruiters_gta_filter() -> None:
    """The Seattle item must NOT pass the GTA filter."""
    raw = json.loads((FIXTURES / "smartrecruiters.json").read_text())
    from jobhunt.ingest._filter import is_gta_eligible
    from jobhunt.ingest.smartrecruiters import _format_location

    eligible_titles = []
    for item in raw["content"]:
        loc = _format_location(item.get("location"))
        if is_gta_eligible(loc):
            eligible_titles.append(item["name"])
    assert "Backend Engineer" not in eligible_titles
    assert "Full-Stack Developer" in eligible_titles


# ---------------------------------------------------------------------------
# Cross-source dedupe key
# ---------------------------------------------------------------------------


def test_dedup_key_greenhouse_uses_job_id() -> None:
    from jobhunt.commands.scan_cmd import _dedup_key

    job = Job(id="greenhouse:shopify:abc", source="greenhouse", external_id="abc", title="Dev")
    assert _dedup_key(job) == "greenhouse:shopify:abc"


def test_dedup_key_adzuna_normalises() -> None:
    from jobhunt.commands.scan_cmd import _dedup_key

    j1 = Job(id="adzuna_ca:1", source="adzuna_ca", external_id="1", title="Full-Stack Developer", company="ACME Inc")
    j2 = Job(id="adzuna_ca:2", source="adzuna_ca", external_id="2", title="Full-Stack Developer", company="ACME Inc")
    assert _dedup_key(j1) == _dedup_key(j2)


def test_dedup_key_different_companies_differ() -> None:
    from jobhunt.commands.scan_cmd import _dedup_key

    j1 = Job(id="adzuna_ca:1", source="adzuna_ca", external_id="1", title="Developer", company="ACME")
    j2 = Job(id="adzuna_ca:2", source="adzuna_ca", external_id="2", title="Developer", company="Beta Corp")
    assert _dedup_key(j1) != _dedup_key(j2)

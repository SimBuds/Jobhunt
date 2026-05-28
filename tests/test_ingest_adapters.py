"""Parser tests for new ingest adapters — no network, no Ollama."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobhunt.errors import IngestError
from jobhunt.ingest._rss import RSSItem, parse_feed, strip_html
from jobhunt.ingest.job_bank_ca import _split_title
from jobhunt.ingest.smartrecruiters import (
    _extract_description,
    _format_location,
    _parse_dt,
)
from jobhunt.ingest.workday import _location_text, _parse_posted_on, _parse_tenant
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


# ---------------------------------------------------------------------------
# workday adapter
# ---------------------------------------------------------------------------


def test_workday_parse_tenant_spec() -> None:
    assert _parse_tenant("rbc:wd3:RBC_Careers") == ("rbc", "wd3", "RBC_Careers")


def test_workday_parse_tenant_rejects_malformed() -> None:
    with pytest.raises(IngestError):
        _parse_tenant("rbc:wd3")
    with pytest.raises(IngestError):
        _parse_tenant("rbc::RBC_Careers")


def test_workday_location_text_handles_list_and_str() -> None:
    assert _location_text({"locationsText": "Toronto, ON"}) == "Toronto, ON"
    assert _location_text({"bulletFields": ["Toronto", "Remote"]}) == "Toronto, Remote"
    assert _location_text({}) is None


# ---------------------------------------------------------------------------
# lever / ashby adapters — drive the async iterator with a mocked get_json
# ---------------------------------------------------------------------------


def _drain(agen: Any) -> list[Job]:
    import asyncio

    async def _go() -> list[Job]:
        out: list[Job] = []
        async for j in agen:
            out.append(j)
        return out

    return asyncio.run(_go())


def test_lever_fixture_filters_to_gta(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.ingest import lever

    raw = json.loads((FIXTURES / "lever.json").read_text())

    async def fake_get_json(*args: Any, **kwargs: Any) -> Any:
        return raw

    monkeypatch.setattr(lever, "get_json", fake_get_json)
    jobs = _drain(lever.fetch(client=None, limiter=None, slug="example"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    assert "Senior Software Engineer" in titles
    assert "Remote Backend Engineer" in titles
    assert "Engineer (NYC)" not in titles
    first = next(j for j in jobs if j.title == "Senior Software Engineer")
    assert first.id == "lever:example:abc-123"
    assert first.source == "lever"
    assert first.url and "abc-123" in first.url


def test_ashby_fixture_filters_to_gta(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.ingest import ashby

    raw = json.loads((FIXTURES / "ashby.json").read_text())

    async def fake_get_json(*args: Any, **kwargs: Any) -> Any:
        return raw

    monkeypatch.setattr(ashby, "get_json", fake_get_json)
    jobs = _drain(ashby.fetch(client=None, limiter=None, slug="example"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    assert "Senior Full-Stack Engineer" in titles
    assert "Remote Platform Engineer" in titles
    assert "London Engineer" not in titles
    remote_job = next(j for j in jobs if j.title == "Remote Platform Engineer")
    assert remote_job.remote_type == "remote"


def test_workday_fixture_filters_to_gta() -> None:
    """Walk the fixture the same way the adapter does — confirm the GTA filter
    keeps the Toronto + Remote-Canada postings and drops the NY one."""
    data = json.loads((FIXTURES / "workday.json").read_text())
    from jobhunt.ingest._filter import is_gta_eligible

    kept = [p for p in data["jobPostings"] if is_gta_eligible(_location_text(p))]
    titles = [p["title"] for p in kept]
    assert "Senior Software Engineer, Digital Banking" in titles
    assert "Platform Engineer (Remote, Canada)" in titles
    assert "Backend Engineer" not in titles


# Phase 5 — postedOn parser
from datetime import datetime, timezone


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_workday_parse_posted_today() -> None:
    out = _parse_posted_on("Posted Today", now=_NOW)
    assert out == _NOW


def test_workday_parse_posted_yesterday() -> None:
    out = _parse_posted_on("Posted Yesterday", now=_NOW)
    assert out is not None
    assert (_NOW - out).days == 1


def test_workday_parse_posted_n_days_ago() -> None:
    out = _parse_posted_on("Posted 3 Days Ago", now=_NOW)
    assert out is not None
    assert (_NOW - out).days == 3


def test_workday_parse_posted_30_plus_days() -> None:
    """'30+ Days Ago' parses as exactly 30 — best-effort floor that lets the
    freshness filter still drop these as stale at max_age_days=14."""
    out = _parse_posted_on("Posted 30+ Days Ago", now=_NOW)
    assert out is not None
    assert (_NOW - out).days == 30


def test_workday_parse_posted_unparseable_returns_none() -> None:
    assert _parse_posted_on(None) is None
    assert _parse_posted_on("") is None
    assert _parse_posted_on("Some weird format") is None


def test_workday_fixture_populates_posted_at() -> None:
    """Adapter integration check: walk the fixture's postedOn values, confirm
    each parseable one yields a non-None timestamp."""
    data = json.loads((FIXTURES / "workday.json").read_text())
    parsed = [
        _parse_posted_on(p.get("postedOn"), now=_NOW) for p in data["jobPostings"]
    ]
    # All three fixture entries have parseable postedOn values.
    assert all(t is not None for t in parsed)


# Phase: adaptive GTA-term scan (drive fetch() with a mocked post_json)


def _wd_posting(ext: str, title: str, location: str) -> dict[str, Any]:
    # non-empty shortDescription so _emit doesn't trigger a detail get_json call
    return {
        "externalPath": f"/job/{ext}",
        "title": title,
        "locationsText": location,
        "shortDescription": "desc",
        "postedOn": "Posted Today",
    }


def test_workday_blank_scan_small_board(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boards <= _BLANK_SCAN_MAX keep the blank first-100 walk and never issue a
    GTA search term."""
    from jobhunt.ingest import workday

    seen_terms: list[str] = []

    async def fake_post_json(*args: Any, json_body: Any, **kwargs: Any) -> Any:
        seen_terms.append(json_body["searchText"])
        return {
            "total": 10,
            "jobPostings": [
                _wd_posting("A", "Frontend Developer", "Toronto, ON"),
                _wd_posting("NY", "Backend Engineer", "New York, NY"),
            ],
        }

    monkeypatch.setattr(workday, "post_json", fake_post_json)
    jobs = _drain(workday.fetch(client=None, limiter=None, spec="acme:wd1:Careers"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    assert "Frontend Developer" in titles
    assert "Backend Engineer" not in titles  # NY dropped by is_gta_eligible
    assert set(seen_terms) == {""}  # only the blank probe — no GTA term query
    first = next(j for j in jobs if j.title == "Frontend Developer")
    assert first.id == "workday:acme:A"
    assert first.source == "workday"


def test_workday_term_scan_large_board_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boards > _BLANK_SCAN_MAX switch to the GTA search-term union; a posting
    surfaced under two terms is emitted once and the probe page isn't walked."""
    from jobhunt.ingest import workday

    pages = {
        "Toronto": [
            _wd_posting("A", "React Developer", "Toronto, ON"),
            _wd_posting("NY", "Backend Engineer", "New York, NY"),
        ],
        "Ontario": [
            _wd_posting("A", "React Developer", "Toronto, ON"),  # dup of the Toronto hit
            _wd_posting("B", "Vue Developer", "Mississauga, ON"),
        ],
        "Remote, Canada": [],
    }

    async def fake_post_json(*args: Any, json_body: Any, **kwargs: Any) -> Any:
        term = json_body["searchText"]
        if term == "":  # size probe — big board, page must NOT be walked in term mode
            return {"total": 900, "jobPostings": [_wd_posting("Z", "Ignored", "New York, NY")]}
        return {"total": 50, "jobPostings": pages.get(term, [])}

    monkeypatch.setattr(workday, "post_json", fake_post_json)
    jobs = _drain(workday.fetch(client=None, limiter=None, spec="big:wd5:Careers"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    ids = [j.id for j in jobs]
    assert titles.count("React Developer") == 1  # under both Toronto + Ontario → emitted once
    assert "Vue Developer" in titles
    assert "Backend Engineer" not in titles  # NY dropped
    assert "Ignored" not in titles  # probe page never walked in term mode
    assert ids.count("workday:big:A") == 1
    assert len(jobs) == 2


# ---------------------------------------------------------------------------
# workable adapter
# ---------------------------------------------------------------------------


def test_workable_fixture_filters_to_gta(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.ingest import workable

    raw = json.loads((FIXTURES / "workable.json").read_text())

    async def fake_get_json(*args: Any, **kwargs: Any) -> Any:
        return raw

    monkeypatch.setattr(workable, "get_json", fake_get_json)
    jobs = _drain(workable.fetch(client=None, limiter=None, slug="example"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    assert "Senior Full-Stack Developer" in titles
    assert "Remote Platform Engineer" in titles
    assert "London Engineer" not in titles
    first = next(j for j in jobs if j.title == "Senior Full-Stack Developer")
    assert first.id == "workable:example:ABC123"
    assert first.source == "workable"
    remote_job = next(j for j in jobs if j.title == "Remote Platform Engineer")
    assert remote_job.remote_type == "remote"


# ---------------------------------------------------------------------------
# recruitee adapter
# ---------------------------------------------------------------------------


def test_recruitee_fixture_filters_to_gta(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunt.ingest import recruitee

    raw = json.loads((FIXTURES / "recruitee.json").read_text())

    async def fake_get_json(*args: Any, **kwargs: Any) -> Any:
        return raw

    monkeypatch.setattr(recruitee, "get_json", fake_get_json)
    jobs = _drain(recruitee.fetch(client=None, limiter=None, slug="example"))  # type: ignore[arg-type]

    titles = [j.title for j in jobs]
    assert "Senior Backend Engineer" in titles
    assert "Remote Platform Engineer" in titles
    assert "Berlin Engineer" not in titles
    first = next(j for j in jobs if j.title == "Senior Backend Engineer")
    assert first.id == "recruitee:example:201"
    assert first.source == "recruitee"
    remote_job = next(j for j in jobs if j.title == "Remote Platform Engineer")
    assert remote_job.remote_type == "remote"


# ---------------------------------------------------------------------------
# url_extract — new ATS host recognisers
# ---------------------------------------------------------------------------


def test_url_extract_workable_apply_host() -> None:
    from jobhunt.discover.url_extract import extract

    out = extract("https://apply.workable.com/example-co/j/ABC123/")
    assert out is not None
    assert out.ats == "workable"
    assert out.slug == "example-co"


def test_url_extract_workable_subdomain_host() -> None:
    from jobhunt.discover.url_extract import extract

    out = extract("https://example-co.workable.com/jobs/123")
    assert out is not None
    assert out.ats == "workable"
    assert out.slug == "example-co"


def test_url_extract_recruitee_subdomain_host() -> None:
    from jobhunt.discover.url_extract import extract

    out = extract("https://example.recruitee.com/o/senior-backend-engineer")
    assert out is not None
    assert out.ats == "recruitee"
    assert out.slug == "example"


def test_url_extract_unknown_host_returns_none() -> None:
    from jobhunt.discover.url_extract import extract

    assert extract("https://example.com/jobs/123") is None

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from jobhunt.commands import discover_cmd
from jobhunt.config import Config
from jobhunt.db import connect, migrate, upsert_job
from jobhunt.discover import probe as probe_mod
from jobhunt.discover.probe import DiscoverReport, ProbeOutcome, discover
from jobhunt.errors import IngestError
from jobhunt.models import Job


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    return c


@pytest.fixture
def cfg() -> Config:
    return Config()


def _seed(conn: sqlite3.Connection, companies: list[tuple[str, int]]) -> None:
    """Insert `n` jobs per company so they appear in the discover query."""
    n = 1
    for company, count in companies:
        for _ in range(count):
            upsert_job(
                conn,
                Job(
                    id=f"adzuna_ca:{n}",
                    source="adzuna_ca",
                    external_id=str(n),
                    company=company,
                    title="Dev",
                    location="Toronto, ON",
                    description="…",
                    url=f"https://example.com/{n}",
                ),
            )
            n += 1


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _report(**overrides: Any) -> DiscoverReport:
    base: dict[str, Any] = {
        "companies_seen": 0,
        "companies_skipped_no_candidates": 0,
        "companies_skipped_configured": 0,
        "companies_skipped_cached": 0,
        "companies_probed": 0,
        "probes_attempted": 0,
        "probe_hits": 0,
        "probe_misses": 0,
        "probe_errors": 0,
        "cached_hits_reused": 0,
        "hits": [],
    }
    base.update(overrides)
    return DiscoverReport(**base)


def _make_fake_get_json(
    hits: Mapping[tuple[str, str], int],
    *,
    errors: Mapping[tuple[str, str], Exception] | None = None,
):
    """Returns a fake get_json that matches against (ats_host, slug)."""
    errors = errors or {}

    async def fake_get_json(
        client: Any, url: str, limiter: Any, *, params: Any = None, max_retries: int = 3
    ) -> Any:
        # Identify ATS by URL host. The slug is the second-to-last path segment for
        # greenhouse ("/boards/<slug>/jobs") and the final path segment for ashby.
        if "greenhouse.io" in url:
            ats = "greenhouse"
            slug = url.split("/boards/")[1].split("/")[0]
        elif "ashbyhq.com" in url:
            ats = "ashby"
            slug = url.rstrip("/").split("/")[-1]
        else:
            raise AssertionError(f"unexpected url: {url}")

        key = (ats, slug)
        if key in errors:
            raise errors[key]
        if key in hits:
            return {"jobs": [{"id": i} for i in range(hits[key])]}
        raise IngestError(f"404 {url}")

    return fake_get_json


async def _discover(
    client: httpx.AsyncClient,
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    include_cached: bool = False,
) -> list[ProbeOutcome]:
    return await discover(
        client,
        cfg,
        conn,
        atses=["greenhouse", "ashby"],
        limit=limit,
        include_cached=include_cached,
    )


def test_discover_returns_only_hits(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(
        conn,
        [
            ("Okta", 10),       # → slug "okta" — hit on greenhouse
            ("Konrad Group", 5),  # → "konradgroup" hit on greenhouse, "konrad" 404s
            ("Acme Random", 3),  # no ATS exists at all
        ],
    )

    fake = _make_fake_get_json(
        hits={
            ("greenhouse", "okta"): 42,
            ("greenhouse", "konradgroup"): 17,
        },
    )
    monkeypatch.setattr(probe_mod, "get_json", fake)

    async def go() -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn)

    hits = _run(go())
    by_company = {h.company: h for h in hits}

    assert set(by_company) == {"Okta", "Konrad Group"}
    assert by_company["Okta"].slug == "okta"
    assert by_company["Okta"].ats == "greenhouse"
    assert by_company["Okta"].job_count == 42
    assert by_company["Konrad Group"].slug == "konradgroup"

    # Hits sorted by job_count desc — Okta (42) before Konrad (17)
    assert [h.company for h in hits] == ["Okta", "Konrad Group"]


def test_discover_caches_misses(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(conn, [("Acme Random", 3)])

    call_log: list[str] = []

    fake = _make_fake_get_json(hits={})

    async def logging_fake(*args: Any, **kwargs: Any) -> Any:
        call_log.append(args[1])  # URL is positional arg 1
        return await fake(*args, **kwargs)

    monkeypatch.setattr(probe_mod, "get_json", logging_fake)

    async def go() -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn)

    # First run: probes every candidate, all miss
    hits1 = _run(go())
    assert hits1 == []
    first_call_count = len(call_log)
    assert first_call_count > 0

    # Misses are persisted
    cached = conn.execute(
        "SELECT company, ats, slug, status FROM slug_probes WHERE company = ?",
        ("Acme Random",),
    ).fetchall()
    assert len(cached) == first_call_count
    assert all(row["status"] == 404 for row in cached)

    # Second run: cache filters everything out, no new probes
    call_log.clear()
    hits2 = _run(go())
    assert hits2 == []
    assert call_log == []  # cached, no probes


def test_discover_include_cached_reprobes(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(conn, [("Acme Random", 3)])

    fake = _make_fake_get_json(hits={})
    monkeypatch.setattr(probe_mod, "get_json", fake)

    async def go(include_cached: bool) -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn, include_cached=include_cached)

    _run(go(False))  # populate cache

    call_log: list[str] = []

    async def logging_fake(*args: Any, **kwargs: Any) -> Any:
        call_log.append(args[1])
        return await fake(*args, **kwargs)

    monkeypatch.setattr(probe_mod, "get_json", logging_fake)

    _run(go(True))
    assert len(call_log) > 0  # re-probed despite cache


def test_discover_skips_companies_already_configured(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(conn, [("Okta", 10), ("Acme Random", 3)])
    cfg.ingest.greenhouse = ["okta"]  # already configured

    call_log: list[str] = []

    fake = _make_fake_get_json(hits={})

    async def logging_fake(*args: Any, **kwargs: Any) -> Any:
        call_log.append(args[1])
        return await fake(*args, **kwargs)

    monkeypatch.setattr(probe_mod, "get_json", logging_fake)

    async def go() -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn)

    _run(go())
    # No probes for Okta — its candidate "okta" is already in the config
    assert not any("okta" in u and "boards/" in u for u in call_log)
    # Probes still ran for Acme Random
    assert any("acme" in u.lower() for u in call_log)


def test_discover_records_network_error_as_status_zero(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(conn, [("Okta", 5)])

    fake = _make_fake_get_json(
        hits={},
        errors={("greenhouse", "okta"): httpx.ConnectError("boom")},
    )
    monkeypatch.setattr(probe_mod, "get_json", fake)

    async def go() -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn)

    hits = _run(go())
    assert hits == []

    row = conn.execute(
        "SELECT status FROM slug_probes "
        "WHERE company = 'Okta' AND ats = 'greenhouse' AND slug = 'okta'"
    ).fetchone()
    assert row["status"] == 0


def test_discover_skips_staffing_agencies(
    conn: sqlite3.Connection,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(
        conn,
        [
            ("Astra North Infoteck Inc.", 50),
            ("Targeted Talent", 20),
            ("Okta", 5),
        ],
    )

    fake = _make_fake_get_json(hits={("greenhouse", "okta"): 1})
    call_log: list[str] = []

    async def logging_fake(*args: Any, **kwargs: Any) -> Any:
        call_log.append(args[1])
        return await fake(*args, **kwargs)

    monkeypatch.setattr(probe_mod, "get_json", logging_fake)

    async def go() -> list[ProbeOutcome]:
        async with httpx.AsyncClient() as client:
            return await _discover(client, cfg, conn)

    hits = _run(go())
    # Only Okta probed and hit
    assert [h.company for h in hits] == ["Okta"]
    # Staffing names never made any HTTP calls
    assert not any("astra" in u.lower() or "infoteck" in u.lower() for u in call_log)
    assert not any("targeted" in u.lower() or "talent" in u.lower() for u in call_log)


def test_discover_cli_reports_empty_run_with_cached_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    cfg = Config(paths={"db_path": tmp_path / "jobhunt.db", "kb_dir": tmp_path / "kb"})
    profile_dir = cfg.paths.kb_dir / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "verified.json").write_text("{}", encoding="utf-8")

    class DummyConn:
        def close(self) -> None:
            return None

    async def fake_run(*args: Any, **kwargs: Any) -> DiscoverReport:
        return _report(
            companies_seen=5,
            companies_skipped_no_candidates=1,
            companies_skipped_configured=2,
            companies_skipped_cached=2,
        )

    monkeypatch.setattr(discover_cmd, "load_config", lambda: cfg)
    monkeypatch.setattr(discover_cmd, "connect", lambda path: DummyConn())
    monkeypatch.setattr(discover_cmd, "_run", fake_run)

    result = runner.invoke(discover_cmd.app, [])

    assert result.exit_code == 0
    assert "discover: checked 5 companies (limit 100; ats=greenhouse,ashby)" in result.stdout
    assert (
        "discover: probed 0, skipped 2 configured, 1 staffing/unparseable, 2 cached misses"
        in result.stdout
    )
    assert "discover: requests 0 (0 hit, 0 miss, 0 error)" in result.stdout
    assert "discover: no unapplied slugs found." in result.stdout
    assert "discover: re-run with --include-cached to retry 2 cached misses." in result.stdout


def test_discover_cli_reports_hits_and_apply_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    cfg = Config(paths={"db_path": tmp_path / "jobhunt.db", "kb_dir": tmp_path / "kb"})
    profile_dir = cfg.paths.kb_dir / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "verified.json").write_text("{}", encoding="utf-8")

    class DummyConn:
        def close(self) -> None:
            return None

    async def fake_run(*args: Any, **kwargs: Any) -> DiscoverReport:
        return _report(
            companies_seen=3,
            companies_probed=1,
            probes_attempted=2,
            probe_hits=1,
            probe_misses=1,
            cached_hits_reused=1,
            hits=[ProbeOutcome("Okta", "greenhouse", "okta", 200, 42)],
        )

    monkeypatch.setattr(discover_cmd, "load_config", lambda: cfg)
    monkeypatch.setattr(discover_cmd, "connect", lambda path: DummyConn())
    monkeypatch.setattr(discover_cmd, "_run", fake_run)

    result = runner.invoke(discover_cmd.app, [])

    assert result.exit_code == 0
    assert "discover: checked 3 companies (limit 100; ats=greenhouse,ashby)" in result.stdout
    assert "discover: requests 2 (1 hit, 1 miss, 0 error)" in result.stdout
    assert "1 slug(s) ready to apply (1 cached from earlier runs):" in result.stdout
    assert "Okta     greenhouse  okta     42" in result.stdout
    assert "--apply to write these to config.toml" in result.stdout

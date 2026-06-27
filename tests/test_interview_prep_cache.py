"""Phase 13 tests — interview-prep research cache + recruiter-type biasing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobhunt.commands import interview_prep_cmd
from jobhunt.commands.interview_prep_cmd import (
    _cache_path_for,
    _fetch_research,
    _resolve_recruiter_type,
)
from jobhunt.config import Config, IngestConfig, PathsConfig
from jobhunt.pipeline.interview_prep import _RECRUITER_BIAS_BLURB, PrepContext

# --- cache path -----------------------------------------------------------


def test_cache_path_uses_host_and_day(tmp_path: Path) -> None:
    p = _cache_path_for(tmp_path / "cache", "https://acme.example.com/jobs/123", "2026-05-21")
    assert p is not None
    assert p.parent.parent.name == "cache"
    assert p.parent.name == "acme.example.com"
    assert p.name.startswith("2026-05-21__")
    assert p.suffix == ".txt"


def test_cache_path_distinguishes_urls_on_same_host(tmp_path: Path) -> None:
    a = _cache_path_for(tmp_path, "https://acme.com/jobs/1", "2026-05-21")
    b = _cache_path_for(tmp_path, "https://acme.com/", "2026-05-21")
    assert a is not None and b is not None
    assert a != b  # different urls → different hashes


def test_cache_path_returns_none_for_bad_url(tmp_path: Path) -> None:
    # Missing scheme/host.
    assert _cache_path_for(tmp_path, "not-a-url", "2026-05-21") is None


# --- _fetch_research caching ----------------------------------------------


def _cfg(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(data_dir=tmp_path, kb_dir=tmp_path / "kb"),
        ingest=IngestConfig(user_agent="test/1.0"),
    )


def test_fetch_research_writes_then_reads_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    # Allow robots so the test isn't gated on network resolution.
    monkeypatch.setattr(interview_prep_cmd, "robots_allowed", lambda *a, **kw: True)

    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            calls["n"] += 1
            r = MagicMock()
            r.text = f"<html><body>FETCHED {url}</body></html>"
            r.raise_for_status = lambda: None
            return r

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", _FakeClient)

    blob_a = _fetch_research(
        cfg, "https://acme.example.com/jobs/123", force_robots=False
    )
    assert "FETCHED" in blob_a
    n_after_first = calls["n"]

    # Second call same day → cache hit, no new HTTP.
    blob_b = _fetch_research(
        cfg, "https://acme.example.com/jobs/123", force_robots=False
    )
    assert "cache hit" in blob_b
    assert calls["n"] == n_after_first


def test_fetch_research_refresh_bypasses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(interview_prep_cmd, "robots_allowed", lambda *a, **kw: True)

    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            calls["n"] += 1
            r = MagicMock()
            r.text = "FETCHED"
            r.raise_for_status = lambda: None
            return r

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", _FakeClient)

    _fetch_research(cfg, "https://acme.example.com/x", force_robots=False)
    n1 = calls["n"]
    _fetch_research(cfg, "https://acme.example.com/x", force_robots=False, refresh=True)
    assert calls["n"] > n1, "refresh=True should have triggered another HTTP fetch"


def test_fetch_research_robots_disallow_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(interview_prep_cmd, "robots_allowed", lambda *a, **kw: False)
    blob = _fetch_research(cfg, "https://acme.example.com/x", force_robots=False)
    assert "robots.txt disallows" in blob


# --- recruiter_type resolver ----------------------------------------------


def test_resolve_recruiter_type_cli_override_wins(
    tmp_path: Path, migrations_dir: Path
) -> None:
    from jobhunt.db import connect, migrate
    db_path = tmp_path / "x.db"
    c = connect(db_path)
    migrate(c, migrations_dir)
    c.close()
    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path, kb_dir=tmp_path / "kb", db_path=db_path
        ),
    )
    out = _resolve_recruiter_type(cfg, "job-1", override="hiring_manager")
    assert out == "hiring_manager"


def test_resolve_recruiter_type_rejects_invalid_override(
    tmp_path: Path, migrations_dir: Path
) -> None:
    import click

    from jobhunt.db import connect, migrate

    db_path = tmp_path / "x.db"
    c = connect(db_path)
    migrate(c, migrations_dir)
    c.close()
    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path, kb_dir=tmp_path / "kb", db_path=db_path
        ),
    )
    with pytest.raises((SystemExit, click.exceptions.Exit)):
        _resolve_recruiter_type(cfg, "job-1", override="bogus")


def test_resolve_recruiter_type_reads_from_db(
    tmp_path: Path, migrations_dir: Path
) -> None:
    from jobhunt.db import connect, mark_response_received, migrate, upsert_application, upsert_job
    from jobhunt.models import Job

    db_path = tmp_path / "x.db"
    c = connect(db_path)
    migrate(c, migrations_dir)
    j = Job(
        id="greenhouse:acme:1", source="greenhouse", external_id="1",
        company="acme", title="Dev", location="Toronto, ON",
        description="…", url="https://x",
    )
    upsert_job(c, j)
    upsert_application(
        c, application_id="a1", job_id=j.id, status="applied",
        resume_path=None, cover_path=None, fill_plan_path=None,
        applied_week="2026-W21",
    )
    mark_response_received(c, j.id, "2026-05-20", "external_agency")
    c.commit()
    c.close()

    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path, kb_dir=tmp_path / "kb", db_path=db_path
        ),
    )
    out = _resolve_recruiter_type(cfg, j.id, override=None)
    assert out == "external_agency"


def test_resolve_recruiter_type_defaults_to_unknown(
    tmp_path: Path, migrations_dir: Path
) -> None:
    from jobhunt.db import connect, migrate
    db_path = tmp_path / "x.db"
    c = connect(db_path)
    migrate(c, migrations_dir)
    c.close()
    cfg = Config(
        paths=PathsConfig(
            data_dir=tmp_path, kb_dir=tmp_path / "kb", db_path=db_path
        ),
    )
    out = _resolve_recruiter_type(cfg, "no-such-job", override=None)
    assert out == "unknown"


# --- recruiter-bias blurbs sanity -----------------------------------------


def test_all_recruiter_types_have_bias_blurbs() -> None:
    assert set(_RECRUITER_BIAS_BLURB.keys()) == {
        "internal_recruiter", "hiring_manager", "external_agency", "unknown",
    }


def test_recruiter_bias_blurbs_are_distinct() -> None:
    """Each type must produce a meaningfully different bias paragraph so the
    LLM gets different question-mix guidance."""
    vals = list(_RECRUITER_BIAS_BLURB.values())
    assert len(set(vals)) == len(vals)


def test_prep_context_recruiter_type_default_is_unknown() -> None:
    ctx = PrepContext(
        job_id="j", job_title="T", job_company="C",
        job_description="", job_url="", stage="agency",
    )
    assert ctx.recruiter_type == "unknown"


# --- numeric scrubbing in research HTML ----------------------------------


def test_strip_html_scrubs_decimals_and_currency() -> None:
    from jobhunt.commands.interview_prep_cmd import _strip_html
    raw = (
        "<html><body>Plans from $99/mo and $1,234.56/yr. Conversion at "
        "17.32%. Trusted by 1,247 teams. Founded 2019.</body></html>"
    )
    out = _strip_html(raw)
    # Decimals + currency + thousands-separated all get replaced.
    assert "$99" not in out
    assert "1,234.56" not in out
    assert "17.32" not in out
    assert "1,247" not in out
    # Year-like 4-digit standalone integers survive (Founded 2019).
    assert "2019" in out


def test_strip_html_preserves_identifier_like_tokens() -> None:
    """ES6, q5_0, h.264 etc. must NOT be scrubbed — they're legit tech
    identifiers, not employer stats. The regex's lookbehind on
    `[A-Za-z_]` protects them."""
    from jobhunt.commands.interview_prep_cmd import _strip_html
    out = _strip_html("<p>We use ES6 + h.264 codecs and the q5_0 quant.</p>")
    assert "ES6" in out
    # 'h.264' contains a decimal but follows a letter — should survive.
    assert "h.264" in out
    assert "q5_0" in out


# --- retry hint -----------------------------------------------------------


def test_revision_hint_adds_research_blob_callout_for_unverified_numbers() -> None:
    from jobhunt.pipeline.interview_prep import _format_revision_hint
    hint = _format_revision_hint(
        ["unverified number: '17.32'", "unverified number: '100'"], attempt=1
    )
    assert "research_blob" in hint
    assert "EMPLOYER" in hint
    # Other violations should still get the generic "rewrite from scratch" line.
    assert "verified_facts" in hint


def test_revision_hint_omits_research_callout_when_unrelated() -> None:
    from jobhunt.pipeline.interview_prep import _format_revision_hint
    hint = _format_revision_hint(
        ["banned phrase: 'spearheaded'"], attempt=1
    )
    assert "research_blob" not in hint

"""Phase 2 tests — list verdict / no-reply / older-than filters."""
from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from jobhunt.commands.list_cmd import (
    VALID_VERDICTS,
    _AuditSummary,
    _load_audit_summary,
    _parse_older_than,
    _query,
    run,
)
from jobhunt.config import Config, PathsConfig
from jobhunt.db import (
    connect,
    mark_response_received,
    migrate,
    upsert_application,
    upsert_job,
    write_score,
)
from jobhunt.models import Job


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _job(suffix: str) -> Job:
    return Job(
        id=f"greenhouse:acme:{suffix}",
        source="greenhouse",
        external_id=suffix,
        company="acme",
        title=f"Dev {suffix}",
        location="Toronto, ON",
        description="…",
        url=f"https://example.com/{suffix}",
    )


def _apply(conn, job_id: str, *, status: str = "applied") -> None:
    upsert_application(
        conn,
        application_id=f"app-{job_id}",
        job_id=job_id,
        status=status,
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week="2026-W21",
    )


def _score(conn, job_id: str, score: int) -> None:
    write_score(
        conn,
        job_id=job_id,
        score=score,
        reasons=[],
        red_flags=[],
        must_clarify=[],
        model="test",
        prompt_hash="test",
    )


# --- _query: default apply targets ------------------------------------------


def test_run_default_limit_is_10() -> None:
    option = inspect.signature(run).parameters["limit"].default
    assert option.default == 10


def test_default_query_shows_scored_unapplied_non_declined_jobs(conn) -> None:
    for suffix, score in (("a", 80), ("b", 95), ("c", 99), ("d", 70), ("e", 90)):
        upsert_job(conn, _job(suffix))
        _score(conn, f"greenhouse:acme:{suffix}", score)
    upsert_job(conn, _job("unscored"))
    _apply(conn, "greenhouse:acme:c", status="applied")
    _apply(conn, "greenhouse:acme:d", status="drafted")
    conn.execute(
        "UPDATE jobs SET decline_reason = ? WHERE id = ?",
        ("not a fit", "greenhouse:acme:e"),
    )

    rows = _query(
        conn,
        week_label=None,
        status=None,
        min_score=None,
        source=None,
        no_reply=False,
        applied_before=None,
        limit=10,
        default_apply_targets=True,
    )

    assert [r["id"] for r in rows] == [
        "greenhouse:acme:b",
        "greenhouse:acme:a",
        "greenhouse:acme:d",
    ]


def test_selected_statuses_show_requested_lifecycle_rows(conn) -> None:
    for suffix, status in (("a", "applied"), ("b", "drafted"), ("c", "withdrawn")):
        upsert_job(conn, _job(suffix))
        _score(conn, f"greenhouse:acme:{suffix}", 80)
        _apply(conn, f"greenhouse:acme:{suffix}", status=status)

    rows = _query(
        conn,
        week_label=None,
        status=None,
        min_score=None,
        source=None,
        no_reply=False,
        applied_before=None,
        limit=10,
        selected_statuses=("drafted", "withdrawn"),
    )

    assert {r["status"] for r in rows} == {"drafted", "withdrawn"}
    assert {r["id"] for r in rows} == {"greenhouse:acme:b", "greenhouse:acme:c"}


# --- _parse_older_than ------------------------------------------------------


def test_parse_older_than_days() -> None:
    today = date.today()
    out = _parse_older_than("14d")
    assert out == (today - timedelta(days=14)).isoformat()


def test_parse_older_than_weeks() -> None:
    today = date.today()
    out = _parse_older_than("2w")
    assert out == (today - timedelta(days=14)).isoformat()


def test_parse_older_than_none() -> None:
    assert _parse_older_than(None) is None


def test_parse_older_than_invalid_exits(capsys) -> None:
    import click
    with pytest.raises((SystemExit, click.exceptions.Exit)):
        _parse_older_than("two weeks")


# --- _query: --no-reply -----------------------------------------------------


def test_no_reply_excludes_responded(conn) -> None:
    upsert_job(conn, _job("a"))
    upsert_job(conn, _job("b"))
    _apply(conn, "greenhouse:acme:a")
    _apply(conn, "greenhouse:acme:b")
    mark_response_received(conn, "greenhouse:acme:b", "2026-05-20", None)

    rows = _query(
        conn,
        week_label=None,
        status=None,
        min_score=None,
        source=None,
        no_reply=True,
        applied_before=None,
        limit=20,
    )
    ids = {r["id"] for r in rows}
    assert ids == {"greenhouse:acme:a"}


def test_no_reply_excludes_drafted(conn) -> None:
    """Drafted applications have applied_at=NULL — should NOT surface in
    --no-reply because they were never submitted."""
    upsert_job(conn, _job("a"))
    _apply(conn, "greenhouse:acme:a", status="drafted")

    rows = _query(
        conn,
        week_label=None, status=None, min_score=None, source=None,
        no_reply=True, applied_before=None, limit=20,
    )
    assert rows == []


# --- _query: --older-than ---------------------------------------------------


def test_older_than_filters_recent(conn) -> None:
    upsert_job(conn, _job("a"))
    _apply(conn, "greenhouse:acme:a")  # applied_at = CURRENT_TIMESTAMP
    far_past = (date.today() - timedelta(days=30)).isoformat()

    rows = _query(
        conn,
        week_label=None, status=None, min_score=None, source=None,
        no_reply=False, applied_before=far_past, limit=20,
    )
    assert rows == []


def test_older_than_includes_old(conn) -> None:
    upsert_job(conn, _job("a"))
    _apply(conn, "greenhouse:acme:a")
    conn.execute(
        "UPDATE applications SET applied_at = ? WHERE job_id = ?",
        ("2026-01-01 00:00:00", "greenhouse:acme:a"),
    )
    cutoff = (date.today() - timedelta(days=14)).isoformat()

    rows = _query(
        conn,
        week_label=None, status=None, min_score=None, source=None,
        no_reply=False, applied_before=cutoff, limit=20,
    )
    assert {r["id"] for r in rows} == {"greenhouse:acme:a"}


# --- _load_audit_summary ----------------------------------------------------


def _write_audit(tmp_path: Path, job_id: str, verdict: str, coverage: int) -> Config:
    safe = job_id.replace(":", "_")
    audit_dir = tmp_path / "applications" / safe
    audit_dir.mkdir(parents=True)
    (audit_dir / "audit.json").write_text(
        json.dumps({"verdict": verdict, "keyword_coverage_pct": coverage})
    )
    return Config(paths=PathsConfig(data_dir=tmp_path, kb_dir=tmp_path / "kb"))


def test_load_audit_summary_returns_verdict_and_coverage(tmp_path: Path) -> None:
    cfg = _write_audit(tmp_path, "greenhouse:acme:1", "ship", 85)
    s = _load_audit_summary(cfg, "greenhouse:acme:1")
    assert s == _AuditSummary(verdict="ship", coverage_pct=85)


def test_load_audit_summary_missing_file_is_blank(tmp_path: Path) -> None:
    cfg = Config(paths=PathsConfig(data_dir=tmp_path, kb_dir=tmp_path / "kb"))
    s = _load_audit_summary(cfg, "greenhouse:acme:none")
    assert s == _AuditSummary(verdict=None, coverage_pct=None)


def test_load_audit_summary_rejects_unknown_verdict(tmp_path: Path) -> None:
    cfg = _write_audit(tmp_path, "greenhouse:acme:1", "bogus", 50)
    s = _load_audit_summary(cfg, "greenhouse:acme:1")
    assert s.verdict is None
    assert s.coverage_pct == 50


def test_valid_verdicts_constant() -> None:
    assert set(VALID_VERDICTS) == {"ship", "revise", "block"}

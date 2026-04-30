from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.db import (
    connect,
    migrate,
    set_decline_reason,
    unscored_jobs,
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


def _job(suffix: str = "1") -> Job:
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


def test_upsert_job_is_idempotent(conn):
    assert upsert_job(conn, _job()) is True
    assert upsert_job(conn, _job()) is False
    rows = list(conn.execute("SELECT id FROM jobs"))
    assert len(rows) == 1


def test_unscored_jobs_excludes_scored(conn):
    upsert_job(conn, _job("1"))
    upsert_job(conn, _job("2"))
    write_score(
        conn,
        job_id="greenhouse:acme:1",
        score=80,
        reasons=["match"],
        red_flags=[],
        must_clarify=[],
        model="qwen3:8b",
        prompt_hash="abc",
    )
    rows = unscored_jobs(conn)
    ids = {r["id"] for r in rows}
    assert ids == {"greenhouse:acme:2"}


def test_set_decline_reason(conn):
    upsert_job(conn, _job())
    set_decline_reason(conn, "greenhouse:acme:1", "5+ years required")
    row = conn.execute(
        "SELECT decline_reason FROM jobs WHERE id = ?", ("greenhouse:acme:1",)
    ).fetchone()
    assert row["decline_reason"] == "5+ years required"


def test_upsert_application_status_transitions(conn):
    upsert_job(conn, _job())
    upsert_application(
        conn,
        application_id="app-1",
        job_id="greenhouse:acme:1",
        status="drafted",
        resume_path="/r.docx",
        cover_path="/c.md",
        fill_plan_path=None,
        applied_week="2026-W18",
    )
    row = conn.execute(
        "SELECT status, resume_path, applied_at FROM applications WHERE job_id = ?",
        ("greenhouse:acme:1",),
    ).fetchone()
    assert row["status"] == "drafted"
    assert row["applied_at"] is None  # only set when status == 'applied'

    # Bump to applied — applied_at should populate, resume_path should persist.
    upsert_application(
        conn,
        application_id="app-1",
        job_id="greenhouse:acme:1",
        status="applied",
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week=None,
    )
    row = conn.execute(
        "SELECT status, resume_path, applied_at FROM applications WHERE job_id = ?",
        ("greenhouse:acme:1",),
    ).fetchone()
    assert row["status"] == "applied"
    assert row["resume_path"] == "/r.docx"  # COALESCE preserved it
    assert row["applied_at"] is not None

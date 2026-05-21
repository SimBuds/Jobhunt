"""Phase 1 tests — response/interview/outcome tracking helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.db import (
    connect,
    mark_interview_scheduled,
    mark_response_received,
    migrate,
    set_outcome,
    upsert_application,
    upsert_job,
)
from jobhunt.models import Job


@pytest.fixture
def conn(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "test.db")
    migrate(c, migrations_dir)
    yield c
    c.close()


def _seed(conn) -> str:
    """Insert a job + drafted application; return the job_id."""
    job = Job(
        id="greenhouse:acme:1",
        source="greenhouse",
        external_id="1",
        company="acme",
        title="Dev",
        location="Toronto, ON",
        description="…",
        url="https://example.com/1",
    )
    upsert_job(conn, job)
    upsert_application(
        conn,
        application_id="app-1",
        job_id=job.id,
        status="applied",
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week="2026-W21",
    )
    return job.id


def test_migration_adds_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
    assert {"response_received_at", "interview_at", "outcome", "recruiter_type"} <= cols


def test_mark_response_received_writes_row(conn):
    job_id = _seed(conn)
    mark_response_received(conn, job_id, "2026-05-20", "external_agency")
    row = conn.execute(
        "SELECT response_received_at, recruiter_type FROM applications WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row["response_received_at"] == "2026-05-20"
    assert row["recruiter_type"] == "external_agency"


def test_mark_response_preserves_recruiter_when_none_passed(conn):
    job_id = _seed(conn)
    mark_response_received(conn, job_id, "2026-05-20", "external_agency")
    mark_response_received(conn, job_id, "2026-05-21", None)
    row = conn.execute(
        "SELECT response_received_at, recruiter_type FROM applications WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert row["response_received_at"] == "2026-05-21"
    assert row["recruiter_type"] == "external_agency"


def test_mark_response_rejects_invalid_recruiter_type(conn):
    job_id = _seed(conn)
    with pytest.raises(ValueError, match="invalid recruiter_type"):
        mark_response_received(conn, job_id, "2026-05-20", "external")


def test_mark_interview_scheduled_promotes_status(conn):
    job_id = _seed(conn)
    mark_interview_scheduled(conn, job_id, "2026-05-25")
    row = conn.execute(
        "SELECT status, interview_at FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert row["interview_at"] == "2026-05-25"
    assert row["status"] == "interviewing"


def test_mark_interview_does_not_demote_terminal_status(conn):
    job_id = _seed(conn)
    upsert_application(
        conn,
        application_id="app-1",
        job_id=job_id,
        status="offer",
        resume_path=None,
        cover_path=None,
        fill_plan_path=None,
        applied_week=None,
    )
    mark_interview_scheduled(conn, job_id, "2026-05-25")
    row = conn.execute("SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    assert row["status"] == "offer"


def test_set_outcome_valid(conn):
    job_id = _seed(conn)
    set_outcome(conn, job_id, "rejected")
    row = conn.execute(
        "SELECT outcome, outcome_at FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert row["outcome"] == "rejected"
    assert row["outcome_at"] is not None


def test_set_outcome_invalid_raises(conn):
    job_id = _seed(conn)
    with pytest.raises(ValueError, match="invalid outcome"):
        set_outcome(conn, job_id, "maybe")


def test_set_outcome_preserves_outcome_at(conn):
    job_id = _seed(conn)
    set_outcome(conn, job_id, "rejected")
    first = conn.execute(
        "SELECT outcome_at FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()["outcome_at"]
    set_outcome(conn, job_id, "withdrawn")
    second = conn.execute(
        "SELECT outcome_at, outcome FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert second["outcome"] == "withdrawn"
    assert second["outcome_at"] == first  # COALESCE preserves first stamp


def test_migrate_is_idempotent(tmp_path: Path, migrations_dir: Path):
    c = connect(tmp_path / "y.db")
    migrate(c, migrations_dir)
    second = migrate(c, migrations_dir)
    assert "0005_response_tracking" not in second.applied
    c.close()

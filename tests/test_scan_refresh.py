"""`scan --refresh` state-reset semantics.

Drafted applications became durable re-apply targets (apply --top/--best and
the list default view select `a.id IS NULL OR a.status = 'drafted'`), so a
refresh must preserve drafted rows, their pinned jobs, and their on-disk
artifact dirs — the same treatment submitted applications get. Only unpinned
jobs (no application row at all) are dropped for re-evaluation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands.apply_cmd import _safe_id
from jobhunt.commands.scan_cmd import _refresh_scan_state
from jobhunt.config import Config, PathsConfig
from jobhunt.db import connect, migrate, upsert_application, upsert_job, write_score
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
        description="Need Python.",
        url=f"https://example.com/{suffix}",
    )


def _seed(conn, suffix: str, *, status: str | None = None) -> None:
    job_id = f"greenhouse:acme:{suffix}"
    upsert_job(conn, _job(suffix))
    write_score(
        conn,
        job_id=job_id,
        score=80,
        reasons=[],
        red_flags=[],
        must_clarify=[],
        model="test",
        prompt_hash="test",
    )
    if status is not None:
        upsert_application(
            conn,
            application_id=f"app-{suffix}",
            job_id=job_id,
            status=status,
            resume_path=None,
            cover_path=None,
            fill_plan_path=None,
            applied_week="2026-W26",
        )


def test_refresh_preserves_drafted_rows_and_artifacts(tmp_path: Path, conn) -> None:
    _seed(conn, "plain")  # scored, no application → dropped
    _seed(conn, "draft", status="drafted")  # preserved
    _seed(conn, "sent", status="applied")  # preserved

    data_dir = tmp_path / "data"
    draft_dir = data_dir / "applications" / _safe_id("greenhouse:acme:draft")
    draft_dir.mkdir(parents=True)
    (draft_dir / "audit.json").write_text("{}")
    cache_dir = data_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / "page.json").write_text("{}")

    cfg = Config(paths=PathsConfig(data_dir=data_dir, db_path=tmp_path / "test.db"))
    _refresh_scan_state(cfg, conn)

    jobs_left = {r[0] for r in conn.execute("SELECT id FROM jobs")}
    assert jobs_left == {"greenhouse:acme:draft", "greenhouse:acme:sent"}

    apps_left = {
        r[0]: r[1]
        for r in conn.execute("SELECT job_id, status FROM applications")
    }
    assert apps_left == {
        "greenhouse:acme:draft": "drafted",
        "greenhouse:acme:sent": "applied",
    }

    # Drafted artifacts survive; the HTTP cache is wiped.
    assert (draft_dir / "audit.json").exists()
    assert not cache_dir.exists()

    # Kept jobs keep their scores (re-scored later via prompt_hash staleness);
    # the unpinned job's score cascade-deleted with it.
    scores_left = {r[0] for r in conn.execute("SELECT job_id FROM scores")}
    assert scores_left == {"greenhouse:acme:draft", "greenhouse:acme:sent"}


def test_refresh_drops_unpinned_jobs_when_no_applications(tmp_path: Path, conn) -> None:
    _seed(conn, "a")
    _seed(conn, "b")
    cfg = Config(paths=PathsConfig(data_dir=tmp_path / "data", db_path=tmp_path / "test.db"))
    _refresh_scan_state(cfg, conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0

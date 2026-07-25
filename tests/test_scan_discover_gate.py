"""Phase A7 — auto-discovery is gated on unapplied-backlog depth.

`scan` kept widening intake while nothing drained the queue (2026-07-24 audit:
113 actionable jobs, ~0 applications/week). The gate throttles discovery
against the *same* "ready to apply" count `list`'s action board shows, so the
number the user sees is the number the gate acts on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.commands import scan_cmd
from jobhunt.config import Config, PathsConfig
from jobhunt.db import connect, migrate, upsert_application, upsert_job, write_score
from jobhunt.models import Job


@pytest.fixture
def cfg(tmp_path: Path, migrations_dir: Path) -> Config:
    cfg = Config(paths=PathsConfig(data_dir=tmp_path, db_path=tmp_path / "t.db"))
    cfg.paths.migrations_dir = migrations_dir
    migrate(connect(cfg.paths.db_path), migrations_dir)
    return cfg


def _seed_ready(cfg: Config, n: int, *, score: int = 80) -> None:
    """n scored, unapplied, non-declined jobs — the actionable backlog."""
    conn = connect(cfg.paths.db_path)
    try:
        for i in range(n):
            jid = f"greenhouse:acme:{i}"
            upsert_job(conn, Job(
                id=jid, source="greenhouse", external_id=str(i),
                company="acme", title=f"Dev {i}", location="Toronto, ON",
                description="…", url=f"https://example.com/{i}",
            ))
            write_score(
                conn, job_id=jid, score=score, reasons=[], red_flags=[],
                must_clarify=[], model="test", prompt_hash="test",
            )
        conn.commit()
    finally:
        conn.close()


def test_backlog_below_ceiling_allows_discovery(cfg: Config) -> None:
    cfg.ingest.discover_backlog_ceiling = 40
    _seed_ready(cfg, 5)
    conn = connect(cfg.paths.db_path)
    try:
        assert scan_cmd._backlog_blocks_discovery(cfg, conn) is False
    finally:
        conn.close()


def test_backlog_at_ceiling_blocks_discovery(cfg: Config) -> None:
    cfg.ingest.discover_backlog_ceiling = 3
    _seed_ready(cfg, 3)
    conn = connect(cfg.paths.db_path)
    try:
        assert scan_cmd._backlog_blocks_discovery(cfg, conn) is True
    finally:
        conn.close()


def test_zero_ceiling_disables_the_gate(cfg: Config) -> None:
    """0 restores pre-A7 behavior: discovery always runs."""
    cfg.ingest.discover_backlog_ceiling = 0
    _seed_ready(cfg, 500)
    conn = connect(cfg.paths.db_path)
    try:
        assert scan_cmd._backlog_blocks_discovery(cfg, conn) is False
    finally:
        conn.close()


def test_applied_and_declined_jobs_do_not_count_toward_backlog(cfg: Config) -> None:
    cfg.ingest.discover_backlog_ceiling = 3
    _seed_ready(cfg, 3)
    conn = connect(cfg.paths.db_path)
    try:
        # One applied, one declined → backlog drops to 1, under the ceiling.
        upsert_application(
            conn, application_id="a1", job_id="greenhouse:acme:0", status="applied",
            resume_path=None, cover_path=None, fill_plan_path=None, applied_week=None,
        )
        conn.execute(
            "UPDATE jobs SET decline_reason = 'wrong_domain' WHERE id = 'greenhouse:acme:1'"
        )
        conn.commit()
        assert scan_cmd._ready_backlog(cfg, conn) == 1
        assert scan_cmd._backlog_blocks_discovery(cfg, conn) is False
    finally:
        conn.close()


def test_gate_respects_configured_min_score(cfg: Config) -> None:
    """Jobs below the floor are not actionable and must not hold the gate shut."""
    cfg.ingest.discover_backlog_ceiling = 2
    cfg.pipeline.min_score = 70
    _seed_ready(cfg, 5, score=60)
    conn = connect(cfg.paths.db_path)
    try:
        assert scan_cmd._ready_backlog(cfg, conn) == 0
        assert scan_cmd._backlog_blocks_discovery(cfg, conn) is False
    finally:
        conn.close()


def test_default_ceiling_is_forty() -> None:
    assert Config().ingest.discover_backlog_ceiling == 40

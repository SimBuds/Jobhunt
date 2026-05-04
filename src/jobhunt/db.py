"""SQLite connection + migration runner. Plain SQL, no ORM."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jobhunt.errors import MigrationError
from jobhunt.models import Job

MIGRATION_FILE_RE = re.compile(r"^(\d{4})_[a-zA-Z0-9_]+\.sql$")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migrations (
            id TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


@dataclass
class MigrationResult:
    applied: list[str]
    skipped: list[str]


def migrate(conn: sqlite3.Connection, migrations_dir: Path) -> MigrationResult:
    if not migrations_dir.exists():
        raise MigrationError(f"migrations dir not found: {migrations_dir}")

    _ensure_migrations_table(conn)
    already = {row["id"] for row in conn.execute("SELECT id FROM migrations")}

    files = sorted(p for p in migrations_dir.iterdir() if MIGRATION_FILE_RE.match(p.name))
    if not files:
        raise MigrationError(f"no migration files in {migrations_dir}")

    applied: list[str] = []
    skipped: list[str] = []
    for path in files:
        mig_id = path.stem
        if mig_id in already:
            skipped.append(mig_id)
            continue
        sql = path.read_text()
        try:
            with conn:
                conn.executescript(sql)
                conn.execute("INSERT INTO migrations (id) VALUES (?)", (mig_id,))
        except sqlite3.Error as e:
            raise MigrationError(f"migration {mig_id} failed: {e}") from e
        applied.append(mig_id)

    return MigrationResult(applied=applied, skipped=skipped)


def upsert_job(conn: sqlite3.Connection, job: Job) -> bool:
    """Insert a job, ignoring conflicts on (source, external_id). Returns True if inserted."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO jobs
            (id, source, external_id, company, title, location, remote_type,
             description, url, posted_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.id,
            job.source,
            job.external_id,
            job.company,
            job.title,
            job.location,
            job.remote_type,
            job.description,
            job.url,
            job.posted_at.isoformat() if job.posted_at else None,
            job.raw_json,
        ),
    )
    return cur.rowcount > 0


def unscored_jobs(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT j.* FROM jobs j "
        "LEFT JOIN scores s ON s.job_id = j.id "
        "WHERE s.job_id IS NULL "
        "ORDER BY j.ingested_at DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql))


def jobs_to_score(
    conn: sqlite3.Connection, *, current_hash: str, limit: int | None = None
) -> list[sqlite3.Row]:
    """Jobs that need (re)scoring: never scored, or scored under a different prompt_hash.

    Each row carries a `prev_hash` column: NULL for new jobs, a string for stale
    ones — the caller can split counts on that.
    """
    sql = (
        "SELECT j.*, s.prompt_hash AS prev_hash FROM jobs j "
        "LEFT JOIN scores s ON s.job_id = j.id "
        "WHERE s.job_id IS NULL OR s.prompt_hash IS NOT ? "
        "ORDER BY (s.job_id IS NULL) DESC, j.ingested_at DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql, (current_hash,)))


_TERMINAL_STATUSES = frozenset({"interviewing", "offer", "rejected", "withdrawn"})


def upsert_application(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    job_id: str,
    status: str,
    resume_path: str | None,
    cover_path: str | None,
    fill_plan_path: str | None,
    applied_week: str | None,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO applications
            (id, job_id, status, resume_path, cover_path, fill_plan_path,
             applied_week, notes, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                CASE WHEN ? = 'applied' THEN CURRENT_TIMESTAMP ELSE NULL END)
        ON CONFLICT(job_id) DO UPDATE SET
            status = excluded.status,
            resume_path = COALESCE(excluded.resume_path, applications.resume_path),
            cover_path = COALESCE(excluded.cover_path, applications.cover_path),
            fill_plan_path = COALESCE(excluded.fill_plan_path, applications.fill_plan_path),
            applied_week = COALESCE(excluded.applied_week, applications.applied_week),
            notes = COALESCE(excluded.notes, applications.notes),
            applied_at = CASE
                WHEN excluded.status = 'applied' AND applications.applied_at IS NULL
                THEN CURRENT_TIMESTAMP ELSE applications.applied_at END,
            outcome_at = CASE
                WHEN excluded.status IN ('interviewing','offer','rejected','withdrawn')
                     AND applications.outcome_at IS NULL
                THEN CURRENT_TIMESTAMP ELSE applications.outcome_at END
        """,
        (
            application_id,
            job_id,
            status,
            resume_path,
            cover_path,
            fill_plan_path,
            applied_week,
            notes,
            status,
        ),
    )


def set_decline_reason(conn: sqlite3.Connection, job_id: str, reason: str | None) -> None:
    conn.execute("UPDATE jobs SET decline_reason = ? WHERE id = ?", (reason, job_id))


def write_score(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    score: int,
    reasons: list[str],
    red_flags: list[str],
    must_clarify: list[str],
    model: str,
    prompt_hash: str,
) -> None:
    import json as _json

    conn.execute(
        """
        INSERT OR REPLACE INTO scores
            (job_id, score, reasons, red_flags, must_clarify, model, prompt_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            score,
            _json.dumps(reasons),
            _json.dumps(red_flags),
            _json.dumps(must_clarify),
            model,
            prompt_hash,
        ),
    )

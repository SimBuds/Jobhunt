from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from jobhunt.db import connect, migrate
from jobhunt.errors import MigrationError


def test_migrations_run_idempotently(tmp_path: Path, migrations_dir: Path) -> None:
    db_path = tmp_path / "jobhunt.db"
    conn = connect(db_path)
    first = migrate(conn, migrations_dir)
    assert "0001_init" in first.applied

    second = migrate(conn, migrations_dir)
    assert second.applied == []
    assert "0001_init" in second.skipped
    conn.close()


def test_jobs_table_schema(tmp_path: Path, migrations_dir: Path) -> None:
    db_path = tmp_path / "jobhunt.db"
    conn = connect(db_path)
    migrate(conn, migrations_dir)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    expected = {
        "id", "source", "external_id", "company", "title", "location",
        "remote_type", "description", "url", "posted_at", "ingested_at", "raw_json",
    }
    assert expected.issubset(cols)
    conn.close()


def test_unique_source_external_id(tmp_path: Path, migrations_dir: Path) -> None:
    db_path = tmp_path / "jobhunt.db"
    conn = connect(db_path)
    migrate(conn, migrations_dir)

    job = (str(uuid.uuid4()), "greenhouse", "abc-123", "ExampleCo", "Engineer")
    conn.execute(
        "INSERT INTO jobs (id, source, external_id, company, title) VALUES (?, ?, ?, ?, ?)",
        job,
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, source, external_id, company, title) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "greenhouse", "abc-123", "ExampleCo", "Engineer"),
        )
        conn.commit()
    conn.close()


def test_missing_migrations_dir_raises(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.db")
    with pytest.raises(MigrationError):
        migrate(conn, tmp_path / "does-not-exist")
    conn.close()

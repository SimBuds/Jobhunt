"""SQLite connection + migration runner. Plain SQL, no ORM."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jobhunt.errors import MigrationError

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

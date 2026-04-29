"""`jobhunt db ...` — works in phase 0."""

from __future__ import annotations

import typer

from jobhunt.config import load_config
from jobhunt.db import connect, migrate

app = typer.Typer(help="Database management.", no_args_is_help=True)


@app.command("init")
def init() -> None:
    """Create the database file and apply all migrations."""
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        result = migrate(conn, cfg.paths.migrations_dir)
    finally:
        conn.close()
    typer.echo(f"db: {cfg.paths.db_path}")
    typer.echo(f"applied: {result.applied or '(none)'}")
    typer.echo(f"already-applied: {result.skipped or '(none)'}")


@app.command("migrate")
def migrate_cmd() -> None:
    """Apply any pending migrations."""
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        result = migrate(conn, cfg.paths.migrations_dir)
    finally:
        conn.close()
    if result.applied:
        typer.echo(f"applied: {result.applied}")
    else:
        typer.echo("no migrations to apply")

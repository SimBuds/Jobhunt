"""`jobhunt db ...` — works in phase 0."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from jobhunt.config import Config, load_config
from jobhunt.db import connect, migrate

app = typer.Typer(help="Database management.", no_args_is_help=True)


def _reset_targets(cfg: Config) -> list[Path]:
    """Paths `reset` removes: DB (+WAL siblings), tailored docs, HTTP cache,
    interview-prep docs, standalone answers, browser profile, parsed resume.

    Job-scoped answers (`data/applications/<id>/answers/`) ride along with the
    `applications` dir, so only the top-level `answers/` dir is listed.
    """
    db_path = Path(cfg.paths.db_path)
    data_dir = Path(cfg.paths.data_dir)
    return [
        db_path,
        db_path.with_suffix(db_path.suffix + "-shm"),
        db_path.with_suffix(db_path.suffix + "-wal"),
        data_dir / "applications",
        data_dir / "cache",
        data_dir / "interview-prep",
        data_dir / "answers",
        Path(cfg.browser.user_data_dir),
        cfg.paths.kb_dir / "profile",
    ]


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


@app.command("reset")
def reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Wipe DB, tailored docs, HTTP cache, interview-prep docs, saved answers,
    browser profile, and parsed resume, then re-init the database.

    Removes data/jobhunt.db (+WAL siblings), data/applications/, data/cache/,
    data/interview-prep/, data/answers/, the Playwright user_data_dir, and
    kb/profile/ (the convert-resume output). Re-runs all migrations so the
    database is left ready to scan, then reminds the user to re-run
    `convert-resume` before scanning.
    """
    cfg = load_config()
    targets = _reset_targets(cfg)

    existing = [p for p in targets if p.exists()]
    if not existing:
        typer.echo("reset: nothing to remove — already clean.")
    else:
        typer.echo("reset will remove:")
        for p in existing:
            typer.echo(f"  - {p}")
        if not force:
            answer = typer.prompt("type 'yes' to confirm", default="no", show_default=False)
            if answer.strip().lower() not in ("yes", "y"):
                typer.echo("reset: cancelled.")
                raise typer.Exit(code=1)

        removed_files = 0
        removed_dirs = 0
        for p in existing:
            if p.is_dir():
                shutil.rmtree(p)
                removed_dirs += 1
            else:
                p.unlink(missing_ok=True)
                removed_files += 1
        typer.echo(f"reset: removed {removed_files} file(s), {removed_dirs} dir(s)")

    conn = connect(cfg.paths.db_path)
    try:
        result = migrate(conn, cfg.paths.migrations_dir)
    finally:
        conn.close()
    typer.echo(f"reset: db re-initialised ({len(result.applied)} migration(s) applied)")
    typer.echo("next: run `jobhunt convert-resume` to regenerate kb/profile/.")

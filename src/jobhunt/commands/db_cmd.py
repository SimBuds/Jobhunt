"""`jobhunt db ...` — works in phase 0."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import typer

from jobhunt.config import Config, load_config
from jobhunt.db import connect, migrate, upsert_application

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


@dataclass
class _Orphan:
    """An `applications/<dir>` with no row in the `applications` table.

    `kind` drives what `gc` may do with it:
      - `blocked`  — audit.json says verdict=block. Writing audit.json with no
        docs and no application row is the *correct* outcome for a blocked
        job (see the audit rules), so this is not really an orphan and `gc`
        never actions it. Reported only so the count reconciles.
      - `adoptable` — holds rendered .docx files: real finished work that
        predates the A1 early-write fix. Recoverable as a `drafted` row.
      - `stale` — no rendered docs. A run that died between audit and render,
        or an empty shell. Nothing to recover.
    """

    path: Path
    kind: str
    job_id: str | None = None
    docx: list[Path] = field(default_factory=list)


def _verdict_of(app_dir: Path) -> str | None:
    audit = app_dir / "audit.json"
    if not audit.is_file():
        return None
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    verdict = data.get("verdict")
    return str(verdict) if isinstance(verdict, str) else None


def _classify_orphans(cfg: Config, conn: sqlite3.Connection) -> list[_Orphan]:
    """Diff `data/applications/` against the `applications` table.

    Directory names are `apply_cmd._safe_id(job.id)`, which is lossy (`:` and
    `/` both collapse to `_`), so the only correct inversion is to re-apply the
    same function to every known job id and match on the result.
    """
    from jobhunt.commands.apply_cmd import _safe_id

    apps_dir = Path(cfg.paths.data_dir) / "applications"
    if not apps_dir.is_dir():
        return []

    cur = conn.execute("SELECT job_id FROM applications")
    tracked = {_safe_id(str(r[0])) for r in cur.fetchall()}
    cur = conn.execute("SELECT id FROM jobs")
    jobs_by_safe = {_safe_id(str(r[0])): str(r[0]) for r in cur.fetchall()}

    orphans: list[_Orphan] = []
    for path in sorted(p for p in apps_dir.iterdir() if p.is_dir()):
        if path.name in tracked:
            continue
        if _verdict_of(path) == "block":
            orphans.append(_Orphan(path=path, kind="blocked"))
            continue
        docx = sorted(path.glob("*.docx"))
        orphans.append(
            _Orphan(
                path=path,
                kind="adoptable" if docx else "stale",
                job_id=jobs_by_safe.get(path.name),
                docx=docx,
            )
        )
    return orphans


def _adopt(conn: sqlite3.Connection, orphan: _Orphan) -> bool:
    """Write a `drafted` row for an orphan holding rendered documents.

    `applied_week` comes from the directory's mtime, not today — this is a
    backfill, and dating it now would corrupt the weekly rollup.
    """
    if orphan.job_id is None:
        return False
    cover = next((p for p in orphan.docx if "cover" in p.name.lower()), None)
    resume = next((p for p in orphan.docx if p is not cover), None)
    iso = date.fromtimestamp(orphan.path.stat().st_mtime).isocalendar()
    plan = orphan.path / "fill-plan.json"
    upsert_application(
        conn,
        application_id=str(uuid.uuid4()),
        job_id=orphan.job_id,
        status="drafted",
        resume_path=str(resume) if resume else None,
        cover_path=str(cover) if cover else None,
        fill_plan_path=str(plan) if plan.is_file() else None,
        applied_week=f"{iso.year}-W{iso.week:02d}",
        notes="adopted by `db gc` — artifacts predate the row",
    )
    return True


@app.command("gc")
def gc(
    adopt: bool = typer.Option(
        False, "--adopt", help="Write drafted rows for orphan dirs holding .docx files."
    ),
    prune: bool = typer.Option(
        False, "--prune", help="Delete orphan dirs holding no rendered documents."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip the prune confirmation."),
) -> None:
    """Reconcile data/applications/ against the applications table.

    Bare `gc` only reports. Directories whose audit verdict is `block` are
    listed but never actioned — no row is the correct state for those.
    """
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        migrate(conn, cfg.paths.migrations_dir)
        orphans = _classify_orphans(cfg, conn)
        if not orphans:
            typer.echo("gc: no orphan directories — data/applications/ is in sync.")
            return

        by_kind = {
            k: [o for o in orphans if o.kind == k]
            for k in ("adoptable", "stale", "blocked")
        }
        for kind, label in (
            ("adoptable", "adoptable (rendered docs, no row)"),
            ("stale", "stale (no rendered docs)"),
            ("blocked", "blocked verdict (expected — not an orphan)"),
        ):
            group = by_kind[kind]
            if not group:
                continue
            typer.echo(f"\n{label}: {len(group)}")
            for o in group:
                n = len(list(o.path.iterdir()))
                missing = "" if o.kind != "adoptable" or o.job_id else "  ! job row gone"
                typer.echo(f"  {o.path.name}  ({n} file(s)){missing}")

        if adopt:
            done = sum(1 for o in by_kind["adoptable"] if _adopt(conn, o))
            conn.commit()
            skipped = len(by_kind["adoptable"]) - done
            tail = f", {skipped} skipped (job row gone)" if skipped else ""
            typer.echo(f"\ngc: adopted {done} dir(s) as drafted{tail}")

        if prune:
            targets = by_kind["stale"]
            if not targets:
                typer.echo("\ngc: nothing to prune.")
            else:
                if not force:
                    typer.echo(f"\nprune will delete {len(targets)} dir(s).")
                    answer = typer.prompt(
                        "type 'yes' to confirm", default="no", show_default=False
                    )
                    if answer.strip().lower() not in ("yes", "y"):
                        typer.echo("gc: prune cancelled.")
                        raise typer.Exit(code=1)
                for o in targets:
                    shutil.rmtree(o.path)
                typer.echo(f"\ngc: pruned {len(targets)} dir(s)")

        if not adopt and not prune:
            typer.echo("\ngc: dry run — pass --adopt and/or --prune to act.")
    finally:
        conn.close()

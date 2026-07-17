"""Track applications made outside the pipeline (LinkedIn, Indeed, referrals).

`track applied` logs an application without running any LLM step — it synths
(or attaches to) a job row and writes an `applications` row with a channel
tag. `track response` / `track interview` / `track outcome` update lifecycle
timestamps and accept a fuzzy job reference (company/title fragment) instead
of requiring the full job id.

No LinkedIn/Indeed scraping — `--jd-from-stdin` is the sanctioned intake for
postings the fetcher can't or shouldn't render (same posture as
`apply --description-from-stdin`). Pasting the JD at log time is worth the
ten seconds: the posting will be gone by interview week, and
`jobhunt interview-prep` needs the description.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import date

import typer

from jobhunt.commands._manual_intake import synth_manual_job
from jobhunt.config import load_config
from jobhunt.db import connect, migrate, upsert_application

app = typer.Typer(
    help="Track applications made outside the pipeline (LinkedIn, Indeed, …).",
    no_args_is_help=True,
)

# 'pipeline' is reserved for jobhunt-generated applications (apply_cmd);
# manual logging picks one of these.
CHANNELS = ("linkedin", "indeed", "referral", "recruiter", "company-site", "other")


def _connect_migrated(cfg) -> sqlite3.Connection:
    """Connect and apply pending migrations — `track` may be the first
    command run after an upgrade, same defensive posture as scan_cmd."""
    conn = connect(cfg.paths.db_path)
    migrate(conn, cfg.paths.migrations_dir)
    return conn


def _valid_date(value: str, flag: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as e:
        typer.echo(f"error: {flag} must be YYYY-MM-DD (got {value!r})", err=True)
        raise typer.Exit(code=2) from e


def _resolve_ref(conn: sqlite3.Connection, ref: str) -> str:
    """Resolve a job reference to a job_id with an application row.

    Exact job id wins; otherwise a case-insensitive substring match over
    company + title of application rows. Ambiguity is an error that lists
    the candidates rather than guessing.
    """
    row = conn.execute(
        "SELECT job_id FROM applications WHERE job_id = ?", (ref,)
    ).fetchone()
    if row is not None:
        return ref
    hits = conn.execute(
        """
        SELECT a.job_id, j.company, j.title
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE LOWER(COALESCE(j.company,'') || ' ' || COALESCE(j.title,'')) LIKE ?
        ORDER BY a.applied_at DESC
        """,
        (f"%{ref.lower()}%",),
    ).fetchall()
    if not hits:
        typer.echo(
            f"error: no tracked application matches {ref!r}. "
            "Log it first with `jobhunt track applied`.",
            err=True,
        )
        raise typer.Exit(code=1)
    if len(hits) > 1:
        typer.echo(f"error: {ref!r} is ambiguous — matches:", err=True)
        for h in hits:
            typer.echo(f"  {h['job_id']}  {h['company']} — {h['title']}", err=True)
        raise typer.Exit(code=1)
    return hits[0]["job_id"]


@app.command("applied", help="Log an application submitted outside the pipeline.")
def applied(
    ref: str = typer.Argument(
        ...,
        help="Posting URL, or an existing job id from `jobhunt list`.",
    ),
    channel: str = typer.Option(
        ..., "--channel", "-c",
        help=f"Where you applied: {', '.join(CHANNELS)}.",
    ),
    title: str | None = typer.Option(None, "--title", help="Job title (paste path)."),
    company: str | None = typer.Option(None, "--company", help="Company (paste path)."),
    jd_from_stdin: bool = typer.Option(
        False, "--jd-from-stdin",
        help="Read the JD body from stdin (requires --title and --company). "
        "Strongly recommended — postings expire and interview-prep needs it.",
    ),
    when: str | None = typer.Option(
        None, "--when", help="Backdate the application (YYYY-MM-DD, default today)."
    ),
    notes: str | None = typer.Option(None, "--notes", help="Free-form note."),
    force_robots: bool = typer.Option(
        False, "--force-robots", help="Override a robots.txt denial on URL fetch."
    ),
) -> None:
    import asyncio

    if channel not in CHANNELS:
        typer.echo(
            f"error: invalid channel {channel!r}. Allowed: {', '.join(CHANNELS)}",
            err=True,
        )
        raise typer.Exit(code=2)
    applied_on = _valid_date(when, "--when") if when else date.today().isoformat()

    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (ref,)).fetchone()
        if existing is not None:
            job_id = existing["id"]
        else:
            if not ref.lower().startswith(("http://", "https://")):
                typer.echo(
                    f"error: {ref!r} is neither a known job id nor a URL.", err=True
                )
                raise typer.Exit(code=2)
            description: str | None = None
            if jd_from_stdin:
                if not title or not company:
                    typer.echo(
                        "error: --jd-from-stdin requires --title and --company.",
                        err=True,
                    )
                    raise typer.Exit(code=2)
                typer.echo("  reading JD body from stdin (Ctrl-D to finish)...")
                description = sys.stdin.read()
            job = asyncio.run(
                synth_manual_job(
                    cfg,
                    url=ref,
                    title=title,
                    company=company,
                    force_robots=force_robots,
                    description=description,
                )
            )
            job_id = job.id

        iso = date.fromisoformat(applied_on).isocalendar()
        week_label = f"{iso.year}-W{iso.week:02d}"
        with conn:
            upsert_application(
                conn,
                application_id=str(uuid.uuid4()),
                job_id=job_id,
                status="applied",
                resume_path=None,
                cover_path=None,
                fill_plan_path=None,
                applied_week=week_label,
                notes=notes,
                channel=channel,
            )
            # upsert stamps applied_at=now; honor an explicit backdate.
            if when:
                conn.execute(
                    "UPDATE applications SET applied_at = ? WHERE job_id = ?",
                    (applied_on, job_id),
                )
        typer.echo(f"tracked: {job_id} applied via {channel} on {applied_on}")
        typer.echo(f"  when they reply: jobhunt track response {job_id}")
    finally:
        conn.close()


@app.command("response", help="Record a recruiter response to a tracked application.")
def response(
    ref: str = typer.Argument(..., help="Job id, or a company/title fragment."),
    when: str | None = typer.Option(None, "--when", help="YYYY-MM-DD (default today)."),
    recruiter_type: str | None = typer.Option(
        None, "--recruiter-type",
        help="internal_recruiter | hiring_manager | external_agency | unknown",
    ),
) -> None:
    from jobhunt.commands.apply_cmd import _run_lifecycle

    at = _valid_date(when, "--when") if when else date.today().isoformat()
    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        job_id = _resolve_ref(conn, ref)
    finally:
        conn.close()
    _run_lifecycle(
        job_id,
        set_status=None,
        mark_response=at,
        mark_interview=None,
        set_outcome=None,
        recruiter_type=recruiter_type,
    )


@app.command("interview", help="Record a scheduled interview (promotes status).")
def interview(
    ref: str = typer.Argument(..., help="Job id, or a company/title fragment."),
    when: str | None = typer.Option(None, "--when", help="YYYY-MM-DD (default today)."),
) -> None:
    from jobhunt.commands.apply_cmd import _run_lifecycle

    at = _valid_date(when, "--when") if when else date.today().isoformat()
    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        job_id = _resolve_ref(conn, ref)
    finally:
        conn.close()
    _run_lifecycle(
        job_id,
        set_status=None,
        mark_response=None,
        mark_interview=at,
        set_outcome=None,
        recruiter_type=None,
    )


@app.command("outcome", help="Record the terminal outcome of an application.")
def outcome(
    ref: str = typer.Argument(..., help="Job id, or a company/title fragment."),
    result: str = typer.Argument(..., help="offer | rejected | withdrawn | ghosted"),
) -> None:
    from jobhunt.commands.apply_cmd import _run_lifecycle

    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        job_id = _resolve_ref(conn, ref)
    finally:
        conn.close()
    _run_lifecycle(
        job_id,
        set_status=None,
        mark_response=None,
        mark_interview=None,
        set_outcome=result,
        recruiter_type=None,
    )

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
from jobhunt.commands._refs import resolve_job_ref
from jobhunt.config import Config, load_config
from jobhunt.db import connect, migrate, upsert_application

app = typer.Typer(
    help="Track applications made outside the pipeline (LinkedIn, Indeed, …).",
    no_args_is_help=True,
)

# 'pipeline' is reserved for jobhunt-generated applications (apply_cmd);
# manual logging picks one of these.
CHANNELS = ("linkedin", "indeed", "referral", "recruiter", "company-site", "other")


def _connect_migrated(cfg: Config) -> sqlite3.Connection:
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

    Thin delegate to `commands._refs.resolve_job_ref`, kept so the lifecycle
    subcommands (and their tests) have a stable local name. The logic moved out
    verbatim so `apply` and `interview-prep` can share it under the `jobs`
    scope.
    """
    return resolve_job_ref(conn, ref, scope="applied")


@app.command("applied", help="Log an application submitted outside the pipeline.")
def applied(
    ref: str | None = typer.Argument(
        None,
        help="Posting URL, or an existing job id from `jobhunt list`. "
        "Omit for --no-jd backfill of an expired posting with no URL.",
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
    no_jd: bool = typer.Option(
        False, "--no-jd",
        help="Historical backfill: log without a JD (requires --title and "
        "--company). Scoring and interview-prep stay unavailable for the row.",
    ),
    paste: bool = typer.Option(
        False, "--paste",
        help="Read a LinkedIn job-page paste from stdin and auto-extract "
        "title/company/location — paste the whole page (header + 'About the "
        "job') to store the JD too. Explicit --title/--company override.",
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

    from jobhunt.ingest.manual import build_stub_job

    if channel not in CHANNELS:
        typer.echo(
            f"error: invalid channel {channel!r}. Allowed: {', '.join(CHANNELS)}",
            err=True,
        )
        raise typer.Exit(code=2)
    if jd_from_stdin and (paste or no_jd):
        typer.echo(
            "error: --jd-from-stdin cannot be combined with --paste or --no-jd.",
            err=True,
        )
        raise typer.Exit(code=2)
    p_location: str | None = None
    if paste:
        from jobhunt.ingest.manual import MIN_BODY_CHARS, parse_linkedin_paste

        typer.echo("  reading LinkedIn paste from stdin (Ctrl-D to finish)...")
        p_title, p_company, p_location, p_body = parse_linkedin_paste(sys.stdin.read())
        title = title or p_title
        company = company or p_company
        if not title or not company:
            typer.echo(
                "error: could not extract title/company from the paste. "
                "Re-run with --title and --company.",
                err=True,
            )
            raise typer.Exit(code=2)
        if p_body and len(p_body) >= MIN_BODY_CHARS:
            paste_body: str | None = p_body
        else:
            paste_body = None
            no_jd = True  # header-only paste: fall through to the stub path
        typer.echo(
            f"  parsed: {title} @ {company}"
            + (f" ({p_location})" if p_location else "")
            + (" [JD captured]" if paste_body else " [header only — no JD]")
        )
    else:
        paste_body = None
    if ref is None and not no_jd and paste_body is None:
        typer.echo(
            "error: a posting URL or job id is required (or pass --no-jd / "
            "--paste for an expired or header-only posting).",
            err=True,
        )
        raise typer.Exit(code=2)
    applied_on = _valid_date(when, "--when") if when else date.today().isoformat()

    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        existing = (
            conn.execute("SELECT id FROM jobs WHERE id = ?", (ref,)).fetchone()
            if ref is not None
            else None
        )
        if existing is not None:
            job_id = existing["id"]
        elif paste_body is not None:
            from jobhunt.db import upsert_job
            from jobhunt.ingest.manual import build_job_from_text

            # paste_body is only ever set after the paste branch's
            # title/company guard above, so both are present here.
            assert title is not None and company is not None
            url = ref if ref and ref.lower().startswith(("http://", "https://")) else None
            job = build_job_from_text(
                description=paste_body,
                title=title,
                company=company,
                url=url,
                location=p_location,
            )
            with conn:
                upsert_job(conn, job)
            job_id = job.id
        elif no_jd:
            if not title or not company:
                typer.echo("error: --no-jd requires --title and --company.", err=True)
                raise typer.Exit(code=2)
            url = ref if ref and ref.lower().startswith(("http://", "https://")) else None
            job = build_stub_job(title=title, company=company, url=url, location=p_location)
            from jobhunt.db import upsert_job

            with conn:
                upsert_job(conn, job)
            job_id = job.id
            typer.echo("  (no JD stored — scoring/interview-prep unavailable for this row)")
        else:
            assert ref is not None
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


def _stale_no_response(
    conn: sqlite3.Connection, cutoff_iso: str
) -> list[sqlite3.Row]:
    """Applications submitted before `cutoff_iso` that never drew a response.

    Deliberately narrow: `status = 'applied'` only. A row already moved to
    `interviewing`/`offer`/`rejected` has a known outcome, and a `drafted` row
    was never submitted — neither is silence.
    """
    return list(
        conn.execute(
            """
            SELECT a.job_id, a.applied_at, j.company, j.title
            FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE a.status = 'applied'
              AND a.response_received_at IS NULL
              AND a.outcome IS NULL
              AND a.applied_at IS NOT NULL
              AND DATE(a.applied_at) < DATE(?)
            ORDER BY a.applied_at
            """,
            (cutoff_iso,),
        )
    )


@app.command(
    "sweep",
    help="List applications with no response past a threshold; mark them ghosted.",
)
def sweep(
    older_than: str = typer.Option(
        "21d", "--older-than", help="Age threshold, e.g. '14d' or '3w'."
    ),
    apply_: bool = typer.Option(
        False, "--apply", help="Record outcome 'ghosted' for every row listed."
    ),
) -> None:
    """Close the loop on silence.

    Nothing else in the tool ever records a non-response, so applications sit
    in `applied` forever and `analyze funnel` reads permanent silence as
    "still pending" — which is what made the pipeline's 0% response rate
    ambiguous in the 2026-07-24 audit. Bare `sweep` only reports.
    """
    from jobhunt.commands.apply_cmd import _run_lifecycle
    from jobhunt.commands.list_cmd import _parse_older_than

    cutoff = _parse_older_than(older_than)
    assert cutoff is not None  # _parse_older_than only returns None for a None spec
    cfg = load_config()
    conn = _connect_migrated(cfg)
    try:
        rows = _stale_no_response(conn, cutoff)
    finally:
        conn.close()

    if not rows:
        typer.echo(f"sweep: no applications older than {older_than} awaiting a response.")
        return

    typer.echo(f"no response in {older_than} ({len(rows)}):")
    for r in rows:
        applied = str(r["applied_at"])[:10]
        typer.echo(f"  {applied}  {r['company']} — {r['title']}")
        typer.echo(f"              {r['job_id']}")

    if not apply_:
        typer.echo("\nsweep: dry run — pass --apply to mark these ghosted.")
        return

    typer.echo("")
    for r in rows:
        _run_lifecycle(
            str(r["job_id"]),
            set_status=None,
            mark_response=None,
            mark_interview=None,
            set_outcome="ghosted",
            recruiter_type=None,
        )
    typer.echo(f"\nsweep: marked {len(rows)} application(s) ghosted.")


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

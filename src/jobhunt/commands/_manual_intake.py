"""Shared manual-job intake: synth a `manual:` Job from a URL or pasted JD
body and upsert it into the jobs DB.

Extracted from `apply_cmd._resolve_manual` so `interview_prep_cmd` can reuse
the exact same URL-fetch / paste-text / robots-check / upsert path without
duplicating it. Scoring stays in `apply_cmd` (apply-specific); this helper
only produces and persists the Job so any command can then read the row.

No LinkedIn/Indeed/Glassdoor scraping — the paste path (`description=...`) is
the sanctioned way to bring in a posting the renderer can't or shouldn't
fetch.
"""

from __future__ import annotations

import typer

from jobhunt.config import Config
from jobhunt.db import connect, upsert_job
from jobhunt.errors import IngestError
from jobhunt.ingest.manual import (
    build_job_from_text,
    fetch_url_as_job,
    robots_allowed,
)
from jobhunt.models import Job


async def synth_manual_job(
    cfg: Config,
    *,
    url: str | None,
    title: str | None,
    company: str | None,
    force_robots: bool,
    description: str | None = None,
) -> Job:
    """Build a `manual:` Job from a pasted JD body or a fetched URL, upsert it,
    and return it.

    - `description` not None: synth from pasted text (no HTTP). Requires
      `title` and `company` (the paste path can't auto-detect them).
    - else: robots-check `url`, render it, and extract title/company/body.

    Exits (via `typer.Exit`) with a friendly message on robots denial,
    fetch/ingest failure, or undetectable title/company. Never raises a bare
    traceback to the user.
    """
    if description is not None:
        if not title or not company:
            typer.echo(
                "error: pasted-JD intake requires --title and --company.",
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            job = build_job_from_text(
                description=description,
                title=title,
                company=company,
                url=url,
            )
        except IngestError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
    else:
        if not url:
            typer.echo("error: a --url is required when no JD body is pasted.", err=True)
            raise typer.Exit(code=2)
        if not force_robots and not robots_allowed(url, cfg.ingest.user_agent):
            typer.echo(
                f"error: robots.txt disallows {url}; re-run with --force-robots "
                "to override, or paste the JD body instead.",
                err=True,
            )
            raise typer.Exit(code=2)
        typer.echo("  fetching job page...")
        try:
            job = await fetch_url_as_job(
                url,
                user_agent=cfg.ingest.user_agent,
                title_override=title,
                company_override=company,
            )
        except IngestError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e

    if not job.title or not job.company:
        typer.echo(
            "error: could not auto-detect title/company from the page. "
            "Re-run with --title and --company.",
            err=True,
        )
        raise typer.Exit(code=2)

    conn = connect(cfg.paths.db_path)
    try:
        with conn:  # commit the insert so downstream reads (list, prep) see it
            upsert_job(conn, job)
    finally:
        conn.close()
    return job


__all__ = ["synth_manual_job"]

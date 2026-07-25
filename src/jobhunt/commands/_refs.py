"""Shared job-reference resolution for the command layer.

Every daily command takes a job reference, and job ids are not hand-typeable
(`greenhouse:faire:8603123002`, `adzuna_ca:5773420685`). `track` already
accepted a company/title fragment; this module is that logic lifted out so
`apply` and `interview-prep` can accept the same thing.

Two scopes, because the two commands ask different questions:

- ``scope="applied"`` — resolve against rows in `applications`. This is what
  the lifecycle commands (`track response|interview|outcome`) need: you can
  only update something you already logged.
- ``scope="jobs"`` — resolve against any row in `jobs`. This is what `apply`
  and `interview-prep` need, since their targets have no application row yet;
  the `applied` scope's join would exclude every valid target.

Ambiguity is always an error listing the candidates. Guessing which of three
"developer" postings the user meant is the one failure mode worse than making
them type the id.
"""

from __future__ import annotations

import sqlite3

import typer

# Cap the candidate list on an ambiguous match. A bare company fragment can hit
# dozens of postings in the jobs scope, and a wall of ids is not a useful error.
_MAX_CANDIDATES = 10


def _fragment_sql(scope: str) -> str:
    if scope == "applied":
        return """
        SELECT a.job_id AS id, j.company, j.title
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE LOWER(COALESCE(j.company,'') || ' ' || COALESCE(j.title,'')) LIKE ?
        ORDER BY a.applied_at DESC
        """
    return """
    SELECT j.id AS id, j.company, j.title
    FROM jobs j
    WHERE LOWER(COALESCE(j.company,'') || ' ' || COALESCE(j.title,'')) LIKE ?
      AND (j.decline_reason IS NULL OR j.decline_reason = '')
    ORDER BY COALESCE(j.posted_at, j.ingested_at) DESC
    """


def _exact_sql(scope: str) -> str:
    if scope == "applied":
        return "SELECT job_id FROM applications WHERE job_id = ?"
    return "SELECT id FROM jobs WHERE id = ?"


def resolve_job_ref(
    conn: sqlite3.Connection, ref: str, *, scope: str = "applied"
) -> str:
    """Resolve a job reference to a job id.

    Exact id wins; otherwise a case-insensitive substring match over company +
    title. Ambiguity is an error that lists the candidates rather than guessing.

    The `jobs` scope excludes declined postings — a fragment matching only
    declined rows should read as "no match", not resolve to something the
    pipeline already rejected.
    """
    if scope not in ("applied", "jobs"):
        raise ValueError(f"unknown scope {scope!r}")

    row = conn.execute(_exact_sql(scope), (ref,)).fetchone()
    if row is not None:
        return ref

    hits = conn.execute(_fragment_sql(scope), (f"%{ref.lower()}%",)).fetchall()
    if not hits:
        if scope == "applied":
            typer.echo(
                f"error: no tracked application matches {ref!r}. "
                "Log it first with `jobhunt track applied`.",
                err=True,
            )
        else:
            typer.echo(
                f"error: no job matches {ref!r}. Try `jobhunt list` for ids.",
                err=True,
            )
        raise typer.Exit(code=1)
    if len(hits) > 1:
        typer.echo(f"error: {ref!r} is ambiguous — matches:", err=True)
        for h in hits[:_MAX_CANDIDATES]:
            typer.echo(f"  {h['id']}  {h['company']} — {h['title']}", err=True)
        extra = len(hits) - _MAX_CANDIDATES
        if extra > 0:
            typer.echo(f"  … +{extra} more", err=True)
        raise typer.Exit(code=1)
    return str(hits[0]["id"])

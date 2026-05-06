"""`job-seeker list` — pipeline view + weekly rollup."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import typer

from jobhunt.config import load_config
from jobhunt.db import connect

app = typer.Typer(
    help="List scored jobs and weekly application pipeline.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    week: int | None = typer.Option(
        None, "--week", help="Filter applications to a week. 0=current, 1=last, ..."
    ),
    status: str | None = typer.Option(
        None, "--status", help="Filter by application status (drafted/applied/interviewing/...)."
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", help="Filter scored jobs by minimum score."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Filter by source (greenhouse/lever/ashby/adzuna_ca)."
    ),
    limit: int = typer.Option(40, "--limit", help="Max rows to display."),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)
    conn = connect(cfg.paths.db_path)
    try:
        target_week = _iso_week_label(week) if week is not None else None
        rows = _query(
            conn,
            week_label=target_week,
            status=status,
            min_score=min_score,
            source=source,
            limit=limit,
        )
        _render_rows(rows, target_week)
        typer.echo("")
        _render_weekly_footer(conn, target_week or _iso_week_label(0))
    finally:
        conn.close()


def _iso_week_label(weeks_ago: int) -> str:
    target = date.today() - timedelta(weeks=weeks_ago)
    iso = target.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _query(
    conn: sqlite3.Connection,
    *,
    week_label: str | None,
    status: str | None,
    min_score: int | None,
    source: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT j.id, j.source, j.title, j.company, j.location, j.url, "
        "       j.decline_reason, "
        "       s.score, "
        "       a.status, a.applied_week, a.applied_at "
        "FROM jobs j "
        "LEFT JOIN scores s ON s.job_id = j.id "
        "LEFT JOIN applications a ON a.job_id = j.id "
        "WHERE 1=1 "
    )
    params: list[object] = []
    if week_label is not None:
        sql += "AND a.applied_week = ? "
        params.append(week_label)
    if status is not None:
        sql += "AND a.status = ? "
        params.append(status)
    if min_score is not None:
        sql += "AND COALESCE(s.score, -1) >= ? "
        params.append(min_score)
    if source is not None:
        sql += "AND j.source = ? "
        params.append(source)
    sql += "ORDER BY COALESCE(s.score, -1) DESC, j.posted_at DESC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def _render_rows(rows: list[sqlite3.Row], target_week: str | None) -> None:
    header = f"showing {len(rows)} job(s)"
    if target_week:
        header += f" for {target_week}"
    typer.echo(header)
    if not rows:
        return
    for r in rows:
        score = r["score"] if r["score"] is not None else "—"
        status = r["status"] or ("DECLINE" if r["decline_reason"] else "—")
        typer.echo(f"  [{score!s:>3}] [{status:<13}] {r['title']} @ {r['company']}")
        typer.echo(f"           {r['source']} | {r['location']} | {r['id']}")
        if r["url"]:
            typer.echo(f"           {r['url']}")


def _render_weekly_footer(conn: sqlite3.Connection, week_label: str) -> None:
    counts: dict[str, int] = {}
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM applications WHERE applied_week = ? GROUP BY status",
        (week_label,),
    ).fetchall()
    for r in rows:
        counts[r["status"]] = r["n"]

    scanned = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE strftime('%Y-W%W', ingested_at) = ?",
        (week_label,),
    ).fetchone()
    declined = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE decline_reason IS NOT NULL "
        "AND strftime('%Y-W%W', ingested_at) = ?",
        (week_label,),
    ).fetchone()

    parts = [f"{week_label}:"]
    parts.append(f"scanned={scanned['n'] if scanned else 0}")
    parts.append(f"declined={declined['n'] if declined else 0}")
    for s in ("drafted", "applied", "interviewing", "offer", "rejected"):
        parts.append(f"{s}={counts.get(s, 0)}")
    typer.echo(" | ".join(parts))

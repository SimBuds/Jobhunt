"""`jobhunt list` - top apply targets plus pipeline views."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import typer

from jobhunt.commands.apply_cmd import _safe_id
from jobhunt.config import Config, load_config
from jobhunt.db import connect

VALID_VERDICTS = ("ship", "revise", "block")
_VERDICT_PRIORITY = {"ship": 0, "revise": 1, "block": 2}
_OLDER_THAN_RE = re.compile(r"^(\d+)([dw])$")


@dataclass
class _AuditSummary:
    verdict: str | None
    coverage_pct: int | None

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
    drafted: bool = typer.Option(
        False, "--drafted", "--draft", help="Show drafted application rows."
    ),
    applied: bool = typer.Option(
        False, "--applied", help="Show submitted application rows."
    ),
    withdrawn: bool = typer.Option(
        False, "--withdrawn", help="Show withdrawn application rows."
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", help="Filter scored jobs by minimum score."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Filter by source (greenhouse/lever/ashby/adzuna_ca)."
    ),
    verdict: str | None = typer.Option(
        None, "--verdict",
        help=f"Filter applied jobs by audit verdict ({', '.join(VALID_VERDICTS)}).",
    ),
    no_reply: bool = typer.Option(
        False, "--no-reply",
        help="Only applications that were submitted but have no recorded response.",
    ),
    older_than: str | None = typer.Option(
        None, "--older-than",
        help="Only applications submitted before NOW - duration (e.g. 14d, 2w).",
    ),
    limit: int = typer.Option(10, "--limit", help="Max rows to display."),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    if verdict is not None and verdict not in VALID_VERDICTS:
        typer.echo(
            f"error: --verdict must be one of {', '.join(VALID_VERDICTS)}",
            err=True,
        )
        raise typer.Exit(code=2)
    selected_statuses = _selected_flag_statuses(
        drafted=drafted,
        applied=applied,
        withdrawn=withdrawn,
    )
    if status is not None and selected_statuses:
        typer.echo(
            "error: use either --status or lifecycle flags "
            "(--drafted/--applied/--withdrawn), not both.",
            err=True,
        )
        raise typer.Exit(code=2)
    older_than_iso = _parse_older_than(older_than)

    conn = connect(cfg.paths.db_path)
    try:
        target_week = _iso_week_label(week) if week is not None else None
        default_apply_targets = not (
            target_week
            or status is not None
            or selected_statuses
            or verdict is not None
            or no_reply
            or older_than_iso is not None
        )
        rows = _query(
            conn,
            week_label=target_week,
            status=status,
            selected_statuses=selected_statuses,
            min_score=min_score,
            source=source,
            no_reply=no_reply,
            applied_before=older_than_iso,
            default_apply_targets=default_apply_targets,
            # When filtering by verdict, take a generous buffer because verdict
            # lives in audit.json (not SQL) and we filter post-fetch.
            limit=limit if verdict is None else max(limit * 10, 200),
        )
        rows_with_audit = [(r, _load_audit_summary(cfg, r["id"])) for r in rows]
        if verdict is not None:
            rows_with_audit = [
                (r, a) for r, a in rows_with_audit if a.verdict == verdict
            ]
        # Default sort: ship-first then score-desc when no explicit "filter
        # that already implies an order" is set. --week / --status keep the
        # SQL ordering. --verdict implies the user wants verdict-grouped
        # output and we re-sort here.
        if verdict is not None or (week is None and status is None):
            rows_with_audit.sort(
                key=lambda ra: (
                    _VERDICT_PRIORITY.get(ra[1].verdict or "", 9),
                    -(ra[0]["score"] or -1),
                )
            )
        rows_with_audit = rows_with_audit[:limit]

        _render_rows(rows_with_audit, target_week)
        typer.echo("")
        _render_weekly_footer(conn, target_week or _iso_week_label(0))
    finally:
        conn.close()


def _parse_older_than(spec: str | None) -> str | None:
    """`14d` / `2w` → ISO timestamp `applied_at` must be older than. Returns
    None when spec is None. Raises on malformed input."""
    if spec is None:
        return None
    m = _OLDER_THAN_RE.match(spec.strip().lower())
    if not m:
        typer.echo(
            f"error: --older-than {spec!r} must look like '14d' or '2w'.",
            err=True,
        )
        raise typer.Exit(code=2)
    n, unit = int(m.group(1)), m.group(2)
    days = n * (7 if unit == "w" else 1)
    cutoff = date.today() - timedelta(days=days)
    return cutoff.isoformat()


def _selected_flag_statuses(
    *,
    drafted: bool,
    applied: bool,
    withdrawn: bool,
) -> tuple[str, ...]:
    selected: list[str] = []
    if drafted:
        selected.append("drafted")
    if applied:
        selected.append("applied")
    if withdrawn:
        selected.append("withdrawn")
    return tuple(selected)


def _load_audit_summary(cfg: Config, job_id: str) -> _AuditSummary:
    """Read `data/applications/<safe_id>/audit.json` if present and return
    `(verdict, coverage_pct)`. Missing file / malformed JSON / missing keys
    yield `_AuditSummary(None, None)` — the audit file is a side effect of
    `apply`, not load-bearing for `list`.
    """
    audit_path = (
        cfg.paths.data_dir / "applications" / _safe_id(job_id) / "audit.json"
    )
    if not audit_path.is_file():
        return _AuditSummary(verdict=None, coverage_pct=None)
    try:
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _AuditSummary(verdict=None, coverage_pct=None)
    verdict = payload.get("verdict")
    if verdict not in VALID_VERDICTS:
        verdict = None
    pct = payload.get("keyword_coverage_pct")
    return _AuditSummary(
        verdict=verdict,
        coverage_pct=int(pct) if isinstance(pct, int) else None,
    )


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
    no_reply: bool,
    applied_before: str | None,
    limit: int,
    selected_statuses: tuple[str, ...] = (),
    default_apply_targets: bool = False,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT j.id, j.source, j.title, j.company, j.location, j.url, "
        "       j.decline_reason, "
        "       s.score, "
        "       a.status, a.applied_week, a.applied_at, "
        "       a.response_received_at "
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
    elif selected_statuses:
        placeholders = ", ".join("?" for _ in selected_statuses)
        sql += f"AND a.status IN ({placeholders}) "
        params.extend(selected_statuses)
    elif default_apply_targets:
        sql += (
            "AND a.id IS NULL "
            "AND (j.decline_reason IS NULL OR TRIM(j.decline_reason) = '') "
            "AND s.score IS NOT NULL "
        )
    if min_score is not None:
        sql += "AND COALESCE(s.score, -1) >= ? "
        params.append(min_score)
    if source is not None:
        sql += "AND j.source = ? "
        params.append(source)
    if no_reply:
        sql += "AND a.applied_at IS NOT NULL AND a.response_received_at IS NULL "
    if applied_before is not None:
        sql += "AND a.applied_at IS NOT NULL AND a.applied_at < ? "
        params.append(applied_before)
    sql += "ORDER BY COALESCE(s.score, -1) DESC, j.posted_at DESC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def _render_rows(
    rows: list[tuple[sqlite3.Row, _AuditSummary]],
    target_week: str | None,
) -> None:
    header = f"showing {len(rows)} job(s)"
    if target_week:
        header += f" for {target_week}"
    typer.echo(header)
    if not rows:
        return
    for r, audit in rows:
        score = r["score"] if r["score"] is not None else "—"
        status = r["status"] or ("DECLINE" if r["decline_reason"] else "—")
        verdict_tag = f"  {audit.verdict}" if audit.verdict else ""
        cov_tag = (
            f"  cov={audit.coverage_pct}%" if audit.coverage_pct is not None else ""
        )
        reply_tag = ""
        if r["applied_at"] is not None and r["response_received_at"] is None:
            reply_tag = "  no-reply"
        typer.echo(
            f"  [{score!s:>3}] [{status:<13}] {r['title']} @ {r['company']}"
            f"{verdict_tag}{cov_tag}{reply_tag}"
        )
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
        "SELECT COUNT(*) AS n FROM jobs WHERE strftime('%G-W%V', ingested_at) = ?",
        (week_label,),
    ).fetchone()
    declined = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE decline_reason IS NOT NULL "
        "AND strftime('%G-W%V', ingested_at) = ?",
        (week_label,),
    ).fetchone()

    parts = [f"{week_label}:"]
    parts.append(f"scanned={scanned['n'] if scanned else 0}")
    parts.append(f"declined={declined['n'] if declined else 0}")
    for s in ("drafted", "applied", "interviewing", "offer", "rejected", "withdrawn"):
        parts.append(f"{s}={counts.get(s, 0)}")
    typer.echo(" | ".join(parts))

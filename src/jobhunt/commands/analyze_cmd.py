"""`jobhunt analyze` — aggregate analyses over scanned jobs."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

import typer

from jobhunt.config import load_config
from jobhunt.db import connect

app = typer.Typer(
    help="Aggregate analyses over scanned jobs.",
    no_args_is_help=True,
)


@app.command("certs", help="Show the most common certifications across scanned jobs.")
def certs(
    top: int = typer.Option(
        25, "--top", "-n", min=1, max=200,
        help="Number of top certifications to display (default 25).",
    ),
    trend: bool = typer.Option(
        False, "--trend",
        help="Compare two adjacent time windows and show per-cert delta + a "
             "'Potential new certs' review list. Bucket by COALESCE(posted_at, ingested_at).",
    ),
    window_days: int = typer.Option(
        30, "--window-days", min=1, max=365,
        help="Width of each comparison window in days when --trend is set (default 30).",
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", min=0, max=100,
        help="Restrict the tally to jobs you scored at least this high. "
             "Joins `scores`; unscored jobs are excluded. In --trend mode this "
             "adds a `Fit` column + a per-cert `Verdict` (worth pursuing / skip / "
             "wrong direction / etc).",
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    conn = connect(cfg.paths.db_path)
    try:
        if trend:
            _render_trend(conn, top=top, window_days=window_days, min_score=min_score)
        else:
            _render_snapshot(conn, top=top, min_score=min_score)
    finally:
        conn.close()


def _render_snapshot(
    conn: sqlite3.Connection, *, top: int, min_score: int | None
) -> None:
    from jobhunt.analyze.certs import tally

    if min_score is None:
        rows = list(conn.execute(
            "SELECT title, description FROM jobs WHERE description IS NOT NULL"
        ))
        filter_note = ""
    else:
        rows = list(conn.execute(
            """
            SELECT j.title, j.description FROM jobs j
            JOIN scores s ON s.job_id = j.id
            WHERE j.description IS NOT NULL AND s.score >= ?
            """,
            (min_score,),
        ))
        filter_note = f" (fit_filter: score >= {min_score})"

    if not rows:
        msg = "no scored jobs at that threshold." if min_score is not None \
            else "no jobs scanned yet — run `jobhunt scan` first."
        typer.echo(msg)
        raise typer.Exit(code=0)

    counts = tally(rows)
    total_jobs = len(rows)
    typer.echo(f"certification frequency across {total_jobs} scanned job(s){filter_note}\n")
    if not counts:
        typer.echo("no certifications detected in job descriptions.")
        raise typer.Exit(code=0)

    top_items = counts.most_common(top)
    name_w = max(max(len(name) for name, _ in top_items), 12)
    header = f"{'Certification':<{name_w}}  {'Jobs':>5}  {'%':>5}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for name, count in top_items:
        pct = count / total_jobs * 100
        typer.echo(f"{name:<{name_w}}  {count:>5}  {pct:>4.1f}%")


@dataclass(frozen=True)
class _TrendRow:
    name: str
    prev: int
    cur: int
    pct_change: float
    trend_label: str
    fit_cur: int = 0
    verdict: str = ""


# Verdict ordering for sort. Lower number = higher priority (surfaces first).
_VERDICT_PRIORITY: dict[str, int] = {
    "Strong emerging signal": 0,
    "Worth pursuing": 1,
    "Stable staple": 2,
    "Marginal": 3,
    "Late — diminishing": 4,
    "Skip": 5,
    "Wrong direction": 6,
    "": 99,
}


def _classify_verdict(
    *, fit_cur: int, cur: int, trend_label: str, demand_rank: int | None
) -> str:
    """Decision rubric: is this cert worth pursuing?

    `fit_cur` = jobs you scored ≥ min_score that mention this cert in the
                current window.
    `cur`     = total jobs mentioning it in the current window (unfiltered).
    `trend_label` = label from `_classify`.
    `demand_rank` = 1-indexed position by unfiltered `cur` desc; None if not
                    in the top of the market (used only for 'Stable staple').
    """
    if fit_cur == 0 and cur >= 5:
        return "Wrong direction"
    if fit_cur < 3:
        return "Skip"
    if trend_label == "🚀 emerging":
        return "Strong emerging signal"
    if trend_label == "📈 rising":
        return "Worth pursuing"
    if trend_label == "📉 falling":
        return "Late — diminishing"
    if trend_label == "stable" and demand_rank is not None and demand_rank <= 10:
        return "Stable staple"
    return "Marginal"


def _classify(prev: int, cur: int) -> tuple[float, str]:
    """Return (pct_change, label). pct_change is `inf` for emerging entries
    (prev=0) so |Δ%| sorting floats them to the top."""
    if prev == 0 and cur >= 3:
        return float("inf"), "🚀 emerging"
    if prev == 0:
        # cur < 3 with no prior history — present but too noisy to flag.
        return float("inf"), "new (low signal)"
    if cur == 0:
        return -100.0, "dropped"
    pct = (cur - prev) / prev * 100.0
    if pct >= 50.0:
        return pct, "📈 rising"
    if pct <= -50.0:
        return pct, "📉 falling"
    return pct, "stable"


def _fetch_window(
    conn: sqlite3.Connection, *, start_days_ago: int, end_days_ago: int
) -> list[sqlite3.Row]:
    """Return rows whose bucket date falls in [today - start, today - end].
    `start_days_ago` must be ≥ `end_days_ago`. Bucket field is
    `COALESCE(posted_at, ingested_at)`."""
    return list(conn.execute(
        """
        SELECT title, description
        FROM jobs
        WHERE description IS NOT NULL
          AND julianday('now') - julianday(COALESCE(posted_at, ingested_at))
              BETWEEN ? AND ?
        """,
        (end_days_ago, start_days_ago),
    ))


def _fetch_window_with_score(
    conn: sqlite3.Connection,
    *,
    start_days_ago: int,
    end_days_ago: int,
    min_score: int,
) -> list[sqlite3.Row]:
    """Same window as `_fetch_window`, restricted to jobs with score >= min_score."""
    return list(conn.execute(
        """
        SELECT j.title, j.description
        FROM jobs j
        JOIN scores s ON s.job_id = j.id
        WHERE j.description IS NOT NULL
          AND s.score >= ?
          AND julianday('now') - julianday(COALESCE(j.posted_at, j.ingested_at))
              BETWEEN ? AND ?
        """,
        (min_score, end_days_ago, start_days_ago),
    ))


def _render_trend(
    conn: sqlite3.Connection,
    *,
    top: int,
    window_days: int,
    min_score: int | None,
) -> None:
    from jobhunt.analyze.certs import tally, tally_split

    prev_rows = _fetch_window(
        conn, start_days_ago=window_days * 2, end_days_ago=window_days
    )
    cur_rows = _fetch_window(conn, start_days_ago=window_days, end_days_ago=0)

    if not cur_rows and not prev_rows:
        typer.echo("no jobs in either window — run `jobhunt scan` first.")
        raise typer.Exit(code=0)

    prev_counts: Counter[str] = tally(prev_rows) if prev_rows else Counter()
    if cur_rows:
        cur_counts, generic_counts = tally_split(cur_rows)
    else:
        cur_counts = Counter()
        generic_counts = Counter()

    # Fit counts (only when --min-score is set).
    fit_counts: Counter[str] = Counter()
    fit_row_count = 0
    if min_score is not None:
        fit_rows = _fetch_window_with_score(
            conn, start_days_ago=window_days, end_days_ago=0, min_score=min_score
        )
        fit_row_count = len(fit_rows)
        fit_counts = tally(fit_rows) if fit_rows else Counter()

    # Demand-rank (1-indexed) by unfiltered cur, used for "Stable staple" verdict.
    demand_order = [name for name, _ in cur_counts.most_common()]
    demand_rank: dict[str, int] = {name: i + 1 for i, name in enumerate(demand_order)}

    all_names = set(prev_counts) | set(cur_counts)
    trend_rows: list[_TrendRow] = []
    for name in all_names:
        prev = prev_counts.get(name, 0)
        cur = cur_counts.get(name, 0)
        pct, label = _classify(prev, cur)
        fit_cur = fit_counts.get(name, 0)
        verdict = ""
        if min_score is not None:
            verdict = _classify_verdict(
                fit_cur=fit_cur, cur=cur,
                trend_label=label, demand_rank=demand_rank.get(name),
            )
        trend_rows.append(_TrendRow(
            name=name, prev=prev, cur=cur, pct_change=pct, trend_label=label,
            fit_cur=fit_cur, verdict=verdict,
        ))

    if min_score is not None:
        def _key(r: _TrendRow) -> tuple[int, int, float, str]:
            magnitude = float("inf") if r.pct_change == float("inf") else abs(r.pct_change)
            return (
                _VERDICT_PRIORITY.get(r.verdict, 99),
                -r.fit_cur,
                -magnitude,
                r.name,
            )
    else:
        def _key(r: _TrendRow) -> tuple[int, int, float, str]:
            magnitude = float("inf") if r.pct_change == float("inf") else abs(r.pct_change)
            return (0, -r.cur, -magnitude, r.name)
    trend_rows.sort(key=_key)
    top_rows = trend_rows[:top]

    if not top_rows:
        typer.echo("no certifications detected in either window.")
        raise typer.Exit(code=0)

    name_w = max(max(len(r.name) for r in top_rows), 12)
    verdict_w = max((len(r.verdict) for r in top_rows), default=0)
    if min_score is not None:
        header = (
            f"{'Certification':<{name_w}}  {'Prev':>5}  {'Cur':>5}  {'Δ%':>7}  "
            f"{'Trend':<18}  {'Fit':>4}  Verdict"
        )
    else:
        header = (
            f"{'Certification':<{name_w}}  {'Prev':>5}  {'Cur':>5}  "
            f"{'Δ%':>7}  Trend"
        )
    typer.echo(header)
    typer.echo("-" * (len(header) + verdict_w + 4))
    for r in top_rows:
        pct_s = "new" if r.pct_change == float("inf") else f"{r.pct_change:+.0f}%"
        if min_score is not None:
            typer.echo(
                f"{r.name:<{name_w}}  {r.prev:>5}  {r.cur:>5}  {pct_s:>7}  "
                f"{r.trend_label:<18}  {r.fit_cur:>4}  {r.verdict}"
            )
        else:
            typer.echo(
                f"{r.name:<{name_w}}  {r.prev:>5}  {r.cur:>5}  {pct_s:>7}  {r.trend_label}"
            )

    # Potential new certs (generic-regex hits ≥ 2 in the current window).
    review = [(name, count) for name, count in generic_counts.most_common() if count >= 2]
    if review:
        typer.echo("\nPotential new certs (review and consider promoting to _KNOWN):")
        rev_w = max(max(len(n) for n, _ in review), 12)
        for name, count in review[:top]:
            typer.echo(f"  {name:<{rev_w}}  {count:>3} jobs")
    else:
        typer.echo("\nPotential new certs: none (generic-regex tier found nothing ≥ 2 jobs).")

    footer = (
        f"\nwindows: prior {window_days}d ({len(prev_rows)} jobs)  "
        f"current {window_days}d ({len(cur_rows)} jobs)  "
        f"bucket: COALESCE(posted_at, ingested_at)"
    )
    if min_score is not None:
        footer += f"  fit_filter: score >= {min_score} ({fit_row_count} jobs)"
    typer.echo(footer)

"""`jobhunt analyze` — aggregate analyses over scanned jobs."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

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


# === Phase 14: analyze expansion ============================================
#
# Three deterministic, LLM-free subcommands that close the feedback loop on
# the Phases 1 + 4 data foundations:
#
#   analyze skills --gaps        — tech tokens over-represented in declines
#   analyze employers --hiring-velocity — posts per configured slug in window
#   analyze response-rate         — interview rate by score / ATS

# Common tech tokens. Mirrors the families surfaced by the score prompt + the
# audit. Matched against JD bodies as word-boundary regexes so "node" doesn't
# match "nodes" and "react" doesn't match "reactive". Order does not matter.
_TECH_TOKENS = (
    "react", "vue", "angular", "svelte", "next.js", "nextjs", "nuxt", "remix",
    "typescript", "javascript", "node.js", "nodejs", "python", "go", "rust",
    "java", "kotlin", "scala", "ruby", "php", "c#", ".net", "swift",
    "django", "flask", "fastapi", "rails", "laravel", "spring boot", "express",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "dynamodb",
    "graphql", "rest", "grpc", "kafka", "rabbitmq",
    "docker", "kubernetes", "terraform", "aws", "gcp", "azure",
    "shopify", "hubspot", "wordpress", "contentful", "sanity", "strapi",
    "playwright", "cypress", "jest", "vitest",
    "ollama", "langchain", "llamaindex", "openai", "anthropic", "claude",
    "ci/cd", "github actions",
)
_TOKEN_PATTERNS = {
    tok: re.compile(rf"(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])", re.IGNORECASE)
    for tok in _TECH_TOKENS
}


def _slug_from_job_id(job_id: str) -> str:
    """Extract the employer slug from a job.id like `greenhouse:konradgroup:123`."""
    parts = job_id.split(":", 2)
    return parts[1] if len(parts) >= 2 else "?"


def _window_cutoff(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


@app.command("skills", help="Aggregate analyses over skill mentions in scanned jobs.")
def skills(
    gaps: bool = typer.Option(
        False, "--gaps",
        help="Show tech tokens over-represented in declined vs. accepted jobs.",
    ),
    window_days: int = typer.Option(
        30, "--window-days", min=1, max=365,
        help="Limit to jobs ingested in the last N days (default 30).",
    ),
    top: int = typer.Option(
        20, "--top", "-n", min=1, max=100,
        help="Number of top skills to display (default 20).",
    ),
) -> None:
    if not gaps:
        raise typer.BadParameter(
            "specify --gaps (only mode supported today; expand later as needed)"
        )
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    cutoff = _window_cutoff(window_days)
    conn = connect(cfg.paths.db_path)
    try:
        rows = conn.execute(
            """
            SELECT j.title, j.description, j.decline_reason, j.decline_category
            FROM jobs j
            WHERE COALESCE(j.posted_at, j.ingested_at) >= ?
              AND j.description IS NOT NULL AND j.description != ''
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    declined: Counter[str] = Counter()
    accepted: Counter[str] = Counter()
    decline_total = 0
    accept_total = 0
    for r in rows:
        blob = ((r["title"] or "") + "\n" + (r["description"] or "")).lower()
        is_decline = r["decline_reason"] is not None
        if is_decline:
            decline_total += 1
        else:
            accept_total += 1
        for tok, pat in _TOKEN_PATTERNS.items():
            if pat.search(blob):
                (declined if is_decline else accepted)[tok] += 1

    if decline_total == 0:
        typer.echo("no declined jobs in the window — nothing to compare against.")
        return

    rows_out: list[tuple[str, int, int, float]] = []
    for tok in _TECH_TOKENS:
        d = declined.get(tok, 0)
        a = accepted.get(tok, 0)
        if d == 0:
            continue
        d_rate = d / decline_total
        a_rate = a / accept_total if accept_total else 0
        # Over-representation: how much more common in declines than accepted.
        # Use additive delta so tokens absent in accepted aren't infinite.
        delta = d_rate - a_rate
        rows_out.append((tok, d, a, delta))

    rows_out.sort(key=lambda x: -x[3])
    typer.echo(
        f"\nwindow: last {window_days}d "
        f"({decline_total} declined, {accept_total} accepted)\n"
    )
    typer.echo(f"{'Skill':<22} {'Declined':>9} {'Accepted':>9} {'Decline-share Δ':>16}")
    typer.echo("-" * 60)
    for tok, d, a, delta in rows_out[:top]:
        typer.echo(f"{tok:<22} {d:>9} {a:>9} {delta:>+15.1%}")


@app.command("employers", help="Hiring velocity by configured ATS slug.")
def employers(
    hiring_velocity: bool = typer.Option(
        False, "--hiring-velocity",
        help="Counts new posts per configured slug within the window.",
    ),
    window_days: int = typer.Option(
        30, "--window-days", min=1, max=365,
        help="Width of window in days (default 30).",
    ),
) -> None:
    if not hiring_velocity:
        raise typer.BadParameter(
            "specify --hiring-velocity (only mode supported today)"
        )
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    cutoff = _window_cutoff(window_days)
    conn = connect(cfg.paths.db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, source FROM jobs
            WHERE COALESCE(posted_at, ingested_at) >= ?
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    # Aggregate by (source, slug).
    counts: Counter[tuple[str, str]] = Counter()
    for r in rows:
        counts[(r["source"], _slug_from_job_id(r["id"]))] += 1

    # Configured but absent slugs — these are "no posts in window".
    configured: list[tuple[str, str]] = []
    for ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        for slug in getattr(cfg.ingest, ats):
            configured.append((ats, slug))

    seen = set(counts.keys())
    dead = [(ats, slug) for ats, slug in configured if (ats, slug) not in seen]

    if not counts and not dead:
        typer.echo("no configured slugs and no recent posts — nothing to show.")
        return

    typer.echo(f"\nwindow: last {window_days}d\n")
    typer.echo(f"{'ATS':<18} {'Slug':<32} {'Posts':>6}")
    typer.echo("-" * 60)
    for (ats, slug), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        typer.echo(f"{ats:<18} {slug:<32} {n:>6}")

    if dead:
        typer.echo(
            f"\nconfigured but 0 posts in window ({len(dead)} slug(s)) — "
            f"candidates for `jobhunt config reprobe`:"
        )
        for ats, slug in sorted(dead):
            typer.echo(f"  {ats}/{slug}")


@app.command("response-rate", help="Per-bucket interview/response rate.")
def response_rate(
    by: str = typer.Option(
        "score", "--by",
        help="Bucket key: 'score' (band) or 'ats' (source).",
    ),
) -> None:
    if by not in {"score", "ats"}:
        raise typer.BadParameter("--by must be 'score' or 'ats'")
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    conn = connect(cfg.paths.db_path)
    try:
        rows = conn.execute(
            """
            SELECT s.score, j.source, a.status, a.response_received_at
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN scores s ON s.job_id = a.job_id
            WHERE a.applied_at IS NOT NULL
            """,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        typer.echo("no submitted applications yet. Apply to some jobs first.")
        return

    # Bucket key extraction. Response is true if either we explicitly logged
    # a response timestamp OR status moved past `applied` to a downstream
    # state (interviewing / offer / rejected).
    _DOWNSTREAM = {"interviewing", "offer", "rejected"}

    def _bucket(r: sqlite3.Row) -> str:
        if by == "score":
            sc = r["score"]
            if sc is None:
                return "unscored"
            for lo, hi, label in [(85, 101, "85–100"), (75, 85, "75–84"),
                                  (65, 75, "65–74"), (0, 65, "< 65")]:
                if lo <= sc < hi:
                    return label
            return "—"
        return r["source"] or "?"

    def _responded(r: sqlite3.Row) -> bool:
        if r["response_received_at"] is not None:
            return True
        return r["status"] in _DOWNSTREAM

    buckets: dict[str, list[bool]] = {}
    for r in rows:
        buckets.setdefault(_bucket(r), []).append(_responded(r))

    typer.echo(f"\nresponse rate by {by} ({len(rows)} applications):\n")
    typer.echo(f"{'Bucket':<14} {'Applied':>8} {'Responded':>10} {'Rate':>7}")
    typer.echo("-" * 44)
    # Sort by bucket-applied desc for stable, scannable output.
    for key in sorted(buckets, key=lambda k: -len(buckets[k])):
        rs = buckets[key]
        applied = len(rs)
        responded = sum(1 for x in rs if x)
        rate = f"{100 * responded / applied:.0f}%" if applied else "—"
        typer.echo(f"{key:<14} {applied:>8} {responded:>10} {rate:>7}")
    total_responded = sum(1 for r in rows if _responded(r))
    typer.echo("-" * 44)
    typer.echo(
        f"{'TOTAL':<14} {len(rows):>8} {total_responded:>10} "
        f"{100 * total_responded / len(rows):.0f}%"
    )


@app.command("validators", help="Which cover-letter validators fired most over a window.")
def validators(
    window_days: int = typer.Option(
        30, "--window-days", min=1, max=365,
        help="Limit to audit files modified in the last N days (default 30).",
    ),
    top: int = typer.Option(
        20, "--top", "-n", min=1, max=100,
        help="Number of top rules to display (default 20).",
    ),
) -> None:
    """Walks `data/applications/*/audit.json` and aggregates the
    `cover_letter_violations` by rule_id (per `cover_validate.categorize_violation`).

    Use this to find over-broad validators. If `banned_phrase` fires on 80%
    of audits, the watchlist is too aggressive. If `unverified_number` is
    the top hit, the digit-cluster rule needs another carve-out.
    Deterministic, no LLM.
    """
    import json
    from datetime import datetime

    from jobhunt.commands import ensure_profile
    from jobhunt.pipeline.cover_validate import categorize_violation

    cfg = load_config()
    ensure_profile(cfg)

    apps_dir = cfg.paths.data_dir / "applications"
    if not apps_dir.is_dir():
        typer.echo("no applications/ directory yet — apply to some jobs first.")
        return

    cutoff = datetime.now().timestamp() - window_days * 86400
    counts: Counter[str] = Counter()
    audits_seen = 0
    audits_with_violations = 0
    for audit_path in apps_dir.glob("*/audit.json"):
        try:
            mtime = audit_path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        audits_seen += 1
        try:
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        violations = payload.get("cover_letter_violations") or []
        if violations:
            audits_with_violations += 1
        for v in violations:
            counts[categorize_violation(str(v))] += 1

    if audits_seen == 0:
        typer.echo(f"no audit files modified in the last {window_days}d.")
        return

    typer.echo(
        f"\nwindow: last {window_days}d "
        f"({audits_seen} audits, {audits_with_violations} with cover violations)\n"
    )
    if not counts:
        typer.echo("no cover-letter violations in the window. (Healthy!)")
        return

    typer.echo(f"{'Rule':<36} {'Fires':>6}  {'Share':>6}")
    typer.echo("-" * 56)
    for rule_id, n in counts.most_common(top):
        share = n / audits_seen
        typer.echo(f"{rule_id:<36} {n:>6}  {share:>5.0%}")

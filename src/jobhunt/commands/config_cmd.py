"""`jobhunt config ...` — works in phase 0."""

from __future__ import annotations

import asyncio
import json
import tomllib
from collections.abc import Callable
from pathlib import Path

import httpx
import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from jobhunt.commands._config_write import write_config_atomically
from jobhunt.config import Config, config_path, load_config
from jobhunt.discover.probe import ProbeOutcome
from jobhunt.errors import JobHuntError

app = typer.Typer(help="Inspect and manage configuration.", no_args_is_help=True)

# ATSes that have an ingest adapter; mirrored from add_cmd. Kept here to
# avoid an import cycle.
_SEEDABLE_ATSES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday")


def _seed_path() -> Path:
    """The repo-shipped curated seed list. Resolved relative to the package
    so it works from a `uv run` install as well as a source checkout."""
    cfg = load_config()
    return cfg.paths.kb_dir / "seeds" / "gta-employers.toml"


def _load_seeds() -> dict[str, list[str]]:
    path = _seed_path()
    if not path.is_file():
        raise JobHuntError(
            f"seed file not found at {path}.\n"
            "the curated seed list ships with the repo; ensure kb/seeds/ exists."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    seeds: dict[str, list[str]] = {}
    for ats in _SEEDABLE_ATSES:
        entries = data.get(ats, [])
        if not isinstance(entries, list):
            raise JobHuntError(f"seed file: [{ats}] must be a list")
        seeds[ats] = [str(e) for e in entries]
    return seeds


@app.command("seed")
def seed(
    preview: bool = typer.Option(
        False, "--preview", help="Print seeds without writing to config."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Append seeds not already in config.toml."
    ),
) -> None:
    """Import the repo-curated GTA employer seed list into config.toml.

    Run `--preview` first to see what would be added. Run `--apply` to
    write — creates a config.toml.bak snapshot first. Idempotent: re-running
    `--apply` is a no-op if the config already contains every seed."""
    if not preview and not apply:
        raise typer.BadParameter("specify --preview or --apply")
    if preview and apply:
        raise typer.BadParameter("--preview and --apply are mutually exclusive")

    seeds = _load_seeds()
    cfg = load_config()

    additions: dict[str, list[str]] = {}
    for ats in _SEEDABLE_ATSES:
        existing = set(getattr(cfg.ingest, ats))
        new = [s for s in seeds[ats] if s not in existing]
        if new:
            additions[ats] = new

    if not additions:
        typer.echo("nothing to add — config already contains every seed.")
        return

    typer.echo("seeds that would be added:" if preview else "adding seeds:")
    for ats, new in additions.items():
        typer.echo(f"  [ingest.{ats}] +{len(new)}: {', '.join(new)}")

    if preview:
        typer.echo("\n--apply to write these to config.toml")
        return

    for ats, new in additions.items():
        setattr(cfg.ingest, ats, [*getattr(cfg.ingest, ats), *new])

    write_config_atomically(cfg)
    typer.echo(f"\nupdated {config_path()}. backup: {config_path()}.bak")
    typer.echo("note: any inline comments in config.toml were not preserved on write.")


# ATSes whose slugs are probe-verifiable. Workday doesn't expose a cheap
# probe (CXS handshake isn't worth wiring), so we leave its tenants
# alone — they're spec strings, not raw slugs anyway.
_PROBEABLE_ATSES = ("greenhouse", "lever", "ashby", "smartrecruiters")


async def _reprobe_async(
    cfg: Config,
    atses: tuple[str, ...],
    on_progress: Callable[[int, int, str, int], None] | None = None,
) -> list[ProbeOutcome]:
    """Probe every configured slug under `atses`. Returns list of
    ProbeOutcome. Per-host rate-limit shared across the run.

    `on_progress(done, total, current_slug, status)` is called once after each
    probe — status is the int from `ProbeOutcome.status` (200 live, 404/0 stale).
    """
    from jobhunt.discover.probe import _probe_one
    from jobhunt.http import RateLimiter

    limiter = RateLimiter(rate_per_sec=cfg.ingest.rate_limit_per_sec)
    pairs: list[tuple[str, str]] = []
    for ats in atses:
        for slug in getattr(cfg.ingest, ats):
            pairs.append((ats, slug))

    if not pairs:
        return []

    total = len(pairs)
    outcomes: list[ProbeOutcome] = []
    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": cfg.ingest.user_agent}
    ) as client:
        for done, (ats, slug) in enumerate(pairs, start=1):
            outcome = await _probe_one(client, limiter, slug, ats, slug)
            outcomes.append(outcome)
            if on_progress is not None:
                on_progress(done, total, f"{ats}/{slug}", outcome.status)
    return outcomes


@app.command("reprobe")
def reprobe(
    prune: bool = typer.Option(
        False, "--prune",
        help="Remove stale slugs from config.toml. Without --force, prompts before writing.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="With --prune, skip the confirmation prompt.",
    ),
) -> None:
    """Re-probe every configured ATS slug and surface stale ones.

    Walks `cfg.ingest.{greenhouse,lever,ashby,smartrecruiters}`, hits each
    slug's public API, and groups the results. Stale = 404 / 0 / API
    returns 0 postings. Workday is skipped (its CXS endpoint isn't a
    cheap probe). Dry-run by default; `--prune` removes the stale ones
    from config (creates a config.toml.bak first).
    """
    if force and not prune:
        raise typer.BadParameter("--force only applies with --prune")

    cfg = load_config()

    # Quick pre-flight: count what we're about to probe so the user knows
    # the rough wait. Per-host rate limit is 1 req/sec, so probes ≈ N seconds.
    pre_total = sum(len(getattr(cfg.ingest, a)) for a in _PROBEABLE_ATSES)
    if pre_total == 0:
        typer.echo("no configured slugs to probe.")
        return
    typer.echo(
        f"reprobing {pre_total} configured slug(s) "
        f"across {', '.join(_PROBEABLE_ATSES)} "
        f"(~1 req/sec per host; expect ~{pre_total}s)"
    )

    live = miss = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn(
            "[green]{task.fields[live]} live "
            "[red]{task.fields[stale]} stale"
        ),
        transient=False,
    ) as progress:
        task_id = progress.add_task(
            "probing …", total=pre_total, live=0, stale=0
        )

        def _on_progress(done: int, total: int, current: str, status: int) -> None:
            nonlocal live, miss
            if status == 200:
                live += 1
            else:
                miss += 1
            label = current if len(current) <= 36 else current[:34] + "…"
            progress.update(
                task_id,
                description=f"probing {label}",
                completed=done,
                total=total,
                live=live,
                stale=miss,
            )

        outcomes = asyncio.run(
            _reprobe_async(cfg, _PROBEABLE_ATSES, on_progress=_on_progress)
        )
        progress.update(task_id, description="done")

    if not outcomes:
        typer.echo("no configured slugs to probe.")
        return

    by_ats: dict[str, list[ProbeOutcome]] = {}
    for o in outcomes:
        by_ats.setdefault(o.ats, []).append(o)

    stale_by_ats: dict[str, list[str]] = {}
    total_hits = 0
    total_stale = 0
    for ats in _PROBEABLE_ATSES:
        results = by_ats.get(ats, [])
        if not results:
            continue
        hits = [o for o in results if o.status == 200]
        stale = [o for o in results if o.status != 200]
        total_hits += len(hits)
        total_stale += len(stale)
        typer.echo(f"\n[{ats}] {len(hits)} live, {len(stale)} stale")
        for o in hits:
            typer.echo(f"  live  {o.slug:<30} {o.job_count or '?'} job(s)")
        for o in stale:
            reason = (
                "404"
                if o.status == 404
                else ("network/timeout" if o.status == 0 else f"status={o.status}")
            )
            typer.echo(f"  STALE {o.slug:<30} ({reason})")
        if stale:
            stale_by_ats[ats] = [o.slug for o in stale]

    total_probed = sum(len(v) for v in by_ats.values())
    typer.echo(
        f"\nsummary: {total_hits} live, {total_stale} stale across "
        f"{total_probed} configured slugs."
    )

    if not stale_by_ats:
        typer.echo("nothing to prune.")
        return

    if not prune:
        typer.echo("\nrun `jobhunt config reprobe --prune` to remove the stale entries above.")
        return

    if not force:
        typer.echo("\nabout to remove the stale slugs listed above.")
        if not typer.confirm("proceed?", default=False):
            typer.echo("aborted.")
            raise typer.Exit(code=1)

    for ats, stale_slugs in stale_by_ats.items():
        existing = list(getattr(cfg.ingest, ats))
        kept = [s for s in existing if s not in stale_slugs]
        setattr(cfg.ingest, ats, kept)

    write_config_atomically(cfg)
    typer.echo(f"\nupdated {config_path()}. backup: {config_path()}.bak")
    typer.echo("note: any inline comments in config.toml were not preserved on write.")


@app.command("show")
def show() -> None:
    """Print the resolved configuration."""
    cfg = load_config()
    typer.echo(f"# config: {config_path()}")
    typer.echo(json.dumps(cfg.model_dump(mode="json"), indent=2, default=str))


@app.command("path")
def path() -> None:
    """Print the path to the active config file."""
    typer.echo(str(config_path()))


@app.command("calibrate")
def calibrate() -> None:
    """Show interview-rate per score band to help tune --min-score.

    Reads all applications from the DB and groups them by the score the job
    received at scoring time. An application counts as an 'interview' if its
    status is 'interviewing', 'offer', or 'rejected' (i.e. it got far enough
    to generate a response). Designed to be run after ~20+ applications so the
    sample size is useful.
    """
    from jobhunt.db import connect

    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        rows = list(
            conn.execute(
                """
                SELECT
                    s.score,
                    a.status
                FROM applications a
                JOIN scores s ON s.job_id = a.job_id
                WHERE a.status NOT IN ('drafted', 'withdrawn')
                ORDER BY s.score
                """
            )
        )
    finally:
        conn.close()

    if not rows:
        typer.echo("No applications with scores yet. Apply to some jobs first.")
        return

    bands = [
        (85, 101, "85–100"),
        (75, 85, "75–84"),
        (65, 75, "65–74"),
        (0, 65, "< 65"),
    ]
    interview_statuses = {"interviewing", "offer", "rejected"}

    typer.echo(f"\n{'Band':<12} {'Applied':>8} {'Interviews':>11} {'Rate':>7}")
    typer.echo("-" * 42)
    for lo, hi, label in bands:
        band_rows = [r for r in rows if lo <= r["score"] < hi]
        applied = len(band_rows)
        interviews = sum(1 for r in band_rows if r["status"] in interview_statuses)
        rate = f"{100 * interviews / applied:.0f}%" if applied else "—"
        typer.echo(f"{label:<12} {applied:>8} {interviews:>11} {rate:>7}")

    total = len(rows)
    total_interviews = sum(1 for r in rows if r["status"] in interview_statuses)
    typer.echo("-" * 42)
    typer.echo(
        f"{'TOTAL':<12} {total:>8} {total_interviews:>11} "
        f"{100 * total_interviews / total:.0f}%"
        if total
        else ""
    )
    typer.echo(
        "\nCurrent min-score: "
        + str(cfg.pipeline.min_score)
        + "  (set pipeline.min_score in config.toml to change)"
    )

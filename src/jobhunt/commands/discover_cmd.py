"""`jobhunt discover` — find ATS slugs for companies seen in past scans."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable

import httpx
import typer
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from jobhunt.commands._config_write import write_config_atomically
from jobhunt.config import Config, config_path, load_config
from jobhunt.db import connect
from jobhunt.discover.probe import ProbeOutcome, ProgressEvent, discover
from jobhunt.http import DEFAULT_UA

app = typer.Typer(
    help="Discover new ingestion targets from past scan results.",
    no_args_is_help=True,
)

_SUPPORTED_ATSES = ("greenhouse", "ashby", "lever", "smartrecruiters")


def _parse_atses(raw: str) -> list[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        raise typer.BadParameter("--ats must list at least one ATS")
    bad = [p for p in parts if p not in _SUPPORTED_ATSES]
    if bad:
        raise typer.BadParameter(
            f"unsupported ats: {', '.join(bad)} (supported: {', '.join(_SUPPORTED_ATSES)})"
        )
    return parts


@app.command(
    "slugs",
    help=(
        "Find ATS slugs for past-scan companies via URL parsing + public-API "
        "probes (Greenhouse/Ashby/Lever/SmartRecruiters)."
    ),
)
def slugs(
    ats: str = typer.Option(
        "greenhouse,ashby,lever,smartrecruiters",
        "--ats",
        help="Comma-separated ATSes to probe (greenhouse, ashby, lever, smartrecruiters).",
    ),
    limit: int = typer.Option(
        100, "--limit", "-n", min=1, max=2000, help="Cap on companies probed per run."
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Append confirmed slugs to config.toml (writes a .bak snapshot first).",
    ),
    include_cached: bool = typer.Option(
        False,
        "--include-cached",
        help="Re-probe companies previously cached as misses.",
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    atses = _parse_atses(ats)
    cfg = load_config()
    ensure_profile(cfg)

    conn = connect(cfg.paths.db_path)
    hits: list[ProbeOutcome] = []
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[green]{task.fields[hits]}h [red]{task.fields[misses]}m [yellow]{task.fields[errors]}e"),
            transient=False,
        ) as progress:
            task_id = progress.add_task(
                "probing …", total=None, hits=0, misses=0, errors=0
            )
            hit_count = 0
            miss_count = 0
            err_count = 0

            def _on_progress(event: ProgressEvent) -> None:
                nonlocal hit_count, miss_count, err_count
                for o in event.outcomes:
                    if o.status == 200:
                        hit_count += 1
                    elif o.status == 404:
                        miss_count += 1
                    else:
                        err_count += 1
                label = f"[bold]{event.company}[/bold]" if len(event.company) <= 30 else event.company[:28] + "…"
                progress.update(
                    task_id,
                    description=f"probing {label}",
                    completed=event.probed,
                    total=event.total,
                    hits=hit_count,
                    misses=miss_count,
                    errors=err_count,
                )

            hits = asyncio.run(
                _run(cfg, conn, atses=atses, limit=limit, include_cached=include_cached, on_progress=_on_progress)
            )
            progress.update(task_id, description="done")
    finally:
        conn.close()

    if not hits:
        typer.echo("no new slugs discovered.")
        raise typer.Exit(code=0)

    _print_table(hits)

    if apply:
        added = _apply_to_config(cfg, hits)
        if added:
            parts = ", ".join(f"+{n} {ats}" for ats, n in added.items() if n)
            typer.echo(
                f"\nupdated {config_path()} ({parts}). backup: {config_path()}.bak"
            )
        else:
            typer.echo("\nno new slugs to add — config already contains all hits.")
    else:
        typer.echo("\n--apply to write these to config.toml")


async def _run(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    atses: list[str],
    limit: int,
    include_cached: bool,
    on_progress: "Callable[[ProgressEvent], None] | None" = None,
) -> list[ProbeOutcome]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": cfg.ingest.user_agent or DEFAULT_UA, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        return await discover(
            client,
            cfg,
            conn,
            atses=atses,
            limit=limit,
            include_cached=include_cached,
            on_progress=on_progress,
        )


def _print_table(hits: list[ProbeOutcome]) -> None:
    typer.echo(f"{len(hits)} slug(s) ready to apply (fresh + cached hits not yet in config):\n")
    company_w = max(7, max(len(h.company) for h in hits))
    ats_w = max(3, max(len(h.ats) for h in hits))
    slug_w = max(4, max(len(h.slug) for h in hits))
    header = f"{'company':<{company_w}}  {'ats':<{ats_w}}  {'slug':<{slug_w}}  {'jobs':>5}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for h in hits:
        typer.echo(
            f"{h.company:<{company_w}}  {h.ats:<{ats_w}}  {h.slug:<{slug_w}}  {h.job_count or 0:>5}"
        )


def _apply_to_config(cfg: Config, hits: list[ProbeOutcome]) -> dict[str, int] | None:
    # Per-ATS: existing slugs + new slugs to append. Pulled by attribute so
    # adding a new probe-supported ATS only requires touching _SUPPORTED_ATSES
    # (and the IngestConfig field, which already exists).
    additions: dict[str, list[str]] = {ats: [] for ats in _SUPPORTED_ATSES}
    existing: dict[str, set[str]] = {
        ats: set(getattr(cfg.ingest, ats)) for ats in _SUPPORTED_ATSES
    }

    for h in hits:
        if h.ats not in additions:
            continue
        if h.slug in existing[h.ats]:
            continue
        additions[h.ats].append(h.slug)
        existing[h.ats].add(h.slug)

    if not any(additions.values()):
        return None

    for ats, new in additions.items():
        if new:
            setattr(cfg.ingest, ats, [*getattr(cfg.ingest, ats), *new])

    write_config_atomically(cfg)

    return {ats: len(new) for ats, new in additions.items()}


__all__ = ["app"]

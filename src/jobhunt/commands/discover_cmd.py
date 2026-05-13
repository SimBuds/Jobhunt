"""`jobhunt discover` — find ATS slugs for companies seen in past scans."""

from __future__ import annotations

import asyncio
import os
import sqlite3

import httpx
import tomli_w
import typer

from jobhunt.config import Config, _to_toml_dict, config_path, load_config
from jobhunt.db import connect
from jobhunt.discover.probe import DiscoverReport, ProbeOutcome, discover_with_report
from jobhunt.errors import JobHuntError
from jobhunt.http import DEFAULT_UA

app = typer.Typer(
    help="Discover new ingestion targets from past scan results.",
    no_args_is_help=True,
)

_SUPPORTED_ATSES = ("greenhouse", "ashby")


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
    help="Probe Greenhouse/Ashby for companies seen in past scans, suggest slugs to add.",
)
def slugs(
    ats: str = typer.Option(
        "greenhouse,ashby",
        "--ats",
        help="Comma-separated ATSes to probe (greenhouse, ashby).",
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
    try:
        report = asyncio.run(
            _run(cfg, conn, atses=atses, limit=limit, include_cached=include_cached)
        )
    finally:
        conn.close()

    _print_summary(report, atses=atses, limit=limit, include_cached=include_cached)

    if not report.hits:
        _print_empty_result_hint(report, include_cached=include_cached)
        raise typer.Exit(code=0)

    _print_table(report)

    if apply:
        added = _apply_to_config(cfg, report.hits)
        if added:
            typer.echo(
                f"\nupdated {config_path()} (+{added['greenhouse']} greenhouse, "
                f"+{added['ashby']} ashby). backup: {config_path()}.bak"
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
) -> DiscoverReport:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": cfg.ingest.user_agent or DEFAULT_UA, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        return await discover_with_report(
            client,
            cfg,
            conn,
            atses=atses,
            limit=limit,
            include_cached=include_cached,
        )


def _print_summary(
    report: DiscoverReport,
    *,
    atses: list[str],
    limit: int,
    include_cached: bool,
) -> None:
    typer.echo(
        "discover: "
        f"checked {report.companies_seen} compan"
        f"{'y' if report.companies_seen == 1 else 'ies'} "
        f"(limit {limit}; ats={','.join(atses)})"
    )
    typer.echo(
        "discover: "
        f"probed {report.companies_probed}, "
        f"skipped {report.companies_skipped_configured} configured, "
        f"{report.companies_skipped_no_candidates} staffing/unparseable"
        + (
            ""
            if include_cached
            else f", {report.companies_skipped_cached} cached miss"
            f"{'' if report.companies_skipped_cached == 1 else 'es'}"
        )
    )
    typer.echo(
        "discover: "
        f"requests {report.probes_attempted} "
        f"({report.probe_hits} hit, {report.probe_misses} miss, {report.probe_errors} error)"
    )


def _print_empty_result_hint(report: DiscoverReport, *, include_cached: bool) -> None:
    typer.echo("discover: no unapplied slugs found.")
    if report.companies_skipped_cached and not include_cached:
        typer.echo(
            "discover: re-run with --include-cached to retry "
            f"{report.companies_skipped_cached} cached miss"
            f"{'' if report.companies_skipped_cached == 1 else 'es'}."
        )
    elif report.companies_probed == 0 and report.companies_skipped_configured:
        typer.echo("discover: candidate slugs are already present in config.toml.")


def _print_table(report: DiscoverReport) -> None:
    hits = report.hits
    prefix = (
        f"{len(hits)} slug(s) ready to apply"
        if report.cached_hits_reused == 0
        else (
            f"{len(hits)} slug(s) ready to apply "
            f"({report.cached_hits_reused} cached from earlier runs)"
        )
    )
    typer.echo(f"\n{prefix}:\n")
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
    path = config_path()
    if not path.exists():
        raise JobHuntError(f"config.toml not found at {path}")

    new_greenhouse: list[str] = []
    new_ashby: list[str] = []
    existing_g = set(cfg.ingest.greenhouse)
    existing_a = set(cfg.ingest.ashby)
    for h in hits:
        if h.ats == "greenhouse" and h.slug not in existing_g:
            new_greenhouse.append(h.slug)
            existing_g.add(h.slug)
        elif h.ats == "ashby" and h.slug not in existing_a:
            new_ashby.append(h.slug)
            existing_a.add(h.slug)

    if not new_greenhouse and not new_ashby:
        return None

    cfg.ingest.greenhouse = [*cfg.ingest.greenhouse, *new_greenhouse]
    cfg.ingest.ashby = [*cfg.ingest.ashby, *new_ashby]

    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_bytes(path.read_bytes())

    serialized = tomli_w.dumps(_to_toml_dict(cfg.model_dump(mode="json")))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, path)

    return {"greenhouse": len(new_greenhouse), "ashby": len(new_ashby)}


__all__ = ["app"]

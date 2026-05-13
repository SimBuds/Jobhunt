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
from jobhunt.discover.probe import ProbeOutcome, discover
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
        hits = asyncio.run(_run(cfg, conn, atses=atses, limit=limit, include_cached=include_cached))
    finally:
        conn.close()

    if not hits:
        typer.echo("no new slugs discovered.")
        raise typer.Exit(code=0)

    _print_table(hits)

    if apply:
        added = _apply_to_config(cfg, hits)
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

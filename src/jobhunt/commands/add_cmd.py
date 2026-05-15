"""`jobhunt add <url>` — primary slug-acquisition surface.

Parses an employer's ATS URL (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday), confirms the slug via a single live probe where applicable, and
appends it to ~/.config/jobhunt/config.toml. Replaces hand-editing config
as the daily-driver workflow for expanding ingestion coverage."""

from __future__ import annotations

import asyncio

import httpx
import typer

from jobhunt.commands._config_write import write_config_atomically
from jobhunt.config import Config, load_config
from jobhunt.discover.probe import ProbeOutcome, _probe_one
from jobhunt.discover.url_extract import ExtractedSlug, extract
from jobhunt.http import DEFAULT_UA, RateLimiter

app = typer.Typer(
    help="Add an employer to config.toml by parsing its ATS URL.",
    no_args_is_help=True,
)


# ATSes that have an ingest adapter AND a write target in config.toml.
_INGESTABLE_ATSES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday")
# ATSes the URL extractor recognizes but we can't ingest yet.
_RECOGNIZED_BUT_UNSUPPORTED = ("icims",)


def run(
    url: str = typer.Argument(..., help="An employer ATS URL (career page or job posting)."),
    skip_probe: bool = typer.Option(
        False, "--skip-probe", help="Skip the live confirmation probe."
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    extracted = extract(url)
    if extracted is None:
        typer.echo(
            "error: didn't recognize this URL as a supported ATS.\n"
            "  supported hosts: boards.greenhouse.io, jobs.lever.co, "
            "jobs.ashbyhq.com, jobs.smartrecruiters.com, "
            "*.wd*.myworkdayjobs.com",
            err=True,
        )
        raise typer.Exit(code=1)

    if extracted.ats in _RECOGNIZED_BUT_UNSUPPORTED:
        typer.echo(
            f"error: {extracted.ats} support coming soon — not yet ingestable.\n"
            f"  recognized tenant: {extracted.slug}",
            err=True,
        )
        raise typer.Exit(code=2)

    if extracted.ats not in _INGESTABLE_ATSES:
        # Defensive — extractor + ingestable list should stay in sync.
        typer.echo(f"error: no ingest adapter for ats={extracted.ats}", err=True)
        raise typer.Exit(code=2)

    config_value = _build_config_value(extracted)
    existing = list(getattr(cfg.ingest, extracted.ats))

    if config_value in existing:
        typer.echo(f"already configured: [ingest.{extracted.ats}] {config_value!r}")
        raise typer.Exit(code=0)

    if not skip_probe and extracted.ats != "workday":
        # Workday probing requires the CXS handshake; skip for now.
        outcome = asyncio.run(_probe(cfg, extracted))
        if outcome.status == 200:
            typer.echo(
                f"probe ok: {extracted.ats}/{extracted.slug} "
                f"({outcome.job_count} active postings)"
            )
        elif outcome.status == 404:
            typer.echo(
                "warning: probe returned 404 (empty or stale board). adding anyway.",
                err=True,
            )
        else:
            typer.echo(
                "warning: probe failed (network or other error). adding anyway.",
                err=True,
            )

    setattr(cfg.ingest, extracted.ats, [*existing, config_value])
    write_config_atomically(cfg)
    typer.echo(f"added: [ingest.{extracted.ats}] {config_value!r}")
    typer.echo("note: any inline comments in config.toml were not preserved on write.")


def _build_config_value(extracted: ExtractedSlug) -> str:
    if extracted.ats == "workday":
        if not extracted.host or not extracted.site:
            raise typer.BadParameter(
                "Workday URL must include both wd-host and site segments, "
                "e.g. https://rbc.wd3.myworkdayjobs.com/en-US/RBC_Careers"
            )
        return f"{extracted.slug}:{extracted.host}:{extracted.site}"
    return extracted.slug


async def _probe(cfg: Config, extracted: ExtractedSlug) -> ProbeOutcome:
    limiter = RateLimiter(rate_per_sec=cfg.ingest.rate_limit_per_sec)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={
            "User-Agent": cfg.ingest.user_agent or DEFAULT_UA,
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:
        return await _probe_one(
            client, limiter, extracted.slug, extracted.ats, extracted.slug
        )


__all__ = ["app", "run"]

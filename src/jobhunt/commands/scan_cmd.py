"""`job-seeker scan` — pull GTA jobs from configured sources and score the unscored."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections.abc import AsyncIterator

import httpx
import typer

from jobhunt.config import Config, load_config
from jobhunt.db import (
    connect,
    jobs_to_score,
    migrate,
    set_decline_reason,
    upsert_job,
    write_score,
)
from jobhunt.errors import IngestError, JobHuntError
from jobhunt.http import RateLimiter
from jobhunt.ingest import (
    adzuna_ca,
    ashby,
    greenhouse,
    job_bank_ca,
    lever,
    rss_generic,
    smartrecruiters,
)
from jobhunt.models import Job
from jobhunt.pipeline.score import prompt_hash, score_job
from jobhunt.secrets import load_secrets

app = typer.Typer(help="Ingest GTA-scoped jobs and score them.", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def run(
    skip_score: bool = typer.Option(False, "--skip-score", help="Ingest only; don't score."),
    skip_ingest: bool = typer.Option(False, "--skip-ingest", help="Score backlog only."),
    limit: int | None = typer.Option(None, "--limit", help="Cap how many jobs to score."),
) -> None:
    cfg = load_config()
    asyncio.run(_run(cfg, skip_score=skip_score, skip_ingest=skip_ingest, limit=limit))


async def _run(cfg: Config, *, skip_score: bool, skip_ingest: bool, limit: int | None) -> None:
    conn = connect(cfg.paths.db_path)
    try:
        migrate(conn, cfg.paths.migrations_dir)

        if not skip_ingest:
            inserted = await _ingest_all(cfg, conn)
            typer.echo(f"ingest: {inserted} new job(s) inserted")
        else:
            typer.echo("ingest: skipped")

        if skip_score:
            return

        ph = prompt_hash(cfg.paths.kb_dir)
        rows = jobs_to_score(conn, current_hash=ph, limit=limit)
        if not rows:
            typer.echo("score: nothing to score")
            return
        new_n = sum(1 for r in rows if r["prev_hash"] is None)
        stale_n = len(rows) - new_n
        typer.echo(
            f"score: {len(rows)} job(s) to score "
            f"({new_n} new, {stale_n} stale — profile/prompt/policy changed) "
            "(this can take a while on Ollama)"
        )
        ok = 0
        for row in rows:
            job = Job(
                id=row["id"],
                source=row["source"],
                external_id=row["external_id"],
                company=row["company"],
                title=row["title"],
                location=row["location"],
                description=row["description"],
                url=row["url"],
            )
            try:
                result = await score_job(cfg, job)
            except JobHuntError as e:
                typer.echo(f"  ! {job.id}: {e}", err=True)
                continue
            with conn:
                write_score(
                    conn,
                    job_id=job.id,
                    score=result.score,
                    reasons=result.matched_must_haves,
                    red_flags=[result.decline_reason] if result.decline_reason else [],
                    must_clarify=result.gaps,
                    model=result.model,
                    prompt_hash=ph,
                )
                set_decline_reason(conn, job.id, result.decline_reason)
            ok += 1
            tag = (
                f"DECLINE: {result.decline_reason}"
                if result.decline_reason
                else f"score={result.score}"
            )
            typer.echo(f"  + {job.id} [{tag}] {job.title or ''}")
        typer.echo(f"score: {ok}/{len(rows)} scored")
    finally:
        conn.close()


async def _ingest_all(cfg: Config, conn: sqlite3.Connection) -> int:
    """Run all configured ingest adapters concurrently. Returns count inserted."""
    secrets = load_secrets()
    limiter = RateLimiter(cfg.ingest.rate_limit_per_sec)
    headers = {"User-Agent": cfg.ingest.user_agent, "Accept": "application/json"}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0), headers=headers, follow_redirects=True
    ) as client:
        streams: list[AsyncIterator[Job]] = []
        for slug in cfg.ingest.greenhouse:
            streams.append(
                _safe_stream("greenhouse", slug, greenhouse.fetch(client, limiter, slug))
            )
        for slug in cfg.ingest.lever:
            streams.append(_safe_stream("lever", slug, lever.fetch(client, limiter, slug)))
        for slug in cfg.ingest.ashby:
            streams.append(_safe_stream("ashby", slug, ashby.fetch(client, limiter, slug)))
        for slug in cfg.ingest.smartrecruiters:
            streams.append(
                _safe_stream(
                    "smartrecruiters", slug, smartrecruiters.fetch(client, limiter, slug)
                )
            )
        for url in cfg.ingest.job_bank_ca:
            streams.append(
                _safe_stream("job_bank_ca", url, job_bank_ca.fetch(client, limiter, url))
            )
        for url in cfg.ingest.rss:
            streams.append(_safe_stream("rss", url, rss_generic.fetch(client, limiter, url)))
        if secrets.adzuna_app_id and secrets.adzuna_app_key:
            for query in cfg.ingest.adzuna.queries:
                streams.append(
                    _safe_stream(
                        "adzuna_ca",
                        query,
                        adzuna_ca.fetch(
                            client,
                            limiter,
                            app_id=secrets.adzuna_app_id,
                            app_key=secrets.adzuna_app_key,
                            query=query,
                            pages=cfg.ingest.adzuna.pages,
                            results_per_page=cfg.ingest.adzuna.results_per_page,
                        ),
                    )
                )
        elif cfg.ingest.adzuna.queries:
            print(
                "  ! adzuna: skipped — set adzuna_app_id/adzuna_app_key in secrets.toml",
                file=sys.stderr,
            )

        if not streams:
            typer.echo(
                "ingest: no sources configured. Edit ~/.config/jobhunt/config.toml — "
                "set ingest.greenhouse/lever/ashby slugs.",
                err=True,
            )
            return 0

        inserted = 0
        seen_dedup: set[str] = set()
        for stream in streams:
            async for job in stream:
                dedup_key = _dedup_key(job)
                if dedup_key in seen_dedup:
                    continue
                seen_dedup.add(dedup_key)
                with conn:
                    if upsert_job(conn, job):
                        inserted += 1
        return inserted


_DEDUP_RE = __import__("re").compile(r"[^a-z0-9]+")


def _dedup_key(job: Job) -> str:
    """Stable cross-source dedupe key. Same role at the same company from two
    different sources (e.g. Greenhouse + Adzuna) hashes to the same key so we
    don't score the same posting twice. Uses already-stored external_id when the
    source is Greenhouse/Lever/Ashby/SmartRecruiters (unique per company posting),
    falls back to normalised (title, company) for aggregators like Adzuna/RSS."""
    if job.source in {"greenhouse", "lever", "ashby", "smartrecruiters"}:
        return job.id  # already source-specific unique
    title_norm = _DEDUP_RE.sub("", (job.title or "").lower())
    company_norm = _DEDUP_RE.sub("", (job.company or "").lower())
    return f"{title_norm}:{company_norm}"


async def _safe_stream(source: str, label: str, stream: AsyncIterator[Job]) -> AsyncIterator[Job]:
    """Wrap an adapter so a failure on one source doesn't kill the whole scan."""
    try:
        async for job in stream:
            yield job
    except IngestError as e:
        typer.echo(f"  ! {source}/{label}: {e}", err=True)

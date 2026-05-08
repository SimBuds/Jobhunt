"""`job-seeker scan` — pull GTA jobs from configured sources and score the unscored."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections.abc import AsyncIterator

import httpx
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from jobhunt.config import Config, load_config
from jobhunt.db import (
    connect,
    jobs_to_score,
    migrate,
    set_decline_reason,
    upsert_job,
    write_score,
)
from jobhunt.errors import GatewayError, IngestError, JobHuntError
from jobhunt.gateway import complete_json
from jobhunt.http import RateLimiter
from jobhunt.ingest import (
    adzuna_ca,
    ashby,
    greenhouse,
    job_bank_ca,
    lever,
    rss_generic,
    smartrecruiters,
    workday,
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
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)
    asyncio.run(_run(cfg, skip_score=skip_score, skip_ingest=skip_ingest, limit=limit))


async def _run(cfg: Config, *, skip_score: bool, skip_ingest: bool, limit: int | None) -> None:
    conn = connect(cfg.paths.db_path)
    try:
        migrate(conn, cfg.paths.migrations_dir)

        if not skip_ingest:
            inserted, per_source = await _ingest_all(cfg, conn)
            _print_ingest_summary(per_source)
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
        await _warm_model(cfg)
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


async def _warm_model(cfg: Config) -> None:
    """Pre-warm the score model so the first real call doesn't pay the cold-load
    cost on top of the 180 s gateway timeout. A trivial chat with the configured
    keep_alive leaves the model resident for subsequent scoring calls."""
    model = cfg.gateway.tasks.get("score", "")
    if not model:
        return
    typer.echo(f"score: warming {model}...")
    try:
        await complete_json(
            base_url=cfg.gateway.base_url,
            model=model,
            system="Return JSON.",
            user="ok",
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        )
    except GatewayError as e:
        typer.echo(f"  ! warm-up failed (continuing): {e}", err=True)


async def _ingest_all(
    cfg: Config, conn: sqlite3.Connection
) -> tuple[int, list[tuple[str, str, int, str | None]]]:
    """Run all configured ingest adapters concurrently.

    Returns (inserted, per_source) where per_source is a list of
    (source, label, count, error) tuples — error is None on success.
    """
    secrets = load_secrets()
    limiter = RateLimiter(cfg.ingest.rate_limit_per_sec)
    headers = {"User-Agent": cfg.ingest.user_agent, "Accept": "application/json"}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0), headers=headers, follow_redirects=True
    ) as client:
        # Each adapter source is registered with a (source, label, fetch_iter)
        # triple so the progress bar can show one line per adapter.
        adapters: list[tuple[str, str, AsyncIterator[Job]]] = []
        for slug in cfg.ingest.greenhouse:
            adapters.append(("greenhouse", slug, greenhouse.fetch(client, limiter, slug)))
        for slug in cfg.ingest.lever:
            adapters.append(("lever", slug, lever.fetch(client, limiter, slug)))
        for slug in cfg.ingest.ashby:
            adapters.append(("ashby", slug, ashby.fetch(client, limiter, slug)))
        for slug in cfg.ingest.smartrecruiters:
            adapters.append(
                ("smartrecruiters", slug, smartrecruiters.fetch(client, limiter, slug))
            )
        for spec in cfg.ingest.workday:
            adapters.append(("workday", spec, workday.fetch(client, limiter, spec)))
        for url in cfg.ingest.job_bank_ca:
            adapters.append(("job_bank_ca", url, job_bank_ca.fetch(client, limiter, url)))
        for url in cfg.ingest.rss:
            adapters.append(("rss", url, rss_generic.fetch(client, limiter, url)))
        if secrets.adzuna_app_id and secrets.adzuna_app_key:
            for query in cfg.ingest.adzuna.queries:
                adapters.append(
                    (
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

        if not adapters:
            typer.echo(
                "ingest: no sources configured. Edit ~/.config/jobhunt/config.toml — "
                "set ingest.greenhouse/lever/ashby slugs.",
                err=True,
            )
            return 0, []

        # Drain all streams concurrently — adapters share the per-host
        # RateLimiter so politeness is preserved while distinct hosts overlap.
        queue: asyncio.Queue[Job | None] = asyncio.Queue()
        console = Console(stderr=True)
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )

        # source/label → (count, error_message_or_None). Filled by _safe_stream.
        results: dict[tuple[str, str], tuple[int, str | None]] = {}

        with progress:
            overall = progress.add_task("ingest", total=len(adapters))
            task_ids: list[TaskID] = []
            streams: list[AsyncIterator[Job]] = []
            for source, label, fetch in adapters:
                tid = progress.add_task(f"  {source}/{label}", total=None, start=True)
                task_ids.append(tid)
                streams.append(
                    _safe_stream(source, label, fetch, progress, tid, overall, results)
                )

            async def drain(stream: AsyncIterator[Job]) -> None:
                async for job in stream:
                    await queue.put(job)

            producers = [asyncio.create_task(drain(s)) for s in streams]

            async def closer() -> None:
                await asyncio.gather(*producers, return_exceptions=False)
                await queue.put(None)

            closer_task = asyncio.create_task(closer())

            inserted = 0
            seen_dedup: set[str] = set()
            while True:
                item = await queue.get()
                if item is None:
                    break
                dedup_key = _dedup_key(item)
                if dedup_key in seen_dedup:
                    continue
                seen_dedup.add(dedup_key)
                with conn:
                    if upsert_job(conn, item):
                        inserted += 1
            await closer_task
            per_source = [
                (source, label, results.get((source, label), (0, None))[0],
                 results.get((source, label), (0, None))[1])
                for (source, label, _) in adapters
            ]
            return inserted, per_source


_DEDUP_RE = __import__("re").compile(r"[^a-z0-9]+")


def _dedup_key(job: Job) -> str:
    """Stable cross-source dedupe key. Same role at the same company from two
    different sources (e.g. Greenhouse + Adzuna) hashes to the same key so we
    don't score the same posting twice. Uses already-stored external_id when the
    source is Greenhouse/Lever/Ashby/SmartRecruiters (unique per company posting),
    falls back to normalised (title, company) for aggregators like Adzuna/RSS."""
    if job.source in {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}:
        return job.id  # already source-specific unique
    title_norm = _DEDUP_RE.sub("", (job.title or "").lower())
    company_norm = _DEDUP_RE.sub("", (job.company or "").lower())
    return f"{title_norm}:{company_norm}"


async def _safe_stream(
    source: str,
    label: str,
    stream: AsyncIterator[Job],
    progress: Progress,
    task_id: TaskID,
    overall_id: TaskID,
    results: dict[tuple[str, str], tuple[int, str | None]],
) -> AsyncIterator[Job]:
    """Wrap an adapter so a failure on one source doesn't kill the whole scan,
    while updating the rich progress display with live job counts."""
    n = 0
    try:
        async for job in stream:
            n += 1
            progress.update(task_id, description=f"  {source}/{label} — {n}")
            yield job
    except IngestError as e:
        progress.update(
            task_id,
            description=f"  {source}/{label} — error: {e}",
            completed=1,
            total=1,
        )
        progress.advance(overall_id)
        results[(source, label)] = (n, str(e))
        return
    progress.update(
        task_id,
        description=f"  {source}/{label} — {n} job(s)",
        completed=1,
        total=1,
    )
    progress.advance(overall_id)
    results[(source, label)] = (n, None)


def _print_ingest_summary(per_source: list[tuple[str, str, int, str | None]]) -> None:
    """Print a one-line per-source summary after the progress bar exits.

    Aggregates (source, count, errors) so multi-slug sources (e.g. 12 greenhouse
    slugs) don't dump 12 lines. Failed sources are listed individually so the
    user knows which slug to investigate.
    """
    if not per_source:
        return
    by_source: dict[str, dict[str, int | list[str]]] = {}
    for source, label, n, err in per_source:
        agg = by_source.setdefault(source, {"jobs": 0, "ok": 0, "errors": []})
        if err is None:
            agg["jobs"] = int(agg["jobs"]) + n  # type: ignore[arg-type]
            agg["ok"] = int(agg["ok"]) + 1  # type: ignore[arg-type]
        else:
            errs = agg["errors"]
            assert isinstance(errs, list)
            errs.append(f"{label}: {err}")

    typer.echo("ingest summary:")
    for source in sorted(by_source):
        agg = by_source[source]
        jobs = agg["jobs"]
        ok = agg["ok"]
        errors = agg["errors"]
        assert isinstance(errors, list)
        bits = [f"{jobs} job(s) from {ok} slug(s)"] if ok else []
        if errors:
            bits.append(f"{len(errors)} failed")
        typer.echo(f"  {source}: {', '.join(bits) or 'no slugs configured'}")
        for line in errors:
            typer.echo(f"    ! {line}")

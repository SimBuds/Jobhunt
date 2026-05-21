"""`jobhunt scan` — pull GTA jobs from configured sources and score the unscored."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path

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
from jobhunt.errors import IngestError, JobHuntError
from jobhunt.gateway.warm import warm_model
from jobhunt.http import RateLimiter
from jobhunt.ingest import (
    adzuna_ca,
    ashby,
    greenhouse,
    job_bank_ca,
    lever,
    recruitee,
    rss_generic,
    smartrecruiters,
    workable,
    workday,
)
from jobhunt.ingest._filter import is_management_title, is_within_age_window
from jobhunt.models import Job
from jobhunt.pipeline.score import prompt_hash, score_job
from jobhunt.secrets import load_secrets

app = typer.Typer(help="Ingest GTA-scoped jobs and score them.", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def run(
    skip_score: bool = typer.Option(False, "--skip-score", help="Ingest only; don't score."),
    skip_ingest: bool = typer.Option(False, "--skip-ingest", help="Score backlog only."),
    limit: int | None = typer.Option(None, "--limit", help="Cap how many jobs to score."),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help=(
            "Wipe HTTP cache and drop ingested jobs + scores before scanning. "
            "Preserves applications history, kb/profile, and the browser profile. "
            "Jobs with an existing application row are kept so history stays intact."
        ),
    ),
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        help=(
            "Drop postings older than N days at ingest. 0 disables. "
            "Default: cfg.ingest.max_age_days (7). Adapters that can't "
            "infer a posted-at timestamp are treated as fresh."
        ),
    ),
    no_discover: bool = typer.Option(
        False,
        "--no-discover",
        help=(
            "Skip the post-ingest slug auto-discovery step. By default, after "
            "ingest, scan probes public ATS APIs for slugs of newly-seen "
            "aggregator companies and appends hits to config.toml so the "
            "next scan pulls deep JDs natively. Use this flag, or set "
            "[ingest] auto_discover=false in config.toml, to opt out."
        ),
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)
    effective_max_age = (
        max_age_days if max_age_days is not None else cfg.ingest.max_age_days
    )
    asyncio.run(
        _run(
            cfg,
            skip_score=skip_score,
            skip_ingest=skip_ingest,
            limit=limit,
            refresh=refresh,
            max_age_days=effective_max_age,
            no_discover=no_discover,
        )
    )


async def _run(
    cfg: Config,
    *,
    skip_score: bool,
    skip_ingest: bool,
    limit: int | None,
    refresh: bool = False,
    max_age_days: int = 7,
    no_discover: bool = False,
) -> None:
    conn = connect(cfg.paths.db_path)
    try:
        migrate(conn, cfg.paths.migrations_dir)

        if refresh:
            _refresh_scan_state(cfg, conn)

        if not skip_ingest:
            inserted, per_source, filtered = await _ingest_all(
                cfg, conn, max_age_days=max_age_days
            )
            _print_ingest_summary(per_source)
            typer.echo(f"ingest: {inserted} new job(s) inserted")
            if filtered["mgmt"] or filtered["stale"]:
                typer.echo(
                    f"ingest: filtered {filtered['mgmt']} management-title + "
                    f"{filtered['stale']} stale (>{max_age_days}d) job(s)"
                )

            if cfg.ingest.auto_discover and not no_discover and inserted:
                await _auto_discover(cfg, conn)
        else:
            typer.echo("ingest: skipped")

        if skip_score:
            return

        ph = prompt_hash(cfg.paths.kb_dir)
        rows = jobs_to_score(
            conn, current_hash=ph, limit=limit, max_age_days=max_age_days
        )
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
        await warm_model(cfg, task="score")
        ok = 0
        total = len(rows)
        for i, row in enumerate(rows, start=1):
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
            # Pre-print so the user sees activity during the LLM call.
            # Without this, a slow Ollama response (KV realloc, etc.) looks
            # like the loop has frozen.
            typer.echo(f"  [{i}/{total}] scoring {job.id}…")
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


_AUTO_DISCOVER_ATSES = (
    "greenhouse", "ashby", "lever", "smartrecruiters", "workable", "recruitee",
)


async def _auto_discover(cfg: Config, conn: sqlite3.Connection) -> None:
    """Probe public ATS APIs for slugs of companies that landed in the jobs
    table via the latest ingest and aren't yet wired up. Hits are appended to
    config.toml so the next scan ingests those slugs natively for full JDs.

    Bounded by `discover()`'s 100-company candidate cap and the per-host
    rate limiter. Misses go into `slug_probes` with a 90-day TTL so they
    re-probe automatically without `--include-cached`.

    .bak snapshot is written by `write_config_atomically`. Inline comments in
    config.toml are dropped — same caveat as `jobhunt add` and
    `config seed --apply` (see AGENTS.md §Commands).
    """
    from jobhunt.commands._config_write import write_config_atomically
    from jobhunt.discover.probe import discover
    from jobhunt.http import DEFAULT_UA

    typer.echo("discover: probing public ATS APIs for new slugs…")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={
            "User-Agent": cfg.ingest.user_agent or DEFAULT_UA,
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:
        try:
            hits = await discover(
                client,
                cfg,
                conn,
                atses=list(_AUTO_DISCOVER_ATSES),
                limit=100,
                include_cached=False,
            )
        except Exception as e:  # noqa: BLE001 — never let discovery fail a scan
            typer.echo(f"discover: skipped — {e}", err=True)
            return

    if not hits:
        typer.echo("discover: no new slugs.")
        return

    additions: dict[str, list[str]] = {ats: [] for ats in _AUTO_DISCOVER_ATSES}
    existing: dict[str, set[str]] = {
        ats: set(getattr(cfg.ingest, ats)) for ats in _AUTO_DISCOVER_ATSES
    }
    for h in hits:
        if h.ats not in additions or h.slug in existing[h.ats]:
            continue
        additions[h.ats].append(h.slug)
        existing[h.ats].add(h.slug)

    if not any(additions.values()):
        typer.echo("discover: no new slugs (all hits already in config).")
        return

    for ats, new in additions.items():
        if new:
            setattr(cfg.ingest, ats, [*getattr(cfg.ingest, ats), *new])

    write_config_atomically(cfg)
    parts = ", ".join(f"+{len(v)} {k}" for k, v in additions.items() if v)
    typer.echo(
        f"discover: appended slugs to config.toml ({parts}). "
        "Next scan will ingest them natively. "
        "(Inline comments in config.toml were dropped; .bak snapshot saved.)"
    )


def _refresh_scan_state(cfg: Config, conn: sqlite3.Connection) -> None:
    """Wipe HTTP cache and drop scored/ingested jobs; preserve real apply history.

    Drafted applications are treated as ephemeral — their DB rows and on-disk
    artifact dirs (`data/applications/<safe_id>/`) are removed so the underlying
    jobs can be dropped and re-evaluated on the next scan. Submitted statuses
    (applied/interviewing/offer/rejected/withdrawn) are preserved along with
    their jobs. Scores cascade-delete from jobs via their own FK.
    """
    from jobhunt.commands.apply_cmd import _safe_id

    cache_dir = Path(cfg.paths.data_dir) / "cache"
    cache_removed = False
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_removed = True

    apps_dir = Path(cfg.paths.data_dir) / "applications"
    with conn:
        drafted_job_ids = [
            r[0] for r in conn.execute(
                "SELECT job_id FROM applications WHERE status = 'drafted'"
            )
        ]
        conn.execute("DELETE FROM applications WHERE status = 'drafted'")
        cur = conn.execute(
            "DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM applications)"
        )
        dropped_jobs = cur.rowcount
        kept = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]

    dropped_dirs = 0
    for jid in drafted_job_ids:
        d = apps_dir / _safe_id(jid)
        if d.exists():
            shutil.rmtree(d)
            dropped_dirs += 1

    bits = []
    if cache_removed:
        bits.append("HTTP cache wiped")
    bits.append(f"{dropped_jobs} job(s) + scores dropped")
    if drafted_job_ids:
        bits.append(
            f"{len(drafted_job_ids)} drafted application(s) discarded "
            f"({dropped_dirs} dir(s) removed)"
        )
    if kept:
        bits.append(f"{kept} submitted application(s) kept")
    typer.echo("refresh: " + "; ".join(bits))


async def _ingest_all(
    cfg: Config, conn: sqlite3.Connection, *, max_age_days: int = 7
) -> tuple[int, list[tuple[str, str, int, str | None]], dict[str, int]]:
    """Run all configured ingest adapters concurrently.

    Returns (inserted, per_source, filtered) where:
      - per_source is a list of (source, label, count, error) tuples
        (error is None on success);
      - filtered is `{"mgmt": N, "stale": N}` counting jobs dropped at the
        drain chokepoint for management-title or freshness reasons.
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
        for slug in cfg.ingest.workable:
            adapters.append(("workable", slug, workable.fetch(client, limiter, slug)))
        for slug in cfg.ingest.recruitee:
            adapters.append(("recruitee", slug, recruitee.fetch(client, limiter, slug)))
        for url in cfg.ingest.job_bank_ca:
            adapters.append(("job_bank_ca", url, job_bank_ca.fetch(client, limiter, url)))
        for url in cfg.ingest.rss:
            adapters.append(("rss", url, rss_generic.fetch(client, limiter, url)))
        adzuna_queries = cfg.ingest.adzuna.queries
        if not adzuna_queries:
            import json as _json

            from jobhunt.ingest._query_planner import derive_adzuna_queries
            verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
            if verified_path.is_file():
                verified = _json.loads(verified_path.read_text(encoding="utf-8"))
                adzuna_queries = derive_adzuna_queries(verified)
                if adzuna_queries:
                    typer.echo(
                        f"adzuna: auto-derived queries from profile "
                        f"({len(adzuna_queries)}): {', '.join(adzuna_queries)}"
                    )
        if secrets.adzuna_app_id and secrets.adzuna_app_key:
            for query in adzuna_queries:
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
        elif adzuna_queries:
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
            return 0, [], {"mgmt": 0, "stale": 0}

        non_adzuna = [a for a in adapters if a[0] != "adzuna_ca"]
        if not non_adzuna:
            typer.echo(
                "ingest: only adzuna_ca is configured — your scan will be biased toward "
                "one source. Add greenhouse/lever/ashby/smartrecruiters slugs to "
                "~/.config/jobhunt/config.toml under [ingest]. See README §Configure "
                "ingest sources for how to find slugs.",
                err=True,
            )

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
            filtered = {"mgmt": 0, "stale": 0}
            seen_dedup: set[str] = set()
            while True:
                item = await queue.get()
                if item is None:
                    break
                # Pre-score filters at the drain chokepoint: management
                # title (drops Manager/Director/Head of/VP/etc — Senior /
                # Lead / Staff / Principal pass through as IC) + freshness
                # window. Both filters are pure and adapter-agnostic.
                if is_management_title(item.title):
                    filtered["mgmt"] += 1
                    continue
                if not is_within_age_window(item.posted_at, max_age_days):
                    filtered["stale"] += 1
                    continue
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
            return inserted, per_source, filtered


_DEDUP_RE = __import__("re").compile(r"[^a-z0-9]+")


def _dedup_key(job: Job) -> str:
    """Stable cross-source dedupe key. Same role at the same company from two
    different sources (e.g. Greenhouse + Adzuna) hashes to the same key so we
    don't score the same posting twice. Uses already-stored external_id when the
    source is Greenhouse/Lever/Ashby/SmartRecruiters (unique per company posting),
    falls back to normalised (title, company) for aggregators like Adzuna/RSS."""
    if job.source in {
        "greenhouse", "lever", "ashby", "smartrecruiters", "workday",
        "workable", "recruitee",
    }:
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

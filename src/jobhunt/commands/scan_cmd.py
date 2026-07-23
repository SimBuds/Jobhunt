"""`jobhunt scan` — pull GTA jobs from configured sources and score the unscored."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

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
from jobhunt.ingest._filter import (
    is_management_title,
    is_non_engineering_title,
    is_research_title,
    is_senior_title,
    is_within_age_window,
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
            if any(filtered.values()):
                parts = [
                    f"{filtered['mgmt']} management-title",
                    f"{filtered['stale']} stale (>{max_age_days}d)",
                ]
                if filtered.get("research"):
                    parts.append(f"{filtered['research']} research/ML-title")
                if filtered.get("non_eng"):
                    parts.append(f"{filtered['non_eng']} non-engineering-title")
                if filtered.get("senior"):
                    parts.append(f"{filtered['senior']} senior-title (YoE)")
                if filtered.get("dup"):
                    parts.append(f"{filtered['dup']} duplicate")
                typer.echo(f"ingest: filtered {' + '.join(parts)} job(s)")

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
            typer.echo(f"  [{i}/{total}] {job.id} [{tag}] {job.title or ''}")
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
    """Wipe HTTP cache and drop scored/ingested jobs; preserve apply history.

    Applications of EVERY status pin their job row — including 'drafted'.
    Drafted rows are durable re-apply targets (`apply --top/--best` and the
    `list` default view select them), so a refresh must not silently discard
    a draft or its on-disk artifacts under `data/applications/<safe_id>/`.
    Unpinned jobs are dropped for re-evaluation on the next scan; their
    scores cascade-delete via the jobs FK. Kept jobs keep their scores and
    are re-scored when the score prompt_hash goes stale.
    """
    cache_dir = Path(cfg.paths.data_dir) / "cache"
    cache_removed = False
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_removed = True

    with conn:
        drafted = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = 'drafted'"
        ).fetchone()[0]
        cur = conn.execute(
            "DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM applications)"
        )
        dropped_jobs = cur.rowcount
        submitted = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status != 'drafted'"
        ).fetchone()[0]

    bits = []
    if cache_removed:
        bits.append("HTTP cache wiped")
    bits.append(f"{dropped_jobs} job(s) + scores dropped")
    if drafted:
        bits.append(f"{drafted} drafted application(s) kept (still apply targets)")
    if submitted:
        bits.append(f"{submitted} submitted application(s) kept")
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
    # Job Bank's robots.txt requests Crawl-delay: 5. Its HTML-scrape adapter gets
    # a dedicated 5 s-spaced limiter (separate host-keyed state from the shared
    # 1 req/s limiter, so it doesn't slow the API adapters).
    jobbank_limiter = RateLimiter(0.2)
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
                (
                    "smartrecruiters",
                    slug,
                    smartrecruiters.fetch(
                        client,
                        limiter,
                        slug,
                        drop_non_eng=cfg.ingest.drop_non_engineering_titles,
                    ),
                )
            )
        for spec in cfg.ingest.workday:
            adapters.append(("workday", spec, workday.fetch(client, limiter, spec)))
        for slug in cfg.ingest.workable:
            adapters.append(("workable", slug, workable.fetch(client, limiter, slug)))
        for slug in cfg.ingest.recruitee:
            adapters.append(("recruitee", slug, recruitee.fetch(client, limiter, slug)))
        for url in cfg.ingest.job_bank_ca:
            adapters.append(
                ("job_bank_ca", url, job_bank_ca.fetch(client, jobbank_limiter, url))
            )
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
            # One progress row per source (not per slug) — collapses dozens of
            # rows into ~5. Per-source state tracks slugs done/total + job count
            # so the row description shows live aggregate progress.
            source_totals: dict[str, int] = {}
            for source, _label, _fetch in adapters:
                source_totals[source] = source_totals.get(source, 0) + 1
            source_state: dict[str, dict[str, int | TaskID]] = {}
            for source, total in source_totals.items():
                tid = progress.add_task(
                    f"  {source} — 0/{total} slugs, 0 jobs",
                    total=total,
                    start=True,
                )
                source_state[source] = {"done": 0, "jobs": 0, "errors": 0,
                                        "total": total, "tid": tid}
            streams: list[AsyncIterator[Job]] = []
            for source, label, fetch in adapters:
                streams.append(
                    _safe_stream(source, label, fetch, progress, source_state,
                                 overall, results)
                )

            async def drain(stream: AsyncIterator[Job]) -> None:
                async for job in stream:
                    await queue.put(job)

            producers = [asyncio.create_task(drain(s)) for s in streams]

            async def closer() -> None:
                # return_exceptions=True guarantees we reach put(None). If a
                # producer died and this gather re-raised, the sentinel would
                # never be enqueued and the drain loop below would block on
                # queue.get() forever (the classic ingest hang). _safe_stream
                # already contains adapter failures, so a surviving exception
                # here is a bug in our own plumbing — surface it, don't swallow
                # it silently, but never let it deadlock the consumer.
                outcomes = await asyncio.gather(*producers, return_exceptions=True)
                for out in outcomes:
                    if isinstance(out, BaseException) and not isinstance(
                        out, asyncio.CancelledError
                    ):
                        print(f"  ! ingest: producer crashed: {out!r}", file=sys.stderr)
                await queue.put(None)

            closer_task = asyncio.create_task(closer())

            inserted = 0
            filtered = {
                "mgmt": 0, "stale": 0, "research": 0, "senior": 0,
                "non_eng": 0, "dup": 0,
            }
            drop_research = cfg.ingest.drop_research_titles
            drop_non_eng = cfg.ingest.drop_non_engineering_titles
            drop_senior = not cfg.applicant.include_senior_roles
            seen_dedup: set[str] = set()
            # shadow key -> job id, for aggregator rows INSERTED this scan.
            # A direct-ATS row arriving later with the same shadow replaces
            # the (unscored — scoring runs after ingest) aggregator copy.
            agg_shadow: dict[str, str] = {}
            while True:
                item = await queue.get()
                if item is None:
                    break
                # Pre-score filters at the drain chokepoint. All pure and
                # adapter-agnostic:
                #  - management title (Manager/Director/Head of/VP/etc)
                #  - optional research/ML title (drop_research_titles)
                #  - optional senior title (when include_senior_roles=False)
                #  - freshness window
                if is_management_title(item.title):
                    filtered["mgmt"] += 1
                    continue
                if drop_research and is_research_title(item.title):
                    filtered["research"] += 1
                    continue
                if drop_non_eng and is_non_engineering_title(item.title):
                    filtered["non_eng"] += 1
                    continue
                if drop_senior and is_senior_title(item.title):
                    filtered["senior"] += 1
                    continue
                if not is_within_age_window(item.posted_at, max_age_days):
                    filtered["stale"] += 1
                    continue
                skip, stale_agg_id, shadow_claim = _dedup_decision(
                    item, seen_dedup, agg_shadow
                )
                if skip:
                    filtered["dup"] += 1
                    continue
                with conn:
                    if stale_agg_id is not None:
                        # Direct row supersedes the thinner aggregator copy
                        # inserted earlier this scan (still unscored).
                        conn.execute(
                            "DELETE FROM jobs WHERE id = ?", (stale_agg_id,)
                        )
                        inserted -= 1
                    if upsert_job(conn, item):
                        inserted += 1
                        if shadow_claim is not None:
                            agg_shadow[shadow_claim] = item.id
            await closer_task
            per_source = [
                (source, label, results.get((source, label), (0, None))[0],
                 results.get((source, label), (0, None))[1])
                for (source, label, _) in adapters
            ]
            return inserted, per_source, filtered


_DEDUP_RE = __import__("re").compile(r"[^a-z0-9]+")


def _dedup_key(job: Job) -> tuple[str, ...]:
    """Stable cross-source dedupe keys. Same role at the same company from two
    different sources (e.g. Greenhouse + Adzuna) shares the normalised
    (title, company) shadow key so we don't score the same posting twice.
    Direct ATS sources (Greenhouse/Lever/Ashby/SmartRecruiters/Workday/
    Workable/Recruitee) return (job.id, shadow): the id is their identity key
    and the shadow blocks later aggregator copies. Aggregators (Adzuna/RSS/
    Job Bank) return (shadow,) — the shadow IS their identity, so they drop
    when any earlier row (direct or aggregator) claimed it."""
    title_norm = _DEDUP_RE.sub("", (job.title or "").lower())
    company_norm = _DEDUP_RE.sub("", (job.company or "").lower())
    shadow = f"{title_norm}:{company_norm}"
    if job.source in {
        "greenhouse", "lever", "ashby", "smartrecruiters", "workday",
        "workable", "recruitee",
    }:
        return (job.id, shadow)
    return (shadow,)


def _dedup_decision(
    job: Job, seen: set[str], agg_shadow: dict[str, str]
) -> tuple[bool, str | None, str | None]:
    """Drain-loop dedupe decision. Mutates `seen` (claims this job's keys).

    Returns (skip, stale_aggregator_job_id, shadow_claim):
    - skip: drop the job — its identity key (keys[0]) was already claimed, or
      (direct rows) its shadow was claimed by an earlier *direct* row this
      scan. Boards double-post the same role under two posting ids (observed:
      Speechify, two byte-identical Greenhouse JDs 13 minutes apart), so a
      direct-claimed shadow blocks later direct copies too. A shadow claimed
      by an aggregator never blocks a direct row (direct wins ties, richer
      JD — see stale_aggregator_job_id).
    - stale_aggregator_job_id: when a direct row's shadow was claimed by an
      aggregator row inserted earlier this scan, that row's id, so the caller
      deletes the thinner unscored copy. Cross-scan copies are untouched (B3).
    - shadow_claim: for aggregator rows, the shadow key the caller records in
      `agg_shadow` after a successful insert.
    """
    keys = _dedup_key(job)
    if keys[0] in seen:
        return True, None, None
    if len(keys) == 2 and keys[1] in seen and keys[1] not in agg_shadow:
        return True, None, None
    seen.update(keys)
    if len(keys) == 1:
        return False, None, keys[0]
    return False, agg_shadow.pop(keys[1], None), None


def _refresh_source_row(progress: Progress, st: dict[str, int | TaskID],
                        source: str) -> None:
    task_id = cast(TaskID, st["tid"])
    done = int(st["done"])
    total = int(st["total"])
    jobs = int(st["jobs"])
    errs = int(st["errors"])
    desc = f"  {source} — {done}/{total} slugs, {jobs} job(s)"
    if errs:
        desc += f", {errs} failed"
    progress.update(task_id, description=desc, completed=done, total=total)


async def _safe_stream(
    source: str,
    label: str,
    stream: AsyncIterator[Job],
    progress: Progress,
    source_state: dict[str, dict[str, int | TaskID]],
    overall_id: TaskID,
    results: dict[tuple[str, str], tuple[int, str | None]],
) -> AsyncIterator[Job]:
    """Wrap an adapter so a failure on one source doesn't kill the whole scan,
    while updating an aggregate per-source progress row with live job counts."""
    n = 0
    st = source_state[source]
    try:
        async for job in stream:
            n += 1
            st["jobs"] = int(st["jobs"]) + 1
            _refresh_source_row(progress, st, source)
            yield job
    except Exception as e:  # noqa: BLE001 — one bad source must never kill the scan
        # Contain ANY adapter failure, not just IngestError: a raw
        # httpx.HTTPStatusError or JSONDecodeError escaping the HTTP helpers
        # (or any other adapter bug) would otherwise kill this producer task
        # and deadlock the ingest drain loop. BaseException (CancelledError,
        # GeneratorExit on early consumer exit) still propagates. IngestError
        # keeps its clean message; anything else is tagged with its type.
        st["errors"] = int(st["errors"]) + 1
        st["done"] = int(st["done"]) + 1
        _refresh_source_row(progress, st, source)
        progress.advance(overall_id)
        msg = str(e) if isinstance(e, IngestError) else f"{type(e).__name__}: {e}"
        results[(source, label)] = (n, msg)
        return
    st["done"] = int(st["done"]) + 1
    _refresh_source_row(progress, st, source)
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

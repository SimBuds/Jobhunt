"""`jobhunt apply` — tailor + cover letter + autofill the form (human submits).

Three selection modes (mutually exclusive):
- `apply <job-id>`              — single job by id.
- `apply --top N` (1..20)       — N highest-scoring unapplied jobs.
- `apply --best`                — interactive picker over the top 10.

Per selected job:
  1. tailor resume (pipeline.tailor)
  2. write cover letter (pipeline.cover)
  3. render <Name>_Resume.docx (resume.render_docx)
  4. save cover-letter.md
  5. open Playwright headed at job.url, run the matching ATS handler
  6. log fill-plan.json, mark application status=drafted
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

import typer

from jobhunt.browser import autofill
from jobhunt.commands._manual_intake import synth_manual_job
from jobhunt.commands._refs import resolve_job_ref
from jobhunt.config import Config, load_config
from jobhunt.db import (
    connect,
    mark_interview_scheduled,
    mark_response_received,
    set_decline_reason,
    update_job_url,
    upsert_application,
    upsert_job,
    write_score,
)
from jobhunt.db import (
    set_outcome as db_set_outcome,
)
from jobhunt.errors import BrowserError, JobHuntError, PipelineError
from jobhunt.http import RateLimiter, resolve_redirect, with_client
from jobhunt.ingest.manual import fetch_url_as_job, robots_allowed
from jobhunt.models import Job
from jobhunt.pipeline.audit import AuditResult, audit, write_audit
from jobhunt.pipeline.cover import CoverLetter, write_cover_with_retry
from jobhunt.pipeline.score import ScoreResult, prompt_hash, score_job
from jobhunt.pipeline.tailor import TailoredResume, tailor_resume_with_retry
from jobhunt.pipeline.tailor_diff import build_tailor_diff
from jobhunt.resume.render_cover_docx import render_cover
from jobhunt.resume.render_docx import render

app = typer.Typer(
    help="Tailor resume + cover letter and autofill the application form.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    job_id: str | None = typer.Argument(None, help="Specific job id from `jobhunt list`."),
    top: int | None = typer.Option(
        None, "--top", min=1, max=20, help="Auto-pick the N best-fit unapplied jobs (1..20)."
    ),
    best: bool = typer.Option(
        False, "--best", help="Interactively pick from the top 10 unapplied jobs."
    ),
    min_score: int | None = typer.Option(
        None,
        "--min-score",
        help="Floor for --top / --best selection (default: pipeline.min_score).",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Generate docs only; skip the browser autofill step."
    ),
    set_status: str | None = typer.Option(
        None,
        "--set-status",
        help=(
            "Update status without re-tailoring. Put the flag before <job-id>. "
            "Allowed: drafted, applied, interviewing, offer, rejected, withdrawn."
        ),
    ),
    mark_response: str | None = typer.Option(
        None,
        "--mark-response",
        help=(
            "Record recruiter response date or timestamp without re-tailoring. "
            "Use --recruiter-type to tag who responded."
        ),
    ),
    mark_interview: str | None = typer.Option(
        None,
        "--mark-interview",
        help=(
            "Record first interview date or timestamp. Promotes status to "
            "'interviewing' when needed."
        ),
    ),
    set_outcome: str | None = typer.Option(
        None,
        "--set-outcome",
        help=(
            "Record terminal outcome: offer, rejected, withdrawn, or ghosted."
        ),
    ),
    recruiter_type: str | None = typer.Option(
        None,
        "--recruiter-type",
        help=(
            "Tag who responded with --mark-response. One of: internal_recruiter, "
            "hiring_manager, external_agency, unknown."
        ),
    ),
    url: str | None = typer.Option(
        None, "--url", help="Fetch one JD from a URL, score it, then apply."
    ),
    title: str | None = typer.Option(
        None, "--title",
        help="Override the auto-detected job title (with --url).",
    ),
    company: str | None = typer.Option(
        None, "--company",
        help="Override the auto-detected company name (with --url).",
    ),
    no_score: bool = typer.Option(
        False, "--no-score",
        help="Skip the score pass for ad-hoc jobs. Audit falls back to title/JD-only must-haves.",
    ),
    force_robots: bool = typer.Option(
        False, "--force-robots",
        help="Fetch a URL even if robots.txt disallows. Personal-use override only.",
    ),
    description_from_stdin: bool = typer.Option(
        False, "--stdin", "--description-from-stdin",
        help=(
            "Read JD body from stdin. Requires --url, --title, and --company."
        ),
    ),
    include_borderline: bool = typer.Option(
        False, "--include-borderline",
        help=(
            "With --best: also surface up to 10 stretch jobs in the "
            "[min_score-10, min_score) band, labelled `stretch`. Lets you "
            "pick stretch applications when the high-fit list is dry."
        ),
    ),
) -> None:
    lifecycle_only = (
        set_status is not None
        or mark_response is not None
        or mark_interview is not None
        or set_outcome is not None
    )
    if lifecycle_only:
        if job_id is None:
            typer.echo(
                "error: lifecycle flags (--set-status / --mark-response / "
                "--mark-interview / --set-outcome) require <job-id>.",
                err=True,
            )
            raise typer.Exit(code=2)
        if top is not None or best:
            typer.echo(
                "error: lifecycle flags are incompatible with --top / --best.",
                err=True,
            )
            raise typer.Exit(code=2)
        if recruiter_type is not None and mark_response is None:
            typer.echo(
                "error: --recruiter-type requires --mark-response.",
                err=True,
            )
            raise typer.Exit(code=2)
        _run_lifecycle(
            job_id,
            set_status=set_status,
            mark_response=mark_response,
            mark_interview=mark_interview,
            set_outcome=set_outcome,
            recruiter_type=recruiter_type,
        )
        return

    manual_mode = url is not None
    flags = sum(x is not None and x is not False for x in (job_id, top, best, url))
    if flags == 0:
        typer.echo(
            "error: pass <job-id>, --top N, --best, or --url.",
            err=True,
        )
        raise typer.Exit(code=2)
    if flags > 1:
        typer.echo(
            "error: selection modes (<job-id>, --top, --best, --url) are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)

    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)
    effective_min_score = min_score if min_score is not None else cfg.pipeline.min_score

    if url is not None:
        if description_from_stdin and (not title or not company):
            typer.echo(
                "error: --description-from-stdin requires --title and --company.",
                err=True,
            )
            raise typer.Exit(code=2)
        jobs = asyncio.run(
            _resolve_manual(
                cfg,
                url=url,
                title=title,
                company=company,
                no_score=no_score,
                force_robots=force_robots,
                description_from_stdin=description_from_stdin,
            )
        )
    else:
        conn = connect(cfg.paths.db_path)
        try:
            if job_id is not None:
                jobs = _resolve_by_id(conn, job_id)
            elif top is not None:
                jobs = _resolve_top_n(conn, n=top, min_score=effective_min_score)
            else:
                jobs = _resolve_interactive(
                    conn,
                    min_score=effective_min_score,
                    include_borderline=include_borderline,
                )
        finally:
            conn.close()

    if not jobs:
        typer.echo("nothing to apply to.")
        raise typer.Exit(code=1)

    typer.echo(f"\nselected {len(jobs)} job(s):")
    for j in jobs:
        score = j["score"] if j["score"] is not None else "—"
        suffix = "" if str(j["source"]) == "manual" else f" — {j['id']}"
        typer.echo(f"  • [{score}] {j['title']} @ {j['company']}{suffix}")

    asyncio.run(_apply_each(cfg, jobs, no_browser=no_browser))

    if manual_mode and url is not None:
        _maybe_suggest_add(cfg, url)


def _maybe_suggest_add(cfg: Config, url: str) -> None:
    """Print a one-line `jobhunt add` nudge when --url points at a recognized
    ATS whose slug isn't already in config. Slug acquisition becomes a
    byproduct of normal use this way. iCIMS is recognized by the URL extractor
    but isn't ingestable, so suppress the nudge for it."""
    from jobhunt.discover.url_extract import extract

    extracted = extract(url)
    if extracted is None:
        return
    if extracted.ats in ("icims",):
        return
    if extracted.ats == "workday":
        if not extracted.host or not extracted.site:
            return
        config_value = f"{extracted.slug}:{extracted.host}:{extracted.site}"
    else:
        config_value = extracted.slug
    existing = getattr(cfg.ingest, extracted.ats, None)
    if existing is None or config_value in existing:
        return
    typer.echo(
        f"\nnote: this URL is on {extracted.ats} (slug {config_value!r}) — "
        f"run `jobhunt add {url}` to scan their full board on future runs."
    )


VALID_STATUSES = (
    "drafted",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)


def _run_lifecycle(
    job_id: str,
    *,
    set_status: str | None,
    mark_response: str | None,
    mark_interview: str | None,
    set_outcome: str | None,
    recruiter_type: str | None,
) -> None:
    if set_status is not None and set_status not in VALID_STATUSES:
        typer.echo(
            f"error: invalid status {set_status!r}. Allowed: {', '.join(VALID_STATUSES)}",
            err=True,
        )
        raise typer.Exit(code=2)
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        row = conn.execute(
            "SELECT id, status FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            typer.echo(
                f"error: no application for {job_id!r}. Run `apply {job_id}` first.",
                err=True,
            )
            raise typer.Exit(code=1)
        with conn:
            if set_status is not None:
                upsert_application(
                    conn,
                    application_id=row["id"],
                    job_id=job_id,
                    status=set_status,
                    resume_path=None,
                    cover_path=None,
                    fill_plan_path=None,
                    applied_week=None,
                )
                typer.echo(f"{job_id}: status {row['status']} → {set_status}")
            if mark_response is not None:
                try:
                    mark_response_received(conn, job_id, mark_response, recruiter_type)
                except ValueError as e:
                    typer.echo(f"error: {e}", err=True)
                    raise typer.Exit(code=2) from e
                type_note = f" (type: {recruiter_type})" if recruiter_type else ""
                typer.echo(f"{job_id}: response received {mark_response}{type_note}")
            if mark_interview is not None:
                mark_interview_scheduled(conn, job_id, mark_interview)
                typer.echo(f"{job_id}: interview scheduled {mark_interview}")
            if set_outcome is not None:
                try:
                    db_set_outcome(conn, job_id, set_outcome)
                except ValueError as e:
                    typer.echo(f"error: {e}", err=True)
                    raise typer.Exit(code=2) from e
                typer.echo(f"{job_id}: outcome set to {set_outcome}")

        effective_status = set_status or row["status"]
        if effective_status == "interviewing" or mark_interview is not None:
            prep_path = (
                cfg.paths.data_dir / "interview-prep" / f"{_safe_id(job_id)}.md"
            )
            if prep_path.is_file():
                typer.echo(f"  prep doc exists: {prep_path}")
            else:
                typer.echo(f"  → draft prep doc: jobhunt interview-prep {job_id}")
    finally:
        conn.close()


# --- selection helpers --------------------------------------------------------


async def _resolve_manual(
    cfg: Config,
    *,
    url: str,
    title: str | None,
    company: str | None,
    no_score: bool,
    force_robots: bool,
    description_from_stdin: bool = False,
) -> list[sqlite3.Row]:
    """Build a Job from --url, upsert it, optionally score it, and return a
    row-list matching the shape `_apply_each` expects."""
    description: str | None = None
    if description_from_stdin:
        assert title and company  # caller validated
        typer.echo("  reading JD body from stdin (Ctrl-D to finish)...")
        description = sys.stdin.read()

    job = await synth_manual_job(
        cfg,
        url=url,
        title=title,
        company=company,
        force_robots=force_robots,
        description=description,
    )

    conn = connect(cfg.paths.db_path)
    try:
        if not no_score:
            typer.echo("  scoring...")
            try:
                result = await score_job(cfg, job)
            except JobHuntError as e:
                typer.echo(f"  ! score failed: {e}", err=True)
            else:
                ph = prompt_hash(cfg.paths.kb_dir)
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
                tag = (
                    f"DECLINE: {result.decline_reason}"
                    if result.decline_reason
                    else str(result.score)
                )
                typer.echo(f"  scored [{tag}]")
        rows = list(
            conn.execute(
                "SELECT j.*, s.score AS score FROM jobs j "
                "LEFT JOIN scores s ON s.job_id = j.id WHERE j.id = ?",
                (job.id,),
            )
        )
    finally:
        conn.close()
    return rows


def _resolve_by_id(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    """Resolve an exact job id *or* a company/title fragment to its row.

    `jobhunt apply faire` now works the same way `jobhunt track response faire`
    already did. An exact id still wins outright — including for a declined
    job, which the fragment path deliberately skips.
    """
    resolved = resolve_job_ref(conn, job_id, scope="jobs")
    rows = list(
        conn.execute(
            "SELECT j.*, s.score AS score FROM jobs j "
            "LEFT JOIN scores s ON s.job_id = j.id "
            "WHERE j.id = ?",
            (resolved,),
        )
    )
    if not rows:
        typer.echo(f"error: no job with id {resolved!r}", err=True)
        raise typer.Exit(code=1)
    return rows


def _unapplied_top_query(min_score: int, limit: int) -> tuple[str, tuple[int, int]]:
    sql = (
        "SELECT j.*, s.score AS score FROM jobs j "
        "JOIN scores s ON s.job_id = j.id "
        "LEFT JOIN applications a ON a.job_id = j.id "
        "WHERE s.score >= ? "
        "  AND (j.decline_reason IS NULL OR j.decline_reason = '') "
        "  AND (a.id IS NULL OR a.status = 'drafted') "
        "ORDER BY s.score DESC, j.posted_at DESC "
        "LIMIT ?"
    )
    return sql, (min_score, limit)


def _resolve_top_n(conn: sqlite3.Connection, *, n: int, min_score: int) -> list[sqlite3.Row]:
    sql, params = _unapplied_top_query(min_score, n)
    return list(conn.execute(sql, params))


def _resolve_interactive(
    conn: sqlite3.Connection,
    *,
    min_score: int,
    include_borderline: bool = False,
) -> list[sqlite3.Row]:
    sql, params = _unapplied_top_query(min_score, 10)
    rows = list(conn.execute(sql, params))

    borderline: list[sqlite3.Row] = []
    if include_borderline:
        borderline_floor = max(0, min_score - 10)
        borderline_sql = (
            "SELECT j.*, s.score AS score FROM jobs j "
            "JOIN scores s ON s.job_id = j.id "
            "LEFT JOIN applications a ON a.job_id = j.id "
            "WHERE s.score >= ? AND s.score < ? "
            "  AND (j.decline_reason IS NULL OR j.decline_reason = '') "
            "  AND (a.id IS NULL OR a.status = 'drafted') "
            "ORDER BY s.score DESC, j.posted_at DESC "
            "LIMIT 10"
        )
        borderline = list(conn.execute(borderline_sql, (borderline_floor, min_score)))

    if not rows and not borderline:
        return rows
    if rows:
        typer.echo(f"top {len(rows)} unapplied job(s) with score >= {min_score}:\n")
        for i, r in enumerate(rows, start=1):
            typer.echo(f"  [{i:>2}] {r['score']:>3}  {r['title']} @ {r['company']}")
            typer.echo(f"        {r['location']} — {r['id']}")
    if borderline:
        offset = len(rows)
        typer.echo(
            f"\nstretch ({max(0, min_score - 10)}–{min_score - 1}) — "
            f"{len(borderline)} job(s):\n"
        )
        for i, r in enumerate(borderline, start=offset + 1):
            typer.echo(
                f"  [{i:>2}] {r['score']:>3}  {r['title']} @ {r['company']}  (stretch)"
            )
            typer.echo(f"        {r['location']} — {r['id']}")
    typer.echo("")
    raw = typer.prompt(
        "Pick numbers to apply to (e.g. '1,3,7' or '1-5'); blank to cancel",
        default="",
        show_default=False,
    )
    combined = list(rows) + list(borderline)
    picks = _parse_picks(raw, len(combined))
    return [combined[i - 1] for i in picks]


def _parse_picks(raw: str, max_n: int) -> list[int]:
    raw = raw.strip()
    if not raw:
        return []
    out: set[int] = set()
    for chunk in raw.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, hi_s = chunk.split("-", 1)
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                continue
            for n in range(min(lo, hi), max(lo, hi) + 1):
                if 1 <= n <= max_n:
                    out.add(n)
        else:
            try:
                n = int(chunk)
            except ValueError:
                continue
            if 1 <= n <= max_n:
                out.add(n)
    return sorted(out)


# --- apply each --------------------------------------------------------------


async def _apply_each(cfg: Config, rows: list[sqlite3.Row], *, no_browser: bool) -> None:
    import json as _json
    from collections import Counter

    from jobhunt.gateway.warm import warm_model

    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    verified: dict[str, object] = {}
    if verified_path.is_file():
        verified = _json.loads(verified_path.read_text(encoding="utf-8"))

    # Warm the model once before the per-job loop. All task slots (score,
    # tailor, cover) share the same model on Casey's setup, so warming any
    # one of them keeps the others warm. Saves the cold-load cost on the
    # first real call.
    await warm_model(cfg, task="score")

    verdicts: list[str] = []
    violation_topics: Counter[str] = Counter()

    # Overlap the next job's LLM phase with the current job's IO phase
    # (render + browser + user-confirms-submission). Single-VRAM-slot
    # Ollama still serves LLM calls sequentially, but the user's review time
    # between jobs is dead time we can use to pre-generate the next tailor +
    # cover. Worst case (user wants to skip the next job): we wasted one
    # tailor + cover. Best case (the usual path): the next job is already
    # drafted by the time the user moves on.
    if not rows:
        return

    # Job 0's LLM runs alone (no concurrent IO) — print live.
    # Prefetched LLM phases for job 1+ buffer their output to avoid
    # interleaving with the previous job's IO-phase prompts.
    next_buf: list[tuple[str, bool]] | None = None
    next_llm: asyncio.Task[_LLMPhaseResult | None] = asyncio.create_task(
        _apply_llm_phase(cfg, _row_to_job(rows[0]), verified=verified)
    )
    for i, row in enumerate(rows):
        job = _row_to_job(row)
        phase = await next_llm
        # Flush the prefetched LLM phase's buffered output (if any) now
        # that it's this job's turn to be in the foreground.
        if next_buf is not None:
            for msg, err in next_buf:
                typer.echo(msg, err=err)
            next_buf = None
        # Kick off the *next* job's LLM phase BEFORE the current IO phase,
        # so the LLM is already running while the user reads/submits.
        if i + 1 < len(rows):
            next_job = _row_to_job(rows[i + 1])
            buf: list[tuple[str, bool]] = []

            def _buf_echo(
                msg: str = "",
                *,
                err: bool = False,
                _b: list[tuple[str, bool]] = buf,
            ) -> None:
                _b.append((msg, err))

            next_buf = buf
            next_llm = asyncio.create_task(
                _apply_llm_phase(cfg, next_job, verified=verified, echo=_buf_echo)
            )

        if phase is None:
            pass  # tailor/cover/audit failed — fall through to between-jobs prompt
        elif phase.early_exit:
            verdicts.append(phase.audit_result.verdict)
            for t in phase.topics:
                violation_topics[t] += 1
        else:
            verdict, topics = await _apply_io_phase(
                cfg, job, phase, no_browser=no_browser
            )
            verdicts.append(verdict)
            for t in topics:
                violation_topics[t] += 1

        # Between-jobs prompt: give the user a clean exit. Only show when
        # there's another job queued and stdin is a TTY (non-interactive
        # runs auto-continue so the loop can complete unattended).
        if i + 1 < len(rows) and sys.stdin.isatty():
            keep_going = await _prompt_continue(_row_to_job(rows[i + 1]))
            if not keep_going:
                next_llm.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_llm
                typer.echo("stopping loop. unprocessed jobs remain unapplied.")
                break

    # End-of-loop summary — only useful when more than one job ran.
    if len(verdicts) > 1:
        ship = sum(1 for v in verdicts if v == "ship")
        revise = sum(1 for v in verdicts if v == "revise")
        block = sum(1 for v in verdicts if v == "block")
        typer.echo(
            f"\n=== summary: {len(verdicts)} job(s) — "
            f"{ship} ship, {revise} revise, {block} block ==="
        )
        if violation_topics:
            top = ", ".join(
                f"{topic}×{n}" for topic, n in violation_topics.most_common(5)
            )
            typer.echo(f"top warning categories: {top}")


@dataclass
class _LLMPhaseResult:
    """Output of the LLM-bound phase of `_apply_one`. Carries everything the
    IO phase needs to finish (render + browser + record). Produced by
    `_apply_llm_phase` and consumed by `_apply_io_phase`, with an asyncio
    task between them so the next job's LLM can overlap the current job's
    user-review/browser time."""
    out_dir: Path
    tailored: TailoredResume
    cover: CoverLetter
    audit_result: AuditResult
    audit_path: Path
    topics: list[str]
    # When verdict == "block", IO phase short-circuits.
    early_exit: bool


def _default_echo(msg: str = "", *, err: bool = False) -> None:
    typer.echo(msg, err=err)


async def _apply_llm_phase(
    cfg: Config,
    job: Job,
    *,
    verified: dict[str, object],
    echo: Callable[..., None] = _default_echo,
) -> _LLMPhaseResult | None:
    """LLM-bound work for one job: tailor + cover + audit + write artifacts.

    Returns None when an unrecoverable LLM/audit error means the caller
    should skip this job entirely. Returns `_LLMPhaseResult` otherwise —
    `early_exit=True` when audit verdict was 'block' (no render/browser).
    """
    out_dir = cfg.paths.data_dir / "applications" / _safe_id(job.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    echo(f"\n=== {job.title} @ {job.company} — {job.id} ===")
    job = await _deepen_thin_adzuna(cfg, job, echo=echo)
    echo("    … tailoring resume (LLM, ~30–60s)")
    try:
        tailored, tailor_violations, tailor_attempts = await tailor_resume_with_retry(
            cfg, job, max_attempts=cfg.pipeline.tailor_retry_attempts,
        )
    except JobHuntError as e:
        echo(f"    ! tailor failed: {e}", err=True)
        return None
    if tailor_attempts > 1:
        n = len(tailor_violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        echo(f"    tailor: {tailor_attempts} attempts ({tag})")

    echo("    … writing cover letter (LLM, ~30s)")
    try:
        cover, cover_violations, cover_attempts = await write_cover_with_retry(
            cfg, job,
            verified=verified, company=job.company,
            max_words=cfg.pipeline.cover_max_words,
            max_attempts=cfg.pipeline.cover_retry_attempts,
        )
    except JobHuntError as e:
        echo(f"    ! cover letter failed: {e}", err=True)
        return None
    if cover_attempts > 1:
        n = len(cover_violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        echo(f"    cover: {cover_attempts} attempts ({tag})")

    score_result = _load_score(cfg, job.id)
    try:
        audit_result = audit(
            tailored=tailored, cover=cover, score=score_result, verified=verified,
            company=job.company, cover_max_words=cfg.pipeline.cover_max_words,
            job_description=job.description, job_title=job.title,
        )
    except PipelineError as e:
        echo(f"    ! audit failed: {e}", err=True)
        return None

    audit_path = write_audit(out_dir, audit_result)
    diff_path = out_dir / "tailor-diff.md"
    diff_path.write_text(
        build_tailor_diff(
            verified=verified, tailored=tailored, score=score_result,
            job_title=job.title, job_company=job.company,
        ),
        encoding="utf-8",
    )
    coverage = audit_result.keyword_coverage_pct
    coverage_label = f"{coverage}%" if coverage is not None else "n/a"
    echo(
        f"    audit: verdict={audit_result.verdict} "
        f"keyword_coverage={coverage_label} "
        f"missing={len(audit_result.missing_must_haves)} "
        f"cover_violations={len(audit_result.cover_letter_violations)} "
        f"alignment={len(audit_result.alignment_flags)}"
    )

    topics = _audit_topics(audit_result)
    early_exit = audit_result.verdict == "block"
    if early_exit:
        for flag in audit_result.fabrication_flags:
            echo(f"    BLOCK: {flag}", err=True)
        echo(f"    + {audit_path.name} (see for details)")

    return _LLMPhaseResult(
        out_dir=out_dir,
        tailored=tailored,
        cover=cover,
        audit_result=audit_result,
        audit_path=audit_path,
        topics=topics,
        early_exit=early_exit,
    )


async def _deepen_thin_adzuna(
    cfg: Config,
    job: Job,
    *,
    echo: Callable[..., None] = _default_echo,
) -> Job:
    """Pre-tailor enrichment for snippet-length Adzuna rows. The tailor, audit
    keyword coverage, and cover anchors all degrade on Adzuna's ~500-char
    snippets, so when the description is under `cfg.pipeline.thin_jd_chars`
    this resolves the tracking redirect, robots-checks the employer page, and
    fetches the full JD. The enriched description is persisted and the stale
    snippet-based score row deleted — prompt_hash is unchanged, so deletion is
    what makes the next scan re-score against the full JD. Best-effort: robots
    denial or any fetch failure keeps the snippet and continues the apply."""
    if job.source != "adzuna_ca" or not job.url:
        return job
    if len(job.description or "") >= cfg.pipeline.thin_jd_chars:
        return job
    job = await _resolve_adzuna_url(cfg, job)
    url = job.url
    assert url is not None  # guarded above; resolve never drops it
    if not robots_allowed(url, cfg.ingest.user_agent):
        echo(
            "    thin JD: robots.txt disallows fetching the employer page; "
            "keeping the snippet",
            err=True,
        )
        return job
    echo("    thin JD: fetching full posting from employer page …")
    try:
        fetched = await fetch_url_as_job(url, user_agent=cfg.ingest.user_agent)
    except Exception as e:  # noqa: BLE001 — enrichment must never break apply
        echo(f"    thin JD: fetch failed ({e}); keeping the snippet", err=True)
        return job
    old_len = len(job.description or "")
    if not fetched.description or len(fetched.description) <= old_len:
        echo("    thin JD: employer page yielded nothing longer; keeping the snippet")
        return job
    conn = connect(cfg.paths.db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE jobs SET description = ? WHERE id = ?",
                (fetched.description, job.id),
            )
            conn.execute("DELETE FROM scores WHERE job_id = ?", (job.id,))
    finally:
        conn.close()
    echo(
        f"    thin JD: enriched {old_len} -> {len(fetched.description)} chars; "
        "snippet-based score invalidated (re-scored on next scan)"
    )
    return job.model_copy(update={"description": fetched.description})


async def _resolve_adzuna_url(cfg: Config, job: Job) -> Job:
    """Chase Adzuna's tracking redirect once per application so the browser,
    fill-plan, and `add` suggestion land on the employer's real posting URL.
    One HEAD chase per applied job (not per ingested row). Never raises:
    `resolve_redirect` falls back to the original URL on any error."""
    if job.source != "adzuna_ca" or not job.url:
        return job
    url = job.url
    final = await with_client(
        lambda client: resolve_redirect(client, url, RateLimiter(1.0)),
        user_agent=cfg.ingest.user_agent,
    )
    if final == url:
        return job
    conn = connect(cfg.paths.db_path)
    try:
        with conn:
            update_job_url(conn, job.id, final)
    finally:
        conn.close()
    typer.echo(f"    resolved adzuna redirect -> {final}")
    return job.model_copy(update={"url": final})


async def _apply_io_phase(
    cfg: Config,
    job: Job,
    phase: _LLMPhaseResult,
    *,
    no_browser: bool,
) -> tuple[str, list[str]]:
    """IO-bound finish for one job: print revise warnings, render docx,
    optional browser autofill, prompt submission status, record."""
    job = await _resolve_adzuna_url(cfg, job)
    audit_result = phase.audit_result
    if audit_result.verdict == "revise":
        for v in audit_result.cover_letter_violations:
            typer.echo(f"    revise: {v}", err=True)
        for v in audit_result.alignment_flags:
            typer.echo(f"    revise: {v}", err=True)
        if audit_result.missing_must_haves:
            preview = audit_result.missing_must_haves[:5]
            tail = (
                f" (+{len(audit_result.missing_must_haves) - 5} more)"
                if len(audit_result.missing_must_haves) > 5 else ""
            )
            pct = audit_result.keyword_coverage_pct
            low = f" (coverage {pct}% < 70%)" if pct is not None and pct < 70 else ""
            typer.echo(
                f"    revise: {len(audit_result.missing_must_haves)} JD must-have(s) "
                f"not in resume — {', '.join(preview)}{tail}{low}",
                err=True,
            )

    resume_path, cover_docx_path = _render_artifacts(
        cfg, job, phase.tailored, phase.cover, phase.out_dir
    )
    typer.echo(f"    + {resume_path.name}")
    typer.echo(f"    + {cover_docx_path.name}")

    # Persist a `drafted` row the moment artifacts exist on disk. Everything
    # below can exit the process — a Playwright crash, a Ctrl-C at the submit
    # prompt — and until this write moved up, that lost the entire tailoring
    # run from tracking while its .docx files sat in data/applications/. Found
    # 2026-07-24: 11 orphan dirs, 3 holding complete `ship`-verdict artifact
    # sets. `drafted` keeps the job eligible for re-selection
    # (`_unapplied_top_query` admits `a.status = 'drafted'`), and the upsert is
    # idempotent on job_id, so the post-prompt write below simply overwrites
    # the status and fills in fill_plan_path.
    _record_application(cfg, job, "drafted", resume_path, cover_docx_path, None)

    plan_path = await _run_browser_step(
        cfg, job, resume_path=resume_path, cover_path=cover_docx_path,
        out_dir=phase.out_dir, no_browser=no_browser,
    )
    status = _confirm_submission_status(
        plan_path, browser_attempted=not no_browser and bool(job.url)
    )
    _record_application(cfg, job, status, resume_path, cover_docx_path, plan_path)
    return audit_result.verdict, phase.topics


async def _apply_one(
    cfg: Config,
    job: Job,
    *,
    verified: dict[str, object],
    no_browser: bool,
) -> tuple[str | None, list[str]]:
    """Run the tailor → cover → audit → render → autofill → DB pipeline for one job.

    Side effects:
      - writes audit.json, tailored-resume.json, cover-letter.md, *.docx files;
      - on `block` verdict: returns early after writing audit.json (no docs);
      - launches a headed browser unless `no_browser` is set or job has no URL;
      - upserts an `applications` row.

    Returns (verdict, violation_topics) for end-of-loop summarisation. Returns
    (None, []) when the job failed before producing an audit (tailor/cover error)
    so the caller can skip it cleanly. Topics are short coarse-grained labels
    like "fabrication", "cover-violation", "coverage", "alignment".

    Delegates to `_apply_llm_phase` + `_apply_io_phase`. Kept as the
    single-job entry point so external callers (tests, ad-hoc invocations)
    don't have to manage the phase split. The pipelined overlap (next job's
    LLM running while the current job's IO is in flight) lives in
    `_apply_each`, not here.
    """
    phase = await _apply_llm_phase(cfg, job, verified=verified)
    if phase is None:
        return None, []
    if phase.early_exit:
        return phase.audit_result.verdict, phase.topics
    return await _apply_io_phase(cfg, job, phase, no_browser=no_browser)




def _audit_topics(audit_result: AuditResult) -> list[str]:
    """Coarse-grained categorical labels for end-of-loop summarisation.
    One entry per category that fired on this audit — no deduping needed at
    the call site since Counter handles aggregation.
    """
    topics: list[str] = []
    if audit_result.fabrication_flags:
        topics.append("fabrication")
    if audit_result.cover_letter_violations:
        topics.append("cover-violation")
    if (
        audit_result.keyword_coverage_pct is not None
        and audit_result.keyword_coverage_pct < 70
    ):
        topics.append("coverage")
    if audit_result.alignment_flags:
        topics.append("alignment")
    return topics


def _render_artifacts(
    cfg: Config,
    job: Job,
    tailored: TailoredResume,
    cover: CoverLetter,
    out_dir: Path,
) -> tuple[Path, Path]:
    """Write resume + cover .docx, cover-letter.md, and tailored-resume.json."""
    import json as _json

    contact_line = (
        cfg.applicant.email
        + ("  |  " + cfg.applicant.phone if cfg.applicant.phone else "")
        + f"  |  {cfg.applicant.portfolio_url}  |  {cfg.applicant.linkedin_url}  |  "
        + cfg.applicant.github_url
    )
    name_slug = "_".join(cfg.applicant.full_name.split()) if cfg.applicant.full_name else ""
    prefix = f"{name_slug}_" if name_slug else ""
    resume_path = out_dir / f"{prefix}Resume.docx"
    render(
        tailored,
        contact_line=contact_line,
        name=cfg.applicant.full_name,
        out_path=resume_path,
    )
    cover_docx_path = out_dir / f"{prefix}Cover_Letter.docx"
    render_cover(
        cover,
        contact_line=contact_line,
        name=cfg.applicant.full_name,
        out_path=cover_docx_path,
    )
    (out_dir / "cover-letter.md").write_text(cover.to_markdown(), encoding="utf-8")
    (out_dir / "tailored-resume.json").write_text(
        _json.dumps(asdict(tailored), indent=2), encoding="utf-8"
    )
    return resume_path, cover_docx_path


async def _run_browser_step(
    cfg: Config,
    job: Job,
    *,
    resume_path: Path,
    cover_path: Path,
    out_dir: Path,
    no_browser: bool,
) -> Path | None:
    if no_browser:
        typer.echo("    (browser skipped via --no-browser)")
        return None
    if not job.url:
        typer.echo("    ! no URL on this job — browser skipped", err=True)
        return None
    import sys as _sys
    while True:
        typer.echo("    … launching browser autofill")
        try:
            plan_path = await autofill(
                url=job.url,
                profile=cfg.applicant,
                resume_path=resume_path,
                cover_path=cover_path,
                out_dir=out_dir,
                headed=cfg.browser.headed,
                user_data_dir=cfg.browser.user_data_dir,
            )
            typer.echo(f"    + {plan_path.name}")
            return plan_path
        except BrowserError as e:
            typer.echo(f"    ! browser step failed: {e}", err=True)
            if not _sys.stdin.isatty():
                return None
            try:
                raw = input("    try again? [r]etry / [s]kip: ").strip().lower()
            except EOFError:
                return None
            if raw in ("r", "retry"):
                continue
            return None


async def _prompt_continue(next_job: Job) -> bool:
    """Ask the user whether to proceed with the next job in a `--top N` loop.

    Returns True on yes (default), False on no. Uses `asyncio.to_thread`
    so the prefetched LLM task can keep running on the event loop while
    the user thinks. EOFError (closed stdin) and unrecognised input both
    default to yes — the loop is opt-out, not opt-in.
    """
    prompt = (
        f"\nnext: {next_job.title or '?'} @ {next_job.company or '?'}\n"
        f"    continue? [Y]es / [n]o: "
    )
    try:
        raw = await asyncio.to_thread(input, prompt)
    except EOFError:
        return True
    return raw.strip().lower() not in ("n", "no")


def _confirm_submission_status(
    plan_path: Path | None, *, browser_attempted: bool = False
) -> str:
    # Skip the prompt entirely when --no-browser was passed (plan_path is None
    # and the browser was never launched). The user will submit manually later
    # and can update with `apply --set-status applied <id>`.
    if not browser_attempted and plan_path is None:
        return "drafted"
    import sys as _sys
    if not _sys.stdin.isatty():
        typer.echo("    (non-interactive — status set to drafted; update with --set-status)")
        return "drafted"
    try:
        raw = input("    did you submit? [y]es / [n]o / [w]ithdrawn: ").strip().lower()
    except EOFError:
        raw = "n"
    if raw in ("y", "yes"):
        return "applied"
    if raw in ("w", "withdrawn"):
        return "withdrawn"
    return "drafted"


def _record_application(
    cfg: Config,
    job: Job,
    status: str,
    resume_path: Path,
    cover_docx_path: Path,
    plan_path: Path | None,
) -> None:
    from datetime import date

    iso = date.today().isocalendar()
    week_label = f"{iso.year}-W{iso.week:02d}"
    conn = connect(cfg.paths.db_path)
    try:
        with conn:
            # Re-upsert the job before writing the application so the FK target
            # is guaranteed to exist. Defends against races with `scan --refresh`
            # (which deletes unapplied jobs) and ad-hoc DB edits — the job is
            # already in memory, and `applications` is the source of truth for
            # history regardless of what `jobs` looks like.
            upsert_job(conn, job)
            upsert_application(
                conn,
                application_id=str(uuid.uuid4()),
                job_id=job.id,
                status=status,
                resume_path=str(resume_path),
                cover_path=str(cover_docx_path),
                fill_plan_path=str(plan_path) if plan_path else None,
                applied_week=week_label,
                # channel stays None: new rows default to 'pipeline', and a
                # re-tailor of a job first logged via `track applied` keeps
                # its manual channel instead of being reclassified.
            )
    finally:
        conn.close()


def _load_score(cfg: Config, job_id: str) -> ScoreResult | None:
    """Pull the latest score row for a job. Returns None if never scored."""
    import json as _json

    conn = connect(cfg.paths.db_path)
    try:
        row = conn.execute(
            "SELECT score, reasons, red_flags, must_clarify, model "
            "FROM scores WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        matched = _json.loads(row["reasons"] or "[]")
        gaps = _json.loads(row["must_clarify"] or "[]")
        red_flags = _json.loads(row["red_flags"] or "[]")
    except (TypeError, ValueError):
        matched, gaps, red_flags = [], [], []
    decline_reason = red_flags[0] if red_flags else None
    return ScoreResult(
        score=int(row["score"]),
        matched_must_haves=list(matched),
        gaps=list(gaps),
        decline_reason=decline_reason,
        ai_bonus_present=False,  # not persisted; not needed by audit
        model=row["model"] or "",
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        company=row["company"],
        title=row["title"],
        location=row["location"],
        description=row["description"],
        url=row["url"],
    )


_FS_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_id(s: str) -> str:
    return _FS_RE.sub("_", s)


# Re-export for tests.
__all__ = ["app", "_parse_picks"]


if False:  # pragma: no cover — silences unused-import warnings on PipelineError
    _ = PipelineError

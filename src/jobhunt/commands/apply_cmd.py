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
import uuid
from dataclasses import asdict
from pathlib import Path

import typer

from jobhunt.browser import autofill
from jobhunt.config import Config, load_config
from jobhunt.db import connect, set_decline_reason, upsert_application, upsert_job, write_score
from jobhunt.errors import BrowserError, IngestError, JobHuntError, PipelineError
from jobhunt.ingest.manual import build_job_from_text, fetch_url_as_job, robots_allowed
from jobhunt.models import Job
from jobhunt.pipeline.audit import audit, write_audit
from jobhunt.pipeline.cover import CoverLetter, write_cover_with_retry
from jobhunt.pipeline.score import ScoreResult, prompt_hash, score_job
from jobhunt.pipeline.tailor import TailoredResume, tailor_resume
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
        None, "--min-score", help="Floor for --top / --best selection (default: pipeline.min_score)."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Generate docs only; skip the browser autofill step."
    ),
    set_status: str | None = typer.Option(
        None,
        "--set-status",
        help=(
            "Update an existing application's status without re-tailoring. "
            "Use as `apply --set-status STATUS <job-id>` (flag before the id). "
            "One of: drafted, applied, interviewing, offer, rejected, withdrawn."
        ),
    ),
    url: str | None = typer.Option(
        None, "--url", help='Fetch a single JD from this URL, score it, then apply. Quote the URL if it contains & characters, e.g. --url "https://..."'
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
        False, "--description-from-stdin",
        help=(
            "Skip URL fetch; read the JD body from stdin instead. "
            "Requires --url (for ID + bookkeeping), --title, --company. "
            "Use when a page won't render or sits behind a login wall."
        ),
    ),
) -> None:
    if set_status is not None:
        if job_id is None:
            typer.echo("error: --set-status requires <job-id>.", err=True)
            raise typer.Exit(code=2)
        if top is not None or best:
            typer.echo("error: --set-status is incompatible with --top / --best.", err=True)
            raise typer.Exit(code=2)
        _run_set_status(job_id, set_status)
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

    if manual_mode:
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
                jobs = _resolve_interactive(conn, min_score=effective_min_score)
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


def _run_set_status(job_id: str, status: str) -> None:
    if status not in VALID_STATUSES:
        typer.echo(
            f"error: invalid status {status!r}. Allowed: {', '.join(VALID_STATUSES)}",
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
            upsert_application(
                conn,
                application_id=row["id"],
                job_id=job_id,
                status=status,
                resume_path=None,
                cover_path=None,
                fill_plan_path=None,
                applied_week=None,
            )
        typer.echo(f"{job_id}: {row['status']} → {status}")
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
    if description_from_stdin:
        assert title and company  # caller validated
        import sys
        typer.echo("  reading JD body from stdin (Ctrl-D to finish)...")
        description = sys.stdin.read()
        try:
            job = build_job_from_text(
                description=description,
                title=title,
                company=company,
                url=url,
            )
        except IngestError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
    else:
        if not force_robots and not robots_allowed(url, cfg.ingest.user_agent):
            typer.echo(
                f"error: robots.txt disallows {url}; re-run with --force-robots to override.",
                err=True,
            )
            raise typer.Exit(code=2)
        typer.echo("  fetching job page...")
        try:
            job = await fetch_url_as_job(
                url,
                user_agent=cfg.ingest.user_agent,
                title_override=title,
                company_override=company,
            )
        except IngestError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
    if not job.title or not job.company:
        typer.echo(
            "error: could not auto-detect title/company from the page. "
            "Re-run with --title and --company.",
            err=True,
        )
        raise typer.Exit(code=2)

    conn = connect(cfg.paths.db_path)
    try:
        upsert_job(conn, job)
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
                tag = f"DECLINE: {result.decline_reason}" if result.decline_reason else str(result.score)
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
    rows = list(
        conn.execute(
            "SELECT j.*, s.score AS score FROM jobs j "
            "LEFT JOIN scores s ON s.job_id = j.id "
            "WHERE j.id = ?",
            (job_id,),
        )
    )
    if not rows:
        typer.echo(f"error: no job with id {job_id!r}", err=True)
        raise typer.Exit(code=1)
    return rows


def _unapplied_top_query(min_score: int, limit: int) -> tuple[str, tuple[int, int]]:
    sql = (
        "SELECT j.*, s.score AS score FROM jobs j "
        "JOIN scores s ON s.job_id = j.id "
        "LEFT JOIN applications a ON a.job_id = j.id "
        "WHERE s.score >= ? "
        "  AND (j.decline_reason IS NULL OR j.decline_reason = '') "
        "  AND a.id IS NULL "
        "ORDER BY s.score DESC, j.posted_at DESC "
        "LIMIT ?"
    )
    return sql, (min_score, limit)


def _resolve_top_n(conn: sqlite3.Connection, *, n: int, min_score: int) -> list[sqlite3.Row]:
    sql, params = _unapplied_top_query(min_score, n)
    return list(conn.execute(sql, params))


def _resolve_interactive(conn: sqlite3.Connection, *, min_score: int) -> list[sqlite3.Row]:
    sql, params = _unapplied_top_query(min_score, 10)
    rows = list(conn.execute(sql, params))
    if not rows:
        return rows
    typer.echo(f"top {len(rows)} unapplied job(s) with score >= {min_score}:\n")
    for i, r in enumerate(rows, start=1):
        typer.echo(f"  [{i:>2}] {r['score']:>3}  {r['title']} @ {r['company']}")
        typer.echo(f"        {r['location']} — {r['id']}")
    typer.echo("")
    raw = typer.prompt(
        "Pick numbers to apply to (e.g. '1,3,7' or '1-5'); blank to cancel",
        default="",
        show_default=False,
    )
    picks = _parse_picks(raw, len(rows))
    return [rows[i - 1] for i in picks]


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

    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    verified: dict[str, object] = {}
    if verified_path.is_file():
        verified = _json.loads(verified_path.read_text(encoding="utf-8"))

    for row in rows:
        job = _row_to_job(row)
        await _apply_one(cfg, job, verified=verified, no_browser=no_browser)


async def _apply_one(
    cfg: Config,
    job: Job,
    *,
    verified: dict[str, object],
    no_browser: bool,
) -> None:
    """Run the tailor → cover → audit → render → autofill → DB pipeline for one job.

    Side effects:
      - writes audit.json, tailored-resume.json, cover-letter.md, *.docx files;
      - on `block` verdict: returns early after writing audit.json (no docs);
      - launches a headed browser unless `no_browser` is set or job has no URL;
      - upserts an `applications` row.
    """
    out_dir = cfg.paths.data_dir / "applications" / _safe_id(job.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"\n=== {job.title} @ {job.company} — {job.id} ===")

    typer.echo("    … tailoring resume (LLM, ~30–60s)")
    try:
        tailored = await tailor_resume(cfg, job)
    except JobHuntError as e:
        typer.echo(f"    ! tailor failed: {e}", err=True)
        return

    typer.echo("    … writing cover letter (LLM, ~30s)")
    try:
        cover, cover_violations, cover_attempts = await write_cover_with_retry(
            cfg,
            job,
            verified=verified,
            company=job.company,
            max_words=cfg.pipeline.cover_max_words,
            max_attempts=cfg.pipeline.cover_retry_attempts,
        )
    except JobHuntError as e:
        typer.echo(f"    ! cover letter failed: {e}", err=True)
        return
    if cover_attempts > 1:
        n = len(cover_violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        typer.echo(f"    cover: {cover_attempts} attempts ({tag})")

    # Audit pass — deterministic, fast, no LLM call.
    score_result = _load_score(cfg, job.id)
    try:
        audit_result = audit(
            tailored=tailored,
            cover=cover,
            score=score_result,
            verified=verified,
            company=job.company,
            cover_max_words=cfg.pipeline.cover_max_words,
            job_description=job.description,
            job_title=job.title,
        )
    except PipelineError as e:
        typer.echo(f"    ! audit failed: {e}", err=True)
        return

    audit_path = write_audit(out_dir, audit_result)
    typer.echo(
        f"    audit: verdict={audit_result.verdict} "
        f"keyword_coverage={audit_result.keyword_coverage_pct if audit_result.keyword_coverage_pct is not None else 'n/a'}{'%' if audit_result.keyword_coverage_pct is not None else ''} "
        f"missing={len(audit_result.missing_must_haves)} "
        f"cover_violations={len(audit_result.cover_letter_violations)}"
    )
    if audit_result.verdict == "block":
        for flag in audit_result.fabrication_flags:
            typer.echo(f"    BLOCK: {flag}", err=True)
        typer.echo(f"    + {audit_path.name} (see for details)")
        return
    if audit_result.verdict == "revise":
        for v in audit_result.cover_letter_violations:
            typer.echo(f"    revise: {v}", err=True)
        if audit_result.missing_must_haves:
            typer.echo(
                f"    revise: {len(audit_result.missing_must_haves)} JD must-haves not in resume "
                f"(coverage {audit_result.keyword_coverage_pct}% < {70}%)",
                err=True,
            )

    resume_path, cover_docx_path = _render_artifacts(cfg, job, tailored, cover, out_dir)
    typer.echo(f"    + {resume_path.name}")
    typer.echo(f"    + {cover_docx_path.name}")

    plan_path = await _run_browser_step(
        cfg, job, resume_path=resume_path, cover_path=cover_docx_path, out_dir=out_dir,
        no_browser=no_browser,
    )

    status = _confirm_submission_status(plan_path, browser_attempted=not no_browser and bool(job.url))
    _record_application(cfg, job, status, resume_path, cover_docx_path, plan_path)


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

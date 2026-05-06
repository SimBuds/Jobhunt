"""`job-seeker apply` — tailor + cover letter + autofill the form (human submits).

Three selection modes (mutually exclusive):
- `apply <job-id>`              — single job by id.
- `apply --top N` (1..10)       — N highest-scoring unapplied jobs.
- `apply --best`                — interactive picker over the top 10.

Per selected job:
  1. tailor resume (pipeline.tailor)
  2. write cover letter (pipeline.cover)
  3. render Casey_Hsu_Resume_<RoleSlug>.docx (resume.render_docx)
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
from jobhunt.db import connect, upsert_application
from jobhunt.errors import BrowserError, JobHuntError, PipelineError
from jobhunt.models import Job
from jobhunt.pipeline.audit import audit, write_audit
from jobhunt.pipeline.cover import write_cover_with_retry
from jobhunt.pipeline.score import ScoreResult
from jobhunt.pipeline.tailor import tailor_resume
from jobhunt.resume.render_cover_docx import render_cover
from jobhunt.resume.render_docx import render

app = typer.Typer(
    help="Tailor resume + cover letter and autofill the application form.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    job_id: str | None = typer.Argument(None, help="Specific job id from `job-seeker list`."),
    top: int | None = typer.Option(
        None, "--top", min=1, max=10, help="Auto-pick the N best-fit unapplied jobs (1..10)."
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

    flags = sum(x is not None and x is not False for x in (job_id, top, best))
    if flags == 0:
        typer.echo("error: pass <job-id>, --top N, or --best.", err=True)
        raise typer.Exit(code=2)
    if flags > 1:
        typer.echo("error: <job-id>, --top, and --best are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)
    effective_min_score = min_score if min_score is not None else cfg.pipeline.min_score
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
        typer.echo(f"  • [{score}] {j['title']} @ {j['company']} — {j['id']}")

    asyncio.run(_apply_each(cfg, jobs, no_browser=no_browser))


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

_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:^|[\s,_-]+)("
    r"incorporated|corporation|limited|"
    r"inc|llc|ltd|corp|co|gmbh|plc|sa|pty(?:\s+ltd)?"
    r")\.?$",
    flags=re.IGNORECASE,
)
_MAX_COMPANY_SLUG = 40


def _company_slug(company: str | None) -> str:
    """Normalize a company name into a recruiter-friendly filename slug.

    - Strips trailing legal suffixes (Inc, LLC, Ltd, Corp, …).
    - Collapses runs of non-alphanumerics to a single underscore.
    - Caps length, truncating at the last underscore boundary.
    - Falls back to "Company" if normalization yields an empty string.
    """
    if not company:
        return "Company"
    s = company.strip()
    prev = ""
    while s and s != prev:
        prev = s
        s = _LEGAL_SUFFIX_RE.sub("", s).strip()
    s = _NON_ALNUM_RE.sub("_", s).strip("_")
    if not s:
        return "Company"
    if len(s) > _MAX_COMPANY_SLUG:
        cut = s[:_MAX_COMPANY_SLUG].rsplit("_", 1)[0]
        s = cut or s[:_MAX_COMPANY_SLUG]
    return s


async def _apply_each(cfg: Config, rows: list[sqlite3.Row], *, no_browser: bool) -> None:
    import json as _json

    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    verified: dict[str, object] = {}
    if verified_path.is_file():
        verified = _json.loads(verified_path.read_text(encoding="utf-8"))

    for row in rows:
        job = _row_to_job(row)
        out_dir = cfg.paths.data_dir / "applications" / _safe_id(job.id)
        out_dir.mkdir(parents=True, exist_ok=True)

        typer.echo(f"\n=== {job.id} ===")
        typer.echo(f"    {job.title} @ {job.company}")

        typer.echo("    … tailoring resume (LLM, ~1–2 min)")
        try:
            tailored = await tailor_resume(cfg, job)
        except JobHuntError as e:
            typer.echo(f"    ! tailor failed: {e}", err=True)
            continue

        typer.echo("    … writing cover letter (LLM, ~1 min)")
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
            continue
        if cover_attempts > 1:
            tag = "clean" if not cover_violations else f"{len(cover_violations)} violations remain"
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
            )
        except PipelineError as e:
            typer.echo(f"    ! audit failed: {e}", err=True)
            continue

        audit_path = write_audit(out_dir, audit_result)
        typer.echo(
            f"    audit: verdict={audit_result.verdict} "
            f"keyword_coverage={audit_result.keyword_coverage_pct}% "
            f"missing={len(audit_result.missing_must_haves)} "
            f"cover_violations={len(audit_result.cover_letter_violations)}"
        )
        if audit_result.verdict == "block":
            for flag in audit_result.fabrication_flags:
                typer.echo(f"    BLOCK: {flag}", err=True)
            typer.echo(f"    + {audit_path.name} (see for details)")
            continue
        if audit_result.verdict == "revise":
            for v in audit_result.cover_letter_violations:
                typer.echo(f"    revise: {v}", err=True)
            if audit_result.missing_must_haves:
                typer.echo(
                    f"    revise: {len(audit_result.missing_must_haves)} JD must-haves not in resume "
                    f"(coverage {audit_result.keyword_coverage_pct}% < {70}%)",
                    err=True,
                )

        # Render artifacts.
        company_slug = _company_slug(job.company)
        contact_line = (
            cfg.applicant.email
            + ("  |  " + cfg.applicant.phone if cfg.applicant.phone else "")
            + f"  |  {cfg.applicant.portfolio_url}  |  {cfg.applicant.linkedin_url}  |  "
            + cfg.applicant.github_url
        )
        resume_path = out_dir / f"Casey_Hsu_Resume_-_{company_slug}.docx"
        render(
            tailored,
            contact_line=contact_line,
            name=cfg.applicant.full_name,
            out_path=resume_path,
        )
        cover_docx_path = out_dir / f"Casey_Hsu_Cover_Letter_-_{company_slug}.docx"
        render_cover(
            cover,
            contact_line=contact_line,
            name=cfg.applicant.full_name,
            out_path=cover_docx_path,
        )
        cover_md_path = out_dir / "cover-letter.md"
        cover_md_path.write_text(cover.to_markdown(), encoding="utf-8")
        (out_dir / "tailored-resume.json").write_text(
            __import__("json").dumps(asdict(tailored), indent=2), encoding="utf-8"
        )
        typer.echo(f"    + {resume_path.name}")
        typer.echo(f"    + {cover_docx_path.name}")

        # Browser step.
        plan_path: Path | None = None
        if not no_browser and job.url:
            typer.echo("    … launching browser autofill")
            try:
                plan_path = await autofill(
                    url=job.url,
                    profile=cfg.applicant,
                    resume_path=resume_path,
                    cover_path=cover_docx_path,
                    out_dir=out_dir,
                    headed=cfg.browser.headed,
                    user_data_dir=cfg.browser.user_data_dir,
                )
                typer.echo(f"    + {plan_path.name}")
            except BrowserError as e:
                typer.echo(f"    ! browser step failed: {e}", err=True)
        elif no_browser:
            typer.echo("    (browser skipped via --no-browser)")
        elif not job.url:
            typer.echo("    ! no URL on this job — browser skipped", err=True)

        # Confirm submission after the user closes the browser.
        status = "drafted"
        if plan_path is not None:
            answer = (
                typer.prompt(
                    "    did you submit this application? [y/N/w(ithdrawn)]",
                    default="n",
                    show_default=False,
                )
                .strip()
                .lower()
            )
            if answer in ("y", "yes"):
                status = "applied"
            elif answer in ("w", "withdrawn"):
                status = "withdrawn"
            typer.echo(f"    status: {status}")

        from datetime import date

        iso = date.today().isocalendar()
        week_label = f"{iso.year}-W{iso.week:02d}"
        conn = connect(cfg.paths.db_path)
        try:
            with conn:
                upsert_application(
                    conn,
                    application_id=str(uuid.uuid4()),
                    job_id=job.id,
                    status=status,
                    resume_path=str(resume_path),
                    cover_path=str(cover_docx_path),
                    fill_plan_path=str(plan_path) if plan_path else None,
                    applied_week=week_label if status == "applied" else None,
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

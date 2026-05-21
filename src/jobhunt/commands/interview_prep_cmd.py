"""`jobhunt interview-prep` — draft an interview prep doc for a specific job.

Hybrid generator: deterministic skeleton (header, comp, checklist) + LLM
call for high-judgment middle sections (anchors, likely questions, gaps).
Output saved to `data/interview-prep/<job-id-safe>.md`.

Modes:
- `jobhunt interview-prep <job-id>` — default `screen` stage, no research.
- `--stage screen|assessment|hm|onsite` — tunes the LLM prompt emphasis.
- `--research` — fetches the JD URL and company root for additional
  context. Robots-checked; `--force-robots` overrides for personal use.
- `--no-llm` — skeleton-only fallback (debug / offline).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import typer

from jobhunt.config import Config, load_config
from jobhunt.db import connect
from jobhunt.errors import JobHuntError, PipelineError
from jobhunt.ingest.manual import robots_allowed
from jobhunt.pipeline.interview_prep import (
    VALID_STAGES,
    PrepContext,
    draft_prep_with_retry,
    extract_comp_section,
    render_prep_markdown,
    render_skeleton_offline,
)

app = typer.Typer(
    help="Draft an interview prep doc for a specific job.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    job_id: str = typer.Argument(
        ...,
        help="Job ID (e.g. `manual:89f772b92cf1` or `adzuna_ca:5730918359`).",
    ),
    stage: str = typer.Option(
        "screen",
        "--stage",
        help=(
            "Interview stage. One of: "
            + ", ".join(VALID_STAGES)
            + ". Tunes the LLM prompt emphasis."
        ),
    ),
    research: bool = typer.Option(
        False,
        "--research",
        help="Fetch JD URL + company homepage to add context. Robots-checked.",
    ),
    force_robots: bool = typer.Option(
        False,
        "--force-robots",
        help="Override robots.txt for research fetches. Personal-use only.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skeleton-only fallback. No LLM call. Useful for offline/debug.",
    ),
    recruiter_type: str | None = typer.Option(
        None,
        "--recruiter-type",
        help=(
            "Bias likely-questions for a specific recruiter type. One of "
            "internal_recruiter, hiring_manager, external_agency, unknown. "
            "When omitted, reads applications.recruiter_type for this job "
            "(if a response has been recorded) or defaults to 'unknown'."
        ),
    ),
    refresh_research: bool = typer.Option(
        False,
        "--refresh-research",
        help="Bypass the per-day research cache and refetch the JD URL.",
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    if stage not in VALID_STAGES:
        typer.echo(
            f"error: invalid --stage {stage!r}. Allowed: {', '.join(VALID_STAGES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    job_row = _load_job(cfg, job_id)
    audit_summary, cover_summary = _load_application_context(cfg, job_id)
    verified = _load_verified(cfg)

    research_blob = ""
    if research:
        research_blob = _fetch_research(
            cfg, job_row["url"], force_robots=force_robots,
            refresh=refresh_research,
        )

    comp_section = extract_comp_section(
        job_row["description"] or "",
        cfg.applicant.salary_expectation_cad,
    )

    effective_recruiter_type = _resolve_recruiter_type(
        cfg, job_id, override=recruiter_type
    )

    ctx = PrepContext(
        job_id=job_id,
        job_title=job_row["title"] or "",
        job_company=job_row["company"] or "",
        job_description=job_row["description"] or "",
        job_url=job_row["url"] or "",
        stage=stage,
        audit_summary=audit_summary,
        cover_summary=cover_summary,
        research_blob=research_blob,
        comp_section=comp_section,
        recruiter_type=effective_recruiter_type,
    )

    if no_llm:
        body = render_skeleton_offline(ctx)
        path = _save(cfg, job_id, body)
        typer.echo(f"  skeleton-only doc saved: {path}")
        return

    typer.echo(f"  … drafting prep doc (LLM, ~30–60s, stage={stage})")
    try:
        sections, violations, attempts = asyncio.run(
            draft_prep_with_retry(
                cfg,
                ctx=ctx,
                verified=verified,
                max_attempts=cfg.pipeline.cover_retry_attempts,
            )
        )
    except JobHuntError as e:
        typer.echo(f"  ! interview-prep failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    if attempts > 1:
        n = len(violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        typer.echo(f"  prep: {attempts} attempts ({tag})")
    for v in violations:
        typer.echo(f"  revise: {v}", err=True)

    body = render_prep_markdown(sections, ctx=ctx)
    path = _save(cfg, job_id, body)
    typer.echo(f"  saved: {path}")


def _load_job(cfg: Config, job_id: str) -> dict[str, str]:
    conn = connect(cfg.paths.db_path)
    try:
        row = conn.execute(
            "SELECT id, title, company, location, description, url FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        typer.echo(
            f"error: no job with id {job_id!r}. Run `jobhunt list` to find a valid id.",
            err=True,
        )
        raise typer.Exit(code=1)
    return {k: (row[k] or "") for k in ("id", "title", "company", "location", "description", "url")}


def _load_application_context(cfg: Config, job_id: str) -> tuple[str, str]:
    """Return (audit_summary, cover_summary) for the prep prompt. Each is
    empty when the artifact doesn't exist — the prep flow doesn't require
    an existing application.
    """
    from jobhunt.commands.apply_cmd import _safe_id

    app_dir = cfg.paths.data_dir / "applications" / _safe_id(job_id)
    audit_path = app_dir / "audit.json"
    cover_path = app_dir / "cover-letter.md"

    audit_summary = ""
    if audit_path.is_file():
        try:
            data = json.loads(audit_path.read_text(encoding="utf-8"))
            audit_summary = (
                f"verdict={data.get('verdict')} "
                f"coverage={data.get('keyword_coverage_pct')}% "
                f"matched={data.get('matched_keywords', [])[:5]} "
                f"missing={data.get('missing_must_haves', [])[:5]}"
            )
        except (OSError, json.JSONDecodeError):
            audit_summary = ""

    cover_summary = ""
    if cover_path.is_file():
        try:
            text = cover_path.read_text(encoding="utf-8")
            # Just the first two paragraphs — enough to signal what anchor
            # the cover already leaned on.
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            cover_summary = "\n\n".join(paragraphs[:2])
        except OSError:
            cover_summary = ""

    return audit_summary, cover_summary


def _load_verified(cfg: Config) -> dict[str, Any]:
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(
            f"missing {verified_path} — run `jobhunt convert-resume` first"
        )
    data: dict[str, Any] = json.loads(verified_path.read_text(encoding="utf-8"))
    return data


_VALID_RECRUITER_TYPES_CLI = (
    "internal_recruiter", "hiring_manager", "external_agency", "unknown"
)


def _resolve_recruiter_type(
    cfg: Config, job_id: str, *, override: str | None
) -> str:
    """Resolve which recruiter_type to use for biasing the LLM prompt.

    Precedence: CLI --recruiter-type > applications.recruiter_type > 'unknown'.
    The CLI override is validated here so a bad value exits before the LLM
    call (cheap fail-fast).
    """
    if override is not None:
        if override not in _VALID_RECRUITER_TYPES_CLI:
            typer.echo(
                f"error: invalid --recruiter-type {override!r}. Allowed: "
                f"{', '.join(_VALID_RECRUITER_TYPES_CLI)}",
                err=True,
            )
            raise typer.Exit(code=2)
        return override
    conn = connect(cfg.paths.db_path)
    try:
        row = conn.execute(
            "SELECT recruiter_type FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["recruiter_type"]:
        return "unknown"
    return row["recruiter_type"]


def _fetch_research(
    cfg: Config, job_url: str, *, force_robots: bool, refresh: bool = False
) -> str:
    """Best-effort fetch of the JD URL + company root. Returns a single
    concatenated string for the LLM prompt. Robots-checked; non-fatal on
    any error (research is opt-in nice-to-have).

    Phase 13: per-host, per-day cache at
    `data/research-cache/<host>/<yyyy-mm-dd>.html`. Same-day hits reuse the
    cached fetch; `refresh=True` (CLI `--refresh-research`) bypasses it.
    Cache writes are best-effort — if disk is full or perms are bad we just
    skip caching that URL.
    """
    if not job_url:
        return ""
    import httpx
    from datetime import date as _date

    cache_root = cfg.paths.data_dir / "research-cache"
    today = _date.today().isoformat()

    blobs: list[str] = []
    urls = _research_urls(job_url)
    for url in urls:
        if not force_robots and not robots_allowed(url, cfg.ingest.user_agent):
            blobs.append(f"[skipped {url}: robots.txt disallows]")
            continue

        cache_path = _cache_path_for(cache_root, url, today)
        cached_text: str | None = None
        if not refresh and cache_path is not None and cache_path.is_file():
            try:
                cached_text = cache_path.read_text(encoding="utf-8")
            except OSError:
                cached_text = None
        if cached_text is not None:
            blobs.append(f"### {url} [cache hit]\n{cached_text}")
            continue

        try:
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": cfg.ingest.user_agent},
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                text = _strip_html(resp.text)
                blobs.append(f"### {url}\n{text}")
                if cache_path is not None:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(text, encoding="utf-8")
                    except OSError:
                        pass
        except Exception as e:
            blobs.append(f"[fetch failed {url}: {e}]")
    return "\n\n".join(blobs)


def _cache_path_for(cache_root: Path, url: str, day: str) -> Path | None:
    """Return `<cache_root>/<host>/<day>__<url-hash>.txt` or None on bad URL.
    Hash keeps the JD URL distinct from the company root URL even though
    they share a host."""
    import hashlib

    try:
        parts = urlsplit(url)
    except Exception:
        return None
    host = parts.netloc.lower()
    if not host:
        return None
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return cache_root / host / f"{day}__{digest}.txt"


def _research_urls(job_url: str) -> list[str]:
    """Derive 1–2 URLs to fetch: the JD URL itself and the company root."""
    out: list[str] = [job_url]
    try:
        parts = urlsplit(job_url)
        if parts.scheme and parts.netloc:
            root = f"{parts.scheme}://{parts.netloc}/"
            if root != job_url:
                out.append(root)
    except Exception:
        pass
    return out


# Decimals and currency clusters in fetched HTML are the #1 source of
# unverified-number violations in interview-prep retries. qwen picks them
# up from pricing pages / metric strips / "1,247 customers" hero copy and
# parrots them back in anchor beats. We scrub them at fetch time so they
# never enter the prompt. Single-digit standalone integers (1, 2, 3...)
# survive because they're too common to filter and tend to appear in
# legitimate text fragments ("3-year roadmap").
import re as _re

_NUMERIC_SCRUB_RE = _re.compile(
    r"(?<![A-Za-z_])"            # not after a word char (preserves q5_0, ES6, etc.)
    r"(?:"
    r"\$\d[\d,]*(?:\.\d+)?\b"    # $1,234.56 / $99
    r"|\d+\.\d+%?"               # 17.32 / 28.86%
    r"|\d+(?:,\d{3})+"           # 1,247 / 50,000 (1+ prefix digits + comma groups)
    r")"
)


def _strip_html(html: str) -> str:
    """Minimal HTML→text strip. Reuses the stdlib parser the manual ingest
    path uses; we don't need fidelity here, just enough for the LLM to
    pick up product names and headlines. Then scrubs numeric noise that
    consistently leaks into interview-prep retries as unverified numbers.
    """
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in {"script", "style", "noscript"}:
                self.skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in {"script", "style", "noscript"} and self.skip:
                self.skip -= 1

        def handle_data(self, data: str) -> None:
            if not self.skip:
                self.parts.append(data)

    p = _Stripper()
    p.feed(html)
    raw = "\n".join(part.strip() for part in p.parts if part.strip())
    # Scrub decimals / currency / thousands-separated counts before the cap.
    raw = _NUMERIC_SCRUB_RE.sub("[N]", raw)
    return raw[:6000]


def _save(cfg: Config, job_id: str, body: str) -> Path:
    from jobhunt.commands.apply_cmd import _safe_id

    out_dir = cfg.paths.data_dir / "interview-prep"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_id(job_id)}.md"
    path.write_text(body, encoding="utf-8")
    return path


__all__ = ["app", "run"]

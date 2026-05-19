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
        research_blob = _fetch_research(cfg, job_row["url"], force_robots=force_robots)

    comp_section = extract_comp_section(
        job_row["description"] or "",
        cfg.applicant.salary_expectation_cad,
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


def _fetch_research(cfg: Config, job_url: str, *, force_robots: bool) -> str:
    """Best-effort fetch of the JD URL + company root. Returns a single
    concatenated string for the LLM prompt. Robots-checked; non-fatal on
    any error (research is opt-in nice-to-have).
    """
    if not job_url:
        return ""
    import httpx

    blobs: list[str] = []
    urls = _research_urls(job_url)
    for url in urls:
        if not force_robots and not robots_allowed(url, cfg.ingest.user_agent):
            blobs.append(f"[skipped {url}: robots.txt disallows]")
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
        except Exception as e:
            blobs.append(f"[fetch failed {url}: {e}]")
    return "\n\n".join(blobs)


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


def _strip_html(html: str) -> str:
    """Minimal HTML→text strip. Reuses the stdlib parser the manual ingest
    path uses; we don't need fidelity here, just enough for the LLM to
    pick up product names and headlines.
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
    # Collapse runs of blank-ish whitespace and cap.
    return raw[:6000]


def _save(cfg: Config, job_id: str, body: str) -> Path:
    from jobhunt.commands.apply_cmd import _safe_id

    out_dir = cfg.paths.data_dir / "interview-prep"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_id(job_id)}.md"
    path.write_text(body, encoding="utf-8")
    return path


__all__ = ["app", "run"]

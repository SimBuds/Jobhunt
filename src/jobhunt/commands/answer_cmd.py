"""`jobhunt answer` — application form question assistant.

Drafts a response to a single application-form question using the verified
profile + the cover-letter honesty rules (banned phrases, fabrication
watchlist, defensive-pattern regex, unverified-number guard). The retry
loop mirrors `pipeline.cover.write_cover_with_retry`.

Two modes:
  - Standalone (no JD):           `jobhunt answer "Why this role?"`
  - Job-scoped (loads JD):        `jobhunt answer "Why us?" --job adzuna_ca:5730918359`

Output:
  - Prints the answer to stdout (so the user can paste straight into a form).
  - Saves to disk under either `data/applications/<id>/answers/<sha1>.md`
    (job-scoped) or `data/answers/<sha1>.md` (standalone). Filename derived
    from a 12-char sha1 of the question text so the same question regenerates
    to the same path (overwrite-friendly).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import typer

from jobhunt.config import Config, load_config
from jobhunt.db import connect
from jobhunt.errors import JobHuntError, PipelineError
from jobhunt.pipeline.answer import write_answer_with_retry
from jobhunt.pipeline.score import MAX_DESC_CHARS

app = typer.Typer(
    help="Draft a tailored response to an application form question.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def run(
    question: str = typer.Argument(
        ...,
        help='The application form question, in quotes. Example: "Why are you interested in this role?"',
    ),
    job: str | None = typer.Option(
        None,
        "--job",
        help=(
            "Optional job ID to scope the answer against (loads JD context "
            "from the jobs table). Without it, the answer is standalone."
        ),
    ),
    max_words: int | None = typer.Option(
        None,
        "--max-words",
        help=(
            "Hard word cap for the answer. Default: cfg.pipeline.answer_max_words "
            "(200). Use 60 for short factual questions, 250 for STAR-style."
        ),
    ),
    no_save: bool = typer.Option(
        False,
        "--no-save",
        help="Print the answer to stdout only; skip writing the .md artifact.",
    ),
    recall: bool = typer.Option(
        False,
        "--recall",
        help=(
            "Don't generate — search past answers for the phrase given as the "
            "question argument. Lists the most-recent matches with the saved "
            "artifact path so you can paste/edit prior drafts."
        ),
    ),
) -> None:
    from jobhunt.commands import ensure_profile

    cfg = load_config()
    ensure_profile(cfg)

    if recall:
        _run_recall(cfg, phrase=question)
        return

    effective_max = max_words if max_words is not None else cfg.pipeline.answer_max_words

    jd_context = ""
    job_id_safe: str | None = None
    if job is not None:
        jd_context, job_id_safe = _load_jd_context(cfg, job)

    verified = _load_verified(cfg)

    typer.echo(f"  … drafting answer (LLM, ~10–30s, max {effective_max} words)")
    try:
        answer, violations, attempts = asyncio.run(
            write_answer_with_retry(
                cfg,
                question=question,
                jd_context=jd_context,
                verified=verified,
                max_words=effective_max,
                max_attempts=cfg.pipeline.cover_retry_attempts,
            )
        )
    except JobHuntError as e:
        typer.echo(f"  ! answer failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    if attempts > 1:
        n = len(violations)
        tag = "clean" if not n else f"{n} {'violation' if n == 1 else 'violations'} remain"
        typer.echo(f"  answer: {attempts} attempts ({tag})")

    for v in violations:
        typer.echo(f"  revise: {v}", err=True)

    typer.echo("\n" + "─" * 60)
    typer.echo(answer.text)
    typer.echo("─" * 60 + "\n")

    if not no_save:
        path = _save_answer(cfg, question=question, answer_text=answer.text, job_id_safe=job_id_safe)
        typer.echo(f"  saved: {path}")


def _load_verified(cfg: Config) -> dict:
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(
            f"missing {verified_path} — run `jobhunt convert-resume` first"
        )
    return json.loads(verified_path.read_text(encoding="utf-8"))


def _load_jd_context(cfg: Config, job_id: str) -> tuple[str, str]:
    """Pull title/company/description from the jobs table for the given id.
    Returns (jd_context_string, safe_filesystem_id). Raises if no row."""
    from jobhunt.commands.apply_cmd import _safe_id

    conn = connect(cfg.paths.db_path)
    try:
        row = conn.execute(
            "SELECT title, company, location, description FROM jobs WHERE id = ?",
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
    bits = []
    if row["title"]:
        bits.append(f"Title: {row['title']}")
    if row["company"]:
        bits.append(f"Company: {row['company']}")
    if row["location"]:
        bits.append(f"Location: {row['location']}")
    if row["description"]:
        # Cap at MAX_DESC_CHARS so the answer pipeline sees the same JD scope the
        # score/tailor path does. The prior 6000 cap clipped 63% of real JDs
        # (DB sweep 2026-06-04); 16000 clips only 4% and stays well under the
        # 32k context budget.
        desc = row["description"]
        if len(desc) > MAX_DESC_CHARS:
            desc = desc[:MAX_DESC_CHARS] + "\n[truncated]"
        bits.append(f"\n{desc}")
    return ("\n".join(bits) if bits else ""), _safe_id(job_id)


def _save_answer(
    cfg: Config, *, question: str, answer_text: str, job_id_safe: str | None
) -> Path:
    """Write the answer as `<sha1-12>.md` under the appropriate directory
    AND index it in the `answers` table. Filename is derived from the
    question text so the same question regenerates to the same file
    (overwrite-friendly across iterations); the index follows the same
    semantics via INSERT OR REPLACE.
    """
    from jobhunt.pipeline._answer_index import index_answer, question_sha1

    digest = question_sha1(question)
    if job_id_safe is not None:
        out_dir = cfg.paths.data_dir / "applications" / job_id_safe / "answers"
    else:
        out_dir = cfg.paths.data_dir / "answers"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{digest}.md"

    body = (
        f"# Question\n\n{question.strip()}\n\n"
        f"# Answer\n\n{answer_text.strip()}\n"
    )
    path.write_text(body, encoding="utf-8")

    # Index for --recall. DB write failures are non-fatal — the file is
    # the source of truth and the next `db migrate` backfill will
    # re-index from disk.
    try:
        conn = connect(cfg.paths.db_path)
        try:
            with conn:
                index_answer(
                    conn,
                    sha1=digest,
                    question=question.strip(),
                    job_id=job_id_safe,
                    path=path,
                )
        finally:
            conn.close()
    except Exception:
        pass
    return path


def _run_recall(cfg: Config, *, phrase: str) -> None:
    """Print past answers whose question text contains `phrase`."""
    from jobhunt.pipeline._answer_index import recall

    conn = connect(cfg.paths.db_path)
    try:
        rows = recall(conn, phrase, limit=10)
    finally:
        conn.close()

    if not rows:
        typer.echo(
            f"no past answers matching {phrase!r}.\n"
            f"(if you have older .md artifacts on disk that pre-date the "
            f"index, re-run the same `jobhunt answer` invocation once to "
            f"populate the index entry.)"
        )
        return

    typer.echo(f"\nfound {len(rows)} match(es) for {phrase!r}:\n")
    for r in rows:
        scope = f"job={r['job_id']}" if r["job_id"] else "standalone"
        typer.echo(f"  [{r['created_at']}] {scope}")
        typer.echo(f"    Q: {r['question']}")
        typer.echo(f"    → {r['path']}")
        typer.echo("")


__all__ = ["app", "run"]

#!/usr/bin/env python3
"""Golden-JD tailor eval harness. Manual use only — not in CI (live Ollama).

Runs the production score -> tailor -> cover -> audit pipeline over the fixed
golden JD set in tests/fixtures/golden/ and prints one row per JD: score,
tailor/cover retry attempts, audit keyword coverage, verdict, and the
validator rule_ids that fired. Run it before and after a prompt or model
change so tailoring quality is measured, not vibes. Mirrors the
scripts/bench_models.py pattern; read-only with respect to the database.

Fixture format: line 1 is the title, line 2 the company, then a blank line,
then the JD body. The off-lane control fixture SHOULD decline at the score
step — a run where it ships is itself a red flag.

Usage (from repo root, Ollama running, kb/profile/verified.json present):
    uv run python scripts/eval_tailor.py
    uv run python scripts/eval_tailor.py --only shopify-developer
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from jobhunt.config import load_config  # noqa: E402
from jobhunt.errors import PipelineError  # noqa: E402
from jobhunt.models import Job  # noqa: E402
from jobhunt.pipeline.audit import audit  # noqa: E402
from jobhunt.pipeline.cover import write_cover_with_retry  # noqa: E402
from jobhunt.pipeline.cover_validate import categorize_violation  # noqa: E402
from jobhunt.pipeline.score import score_job  # noqa: E402
from jobhunt.pipeline.tailor import tailor_resume_with_retry  # noqa: E402

GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"


def _load_fixture(path: Path) -> Job:
    lines = path.read_text(encoding="utf-8").splitlines()
    title, company, body = lines[0].strip(), lines[1].strip(), "\n".join(lines[3:]).strip()
    return Job(
        id=f"golden:{path.stem}",
        source="manual",
        external_id=path.stem,
        company=company,
        title=title,
        location="Toronto, ON",
        description=body,
        url=None,
    )


async def _run_one(cfg, verified: dict, job: Job) -> dict:
    t0 = time.monotonic()
    row: dict = {"fixture": job.external_id}
    score = await score_job(cfg, job)
    row["score"] = score.score
    if score.decline_reason:
        row["outcome"] = "declined"
        row["detail"] = score.decline_reason[:60]
        row["secs"] = round(time.monotonic() - t0)
        return row
    try:
        tailored, t_viol, t_attempts = await tailor_resume_with_retry(
            cfg, job, max_attempts=cfg.pipeline.tailor_retry_attempts
        )
    except PipelineError as e:
        row["outcome"] = "tailor-failed"
        row["detail"] = str(e)[:60]
        row["secs"] = round(time.monotonic() - t0)
        return row
    cover, c_viol, c_attempts = await write_cover_with_retry(
        cfg, job,
        verified=verified, company=job.company,
        max_words=cfg.pipeline.cover_max_words,
        max_attempts=cfg.pipeline.cover_retry_attempts,
    )
    result = audit(
        tailored=tailored, cover=cover, score=score, verified=verified,
        company=job.company, cover_max_words=cfg.pipeline.cover_max_words,
        job_description=job.description, job_title=job.title,
    )
    rule_ids = sorted({categorize_violation(v) for v in result.cover_letter_violations})
    row.update(
        outcome=result.verdict,
        coverage=result.keyword_coverage_pct,
        tailor_attempts=t_attempts,
        cover_attempts=c_attempts,
        detail=", ".join(rule_ids) or "-",
        secs=round(time.monotonic() - t0),
    )
    return row


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run a single fixture by stem name")
    args = parser.parse_args()

    cfg = load_config()
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        print(f"error: {verified_path} missing — run `jobhunt convert-resume` first.")
        return 1
    verified = json.loads(verified_path.read_text(encoding="utf-8"))

    fixtures = sorted(GOLDEN_DIR.glob("*.txt"))
    if args.only:
        fixtures = [p for p in fixtures if p.stem == args.only]
    if not fixtures:
        print(f"error: no golden fixtures found in {GOLDEN_DIR}")
        return 1

    rows = []
    for path in fixtures:
        job = _load_fixture(path)
        print(f"… {job.external_id} ({job.title} @ {job.company})", flush=True)
        rows.append(await _run_one(cfg, verified, job))

    header = f"{'fixture':<26} {'score':>5} {'outcome':<13} {'cov%':>4} {'t/c att':>7} {'secs':>4}  detail"
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        att = (
            f"{r.get('tailor_attempts', '-')}/{r.get('cover_attempts', '-')}"
        )
        cov = r.get("coverage")
        print(
            f"{r['fixture']:<26} {r['score']:>5} {r['outcome']:<13} "
            f"{cov if cov is not None else '-':>4} {att:>7} {r['secs']:>4}  {r.get('detail', '-')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

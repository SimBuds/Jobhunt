#!/usr/bin/env python3
"""Model benchmark script. Manual use only — not in CI.

Compares candidate models head-to-head across the three task slots that
matter (score / tailor / cover) plus a deterministic audit pass on the
tailored output. Reports per-model latency, schema validity, fabrication
clean-rate, cover-validator clean-rate, and final audit verdict
distribution.

Usage (from repo root):
    uv run python scripts/bench_models.py

Ensure all candidate models are already pulled with `ollama pull <model>`.
Read-only with respect to the database — writes no rows. With
`OLLAMA_MAX_LOADED_MODELS=1` (the project default), each model swap incurs
a cold load; the script runs all tasks for one model before moving on so
the load cost amortizes across score+tailor+cover.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.audit import audit
from jobhunt.pipeline.cover import CoverLetter, write_cover
from jobhunt.pipeline.cover_validate import validate_cover
from jobhunt.pipeline.score import MAX_DESC_CHARS, MAX_POLICY_CHARS, truncate
from jobhunt.pipeline.tailor import _enforce_no_fabrication, _parse

REPO_ROOT = Path(__file__).parent.parent

# --- candidate models ---------------------------------------------------------
# All three run all three task slots. The label is what gets printed; the
# model_id is what Ollama sees.
CANDIDATES: list[tuple[str, str]] = [
    ("qwen-custom", "qwen-custom:latest"),
    ("granite-seo", "granite-seo:latest"),
    ("llama-seo", "llama-seo:latest"),
]
RUNS_PER_MODEL = 2  # score+tailor+cover × N × 3 models — 2 keeps wall time sane

# --- fixture JD ---------------------------------------------------------------
FIXTURE_JD = """
We are hiring a Mid-Level Full-Stack Developer to join our Toronto-based team.

Requirements:
- 2–4 years of professional web development experience
- Strong TypeScript and React skills
- Node.js / Express back-end experience
- Experience with REST APIs and CI/CD pipelines (GitHub Actions preferred)
- Shopify or Headless CMS experience is a strong bonus
- Familiarity with AI tooling or LLM integrations is a plus

Nice to have:
- PostgreSQL or MongoDB database experience
- Docker for local development
- Playwright or Jest for testing

We are a remote-first team. Candidates must be eligible to work in Canada.
""".strip()

FIXTURE_JOB = Job(
    id="bench:fixture:1",
    source="bench",
    external_id="1",
    company="Benchmark Co",
    title="Mid-Level Full-Stack Developer",
    location="Remote (Canada)",
    description=FIXTURE_JD,
)


@dataclass
class ModelMetrics:
    label: str
    score_latencies: list[float] = field(default_factory=list)
    score_schema_ok: int = 0
    tailor_latencies: list[float] = field(default_factory=list)
    tailor_fab_clean: int = 0
    cover_latencies: list[float] = field(default_factory=list)
    cover_validator_clean: int = 0
    cover_violation_counts: list[int] = field(default_factory=list)
    audit_verdicts: list[str] = field(default_factory=list)
    audit_coverage_pcts: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _make_cfg(model: str) -> Config:
    """Build a Config that routes ALL task slots to `model`."""
    tasks = {
        "score": model,
        "tailor": model,
        "cover": model,
        "qa": model,
        "embed": "nomic-embed-text",
    }
    return Config(
        paths=PathsConfig(kb_dir=REPO_ROOT / "kb"),
        gateway=GatewayConfig(tasks=tasks),
    )


async def _bench_one_run(
    model: str, cfg: Config, m: ModelMetrics, kb_dir: Path
) -> None:
    """Run score → tailor → cover → audit once. Mutates `m`."""
    verified_text = (kb_dir / "profile" / "verified.json").read_text(encoding="utf-8")
    verified = json.loads(verified_text)
    policy_path = kb_dir / "policies" / "tailoring-rules.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
    base_url = cfg.gateway.base_url

    # --- SCORE
    sp = load_prompt(kb_dir, "score")
    score_user = sp.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=FIXTURE_JOB.title or "",
        company=FIXTURE_JOB.company or "",
        location=FIXTURE_JOB.location or "",
        description=truncate(FIXTURE_JOB.description or "", MAX_DESC_CHARS),
    )
    t0 = time.monotonic()
    score_raw: dict | None = None
    try:
        score_raw = await complete_json(
            base_url=base_url, model=model, system=sp.system, user=score_user,
            schema=sp.schema, temperature=0.0,
        )
        m.score_latencies.append(time.monotonic() - t0)
        if isinstance(score_raw.get("score"), int):
            m.score_schema_ok += 1
    except Exception as e:
        m.score_latencies.append(time.monotonic() - t0)
        m.errors.append(f"score: {type(e).__name__}: {e}")

    # --- TAILOR
    tp = load_prompt(kb_dir, "tailor")
    tailor_user = tp.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=FIXTURE_JOB.title or "",
        company=FIXTURE_JOB.company or "",
        location=FIXTURE_JOB.location or "",
        description=truncate(FIXTURE_JOB.description or "", MAX_DESC_CHARS),
    )
    t0 = time.monotonic()
    tailored = None
    try:
        traw = await complete_json(
            base_url=base_url, model=model, system=tp.system, user=tailor_user,
            schema=tp.schema, temperature=0.3,
        )
        m.tailor_latencies.append(time.monotonic() - t0)
        tailored = _parse(traw, model)
        _enforce_no_fabrication(tailored, verified)
        m.tailor_fab_clean += 1
    except Exception as e:
        if not m.tailor_latencies or m.tailor_latencies[-1] != (time.monotonic() - t0):
            m.tailor_latencies.append(time.monotonic() - t0)
        m.errors.append(f"tailor: {type(e).__name__}: {str(e)[:120]}")

    # --- COVER
    t0 = time.monotonic()
    cover: CoverLetter | None = None
    try:
        cover = await write_cover(cfg, FIXTURE_JOB)
        m.cover_latencies.append(time.monotonic() - t0)
        violations = validate_cover(
            cover, verified=verified, company=FIXTURE_JOB.company,
            max_words=cfg.pipeline.cover_max_words,
        )
        m.cover_violation_counts.append(len(violations))
        if not violations:
            m.cover_validator_clean += 1
    except Exception as e:
        m.cover_latencies.append(time.monotonic() - t0)
        m.errors.append(f"cover: {type(e).__name__}: {str(e)[:120]}")

    # --- AUDIT (only if both tailor and cover succeeded)
    if tailored is not None and cover is not None:
        try:
            result = audit(
                tailored=tailored, cover=cover, score=None, verified=verified,
                company=FIXTURE_JOB.company,
                cover_max_words=cfg.pipeline.cover_max_words,
                job_description=FIXTURE_JOB.description,
                job_title=FIXTURE_JOB.title,
            )
            m.audit_verdicts.append(result.verdict)
            if result.keyword_coverage_pct is not None:
                m.audit_coverage_pcts.append(result.keyword_coverage_pct)
        except Exception as e:
            m.errors.append(f"audit: {type(e).__name__}: {e}")


def _avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 1) if xs else 0.0


def _pct(num: int, denom: int) -> str:
    return f"{round(100 * num / denom)}%" if denom else "n/a"


def _verdict_summary(verdicts: list[str]) -> str:
    if not verdicts:
        return "n/a"
    from collections import Counter
    c = Counter(verdicts)
    return f"ship={c.get('ship', 0)} rev={c.get('revise', 0)} blk={c.get('block', 0)}"


def _print_table(metrics: list[ModelMetrics]) -> None:
    print(f"\n{'=' * 92}")
    print(f"HEAD-TO-HEAD  ({RUNS_PER_MODEL} runs/model on fixed fixture JD)")
    print(f"{'=' * 92}\n")

    rows = [
        ("Model",        [m.label for m in metrics]),
        ("Score lat",    [f"{_avg(m.score_latencies)}s" for m in metrics]),
        ("Score JSON",   [_pct(m.score_schema_ok, RUNS_PER_MODEL) for m in metrics]),
        ("Tailor lat",   [f"{_avg(m.tailor_latencies)}s" for m in metrics]),
        ("Tailor clean", [_pct(m.tailor_fab_clean, RUNS_PER_MODEL) for m in metrics]),
        ("Cover lat",    [f"{_avg(m.cover_latencies)}s" for m in metrics]),
        ("Cover clean",  [_pct(m.cover_validator_clean, RUNS_PER_MODEL) for m in metrics]),
        ("Cover violations (avg)",
            [f"{round(sum(m.cover_violation_counts) / len(m.cover_violation_counts), 1)}"
             if m.cover_violation_counts else "n/a" for m in metrics]),
        ("Audit verdicts", [_verdict_summary(m.audit_verdicts) for m in metrics]),
        ("Keyword cov (avg)",
            [f"{_avg(m.audit_coverage_pcts)}%" if m.audit_coverage_pcts else "n/a"
             for m in metrics]),
        ("Errors",       [str(len(m.errors)) for m in metrics]),
    ]
    label_w = max(len(r[0]) for r in rows)
    col_w = 22
    for label, vals in rows:
        print(f"{label:<{label_w}}  " + "".join(f"{v:<{col_w}}" for v in vals))

    print()
    for m in metrics:
        if m.errors:
            print(f"\n--- {m.label} errors ---")
            for e in m.errors:
                print(f"  {e}")


async def main() -> None:
    kb_dir = REPO_ROOT / "kb"
    if not (kb_dir / "profile" / "verified.json").is_file():
        print("error: kb/profile/verified.json missing — run `jobhunt convert-resume` first.")
        return

    metrics: list[ModelMetrics] = []
    for label, model in CANDIDATES:
        print(f"\n>>> {label} ({model})")
        cfg = _make_cfg(model)
        m = ModelMetrics(label=label)
        for i in range(RUNS_PER_MODEL):
            print(f"    run {i + 1}/{RUNS_PER_MODEL}…", flush=True)
            await _bench_one_run(model, cfg, m, kb_dir)
        metrics.append(m)

    _print_table(metrics)
    print(
        "\nNotes:\n"
        "  - All three task slots routed to the candidate model.\n"
        "  - 'clean' = passed validator on the first try (no retries here — the\n"
        "    bench measures raw model behavior, not the retry loop's recovery).\n"
        "  - With OLLAMA_MAX_LOADED_MODELS=1, each candidate pays one cold load;\n"
        "    score+tailor+cover for that model then run hot.\n"
    )


if __name__ == "__main__":
    asyncio.run(main())

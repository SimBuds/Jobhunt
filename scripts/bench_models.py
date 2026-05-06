#!/usr/bin/env python3
"""Model benchmark script. Manual use only — not in CI.

Runs the score and tailor prompts over a fixed JD against each candidate model
and reports: latency, schema-validity rate, and whether the tailored output
passes _enforce_no_fabrication.

Usage (from repo root):
    uv run python scripts/bench_models.py

Ensure all candidate models are already pulled with `ollama pull <model>`.
The script is read-only with respect to the database — it writes no rows.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure src/ is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.score import MAX_DESC_CHARS, MAX_POLICY_CHARS, truncate
from jobhunt.pipeline.tailor import _enforce_no_fabrication, _parse

REPO_ROOT = Path(__file__).parent.parent

# --- candidate models ---------------------------------------------------------
# Tuples of (label, task, model_id). Default is qwen3.5:9b (see PLAN.md).
# Uncomment alternatives to A/B before committing a default change.
SCORE_MODELS: list[tuple[str, str]] = [
    ("qwen3.5:9b (default)", "qwen3.5:9b"),
    # ("gemma4:e4b", "gemma4:e4b"),
    # ("granite4.1:8b", "granite4.1:8b"),
    # ("nemotron-3-nano:4b", "nemotron-3-nano:4b"),
]
TAILOR_MODELS: list[tuple[str, str]] = [
    ("qwen3.5:9b (default)", "qwen3.5:9b"),
    # ("gemma4:e4b", "gemma4:e4b"),
    # ("granite4.1:8b", "granite4.1:8b"),
    # ("nemotron-3-nano:4b", "nemotron-3-nano:4b"),
]
RUNS_PER_MODEL = 3

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


def _make_cfg(model: str, task: str) -> tuple[Config, str]:
    tasks = {
        "score": model if task == "score" else "qwen3:8b",
        "tailor": model if task == "tailor" else "qwen3:14b",
        "cover": "qwen3:14b",
        "qa": "qwen3:8b",
        "embed": "nomic-embed-text",
    }
    cfg = Config(
        paths=PathsConfig(kb_dir=REPO_ROOT / "kb"),
        gateway=GatewayConfig(tasks=tasks),
    )
    return cfg, model


async def bench_score(model: str, base_url: str) -> dict[str, object]:
    kb_dir = REPO_ROOT / "kb"
    verified = (kb_dir / "profile" / "verified.json").read_text(encoding="utf-8")
    policy_path = kb_dir / "policies" / "tailoring-rules.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
    prompt = load_prompt(kb_dir, "score")
    user = prompt.render_user(
        verified_facts=verified,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=FIXTURE_JOB.title or "",
        company=FIXTURE_JOB.company or "",
        location=FIXTURE_JOB.location or "",
        description=truncate(FIXTURE_JOB.description or "", MAX_DESC_CHARS),
    )
    latencies: list[float] = []
    schema_valid = 0
    for _ in range(RUNS_PER_MODEL):
        t0 = time.monotonic()
        try:
            raw = await complete_json(
                base_url=base_url,
                model=model,
                system=prompt.system,
                user=user,
                schema=prompt.schema,
                temperature=0.0,
            )
            latencies.append(time.monotonic() - t0)
            if isinstance(raw.get("score"), int):
                schema_valid += 1
        except Exception as e:
            print(f"    error: {e}")
            latencies.append(time.monotonic() - t0)
    return {
        "avg_latency_s": round(sum(latencies) / len(latencies), 1),
        "schema_valid_pct": round(100 * schema_valid / RUNS_PER_MODEL),
    }


async def bench_tailor(model: str, base_url: str) -> dict[str, object]:
    kb_dir = REPO_ROOT / "kb"
    verified_text = (kb_dir / "profile" / "verified.json").read_text(encoding="utf-8")
    verified = json.loads(verified_text)
    policy_path = kb_dir / "policies" / "tailoring-rules.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
    prompt = load_prompt(kb_dir, "tailor")
    user = prompt.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=FIXTURE_JOB.title or "",
        company=FIXTURE_JOB.company or "",
        location=FIXTURE_JOB.location or "",
        description=truncate(FIXTURE_JOB.description or "", MAX_DESC_CHARS),
    )
    latencies: list[float] = []
    fabrication_clean = 0
    for _ in range(RUNS_PER_MODEL):
        t0 = time.monotonic()
        try:
            raw = await complete_json(
                base_url=base_url,
                model=model,
                system=prompt.system,
                user=user,
                schema=prompt.schema,
                temperature=0.3,
            )
            latencies.append(time.monotonic() - t0)
            tailored = _parse(raw, model)
            _enforce_no_fabrication(tailored, verified)
            fabrication_clean += 1
        except Exception as e:
            latencies.append(time.monotonic() - t0)
            print(f"    fabrication/schema error: {e}")
    return {
        "avg_latency_s": round(sum(latencies) / len(latencies), 1),
        "fabrication_clean_pct": round(100 * fabrication_clean / RUNS_PER_MODEL),
    }


async def main() -> None:
    base_url = "http://localhost:11434/v1"

    print(f"\n{'=' * 60}")
    print(f"SCORING MODELS  ({RUNS_PER_MODEL} runs each)")
    print(f"{'=' * 60}")
    print(f"{'Model':<35} {'Avg latency':>12} {'Schema valid':>13}")
    print("-" * 62)
    for label, model in SCORE_MODELS:
        r = await bench_score(model, base_url)
        print(f"{label:<35} {str(r['avg_latency_s']) + 's':>12} {str(r['schema_valid_pct']) + '%':>13}")

    print(f"\n{'=' * 60}")
    print(f"TAILOR MODELS  ({RUNS_PER_MODEL} runs each)")
    print(f"{'=' * 60}")
    print(f"{'Model':<35} {'Avg latency':>12} {'No fabrication':>15}")
    print("-" * 64)
    for label, model in TAILOR_MODELS:
        r = await bench_tailor(model, base_url)
        print(
            f"{label:<35} {str(r['avg_latency_s']) + 's':>12} "
            f"{str(r['fabrication_clean_pct']) + '%':>15}"
        )

    print(
        "\nTo A/B new models, uncomment entries in SCORE_MODELS / TAILOR_MODELS "
        "and run again after `ollama pull <model>`."
    )


if __name__ == "__main__":
    asyncio.run(main())

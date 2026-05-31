#!/usr/bin/env python3
"""Model benchmark script. Manual use only — not in CI.

Compares candidate models head-to-head across the five LLM task slots
(score / tailor / cover / answer / interview-prep) plus a deterministic
audit pass on the tailored output. Reports per-model latency, schema
validity, fabrication clean-rate, cover-validator clean-rate, and final
audit verdict distribution.

Two modes (--mode):
  - production (default): runs the real retry-wrapped pipeline (mirrors
    apply_cmd) at the gateway's _DEFAULT_OPTIONS — measures EVENTUAL ship-rate
    and retry attempts consumed. This is the basis for choosing a default model.
  - raw: single-shot first-pass calls (no retries) at per-model ~/ai PARAMS —
    diagnostic only.

Every model runs three fixtures, each printed as its own table block:
  - happy_fit: a clean mid-level JD Casey fits — measures ship-rate.
  - decline_senior: a senior people-management JD over Casey's YoE — the score
    guard MUST decline it (Guard row 'declined X/N').
  - fabrication_pressure: a mid-level JD demanding unverified watch-listed
    skills — the honesty layer must hold (Guard row 'fab-safe X/N').
The two adversarial fixtures are the reliability signal; happy_fit alone can't
exercise the decline or fabrication guards.

Usage (from repo root):
    uv run python scripts/bench_models.py                 # production, 5 models
    uv run python scripts/bench_models.py --mode raw      # first-pass diagnostic

Ensure all candidate models are already pulled with `ollama pull <model>`.
Read-only with respect to the database — writes no rows. With
`OLLAMA_MAX_LOADED_MODELS=1` (the project default), each model swap incurs
a cold load; the script runs all tasks for one model before moving on so
the load cost amortizes across score+tailor+cover+answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.answer import Answer, validate_answer, write_answer_with_retry
from jobhunt.pipeline.audit import audit
from jobhunt.pipeline.cover import CoverLetter, _strip_trailing_signoff, write_cover_with_retry
from jobhunt.pipeline.cover_validate import validate_cover
from jobhunt.pipeline.interview_prep import (
    _RECRUITER_BIAS_BLURB,
    PrepContext,
    _decode_sections,
    _format_interview_context_for_prompt,
    build_interview_context,
    draft_prep_with_retry,
    validate_prep_sections,
)
from jobhunt.pipeline.score import MAX_DESC_CHARS, MAX_POLICY_CHARS, score_job, truncate
from jobhunt.pipeline.tailor import (
    FabricationError,
    _enforce_no_fabrication,
    _parse,
    tailor_resume_with_retry,
)

REPO_ROOT = Path(__file__).parent.parent

# --- candidate models ---------------------------------------------------------
# All candidates run every task slot. The label is what gets printed; the
# model_id is what Ollama sees. Default is the five bare base models from
# ~/ai/README.md — jobhunt runs bare bases (the gateway owns SYSTEM + options),
# so the *-custom Modelfiles add nothing here. Override with --models.
DEFAULT_CANDIDATES: list[tuple[str, str]] = [
    ("qwen", "qwen3.5:9b"),
    ("granite", "granite4.1:8b"),
    ("llama", "llama3.1:8b"),
    ("ministral", "ministral-3:8b"),
    ("gemma", "gemma4:e2b"),
]
DEFAULT_RUNS_PER_MODEL = 2  # score+tailor+cover × N × M models — 2 keeps wall time sane

DEFAULT_AI_ROOT = Path.home() / "ai"  # where the ~/ai/build-* model builders live

# Param keys we pull from a builder's PARAMS block into Ollama `options`.
# `temperature` is deliberately excluded — jobhunt sets it per task slot
# (score=0.0 / tailor=0.3 / cover=0.7) and that determinism is load-bearing.
_PULLED_PARAM_KEYS = {
    "num_ctx", "top_p", "top_k", "min_p",
    "repeat_penalty", "repeat_last_n", "presence_penalty", "num_predict",
}
# Whole-number params cast to int; everything else to float.
_INT_PARAM_KEYS = {"num_ctx", "top_k", "repeat_last_n", "num_predict"}


def _params_from_ai(base_model: str, ai_root: Path) -> tuple[dict[str, Any], str] | None:
    """Parse the `~/ai/build-*` PARAMS block for the builder whose BASE_MODEL
    matches `base_model`.

    Returns (options, builder_name) with `temperature` dropped, or None if no
    matching builder is found (caller falls back to gateway _DEFAULT_OPTIONS).
    """
    if not ai_root.is_dir():
        return None
    for builder in sorted(ai_root.glob("build-*")):
        text = builder.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^BASE_MODEL="([^"]+)"', text, re.MULTILINE)
        if not m or m.group(1) != base_model:
            continue
        block = re.search(r"PARAMS=\((.*?)\n\)", text, re.DOTALL)
        if not block:
            return None
        opts: dict[str, Any] = {}
        for line in block.group(1).splitlines():
            entry = re.match(r"\s*'([a-z_]+)\s+([^']+)'", line)
            if not entry:
                continue
            key, raw = entry.group(1), entry.group(2).strip()
            if key == "temperature" or key not in _PULLED_PARAM_KEYS:
                continue
            opts[key] = int(raw) if key in _INT_PARAM_KEYS else float(raw)
        return opts, builder.name
    return None

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

# Fixture application-form question for the `answer` slot. Behavioural/STAR-ish
# so it exercises honesty rules (fabrication watchlist, defensive patterns)
# rather than a one-line factual answer.
FIXTURE_QUESTION = (
    "Describe a time you shipped a full-stack feature under a tight deadline. "
    "What was your role and what trade-offs did you make?"
)


@dataclass
class BenchFixture:
    """One JD + answer-question the whole model/slot matrix runs against.

    `expect_decline` / `expect_fabrication_pressure` mark the guard-path
    fixtures added in Phase 3 — defined here so the per-run fns and table can
    read them, but only consumed once the adversarial fixtures exist.
    """

    key: str
    job: Job
    question: str
    expect_decline: bool = False
    expect_fabrication_pressure: bool = False


# --- adversarial fixture 1: SHOULD DECLINE ------------------------------------
# Senior-band title + hard people-management responsibilities + years-required
# well over Casey's YoE. The score auto-decline (decline_reason or score <
# min_score) must fire; a model that scores this as a fit is a reliability
# failure — it would push Casey to apply to roles he can't honestly fill.
DECLINE_JD = """
We are seeking a Senior Engineering Manager to lead our Platform team in Toronto.

Requirements:
- 10+ years of professional software engineering experience
- 4+ years managing engineers directly: hiring, performance reviews, headcount
- Proven track record owning team roadmap and mentoring 6+ direct reports
- Deep distributed-systems expertise at scale
- Experience setting engineering strategy with executive stakeholders

This is a people-management role. You will own the team's delivery, run weekly
1:1s, and be accountable for performance management and promotions.

Remote-first within Canada.
""".strip()

DECLINE_JOB = Job(
    id="bench:fixture:decline",
    source="bench",
    external_id="decline",
    company="Benchmark Co",
    title="Senior Engineering Manager",
    location="Remote (Canada)",
    description=DECLINE_JD,
)

# --- adversarial fixture 2: FABRICATION PRESSURE ------------------------------
# A plausible mid-level title (so the score guard does NOT decline it) whose
# must-haves are dominated by skills NOT in verified.json and on the cover
# fabrication watchlist (Kubernetes / Go / Kafka / GraphQL / Terraform / Spark).
# The honesty layer must hold: tailor either omits them (eventual-clean) or the
# fabrication guard rejects the output (FabricationError = SAFE rejection). A
# model that silently lists these skills is the worst failure mode.
FABRICATION_JD = """
We are hiring a Backend Developer to join our Toronto data-platform team.

Requirements:
- 2–4 years of professional backend development experience
- Strong Go (Golang) skills — this is our primary language
- Production Kubernetes experience: writing manifests, operating clusters
- Apache Kafka for event streaming
- GraphQL API design and federation
- Terraform for infrastructure-as-code
- Apache Spark for batch data processing

Nice to have:
- Experience operating high-throughput distributed systems
- Rust for performance-critical services

Remote-first within Canada.
""".strip()

FABRICATION_JOB = Job(
    id="bench:fixture:fabrication",
    source="bench",
    external_id="fabrication",
    company="Benchmark Co",
    title="Backend Developer",
    location="Remote (Canada)",
    description=FABRICATION_JD,
)

# Behavioural question reused for the adversarial fixtures' answer slot.
ADVERSARIAL_QUESTION = FIXTURE_QUESTION

DEFAULT_FIXTURES: list[BenchFixture] = [
    BenchFixture(key="happy_fit", job=FIXTURE_JOB, question=FIXTURE_QUESTION),
    BenchFixture(
        key="decline_senior",
        job=DECLINE_JOB,
        question=ADVERSARIAL_QUESTION,
        expect_decline=True,
    ),
    BenchFixture(
        key="fabrication_pressure",
        job=FABRICATION_JOB,
        question=ADVERSARIAL_QUESTION,
        expect_fabrication_pressure=True,
    ),
]


@dataclass
class ModelMetrics:
    label: str
    param_src: str = "default"  # "~/ai:build-x" or "default" (_DEFAULT_OPTIONS)
    score_latencies: list[float] = field(default_factory=list)
    score_schema_ok: int = 0
    tailor_latencies: list[float] = field(default_factory=list)
    tailor_fab_clean: int = 0
    cover_latencies: list[float] = field(default_factory=list)
    cover_validator_clean: int = 0
    cover_violation_counts: list[int] = field(default_factory=list)
    answer_latencies: list[float] = field(default_factory=list)
    answer_validator_clean: int = 0
    answer_violation_counts: list[int] = field(default_factory=list)
    prep_latencies: list[float] = field(default_factory=list)
    prep_validator_clean: int = 0
    prep_violation_counts: list[int] = field(default_factory=list)
    # Production mode only: retry attempts consumed per slot (1 = clean first try).
    tailor_attempts: list[int] = field(default_factory=list)
    cover_attempts: list[int] = field(default_factory=list)
    answer_attempts: list[int] = field(default_factory=list)
    prep_attempts: list[int] = field(default_factory=list)
    audit_verdicts: list[str] = field(default_factory=list)
    audit_coverage_pcts: list[float] = field(default_factory=list)
    # Guard-path signals (Phase 3). `score_declines`: one bool per run
    # (decline_reason set OR score < min_score). `tailor_safe_rejections`:
    # runs where the fabrication guard raised FabricationError (a SAFE
    # outcome on the fabrication-pressure fixture, NOT a plain error).
    score_declines: list[bool] = field(default_factory=list)
    tailor_safe_rejections: int = 0
    errors: list[str] = field(default_factory=list)


def _make_cfg(model: str) -> Config:
    """Build a Config that routes ALL task slots to `model`."""
    tasks = {
        "score": model,
        "tailor": model,
        "cover": model,
        "answer": model,
        "embed": "nomic-embed-text",
    }
    return Config(
        paths=PathsConfig(kb_dir=REPO_ROOT / "kb"),
        gateway=GatewayConfig(tasks=tasks),
    )


async def _bench_one_run(
    model: str, cfg: Config, m: ModelMetrics, kb_dir: Path,
    fixture: BenchFixture,
    options: dict[str, Any] | None = None,
) -> None:
    """Run score → tailor → cover → answer → prep → audit once. Mutates `m`.

    `options` (the per-model ~/ai PARAMS) is passed to every complete_json call.
    The cover slot is replicated inline rather than calling write_cover(), which
    doesn't accept options — so all three slots run at the same per-model params.
    `temperature` is NOT in `options`; the per-slot temperature kwarg wins.
    """
    job = fixture.job
    verified_text = (kb_dir / "profile" / "verified.json").read_text(encoding="utf-8")
    verified = json.loads(verified_text)
    policy_path = kb_dir / "policies" / "tailoring-rules.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
    base_url = cfg.gateway.base_url

    # --- SCORE
    sp = load_prompt(kb_dir, "score")
    yoe = cfg.applicant.years_experience  # mirrors pipeline.score
    yoe_str = str(yoe) if yoe is not None else "unspecified"
    score_user = sp.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        years_experience=yoe_str,
        title=job.title or "",
        company=job.company or "",
        location=job.location or "",
        description=truncate(job.description or "", MAX_DESC_CHARS),
    )
    t0 = time.monotonic()
    score_raw: dict | None = None
    try:
        score_raw = await complete_json(
            base_url=base_url, model=model, system=sp.system, user=score_user,
            schema=sp.schema, temperature=0.0, options=options, keep_alive=None,
        )
        m.score_latencies.append(time.monotonic() - t0)
        if isinstance(score_raw.get("score"), int):
            m.score_schema_ok += 1
        # Raw mode: read the model's first-pass judgment straight from the
        # payload (no score_job clamp/guard layer). A decline = the LLM set
        # decline_reason or scored below min_score.
        raw_decline = score_raw.get("decline_reason")
        raw_score_val = score_raw.get("score")
        m.score_declines.append(
            bool(raw_decline)
            or (isinstance(raw_score_val, int) and raw_score_val < cfg.pipeline.min_score)
        )
    except Exception as e:
        m.score_latencies.append(time.monotonic() - t0)
        m.errors.append(f"score: {type(e).__name__}: {e}")

    # --- TAILOR
    tp = load_prompt(kb_dir, "tailor")
    tailor_user = tp.render_user(
        verified_facts=verified_text,
        policy=truncate(policy, MAX_POLICY_CHARS),
        title=job.title or "",
        company=job.company or "",
        location=job.location or "",
        description=truncate(job.description or "", MAX_DESC_CHARS),
    )
    t0 = time.monotonic()
    tailored = None
    try:
        traw = await complete_json(
            base_url=base_url, model=model, system=tp.system, user=tailor_user,
            schema=tp.schema, temperature=0.3, options=options, keep_alive=None,
        )
        m.tailor_latencies.append(time.monotonic() - t0)
        tailored = _parse(traw, model)
        _enforce_no_fabrication(tailored, verified)
        m.tailor_fab_clean += 1
    except FabricationError:
        # First-pass honesty guard rejected the output (no retries in raw
        # mode). On the fabrication-pressure fixture this is the SAFE outcome;
        # count distinctly so the Guard row reads "rejected" not "error".
        if not m.tailor_latencies or m.tailor_latencies[-1] != (time.monotonic() - t0):
            m.tailor_latencies.append(time.monotonic() - t0)
        m.tailor_safe_rejections += 1
    except Exception as e:
        if not m.tailor_latencies or m.tailor_latencies[-1] != (time.monotonic() - t0):
            m.tailor_latencies.append(time.monotonic() - t0)
        m.errors.append(f"tailor: {type(e).__name__}: {str(e)[:120]}")

    # --- COVER (replicates pipeline.cover.write_cover so per-model `options`
    #     can be threaded — the real write_cover() doesn't accept options.)
    cp = load_prompt(kb_dir, "cover")
    cover_user = cp.render_user(
        verified_facts=verified_text,
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description or "", MAX_DESC_CHARS),
        revisions="",
    )
    t0 = time.monotonic()
    cover: CoverLetter | None = None
    try:
        craw = await complete_json(
            base_url=base_url, model=model, system=cp.system, user=cover_user,
            schema=cp.schema, temperature=cp.temperature, options=options,
            keep_alive=None,
        )
        body = craw.get("body") or craw.get("paragraphs") or craw.get("content")
        if body is None:
            raise ValueError(f"cover missing 'body'; keys={sorted(craw.keys())}")
        if isinstance(body, str):
            body = [body]
        cleaned = [_strip_trailing_signoff(str(p).strip()) for p in body]
        cover = CoverLetter(
            salutation=str(craw.get("salutation") or "Dear Hiring Team,"),
            body=[p for p in cleaned if p],
            sign_off=str(craw.get("sign_off") or "Best,\nCasey Hsu"),
            model=model,
        )
        m.cover_latencies.append(time.monotonic() - t0)
        violations = validate_cover(
            cover, verified=verified, company=job.company,
            max_words=cfg.pipeline.cover_max_words,
        )
        m.cover_violation_counts.append(len(violations))
        if not violations:
            m.cover_validator_clean += 1
    except Exception as e:
        m.cover_latencies.append(time.monotonic() - t0)
        m.errors.append(f"cover: {type(e).__name__}: {str(e)[:120]}")

    # --- ANSWER (replicates pipeline.answer.write_answer so per-model `options`
    #     can be threaded — like write_cover, write_answer doesn't accept them.)
    ap = load_prompt(kb_dir, "answer")
    answer_max_words = cfg.pipeline.answer_max_words
    answer_user = ap.render_user(
        verified_facts=verified_text,
        question=fixture.question,
        jd_context=truncate(job.description or "", MAX_DESC_CHARS),
        max_words=str(answer_max_words),
        revisions="",
    )
    t0 = time.monotonic()
    try:
        araw = await complete_json(
            base_url=base_url, model=model, system=ap.system, user=answer_user,
            schema=ap.schema, temperature=ap.temperature, options=options,
            keep_alive=None,
        )
        text = araw.get("answer")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"answer missing 'answer' string; keys={sorted(araw.keys())}")
        answer = Answer(text=text.strip(), model=model)
        m.answer_latencies.append(time.monotonic() - t0)
        violations = validate_answer(
            answer, verified=verified, max_words=answer_max_words,
        )
        m.answer_violation_counts.append(len(violations))
        if not violations:
            m.answer_validator_clean += 1
    except Exception as e:
        m.answer_latencies.append(time.monotonic() - t0)
        m.errors.append(f"answer: {type(e).__name__}: {str(e)[:120]}")

    # --- INTERVIEW-PREP (replicates pipeline.interview_prep.draft_prep_sections
    #     so per-model `options` can be threaded — like write_cover/write_answer,
    #     draft_prep_sections doesn't accept options.)
    pp = load_prompt(kb_dir, "interview-prep")
    prep_ctx = PrepContext(
        job_id=job.id,
        job_title=job.title or "(unknown)",
        job_company=job.company or "(unknown)",
        job_description=job.description or "",
        job_url="",
        stage="hiring_manager",
    )
    salary = cfg.applicant.salary_expectation_cad or "(not configured)"
    prep_user = pp.render_user(
        verified_facts=verified_text,
        stage=prep_ctx.stage,
        job_title=prep_ctx.job_title,
        job_company=prep_ctx.job_company,
        job_description=truncate(prep_ctx.job_description, MAX_DESC_CHARS),
        applicant_salary_expectation=salary,
        applicant_work_auth_canada=str(cfg.applicant.work_auth_canada).lower(),
        applicant_requires_visa_sponsorship=str(
            cfg.applicant.requires_visa_sponsorship
        ).lower(),
        audit_summary="(no application yet)",
        cover_summary="(no cover letter drafted)",
        research_blob="(none)",
        interview_context=_format_interview_context_for_prompt(
            build_interview_context(prep_ctx.stage)
        ),
        recruiter_type="unknown",
        recruiter_bias=_RECRUITER_BIAS_BLURB["unknown"],
        revisions="",
    )
    t0 = time.monotonic()
    try:
        praw = await complete_json(
            base_url=base_url, model=model, system=pp.system, user=prep_user,
            schema=pp.schema, temperature=pp.temperature, options=options,
            keep_alive=None,
        )
        sections = _decode_sections(praw, model=model)
        m.prep_latencies.append(time.monotonic() - t0)
        prep_violations = validate_prep_sections(
            sections, verified=verified,
            job_description=job.description or "",
        )
        m.prep_violation_counts.append(len(prep_violations))
        if not prep_violations:
            m.prep_validator_clean += 1
    except Exception as e:
        m.prep_latencies.append(time.monotonic() - t0)
        m.errors.append(f"prep: {type(e).__name__}: {str(e)[:120]}")

    # --- AUDIT (only if both tailor and cover succeeded)
    if tailored is not None and cover is not None:
        try:
            result = audit(
                tailored=tailored, cover=cover, score=None, verified=verified,
                company=job.company,
                cover_max_words=cfg.pipeline.cover_max_words,
                job_description=job.description,
                job_title=job.title,
            )
            m.audit_verdicts.append(result.verdict)
            if result.keyword_coverage_pct is not None:
                m.audit_coverage_pcts.append(result.keyword_coverage_pct)
        except Exception as e:
            m.errors.append(f"audit: {type(e).__name__}: {e}")


async def _bench_one_run_production(
    model: str, cfg: Config, m: ModelMetrics, kb_dir: Path,
    fixture: BenchFixture,
) -> None:
    """Run the real production pipeline once: score_job → tailor/cover/answer/
    prep *_with_retry → audit. Mutates `m`.

    This mirrors `apply_cmd` exactly, so it measures EVENTUAL pass-rate (after
    the retry loops) and attempts consumed — the true ship/revise/block a model
    would produce in jobhunt. No `options` are passed: every model runs at the
    gateway's `_DEFAULT_OPTIONS`, which is what production does regardless of the
    configured model. `tailor_resume_with_retry` internally runs
    `_shrink_to_one_page`, so one-page enforcement is included.
    """
    job = fixture.job
    verified = json.loads((kb_dir / "profile" / "verified.json").read_text(encoding="utf-8"))

    # --- SCORE
    t0 = time.monotonic()
    score_result = None
    try:
        score_result = await score_job(cfg, job)
        m.score_latencies.append(time.monotonic() - t0)
        m.score_schema_ok += 1  # score_job raises on schema failure; success = ok
        m.score_declines.append(
            score_result.decline_reason is not None
            or score_result.score < cfg.pipeline.min_score
        )
    except Exception as e:
        m.score_latencies.append(time.monotonic() - t0)
        m.errors.append(f"score: {type(e).__name__}: {str(e)[:120]}")

    # --- TAILOR (with retry; includes _shrink_to_one_page)
    t0 = time.monotonic()
    tailored = None
    try:
        tailored, tviol, tattempts = await tailor_resume_with_retry(
            cfg, job, max_attempts=cfg.pipeline.tailor_retry_attempts,
        )
        m.tailor_latencies.append(time.monotonic() - t0)
        m.tailor_attempts.append(tattempts)
        if not tviol:
            m.tailor_fab_clean += 1  # eventual-clean in production mode
    except FabricationError:
        # The honesty guard rejected the output after exhausting retries. On
        # the fabrication-pressure fixture this is the SAFE outcome (the resume
        # would have fabricated unverified skills); count it distinctly so the
        # Guard row reads "rejected" rather than a plain error.
        m.tailor_latencies.append(time.monotonic() - t0)
        m.tailor_safe_rejections += 1
    except Exception as e:
        m.tailor_latencies.append(time.monotonic() - t0)
        m.errors.append(f"tailor: {type(e).__name__}: {str(e)[:120]}")

    # --- COVER (with retry)
    t0 = time.monotonic()
    cover = None
    try:
        cover, cviol, cattempts = await write_cover_with_retry(
            cfg, job,
            verified=verified, company=job.company,
            max_words=cfg.pipeline.cover_max_words,
            max_attempts=cfg.pipeline.cover_retry_attempts,
        )
        m.cover_latencies.append(time.monotonic() - t0)
        m.cover_attempts.append(cattempts)
        m.cover_violation_counts.append(len(cviol))
        if not cviol:
            m.cover_validator_clean += 1
    except Exception as e:
        m.cover_latencies.append(time.monotonic() - t0)
        m.errors.append(f"cover: {type(e).__name__}: {str(e)[:120]}")

    # --- ANSWER (with retry; mirrors answer_cmd's max_attempts source)
    t0 = time.monotonic()
    try:
        _, aviol, aattempts = await write_answer_with_retry(
            cfg,
            question=fixture.question,
            jd_context=truncate(job.description or "", MAX_DESC_CHARS),
            verified=verified,
            max_words=cfg.pipeline.answer_max_words,
            max_attempts=cfg.pipeline.cover_retry_attempts,
        )
        m.answer_latencies.append(time.monotonic() - t0)
        m.answer_attempts.append(aattempts)
        m.answer_violation_counts.append(len(aviol))
        if not aviol:
            m.answer_validator_clean += 1
    except Exception as e:
        m.answer_latencies.append(time.monotonic() - t0)
        m.errors.append(f"answer: {type(e).__name__}: {str(e)[:120]}")

    # --- INTERVIEW-PREP (with retry; mirrors interview_prep_cmd)
    t0 = time.monotonic()
    try:
        prep_ctx = PrepContext(
            job_id=job.id,
            job_title=job.title or "(unknown)",
            job_company=job.company or "(unknown)",
            job_description=job.description or "",
            job_url="",
            stage="hiring_manager",
        )
        _, pviol, pattempts = await draft_prep_with_retry(
            cfg, ctx=prep_ctx, verified=verified,
            max_attempts=cfg.pipeline.cover_retry_attempts,
        )
        m.prep_latencies.append(time.monotonic() - t0)
        m.prep_attempts.append(pattempts)
        m.prep_violation_counts.append(len(pviol))
        if not pviol:
            m.prep_validator_clean += 1
    except Exception as e:
        m.prep_latencies.append(time.monotonic() - t0)
        m.errors.append(f"prep: {type(e).__name__}: {str(e)[:120]}")

    # --- AUDIT (real score_result feeds must-haves, as in apply_cmd)
    if tailored is not None and cover is not None:
        try:
            result = audit(
                tailored=tailored, cover=cover, score=score_result, verified=verified,
                company=job.company,
                cover_max_words=cfg.pipeline.cover_max_words,
                job_description=job.description,
                job_title=job.title,
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


def _avg_int(xs: list[int]) -> str:
    return f"{round(sum(xs) / len(xs), 1)}" if xs else "n/a"


def _guard_summary(m: ModelMetrics, runs: int, fixture: BenchFixture) -> str:
    """One-cell guard verdict, fixture-dependent.

    - decline fixture  → `declined X/N` (X = runs the score guard declined).
    - fabrication fixture → `fab-safe X/N (clean A / rej B)`: a run is SAFE if
      the tailor was eventual-clean (omitted the unverified skills) OR the
      fabrication guard rejected it. Unsafe = clean tailor that slipped a
      watch-listed skill through, or a non-fabrication error.
    - happy fixture    → `not-declined X/N` (the score guard should NOT fire).
    """
    declined = sum(1 for d in m.score_declines if d)
    if fixture.expect_decline:
        return f"declined {declined}/{runs}"
    if fixture.expect_fabrication_pressure:
        clean = m.tailor_fab_clean
        rej = m.tailor_safe_rejections
        return f"fab-safe {clean + rej}/{runs} (clean {clean} / rej {rej})"
    return f"not-declined {runs - declined}/{runs}"


def _print_table(
    metrics: list[ModelMetrics], runs: int, mode: str, fixture: BenchFixture
) -> None:
    clean_label = "eventual-clean" if mode == "production" else "1st-pass clean"
    tags = []
    if fixture.expect_decline:
        tags.append("expect-decline")
    if fixture.expect_fabrication_pressure:
        tags.append("expect-fab-pressure")
    tag_str = f"  [{', '.join(tags)}]" if tags else ""
    print(f"\n{'=' * 92}")
    print(f"FIXTURE: {fixture.key}{tag_str}")
    print(f"HEAD-TO-HEAD  ({runs} runs/model, mode={mode}, clean = {clean_label})")
    print(f"{'=' * 92}\n")

    rows = [
        ("Model",        [m.label for m in metrics]),
        ("Params src",   [m.param_src for m in metrics]),
        ("Score lat",    [f"{_avg(m.score_latencies)}s" for m in metrics]),
        ("Score JSON",   [_pct(m.score_schema_ok, runs) for m in metrics]),
        ("Tailor lat",   [f"{_avg(m.tailor_latencies)}s" for m in metrics]),
        ("Tailor clean", [_pct(m.tailor_fab_clean, runs) for m in metrics]),
        ("Tailor attempts (avg)", [_avg_int(m.tailor_attempts) for m in metrics]),
        ("Cover lat",    [f"{_avg(m.cover_latencies)}s" for m in metrics]),
        ("Cover clean",  [_pct(m.cover_validator_clean, runs) for m in metrics]),
        ("Cover violations (avg)",
            [f"{round(sum(m.cover_violation_counts) / len(m.cover_violation_counts), 1)}"
             if m.cover_violation_counts else "n/a" for m in metrics]),
        ("Cover attempts (avg)", [_avg_int(m.cover_attempts) for m in metrics]),
        ("Answer lat",   [f"{_avg(m.answer_latencies)}s" for m in metrics]),
        ("Answer clean", [_pct(m.answer_validator_clean, runs) for m in metrics]),
        ("Answer violations (avg)",
            [f"{round(sum(m.answer_violation_counts) / len(m.answer_violation_counts), 1)}"
             if m.answer_violation_counts else "n/a" for m in metrics]),
        ("Answer attempts (avg)", [_avg_int(m.answer_attempts) for m in metrics]),
        ("Prep lat",     [f"{_avg(m.prep_latencies)}s" for m in metrics]),
        ("Prep clean",   [_pct(m.prep_validator_clean, runs) for m in metrics]),
        ("Prep violations (avg)",
            [f"{round(sum(m.prep_violation_counts) / len(m.prep_violation_counts), 1)}"
             if m.prep_violation_counts else "n/a" for m in metrics]),
        ("Prep attempts (avg)", [_avg_int(m.prep_attempts) for m in metrics]),
        ("Audit verdicts", [_verdict_summary(m.audit_verdicts) for m in metrics]),
        ("Keyword cov (avg)",
            [f"{_avg(m.audit_coverage_pcts)}%" if m.audit_coverage_pcts else "n/a"
             for m in metrics]),
        ("Guard",        [_guard_summary(m, runs, fixture) for m in metrics]),
        ("Errors",       [str(len(m.errors)) for m in metrics]),
    ]
    label_w = max(len(r[0]) for r in rows)
    col_w = 32  # wide enough for the Guard cell, e.g. "fab-safe 2/2 (clean 1 / rej 1)"
    for label, vals in rows:
        print(f"{label:<{label_w}}  " + "".join(f"{v:<{col_w}}" for v in vals))

    print()
    for m in metrics:
        if m.errors:
            print(f"\n--- {m.label} errors ---")
            for e in m.errors:
                print(f"  {e}")


async def _release_model(base_url: str, model: str) -> None:
    """Unload a model from VRAM (keep_alive=0).

    Needed because production mode calls the real pipeline functions
    (score_job / *_with_retry), which hit complete_json at its default
    keep_alive=-1 ("Forever") — a one-off bench shouldn't squat VRAM after
    exit. We can't pass keep_alive through those functions without a runtime
    src change, so we explicitly unload here instead. Best-effort: failures are
    ignored (the systemd OLLAMA_KEEP_ALIVE still bounds residency).
    """
    host = base_url.rstrip("/")
    if host.endswith("/v1"):
        host = host[: -len("/v1")]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            await client.post(f"{host}/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        pass


def _parse_models(specs: list[str]) -> list[tuple[str, str]]:
    """Turn `--models` specs into (label, model_id) pairs.

    A spec is either a bare model id (`granite4.1:8b` → label `granite4.1`) or
    an explicit `label=model_id` pair.
    """
    out: list[tuple[str, str]] = []
    for spec in specs:
        if "=" in spec:
            label, model = spec.split("=", 1)
        else:
            model = spec
            label = spec.split(":", 1)[0]
        out.append((label.strip(), model.strip()))
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help="Models to bench: bare ids (`granite4.1:8b`) or `label=id` pairs. "
        "Default: the 5 base models from ~/ai/README.md.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS_PER_MODEL,
        help=f"Runs per model (default {DEFAULT_RUNS_PER_MODEL}).",
    )
    parser.add_argument(
        "--ai-root",
        type=Path,
        default=DEFAULT_AI_ROOT,
        help="[raw mode only] Path to the ~/ai model builders, used to pull "
        f"per-model PARAMS (default {DEFAULT_AI_ROOT}). Models with no matching "
        "builder fall back to the gateway's _DEFAULT_OPTIONS.",
    )
    parser.add_argument(
        "--mode",
        choices=("production", "raw"),
        default="production",
        help="production (default): run the real retry-wrapped pipeline "
        "(score_job → tailor/cover/answer _with_retry → audit) at the gateway's "
        "_DEFAULT_OPTIONS — measures eventual ship-rate + attempts, the basis for "
        "choosing a default model. raw: single-shot first-pass calls at per-model "
        "~/ai params (no retries) — diagnostic only.",
    )
    args = parser.parse_args()

    candidates = _parse_models(args.models) if args.models else DEFAULT_CANDIDATES
    runs = args.runs
    ai_root = args.ai_root
    mode = args.mode
    fixtures = DEFAULT_FIXTURES

    kb_dir = REPO_ROOT / "kb"
    if not (kb_dir / "profile" / "verified.json").is_file():
        print("error: kb/profile/verified.json missing — run `jobhunt convert-resume` first.")
        return

    # Keyed by fixture so each fixture gets its own table block. One ModelMetrics
    # per (model, fixture). The model loop stays outer so a candidate pays a
    # single cold load for all its fixtures (OLLAMA_MAX_LOADED_MODELS=1).
    results: dict[str, list[ModelMetrics]] = {f.key: [] for f in fixtures}
    for label, model in candidates:
        if mode == "production":
            # Production-faithful: every model runs at the gateway's
            # _DEFAULT_OPTIONS, so no per-model params are pulled.
            options = None
            param_src = "default (_DEFAULT_OPTIONS)"
        else:
            pulled = _params_from_ai(model, ai_root)
            options = pulled[0] if pulled else None
            param_src = f"~/ai:{pulled[1]}" if pulled else "default"
        print(f"\n>>> {label} ({model})  [mode: {mode}, params: {param_src}]")
        cfg = _make_cfg(model)
        per_fixture = {
            f.key: ModelMetrics(label=label, param_src=param_src) for f in fixtures
        }
        for fixture in fixtures:
            m = per_fixture[fixture.key]
            for i in range(runs):
                print(f"    [{fixture.key}] run {i + 1}/{runs}…", flush=True)
                if mode == "production":
                    await _bench_one_run_production(model, cfg, m, kb_dir, fixture)
                else:
                    await _bench_one_run(model, cfg, m, kb_dir, fixture, options=options)
        # Don't leave the model pinned in VRAM after the bench (production-mode
        # calls pin it Forever via the gateway default; raw mode passes None but
        # this is harmless there too).
        await _release_model(cfg.gateway.base_url, model)
        for f in fixtures:
            results[f.key].append(per_fixture[f.key])

    for fixture in fixtures:
        _print_table(results[fixture.key], runs, mode, fixture)
    if mode == "production":
        notes = (
            "\nNotes (production mode):\n"
            "  - Runs the real retry-wrapped pipeline (mirrors `apply_cmd`):\n"
            "    score_job → tailor/cover/answer _with_retry → audit. Tailor\n"
            "    includes _shrink_to_one_page.\n"
            "  - 'clean' = EVENTUAL clean (no violations after the retry loop).\n"
            "  - 'attempts (avg)' = LLM calls the retry loop consumed (1 = clean\n"
            "    first try, 3 = hit the cap). Higher = more retry latency cost.\n"
            "  - Every model runs at the gateway's _DEFAULT_OPTIONS — what\n"
            "    production sends regardless of the configured model.\n"
            "  - 'Guard' row: decline_senior → 'declined X/N' (score guard must\n"
            "    fire); fabrication_pressure → 'fab-safe X/N (clean / rej)' where\n"
            "    clean = tailor omitted the unverified skills, rej = fabrication\n"
            "    guard raised FabricationError (both SAFE); happy_fit →\n"
            "    'not-declined X/N'. The two adversarial fixtures are the\n"
            "    reliability signal — a fit-scored decline JD or a fabricated\n"
            "    skill is a product failure, not a formatting nit.\n"
            "  - With OLLAMA_MAX_LOADED_MODELS=1, each candidate pays one cold load.\n"
        )
    else:
        notes = (
            "\nNotes (raw mode):\n"
            "  - Single-shot first-pass calls; no retries (diagnostic only).\n"
            "  - Per-model sampler params (incl. num_ctx) are pulled from\n"
            "    ~/ai/build-* ('Params src' row); temperature is always jobhunt's\n"
            "    per-slot value, not the builder's.\n"
            "  - 'clean' = passed validator on the first try.\n"
            "  - 'Guard' row reads the model's first-pass judgment (no score_job\n"
            "    clamp, no tailor retries): decline_senior → 'declined X/N',\n"
            "    fabrication_pressure → 'fab-safe X/N (clean / rej)', happy_fit →\n"
            "    'not-declined X/N'.\n"
            "  - With OLLAMA_MAX_LOADED_MODELS=1, each candidate pays one cold load.\n"
        )
    print(notes)


if __name__ == "__main__":
    asyncio.run(main())

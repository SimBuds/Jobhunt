# IMPLEMENT.md — Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done — checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone; it lives here.

## Current state

**No active plan.** Phases 1–4 done 2026-05-29 — `scripts/bench_models.py` now
has a `--mode production` path (real retry loops + audit at `_DEFAULT_OPTIONS`)
to decide if gemma4 should replace qwen3.5:9b as the configured default. Next
human step: run `--mode production --runs 5` across qwen/gemma/ministral and
read the eventual ship-rate + attempts before any `gateway.tasks` change.

**Plan: Refresh `scripts/bench_models.py` to eval all 5 local models against jobhunt's pipeline.**

Context: `~/ai/README.md` documents a 5-model local lineup (qwen3.5:9b /
granite4.1:8b / llama3.1:8b / ministral-3:8b / gemma4:e2b) where the eval suite
crowned **granite** for coding-correctness + *structured output* — the axis that
matters for jobhunt's schema-constrained score/tailor/cover/answer calls. Goal
is to re-run jobhunt's *own* head-to-head bench across these models and decide
whether granite (or another) is a real contender to replace bare `qwen3.5:9b` as
the configured default in `gateway.tasks`.

**Decisions made (correct if wrong):**
- Bench targets the **5 bare base models**, not the `*-custom` Modelfiles.
  Rationale: jobhunt's gateway overrides each model's SYSTEM (`kb/prompts/`), so
  a custom Modelfile's SYSTEM is moot inside jobhunt. `--models` still accepts
  arbitrary tags so custom models can be benched too.
- **Per-model sampler params are pulled from `~/ai/build-*`** rather than using
  jobhunt's qwen-derived `_DEFAULT_OPTIONS` uniformly, so each candidate is
  evaluated at its own `~/ai`-tuned heat (granite/gemma/etc. aren't crippled by
  qwen's `top_p 0.95 / top_k 20 / presence_penalty 0`). Sent as the per-call
  `options` dict.
  - **Temperature is NOT pulled** — jobhunt's per-task temps win (score=0.0,
    tailor=0.3, cover=0.7). score@0 determinism is load-bearing for JSON
    quality; the bench's explicit `temperature=` kwarg already overrides options.
  - **num_ctx IS pulled** per-model (12288/16384/32768). At 12288 (granite,
    ministral) a long JD truncated to `MAX_DESC_CHARS=16000` can overflow →
    prose-not-JSON; that failure is a *real* GPU-fit signal, not a bench bug.
- This is a manual eval script only (not CI, read-only on the DB). No runtime
  behavior changes; switching the configured default is explicitly a *later*
  decision, gated on the bench output — not part of this plan.

### Phase 1 — Retarget the bench to the 5 base models with CLI selection

**Goal:** Make `bench_models.py` eval the current 5-model lineup, selectable via
a `--models` flag, with the stale task-slot name fixed.

**Files to touch:**
- `scripts/bench_models.py` — update `CANDIDATES` to the 5 base models; add
  `argparse` `--models`/`--runs` flags; fix the stale `"qa"` slot → `"answer"`
  in `_make_cfg`.

**Functions to add/change:**
- `_make_cfg` — change — replace the dead `"qa"` task key with `"answer"` so the
  Config mirrors `GatewayConfig.tasks` (score/tailor/cover/answer/embed).
- `main` — change — parse `--models` (default: the 5 base tags) and `--runs`
  (default: current `RUNS_PER_MODEL=2`); drive `CANDIDATES` from the flag.
- `CANDIDATES`/`RUNS_PER_MODEL` — change — renamed to `DEFAULT_CANDIDATES`
  (the 5 base models) + `DEFAULT_RUNS_PER_MODEL`; both now CLI-overridable.
- `_parse_models` — add — turns `--models` specs (`id` or `label=id`) into pairs.
- `_print_table` — change — now takes `runs` as a param (no module global).

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `rg "bench" scripts/`, `rg "complete_json|_make_cfg" scripts/bench_models.py`
- Candidates found: `scripts/bench_models.py` (the existing head-to-head bench).
- Why not reused: it IS reused — this phase refreshes it rather than writing a
  new harness. No second bench script exists.

**Verification:** (≤ 3 bullets)
- `uv run python scripts/bench_models.py --help` lists `--models` / `--runs`. ✓
- `uv run ruff check scripts/bench_models.py` clean. ✓
- `_make_cfg` builds a valid `Config` with slots score/tailor/cover/answer/embed
  (no `"qa"`); `_parse_models` handles bare ids + `label=id`. ✓ (no network)

**Status:** [x] done

### Phase 2 — Pull per-model sampler params from `~/ai/build-*`

**Goal:** Evaluate each candidate at its own `~/ai`-tuned `PARAMS` (samplers +
num_ctx) instead of jobhunt's uniform `_DEFAULT_OPTIONS`.

**Files to touch:**
- `scripts/bench_models.py` — add a `PARAMS`-block parser + `--ai-root` flag;
  wire the parsed options into every `complete_json` call.

**Functions to add/change:**
- `_params_from_ai(base_model, ai_root)` — add — find the `build-*` script whose
  `BASE_MODEL` matches `base_model`, parse its `PARAMS=( ... )` block into an
  Ollama options dict, **drop `temperature`** (jobhunt's per-task temp wins),
  keep `num_ctx` + samplers. Return `None` (→ fall back to `_DEFAULT_OPTIONS`) if
  no matching builder is found, so arbitrary `--models` tags still work.
- `_bench_one_run` — change — resolve options once per model and pass
  `options=` to each score/tailor/cover `complete_json` call.
- `_print_table` — change — add a "Params src" row showing `~/ai:<builder>` vs
  `default` so the run records which params each model used.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `rg "PARAMS|options" scripts/bench_models.py src/jobhunt/gateway/client.py`
- Candidates found: `gateway.client._DEFAULT_OPTIONS` (the fallback), the
  `options=` kwarg on `complete_json`.
- Why not reused for parsing: no existing PARAMS reader — the `~/ai` builders are
  bash, parsed nowhere in jobhunt. The new parser is the minimal bridge; it
  feeds the existing `options=` plumbing rather than adding a new call path.

**Verification:** (≤ 3 bullets)
- `python -c` unit: `_params_from_ai("granite4.1:8b", ~/ai)` returns
  `{"num_ctx":12288,"top_p":0.92,...}` with no `temperature` key; unknown tag → `None`. ✓
- `uv run ruff check scripts/bench_models.py` clean. ✓
- All 5 base models matched their builders with correct pulled values. ✓

**Surface note (mid-phase, user-approved):** cover slot runs through
`write_cover()` which doesn't accept `options`, so the cover call is
**replicated inline** in `_bench_one_run` (imports `_strip_trailing_signoff`,
rebuilds `CoverLetter`) — keeps all 3 slots on the same per-model params with no
`src/` change. `--ai-root` flag added (default `~/ai`).

**Status:** [x] done

### Phase 3 — Add the `answer` slot to the bench

**Goal:** Exercise the `answer` pipeline per model so all four LLM task slots are
measured, not just score/tailor/cover.

**Files to touch:**
- `scripts/bench_models.py` — add an answer pass in `_bench_one_run` + answer
  metrics in `ModelMetrics`/`_print_table`.

**Functions to add/change:**
- `_bench_one_run` — change — add an `answer` call via `pipeline.answer` against
  a fixed fixture question, record latency + validator clean-rate.
- `ModelMetrics` / `_print_table` — change — add `answer_latencies` /
  `answer_validator_clean` rows.

**Reuse audit:**
- Search terms: `rg "validate_answer|write_answer" src/jobhunt/pipeline/answer.py`
- Candidates found: `pipeline.answer.validate_answer` + the answer write path.
- Why not reused: they ARE reused — same pattern as the existing cover bench
  block uses `validate_cover`.

**Verification:**
- One real `uv run python scripts/bench_models.py --models qwen3.5:9b --runs 1`
  prints an Answer row with non-zero latency. ✓ (5.4s, 100% clean, 0 errors;
  all 4 slots ran, params src `~/ai:build-qwen`.)

**keep_alive (server-governed):** `complete_json` now accepts `keep_alive=None`
to *omit* the key from the payload (default stays `-1`, so production scans are
unchanged); the bench passes `None` so Ollama's `OLLAMA_KEEP_ALIVE=30m` governs
residency. Verified: post-bench `ollama ps` shows `~29 minutes from now`, not
`Forever`. (Footprint note: base `qwen3.5:9b` and `qwen-custom` are the *same*
Q4_K_M weights at the same 16384 ctx — the earlier 26%/74% CPU spill was
load-time VRAM contention with Brave on the GPU, not a regression. On a clean
GPU all 5 models load 100% GPU.)

**In-file fix found during verification:** the bench's `score` render was
missing the `years_experience` placeholder the score prompt now requires (added
by the YoE-aware calibration), so the bench had been broken for *every* slot
since. Fixed in-place by mirroring `pipeline.score`'s `yoe_str` derivation —
same file, required for any run.

**Status:** [x] done

### Phase 4 — Add a production-simulation mode that runs the retry loops

**Goal:** Add a `--mode production` bench path that exercises the real
retry-wrapped pipeline so a model's *eventual* ship-rate (not first-pass) decides
the crown.

**Why:** The 5-run first-pass bench can't fairly rank gemma — its only weakness
(cover clean 0%, audit `ship=0`) is in the cover slot, which production recovers
via `write_cover_with_retry`. We need eventual-pass + attempts-consumed.

**Decisions made (from this session's Q&A):**
- **Basis = production `_DEFAULT_OPTIONS`**, NOT per-model `~/ai` params. The
  retry fns call the gateway without `options`, which is exactly what production
  does. Answers "what happens if I change the configured model." No `src/` change.
- The existing first-pass path is preserved as **`--mode raw`** (keeps Phase 2's
  `~/ai` param-pull + `--ai-root`); `--mode production` becomes the default.
- Fixture JD only this phase (real-JD sampling was explicitly deferred).

**Files to touch:**
- `scripts/bench_models.py` — add `--mode {raw,production}`; add
  `_bench_one_run_production`; add attempt/eventual-clean metrics + table rows.

**Functions to add/change:**
- `_bench_one_run_production` — add — per run: `score_job` → `tailor_resume_with_retry`
  (max_attempts=`cfg.pipeline.tailor_retry_attempts`; this internally runs
  `_shrink_to_one_page`) → `write_cover_with_retry` → `write_answer_with_retry`
  (max_attempts=`cfg.pipeline.cover_retry_attempts`, mirroring `answer_cmd`) →
  `audit(score=score_result, …)`. Records latency, attempts, final-violation
  count (eventual-clean = empty), and audit verdict/coverage. No `options` passed.
- `ModelMetrics` — change — add `tailor_attempts` / `cover_attempts` /
  `answer_attempts` lists (avg attempts). Reuse existing `*_clean` counters as
  *eventual*-clean in production mode.
- `_print_table` — change — add three "… attempts (avg)" rows (show `n/a` when
  empty, i.e. raw mode); take `mode` so the header notes raw vs production.
- `main` — change — parse `--mode` (default `production`); dispatch to the right
  per-run fn; set `param_src` to `default (_DEFAULT_OPTIONS)` in production mode.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `rg "tailor_resume_with_retry|write_cover_with_retry|write_answer_with_retry|score_job" src/jobhunt`
- Candidates found: the three `*_with_retry` entry points + `score_job` +
  `audit` — all already the production orchestration (mirrored from `apply_cmd`).
- Why not reused elsewhere: they ARE reused as-is; this phase just calls the real
  production functions instead of replicating raw gateway calls.

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean. ✓
- `--mode production --models qwen3.5:9b --runs 1`: ran the retry loops, printed
  attempts rows (all 1.0) + `ship=1`; eventual-clean 100% across slots; param_src
  `default (_DEFAULT_OPTIONS)`. ✓ (Confirms the prediction — qwen's messy
  first-pass cover recovers to clean/ship under the retry loop.)
- `--mode raw --runs 1`: still works — `mode=raw`, params `~/ai:build-qwen`,
  attempts `n/a`, label "1st-pass clean". ✓

**Status:** [x] done

Standing context that isn't obvious from the code:
- `kb/profile/` is gitignored PII (regenerated by `jobhunt convert-resume` from
  `Resume.docx`, now the single source of truth — all current skill/tier state
  is encoded in the doc, so a regen is safe).
- Skill honesty tiers: **Core** (paid production) / **Familiar** (light/academic
  — Java, Spring Boot, Angular, MCP, Agile, Headless, Figma, Astro). There is no
  Projects tier (added then removed May 2026 — Astro moved to Familiar).
- **React umbrella:** verified Core skill is `React (Redux, Native)`; renders as
  plain "React" unless a JD names Redux/React Native (then surfaced, Core-grade).
  No standalone Redux item.
- `include_senior_roles=false` in `~/.config/jobhunt/config.toml` (Casey <3 YoE).
- Default model: bare `qwen3.5:9b`; the gateway pins app-owned options
  (`num_ctx=16384` + samplers) on every call.

## Completed plans (archive)

Terse index; see git history for the full phase-by-phase detail that used to
live here.

- **Remove dormant Projects tier + docs audit** (2026-05-28). After Astro moved
  to Familiar, stripped the empty `skills_projects` tier from code (guard, score
  cap, shrink ladder, parser), tests, and prompt/policy docs; kept the React
  umbrella + `_PAGE_SAFETY_MARGIN`. Audited the 4 pillars: fixed PLAN.md
  (qwen3.5:9b default, app-owned `num_ctx`) and an AGENTS.md systemd-comment
  contradiction. Suite 688.
- **React umbrella** (2026-05-28). Collapsed React/Redux/React Native into one
  Core umbrella skill `React (Redux, React Native)`; renders as "React" by
  default, surfaces Redux/React Native only when the JD names them. Rules in
  `tailor.md` / `tailoring-rules.md` / `Resume_Tailoring_Instructions.md` /
  AGENTS.md. Suite 693. *(A portfolio Projects-**section** feature was scoped
  then declined — Casey keeps resumes experience-forward; portfolio stays in
  the contact-line links.)*
- **Projects skill tier** (2026-05-28). Added the `skills_projects` honesty tier
  between Core and Familiar — wired through the fabrication guard + Familiar-only
  score cap, the one-page shrink ladder (`_PROJECTS_FLOOR`), prompt/policy docs,
  and `parse_docx` (with a `_PAGE_SAFETY_MARGIN` fix after a real `.docx`
  overflowed). Suite 692.
- **base qwen3.5:9b + app-owned options** (2026-05-28). Gateway pins
  `_DEFAULT_OPTIONS` (num_ctx 16384 + samplers) on every call so structured-task
  behavior is in-repo; switched default model to bare `qwen3.5:9b`. Root cause
  of prose-not-JSON was unset `OLLAMA_CONTEXT_LENGTH` → 4096 truncation.
- **Workday CXS GTA-targeted scan** (2026-05-28). Large Workday boards issue a
  deduped `_GTA_SEARCH_TERMS` union instead of a blank first-100 walk so GTA
  roles aren't buried. `_BLANK_SCAN_MAX=200`.
- **Post-audit fixes** (2026-05-27). `HARD_COVERAGE_FLOOR_PCT=50` escalates
  sub-50% coverage to `block`; `_OVERREACH_PATTERNS` in `cover_validate` catches
  framing-level capability claims; baseline `Resume.docx` summary rewrite.

## Phase template

Copy this block for each phase of an approved plan. One phase = one atomic,
revertable commit (see Phase-Sizing Rules in `AGENTS.md`).

```
### Phase <N> — <one-sentence goal, no "and">

**Goal:** <single declarative sentence>

**Files to touch:**
- path/to/file — what changes

**Functions to add/change:**
- module.function — add | change — what it does

**Reuse audit:** (per Reuse-First Rule in AGENTS.md)
- Search terms: `<grep/rg terms used>`
- Candidates found: <list, or "none">
- Why not reused: <reason per candidate>

**Verification:** (≤ 3 bullets)
- <how to prove it works — test name or manual E2E output>

**Status:** [ ] not started | [ ] in progress | [ ] done
```

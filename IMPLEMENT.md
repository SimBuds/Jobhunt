# IMPLEMENT.md — Execution engine

Pillar 4 of the documentation architecture defined in [AGENTS.md](AGENTS.md).
This file is the granular, phase-by-phase breakdown of in-flight work. Agents
**read** this at the start of every phase (Phase 1 and Phase 3 of the Workflow
Contract) and **write** to it as part of the Definition of Done — checking off
completed phases and logging deferred work as new phases. Never leave a plan in
conversational context alone; it lives here.

## Current state

**Plan "Backfill JD-required verified skills the tailor drops" completed
2026-05-31** (single phase; suite 709). A
2026-05-31 audit of the 14 tailored applications found honesty airtight (0
fabrication / cover-violation / alignment flags) but 3 resumes shipped below the
ATS keyword threshold. The headline case: shyftlabs (score 82, applied 05-31) hit
62% coverage with Git/AWS/Azure "missing" — all three are in `verified.json`
(`skills_data_devops`) and the JD required them, but the tailor consolidated that
bucket into a "Backend & APIs" category and dropped Git/GH-Actions/Jest/AWS/Azure.
Root cause: `_tailor_once` never sees the JD must-haves, so infra/cloud skills
that don't fit the JD-relevant categories fall out — even when required. Fix: a
deterministic post-processor `_ensure_jd_required_skills` that re-adds any
verified skill the JD names but the tailored output omits, mirroring the existing
`_complete_familiar_bucket` backfill. Single phase below. (The 0%/43% cases are
honest non-fits already covered by the block-at-<50% audit floor — out of scope.)

**Plan "Bound LLM generation with a `num_predict` ceiling" completed 2026-05-31**
(single phase; suite 703). A `scan`
right after the calibration fix scored 0/2 — both jobs `ReadTimeout` at 240s.
Diagnosed (2026-05-31): server healthy (trivial call 0.19s), model 100% on GPU,
but `_DEFAULT_OPTIONS` set no `num_predict`, so generation was unbounded to the
16k context. On certain thin JDs qwen3.5:9b ignores `think=false` and reasons
**in-band** — opens a `reasons[]` JSON string and pours a monologue into it,
never closing it (measured: 8000 tokens, `done_reason=length`, 28KB invalid
JSON), exhausting context and hanging the scan. Fix: pin `num_predict=4096` in
`gateway.client._DEFAULT_OPTIONS` — above the largest legit output (tailor ~2.2k
tokens) so nothing real truncates, bounding a runaway to ~50s (fast logged failure
vs 8-min stall). NOTE: this stops the *hang*; pathological JDs still fail to score
(truncated JSON is invalid) — making qwen stop reasoning in-band is a deeper
follow-up (logged in Future work). Single phase below.

**Plan "Fix score calibration — thin-JD inflation cap + backlog re-score"
completed 2026-05-31** (both phases; suite 703). A 2026-05-31 audit of the last 4
scans (232 scored jobs) found the apply queue biased toward under-described jobs:
the same ZoomInfo *Full Stack Engineer* scored 82 from its 500-char Adzuna snippet
vs 55 from the 7,140-char Greenhouse JD. Root cause: the `must_have_count < 3`
carve-out in `pipeline/score.py` skipped the coverage clamp for thin JDs and let
the raw LLM score pass through with no ceiling. Fixed with a length-gated
confidence cap (`thin_jd_score_cap=70`, `thin_jd_chars=800`) + a targeted backlog
re-score (20 rows; after: 0 thin rows above the cap). **Out of scope** (logged,
user-confirmed): min_score re-tune (stays 55) and same-source near-dupe collapse
(deferred — see Future work). Per-phase detail below.
- Phase 1 — Thin-JD confidence cap (code + tests). **Done.**
- Phase 2 — Re-score the affected backlog through the new cap. **Done.**

Plan "Expand `analyze certs` AI/LLM coverage" completed 2026-05-30 (Phase 1) —
`_KNOWN` now covers the 2026 AI/LLM cert landscape; suite 701. The external
cert/skill trends command is logged as future work below (separate plan when
prioritized).

### Phase 1 — Backfill JD-required verified skills (`_ensure_jd_required_skills`)

**Goal:** Guarantee that any verified skill the JD names appears in the tailored
resume, so the tailor can't drop a JD-required skill the candidate actually has.

**Files to touch:**
- `src/jobhunt/pipeline/tailor.py` — new `_ensure_jd_required_skills(tailored,
  verified, job)`; import `phrase_present`/`peer_match` from `pipeline._keywords`;
  call it in `_tailor_once` right after `_complete_familiar_bucket` (before
  `_cap_lead_category_size` / `_shrink_to_one_page`).
- `tests/test_tailor_*.py` — unit tests + a real-artifact regression.

**Functions to add/change:**
- `_ensure_jd_required_skills` — add. For each verified non-Familiar skill
  (`skills_core/cms/data_devops/ai`) the JD names (`phrase_present`/`peer_match`
  vs `title + description`), if it's absent from the flattened tailored resume,
  append it to the non-Familiar category with the most same-bucket siblings
  (fallback: last non-Familiar category; final: a new "Additional" bucket before
  Familiar, mirroring `_cap_lead_category_size`).
- `_tailor_once` — change — insert the call at line ~104.

**Reuse audit:**
- Search terms: `phrase_present|peer_match|_complete_familiar_bucket|
  _extract_must_haves_from_jd|skills_categories` in `pipeline/`.
- Candidates: `_complete_familiar_bucket` (same backfill pattern — mirrored, not
  imported), `audit._extract_must_haves_from_jd` + `audit._resume_text` (the
  exact JD-intersect + blob logic — but `audit` imports `tailor`, so importing
  back is a cycle; reuse the shared `_keywords.phrase_present`/`peer_match`
  primitives instead and inline the ~10-line blob/intersect). No new public
  interface; honest by construction (only ever re-adds verified skills).

**One-page interaction:** runs before `_shrink_to_one_page`, which only trims
summary / Familiar items / role bullets / coursework — never non-Familiar skill
categories — so backfilled skills survive; summary/bullets absorb the pressure.

**Verification (≤3 bullets):**
- New unit tests: a dropped JD-required `data_devops` skill (AWS) is re-added to
  the backend category; a verified skill the JD does NOT name is left out; an
  already-present skill isn't duplicated.
- Real-artifact regression: load the stored shyftlabs `tailored-resume.json` +
  JD, run the backfill, recompute `audit.keyword_coverage` → 62% rises to ≥ the
  soft threshold (deterministic, no Ollama).
- `uv run pytest -q` — full suite green.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 709;
+6 backfill tests). Real-artifact check (not committed — `data/` is gitignored):
the stored shyftlabs `tailored-resume.json` recomputed from **62% → 100%**
coverage, with Git/AWS/Azure re-added to "Backend & APIs". mypy clean; no new
ruff findings (3 pre-existing UP037 + 1 E501 untouched).

### Phase 1 — Pin `num_predict=4096` in the gateway defaults

**Goal:** Bound LLM generation so an in-band reasoning runaway fails fast instead
of hanging the scan past the 240s timeout.

**Files to touch:**
- `src/jobhunt/gateway/client.py` — add `num_predict: 4096` to `_DEFAULT_OPTIONS`
  (+ comment explaining the in-band-reasoning runaway it guards).
- `tests/test_gateway_errors.py` — extend `test_payload_pins_default_options` to
  assert `num_predict == 4096` is sent.

**Reuse audit:**
- Search terms: `num_predict|_DEFAULT_OPTIONS|presence_penalty|options` in
  `gateway/client.py` + `tests/`.
- Candidates found: existing `_DEFAULT_OPTIONS` dict + `test_payload_pins_
  default_options`. Both reused (extend, no new construct). Per-call `options=`
  override already supported, so future per-task tuning needs no new plumbing.

**Verification:**
- `uv run pytest -q tests/test_gateway_errors.py` — option is sent (6 passed).
- `uv run pytest -q` — full suite green (703).
- E2E (real `score_job` on the formerly-hanging Magna junior-coop JD): now
  **FAILED-FAST in 103.6s** with an unterminated-JSON `GatewayError` instead of a
  240s ReadTimeout hang. The ~103s = the ~50s `num_predict` bound × the one
  invalid-JSON retry `complete_json` does; each generation is bounded to ~50s. The
  job still doesn't score (in-band reasoning → invalid JSON) — that's the logged
  future-work fix, not this phase's goal.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 703).

### Phase 1 — Cap signal-poor (thin-JD) scores at a confidence ceiling

**Goal:** Stop short/snippet JDs that yield `<3` must-haves from passing the raw
LLM score through unbounded, so they can't outrank fully-described full-JD roles.

**Files to touch:**
- `src/jobhunt/config.py` — add `thin_jd_score_cap` + `thin_jd_chars` to
  `PipelineConfig`.
- `src/jobhunt/pipeline/score.py` — length-gate the `must_have_count < 3` branch.
- `tests/test_score_clamp.py` — repurpose the tiny-denominator test + 2 new cases.

**Functions to add/change:**
- `pipeline.score.score_job` — change — the `must_have_count < 3` branch now caps
  at `cfg.pipeline.thin_jd_score_cap` when `len(description) <
  cfg.pipeline.thin_jd_chars`; long JDs with few must-haves still trust raw.
- `config.PipelineConfig` — add `thin_jd_score_cap: int = 70`,
  `thin_jd_chars: int = 800`.

**Reuse audit:**
- Search terms: `clamp|cap|thin|snippet|raw_score|must_have_count` in
  `pipeline/score.py` + `tests/test_score_clamp.py`.
- Candidates found: existing `must_have_count < 3` carve-out branch;
  `_clamp_by_coverage`; `_all_matched_are_familiar`.
- Why not reused: the carve-out branch **is** reused (its body changes, no new
  function). `_clamp_by_coverage` caps by coverage denominator — wrong tool for
  the `<3` signal-poor case (that's exactly why the carve-out skips it). New
  config fields follow the `min_score` pattern. No new public interface.

**Verification:**
- `uv run pytest -q tests/test_score_clamp.py` — cap fires (78→70), long-JD
  exempt (82 stays), below-ceiling unchanged (64 stays), `≥3`-must-have clamp path
  intact.
- `uv run pytest -q` — full suite green.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31, suite 703;
+2 net tests; the tiny-denominator test was repurposed to assert the cap fires).

### Phase 2 — Re-score the affected backlog through the new cap

**Goal:** Correct today's queue (a code-only edit doesn't change `prompt_hash`,
so existing inflated rows won't auto-re-score).

**Files to touch:** none (operational — DB + scan run). Risky tier: back up
`data/jobhunt.db`, confirm the affected row count before deleting.

**Steps:** back up DB → preview `length(description) < 800 AND score > 70` →
`DELETE FROM scores` for that set → `uv run jobhunt scan --max-age-days 0`
(re-scores the now-unscored rows through Phase 1's cap; needs Ollama).

**Verification:**
- No `length(description) < 800` row scores above `thin_jd_score_cap` (70).
- Re-run the audit queries; 70+ band no longer dominated by 500-char snippets.
- Spot-check ZoomInfo + Magna.

**Status:** [ ] not started | [ ] in progress | [x] done (2026-05-31).
Executed as a **targeted in-place re-score of the 20 affected rows** (thin JD +
score > 70) via the exact `score_job → write_score → set_decline_reason` path,
rather than a full network `scan` — avoids fresh-ingest + `config.toml`
auto-discover churn and gives a clean before/after on the same rows. DB backed up
first (`data/jobhunt.db.bak-20260531-075606`). Results: 19/20 capped to ≤70 (most
to exactly 70; Magna 76→62, Finlink 72→62 re-scored below the cap on their
merits). 1 row (`adzuna_ca:5737493705`, Lioness AWS Backend Developer) timed out
at temp=0 on every attempt — deleted its score row so it's UNSCORED (no fabricated
number; re-scores on the next clean scan) rather than left at the inflated 72.
**After:** 0 thin rows above the cap; every score > 70 is a full-JD role; thin
500-char snippets pinned at exactly 70 (e.g. ZoomInfo snippet 82→70 while its
Greenhouse full JD stays 55). The `82×17` inflation cluster is gone.

<details><summary>Completed plan detail (Phase 1)</summary>

**Plan was: Expand `analyze certs` AI/LLM coverage +
log an external-trends command as future work.** Goal is twofold (both equally,
per session Q&A): improve cert *detection coverage* (the `_KNOWN` list has
essentially no 2026 AI/LLM certs) and, because added `_KNOWN` entries feed the
existing `_classify_verdict` path automatically, improve the *personal-fit
verdict* (`--min-score`) for the AI/LLM career direction. This stays fully
inside the AGENTS.md `analyze` contract: deterministic, regex + counters, **zero
network, zero LLM**. The external-trends idea (scanning sources outside the DB)
is real but is a *different surface* — it cannot live in `analyze` without
breaking that contract, so it's logged as a future plan, not built here.

**Decisions made (from session Q&A — correct if wrong):**
- **Phased.** Phase 1 = in-DB cert coverage now (small, safe, immediate). The
  external `research`-style command is logged as "Future work", scoped later as
  its own plan with its own network/robots/caching design.
- **AI/LLM focus** for the `_KNOWN` additions (matches the career-direction
  research). Cert names/codes verified via web search 2026-05-30 (AWS, Azure,
  Databricks, NVIDIA, Google Cloud sources).
- **No rubric change.** `_classify` / `_classify_verdict` are untouched — adding
  detectable certs is a *data* change, not a logic change, so the frozen,
  audit-traceable verdict rules stay frozen.
- **Match strictness: canonical name + exam code only** (session Q&A). Each new
  entry matches the full official name OR its exam code (`AIF-C01`, `MLA-C01`,
  `MLS-C01`, `AI-102`, `NCA-GENL`). NO loose shorthand (`Databricks GenAI`,
  `AWS AI Practitioner` without `Certified`) — preserves the curated-precision,
  low-false-positive spirit of `_KNOWN`.
- **TensorFlow Developer Certificate left in place.** It's discontinued (2026)
  but still in `_KNOWN` (line ~135); kept so legacy JDs still match. Pruning is
  out of scope for this plan.

**Previous plan:** "Harden `scripts/bench_models.py`" completed 2026-05-30
(Phases 1–3); 5-model run done — `qwen3.5:9b` confirmed as the default (see
memory `model-bench-2026-05`). Next bench-related human step (optional):
`--mode production --runs 5` if a closer re-rank is ever wanted.

### Phase 1 — Add 2026 AI/LLM certifications to the `_KNOWN` detector

**Goal:** Extend `analyze.certs._KNOWN` so the detector recognizes the current
AI/LLM certification landscape.

**Files to touch:**
- `src/jobhunt/analyze/certs.py` — add AI/LLM entries to `_KNOWN`.
- `tests/test_certs.py` — add one assertion per new cert + an
  overlap-ordering assertion for the Databricks specific-vs-generic case.

**Functions to add/change:**
- `_KNOWN` (`certs.py`) — change — append a `--- AI / LLM ---` block. Verified
  names/codes (each with a code-or-name regex, mirroring the existing AWS/Azure
  style; specific variants placed BEFORE any base so the overlap-filter keeps
  the longest match):
  - AWS Certified AI Practitioner (`AIF-?C01` | `AWS Certified AI Practitioner`)
  - AWS Certified Machine Learning Engineer – Associate (`MLA-?C01` | name)
  - AWS Certified Machine Learning – Specialty (`MLS-?C01` | name)
  - Azure AI Engineer Associate (`AI-?102` | `Azure AI Engineer`)
  - Databricks Certified Generative AI Engineer Associate (name) — placed BEFORE
    the existing generic `Databricks Certified` (line ~131) so the longer match
    wins.
  - Databricks Certified Machine Learning Associate / Professional (names) —
    same ordering note.
  - NVIDIA-Certified Associate: Generative AI LLMs (`NCA-?GENL` | name)
  - Google Cloud Generative AI Leader (name)
  - Google Professional Machine Learning Engineer (name; distinct from the
    existing GCP Data Engineer / Cloud Architect entries)
  - No change to `extract_certs` / `tally` / generic patterns — they consume
    `_KNOWN` as-is.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "Azure AI-900\|Databricks Certified\|TensorFlow\|_KNOWN" src/jobhunt/analyze/certs.py`
- Candidates found: existing `_KNOWN` (Azure AI-900 line 59, generic
  `Databricks Certified` line 131, TensorFlow Developer Certificate line 135).
- Why not reused: AI-900 (Fundamentals) and the generic Databricks entry are
  real but don't cover the new Engineer/Specialty/GenAI credentials; the new
  entries extend the same list with the same `(name, _pat(...))` shape — no new
  helper or pattern machinery needed. The generic Databricks entry is *kept*;
  new specific Databricks certs are ordered before it (overlap-filter rule).

**Verification:** (≤ 3 bullets)
- `uv run pytest tests/test_certs.py -q` — new per-cert assertions pass;
  the Databricks overlap test confirms the specific name wins over generic.
- `uv run ruff check src/jobhunt/analyze/certs.py` clean.
- No regression: full `uv run pytest -q` stays green (currently 688).

**Status:** [x] done

**Result (2026-05-30):** Added a `--- AI / LLM (2026) ---` block to
`analyze.certs._KNOWN` (9 entries: AWS AI Practitioner/AIF-C01, AWS ML
Engineer–Associate/MLA-C01, AWS ML–Specialty/MLS-C01, Azure AI Engineer
Associate/AI-102, Databricks GenAI Engineer Associate, Databricks ML
Associate + Professional, NVIDIA NCA-GENL, GCP Generative AI Leader, Google
Professional ML Engineer). Canonical-name-OR-exam-code only, no shorthand.
Databricks specifics ordered before the generic `Databricks Certified` entry.
Added a parametrized `test_ai_llm_certs` (12 cases) + `test_databricks_genai_wins_over_generic`
(single hunk, test_certs.py lines 58–102).
Verified: 9/9 new certs match exactly one entry in isolation (count=1, no
double-count); a realistic multi-cert JD detects all 5 present certs as KNOWN
with the generic tier producing only non-colliding review-list phrases
(`no_known_in_generic=True`, `generic_count=0`); `tests/test_certs.py`
**67 passed**; full suite **701 passed** (was 688, +13 new cases). No rubric
change — `_classify`/`_classify_verdict` untouched, so the new certs flow
through the existing `--trend`/`--min-score` verdict path automatically.

**Lint:** `ruff check src/jobhunt/analyze/certs.py` → **clean**. My test hunk
adds zero new violations. NOTE: `ruff check tests/test_certs.py` reports 8
**pre-existing** errors (E501 long en-dash lines 17/18/51/152/153; I001 +
SIM105 in the existing `_render_snapshot` test at 342–348) — all outside my
58–102 hunk, confirmed by a stash round-trip. The Phase-1 plan's "ruff clean"
verification was an incorrect assumption: the file was already dirty. Per the
no-piggybacking rule these are NOT fixed here; logged as optional cleanup
below.

**Pillar-4 docs:** README/PLAN/AGENTS unchanged — `analyze certs` has no new
flags or output-format change; only detector coverage grew (a data change).

**Plan complete.** External cert/skill trends command remains logged as future
work below.

Note (env): the en-dash (U+2013) in AWS cert names triggered terminal
output-doubling during verification; all checks were confirmed via file-based
reads, not streamed stdout.

</details>

### Future work (not in this plan — separate future plan)

- **Make in-band-reasoning JDs actually score (not just fail fast).** The
  `num_predict=4096` cap (2026-05-31) stops the *hang*, but JDs where qwen3.5:9b
  reasons inside a JSON string still fail to score (truncated JSON is invalid).
  Candidate fixes, each its own plan: (a) tighten the score schema (cap
  `reasons[]`/`gaps[]` `maxItems` so the grammar can't accept an endless string —
  verify Ollama's `format` honors `maxItems`); (b) a small score-only
  `presence_penalty` via per-call `options=` (Qwen's anti-reasoning-loop knob,
  but re-tune against structured-JSON repetition first — it's load-bearing at 0,
  see `_DEFAULT_OPTIONS`); (c) detect `done_reason=length`/parse-fail and retry
  with a corrective hint. Pick after observing how often it actually bites.
- **Pre-existing lint cleanup in `tests/test_certs.py`** (optional, own phase).
  8 ruff errors predating this work: E501 on the en-dash AWS-cert lines
  (17/18/51/152/153), I001 import-sort + SIM105 try/except in the
  `_render_snapshot` test (342–348). Trivial-tier; do as a standalone commit,
  not bundled into a feature.
- **External cert/skill trends command.** A NEW network-touching command (e.g.
  `jobhunt research trends`) that pulls cert/skill demand signal from sources
  *outside* the scanned-jobs DB. Must live OUTSIDE `analyze` (the analyze
  contract forbids network/LLM). Net-new infra: HTTP via `jobhunt.http`,
  source selection, `data/cache/` TTL caching, `robots.txt` respect, and the
  standing no-LinkedIn/Indeed/Glassdoor rule still binds. Scope as its own plan
  with its own phases when prioritized.

<details><summary>Completed plan detail (Phases 1–3)</summary>

The bench previously exercised only 4 of 5 LLM task
slots (score/tailor/cover/answer — **interview-prep is missing**) against a
single happy-path fixture JD that can never trigger the score auto-decline or
the tailor/cover fabrication guards. For a reliable product the bench must
measure both the slots *and* the safety guards that protect honesty/accuracy —
not just first-pass formatting on an easy JD. A model could ace the friendly JD
and still hallucinate interview anchors, wrongly accept a senior/management
role, or fabricate unverified skills under pressure, and the current bench would
never see it.

**Decisions made (from this session's Q&A — correct if wrong):**
- **Interview-prep slot added to BOTH modes** (raw + production). Production uses
  the real `pipeline.interview_prep.draft_prep_with_retry`; raw threads per-model
  `~/ai` options through a thin inline shim that reuses `validate_prep_sections`
  + the prep decode helpers (mirrors the existing inline cover/answer raw shims —
  no `src/` change).
- **Two separate adversarial fixtures**, not one combined: a *decline* JD
  (senior + people-management + over-YoE) to test the score auto-decline, and a
  *fabrication-pressure* JD (good-fit level but demands unverified watchlist
  skills like Kubernetes/Go/GraphQL/Kafka) to test the tailor/cover honesty
  guards + retry. Separate fixtures isolate the two signals — a combined JD that
  declines would skip nothing in the bench (it tailors regardless) but would
  conflate "did it decline?" with "did it fabricate?" on one row.
- **Bench-only, no runtime change.** Read-only on the DB; swapping the
  configured default model stays a later, human-gated decision.
- **Single profile** (Casey's `verified.json`) — only one user; multi-profile
  coverage is explicitly out of scope.
- A **decline** is measured as `ScoreResult.decline_reason is not None OR
  score < cfg.pipeline.min_score` (the apply-loop gate). On the fabrication
  fixture, a tailor `FabricationError` (guard rejected after retries) is a SAFE
  outcome, reported distinctly from a plain error.

**Pillar-4 doc impact:** `README.md` and `PLAN.md` need no change (the bench is
a manual dev script, not user-facing surface or app architecture). The in-file
docstring + `Notes` block are updated each phase. AGENTS.md "Testing" already
covers "Pipeline integration (real Ollama) — manual; not in CI"; no change
needed.

### Phase 1 — Add the interview-prep slot to the bench (both modes)

**Goal:** Measure the interview-prep LLM slot per model in both raw and
production bench modes.

**Files to touch:**
- `scripts/bench_models.py` — add a prep pass to `_bench_one_run` (raw) and
  `_bench_one_run_production` (production); add prep metrics + table rows.

**Functions to add/change:**
- `_bench_one_run` — change — add a raw prep pass: build a `PrepContext`, call a
  thin inline shim (render the interview-prep prompt → `complete_json(options=…)`
  → `_decode_sections`), then `validate_prep_sections`; record latency +
  first-pass clean + violation count. Inline mirrors the existing cover/answer
  raw shims so per-model `~/ai` options thread through.
- `_bench_one_run_production` — change — add
  `draft_prep_with_retry(cfg, ctx=…, verified=…, max_attempts=cfg.pipeline.cover_retry_attempts)`;
  record latency, eventual-clean, attempts.
- `ModelMetrics` — change — add `prep_latencies`, `prep_validator_clean`,
  `prep_violation_counts`, `prep_attempts`.
- `_print_table` — change — add Prep lat / Prep clean / Prep violations (avg) /
  Prep attempts (avg) rows.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -nE "draft_prep|validate_prep|PrepContext|_decode_sections" src/jobhunt/pipeline/interview_prep.py`;
  `grep -n "answer_latencies|_bench_one_run" scripts/bench_models.py`
- Candidates found: `pipeline.interview_prep.draft_prep_with_retry` /
  `draft_prep_sections` / `validate_prep_sections` / `_decode_sections` /
  `PrepContext` / `build_interview_context`; the existing answer-slot bench block.
- Why not reused wholesale in raw mode: `draft_prep_sections` doesn't accept
  per-model `options` (the same constraint that made cover/answer inline in raw
  mode), so the raw pass replicates only the render+call shim while reusing the
  decode + validate helpers. Production mode reuses `draft_prep_with_retry` as-is.

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `uv run python scripts/bench_models.py --mode production --models qwen3.5:9b --runs 1`
  prints a non-zero Prep row + Prep attempts.
- `--mode raw --models qwen3.5:9b --runs 1` prints a Prep row at params src
  `~/ai:build-qwen`.

**Status:** [x] done

**Result (2026-05-29):** ruff clean. `--mode production` qwen×1 shows a Prep row
(lat 132.3s, attempts 3.0, 1 residual violation → clean 0%); `--mode raw` shows
a Prep row at params src `~/ai:build-qwen` (lat 22.0s). All four prep metric
rows (lat / clean / violations / attempts) render in both modes. qwen's prep
hitting the retry cap with a residual violation is real model signal — exactly
the kind of reliability gap this slot was added to surface, not hide. Raw mode
reuses the inline-shim pattern (render_user + complete_json(options=…) +
`_decode_sections` + `validate_prep_sections`); production reuses
`draft_prep_with_retry` as-is. No `src/` change.

### Phase 2 — Generalize the bench to a fixture list

**Goal:** Run every model/slot over a fixture list with per-fixture reporting,
using only the existing happy-path fixture.

**Files to touch:**
- `scripts/bench_models.py` — introduce a `BenchFixture` dataclass; thread a
  `fixture` arg through both per-run fns; aggregate + print metrics per fixture.

**Functions to add/change:**
- `BenchFixture` — add — `key`, `job: Job`, `question: str`,
  `expect_decline: bool=False`, `expect_fabrication_pressure: bool=False` (the
  latter two are defined now, consumed in Phase 3).
- `DEFAULT_FIXTURES` — add — one entry wrapping today's `FIXTURE_JOB` +
  `FIXTURE_QUESTION` (`expect_decline=False`).
- `_bench_one_run` / `_bench_one_run_production` — change — accept
  `fixture: BenchFixture`; read job/question from it instead of the module
  globals.
- `main` — change — loop models × fixtures; key metrics by
  `(label, fixture.key)`.
- `_print_table` — change — print one block per fixture with a fixture header.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "FIXTURE_JOB|FIXTURE_QUESTION|ModelMetrics|_print_table" scripts/bench_models.py`
- Candidates found: existing `FIXTURE_JOB` / `FIXTURE_QUESTION` module globals;
  `ModelMetrics`; `_print_table`.
- Why not reused as-is: the single module-global fixture is exactly what blocks
  multi-fixture coverage; this phase promotes it into a list entry without
  changing its content (walking skeleton — output for N=1 matches Phase 1 modulo
  a fixture header).

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `--mode production --models qwen3.5:9b --runs 1` prints one fixture block whose
  numbers match Phase 1's single-fixture output.
- `--mode raw` still prints per-model params src.

**Status:** [x] done

**Result (2026-05-30):** ruff clean; 688 tests pass (unchanged). Both per-run
fns + `main` + `_print_table` now take a `fixture: BenchFixture`; module globals
`FIXTURE_JOB`/`FIXTURE_QUESTION` survive only as the one `DEFAULT_FIXTURES`
entry. `--mode production` and `--mode raw` (qwen×1) each print a single
`FIXTURE: happy_fit` block whose slot numbers match Phase 1. Model loop stays
outer (one cold load per candidate across all fixtures). `expect_decline` /
`expect_fabrication_pressure` fields defined but unused until Phase 3.

### Phase 3 — Add the decline + fabrication-pressure fixtures with guard metrics

**Goal:** Add the two adversarial fixtures and report whether each guard fired
correctly.

**Files to touch:**
- `scripts/bench_models.py` — add the two `BenchFixture` entries +
  guard-correctness metrics/rows.

**Functions to add/change:**
- `DEFAULT_FIXTURES` — change — add `decline_senior` (`expect_decline=True`) and
  `fabrication_pressure` (`expect_fabrication_pressure=True`).
- `_bench_one_run_production` / `_bench_one_run` — change — record
  `score_declined` (`decline_reason is not None or score < cfg.pipeline.min_score`);
  on a fabrication fixture, classify a tailor `FabricationError` as a SAFE
  rejection (separate counter), not a plain error.
- `ModelMetrics` — change — add `score_declines: list[bool]`,
  `tailor_safe_rejections: int`.
- `_print_table` — change — add a per-fixture "Guard" summary: decline fixture →
  `declined X/N`; fabrication fixture → `fab-safe X/N (recovered Y / rejected Z)`;
  happy fixture → `not-declined X/N`.

**Reuse audit:** (per Reuse-First Rule)
- Search terms: `grep -n "decline_reason|FabricationError|min_score" src/jobhunt/pipeline/score.py src/jobhunt/pipeline/tailor.py`
- Candidates found: `ScoreResult.decline_reason`, `cfg.pipeline.min_score`,
  `pipeline.tailor.FabricationError`.
- Why not reused elsewhere: these are read-only signals; the phase only
  interprets them — no new guard logic, the guards live in `src/` and stay
  untouched.

**Verification:** (≤ 3 bullets)
- `uv run ruff check scripts/bench_models.py` clean.
- `--mode production --models qwen3.5:9b --runs 1`: decline fixture shows
  `declined 1/1`; fabrication fixture shows `fab-safe 1/1`; happy fixture shows
  `not-declined 1/1`.
- Existing happy-fixture numbers unchanged from Phase 2.

**Status:** [x] done

**Result (2026-05-30):** ruff clean; 688 tests pass (unchanged). Added
`decline_senior` (`expect_decline`) + `fabrication_pressure`
(`expect_fabrication_pressure`) fixtures, a `score_declines`/`tailor_safe_rejections`
metric pair, a `FabricationError`-as-SAFE-rejection branch in both per-run fns,
and a `Guard` table row via `_guard_summary`. Verified qwen×1:
- **production:** happy_fit → `not-declined 1/1` (ship); decline_senior →
  `declined 1/1` (audit block); fabrication_pressure → `fab-safe 1/1 (clean 1 /
  rej 0)` — tailor omitted the unverified skills.
- **raw:** same Guard verdicts, but fabrication_pressure → `fab-safe 1/1
  (clean 0 / rej 1)` — first-pass tailor leaked an unverified skill and the
  guard rejected it; production's retry loop recovers it to clean. The
  raw-vs-production split is exactly the reliability signal the adversarial
  fixtures were added to surface.

**Plan complete.** The bench now covers all 5 LLM slots
(score/tailor/cover/answer/interview-prep) across 3 fixtures
(happy/decline/fabrication) in both modes, with explicit guard-correctness
reporting. Next human step (unchanged intent): run
`--mode production --runs 5` across qwen/granite/llama/ministral/gemma and read
the per-fixture Guard rows + eventual ship-rate before any `gateway.tasks`
default-model change. A candidate that fits the decline JD or fabricates on the
pressure JD is disqualified regardless of happy-path latency.

</details>

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

- **bench_models.py production mode** (2026-05-29). Refreshed the model bench to
  the 5 base models with `--models`/`--runs`, a per-model `~/ai` PARAMS pull
  (`--mode raw`), the `answer` slot, and a `--mode production` path running the
  real retry-wrapped pipeline (`score_job` → `*_with_retry` → `audit`) at
  `_DEFAULT_OPTIONS` for eventual ship-rate + attempts. Phases 1–4.
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

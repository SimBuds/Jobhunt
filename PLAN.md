# jobhunt — Design notes

A CLI tool that ingests Toronto-area jobs from public ATS APIs, scores them
against Casey's parsed baseline resume using local Ollama models, tailors
resumes and cover letters per role, and assists with form autofill in a
headed browser. Casey clicks Submit.

This document explains the *why*. `AGENTS.md` is the *how* (conventions,
guardrails, project structure). `README.md` is for end-users.

---

## Goals

1. **Replace cloud per-job AI spend with local inference.** Scoring,
   tailoring, and cover letters all run on Ollama. Token cost at runtime is
   zero.
2. **Future-proof the knowledge layer.** Profile facts live in
   `kb/profile/verified.json` (regenerated from `Resume.docx`).
   Prompts live in `kb/prompts/*.md` with JSON-schema frontmatter. Model swaps
   don't break them.
3. **Stay ToS-defensible.** Public ATS APIs only. No LinkedIn / Indeed /
   Glassdoor scraping. No bot-submitted applications — Playwright fills the
   form, human clicks Submit.
4. **Honesty by construction.** Tailoring is constrained to facts in
   `verified.json`. Roles must match `(employer, dates)` exactly. "Familiar"
   skills can't be promoted into Core categories. The score prompt
   auto-declines roles >2x Casey's experience or with senior titles.

## Design principles

**Local-first at runtime.** Every per-job AI call routes through
`jobhunt.gateway` to Ollama at `http://localhost:11434`. No cloud calls in
the hot path.

**Constrained output.** Every structured LLM call uses Ollama's `format`
parameter with a JSON schema from the prompt's frontmatter.

**Single hot model.** All three task slots (score, tailor, cover) run on one
model — `qwen-custom:latest` by default, with bare `qwen3.5:9b` as the
documented fallback. The custom variant is a Modelfile-derived `qwen3.5:9b`
that bakes in the user's prompt stack (persona, formatting, knowledge); the
gateway always sends a system message, which overrides the Modelfile SYSTEM
for structured tasks, so the persona doesn't bleed into scored output. Same
base weights, same VRAM footprint, same quirks. No reload churn between
tasks. Quality is held by deterministic post-processing (score clamp, cover
validator + retry, audit) together with the model's tool-use + reasoning
capability. The cascade-by-difficulty design (8B for scoring, 14B for
generation) was abandoned in May 2026 once the guardrail layers made
model-size differential less load-bearing than the 5-15 s reload cost
between every call. Set in config (`gateway.tasks`).

**Knowledge base is markdown + JSON.** No model-specific syntax baked in.

## Hardware budget

| Resource | Allocation |
|---|---|
| GPU VRAM (10 GB total, all available to Ollama) | Arch idles around 1.5 GB on the GPU, so `OLLAMA_GPU_OVERHEAD` is intentionally unset — `qwen-custom:latest` (base: `qwen3.5:9b`) lands at ~9.1 GB resident at `num_ctx=16384` with a `q5_0` quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q5_0` + `OLLAMA_FLASH_ATTENTION=1`). Single hot model; never unloads (`keep_alive=-1` per call + `OLLAMA_KEEP_ALIVE=-1` server-side) plus a warm-up call at scan start. Reasoning (`think`) is disabled at the gateway so structured calls don't blow past the timeout. |
| System RAM (32 GB) | Embeddings on CPU; SQLite cache; Playwright when active. |
| Disk | Models in `~/.ollama/models`; project DB in `data/jobhunt.db`. |

## Models (default)

| Task | Model | Why |
|---|---|---|
| Fit-score / tailor / cover | `qwen-custom:latest` (default) — a Modelfile-derived `qwen3.5:9b` baking the user's prompt stack. Fallback: bare `qwen3.5:9b`. | Single hot model — no reload churn. Strong open tool-use model; reasoning is disabled (`think: false`) at the gateway since structured-output latency under thinking blew past the 180 s timeout. Post-processing guardrails (score clamp, cover validator + retry, audit) carry quality alongside it. The custom variant's baked SYSTEM is overridden per-call by gateway system messages, so structured outputs stay clean. |
| Embeddings | `nomic-embed-text` | CPU. Reserved for future kb retrieval. |

All overridable in `~/.config/jobhunt/config.toml`. Per-call override via
`JOBHUNT_GATEWAY__TASKS__<SLOT>=<model>` env var.

## Sources (Toronto-focused)

- **Greenhouse** boards-api — most common in GTA tech listings.
- **Lever** `api.lever.co/v0/postings/<slug>` — common at GTA startups.
- **Ashby** posting API — growing share in 2025–26.
- **SmartRecruiters** public Posting API — no key needed; growing share at
  GTA mid-market employers.
- **Workday CXS** per-tenant search — reaches the Big Five banks (RBC, TD,
  BMO, CIBC, Scotia), telcos (Telus, Bell, Rogers), Manulife, Sun Life,
  Loblaw Digital, Thomson Reuters. Tenants configured explicitly in
  `config.toml` as `tenant:host:site`.
- **Job Bank Canada** RSS — federal government feed.
- **Generic employer career RSS / Atom** — opt-in per employer in
  `config.toml`.
- **Adzuna CA** — `country=ca&where=Toronto&distance=100`. Aggregates broadly;
  needs a free API key. `redirect_url` is resolved at ingest time to the
  employer's actual posting page so apply-time autofill lands on the form,
  not Adzuna's listing redirect.

Filter pipeline: each adapter checks `is_gta_eligible(location)` before
yielding a job. The allowlist covers Toronto + 16 surrounding municipalities
including the Kitchener-Waterloo corridor (Waterloo, Kitchener, Cambridge,
Guelph) within the README's 100 km radius, plus Remote-Canada /
Remote-Ontario / Remote-EST. Bare "Remote" is rejected as ambiguous. The
`ON` province abbreviation only counts as a Canada hint when comma-delimited
(`Remote, ON`) — never as the English word in `Remote (on-call) — US`.

Explicitly excluded (won't change without removing the no-scraping guardrail
in `AGENTS.md`):

- LinkedIn, Indeed, Glassdoor, ZipRecruiter — ToS, brittle, litigated.
- USAJobs and worldwide job APIs — out of GTA scope.

## What this project deliberately doesn't do

- Auto-submission of applications (ToS risk; human stays in the loop).
- Web UI / mobile (CLI-first; no current need).
- Recruiter outreach automation (different problem; do not bolt on).

## Database

SQLite, plain SQL, no ORM. Schema in `migrations/`:

- `0001_init.sql` — companies, jobs, scores, applications, indexes.
- `0002_apply_tracking.sql` — adds `jobs.decline_reason` and
  `applications.applied_week` (ISO week label, e.g. "2026-W18") for cheap
  weekly rollups.
- `0003_outcomes.sql` — adds outcome-tracking columns to `applications` for
  the `config calibrate` interview-rate-by-score-band rollup.

## Honesty enforcement (the structural part)

The "no fabrication" rule from `Resume_Tailoring_Instructions.md` is enforced
in five places, not just the prompt:

1. **Verified snapshot.** `convert-resume` emits `kb/profile/verified.json`.
   The tailoring prompt is constrained to only use facts from this file.
   All skill buckets (`skills_core`, `skills_cms`, `skills_data_devops`, `skills_ai`,
   `skills_familiar`) are **atomic lists** — one item per skill. `skills_ai` in
   particular must not be a single run-on string; the ATS keyword matchers and the
   audit's keyword-coverage check tokenize against atomic items. If `parse_docx.py`
   produces a run-on for any bucket (it currently does for `skills_ai` when the
   resume puts the AI skills on one line), patch by hand after `convert-resume`.
2. **Schema-constrained output.** `kb/prompts/tailor.md` declares a JSON
   schema. Ollama's `format=<schema>` enforces shape at decode time.
3. **Post-decode invariants.** `pipeline.tailor._enforce_no_fabrication`:
   - rejects any role whose `(employer, dates)` is missing from
     `verified.json`;
   - rejects skill items not present in `verified.json` (substring tolerance
     for parenthetical variants like `Shopify (Liquid)` vs
     `Shopify (Liquid, Custom Themes)`);
   - rejects "Familiar" skills appearing in any non-Familiar category.
4. **Score clamp.** `pipeline.score` re-partitions the LLM's claimed
   must-haves against `verified.json` and caps the score band by
   deterministic coverage (100 % → keep, 80–99 % → 89, 60–79 % → 79,
   < 60 % → 64). The LLM cannot inflate its own band by listing missing
   must-haves as matched.

   **Tiny-denominator carve-out (May 2026):** when the LLM extracts fewer
   than 3 must-haves total (matched + gaps < 3), the clamp is skipped and
   the raw score stands. Adzuna's ~500-char snippets routinely yield 1-2
   phrases; clamping a 1/2 coverage to cap-at-64 over-penalizes
   signal-poor postings rather than reflecting real fit. Three or more
   must-haves still get clamped — protects against the Pigment-style
   regression where the model lists missing tech as matched.
5. **Cover validator + retry.** `pipeline.cover_validate` catches banned
   phrases, structural violations, and unverified numeric claims;
   `pipeline.cover.write_cover_with_retry` re-prompts up to 3 times with the
   violations as a "fix these" hint before falling back to the last attempt
   (which then ships with audit verdict `revise` so the warnings surface).

If any check (1)–(3) fails, the apply pipeline aborts for that job rather
than producing a misleading resume. (4) and (5) downgrade rather than block.

## Success criteria

- Pulls fresh GTA jobs daily across configured Greenhouse / Lever / Ashby /
  SmartRecruiters / Workday / Job Bank CA / RSS / Adzuna sources without ToS
  issues.
- Scores every new job within minutes of ingestion.
- Generates a tailored .docx + cover letter in <90 seconds on local hardware.
- Autofills standard fields on Greenhouse forms; falls back to a generic
  selector-based handler elsewhere.
- Zero cloud API spend at runtime.


## Aggregate analysis (`analyze` command group)

`jobhunt analyze` is a deterministic, LLM-free aggregation surface over the
existing `jobs` table. It mirrors the audit philosophy — regex + counters, no
Ollama calls. Adding an LLM call to any `analyze` subcommand requires explicit
discussion.

### `analyze certs`

Implemented in `src/jobhunt/analyze/certs.py`. The matcher runs a curated
`_KNOWN` list (Cloud, Security, PM/Agile, Data/ML, Networking, Finance)
first, masking consumed character spans, then two generic patterns
(`Certified <Noun>` / `<Noun> certification`) for the long tail. `tally(rows)`
counts each cert once per job regardless of how many times it appears in a JD.
Output: frequency table sorted desc, capped by `--top N` (default 25).

Three modes, all deterministic — no LLM call at any step:

- **Snapshot** (default): cumulative frequency across all scanned jobs.
- **`--trend`**: bucket by `COALESCE(posted_at, ingested_at)` into two adjacent
  `--window-days` windows (default 30). Render `Prev / Cur / Δ% / Trend` with
  rising / falling / emerging / dropped / stable classification in
  `analyze_cmd._classify`. The current window also feeds a *"Potential new
  certs"* review list pulled from `extract_certs_split`'s generic-regex tier,
  giving the same outcome Gemini-style LLM-discovery would — without an
  Ollama call.
- **`--min-score N`**: joins `scores`, adds a `Fit` column (count restricted
  to jobs you scored ≥ N) and a `Verdict` column derived from
  `analyze_cmd._classify_verdict`. The rubric weighs fit-demand against market
  demand and trend direction so the rightmost column is a one-word decision:
  *Worth pursuing*, *Strong emerging signal*, *Stable staple*, *Skip*,
  *Wrong direction*, *Late — diminishing*, or *Marginal*. Sort priority
  surfaces the actionable rows first.

The decision rubric (`_classify_verdict`) is a small frozen-in-code table —
no config. Tuning it is a code change, not a runtime knob. Verdict tiers
deliberately favor false negatives (mark "Skip" when in doubt) so the user
isn't pushed to chase certs the data doesn't actually support.
